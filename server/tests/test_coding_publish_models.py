from __future__ import annotations

import pytest

from server.coding_runtime.publish_models import (
    PublishCommit,
    PublishManifest,
    PublishReceipt,
    PublishState,
    build_publish_branch,
    normalize_pr_body,
    normalize_pr_title,
)


BASE = "a" * 40
HEAD = "c" * 40
PUBLISH_ID = "p" * 24
TASK_ID = "t" * 24


def _commit(*, files: tuple[str, ...] = ("docs/publish.txt",)) -> PublishCommit:
    return PublishCommit(
        commit_id="k" * 24,
        commit_sha=HEAD,
        parent_sha=BASE,
        message="docs: 更新发布说明",
        files=files,
    )


def _manifest(**overrides: object) -> PublishManifest:
    values: dict[str, object] = {
        "publish_id": PUBLISH_ID,
        "task_id": TASK_ID,
        "revision": 4,
        "snapshot_fingerprint": "f" * 64,
        "base_sha": BASE,
        "head_sha": HEAD,
        "commits": (_commit(),),
        "title": "更新随机发布说明",
        "body": "包含 1 个本地提交。",
    }
    values.update(overrides)
    return PublishManifest(**values)  # type: ignore[arg-type]


def test_manifest_round_trip_and_stable_branch() -> None:
    manifest = _manifest()

    assert PublishManifest.from_dict(manifest.to_dict()) == manifest
    assert manifest.branch == f"codex/modelmirror-{TASK_ID[:12]}-{HEAD[:12]}"
    assert manifest.files == ("docs/publish.txt",)
    assert build_publish_branch(TASK_ID, HEAD) == manifest.branch


def test_manifest_requires_linear_chain_and_exact_head() -> None:
    second = PublishCommit(
        commit_id="m" * 24,
        commit_sha="d" * 40,
        parent_sha="b" * 40,
        message="feature: 更新项目功能",
        files=("server/example.py",),
    )

    with pytest.raises(ValueError, match="chain"):
        _manifest(commits=(_commit(), second), head_sha=second.commit_sha)

    with pytest.raises(ValueError, match="inconsistent"):
        _manifest(head_sha="e" * 40)


def test_manifest_rejects_workflow_and_noncanonical_paths() -> None:
    with pytest.raises(ValueError, match="Workflow"):
        _manifest(commits=(_commit(files=(".github/workflows/release.yml",)),))

    with pytest.raises(ValueError, match="paths"):
        _commit(files=("docs/z.txt", "docs/a.txt"))


@pytest.mark.parametrize(
    "value",
    [
        "",
        "line one\nline two",
        "x" * 121,
        "contains github_pat_" + "a" * 45,
        "contains\x00nul",
    ],
)
def test_title_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_pr_title(value)


def test_publish_text_normalizes_newlines_and_rejects_secrets() -> None:
    assert normalize_pr_title("  更新说明  ") == "更新说明"
    assert normalize_pr_body("第一行\r\n第二行\r\n") == "第一行\n第二行"

    with pytest.raises(ValueError, match="secret"):
        normalize_pr_body("-----BEGIN PRIVATE KEY-----")


def test_receipt_round_trip_and_ready_timestamp() -> None:
    draft = PublishReceipt(
        publish_id=PUBLISH_ID,
        revision=4,
        repository_id=731,
        repository="PinkElf-Elysia/ModelMirror",
        base_branch="main",
        branch=_manifest().branch,
        head_sha=HEAD,
        pr_number=81,
        pr_node_id="PR_kwDOExample731",
        pr_url="https://github.com/PinkElf-Elysia/ModelMirror/pull/81",
        published_at=100.0,
    )

    assert PublishReceipt.from_dict(draft.to_dict()) == draft

    ready = PublishReceipt(
        **{
            **draft.to_dict(),
            "state": PublishState.READY,
            "ready_at": 101.0,
        }
    )
    assert ready.state is PublishState.READY


def test_receipt_rejects_non_github_url_and_invalid_ready_state() -> None:
    values = {
        "publish_id": PUBLISH_ID,
        "revision": 4,
        "repository_id": 731,
        "repository": "PinkElf-Elysia/ModelMirror",
        "base_branch": "main",
        "branch": _manifest().branch,
        "head_sha": HEAD,
        "pr_number": 81,
        "pr_node_id": "PR_kwDOExample731",
        "published_at": 100.0,
    }
    with pytest.raises(ValueError, match="URL"):
        PublishReceipt(pr_url="https://example.com/pull/81", **values)

    with pytest.raises(ValueError, match="timestamp"):
        PublishReceipt(
            pr_url="https://github.com/PinkElf-Elysia/ModelMirror/pull/81",
            state=PublishState.READY,
            **values,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository_id", "731"),
        ("pr_number", "81"),
        ("pr_node_id", 81),
        ("pr_url", 81),
        ("state", "draft"),
    ],
)
def test_receipt_rejects_wrong_metadata_types(field: str, value: object) -> None:
    values: dict[str, object] = {
        "publish_id": PUBLISH_ID,
        "revision": 4,
        "repository_id": 731,
        "repository": "PinkElf-Elysia/ModelMirror",
        "base_branch": "main",
        "branch": _manifest().branch,
        "head_sha": HEAD,
        "pr_number": 81,
        "pr_node_id": "PR_kwDOExample731",
        "pr_url": "https://github.com/PinkElf-Elysia/ModelMirror/pull/81",
        "published_at": 100.0,
    }
    values[field] = value

    with pytest.raises(ValueError):
        PublishReceipt(**values)  # type: ignore[arg-type]
