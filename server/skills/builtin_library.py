from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SkillStatus = Literal[
    "ready", "conditional", "dependency_missing", "reference_only"
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillsetMember(_StrictModel):
    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class BuiltinSkill(_StrictModel):
    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    description: str
    status: SkillStatus
    reason: str
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_url: str
    source_path: str
    source_license: Literal["Apache-2.0"]
    adapted: bool
    available: bool = False
    availability_reason: str = ""
    inject_runtime: bool = False


class Skillset(_StrictModel):
    skillset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    builtin: bool = False
    members: list[SkillsetMember] = Field(min_length=1, max_length=64)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def unique_members(self) -> "Skillset":
        ids = [member.skill_id for member in self.members]
        if len(ids) != len(set(ids)):
            raise ValueError("Skillset members must be unique")
        return self


class SkillsetWrite(_StrictModel):
    skillset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    skill_ids: list[str] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_skill_ids(self) -> "SkillsetWrite":
        if len(self.skill_ids) != len(set(self.skill_ids)):
            raise ValueError("Skill ids must be unique")
        return self


class SkillsetUpdate(_StrictModel):
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    skill_ids: list[str] = Field(min_length=1, max_length=16)


class BuiltinSkillLibraryError(Exception):
    pass


class BuiltinSkillLibrary:
    def __init__(
        self,
        root: Path | None = None,
        skillset_path: Path | None = None,
    ) -> None:
        package_dir = Path(__file__).resolve().parent
        self.root = Path(root or package_dir / "builtin")
        installed_root = Path(
            os.getenv("SKILL_INSTALLED_DIR") or package_dir / "installed"
        )
        self.skillset_path = Path(
            skillset_path
            or os.getenv("SKILLSET_STORE_PATH")
            or installed_root / "skillsets.json"
        )
        self._lock = threading.RLock()

    def list_skills(self) -> list[BuiltinSkill]:
        records, _ = self._load_manifest()
        return [self._with_availability(record) for record in records]

    def get_content(self, skill_id: str) -> str:
        records, _ = self._load_manifest()
        if skill_id not in {record.skill_id for record in records}:
            raise BuiltinSkillLibraryError(f"Built-in Skill '{skill_id}' not found")
        return (self.root / skill_id / "SKILL.md").read_text(encoding="utf-8")

    def list_skillsets(self) -> list[Skillset]:
        _, default_skillset = self._load_manifest()
        with self._lock:
            custom = self._read_custom_skillsets()
        return [default_skillset, *sorted(custom, key=lambda item: item.name.lower())]

    def get_skillset(self, skillset_id: str) -> Skillset:
        match = next(
            (
                item
                for item in self.list_skillsets()
                if item.skillset_id == skillset_id
            ),
            None,
        )
        if match is None:
            raise BuiltinSkillLibraryError("Skillset not found")
        return match

    def create_skillset(self, request: SkillsetWrite) -> Skillset:
        if request.skillset_id == "general-agent-default":
            raise BuiltinSkillLibraryError("The default Skillset is read-only")
        with self._lock:
            current = self._read_custom_skillsets()
            if any(item.skillset_id == request.skillset_id for item in current):
                raise BuiltinSkillLibraryError("Skillset id already exists")
            created = self._build_skillset(request, builtin=False)
            self._write_custom_skillsets([*current, created])
            return created

    def update_skillset(
        self, skillset_id: str, request: SkillsetUpdate
    ) -> Skillset:
        if skillset_id == "general-agent-default":
            raise BuiltinSkillLibraryError("The default Skillset is read-only")
        with self._lock:
            current = self._read_custom_skillsets()
            existing = next(
                (item for item in current if item.skillset_id == skillset_id), None
            )
            if existing is None:
                raise BuiltinSkillLibraryError("Skillset not found")
            if existing.revision != request.expected_revision:
                raise BuiltinSkillLibraryError("Skillset changed; reload before saving")
            updated = self._build_skillset(
                SkillsetWrite(
                    skillset_id=skillset_id,
                    name=request.name,
                    description=request.description,
                    skill_ids=request.skill_ids,
                ),
                builtin=False,
            )
            self._write_custom_skillsets(
                [updated if item.skillset_id == skillset_id else item for item in current]
            )
            return updated

    def delete_skillset(self, skillset_id: str) -> None:
        if skillset_id == "general-agent-default":
            raise BuiltinSkillLibraryError("The default Skillset is read-only")
        with self._lock:
            current = self._read_custom_skillsets()
            retained = [item for item in current if item.skillset_id != skillset_id]
            if len(retained) == len(current):
                raise BuiltinSkillLibraryError("Skillset not found")
            self._write_custom_skillsets(retained)

    def _load_manifest(self) -> tuple[list[BuiltinSkill], Skillset]:
        try:
            raw = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
            records = [BuiltinSkill.model_validate(item) for item in raw["skills"]]
            raw_skillset = raw["skillset"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BuiltinSkillLibraryError("Built-in Skill manifest is invalid") from exc
        ids = [record.skill_id for record in records]
        if len(ids) != 16 or len(set(ids)) != 16:
            raise BuiltinSkillLibraryError("The built-in library must contain 16 Skills")
        for record in records:
            content = (self.root / record.skill_id / "SKILL.md").read_bytes()
            if hashlib.sha256(content).hexdigest() != record.digest:
                raise BuiltinSkillLibraryError(
                    f"Built-in Skill digest mismatch: {record.skill_id}"
                )
        members = [SkillsetMember.model_validate(item) for item in raw_skillset["members"]]
        if [member.skill_id for member in members] != ids:
            raise BuiltinSkillLibraryError("Default Skillset membership is invalid")
        default = Skillset(
            skillset_id=raw_skillset["skillset_id"],
            name=raw_skillset["name"],
            description=raw_skillset["description"],
            builtin=True,
            members=members,
            revision=self._revision(members),
        )
        return records, default

    def _build_skillset(self, request: SkillsetWrite, *, builtin: bool) -> Skillset:
        records, _ = self._load_manifest()
        by_id = {record.skill_id: record for record in records}
        unknown = sorted(set(request.skill_ids) - set(by_id))
        if unknown:
            raise BuiltinSkillLibraryError(
                f"Unknown built-in Skill ids: {', '.join(unknown)}"
            )
        members = [
            SkillsetMember(skill_id=skill_id, digest=by_id[skill_id].digest)
            for skill_id in request.skill_ids
        ]
        return Skillset(
            skillset_id=request.skillset_id,
            name=request.name,
            description=request.description,
            builtin=builtin,
            members=members,
            revision=self._revision(members, request.name, request.description),
        )

    def _with_availability(self, record: BuiltinSkill) -> BuiltinSkill:
        available = record.status == "ready"
        reason = record.reason
        if record.status == "conditional":
            checks = {
                "llamafactory": bool(shutil.which("llamafactory-cli"))
                or importlib.util.find_spec("llamafactory") is not None,
                "ollama": bool(shutil.which("ollama")),
                "vllm": importlib.util.find_spec("vllm") is not None,
            }
            available = checks.get(record.skill_id, False)
            if not available:
                reason = f"{record.reason} 当前运行环境未检测到对应依赖。"
        return record.model_copy(
            update={
                "available": available,
                "availability_reason": reason,
                "inject_runtime": available
                and record.status in {"ready", "conditional"},
            }
        )

    def _read_custom_skillsets(self) -> list[Skillset]:
        if not self.skillset_path.exists():
            return []
        try:
            raw = json.loads(self.skillset_path.read_text(encoding="utf-8"))
            return [Skillset.model_validate(item) for item in raw.get("skillsets", [])]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise BuiltinSkillLibraryError("Custom Skillset store is invalid") from exc

    def _write_custom_skillsets(self, skillsets: list[Skillset]) -> None:
        self.skillset_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"skillsets": [item.model_dump(mode="json") for item in skillsets]},
            ensure_ascii=False,
            indent=2,
        )
        fd, temp_name = tempfile.mkstemp(
            prefix=".skillsets.", dir=str(self.skillset_path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.skillset_path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _revision(
        members: list[SkillsetMember], name: str = "", description: str = ""
    ) -> str:
        digest = hashlib.sha256()
        digest.update(name.encode("utf-8"))
        digest.update(description.encode("utf-8"))
        for member in members:
            digest.update(member.model_dump_json().encode("utf-8"))
        return digest.hexdigest()
