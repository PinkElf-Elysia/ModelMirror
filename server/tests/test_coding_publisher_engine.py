from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from server.coding_publisher.engine import (
    CodingPublisherEngine,
    FixedGitRunner,
    GIT_TIMEOUT_SECONDS,
    HttpxGitHubTransport,
    PublisherConfig,
)
from server.coding_runtime.publish_models import (
    CodingPublishError,
    PublishCommit,
    PublishManifest,
    PublishState,
)


BASE = "a" * 40
HEAD = "b" * 40
TOKEN = "ghs_" + "x" * 60


def _private_key() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())


def _config(key: bytes) -> PublisherConfig:
    return PublisherConfig(
        app_id=731,
        installation_id=1731,
        repository_id=2731,
        repository="PinkElf-Elysia/ModelMirror",
        base_branch="main",
        private_key=key,
    )


def _manifest(*, base_sha: str = BASE, head_sha: str = HEAD) -> PublishManifest:
    commit = PublishCommit(
        commit_id="k" * 24,
        commit_sha=head_sha,
        parent_sha=base_sha,
        message="docs: 更新随机发布说明",
        files=("docs/random-publish-731.txt",),
    )
    return PublishManifest(
        publish_id="p" * 24,
        task_id="t" * 24,
        revision=4,
        snapshot_fingerprint="f" * 64,
        base_sha=base_sha,
        head_sha=head_sha,
        commits=(commit,),
        title="更新随机发布说明",
        body="包含一项经过验证的本地修改。",
    )


class FakeGitRunner:
    def __init__(self, transport: FakeTransport) -> None:
        self.transport = transport
        self.preflights = 0
        self.pushes = 0
        self.tokens: list[str] = []

    def preflight(self, manifest: PublishManifest) -> None:
        assert manifest.head_sha == HEAD
        self.preflights += 1

    def push(
        self,
        manifest: PublishManifest,
        *,
        repository: str,
        token: str,
    ) -> None:
        assert repository == "PinkElf-Elysia/ModelMirror"
        self.pushes += 1
        self.tokens.append(token)
        self.transport.refs[manifest.branch] = manifest.head_sha


