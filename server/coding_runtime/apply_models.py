from __future__ import annotations

import math
import re
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum

from .draft_workspace import DraftPolicyError, DraftWorkspace
from .patch_policy import SNAPSHOT_FINGERPRINT_PATTERN


APPLY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ApplyState(StrEnum):
    NOT_APPLIED = "not_applied"
    APPLYING = "applying"
    APPLIED = "applied"
    REVERTING = "reverting"
    REVERTED = "reverted"
    FAILED = "failed"


class CodingApplyError(RuntimeError):
    def __init__(self, message: str, *, code: str = "apply_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ApplyFileReceipt:
    path: str
    existed_before: bool
    before_sha256: str | None
    after_sha256: str

    def __post_init__(self) -> None:
        try:
            normalized = DraftWorkspace.normalize_relative_path(self.path)
        except DraftPolicyError as exc:
            raise ValueError("Apply receipt path is invalid") from exc
        if normalized != self.path:
            raise ValueError("Apply receipt path is not canonical")
        if not SHA256_PATTERN.fullmatch(self.after_sha256):
            raise ValueError("Apply receipt after hash is invalid")
        if self.existed_before:
            if (
                self.before_sha256 is None
                or not SHA256_PATTERN.fullmatch(self.before_sha256)
            ):
                raise ValueError("Apply receipt before hash is invalid")
        elif self.before_sha256 is not None:
            raise ValueError("New files cannot have a before hash")


@dataclass(frozen=True, slots=True)
class ApplyReceipt:
    apply_id: str
    revision: int
    snapshot_fingerprint: str
    files: tuple[ApplyFileReceipt, ...]
    applied_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not APPLY_ID_PATTERN.fullmatch(self.apply_id):
            raise ValueError("Apply id is invalid")
        if isinstance(self.revision, bool) or self.revision < 0:
            raise ValueError("Apply revision is invalid")
        if not SNAPSHOT_FINGERPRINT_PATTERN.fullmatch(self.snapshot_fingerprint):
            raise ValueError("Apply snapshot fingerprint is invalid")
        paths = tuple(item.path for item in self.files)
        if not paths or len(paths) > 20 or paths != tuple(sorted(set(paths))):
            raise ValueError("Apply receipt files are invalid")
        if not math.isfinite(self.applied_at) or self.applied_at < 0:
            raise ValueError("Apply timestamp is invalid")

    @classmethod
    def create(
        cls,
        *,
        revision: int,
        snapshot_fingerprint: str,
        files: tuple[ApplyFileReceipt, ...],
    ) -> ApplyReceipt:
        return cls(
            apply_id=secrets.token_urlsafe(18),
            revision=revision,
            snapshot_fingerprint=snapshot_fingerprint,
            files=files,
        )

    def to_public(self, *, state: ApplyState = ApplyState.APPLIED) -> dict[str, object]:
        return {
            "revision": self.revision,
            "state": state.value,
            "apply_id": self.apply_id,
            "applied_at": self.applied_at,
            "file_count": len(self.files),
            "can_revert": state is ApplyState.APPLIED,
        }


def not_applied_payload(revision: int) -> dict[str, object]:
    if isinstance(revision, bool) or revision < 0:
        raise ValueError("Apply revision is invalid")
    return {
        "revision": revision,
        "state": ApplyState.NOT_APPLIED.value,
        "apply_id": None,
        "applied_at": None,
        "file_count": 0,
        "can_revert": False,
    }
