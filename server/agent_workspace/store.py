from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

from .defaults import default_system_config
from .models import (
    AGENT_ID_PATTERN,
    AgentCreateRequest,
    AgentPayload,
    AgentSkillSnapshot,
    AgentSummary,
    AgentSystemConfig,
)


DEFAULT_AGENT_ID = "default_agent"


class AgentWorkspaceError(Exception):
    """Base error for Agent State persistence."""


class AgentNotFoundError(AgentWorkspaceError):
    pass


class AgentConflictError(AgentWorkspaceError):
    pass


class AgentStateValidationError(AgentWorkspaceError):
    pass


class AgentStateStore:
    """Filesystem-backed Agent State store with atomic, revisioned updates."""

    def __init__(
        self,
        root: Path | None = None,
        builtin_skills_root: Path | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.root = Path(
            root
            or os.getenv("AGENT_WORKSPACE_ROOT")
            or package_dir / "storage"
        )
        self.agents_root = self.root / "agents"
        self.builtin_skills_root = Path(
            builtin_skills_root
            or package_dir.parent / "skills" / "builtin"
        )
        self._lock = threading.RLock()

    def list_agents(self) -> list[AgentSummary]:
        with self._lock:
            self.ensure_default_agent()
            summaries: list[AgentSummary] = []
            for candidate in sorted(self.agents_root.iterdir()):
                if not candidate.is_dir() or not re.fullmatch(
                    AGENT_ID_PATTERN, candidate.name
                ):
                    continue
                try:
                    payload = self._read_payload(candidate.name)
                except AgentWorkspaceError:
                    continue
                summaries.append(
                    AgentSummary(
                        agent_id=payload.agent_id,
                        name=payload.config.name,
                        description=payload.config.description,
                        version=payload.config.version,
                        builtin=payload.builtin,
                        skill_count=len(payload.skills),
                        revision=payload.revision,
                    )
                )
            summaries.sort(key=lambda item: (not item.builtin, item.name.lower()))
            return summaries

    def get_agent(self, agent_id: str) -> AgentPayload:
        normalized = self._validate_agent_id(agent_id)
        with self._lock:
            if normalized == DEFAULT_AGENT_ID:
                self.ensure_default_agent()
            return self._read_payload(normalized)

    def create_agent(self, request: AgentCreateRequest) -> AgentPayload:
        with self._lock:
            self._ensure_root()
            agent_id = self._validate_agent_id(request.agent_id)
            target = self._agent_dir(agent_id)
            if target.exists():
                raise AgentConflictError(f"Agent '{agent_id}' already exists")
            config = default_system_config(
                name=request.name,
                description=request.description,
            )
            self._create_agent_directory(agent_id, config=config, agents_md="")
            return self._read_payload(agent_id)

    def create_generated_agent(
        self,
        *,
        agent_id: str,
        config: AgentSystemConfig,
        agents_md: str,
        skill_ids: list[str],
        source_agent_id: str,
    ) -> AgentPayload:
        """Atomically promote a validated candidate using source Skill snapshots."""

        clean_agents_md = agents_md.strip()
        if not clean_agents_md:
            raise AgentStateValidationError("Generated AGENTS.md cannot be empty")
        with self._lock:
            self._ensure_root()
            normalized = self._validate_agent_id(agent_id)
            if self._agent_dir(normalized).exists():
                raise AgentConflictError(f"Agent '{normalized}' already exists")
            source_id = self._validate_agent_id(source_agent_id)
            source_agent = self._read_payload(source_id)
            available = {item.skill_id: item for item in source_agent.skills}
            if len(skill_ids) != len(set(skill_ids)):
                raise AgentStateValidationError(
                    "Generated Agent Skill references must be unique"
                )
            unknown = sorted(set(skill_ids) - set(available))
            if unknown:
                raise AgentStateValidationError(
                    f"Generated Agent references unknown Skills: {', '.join(unknown)}"
                )
            selected = [available[skill_id] for skill_id in skill_ids]
            self._create_agent_directory(
                normalized,
                config=config,
                agents_md=clean_agents_md,
                skill_snapshots=selected,
                skill_source_root=self._state_dir(source_id) / "skills",
            )
            return self._read_payload(normalized)

    def update_agent(
        self,
        agent_id: str,
        *,
        expected_revision: str,
        config: AgentSystemConfig,
        agents_md: str,
    ) -> AgentPayload:
        normalized = self._validate_agent_id(agent_id)
        with self._lock:
            current = self._read_payload(normalized)
            if current.revision != expected_revision:
                raise AgentConflictError(
                    "Agent State changed. Reload before saving your changes."
                )
            state_dir = self._state_dir(normalized)
            config_path = state_dir / "system_config.yaml"
            agents_path = state_dir / "AGENTS.md"
            old_config = config_path.read_text(encoding="utf-8")
            old_agents = agents_path.read_text(encoding="utf-8")
            try:
                self._atomic_write_text(agents_path, agents_md)
                self._atomic_write_config(config_path, config)
            except Exception:
                self._atomic_write_text(agents_path, old_agents)
                self._atomic_write_text(config_path, old_config)
                raise
            return self._read_payload(normalized)

    def reset_agent_config(
        self, agent_id: str, *, expected_revision: str
    ) -> AgentPayload:
        normalized = self._validate_agent_id(agent_id)
        with self._lock:
            current = self._read_payload(normalized)
            if current.revision != expected_revision:
                raise AgentConflictError(
                    "Agent State changed. Reload before resetting configuration."
                )
            reset = default_system_config(
                name=current.config.name,
                description=current.config.description,
            )
            self._atomic_write_config(
                self._state_dir(normalized) / "system_config.yaml", reset
            )
            return self._read_payload(normalized)

    def materialize_builtin_skillset(
        self,
        agent_id: str,
        *,
        skillset_id: str,
        members: list[dict[str, str]],
        expected_revision: str,
    ) -> AgentPayload:
        """Replace one Agent's built-in Skill snapshots as one recoverable unit."""

        normalized = self._validate_agent_id(agent_id)
        with self._lock:
            current = self._read_payload(normalized)
            if current.revision != expected_revision:
                raise AgentConflictError(
                    "Agent State changed. Reload before installing a Skillset."
                )
            manifest = self._read_builtin_manifest()
            by_id = {item["skill_id"]: item for item in manifest["skills"]}
            selected: list[dict[str, Any]] = []
            for member in members:
                skill_id = member.get("skill_id", "")
                item = by_id.get(skill_id)
                if item is None or member.get("digest") != item["digest"]:
                    raise AgentStateValidationError(
                        f"Skillset member is missing or stale: {skill_id}"
                    )
                selected.append(item)
            if not selected:
                raise AgentStateValidationError("A Skillset must contain a Skill")

            state_dir = self._state_dir(normalized)
            skills_dir = state_dir / "skills"
            snapshot_path = state_dir / "skillset_snapshot.json"
            config_path = state_dir / "system_config.yaml"
            temp_root = Path(
                tempfile.mkdtemp(prefix=".skillset-", dir=str(state_dir))
            )
            staged_skills = temp_root / "skills"
            backup_skills = temp_root / "previous-skills"
            previous_snapshot = snapshot_path.read_text(encoding="utf-8")
            previous_config = config_path.read_text(encoding="utf-8")
            config = current.config.model_copy(update={"skillset_id": skillset_id})
            try:
                staged_skills.mkdir()
                for item in selected:
                    shutil.copytree(
                        self.builtin_skills_root / item["skill_id"],
                        staged_skills / item["skill_id"],
                    )
                os.replace(skills_dir, backup_skills)
                os.replace(staged_skills, skills_dir)
                self._atomic_write_text(
                    snapshot_path,
                    json.dumps(
                        {"skillset_id": skillset_id, "skills": selected},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                self._atomic_write_config(config_path, config)
            except Exception:
                if skills_dir.exists():
                    shutil.rmtree(skills_dir, ignore_errors=True)
                if backup_skills.exists():
                    os.replace(backup_skills, skills_dir)
                self._atomic_write_text(snapshot_path, previous_snapshot)
                self._atomic_write_text(config_path, previous_config)
                raise
            finally:
                shutil.rmtree(temp_root, ignore_errors=True)
            return self._read_payload(normalized)

    def delete_agent(self, agent_id: str) -> None:
        normalized = self._validate_agent_id(agent_id)
        if normalized == DEFAULT_AGENT_ID:
            raise AgentConflictError("General Agent cannot be deleted")
        with self._lock:
            target = self._agent_dir(normalized)
            if not target.exists():
                raise AgentNotFoundError(f"Agent '{normalized}' was not found")
            shutil.rmtree(target)

    def ensure_default_agent(self) -> AgentPayload:
        with self._lock:
            self._ensure_root()
            config_path = self._state_dir(DEFAULT_AGENT_ID) / "system_config.yaml"
            if config_path.exists():
                return self._read_payload(DEFAULT_AGENT_ID)
            if self._agent_dir(DEFAULT_AGENT_ID).exists():
                raise AgentStateValidationError(
                    "General Agent directory exists without system_config.yaml"
                )
            self._create_agent_directory(
                DEFAULT_AGENT_ID,
                config=default_system_config(),
                agents_md="",
            )
            return self._read_payload(DEFAULT_AGENT_ID)

    def _create_agent_directory(
        self,
        agent_id: str,
        *,
        config: AgentSystemConfig,
        agents_md: str,
        skill_snapshots: list[AgentSkillSnapshot] | None = None,
        skill_source_root: Path | None = None,
    ) -> None:
        manifest = self._read_builtin_manifest() if skill_snapshots is None else None
        if skill_snapshots is None:
            snapshots = [
                AgentSkillSnapshot.model_validate(item)
                for item in manifest["skills"]
            ]
            source_root = self.builtin_skills_root
            snapshot_skillset_id = manifest["skillset"]["skillset_id"]
        else:
            snapshots = list(skill_snapshots)
            if skill_source_root is None:
                raise AgentStateValidationError(
                    "Generated Skill snapshot source is required"
                )
            source_root = skill_source_root
            snapshot_skillset_id = config.skillset_id
        temp_parent = Path(
            tempfile.mkdtemp(prefix=f".{agent_id}-", dir=str(self.agents_root))
        )
        temp_agent = temp_parent / agent_id
        try:
            state_dir = temp_agent / "agent_state"
            skills_dir = state_dir / "skills"
            for path in (
                skills_dir,
                state_dir / "memory",
                state_dir / "tools",
                temp_agent / "scratchpad",
            ):
                path.mkdir(parents=True, exist_ok=True)
            snapshot_payloads: list[dict[str, Any]] = []
            for snapshot in snapshots:
                source = source_root / snapshot.skill_id
                if not (source / "SKILL.md").exists():
                    raise AgentStateValidationError(
                        f"Skill snapshot '{snapshot.skill_id}' is missing SKILL.md"
                    )
                shutil.copytree(source, skills_dir / snapshot.skill_id)
                snapshot_payloads.append(snapshot.model_dump(mode="json"))
            self._atomic_write_text(state_dir / "AGENTS.md", agents_md)
            self._atomic_write_text(
                state_dir / "skillset_snapshot.json",
                json.dumps(
                    {
                        "skillset_id": snapshot_skillset_id,
                        "skills": snapshot_payloads,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            # system_config.yaml is written last and is the completion marker.
            self._atomic_write_config(state_dir / "system_config.yaml", config)
            os.replace(temp_agent, self._agent_dir(agent_id))
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)

    def _read_payload(self, agent_id: str) -> AgentPayload:
        state_dir = self._state_dir(agent_id)
        config_path = state_dir / "system_config.yaml"
        agents_path = state_dir / "AGENTS.md"
        snapshot_path = state_dir / "skillset_snapshot.json"
        if not config_path.exists():
            raise AgentNotFoundError(f"Agent '{agent_id}' was not found")
        try:
            raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config = AgentSystemConfig.model_validate(raw_config)
            agents_md = agents_path.read_text(encoding="utf-8")
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            skills = [
                AgentSkillSnapshot.model_validate(item)
                for item in snapshot.get("skills", [])
            ]
        except (OSError, ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            raise AgentStateValidationError(
                f"Agent '{agent_id}' has invalid state: {exc}"
            ) from exc
        revision = self._revision(config, agents_md, skills)
        return AgentPayload(
            agent_id=agent_id,
            builtin=agent_id == DEFAULT_AGENT_ID,
            config=config,
            agents_md=agents_md,
            skills=skills,
            revision=revision,
            state_path=f"agents/{agent_id}/agent_state",
        )

    def _read_builtin_manifest(self) -> dict[str, Any]:
        path = self.builtin_skills_root / "manifest.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            skills = payload["skills"]
            skillset = payload["skillset"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AgentStateValidationError(
                "built-in Skill manifest is missing or invalid"
            ) from exc
        ids = [item.get("skill_id") for item in skills if isinstance(item, dict)]
        if len(ids) != 16 or len(set(ids)) != 16:
            raise AgentStateValidationError(
                "General Agent requires exactly 16 unique built-in Skills"
            )
        if skillset.get("skillset_id") != "general-agent-default":
            raise AgentStateValidationError("default Skillset id is invalid")
        members = skillset.get("members")
        if not isinstance(members, list):
            raise AgentStateValidationError("default Skillset members are invalid")
        member_ids = [
            member.get("skill_id")
            for member in members
            if isinstance(member, dict)
        ]
        if member_ids != ids:
            raise AgentStateValidationError(
                "default Skillset membership must match the built-in manifest"
            )
        expected_digests = {item["skill_id"]: item["digest"] for item in skills}
        if any(
            member.get("digest") != expected_digests.get(member.get("skill_id"))
            for member in members
        ):
            raise AgentStateValidationError(
                "default Skillset content digests do not match the library"
            )
        for item in skills:
            AgentSkillSnapshot.model_validate(item)
        return payload

    def _ensure_root(self) -> None:
        self.agents_root.mkdir(parents=True, exist_ok=True)

    def _agent_dir(self, agent_id: str) -> Path:
        target = (self.agents_root / agent_id).resolve()
        root = self.agents_root.resolve()
        if target.parent != root:
            raise AgentStateValidationError("unsafe Agent path")
        return target

    def _state_dir(self, agent_id: str) -> Path:
        return self._agent_dir(agent_id) / "agent_state"

    @staticmethod
    def _validate_agent_id(agent_id: str) -> str:
        if not re.fullmatch(AGENT_ID_PATTERN, agent_id):
            raise AgentStateValidationError(
                "Agent id may contain only letters, numbers, underscores, and hyphens"
            )
        return agent_id

    @staticmethod
    def _revision(
        config: AgentSystemConfig,
        agents_md: str,
        skills: list[AgentSkillSnapshot],
    ) -> str:
        digest = hashlib.sha256()
        digest.update(config.model_dump_json().encode("utf-8"))
        digest.update(b"\0")
        digest.update(agents_md.encode("utf-8"))
        digest.update(b"\0")
        for skill in skills:
            digest.update(skill.model_dump_json().encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def _atomic_write_config(path: Path, config: AgentSystemConfig) -> None:
        AgentStateStore._atomic_write_text(
            path,
            yaml.safe_dump(
                config.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=False,
            ),
        )

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
