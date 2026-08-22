from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

from server.coding_worker.ports import (
    CodingSubstrateHandle,
    EvaluationAdapter,
    ExecutionBackend,
    HarnessDriver,
    InteractionProjection,
    TaskControlPlane,
)


ROOT = Path(__file__).resolve().parents[2]


def _imports(relative_path: str) -> set[str]:
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            prefix = "." * node.level
            result.add(prefix + node.module)
    return result


def test_v19_ports_cover_control_harness_execution_projection_and_evaluation() -> None:
    assert {
        "control_plane",
        "projection",
        "harness_driver",
        "execution_backend",
        "evaluation",
    } == set(CodingSubstrateHandle.__dataclass_fields__)
    assert inspect.isclass(TaskControlPlane)
    assert inspect.isclass(InteractionProjection)
    assert inspect.isclass(HarnessDriver)
    assert inspect.isclass(ExecutionBackend)
    assert inspect.isclass(EvaluationAdapter)


def test_reference_lifecycles_fit_the_neutral_harness_driver() -> None:
    methods = set(HarnessDriver.__dict__)
    reference_mapping = {
        "acp-v2": {
            "initialize": "capabilities",
            "session/new": "open",
            "session/load": "restore",
            "session/prompt": "message",
            "session/cancel": "interrupt_turn",
        },
        "codex-app-server": {
            "initialize": "capabilities",
            "thread/start": "open",
            "thread/resume": "restore",
            "turn/start": "message",
            "turn/steer": "message",
            "turn/interrupt": "interrupt_turn",
        },
    }
    for mapping in reference_mapping.values():
        assert set(mapping.values()).issubset(methods)
    assert {"checkpoint", "close", "cancel"}.issubset(methods)


def test_pr_a_freezes_existing_boundary_debt_without_allowing_expansion() -> None:
    """PR B/C must shrink these exact exceptions to an empty set."""

    forbidden = {
        ".service",
        ".store",
        ".workspace",
        ".evidence",
        ".provider",
        ".executor",
        ".provider_rpc",
        ".opencode_provider",
        ".claude_provider",
        ".harness_v3",
        "server.coding_worker.api",
        "coding_worker.api",
    }
    actual = {
        path: sorted(_imports(path) & forbidden)
        for path in (
            "server/coding_worker/api.py",
            "server/coding_worker/sdk.py",
            "server/coding_runtime/api.py",
        )
    }
    assert actual == {
        "server/coding_worker/api.py": [],
        "server/coding_worker/sdk.py": [".service", ".workspace"],
        "server/coding_runtime/api.py": [
            "coding_worker.api",
            "server.coding_worker.api",
        ],
    }


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
        ".opencode_provider",
        ".claude_provider",
        ".provider_rpc",
        ".executor",
    }
    assert imports.isdisjoint(forbidden)
