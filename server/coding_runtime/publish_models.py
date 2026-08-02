from __future__ import annotations

import math
import re
import time
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable
from urllib.parse import urlparse

from .commit_models import COMMIT_ID_PATTERN, GIT_OBJECT_ID_PATTERN, normalize_commit_message
from .draft_workspace import DraftPolicyError, DraftWorkspace
from .patch_policy import SNAPSHOT_FINGERPRINT_PATTERN


MAX_PUBLISH_COMMITS = 10
MAX_PUBLISH_FILES = 20
MAX_PR_TITLE_CHARS = 120
MAX_PR_BODY_CHARS = 10_000
PUBLISH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._/-]{0,198}[A-Za-z0-9])?$")
NODE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_=-]{8,256}$")
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
)


class PublishState(StrEnum):
    NOT_PUBLISHED = "not_published"
    PUBLISHING = "publishing"
    DRAFT = "draft"
    MARKING_READY = "marking_ready"
    READY = "ready"
    FAILED = "failed"
    CONFLICT = "conflict"


class CodingPublishError(RuntimeError):
    def __init__(self, message: str, *, code: str = "publish_failed") -> None:
        super().__init__(message)
        self.code = code


def normalize_pr_title(value: str) -> str:
    normalized = _normalize_publish_text(value, max_chars=MAX_PR_TITLE_CHARS)
    if not normalized or "\n" in normalized:
        raise ValueError("Pull request title is invalid")
    return normalized


def normalize_pr_body(value: str) -> str:
    return _normalize_publish_text(value, max_chars=MAX_PR_BODY_CHARS)


def build_publish_branch(task_id: str, head_sha: str) -> str:
    if TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise ValueError("Publish task id is invalid")
    if GIT_OBJECT_ID_PATTERN.fullmatch(head_sha) is None:
        raise ValueError("Publish head is invalid")
    return f"codex/modelmirror-{task_id[:12].lower()}-{head_sha[:12]}"


@dataclass(frozen=True, slots=True)
class PublishCommit:
    commit_id: str
    commit_sha: str
    parent_sha: str
    message: str
    files: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.commit_id, str) or COMMIT_ID_PATTERN.fullmatch(self.commit_id) is None:
            raise ValueError("Publish commit id is invalid")
        if any(
            not isinstance(value, str) or GIT_OBJECT_ID_PATTERN.fullmatch(value) is None
            for value in (self.commit_sha, self.parent_sha)
        ):
            raise ValueError("Publish commit object id is invalid")
        if self.commit_sha == self.parent_sha:
            raise ValueError("Publish commit parent is invalid")
        if normalize_commit_message(self.message) != self.message:
            raise ValueError("Publish commit message is invalid")
        safe_files = _normalize_paths(self.files)
        if not safe_files or safe_files != self.files:
            raise ValueError("Publish commit files are invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "commit_sha": self.commit_sha,
            "parent_sha": self.parent_sha,
            "message": self.message,
            "files": list(self.files),
        }

    @classmethod
    def from_dict(cls, value: Any) -> PublishCommit:
        expected = {"commit_id", "commit_sha", "parent_sha", "message", "files"}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Publish commit payload is invalid")
        files = value["files"]
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise ValueError("Publish commit files are invalid")
        return cls(
            commit_id=value["commit_id"],
            commit_sha=value["commit_sha"],
            parent_sha=value["parent_sha"],
            message=value["message"],
            files=tuple(files),
        )


