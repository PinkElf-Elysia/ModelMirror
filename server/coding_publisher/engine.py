from __future__ import annotations

import base64
import json
import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from server.coding_runtime.commit_models import COMMIT_BRANCH
from server.coding_runtime.publish_models import (
    BRANCH_PATTERN,
    REPOSITORY_PATTERN,
    CodingPublishError,
    PublishManifest,
    PublishReceipt,
    PublishState,
)


API_VERSION = "2026-03-10"
GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_PATH = "/graphql"
GIT_TIMEOUT_SECONDS = 120
MAX_GIT_OUTPUT_CHARS = 8_000
MAX_GITHUB_RESPONSE_BYTES = 1_000_000
PUBLISH_PROXY_URL = "http://coding-github-egress:8080"
_SAFE_LOCAL_CONFIG = frozenset(
    {
        "core.autocrlf",
        "core.bare",
        "core.filemode",
        "core.ignorecase",
        "core.logallrefupdates",
        "core.precomposeunicode",
        "core.repositoryformatversion",
        "core.symlinks",
    }
)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_]{20,512}$")


def _configured_branch_is_safe(value: object) -> bool:
    if not isinstance(value, str) or BRANCH_PATTERN.fullmatch(value) is None:
        return False
    if (
        value == "HEAD"
        or value.startswith("refs/")
        or ".." in value
        or "//" in value
        or "@{" in value
    ):
        return False
    return all(
        part and not part.startswith(".") and not part.endswith(".lock")
        for part in value.split("/")
    )


class GitRunner(Protocol):
    def preflight(self, manifest: PublishManifest) -> None: ...

    def push(
        self,
        manifest: PublishManifest,
        *,
        repository: str,
        token: str,
    ) -> None: ...


class GitHubTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> tuple[int, Any]: ...


@dataclass(frozen=True, slots=True)
class PublisherConfig:
    app_id: int
    installation_id: int
    repository_id: int
    repository: str
    base_branch: str
    private_key: bytes

    def __post_init__(self) -> None:
        for value in (self.app_id, self.installation_id, self.repository_id):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("GitHub App identifier is invalid")
        if not isinstance(self.repository, str) or REPOSITORY_PATTERN.fullmatch(self.repository) is None:
            raise ValueError("GitHub repository is invalid")
        if not _configured_branch_is_safe(self.base_branch):
            raise ValueError("GitHub base branch is invalid")
        if not isinstance(self.private_key, bytes) or len(self.private_key) > 64 * 1024:
            raise ValueError("GitHub App private key is invalid")
        try:
            key = serialization.load_pem_private_key(self.private_key, password=None)
        except (TypeError, ValueError) as exc:
            raise ValueError("GitHub App private key is invalid") from exc
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
            raise ValueError("GitHub App private key is invalid")


@dataclass(frozen=True, slots=True)
class _InstallationToken:
    value: str
    expires_at: float


