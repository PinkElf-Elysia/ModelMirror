from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from server.coding_worker.executor import SidecarExecutor


pytestmark = pytest.mark.skipif(
    os.name == "nt"
    or shutil.which("pyright-langserver") is None
    or shutil.which("typescript-language-server") is None,
    reason="the fixed language servers run in the Linux executor image",
)


def _executor(tmp_path: Path) -> tuple[SidecarExecutor, Path, Path]:
    repository = tmp_path / "slot" / "workspaces" / "workspace_lsp" / "repo"
    repository.mkdir(parents=True)
    runtime = tmp_path / "slot" / "runtime"
    return (
        SidecarExecutor(
            lambda workspace_id: (
                repository if workspace_id == "workspace_lsp" else tmp_path / "missing"
            ),
            runtime_root=runtime,
        ),
        repository,
        runtime,
    )


async def _query(
    executor: SidecarExecutor,
    operation_id: str,
    operation: str,
    path: str,
    *,
    line: int = 0,
    character: int = 0,
) -> dict[str, object]:
    return await executor.code_intelligence(
        task_id="task_lsp",
        workspace_id="workspace_lsp",
        operation_id=operation_id,
        operation=operation,
        path=path,
        line=line,
        character=character,
    )


@pytest.mark.asyncio
async def test_pyright_symbols_definition_references_hover_and_diagnostics(
    tmp_path: Path,
) -> None:
    executor, repository, runtime = _executor(tmp_path)
    repository.joinpath("calculation.py").write_text(
        "def multiply(left: int, right: int) -> int:\n"
        "    return left * right\n\n"
        "value = multiply('wrong', 2)\n",
        encoding="utf-8",
    )
    original = repository.joinpath("calculation.py").read_bytes()

    symbols = await _query(
        executor, "python_symbols", "symbols", "calculation.py"
    )
    definition = await _query(
        executor,
        "python_definition",
        "definition",
        "calculation.py",
        line=3,
        character=10,
    )
    references = await _query(
        executor,
        "python_references",
        "references",
        "calculation.py",
        line=0,
        character=6,
    )
    hover = await _query(
        executor,
        "python_hover",
        "hover",
        "calculation.py",
        line=3,
        character=10,
    )
    diagnostics = await _query(
        executor, "python_diagnostics", "diagnostics", "calculation.py"
    )

    assert any(item["name"] == "multiply" for item in symbols["symbols"])
    assert definition["locations"][0]["path"] == "calculation.py"
    assert len(references["locations"]) >= 2
    assert "multiply" in hover["hover"]["text"]
    assert any(
        "not assignable" in item["message"]
        for item in diagnostics["diagnostics"]
    ), diagnostics
    assert repository.joinpath("calculation.py").read_bytes() == original
    assert str(tmp_path) not in json.dumps(
        [symbols, definition, references, hover, diagnostics]
    )
    assert not runtime.joinpath("lsp", "task_lsp").exists()


@pytest.mark.asyncio
async def test_typescript_server_definition_and_diagnostics_rebuild_after_restart(
    tmp_path: Path,
) -> None:
    executor, repository, runtime = _executor(tmp_path)
    repository.joinpath("example.ts").write_text(
        "export function double(value: number): number {\n"
        "  return value * 2;\n"
        "}\n\n"
        "const total: string = double(2);\n",
        encoding="utf-8",
    )

    definition = await _query(
        executor,
        "typescript_definition",
        "definition",
        "example.ts",
        line=4,
        character=24,
    )
    diagnostics = await _query(
        executor, "typescript_diagnostics", "diagnostics", "example.ts"
    )
    restarted = SidecarExecutor(
        lambda _workspace_id: repository,
        runtime_root=runtime,
    )
    symbols = await _query(
        restarted, "typescript_symbols_restart", "symbols", "example.ts"
    )

    assert definition["locations"][0]["path"] == "example.ts"
    assert any("string" in item["message"] for item in diagnostics["diagnostics"])
    assert any(item["name"] == "double" for item in symbols["symbols"])
    assert not runtime.joinpath("lsp", "task_lsp").exists()


def test_executor_image_pins_language_servers_with_integrity() -> None:
    dockerfile = Path(__file__).parents[2].joinpath(
        "server", "coding_worker", "Dockerfile.v14"
    ).read_text(encoding="utf-8")
    assert "PYRIGHT_VERSION=1.1.411" in dockerfile
    assert "PYRIGHT_INTEGRITY=sha512-03S/vmS5lF1S/" in dockerfile
    assert "TYPESCRIPT_LANGUAGE_SERVER_VERSION=5.3.0" in dockerfile
    assert "TYPESCRIPT_LANGUAGE_SERVER_INTEGRITY=sha512-5puofxZHgFdAY" in dockerfile
    assert "TYPESCRIPT_VERSION=5.9.3" in dockerfile
    assert "TYPESCRIPT_INTEGRITY=sha512-jl1vZzPDinLr9" in dockerfile
    assert dockerfile.count("language server package integrity mismatch") == 1
    assert "pyright-langserver" in dockerfile
    assert "typescript-language-server" in dockerfile
