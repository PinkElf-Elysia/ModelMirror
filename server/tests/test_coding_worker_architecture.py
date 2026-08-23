from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

from server.coding_worker.ports import (
    CodingSubstrateHandle,
    EvaluationAdapter,
    ExecutionBackend,
    HarnessDriver,
    HarnessSupervisor,
    InteractionProjection,
    TaskControlPlane,
)


ROOT = Path(__file__).resolve().parents[2]


def _imports_from_source(source: str, relative_path: str) -> set[str]:
    tree = ast.parse(source)
    module = Path(relative_path).with_suffix("").as_posix().replace("/", ".")
    package = module.rsplit(".", 1)[0]
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                reference = "." * node.level + (node.module or "")
                imported = resolve_name(reference, package)
            else:
                imported = node.module or ""
            if imported:
                result.add(imported)
                result.update(f"{imported}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args:
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if isinstance(node.func, ast.Attribute) and isinstance(
                node.func.value, ast.Name
            ):
                name = f"{node.func.value.id}.{node.func.attr}"
            if name in {"__import__", "importlib.import_module"}:
                target = node.args[0]
                if isinstance(target, ast.Constant) and isinstance(target.value, str):
                    result.add(target.value)
    return result


def _imports(relative_path: str) -> set[str]:
    return _imports_from_source(
        (ROOT / relative_path).read_text(encoding="utf-8"), relative_path
    )


def _matches_forbidden(imported: set[str], forbidden: set[str]) -> list[str]:
    return sorted(
        name
        for name in imported
        if any(name == item or name.startswith(item + ".") for item in forbidden)
    )


def test_v19_ports_cover_control_harness_execution_projection_and_evaluation() -> None:
    assert {
        "control_plane",
        "projection",
        "harness_supervisor",
        "harness_driver",
        "execution_backend",
        "evaluation",
    } == set(CodingSubstrateHandle.__dataclass_fields__)
    assert inspect.isclass(TaskControlPlane)
    assert inspect.isclass(InteractionProjection)
    assert inspect.isclass(HarnessSupervisor)
    assert inspect.isclass(HarnessDriver)
    assert inspect.isclass(ExecutionBackend)
    assert inspect.isclass(EvaluationAdapter)


def test_harness_supervision_is_not_part_of_the_session_driver() -> None:
    supervisor_methods = set(HarnessSupervisor.__dict__)
    driver_methods = set(HarnessDriver.__dict__)
    assert {
        "capabilities",
        "capabilities_for_slots",
        "controller_generation",
        "harness_attestations",
        "harness_descriptors_for_slots",
    }.issubset(supervisor_methods)
    assert {
        "open",
        "message",
        "steer",
        "cancel",
        "interrupt_turn",
        "checkpoint",
        "restore",
        "close",
    }.issubset(driver_methods)
    assert {
        "capabilities",
        "capabilities_for_slots",
        "controller_generation",
        "harness_attestations",
        "harness_descriptors_for_slots",
    }.isdisjoint(driver_methods)


def test_pr_a_freezes_existing_boundary_debt_without_allowing_expansion() -> None:
    """PR B/C must shrink these exact exceptions to an empty set."""

    forbidden = {
        "server.coding_worker.service",
        "server.coding_worker.store",
        "server.coding_worker.workspace",
        "server.coding_worker.evidence",
        "server.coding_worker.provider",
        "server.coding_worker.executor",
        "server.coding_worker.provider_rpc",
        "server.coding_worker.opencode_provider",
        "server.coding_worker.claude_provider",
        "server.coding_worker.harness_v3",
        "server.coding_worker.evaluation_driver",
        "server.coding_worker.evaluation_loader",
        "server.coding_worker.evaluation_sidecar",
        "server.coding_worker.acp_driver",
        "server.coding_worker.codex_app_server_driver",
        "agentclientprotocol",
        "coding_worker.service",
        "coding_worker.store",
        "coding_worker.workspace",
        "coding_worker.evidence",
        "coding_worker.provider",
        "coding_worker.executor",
        "coding_worker.provider_rpc",
        "coding_worker.opencode_provider",
        "coding_worker.claude_provider",
        "coding_worker.harness_v3",
        "coding_worker.evaluation_driver",
        "coding_worker.evaluation_loader",
        "coding_worker.evaluation_sidecar",
        "coding_worker.acp_driver",
        "coding_worker.codex_app_server_driver",
        "server.coding_worker.api",
        "coding_worker.api",
    }
    actual = {
        path: _matches_forbidden(_imports(path), forbidden)
        for path in (
            "server/coding_worker/api.py",
            "server/coding_worker/sdk.py",
            "server/coding_runtime/api.py",
        )
    }
    assert actual == {
        "server/coding_worker/api.py": [],
        "server/coding_worker/sdk.py": [],
        "server/coding_runtime/api.py": [],
    }


def test_dependency_gate_detects_absolute_relative_and_dynamic_bypasses() -> None:
    forbidden = {"server.coding_worker.store"}
    sources = (
        "from server.coding_worker.store import CodingWorkerStore\n",
        "from . import store\n",
        "import importlib\nimportlib.import_module('server.coding_worker.store')\n",
        "__import__('server.coding_worker.store')\n",
    )

    for source in sources:
        imported = _imports_from_source(source, "server/coding_worker/api.py")
        assert _matches_forbidden(imported, forbidden)


def test_production_api_imports_when_evaluation_modules_are_unavailable() -> None:
    script = """
import importlib.abc
import sys

class DenyEvaluation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            'server.coding_worker.harness_v3',
            'server.coding_worker.parity',
            'server.coding_worker.evaluation',
            'server.coding_worker.evaluation_driver',
            'server.coding_worker.evaluation_loader',
            'server.coding_worker.evaluation_sidecar',
            'server.coding_worker.acp_driver',
            'server.coding_worker.codex_app_server_driver',
            'agentclientprotocol',
        }:
            raise ImportError(f'evaluation module denied: {fullname}')
        return None

sys.meta_path.insert(0, DenyEvaluation())
import server.coding_worker.api
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_scheduler_has_no_concrete_supplier_or_sidecar_imports() -> None:
    imports = _imports("server/coding_worker/service.py")
    forbidden = {
        "server.coding_worker.opencode_provider",
        "server.coding_worker.claude_provider",
        "server.coding_worker.provider_rpc",
        "server.coding_worker.executor",
    }
    assert _matches_forbidden(imports, forbidden) == []


def test_control_plane_uses_only_neutral_harness_contracts() -> None:
    forbidden = {
        "server.coding_worker.provider",
        "server.coding_worker.provider_rpc",
        "server.coding_worker.harness_driver",
        "server.coding_worker.opencode_provider",
        "server.coding_worker.claude_provider",
    }
    for relative_path in (
        "server/coding_worker/ports.py",
        "server/coding_worker/service.py",
        "server/coding_worker/harness_contracts.py",
    ):
        assert _matches_forbidden(_imports(relative_path), forbidden) == []
