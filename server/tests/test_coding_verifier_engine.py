from __future__ import annotations

import asyncio
import difflib
import os
import sys
from pathlib import Path

import pytest

from server.coding_runtime.verification import (
    VerificationResult,
    VerificationState,
    VerificationStepId,
)
from server.coding_runtime.patch_policy import (
    PatchPolicyError,
    snapshot_fingerprint as shared_snapshot_fingerprint,
    validate_patch,
)
from server.coding_verifier.engine import (
    CodingVerifierEngine,
    CommandResult,
    FixedCommand,
    SubprocessVerificationRunner,
    VerificationEngineError,
    snapshot_fingerprint,
    validate_verification_patch,
)


class FakeRunner:
    def __init__(
        self,
        *,
        results: dict[VerificationStepId, CommandResult] | None = None,
        wait_for_cancel: bool = False,
    ) -> None:
        self.results = dict(results or {})
        self.wait_for_cancel = wait_for_cancel
        self.calls: list[tuple[VerificationStepId, str]] = []
        self.frontend_dependencies: list[Path | None] = []
        self.started = asyncio.Event()

    async def run(
        self,
        step_id: VerificationStepId,
        workspace: Path,
    ) -> CommandResult:
        observed_test = (workspace / "server/tests/test_example.py").read_text(
            encoding="utf-8"
        ) if (workspace / "server/tests/test_example.py").exists() else ""
        self.calls.append((step_id, observed_test))
        node_modules = workspace / "client/node_modules"
        self.frontend_dependencies.append(
            node_modules.resolve() if node_modules.is_symlink() else None
        )
        self.started.set()
        if self.wait_for_cancel:
            await asyncio.Future()
        return self.results.get(
            step_id,
            CommandResult(
                exit_code=0,
                stdout="ok",
                stderr="",
                duration_ms=12,
            ),
        )


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "server/tests").mkdir(parents=True)
    (source / "server/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "server/tests/test_example.py").write_text(
        "def test_value():\n    assert True\n",
        encoding="utf-8",
    )
    (source / "client/src").mkdir(parents=True)
    (source / "client/src/App.tsx").write_text(
        "export const value = 1;\n",
        encoding="utf-8",
    )
    return source


def modified_patch(path: str, old: str, new: str) -> str:
    old_text = old if old.endswith("\n") else f"{old}\n"
    new_text = new if new.endswith("\n") else f"{new}\n"
    body = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}"


def test_snapshot_fingerprint_changes_with_content(source_root: Path) -> None:
    original = snapshot_fingerprint(source_root)
    (source_root / "server/app.py").write_text("VALUE = 2\n", encoding="utf-8")

    assert snapshot_fingerprint(source_root) != original


def test_shared_fingerprint_can_ignore_only_named_root_entry(
    source_root: Path,
) -> None:
    original = shared_snapshot_fingerprint(source_root)
    (source_root / ".git").write_text("gitdir: ignored\n", encoding="utf-8")

    assert (
        shared_snapshot_fingerprint(
            source_root,
            ignored_root_names={".git"},
        )
        == original
    )
    assert shared_snapshot_fingerprint(source_root) != original


def test_snapshot_fingerprint_rejects_symlinks(
    source_root: Path,
    tmp_path: Path,
) -> None:
    link = source_root / "linked.py"
    try:
        link.symlink_to(tmp_path / "outside.py")
    except OSError:
        pytest.skip("This host does not allow symbolic links")

    with pytest.raises(VerificationEngineError, match="symlink"):
        snapshot_fingerprint(source_root)