class HttpxGitHubTransport:
    """HTTPS-only GitHub API transport forced through one configured proxy."""

    def __init__(self, proxy_url: str) -> None:
        if proxy_url != PUBLISH_PROXY_URL:
            raise ValueError("GitHub proxy URL is invalid")
        self._client = httpx.Client(
            base_url=GITHUB_API_URL,
            proxy=proxy_url,
            timeout=httpx.Timeout(30.0, connect=10.0, read=30.0, write=30.0),
            follow_redirects=False,
            trust_env=False,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> tuple[int, Any]:
        if method not in {"GET", "POST"} or not path.startswith("/") or "://" in path:
            raise CodingPublishError("GitHub request is invalid.", code="invalid_request")
        try:
            with self._client.stream(
                method,
                path,
                headers=dict(headers),
                json=dict(json_body) if json_body is not None else None,
                params=dict(params) if params is not None else None,
            ) as response:
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_GITHUB_RESPONSE_BYTES:
                        raise CodingPublishError(
                            "GitHub response is too large.",
                            code="github_response_invalid",
                        )
                payload = json.loads(content) if content else None
                status_code = response.status_code
        except CodingPublishError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise CodingPublishError(
                "GitHub request failed.",
                code="github_unavailable",
            ) from exc
        return status_code, payload


class FixedGitRunner:
    """Reads one local repository and pushes one exact object without mutation."""

    def __init__(
        self,
        target_root: Path,
        temporary_root: Path,
        proxy_url: str,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if target_root.is_symlink() or temporary_root.is_symlink():
            raise CodingPublishError("Publisher root is unsafe.", code="unsafe_repository")
        self.target_root = target_root.resolve()
        self.temporary_root = temporary_root.resolve()
        if proxy_url != PUBLISH_PROXY_URL:
            raise CodingPublishError("GitHub proxy is invalid.", code="invalid_configuration")
        self.proxy_url = proxy_url
        self._run = run
        if not self.target_root.is_dir() or not self.temporary_root.is_dir():
            raise CodingPublishError("Publisher root is unavailable.", code="repository_not_ready")
        git_path = self.target_root / ".git"
        if not git_path.is_dir() or git_path.is_symlink():
            raise CodingPublishError("Repository is not independent.", code="repository_not_independent")

    def preflight(self, manifest: PublishManifest) -> None:
        if self._git_text("remote"):
            raise CodingPublishError("Repository has a remote.", code="repository_has_remote")
        if self._git_text("symbolic-ref", "--quiet", "HEAD") != f"refs/heads/{COMMIT_BRANCH}":
            raise CodingPublishError("Repository branch is invalid.", code="wrong_branch")
        if self._git_text("status", "--porcelain=v1", "--untracked-files=all"):
            raise CodingPublishError("Repository is not clean.", code="repository_not_ready")
        if self._git_text("rev-parse", "--verify", "HEAD") != manifest.head_sha:
            raise CodingPublishError("Repository head changed.", code="commit_mismatch")
        chain = tuple(
            line
            for line in self._git_text(
                "rev-list",
                "--reverse",
                f"{manifest.base_sha}..{manifest.head_sha}",
            ).splitlines()
            if line
        )
        if chain != tuple(commit.commit_sha for commit in manifest.commits):
            raise CodingPublishError("Commit chain changed.", code="commit_mismatch")
        for commit in manifest.commits:
            relationship = tuple(
                self._git_text(
                    "rev-list",
                    "--parents",
                    "-n",
                    "1",
                    commit.commit_sha,
                ).split()
            )
            if relationship != (commit.commit_sha, commit.parent_sha):
                raise CodingPublishError("Commit parents changed.", code="commit_mismatch")
            if self._git_text("log", "-1", "--format=%B", commit.commit_sha) != commit.message:
                raise CodingPublishError("Commit message changed.", code="commit_mismatch")
            changed = tuple(
                field
                for field in self._git_text(
                    "diff",
                    "--name-status",
                    "--no-renames",
                    "-z",
                    commit.parent_sha,
                    commit.commit_sha,
                    "--",
                ).split("\0")
                if field
            )
            if len(changed) % 2 or any(
                changed[index] not in {"A", "M"}
                for index in range(0, len(changed), 2)
            ):
                raise CodingPublishError("Commit files are unsafe.", code="commit_mismatch")
            actual_files = tuple(sorted(changed[index] for index in range(1, len(changed), 2)))
            if actual_files != commit.files:
                raise CodingPublishError("Commit files changed.", code="commit_mismatch")
        if self._git_text("for-each-ref", "--format=%(refname)", "refs/replace"):
            raise CodingPublishError("Repository replace refs are unsafe.", code="unsafe_repository")
        alternates = self.target_root / ".git" / "objects" / "info" / "alternates"
        if alternates.exists() and alternates.read_text(encoding="utf-8").strip():
            raise CodingPublishError("Repository alternates are unsafe.", code="unsafe_repository")
        config_names = {
            name
            for name in self._git_text("config", "--local", "--name-only", "--null", "--list")
            .split("\0")
            if name
        }
        if not config_names.issubset(_SAFE_LOCAL_CONFIG):
            raise CodingPublishError("Repository config is unsafe.", code="unsafe_repository")

    def push(
        self,
        manifest: PublishManifest,
        *,
        repository: str,
        token: str,
    ) -> None:
        if REPOSITORY_PATTERN.fullmatch(repository) is None or _TOKEN_PATTERN.fullmatch(token) is None:
            raise CodingPublishError("Publish credential is invalid.", code="invalid_request")
        self.preflight(manifest)
        credential = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(self.temporary_root),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "5",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {credential}",
            "GIT_CONFIG_KEY_1": "core.hooksPath",
            "GIT_CONFIG_VALUE_1": os.devnull,
            "GIT_CONFIG_KEY_2": "http.proxy",
            "GIT_CONFIG_VALUE_2": self.proxy_url,
            "GIT_CONFIG_KEY_3": "credential.helper",
            "GIT_CONFIG_VALUE_3": "",
            "GIT_CONFIG_KEY_4": "safe.directory",
            "GIT_CONFIG_VALUE_4": str(self.target_root),
        }
        self._execute(
            (
                "git",
                "push",
                "--no-verify",
                "--porcelain",
                f"https://github.com/{repository}.git",
                f"{manifest.head_sha}:refs/heads/{manifest.branch}",
            ),
            env=env,
            timeout=GIT_TIMEOUT_SECONDS,
            code="github_push_failed",
        )

    def _git_text(self, *args: str) -> str:
        result = self._execute(
            (
                "git",
                "-c",
                f"safe.directory={self.target_root}",
                "-c",
                f"core.hooksPath={os.devnull}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *args,
            ),
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": str(self.temporary_root),
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
            # Git status over a Windows bind mount can legitimately exceed 30s.
            # Keep the operation bounded by the same ceiling as the fixed push.
            timeout=GIT_TIMEOUT_SECONDS,
            code="repository_not_ready",
        )
        return result.stdout.strip()

    def _execute(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str],
        timeout: int,
        code: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._run(
                list(args),
                cwd=self.target_root,
                env=dict(env),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CodingPublishError("Git operation failed.", code=code) from exc
        if result.returncode != 0:
            raise CodingPublishError("Git operation failed.", code=code)
        if len(result.stdout) + len(result.stderr) > MAX_GIT_OUTPUT_CHARS:
            raise CodingPublishError("Git output is too large.", code="git_output_too_large")
        return result


class CodingPublisherEngine:
    """Publishes a verified local commit chain to one fixed GitHub repository."""

    def __init__(
        self,
        config: PublisherConfig,
        git_runner: GitRunner,
        transport: GitHubTransport,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.git_runner = git_runner
        self.transport = transport
        self._clock = clock
        self._lock = threading.Lock()
        self._token: _InstallationToken | None = None

    def health(self) -> dict[str, object]:
        return {
            "configured": True,
            "available": True,
            "provider": "github",
            "target": "fixed_repository",
            "repository": self.config.repository,
            "base_branch": self.config.base_branch,
        }

    def publish(self, manifest: PublishManifest) -> PublishReceipt:
        with self._lock:
            self.git_runner.preflight(manifest)
            token = self._installation_token()
            self._assert_repository(token)
            self._assert_exact_base(token, manifest.base_sha)
            remote_head = self._get_ref(token, manifest.branch)
            if remote_head is not None and remote_head != manifest.head_sha:
                raise CodingPublishError("Remote branch is occupied.", code="remote_branch_conflict")
            if remote_head is None:
                self.git_runner.push(
                    manifest,
                    repository=self.config.repository,
                    token=token,
                )
                remote_head = self._get_ref(token, manifest.branch)
                if remote_head != manifest.head_sha:
                    raise CodingPublishError(
                        "Remote push result is unknown.",
                        code="publish_result_unknown",
                    )
            self._assert_exact_base(token, manifest.base_sha)
            receipt = self._find_pull_request(token, manifest)
            if receipt is not None:
                return receipt
            self._assert_exact_base(token, manifest.base_sha)
            return self._create_pull_request(token, manifest)

    def reconcile(self, manifest: PublishManifest) -> tuple[str, PublishReceipt | None]:
        with self._lock:
            token = self._installation_token()
            self._assert_repository(token)
            remote_head = self._get_ref(token, manifest.branch)
            if remote_head is None:
                return PublishState.NOT_PUBLISHED.value, None
            if remote_head != manifest.head_sha:
                return PublishState.CONFLICT.value, None
            receipt = self._find_pull_request(token, manifest)
            if receipt is None:
                return "branch_pushed", None
            return receipt.state.value, receipt

    def mark_ready(self, manifest: PublishManifest, receipt: PublishReceipt) -> PublishReceipt:
        with self._lock:
            self._assert_receipt(manifest, receipt)
            token = self._installation_token()
            self._assert_repository(token)
            remote_head = self._get_ref(token, manifest.branch)
            if remote_head != manifest.head_sha:
                raise CodingPublishError("Remote branch changed.", code="remote_branch_conflict")
            current = self._get_pull_request(token, manifest, receipt.pr_number)
            if current.state is PublishState.READY:
                return current
            status, payload = self.transport.request(
                "POST",
                GITHUB_GRAPHQL_PATH,
                headers=self._headers(token),
                json_body={
                    "query": (
                        "mutation($id:ID!,$clientMutationId:String!){"
                        "markPullRequestReadyForReview(input:{pullRequestId:$id,"
                        "clientMutationId:$clientMutationId}){pullRequest{id number url isDraft}}}"
                    ),
                    "variables": {
                        "id": receipt.pr_node_id,
                        "clientMutationId": manifest.publish_id,
                    },
                },
            )
            if status != 200 or not isinstance(payload, dict) or payload.get("errors"):
                raise CodingPublishError("Pull request could not be marked ready.", code="github_ready_failed")
            result = _nested(payload, "data", "markPullRequestReadyForReview", "pullRequest")
            if not isinstance(result, dict) or result.get("isDraft") is not False:
                raise CodingPublishError("Ready result is invalid.", code="publish_result_unknown")
            confirmed = self._get_pull_request(token, manifest, receipt.pr_number)
            if confirmed.state is not PublishState.READY:
                raise CodingPublishError("Ready result is invalid.", code="publish_result_unknown")
            return replace(confirmed, ready_at=max(confirmed.published_at, self._now()))

    def _installation_token(self) -> str:
        now = self._now()
        if self._token is not None and self._token.expires_at - now >= 120:
            return self._token.value
        app_jwt = _create_app_jwt(self.config, now=now)
        status, payload = self.transport.request(
            "POST",
            f"/app/installations/{self.config.installation_id}/access_tokens",
            headers=self._headers(app_jwt),
            json_body={
                "repository_ids": [self.config.repository_id],
                "permissions": {
                    "contents": "write",
                    "pull_requests": "write",
                    "metadata": "read",
                },
            },
        )
        if status != 201 or not isinstance(payload, dict):
            raise CodingPublishError("GitHub App authentication failed.", code="github_auth_failed")
        token = payload.get("token")
        expires_at = _parse_github_time(payload.get("expires_at"))
        if not isinstance(token, str) or _TOKEN_PATTERN.fullmatch(token) is None or expires_at <= now:
            raise CodingPublishError("GitHub App token is invalid.", code="github_auth_failed")
        self._token = _InstallationToken(token, expires_at)
        return token

    def _assert_repository(self, token: str) -> None:
        status, payload = self.transport.request(
            "GET",
            f"/repositories/{self.config.repository_id}",
            headers=self._headers(token),
        )
        if (
            status != 200
            or not isinstance(payload, dict)
            or payload.get("id") != self.config.repository_id
            or payload.get("full_name") != self.config.repository
        ):
            raise CodingPublishError("GitHub repository does not match configuration.", code="repository_mismatch")

    def _assert_exact_base(self, token: str, expected_sha: str) -> None:
        if self._get_ref(token, self.config.base_branch) != expected_sha:
            raise CodingPublishError("GitHub base branch changed.", code="base_branch_changed")

    def _get_ref(self, token: str, branch: str) -> str | None:
        if BRANCH_PATTERN.fullmatch(branch) is None:
            raise CodingPublishError("GitHub branch is invalid.", code="invalid_request")
        status, payload = self.transport.request(
            "GET",
            f"/repos/{self.config.repository}/git/ref/heads/{quote(branch, safe='')}",
            headers=self._headers(token),
        )
        if status == 404:
            return None
        sha = _nested(payload, "object", "sha")
        if status != 200 or not isinstance(sha, str):
            raise CodingPublishError("GitHub ref response is invalid.", code="github_unavailable")
        return sha

    def _find_pull_request(
        self,
        token: str,
        manifest: PublishManifest,
    ) -> PublishReceipt | None:
        owner = self.config.repository.split("/", 1)[0]
        status, payload = self.transport.request(
            "GET",
            f"/repos/{self.config.repository}/pulls",
            headers=self._headers(token),
            params={
                "state": "all",
                "head": f"{owner}:{manifest.branch}",
                "base": self.config.base_branch,
            },
        )
        if status != 200 or not isinstance(payload, list):
            raise CodingPublishError("GitHub pull request query failed.", code="github_unavailable")
        matches = [item for item in payload if isinstance(item, dict)]
        if len(matches) > 1:
            raise CodingPublishError("Multiple pull requests match.", code="remote_pr_conflict")
        if not matches:
            return None
        return self._receipt_from_pull(manifest, matches[0])

    def _create_pull_request(self, token: str, manifest: PublishManifest) -> PublishReceipt:
        status, payload = self.transport.request(
            "POST",
            f"/repos/{self.config.repository}/pulls",
            headers=self._headers(token),
            json_body={
                "title": manifest.title,
                "body": manifest.body,
                "head": manifest.branch,
                "base": self.config.base_branch,
                "draft": True,
            },
        )
        if status != 201 or not isinstance(payload, dict):
            raise CodingPublishError("Draft pull request creation failed.", code="github_pr_create_failed")
        return self._receipt_from_pull(manifest, payload)

    def _get_pull_request(
        self,
        token: str,
        manifest: PublishManifest,
        number: int,
    ) -> PublishReceipt:
        status, payload = self.transport.request(
            "GET",
            f"/repos/{self.config.repository}/pulls/{number}",
            headers=self._headers(token),
        )
        if (
            status != 200
            or not isinstance(payload, dict)
            or payload.get("number") != number
        ):
            raise CodingPublishError("Pull request changed externally.", code="remote_pr_conflict")
        return self._receipt_from_pull(manifest, payload)

    def _receipt_from_pull(
        self,
        manifest: PublishManifest,
        payload: Mapping[str, Any],
    ) -> PublishReceipt:
        if (
            payload.get("state") != "open"
            or not isinstance(payload.get("draft"), bool)
            or payload.get("title") != manifest.title
            or payload.get("body") != manifest.body
            or _nested(payload, "head", "sha") != manifest.head_sha
            or _nested(payload, "head", "ref") != manifest.branch
            or _nested(payload, "head", "repo", "id") != self.config.repository_id
            or _nested(payload, "base", "ref") != self.config.base_branch
            or _nested(payload, "base", "repo", "id") != self.config.repository_id
        ):
            raise CodingPublishError("Pull request target is inconsistent.", code="remote_pr_conflict")
        state = PublishState.DRAFT if payload.get("draft") is True else PublishState.READY
        now = self._now()
        try:
            number = payload["number"]
            expected_url = f"https://github.com/{self.config.repository}/pull/{number}"
            if payload["html_url"] != expected_url:
                raise ValueError("Pull request URL is invalid")
            return PublishReceipt(
                publish_id=manifest.publish_id,
                revision=manifest.revision,
                repository_id=self.config.repository_id,
                repository=self.config.repository,
                base_branch=self.config.base_branch,
                branch=manifest.branch,
                head_sha=manifest.head_sha,
                pr_number=number,
                pr_node_id=payload["node_id"],
                pr_url=payload["html_url"],
                state=state,
                published_at=now,
                ready_at=now if state is PublishState.READY else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CodingPublishError("Pull request response is invalid.", code="github_response_invalid") from exc

    @staticmethod
    def _assert_receipt(manifest: PublishManifest, receipt: PublishReceipt) -> None:
        if (
            receipt.publish_id != manifest.publish_id
            or receipt.revision != manifest.revision
            or receipt.branch != manifest.branch
            or receipt.head_sha != manifest.head_sha
        ):
            raise CodingPublishError("Publish receipt does not match.", code="publish_mismatch")

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
        }

    def _now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value) or value < 0:
            raise CodingPublishError("Publisher clock is invalid.", code="publisher_unavailable")
        return value


def _create_app_jwt(config: PublisherConfig, *, now: float) -> str:
    header = _base64url_json({"alg": "RS256", "typ": "JWT"})
    payload = _base64url_json(
        {
            "iat": int(now) - 60,
            "exp": int(now) + 540,
            "iss": str(config.app_id),
        }
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    key = serialization.load_pem_private_key(config.private_key, password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_base64url(signature)}"


def _base64url_json(value: Mapping[str, Any]) -> str:
    return _base64url(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _parse_github_time(value: Any) -> float:
    if not isinstance(value, str) or len(value) > 64:
        return -1
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return -1


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current
