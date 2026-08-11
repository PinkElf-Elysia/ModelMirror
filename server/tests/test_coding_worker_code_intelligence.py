from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from server.coding_worker.contracts import (
    AcceptanceCheck,
    AcceptanceContract,
    Origin,
    PolicyProfile,
    TaskSpec,
    TaskState,
    WorkspaceSource,
)
from server.coding_worker.store import CodingWorkerStore
from server.coding_worker.tool_broker import ToolBroker, ToolBrokerError
from server.coding_worker.workspace import InMemoryWorkspaceSourceAdapter, WorkspaceBroker


CODE_RANGE = {
    "start": {"line": 0, "character": 0},
    "end": {"line": 0, "character": 5},
}


class _CodeExecutor:
    def __init__(self, repository: Path) -> None:
        self.repository = repository
        self.calls: list[dict[str, Any]] = []
        self.mutate = False
        self.invalid_path = False

    async def code_intelligence(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.mutate:
            self.repository.joinpath("unexpected.py").write_text(
                "changed = True\n", encoding="utf-8"
            )
        operation = kwargs["operation"]
        path = "outside.py" if self.invalid_path else kwargs["path"]
        result: dict[str, Any] = {"language": "python", "path": path}
        if operation == "symbols":
            result["symbols"] = [
                {
                    "name": "value",
                    "kind": 13,
                    "range": CODE_RANGE,
                    "selection_range": CODE_RANGE,
                    "container_name": None,
                }
            ]
        elif operation in {"definition", "references"}:
            result["locations"] = [{"path": path, "range": CODE_RANGE}]
        elif operation == "hover":
            result["hover"] = {"text": "value: int", "range": CODE_RANGE}
        else:
            result["diagnostics"] = [
                {
                    "range": CODE_RANGE,
                    "severity": 1,
                    "code": "reportAssignmentType",
                    "message": "Type is not assignable",
                }
            ]
        return result


async def _broker(
    tmp_path: Path,
) -> tuple[ToolBroker, CodingWorkerStore, str, WorkspaceBroker, str, _CodeExecutor]:
    source = WorkspaceSource(kind="manifest", source_id="code", revision="h0")
    workspace = WorkspaceBroker(
        tmp_path / "workspace",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {("code", "h0"): {"app.py": b"value: int = 'bad'\n"}}
            )
        },
        id_key=b"i" * 32,
    )
    prepared = await workspace.prepare(source)
    store = CodingWorkerStore(tmp_path / "store", master_key=Fernet.generate_key())
    task = store.create_task(
        TaskSpec(
            client_task_id="code-intelligence",
            origin=Origin(module="tests", object_id="code-intelligence"),
            objective="diagnose source",
            workspace_source=source,
            acceptance=AcceptanceContract(
                contract_id="contract",
                required_checks=(
                    AcceptanceCheck(
                        check_id="syntax", label="syntax", kind="command"
                    ),
                ),
            ),
            policy_profile=PolicyProfile.INSPECT,
            model_route="coding/default",
        )
    )
    store.transition(task.task_id, TaskState.PREPARING)
    store.transition(
        task.task_id, TaskState.RUNNING, workspace_id=prepared.workspace_id
    )
    executor = _CodeExecutor(workspace.repository_path(prepared.workspace_id))
    broker = ToolBroker(
        store=store, workspace_broker=workspace, executor=executor
    )
    entry = next(
        item
        for item in workspace.tree(prepared.workspace_id)
        if item.display_path == "app.py"
    )
    return broker, store, task.task_id, workspace, entry.entry_id, executor