def test_engine_rejects_workspace_that_contains_source(
    source_root: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(VerificationEngineError) as raised:
        CodingVerifierEngine(source_root, tmp_path)

    assert raised.value.code == "unsafe_workspace_root"


def test_patch_validation_matches_expected_paths() -> None:
    patch = modified_patch("server/app.py", "VALUE = 1", "VALUE = 2")

    assert validate_verification_patch(
        patch,
        expected_paths=["server/app.py"],
    ) == ("server/app.py",)
    assert validate_patch(
        patch,
        expected_paths=["server/app.py"],
    ) == ("server/app.py",)


@pytest.mark.parametrize(
    "patch",
    [
        (
            "diff --git a/server/app.py b/../outside.py\n"
            "--- a/server/app.py\n+++ b/../outside.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        ),
        (
            "diff --git a/server/app.py b/server/app.py\n"
            "deleted file mode 100644\n"
            "--- a/server/app.py\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-old\n"
        ),
        "GIT binary patch\n",
    ],
)
def test_patch_validation_rejects_unsafe_forms(patch: str) -> None:
    with pytest.raises(VerificationEngineError):
        validate_verification_patch(
            patch,
            expected_paths=["server/app.py"],
        )
    with pytest.raises(PatchPolicyError):
        validate_patch(
            patch,
            expected_paths=["server/app.py"],
        )


@pytest.mark.asyncio
async def test_engine_applies_patch_and_cleans_workspace(
    source_root: Path,
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    workspace = tmp_path / "workspace"
    engine = CodingVerifierEngine(source_root, workspace, runner=runner)
    patch = modified_patch("server/app.py", "VALUE = 1", "VALUE = 2")

    report = await engine.verify(
        revision=1,
        patch=patch,
        paths=["server/app.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert report.state is VerificationState.COMPLETED
    assert report.result is VerificationResult.PASSED
    assert [call[0] for call in runner.calls] == [
        VerificationStepId.BACKEND_TESTS
    ]
    assert workspace.exists() is False
    assert (source_root / "server/app.py").read_text(encoding="utf-8") == (
        "VALUE = 1\n"
    )


@pytest.mark.asyncio
async def test_test_change_runs_baseline_then_draft_tests(
    source_root: Path,
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    engine = CodingVerifierEngine(
        source_root,
        tmp_path / "workspace",
        runner=runner,
    )
    old = "def test_value():\n    assert True\n"
    new = "def test_updated_value():\n    assert True\n"
    patch = modified_patch("server/tests/test_example.py", old, new)

    report = await engine.verify(
        revision=2,
        patch=patch,
        paths=["server/tests/test_example.py"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert report.result is VerificationResult.PASSED
    assert runner.calls[0][0] is VerificationStepId.BACKEND_BASELINE_TESTS
    assert old in runner.calls[0][1]
    assert runner.calls[1][0] is VerificationStepId.BACKEND_DRAFT_TESTS
    assert new in runner.calls[1][1]


@pytest.mark.asyncio
async def test_engine_collects_all_steps_and_redacts_failure(
    source_root: Path,
    tmp_path: Path,
) -> None:
    secret = "sk-" + ("z" * 24)
    runner = FakeRunner(
        results={
            VerificationStepId.BACKEND_TESTS: CommandResult(
                exit_code=1,
                stdout=f"/workspace/server/app.py failed {secret}",
                stderr="",
                duration_ms=20,
            )
        }
    )
    engine = CodingVerifierEngine(
        source_root,
        tmp_path / "workspace",
        runner=runner,
    )
    patch = modified_patch("server/app.py", "VALUE = 1", "VALUE = 2")
    patch += modified_patch(
        "client/src/App.tsx",
        "export const value = 1;",
        "export const value = 2;",
    )

    report = await engine.verify(
        revision=3,
        patch=patch,
        paths=["server/app.py", "client/src/App.tsx"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert report.result is VerificationResult.FAILED
    assert len(report.steps) == 2
    assert report.steps[1].result is VerificationResult.PASSED
    assert secret not in report.steps[0].details
    assert "/workspace" not in report.steps[0].details


@pytest.mark.asyncio
async def test_engine_links_preinstalled_frontend_dependencies(
    source_root: Path,
    tmp_path: Path,
) -> None:
    dependencies = tmp_path / "dependencies"
    dependencies.mkdir()
    runner = FakeRunner()
    engine = CodingVerifierEngine(
        source_root,
        tmp_path / "workspace",
        frontend_dependencies=dependencies,
        runner=runner,
    )

    report = await engine.verify(
        revision=4,
        patch=modified_patch(
            "client/src/App.tsx",
            "export const value = 1;",
            "export const value = 2;",
        ),
        paths=["client/src/App.tsx"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert report.result is VerificationResult.PASSED
    assert runner.frontend_dependencies == [dependencies.resolve()]
    assert (source_root / "client/node_modules").exists() is False


@pytest.mark.asyncio
async def test_snapshot_mismatch_fails_before_workspace_creation(
    source_root: Path,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    engine = CodingVerifierEngine(source_root, workspace, runner=FakeRunner())

    with pytest.raises(VerificationEngineError) as raised:
        await engine.verify(
            revision=1,
            patch=modified_patch("server/app.py", "VALUE = 1", "VALUE = 2"),
            paths=["server/app.py"],
            expected_fingerprint="0" * 64,
        )

    assert raised.value.code == "snapshot_mismatch"
    assert workspace.exists() is False


@pytest.mark.asyncio
async def test_cancellation_cleans_workspace(
    source_root: Path,
    tmp_path: Path,
) -> None:
    runner = FakeRunner(wait_for_cancel=True)
    workspace = tmp_path / "workspace"
    engine = CodingVerifierEngine(source_root, workspace, runner=runner)
    task = asyncio.create_task(
        engine.verify(
            revision=4,
            patch=modified_patch("server/app.py", "VALUE = 1", "VALUE = 2"),
            paths=["server/app.py"],
            expected_fingerprint=engine.source_fingerprint,
        )
    )
    await asyncio.wait_for(runner.started.wait(), timeout=2)

    task.cancel()
    report = await task

    assert report.state is VerificationState.CANCELLED
    assert report.result is VerificationResult.NOT_RUN
    assert workspace.exists() is False


@pytest.mark.asyncio
async def test_dependency_change_does_not_run_commands(
    source_root: Path,
    tmp_path: Path,
) -> None:
    (source_root / "server/requirements.txt").write_text(
        "fastapi==0.116.2\n",
        encoding="utf-8",
    )
    runner = FakeRunner()
    engine = CodingVerifierEngine(
        source_root,
        tmp_path / "workspace",
        runner=runner,
    )
    patch = modified_patch(
        "server/requirements.txt",
        "fastapi==0.116.2",
        "fastapi==0.117.0",
    )

    report = await engine.verify(
        revision=5,
        patch=patch,
        paths=["server/requirements.txt"],
        expected_fingerprint=engine.source_fingerprint,
    )

    assert report.result is VerificationResult.NOT_RUN
    assert report.reason == "dependency_change_unsupported"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_subprocess_runner_bounds_output(tmp_path: Path) -> None:
    runner = SubprocessVerificationRunner(
        {
            VerificationStepId.BACKEND_TESTS: FixedCommand(
                argv=(
                    sys.executable,
                    "-c",
                    "print('x' * 70000)",
                ),
                timeout_seconds=10,
            )
        }
    )

    result = await runner.run(VerificationStepId.BACKEND_TESTS, tmp_path)

    assert result.exit_code == 0
    assert result.output_truncated is True
    assert len(result.stdout.encode("utf-8")) <= 64 * 1024


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group check requires POSIX")
async def test_subprocess_runner_cancellation_kills_process_group(
    tmp_path: Path,
) -> None:
    script = (
        "import os,pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "pathlib.Path('pids.txt').write_text(f'{os.getpid()} {child.pid}');"
        "time.sleep(30)"
    )
    runner = SubprocessVerificationRunner(
        {
            VerificationStepId.BACKEND_TESTS: FixedCommand(
                argv=(sys.executable, "-c", script),
                timeout_seconds=30,
            )
        }
    )
    task = asyncio.create_task(
        runner.run(VerificationStepId.BACKEND_TESTS, tmp_path)
    )
    pid_file = tmp_path / "pids.txt"
    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists()
    pids = [int(item) for item in pid_file.read_text().split()]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    def process_is_live(pid: int) -> bool:
        status = Path(f"/proc/{pid}/stat")
        if not status.exists():
            return False
        return status.read_text().split()[2] != "Z"

    for _ in range(100):
        if not any(process_is_live(pid) for pid in pids):
            break
        await asyncio.sleep(0.02)

    assert not any(process_is_live(pid) for pid in pids)