@dataclass(frozen=True, slots=True)
class PublishManifest:
    publish_id: str
    task_id: str
    revision: int
    snapshot_fingerprint: str
    base_sha: str
    head_sha: str
    commits: tuple[PublishCommit, ...]
    title: str
    body: str

    def __post_init__(self) -> None:
        if not isinstance(self.publish_id, str) or PUBLISH_ID_PATTERN.fullmatch(self.publish_id) is None:
            raise ValueError("Publish id is invalid")
        if not isinstance(self.task_id, str) or TASK_ID_PATTERN.fullmatch(self.task_id) is None:
            raise ValueError("Publish task id is invalid")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("Publish revision is invalid")
        if (
            not isinstance(self.snapshot_fingerprint, str)
            or SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(self.snapshot_fingerprint) is None
        ):
            raise ValueError("Publish snapshot fingerprint is invalid")
        if any(
            not isinstance(value, str) or GIT_OBJECT_ID_PATTERN.fullmatch(value) is None
            for value in (self.base_sha, self.head_sha)
        ):
            raise ValueError("Publish object id is invalid")
        if not isinstance(self.commits, tuple) or not 1 <= len(self.commits) <= MAX_PUBLISH_COMMITS:
            raise ValueError("Publish commit count is invalid")
        expected_parent = self.base_sha
        all_files: set[str] = set()
        for commit in self.commits:
            if not isinstance(commit, PublishCommit) or commit.parent_sha != expected_parent:
                raise ValueError("Publish commit chain is invalid")
            expected_parent = commit.commit_sha
            all_files.update(commit.files)
        if expected_parent != self.head_sha or len(all_files) > MAX_PUBLISH_FILES:
            raise ValueError("Publish commit chain is inconsistent")
        if any(path.startswith(".github/workflows/") for path in all_files):
            raise ValueError("Workflow files cannot be published")
        if normalize_pr_title(self.title) != self.title or normalize_pr_body(self.body) != self.body:
            raise ValueError("Pull request text is invalid")

    @property
    def branch(self) -> str:
        return build_publish_branch(self.task_id, self.head_sha)

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(sorted({path for commit in self.commits for path in commit.files}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "publish_id": self.publish_id,
            "task_id": self.task_id,
            "revision": self.revision,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "commits": [commit.to_dict() for commit in self.commits],
            "title": self.title,
            "body": self.body,
        }

    @classmethod
    def from_dict(cls, value: Any) -> PublishManifest:
        expected = {
            "publish_id", "task_id", "revision", "snapshot_fingerprint",
            "base_sha", "head_sha", "commits", "title", "body",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Publish manifest payload is invalid")
        commits = value["commits"]
        if not isinstance(commits, list):
            raise ValueError("Publish manifest commits are invalid")
        return cls(
            publish_id=value["publish_id"],
            task_id=value["task_id"],
            revision=value["revision"],
            snapshot_fingerprint=value["snapshot_fingerprint"],
            base_sha=value["base_sha"],
            head_sha=value["head_sha"],
            commits=tuple(PublishCommit.from_dict(item) for item in commits),
            title=value["title"],
            body=value["body"],
        )


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    publish_id: str
    revision: int
    repository_id: int
    repository: str
    base_branch: str
    branch: str
    head_sha: str
    pr_number: int
    pr_node_id: str
    pr_url: str
    state: PublishState = PublishState.DRAFT
    published_at: float = field(default_factory=time.time)
    ready_at: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.publish_id, str) or PUBLISH_ID_PATTERN.fullmatch(self.publish_id) is None:
            raise ValueError("Publish receipt id is invalid")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise ValueError("Publish receipt revision is invalid")
        if (
            isinstance(self.repository_id, bool)
            or not isinstance(self.repository_id, int)
            or self.repository_id < 1
        ):
            raise ValueError("Publish repository id is invalid")
        if not isinstance(self.repository, str) or REPOSITORY_PATTERN.fullmatch(self.repository) is None:
            raise ValueError("Publish repository is invalid")
        if not _safe_branch(self.base_branch) or not _safe_branch(self.branch):
            raise ValueError("Publish branch is invalid")
        if not isinstance(self.head_sha, str) or GIT_OBJECT_ID_PATTERN.fullmatch(self.head_sha) is None:
            raise ValueError("Publish head is invalid")
        if isinstance(self.pr_number, bool) or not isinstance(self.pr_number, int) or self.pr_number < 1:
            raise ValueError("Pull request number is invalid")
        if not isinstance(self.pr_node_id, str) or NODE_ID_PATTERN.fullmatch(self.pr_node_id) is None:
            raise ValueError("Pull request node id is invalid")
        if not isinstance(self.pr_url, str):
            raise ValueError("Pull request URL is invalid")
        parsed = urlparse(self.pr_url)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username:
            raise ValueError("Pull request URL is invalid")
        if not isinstance(self.state, PublishState) or self.state not in {
            PublishState.DRAFT,
            PublishState.READY,
        }:
            raise ValueError("Publish receipt state is invalid")
        timestamps = (self.published_at,) if self.ready_at is None else (self.published_at, self.ready_at)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            for value in timestamps
        ):
            raise ValueError("Publish receipt timestamp is invalid")
        if self.state is PublishState.READY:
            if self.ready_at is None or self.ready_at < self.published_at:
                raise ValueError("Publish ready timestamp is invalid")
        elif self.ready_at is not None:
            raise ValueError("Draft receipt cannot have ready timestamp")

    def to_dict(self) -> dict[str, Any]:
        return {
            "publish_id": self.publish_id,
            "revision": self.revision,
            "repository_id": self.repository_id,
            "repository": self.repository,
            "base_branch": self.base_branch,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "pr_number": self.pr_number,
            "pr_node_id": self.pr_node_id,
            "pr_url": self.pr_url,
            "state": self.state.value,
            "published_at": self.published_at,
            "ready_at": self.ready_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> PublishReceipt:
        expected = {
            "publish_id", "revision", "repository_id", "repository", "base_branch",
            "branch", "head_sha", "pr_number", "pr_node_id", "pr_url", "state",
            "published_at", "ready_at",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Publish receipt payload is invalid")
        return cls(
            publish_id=value["publish_id"],
            revision=value["revision"],
            repository_id=value["repository_id"],
            repository=value["repository"],
            base_branch=value["base_branch"],
            branch=value["branch"],
            head_sha=value["head_sha"],
            pr_number=value["pr_number"],
            pr_node_id=value["pr_node_id"],
            pr_url=value["pr_url"],
            state=PublishState(value["state"]),
            published_at=value["published_at"],
            ready_at=value["ready_at"],
        )


def _normalize_publish_text(value: str, *, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ValueError("Publish text must be text")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) > max_chars or any(
        unicodedata.category(character) == "Cc" and character != "\n"
        for character in normalized
    ):
        raise ValueError("Publish text is outside the allowed scope")
    if any(pattern.search(normalized) for pattern in _SECRET_PATTERNS):
        raise ValueError("Publish text contains a secret pattern")
    return normalized


def _normalize_paths(paths: Iterable[str]) -> tuple[str, ...]:
    try:
        safe_paths = tuple(DraftWorkspace.normalize_relative_path(path) for path in paths)
    except (DraftPolicyError, TypeError) as exc:
        raise ValueError("Publish path is invalid") from exc
    if safe_paths != tuple(sorted(set(safe_paths))) or len(safe_paths) > MAX_PUBLISH_FILES:
        raise ValueError("Publish paths are invalid")
    return safe_paths


def _safe_branch(value: str) -> bool:
    return bool(
        isinstance(value, str)
        and BRANCH_PATTERN.fullmatch(value)
        and ".." not in value
        and "//" not in value
        and not value.endswith((".", "/", ".lock"))
    )
