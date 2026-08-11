from __future__ import annotations

from pathlib import Path

import pytest

from server.agent_upstream.tools import (
    ToolExecutionError,
    compute_shadow_candidate_sha256,
)


def test_candidate_hash_covers_only_the_single_file_entrypoint(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>first</h1>", encoding="utf-8")
    (tmp_path / "PLAN.md").write_text("draft one", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "notes.txt").write_text("note one", encoding="utf-8")

    original = compute_shadow_candidate_sha256(tmp_path)
    (tmp_path / "PLAN.md").write_text("draft two", encoding="utf-8")
    (scratch / "notes.txt").write_text("note two", encoding="utf-8")
    assert compute_shadow_candidate_sha256(tmp_path) == original

    (tmp_path / "index.html").write_text("<h1>second</h1>", encoding="utf-8")
    assert compute_shadow_candidate_sha256(tmp_path) != original


def test_candidate_hash_requires_index_html(tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError, match="index.html"):
        compute_shadow_candidate_sha256(tmp_path)


def test_candidate_hash_rejects_symlink_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "target.html"
    target.write_text("<h1>outside contract</h1>", encoding="utf-8")
    entrypoint = tmp_path / "index.html"
    try:
        entrypoint.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(ToolExecutionError, match="safe index.html"):
        compute_shadow_candidate_sha256(tmp_path)
