from __future__ import annotations

import hashlib

from .contracts import EvidenceStatus, WorkerEvidence
from .store import CodingWorkerStore
from .tool_broker import ToolBroker, ToolBrokerError
from .workspace import WorkspaceBroker


class HarnessRunner:
    """Runs the immutable server-side acceptance contract and records evidence."""

    def __init__(
        self,
        *,
        store: CodingWorkerStore,
        workspace_broker: WorkspaceBroker,
        tool_broker: ToolBroker,
    ) -> None:
        self.store = store
        self.workspace_broker = workspace_broker
        self.tool_broker = tool_broker

    async def run_required_checks(self, task_id: str) -> tuple[WorkerEvidence, ...]:
        task = self.store.get_task(task_id)
        if task.workspace_id is None:
            raise ToolBrokerError("Task workspace is unavailable.", code="workspace_unavailable")
        results: list[WorkerEvidence] = []
        for check in task.spec.acceptance.required_checks:
            before = self.workspace_broker.current_tree_hash(task.workspace_id)
            operation_id = self._operation_id(task_id, check.check_id, before)
            if check.kind == "command":
                result = await self.tool_broker.execute(
                    task_id=task_id,
                    operation_id=operation_id,
                    tool_name="run_check",
                    arguments={"check_id": check.check_id},
                )
                exit_code = int(result.data["exit_code"])
                output = str(result.data.get("output", "")).encode("utf-8")
            elif check.kind == "diff":
                output = self.workspace_broker.diff(task.workspace_id)
                exit_code = 0 if output else 1
            else:
                output = f"Unsupported acceptance check kind: {check.kind}\n".encode()
                exit_code = 2
            after = self.workspace_broker.current_tree_hash(task.workspace_id)
            if after != before:
                raise ToolBrokerError(
                    "Workspace changed while acceptance was running.",
                    code="acceptance_workspace_changed",
                )
            artifact = self.store.create_artifact(
                task_id=task_id,
                media_type="text/plain; charset=utf-8",
                content=output,
                metadata={"check_id": check.check_id, "workspace_tree_hash": before},
            )
            results.append(
                self.store.record_evidence(
                    task_id=task_id,
                    check_id=check.check_id,
                    operation_id=operation_id,
                    workspace_tree_hash=before,
                    exit_code=exit_code,
                    artifact_id=artifact.artifact_id,
                )
            )
        return tuple(results)

    def acceptance_satisfied(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        if task.workspace_id is None:
            return False
        tree_hash = self.workspace_broker.current_tree_hash(task.workspace_id)
        evidence = self.store.list_evidence(task_id, current_tree_hash=tree_hash)
        latest: dict[str, WorkerEvidence] = {}
        for item in evidence:
            latest[item.check_id] = item
        if any(
            latest.get(check.check_id) is None
            or latest[check.check_id].status is not EvidenceStatus.PASSED
            for check in task.spec.acceptance.required_checks
        ):
            return False
        artifacts = self.store.list_artifacts(task_id)
        requirement_ids = {
            str(item.metadata.get("requirement_id"))
            for item in artifacts
            if item.metadata.get("workspace_tree_hash") == tree_hash
        }
        return all(
            requirement.artifact_id in requirement_ids
            for requirement in task.spec.acceptance.required_artifacts
        )

    @staticmethod
    def _operation_id(task_id: str, check_id: str, tree_hash: str) -> str:
        digest = hashlib.sha256(f"{task_id}\0{check_id}\0{tree_hash}".encode()).hexdigest()
        return f"acceptance_{digest[:32]}"
