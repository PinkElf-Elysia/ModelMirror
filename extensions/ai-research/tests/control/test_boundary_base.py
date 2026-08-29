from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = MODULE_ROOT / "scripts" / "validate_boundary.py"
SPEC = importlib.util.spec_from_file_location("validate_boundary_base", SCRIPT)
assert SPEC and SPEC.loader
boundary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boundary)


def test_main_uses_requested_base_for_pr_scope_and_locked_base_for_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module_root = tmp_path / "ai-research"
    module_root.mkdir()
    locked_base = "1" * 40
    boundary_config = {"allowedParentFiles": ["server/main.py"]}
    source_lock = {"modelMirrorBaseCommit": locked_base}
    (module_root / "module-boundary.json").write_text(
        json.dumps(boundary_config), encoding="utf-8"
    )
    (module_root / "source-lock.json").write_text(json.dumps(source_lock), encoding="utf-8")

    calls: dict[str, object] = {}
    monkeypatch.setattr(boundary, "MODULE_ROOT", module_root)
    monkeypatch.setattr(
        boundary,
        "validate_requested_base",
        lambda requested, locked: calls.update(requested=(requested, locked)),
    )
    monkeypatch.setattr(
        boundary,
        "validate_paths",
        lambda base, config: calls.update(paths=(base, config)),
    )
    monkeypatch.setattr(
        boundary,
        "validate_locked_files",
        lambda lock: calls.update(locked_files=lock),
    )
    for name in (
        "validate_runtime_references",
        "validate_metric_names",
        "validate_parent_controls",
        "validate_ldr_distribution_mode",
        "validate_runtime_privacy_defaults",
        "validate_no_secrets",
    ):
        monkeypatch.setattr(boundary, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_boundary.py", "--base", "origin/main", "--distribution-mode", "external-pull"],
    )

    assert boundary.main() == 0
    assert calls["requested"] == ("origin/main", locked_base)
    assert calls["paths"] == ("origin/main", boundary_config)
    assert calls["locked_files"] == source_lock


def test_scope_allows_only_current_pr_files(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"allowedParentFiles": ["server/main.py"]}
    monkeypatch.setattr(
        boundary,
        "changed_paths",
        lambda base: {"extensions/ai-research/README.md", "server/main.py"},
    )
    monkeypatch.setattr(boundary, "MODULE_ROOT", Path(__file__).parent / "missing-module-root")

    boundary.validate_paths("origin/main", config)


def test_scope_still_rejects_unapproved_current_pr_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boundary, "changed_paths", lambda base: {"server/unapproved.py"})
    monkeypatch.setattr(boundary, "MODULE_ROOT", Path(__file__).parent / "missing-module-root")

    with pytest.raises(boundary.BoundaryFailure, match="outside the approved boundary"):
        boundary.validate_paths("origin/main", {"allowedParentFiles": []})


@pytest.mark.parametrize("returncode", [1, 128])
def test_requested_base_must_descend_from_locked_provenance(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
) -> None:
    locked_base = "1" * 40
    requested_sha = "2" * 40
    monkeypatch.setattr(boundary, "git", lambda *args: [requested_sha])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode),
    )

    with pytest.raises(boundary.BoundaryFailure, match="diverged from locked base"):
        boundary.validate_requested_base("origin/main", locked_base)


def test_advanced_requested_base_keeps_locked_provenance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    locked_base = "1" * 40
    requested_sha = "2" * 40
    monkeypatch.setattr(boundary, "git", lambda *args: [requested_sha])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    boundary.validate_requested_base("origin/main", locked_base)

    stderr = capsys.readouterr().err
    assert f"advanced to {requested_sha}" in stderr
    assert f"provenance remains pinned to {locked_base}" in stderr
