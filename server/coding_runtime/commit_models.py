from __future__ import annotations

import math
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from .apply_models import APPLY_ID_PATTERN
from .draft_workspace import DraftPolicyError, DraftWorkspace
from .verification import select_verification_plan


COMMIT_BRANCH = "coding/local-draft"
COMMIT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
GIT_OBJECT_ID_PATTERN = re.compile(r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
MAX_COMMIT_MESSAGE_CHARS = 2_000
MAX_COMMIT_SUBJECT_CHARS = 120
MAX_COMMIT_BRANCH_CHARS = 200


class CommitState(StrEnum):
    NOT_COMMITTED = "not_committed"
    COMMITTING = "committing"
    COMMITTED = "committed"
    UNDOING = "undoing"
    UNDONE = "undone"
    FAILED = "failed"


class CodingCommitError(RuntimeError):
    def __init__(self, message: str, *, code: str = "commit_failed") -> None:
        super().__init__(message)
        self.code = code


def normalize_commit_message(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Commit message must be text")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    normalized = "\n".join(lines)
    if (
        not normalized
        or len(normalized) > MAX_COMMIT_MESSAGE_CHARS
        or len(lines[0]) > MAX_COMMIT_SUBJECT_CHARS
        or any(
            unicodedata.category(character) == "Cc" and character != "\n"
            for character in normalized
        )
    ):
        raise ValueError("Commit message is outside the allowed scope")
    return normalized


def validate_commit_branch(value: str) -> str:
    """Validate a branch without invoking repository Git configuration."""
    if not isinstance(value, str):
        raise ValueError("Commit branch must be text")
    normalized = unicodedata.normalize("NFC", value)
    parts = normalized.split("/")
    if (
        not normalized
        or normalized != value
        or len(normalized) > MAX_COMMIT_BRANCH_CHARS
        or normalized in {"@", "HEAD"}
        or normalized.startswith(("-", "/"))
        or normalized.endswith((".", "/"))
        or ".." in normalized
        or "@{" in normalized
        or "\\" in normalized
        or any(
            not part
            or part.startswith(".")
            or part.endswith(".")
            or part.endswith(".lock")
            for part in parts
        )
        or any(
            character in " ~^:?*["
            or unicodedata.category(character).startswith("C")
            for character in normalized
        )
    ):
        raise ValueError("Commit branch is invalid")
    return normalized


def suggest_commit_message(paths: Iterable[str]) -> str:
    try:
        safe_paths = tuple(
            sorted({DraftWorkspace.normalize_relative_path(path) for path in paths})
        )
    except DraftPolicyError as exc:
        raise ValueError("Commit paths are invalid") from exc
    if not safe_paths:
        raise ValueError("Commit paths are empty")
    plan = select_verification_plan(safe_paths)
    if plan.reason == "documentation_only":
        return "docs: 更新项目说明"
    if all(_is_test_path(path) for path in safe_paths):
        return "test: 更新项目检查"
    return "feature: 更新项目功能"


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    commit_id: str
    revision: int
    apply_id: str
    commit_sha: str
    parent_sha: str
    tree_sha: str
    message: str
    files: tuple[str, ...]
    branch: str = COMMIT_BRANCH
    committed_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not COMMIT_ID_PATTERN.fullmatch(self.commit_id):
            raise ValueError("Commit id is invalid")
        if isinstance(self.revision, bool) or self.revision < 0:
            raise ValueError("Commit revision is invalid")
        if not APPLY_ID_PATTERN.fullmatch(self.apply_id):
            raise ValueError("Commit apply id is invalid")
        if any(
            not GIT_OBJECT_ID_PATTERN.fullmatch(value)
            for value in (self.commit_sha, self.parent_sha, self.tree_sha)
        ):
            raise ValueError("Commit object id is invalid")
        if self.commit_sha in {self.parent_sha, self.tree_sha}:
            raise ValueError("Commit object ids are inconsistent")
        if normalize_commit_message(self.message) != self.message:
            raise ValueError("Commit message is not normalized")
        if validate_commit_branch(self.branch) != self.branch:
            raise ValueError("Commit branch is invalid")
        try:
            safe_files = tuple(
                DraftWorkspace.normalize_relative_path(path) for path in self.files
            )
        except DraftPolicyError as exc:
            raise ValueError("Commit receipt path is invalid") from exc
        if (
            not safe_files
            or len(safe_files) > 20
            or safe_files != self.files
            or safe_files != tuple(sorted(set(safe_files)))
        ):
            raise ValueError("Commit receipt files are invalid")
        if not math.isfinite(self.committed_at) or self.committed_at < 0:
            raise ValueError("Commit timestamp is invalid")

    @classmethod
    def create(
        cls,
        *,
        revision: int,
        apply_id: str,
        commit_sha: str,
        parent_sha: str,
        tree_sha: str,
        message: str,
        files: tuple[str, ...],
        branch: str = COMMIT_BRANCH,
    ) -> CommitReceipt:
        return cls(
            commit_id=secrets.token_urlsafe(18),
            revision=revision,
            apply_id=apply_id,
            commit_sha=commit_sha,
            parent_sha=parent_sha,
            tree_sha=tree_sha,
            message=normalize_commit_message(message),
            files=files,
            branch=validate_commit_branch(branch),
        )

    def to_public(self, *, state: CommitState = CommitState.COMMITTED) -> dict[str, object]:
        return {
            "revision": self.revision,
            "state": state.value,
            "commit_id": self.commit_id,
            "commit_sha": self.commit_sha,
            "short_sha": self.commit_sha[:12],
            "branch": self.branch,
            "message": self.message,
            "committed_at": self.committed_at,
            "file_count": len(self.files),
            "can_undo": state is CommitState.COMMITTED,
        }


def not_committed_payload(
    revision: int,
    *,
    suggested_message: str,
    state: CommitState = CommitState.NOT_COMMITTED,
    reason: str | None = None,
    branch: str = COMMIT_BRANCH,
) -> dict[str, object]:
    if isinstance(revision, bool) or revision < 0:
        raise ValueError("Commit revision is invalid")
    return {
        "revision": revision,
        "state": state.value,
        "commit_id": None,
        "commit_sha": None,
        "short_sha": None,
        "branch": validate_commit_branch(branch),
        "message": None,
        "suggested_message": normalize_commit_message(suggested_message),
        "committed_at": None,
        "file_count": 0,
        "can_undo": False,
        "reason": reason,
    }


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", maxsplit=1)[-1]
    return (
        lowered.startswith("server/tests/")
        or lowered.startswith("client/tests/")
        or "/__tests__/" in lowered
        or name.startswith("test_")
        or name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )
