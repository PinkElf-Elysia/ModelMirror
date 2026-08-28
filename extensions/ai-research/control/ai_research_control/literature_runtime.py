from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from .ldr_client import (
    LdrClient,
    LdrConflict,
    LdrProtocolError,
    LdrSessionExpired,
    LdrUnavailable,
)
from .literature_artifacts import LiteratureArtifactError, LiteratureArtifactStore
from .project_store import ProjectConflict, ProjectStore


MAX_RECONCILE_ATTEMPTS = 6
MAX_STATUS_FAILURES = 3


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class LiteratureRuntime:
    def __init__(
        self,
        *,
        projects: ProjectStore,
        artifacts: LiteratureArtifactStore,
        ldr: LdrClient,
        model_id: str,
        bridge_url: str,
    ) -> None:
        self.projects = projects
        self.artifacts = artifacts
        self.ldr = ldr
        self.model_id = model_id
        self.bridge_url = bridge_url.rstrip("/")
        self._start_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()

    async def start_run(
        self,
        project_id: str,
        *,
        idempotency_key: str,
        collection_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        async with self._start_lock:
            session = await asyncio.to_thread(self.ldr.session_status)
            if session["status"] != "ready":
                raise LdrSessionExpired("LDR session is not unlocked")
            active = await asyncio.to_thread(self.projects.active_literature)
            existing = next(
                (
                    (project, attempt)
                    for project, attempt in active
                    if project["projectId"] == project_id
                    and attempt["idempotencyHash"]
                    == __import__("hashlib").sha256(
                        idempotency_key.encode("utf-8")
                    ).hexdigest()
                ),
                None,
            )
            if existing is not None:
                return existing[0], False
            if active:
                raise ProjectConflict("another literature research is active")
            await self._require_exact_collection_scope(collection_id)

            project, attempt, created = await asyncio.to_thread(
                self.projects.begin_attempt,
                project_id,
                idempotency_key=idempotency_key,
                model_id=self.model_id,
                collection_id=collection_id,
            )
            if not created:
                return project, False
            try:
                ldr_id, raw_start = await asyncio.to_thread(
                    self.ldr.start_research,
                    question=project["researchQuestion"],
                    control_run_id=attempt["runId"],
                    model_id=self.model_id,
                    bridge_url=self.bridge_url,
                    collection_id=collection_id,
                )
            except LdrConflict:
                project, _ = await asyncio.to_thread(
                    self.projects.update_attempt,
                    project_id,
                    attempt["runId"],
                    {
                        "phase": "terminal",
                        "outcome": "failed",
                        "rawStatus": "rejected",
                        "terminalAt": utc_now(),
                        "errorType": "upstream_rejected",
                        "errorMessage": "LDR refused the fixed research request",
                    },
                )
                raise
            except (LdrUnavailable, LdrProtocolError):
                # The upstream can accept the request before the response is
                # lost or found malformed. Keep the durable queued attempt and
                # reconcile it by the metadata marker on later ticks.
                return project, True

            phase = "queued" if raw_start == "queued" else "running"
            project, _ = await asyncio.to_thread(
                self.projects.update_attempt,
                project_id,
                attempt["runId"],
                {
                    "ldrResearchId": ldr_id,
                    "phase": phase,
                    "rawStatus": raw_start,
                    "startedAt": utc_now() if phase == "running" else None,
                },
            )
            return project, True

    async def cancel(self, project_id: str) -> dict[str, Any]:
        project = await asyncio.to_thread(self.projects.get, project_id)
        run_id = project["literature"]["activeRunId"]
        if not run_id:
            return project
        _, attempt = await asyncio.to_thread(
            self.projects.update_attempt,
            project_id,
            run_id,
            {"cancelRequestedAt": attempt_time(project, run_id, "cancelRequestedAt")},
        )
        ldr_id = attempt.get("ldrResearchId")
        if not ldr_id:
            try:
                ldr_id = await asyncio.to_thread(
                    self.ldr.find_research_by_run_id, run_id
                )
            except (LdrSessionExpired, LdrUnavailable):
                return (await asyncio.to_thread(self.projects.get, project_id))
            if ldr_id:
                await asyncio.to_thread(
                    self.projects.update_attempt,
                    project_id,
                    run_id,
                    {"ldrResearchId": ldr_id},
                )
        if ldr_id and not attempt.get("cancelAppliedAt"):
            try:
                await asyncio.to_thread(self.ldr.terminate, ldr_id)
            except (LdrSessionExpired, LdrUnavailable):
                return (await asyncio.to_thread(self.projects.get, project_id))
            project, _ = await asyncio.to_thread(
                self.projects.update_attempt,
                project_id,
                run_id,
                {"cancelAppliedAt": utc_now()},
            )
            return project
        return await asyncio.to_thread(self.projects.get, project_id)

    async def sync(self, project_id: str) -> dict[str, Any]:
        project = await asyncio.to_thread(self.projects.get, project_id)
        run_id = project["literature"].get("completedRunId")
        if not run_id:
            run_id = next(
                (
                    attempt["runId"]
                    for attempt in reversed(project["literature"]["attempts"])
                    if attempt.get("rawStatus") == "completed"
                    and attempt.get("ldrResearchId")
                ),
                None,
            )
        if not run_id:
            raise ProjectConflict("project does not have completed upstream artifacts")
        _, attempt = await asyncio.to_thread(
            self.projects.get_attempt, project_id, run_id
        )
        if attempt.get("rawStatus") != "completed" or not attempt.get(
            "ldrResearchId"
        ):
            raise ProjectConflict("completed upstream artifacts are unavailable")
        await self._sync_completed(
            project_id,
            run_id,
            attempt["ldrResearchId"],
            surface_upstream_errors=True,
            force=True,
        )
        return await asyncio.to_thread(self.projects.get, project_id)

    async def tick(self) -> None:
        try:
            session = await asyncio.to_thread(self.ldr.session_status)
        except LdrUnavailable:
            return
        if session["status"] != "ready":
            return
        for project, attempt in await asyncio.to_thread(
            self.projects.active_literature
        ):
            await self._reconcile_attempt(project, attempt)

    async def _reconcile_attempt(
        self, project: dict[str, Any], attempt: dict[str, Any]
    ) -> None:
        project_id = project["projectId"]
        run_id = attempt["runId"]
        ldr_id = attempt.get("ldrResearchId")
        if not ldr_id:
            try:
                ldr_id = await asyncio.to_thread(
                    self.ldr.find_research_by_run_id, run_id
                )
            except (LdrSessionExpired, LdrUnavailable):
                return
            if not ldr_id:
                attempts = int(attempt.get("reconcileAttempts", 0)) + 1
                fields: dict[str, Any] = {"reconcileAttempts": attempts}
                if attempts >= MAX_RECONCILE_ATTEMPTS:
                    fields.update(
                        {
                            "phase": "terminal",
                            "outcome": "infrastructure_error",
                            "rawStatus": attempt.get("rawStatus"),
                            "terminalAt": utc_now(),
                            "errorType": "missing_upstream_run",
                            "errorMessage": "LDR did not expose an accepted research run",
                        }
                    )
                await asyncio.to_thread(
                    self.projects.update_attempt, project_id, run_id, fields
                )
                return
            _, attempt = await asyncio.to_thread(
                self.projects.update_attempt,
                project_id,
                run_id,
                {"ldrResearchId": ldr_id, "reconcileAttempts": 0},
            )

        if attempt.get("cancelRequestedAt") and not attempt.get("cancelAppliedAt"):
            try:
                await asyncio.to_thread(self.ldr.terminate, ldr_id)
            except (LdrSessionExpired, LdrUnavailable):
                return
            _, attempt = await asyncio.to_thread(
                self.projects.update_attempt,
                project_id,
                run_id,
                {"cancelAppliedAt": utc_now()},
            )

        try:
            upstream = await asyncio.to_thread(self.ldr.research_status, ldr_id)
        except (LdrSessionExpired, LdrUnavailable):
            return
        except LdrProtocolError:
            await self._record_status_failure(project_id, attempt)
            return
        raw = upstream.get("status")
        if not isinstance(raw, str):
            await self._record_status_failure(project_id, attempt)
            return
        raw = raw.casefold()
        fields = {
            "rawStatus": raw,
            "statusFailures": 0,
            "progress": normalize_progress(upstream.get("progress")),
            "latestLog": upstream.get("log_entry")
            if isinstance(upstream.get("log_entry"), dict)
            else None,
        }
        if raw == "queued":
            fields.update({"phase": "queued", "outcome": None})
        elif raw == "in_progress":
            fields.update(
                {
                    "phase": "running",
                    "outcome": None,
                    "startedAt": attempt.get("startedAt") or utc_now(),
                }
            )
        elif raw == "completed":
            fields.update(
                {"phase": "terminal", "outcome": "completed", "terminalAt": utc_now()}
            )
        elif raw == "suspended":
            fields.update(
                {"phase": "terminal", "outcome": "cancelled", "terminalAt": utc_now()}
            )
        elif raw in {"failed", "error"}:
            outcome = (
                "cancelled"
                if attempt.get("cancelRequestedAt") and attempt.get("cancelAppliedAt")
                else "failed"
            )
            error_info = upstream.get("metadata", {}).get("error_info", {})
            fields.update(
                {
                    "phase": "terminal",
                    "outcome": outcome,
                    "terminalAt": utc_now(),
                    "errorType": error_info.get("type")
                    if isinstance(error_info, dict)
                    else "upstream_error",
                    "errorMessage": error_info.get("message")
                    if isinstance(error_info, dict)
                    else "LDR research failed",
                }
            )
        else:
            await asyncio.to_thread(
                self.projects.update_attempt,
                project_id,
                run_id,
                {"rawStatus": raw},
            )
            await self._record_status_failure(project_id, attempt)
            return
        project, updated_attempt = await asyncio.to_thread(
            self.projects.update_attempt, project_id, run_id, fields
        )
        if raw == "completed":
            await self._sync_completed(
                project_id,
                run_id,
                str(updated_attempt["ldrResearchId"]),
            )

    async def _sync_completed(
        self,
        project_id: str,
        run_id: str,
        ldr_research_id: str,
        *,
        surface_upstream_errors: bool = False,
        force: bool = False,
    ) -> None:
        async with self._sync_lock:
            project, attempt = await asyncio.to_thread(
                self.projects.get_attempt, project_id, run_id
            )
            if attempt.get("integrityStatus") == "verified" and not force:
                return
            try:
                report = await asyncio.to_thread(self.ldr.report, ldr_research_id)
                quarto = await asyncio.to_thread(
                    self.ldr.export, ldr_research_id, "quarto"
                )
                ris = await asyncio.to_thread(self.ldr.export, ldr_research_id, "ris")
                directory = await asyncio.to_thread(
                    self.projects.run_directory, project_id, run_id
                )
                synced_at = utc_now()
                receipt_attempt = dict(attempt)
                receipt_attempt.update(
                    {
                        "outcome": "completed",
                        "integrityStatus": "verified",
                        "syncedAt": synced_at,
                        "errorType": None,
                        "errorMessage": None,
                    }
                )
                manifest = await asyncio.to_thread(
                    self.artifacts.persist,
                    run_directory=directory,
                    project=project,
                    attempt=receipt_attempt,
                    report=report,
                    quarto_zip=quarto,
                    ris=ris,
                )
            except (
                LdrSessionExpired,
                LdrUnavailable,
                LdrProtocolError,
                LiteratureArtifactError,
            ) as exc:
                error_message = (
                    "The upstream review completed, but its result package failed "
                    "integrity validation or synchronization"
                )
                if isinstance(exc, LiteratureArtifactError):
                    error_message = f"{error_message}: {exc}"
                await asyncio.to_thread(
                    self.projects.update_attempt,
                    project_id,
                    run_id,
                    {
                        "outcome": "infrastructure_error",
                        "integrityStatus": "failed",
                        "errorType": "artifact_sync_failed",
                        "errorMessage": error_message,
                    },
                )
                if surface_upstream_errors and isinstance(
                    exc, (LdrSessionExpired, LdrUnavailable)
                ):
                    raise
                return
            await asyncio.to_thread(
                self.projects.update_attempt,
                project_id,
                run_id,
                {
                    "outcome": "completed",
                    "integrityStatus": "verified",
                    "syncedAt": synced_at,
                    "artifacts": manifest,
                    "errorType": None,
                    "errorMessage": None,
                },
            )

    async def _record_status_failure(
        self, project_id: str, attempt: dict[str, Any]
    ) -> None:
        failures = int(attempt.get("statusFailures", 0)) + 1
        fields: dict[str, Any] = {"statusFailures": failures}
        if failures >= MAX_STATUS_FAILURES:
            fields.update(
                {
                    "phase": "terminal",
                    "outcome": "infrastructure_error",
                    "terminalAt": utc_now(),
                    "errorType": "invalid_upstream_status",
                    "errorMessage": "LDR returned an unknown or malformed status",
                }
            )
        await asyncio.to_thread(
            self.projects.update_attempt,
            project_id,
            attempt["runId"],
            fields,
        )

    async def _require_exact_collection_scope(
        self, collection_id: str | None
    ) -> None:
        collections = await asyncio.to_thread(self.ldr.collections)
        exposed_collection_ids = {
            str(item.get("id"))
            for item in collections
            if item.get("is_public") is True
            and item.get("agent_enabled") is True
            and item.get("id")
        }
        if collection_id is None:
            if exposed_collection_ids:
                raise ProjectConflict(
                    "public research-agent collections must match the selected project collection"
                )
            return
        collection = next(
            (item for item in collections if item.get("id") == collection_id), None
        )
        if collection is None:
            raise ProjectConflict("LDR collection was not found")
        document_count = int(collection.get("document_count") or 0)
        indexed_count = int(collection.get("indexed_document_count") or 0)
        if (
            document_count <= 0
            or indexed_count < document_count
            or collection.get("is_public") is not True
            or collection.get("agent_enabled") is not True
        ):
            raise ProjectConflict(
                "collection must be fully indexed, public, and enabled for the research agent"
            )
        if exposed_collection_ids != {collection_id}:
            raise ProjectConflict(
                "public research-agent collections must match the selected project collection"
            )


def attempt_time(project: dict[str, Any], run_id: str, field: str) -> str:
    for attempt in project["literature"]["attempts"]:
        if attempt["runId"] == run_id:
            return attempt.get(field) or utc_now()
    return utc_now()


def normalize_progress(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, min(int(value), 100))
    return 0