@pytest.mark.asyncio
async def test_code_results_are_entry_task_and_tree_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_CODE_INTELLIGENCE_ENABLED", "true")
    broker, store, task_id, workspace, entry_id, executor = await _broker(tmp_path)
    workspace_id = store.get_task(task_id).workspace_id
    assert workspace_id is not None

    definition = await broker.execute(
        task_id=task_id,
        operation_id="code-definition",
        tool_name="code_definition",
        arguments={"entry_id": entry_id, "line": 0, "character": 1},
    )
    symbols = await broker.execute(
        task_id=task_id,
        operation_id="code-symbols",
        tool_name="code_symbols",
        arguments={"entry_id": entry_id},
    )
    references = await broker.execute(
        task_id=task_id,
        operation_id="code-references",
        tool_name="code_references",
        arguments={"entry_id": entry_id, "line": 0, "character": 1},
    )
    hover = await broker.execute(
        task_id=task_id,
        operation_id="code-hover",
        tool_name="code_hover",
        arguments={"entry_id": entry_id, "line": 0, "character": 1},
    )
    diagnostics = await broker.execute(
        task_id=task_id,
        operation_id="code-diagnostics",
        tool_name="code_diagnostics",
        arguments={"entry_id": entry_id},
    )
    replay = await broker.execute(
        task_id=task_id,
        operation_id="code-diagnostics",
        tool_name="code_diagnostics",
        arguments={"entry_id": entry_id},
    )

    tree_hash = workspace.current_tree_hash(workspace_id)
    assert definition.data["task_id"] == task_id
    assert definition.data["entry_id"] == entry_id
    assert definition.data["workspace_tree_hash"] == tree_hash
    assert definition.data["locations"] == [
        {"entry_id": entry_id, "range": CODE_RANGE}
    ]
    assert symbols.data["symbols"][0]["name"] == "value"
    assert references.data["locations"][0]["entry_id"] == entry_id
    assert hover.data["hover"]["text"] == "value: int"
    diagnostic = diagnostics.data["diagnostics"][0]
    assert diagnostic["task_id"] == task_id
    assert diagnostic["entry_id"] == entry_id
    assert diagnostic["workspace_tree_hash"] == tree_hash
    assert diagnostic["severity"] == "error"
    assert replay == diagnostics
    assert len(executor.calls) == 5
    assert executor.calls[0]["path"] == "app.py"
    public = json.dumps(
        [definition.data, symbols.data, references.data, hover.data, diagnostics.data],
        sort_keys=True,
    )
    assert str(tmp_path) not in public
    assert '"path"' not in public


@pytest.mark.asyncio
async def test_code_intelligence_requires_both_flags_and_current_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    broker, _, task_id, _, entry_id, executor = await _broker(tmp_path)
    with pytest.raises(ToolBrokerError) as disabled:
        await broker.execute(
            task_id=task_id,
            operation_id="code-disabled",
            tool_name="code_symbols",
            arguments={"entry_id": entry_id},
        )
    assert disabled.value.code == "tool_not_allowed"
    assert executor.calls == []

    monkeypatch.setenv("CODING_WORKER_CODE_INTELLIGENCE_ENABLED", "true")
    with pytest.raises(ToolBrokerError) as foreign:
        await broker.execute(
            task_id=task_id,
            operation_id="code-foreign-entry",
            tool_name="code_symbols",
            arguments={"entry_id": "entry_not_from_this_workspace"},
        )
    assert foreign.value.code == "entry_not_found"
    assert executor.calls == []


@pytest.mark.asyncio
async def test_code_intelligence_rejects_tree_change_and_untrusted_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODING_WORKER_V15_ENABLED", "true")
    monkeypatch.setenv("CODING_WORKER_CODE_INTELLIGENCE_ENABLED", "true")
    broker, _, task_id, _, entry_id, executor = await _broker(tmp_path)
    executor.invalid_path = True
    with pytest.raises(ToolBrokerError) as invalid:
        await broker.execute(
            task_id=task_id,
            operation_id="code-invalid-path",
            tool_name="code_definition",
            arguments={"entry_id": entry_id, "line": 0, "character": 0},
        )
    assert invalid.value.code == "code_intelligence_invalid_response"

    executor.invalid_path = False
    executor.mutate = True
    with pytest.raises(ToolBrokerError) as changed:
        await broker.execute(
            task_id=task_id,
            operation_id="code-tree-changed",
            tool_name="code_symbols",
            arguments={"entry_id": entry_id},
        )
    assert changed.value.code == "workspace_tree_changed"