class FakeTransport:
    def __init__(self) -> None:
        self.refs: dict[str, str] = {"main": BASE}
        self.pulls: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.fail_create_once = False

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> tuple[int, Any]:
        self.requests.append(
            {
                "method": method,
                "path": path,
                "headers": dict(headers),
                "json": dict(json_body or {}),
                "params": dict(params or {}),
            }
        )
        if path == "/app/installations/1731/access_tokens":
            return 201, {"token": TOKEN, "expires_at": "2030-01-01T00:00:00Z"}
        if path == "/repositories/2731":
            return 200, {"id": 2731, "full_name": "PinkElf-Elysia/ModelMirror"}
        if "/git/ref/heads/" in path:
            branch = unquote(path.split("/git/ref/heads/", 1)[1])
            sha = self.refs.get(branch)
            return (200, {"object": {"sha": sha}}) if sha else (404, {"message": "missing"})
        if path.endswith("/pulls") and method == "GET":
            branch = params["head"].split(":", 1)[1] if params else ""
            return 200, [item for item in self.pulls if item["head"]["ref"] == branch]
        if path.endswith("/pulls") and method == "POST":
            if self.fail_create_once:
                self.fail_create_once = False
                return 503, {"message": "retry"}
            item = self._pull(json_body or {})
            self.pulls.append(item)
            return 201, item
        if "/pulls/" in path and method == "GET":
            number = int(path.rsplit("/", 1)[1])
            item = next((pull for pull in self.pulls if pull["number"] == number), None)
            return (200, item) if item else (404, {"message": "missing"})
        if path == "/graphql":
            assert json_body is not None
            node_id = json_body["variables"]["id"]
            item = next(pull for pull in self.pulls if pull["node_id"] == node_id)
            item["draft"] = False
            return 200, {
                "data": {
                    "markPullRequestReadyForReview": {
                        "pullRequest": {
                            "id": node_id,
                            "number": item["number"],
                            "url": item["html_url"],
                            "isDraft": False,
                        }
                    }
                }
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    def _pull(self, body: Mapping[str, Any]) -> dict[str, Any]:
        number = len(self.pulls) + 81
        branch = str(body["head"])
        return {
            "number": number,
            "node_id": f"PR_kwDOExample{number}",
            "html_url": f"https://github.com/PinkElf-Elysia/ModelMirror/pull/{number}",
            "state": "open",
            "draft": bool(body.get("draft")),
            "title": body["title"],
            "body": body["body"],
            "head": {"ref": branch, "sha": self.refs[branch], "repo": {"id": 2731}},
            "base": {"ref": "main", "repo": {"id": 2731}},
        }


def _engine() -> tuple[CodingPublisherEngine, FakeGitRunner, FakeTransport]:
    transport = FakeTransport()
    runner = FakeGitRunner(transport)
    engine = CodingPublisherEngine(
        _config(_private_key()),
        runner,
        transport,
        clock=lambda: 1_800_000_000.0,
    )
    return engine, runner, transport


def test_publish_creates_one_branch_and_draft_pr_idempotently() -> None:
    engine, runner, transport = _engine()
    manifest = _manifest()

    first = engine.publish(manifest)
    second = engine.publish(manifest)

    assert first.state is PublishState.DRAFT
    assert second.pr_number == first.pr_number
    assert runner.pushes == 1
    assert len(transport.pulls) == 1
    assert runner.tokens == [TOKEN]
    token_request = transport.requests[0]
    assert token_request["json"] == {
        "repository_ids": [2731],
        "permissions": {
            "contents": "write",
            "pull_requests": "write",
            "metadata": "read",
        },
    }
    encoded_jwt = token_request["headers"]["Authorization"].removeprefix("Bearer ")
    _, payload, _ = encoded_jwt.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert claims == {"exp": 1_800_000_540, "iat": 1_799_999_940, "iss": "731"}


def test_base_drift_and_remote_collision_fail_before_push() -> None:
    engine, runner, transport = _engine()
    transport.refs["main"] = "d" * 40

    with pytest.raises(CodingPublishError) as drift:
        engine.publish(_manifest())
    assert drift.value.code == "base_branch_changed"
    assert runner.pushes == 0

    transport.refs["main"] = BASE
    transport.refs[_manifest().branch] = "e" * 40
    with pytest.raises(CodingPublishError) as collision:
        engine.publish(_manifest())
    assert collision.value.code == "remote_branch_conflict"
    assert runner.pushes == 0


def test_retry_after_pr_failure_reuses_pushed_branch() -> None:
    engine, runner, transport = _engine()
    transport.fail_create_once = True

    with pytest.raises(CodingPublishError) as failed:
        engine.publish(_manifest())
    assert failed.value.code == "github_pr_create_failed"
    assert runner.pushes == 1

    receipt = engine.publish(_manifest())
    assert receipt.state is PublishState.DRAFT
    assert runner.pushes == 1
    assert len(transport.pulls) == 1


def test_external_pr_edit_or_close_is_a_conflict() -> None:
    engine, _, transport = _engine()
    manifest = _manifest()
    engine.publish(manifest)

    transport.pulls[0]["title"] = "externally changed"
    with pytest.raises(CodingPublishError) as edited:
        engine.reconcile(manifest)
    assert edited.value.code == "remote_pr_conflict"

    transport.pulls[0]["title"] = manifest.title
    transport.pulls[0]["state"] = "closed"
    with pytest.raises(CodingPublishError) as closed:
        engine.publish(manifest)
    assert closed.value.code == "remote_pr_conflict"


def test_reconcile_and_mark_ready_are_idempotent() -> None:
    engine, _, _ = _engine()
    manifest = _manifest()
    receipt = engine.publish(manifest)

    state, recovered = engine.reconcile(manifest)
    ready = engine.mark_ready(manifest, receipt)
    repeated = engine.mark_ready(manifest, ready)

    assert state == "draft"
    assert recovered is not None and recovered.pr_number == receipt.pr_number
    assert ready.state is PublishState.READY
    assert repeated.state is PublishState.READY


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Publisher Test",
        "GIT_AUTHOR_EMAIL": "publisher@example.invalid",
        "GIT_COMMITTER_NAME": "Publisher Test",
        "GIT_COMMITTER_EMAIL": "publisher@example.invalid",
    }
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _local_repository(tmp_path: Path) -> tuple[Path, Path, PublishManifest]:
    target = tmp_path / "target"
    temporary = tmp_path / "temporary"
    target.mkdir()
    temporary.mkdir()
    _git(target, "init", "-b", "main")
    (target / "README.md").write_text("base\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "base")
    base = _git(target, "rev-parse", "HEAD")
    _git(target, "switch", "-c", "coding/local-draft")
    (target / "README.md").write_text("base\npublish\n", encoding="utf-8")
    _git(target, "add", "README.md")
    _git(target, "commit", "-m", "docs: update publish fixture")
    head = _git(target, "rev-parse", "HEAD")
    manifest = PublishManifest(
        publish_id="p" * 24,
        task_id="t" * 24,
        revision=1,
        snapshot_fingerprint="f" * 64,
        base_sha=base,
        head_sha=head,
        commits=(
            PublishCommit(
                commit_id="k" * 24,
                commit_sha=head,
                parent_sha=base,
                message="docs: update publish fixture",
                files=("README.md",),
            ),
        ),
        title="Publish fixture",
        body="",
    )
    return target, temporary, manifest


def test_fixed_git_runner_rejects_dangerous_config_and_uses_fixed_push(
    tmp_path: Path,
) -> None:
    target, temporary, manifest = _local_repository(tmp_path)
    calls: list[tuple[list[str], dict[str, str]]] = []
    read_calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["git", "push"]:
            calls.append((args, kwargs["env"]))
            return subprocess.CompletedProcess(args, 0, "ok", "")
        read_calls.append((args, kwargs))
        return subprocess.run(args, **kwargs)

    runner = FixedGitRunner(
        target,
        temporary,
        "http://coding-github-egress:8080",
        run=run,
    )
    runner.preflight(manifest)
    runner.push(manifest, repository="PinkElf-Elysia/ModelMirror", token=TOKEN)

    assert len(calls) == 1
    args, env = calls[0]
    assert args == [
        "git",
        "push",
        "--no-verify",
        "--porcelain",
        "https://github.com/PinkElf-Elysia/ModelMirror.git",
        f"{manifest.head_sha}:refs/heads/{manifest.branch}",
    ]
    assert TOKEN not in " ".join(args)
    assert env["GIT_CONFIG_VALUE_2"] == "http://coding-github-egress:8080"
    assert env["GIT_CONFIG_KEY_3"] == "credential.helper"
    assert env["GIT_CONFIG_VALUE_3"] == ""
    assert env["GIT_CONFIG_COUNT"] == "5"
    assert env["GIT_CONFIG_KEY_4"] == "safe.directory"
    assert env["GIT_CONFIG_VALUE_4"] == str(target.resolve())

    assert read_calls
    assert all(
        f"safe.directory={target.resolve()}" in read_args
        and kwargs["timeout"] == GIT_TIMEOUT_SECONDS
        for read_args, kwargs in read_calls
    )

    _git(target, "config", "url.https://evil.invalid/.insteadOf", "https://github.com/")
    with pytest.raises(CodingPublishError) as unsafe:
        runner.preflight(manifest)
    assert unsafe.value.code == "unsafe_repository"


def test_git_preflight_rejects_files_hidden_from_manifest(tmp_path: Path) -> None:
    target, temporary, first = _local_repository(tmp_path)
    workflow = target / ".github" / "workflows" / "hidden.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: hidden\n", encoding="utf-8")
    _git(target, "add", ".github/workflows/hidden.yml")
    _git(target, "commit", "-m", "docs: hide workflow")
    head = _git(target, "rev-parse", "HEAD")
    manifest = PublishManifest(
        publish_id=first.publish_id,
        task_id=first.task_id,
        revision=first.revision,
        snapshot_fingerprint=first.snapshot_fingerprint,
        base_sha=first.base_sha,
        head_sha=head,
        commits=(
            first.commits[0],
            PublishCommit(
                commit_id="m" * 24,
                commit_sha=head,
                parent_sha=first.head_sha,
                message="docs: hide workflow",
                files=("README.md",),
            ),
        ),
        title=first.title,
        body=first.body,
    )
    runner = FixedGitRunner(
        target,
        temporary,
        "http://coding-github-egress:8080",
    )

    with pytest.raises(CodingPublishError) as hidden:
        runner.preflight(manifest)
    assert hidden.value.code == "commit_mismatch"


def test_http_transport_rejects_non_allowlisted_proxy() -> None:
    with pytest.raises(ValueError):
        HttpxGitHubTransport("http://example.invalid:8080")
