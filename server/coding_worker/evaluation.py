from __future__ import annotations

import io
import secrets
import tarfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from .contracts import TERMINAL_STATES, WorkerArtifact
from .service import CodingWorkerService
from .store import WorkerConflictError


class LegacyEvaluationAdapter:
    """Evaluation-only facade loaded solely by an enabled evaluation profile."""

    def __init__(
        self,
        service: CodingWorkerService,
        *,
        attestation_reader: Callable[
            [], Awaitable[Mapping[str, Mapping[str, Any]]]
        ],
        controller_generation: int | Callable[[], int],
        server_generation: str | None = None,
    ) -> None:
        self._service = service
        self._attestation_reader = attestation_reader
        self._controller_generation = (
            controller_generation
            if callable(controller_generation)
            else lambda: controller_generation
        )
        self._server_generation = server_generation or secrets.token_hex(16)

    @property
    def enabled(self) -> bool:
        return True

    async def attestation(self) -> Mapping[str, Any]:
        from .harness_v3 import (
            SERVER_HARNESS_CODE_FILES,
            harness_code_bundle_sha256,
        )

        providers = dict(await self._attestation_reader())
        if len(providers) != self._service.max_active_tasks:
            raise WorkerConflictError(
                "Provider Harness attestation is incomplete.",
                code="harness_attestation_unavailable",
            )
        return {
            "protocol": "modelmirror-coding-harness-attestation/v1",
            "server_code_bundle_sha256": harness_code_bundle_sha256(
                Path(__file__).resolve().parent,
                SERVER_HARNESS_CODE_FILES,
            ),
            "server_generation": self._server_generation,
            "controller_generation": self._controller_generation(),
            "providers": providers,
        }

    def arm_fault(self, task_id: str, component: str, point: str) -> None:
        self._service.arm_harness_fault(task_id, component, point)

    def export_workspace(
        self, task_id: str, *, harness_v3: bool
    ) -> WorkerArtifact:
        task = self._service.store.get_task(task_id)
        if task.workspace_id is None:
            raise WorkerConflictError(
                "Workspace is not ready.", code="workspace_not_ready"
            )
        if task.state not in TERMINAL_STATES:
            raise WorkerConflictError(
                "Parity export requires a terminal task.",
                code="parity_task_not_terminal",
            )
        workspace = self._service.workspace_broker
        snapshot = workspace.capture_snapshot(task.workspace_id)
        files = workspace.snapshot_files(task.workspace_id, snapshot)
        usage = self._service.store.budget_usage(task_id)
        return self._service.store.create_artifact(
            task_id=task_id,
            media_type=(
                "application/vnd.modelmirror.harness-workspace+tar"
                if harness_v3
                else "application/vnd.modelmirror.parity-workspace+tar"
            ),
            content=_deterministic_workspace_tar(files),
            metadata={
                "kind": (
                    "harness_workspace_export"
                    if harness_v3
                    else "parity_workspace_export"
                ),
                "workspace_tree_hash": snapshot.tree_hash,
                "file_count": len(files),
                "active_seconds": usage.active_seconds,
                "tool_calls": usage.tool_calls,
                "turns_started": usage.turns_started,
            },
        )


def _deterministic_workspace_tar(files: tuple[Any, ...]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for entry in sorted(files, key=lambda item: item.path):
            info = tarfile.TarInfo(entry.path)
            info.size = len(entry.content)
            info.mode = 0o755 if entry.executable else 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(entry.content))
    return output.getvalue()
