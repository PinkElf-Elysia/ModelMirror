from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml


PROJECT_ID_RE = re.compile(r"^rp_[0-9a-f]{32}$")
RUN_ID_RE = re.compile(r"^lr_[0-9a-f]{32}$")
MAX_MANIFEST_BYTES = 512 * 1024
MAX_ATTEMPTS = 10


class ProjectConflict(RuntimeError):
    pass


class ProjectIntegrityError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ProjectStore:
    def __init__(self, root: Path, *, source_lock_sha256: str) -> None:
        self.root = root
        self.source_lock_sha256 = source_lock_sha256
        self._lock = threading.RLock()

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ProjectIntegrityError("projects root must not be a symlink")

    def create(
        self, *, title: str, research_question: str, idempotency_key: str
    ) -> tuple[dict[str, Any], bool]:
        with self._lock:
            return self._create(
                title=title,
                research_question=research_question,
                idempotency_key=idempotency_key,
            )

    def _create(
        self, *, title: str, research_question: str, idempotency_key: str
    ) -> tuple[dict[str, Any], bool]:
        key_hash = _hash_key(idempotency_key)
        for project in self._all():
            if project["control"]["createIdempotencyHash"] == key_hash:
                if (
                    project["title"] != title
                    or project["researchQuestion"] != research_question
                ):
                    raise ProjectConflict("idempotency key already used with other input")
                return project, False

        project_id = f"rp_{uuid4().hex}"
        now = utc_now()
        project = {
            "schemaVersion": 1,
            "projectId": project_id,
            "title": title,
            "researchQuestion": research_question,
            "domain": "ai_agent",
            "currentStage": "literature",
            "stages": {
                "literature": "active",
                "hypothesis_protocol": "not_available",
                "research_workspace": "not_available",
                "evaluation": "not_available",
                "analysis_report": "not_available",
            },
            "createdAt": now,
            "updatedAt": now,
            "control": {
                "createIdempotencyHash": key_hash,
                "sourceLockSha256": self.source_lock_sha256,
            },
            "literature": {
                "profileId": "v0.1-literature-default",
                "phase": "not_started",
                "outcome": None,
                "activeRunId": None,
                "completedRunId": None,
                "collectionId": None,
                "modelId": None,
                "attempts": [],
            },
        }
        directory = self._project_dir(project_id, must_exist=False)
        directory.mkdir(mode=0o700)
        self._write(project)
        return project, True

    def get(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read(self._manifest(project_id))

    def list(
        self,
        *,
        after_project_id: str | None,
        limit: int,
        query: str | None = None,
        phase: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return self._list(
                after_project_id=after_project_id,
                limit=limit,
                query=query,
                phase=phase,
                outcome=outcome,
            )

    def _list(
        self,
        *,
        after_project_id: str | None,
        limit: int,
        query: str | None = None,
        phase: str | None = None,
        outcome: str | None = None,
    ) -> list[dict[str, Any]]:
        projects = self._all()
        projects.sort(key=lambda item: (item["createdAt"], item["projectId"]), reverse=True)
        if query:
            needle = query.casefold()
            projects = [
                item
                for item in projects
                if needle
                in " ".join(
                    (item["projectId"], item["title"], item["researchQuestion"])
                ).casefold()
            ]
        if phase:
            projects = [item for item in projects if item["literature"]["phase"] == phase]
        if outcome:
            projects = [
                item for item in projects if item["literature"]["outcome"] == outcome
            ]
        if after_project_id:
            positions = [
                index
                for index, item in enumerate(projects)
                if item["projectId"] == after_project_id
            ]
            if not positions:
                raise KeyError(after_project_id)
            projects = projects[positions[0] + 1 :]
        return projects[: max(1, min(limit, 101))]

    def update(
        self,
        project_id: str,
        *,
        title: str | None,
        research_question: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            project = self.get(project_id)
            if project["literature"]["attempts"]:
                raise ProjectConflict("project is immutable after literature starts")
            if title is not None:
                project["title"] = title
            if research_question is not None:
                project["researchQuestion"] = research_question
            project["updatedAt"] = utc_now()
            self._write(project)
            return project

    def bind_collection(self, project_id: str, collection_id: str | None) -> dict[str, Any]:
        with self._lock:
            project = self.get(project_id)
            if project["literature"]["phase"] in {"queued", "running"}:
                raise ProjectConflict("cannot change collection during an active run")
            project["literature"]["collectionId"] = collection_id
            project["updatedAt"] = utc_now()
            self._write(project)
            return project

    def begin_attempt(
        self,
        project_id: str,
        *,
        idempotency_key: str,
        model_id: str,
        collection_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        with self._lock:
            return self._begin_attempt(
                project_id,
                idempotency_key=idempotency_key,
                model_id=model_id,
                collection_id=collection_id,
            )

    def _begin_attempt(
        self,
        project_id: str,
        *,
        idempotency_key: str,
        model_id: str,
        collection_id: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        project = self.get(project_id)
        literature = project["literature"]
        key_hash = _hash_key(idempotency_key)
        expected_search_engine = "openalex"
        for attempt in literature["attempts"]:
            if attempt["idempotencyHash"] == key_hash:
                if (
                    attempt.get("modelId") != model_id
                    or attempt.get("searchEngine") != expected_search_engine
                    or attempt.get(
                        "collectionId", literature.get("collectionId")
                    )
                    != collection_id
                ):
                    raise ProjectConflict(
                        "idempotency key already used with other literature input"
                    )
                return project, attempt, False
        if literature["completedRunId"]:
            raise ProjectConflict("project already has a completed literature review")
        if literature["phase"] in {"queued", "running"}:
            raise ProjectConflict("project already has an active literature run")
        if len(literature["attempts"]) >= MAX_ATTEMPTS:
            raise ProjectConflict("project reached the literature attempt limit")

        run_id = f"lr_{uuid4().hex}"
        now = utc_now()
        attempt = {
            "runId": run_id,
            "idempotencyHash": key_hash,
            "ldrResearchId": None,
            "phase": "queued",
            "outcome": None,
            "rawStatus": None,
            "cancelRequestedAt": None,
            "cancelAppliedAt": None,
            "createdAt": now,
            "startedAt": None,
            "terminalAt": None,
            "syncedAt": None,
            "errorType": None,
            "errorMessage": None,
            "integrityStatus": "pending",
            "reconcileAttempts": 0,
            "statusFailures": 0,
            "progress": 0,
            "latestLog": None,
            "profileId": "v0.1-literature-default",
            "modelId": model_id,
            "searchEngine": expected_search_engine,
            "collectionId": collection_id,
            "strategy": "langgraph-agent",
            "egress": "public_only",
            "maxResults": 15,
            "iterations": 2,
            "questionsPerIteration": 3,
            "artifacts": {},
        }
        literature["attempts"].append(attempt)
        literature.update(
            {
                "phase": "queued",
                "outcome": None,
                "activeRunId": run_id,
                "collectionId": collection_id,
                "modelId": model_id,
            }
        )
        project["updatedAt"] = now
        self.run_directory(project_id, run_id, must_exist=False).mkdir(parents=True)
        self._write(project)
        return project, attempt, True

    def update_attempt(
        self, project_id: str, run_id: str, fields: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            project = self.get(project_id)
            attempt = self._attempt(project, run_id)
            allowed = {
                "ldrResearchId",
                "phase",
                "outcome",
                "rawStatus",
                "cancelRequestedAt",
                "cancelAppliedAt",
                "startedAt",
                "terminalAt",
                "syncedAt",
                "errorType",
                "errorMessage",
                "integrityStatus",
                "artifacts",
                "reconcileAttempts",
                "statusFailures",
                "progress",
                "latestLog",
            }
            unknown = set(fields) - allowed
            if unknown:
                raise ValueError(f"unsupported attempt fields: {sorted(unknown)}")
            attempt.update(fields)
            literature = project["literature"]
            literature["phase"] = attempt["phase"]
            literature["outcome"] = attempt["outcome"]
            if attempt["phase"] == "terminal":
                literature["activeRunId"] = None
                if (
                    attempt["outcome"] == "completed"
                    and attempt.get("integrityStatus") == "verified"
                ):
                    literature["completedRunId"] = run_id
                elif literature.get("completedRunId") == run_id:
                    literature["completedRunId"] = None
            project["updatedAt"] = utc_now()
            self._write(project)
            return project, attempt

    def get_attempt(
        self, project_id: str, run_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            project = self.get(project_id)
            return project, self._attempt(project, run_id)

    def active_literature(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        with self._lock:
            active: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for project in self._all():
                for attempt in project["literature"]["attempts"]:
                    if attempt["phase"] in {"queued", "running"}:
                        active.append((project, attempt))
            active.sort(key=lambda item: item[1]["createdAt"])
            return active

    def run_directory(self, project_id: str, run_id: str, *, must_exist: bool = True) -> Path:
        if not RUN_ID_RE.fullmatch(run_id):
            raise KeyError(run_id)
        project_directory = self._project_dir(project_id)
        literature_directory = project_directory / "literature"
        runs_directory = literature_directory / "runs"
        directory = runs_directory / run_id
        for candidate in (literature_directory, runs_directory, directory):
            if candidate.is_symlink():
                raise ProjectIntegrityError("literature path must not be a symlink")
            if candidate.exists() and not candidate.is_dir():
                raise ProjectIntegrityError("literature path must be a directory")
        if must_exist and not directory.is_dir():
            raise KeyError(run_id)
        return directory

    def _all(self) -> list[dict[str, Any]]:
        self.prepare()
        projects: list[dict[str, Any]] = []
        for child in self.root.iterdir():
            if child.is_symlink():
                raise ProjectIntegrityError("project directory must not be a symlink")
            if child.is_dir() and PROJECT_ID_RE.fullmatch(child.name):
                projects.append(self._read(child / "research.yaml"))
        return projects

    def _project_dir(self, project_id: str, *, must_exist: bool = True) -> Path:
        if not PROJECT_ID_RE.fullmatch(project_id):
            raise KeyError(project_id)
        directory = self.root / project_id
        if must_exist and (not directory.is_dir() or directory.is_symlink()):
            raise KeyError(project_id)
        return directory

    def _manifest(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "research.yaml"

    def _write(self, project: dict[str, Any]) -> None:
        path = self._project_dir(project["projectId"]) / "research.yaml"
        content = yaml.safe_dump(
            project, allow_unicode=True, sort_keys=False, width=100
        ).encode("utf-8")
        if len(content) > MAX_MANIFEST_BYTES:
            raise ProjectIntegrityError("research manifest exceeds size limit")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".research-", suffix=".yaml.tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self, path: Path) -> dict[str, Any]:
        if not path.is_file() or path.is_symlink():
            raise KeyError(path.parent.name)
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProjectIntegrityError("research manifest exceeds size limit")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise ProjectIntegrityError("unsupported research manifest")
        if value.get("projectId") != path.parent.name:
            raise ProjectIntegrityError("project id does not match directory")
        return value

    @staticmethod
    def _attempt(project: dict[str, Any], run_id: str) -> dict[str, Any]:
        for attempt in project["literature"]["attempts"]:
            if attempt["runId"] == run_id:
                return attempt
        raise KeyError(run_id)
