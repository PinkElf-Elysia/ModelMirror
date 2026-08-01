from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from .draft_workspace import DraftWorkspace


MAX_VERIFICATION_SUMMARY_CHARS = 500
MAX_VERIFICATION_DETAIL_CHARS = 16_000
VERIFICATION_MAX_DURATION_SECONDS = 600


class VerificationState(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class VerificationResult(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class VerificationStepId(StrEnum):
    BACKEND_TESTS = "backend_tests"
    BACKEND_BASELINE_TESTS = "backend_baseline_tests"
    BACKEND_DRAFT_TESTS = "backend_draft_tests"
    FRONTEND_BUILD = "frontend_build"


_STEP_LABELS: dict[VerificationStepId, str] = {
    VerificationStepId.BACKEND_TESTS: "检查服务代码",
    VerificationStepId.BACKEND_BASELINE_TESTS: "使用原有测试检查服务代码",
    VerificationStepId.BACKEND_DRAFT_TESTS: "检查更新后的服务测试",
    VerificationStepId.FRONTEND_BUILD: "检查页面构建",
}
_DEPENDENCY_MANIFESTS = frozenset(
    {
        "server/requirements.txt",
        "client/package.json",
        "client/package-lock.json",
    }
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:\\(?:[^\\\r\n\t :]+\\)*[^\\\r\n\t :]*"
)
_CONTAINER_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:workspace|opt/modelmirror-source|tmp|home/verifier)"
    r"(?:/[^\s:]+)*"
)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class VerificationPlan:
    paths: tuple[str, ...]
    step_ids: tuple[VerificationStepId, ...]
    reason: str | None = None

    @property
    def runnable(self) -> bool:
        return bool(self.step_ids) and self.reason is None


@dataclass(frozen=True, slots=True)
class SanitizedOutput:
    text: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class VerificationStep:
    step_id: VerificationStepId
    state: VerificationState = VerificationState.NOT_STARTED
    result: VerificationResult = VerificationResult.NOT_RUN
    duration_ms: int | None = None
    summary: str = ""
    details: str = field(default="", repr=False)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.step_id.value,
            "label": _STEP_LABELS[self.step_id],
            "state": self.state.value,
            "result": self.result.value,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "details": self.details,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    revision: int
    state: VerificationState
    result: VerificationResult
    steps: tuple[VerificationStep, ...]
    reason: str | None = None
    started_at: float | None = None
    finished_at: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("Verification revision must be a non-negative integer.")
        if self.state is VerificationState.RUNNING and self.started_at is None:
            raise ValueError("Running verification must have a start time.")
        if self.state in {
            VerificationState.COMPLETED,
            VerificationState.CANCELLED,
        } and self.finished_at is None:
            raise ValueError("Terminal verification must have a finish time.")

    def to_dict(self, *, current_revision: int | None = None) -> dict[str, Any]:
        stale = (
            current_revision is not None
            and not isinstance(current_revision, bool)
            and current_revision != self.revision
        )
        return {
            "revision": self.revision,
            "state": self.state.value,
            "result": self.result.value,
            "stale": stale,
            "reason": self.reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": [step.to_dict() for step in self.steps],
        }


def select_verification_plan(paths: Iterable[str]) -> VerificationPlan:
    safe_paths = tuple(
        sorted({DraftWorkspace.normalize_relative_path(path) for path in paths})
    )
    if any(path in _DEPENDENCY_MANIFESTS for path in safe_paths):
        return VerificationPlan(
            paths=safe_paths,
            step_ids=(),
            reason="dependency_change_unsupported",
        )
    if not safe_paths:
        return VerificationPlan(paths=(), step_ids=(), reason="no_changes")
    if all(_is_documentation_path(path) for path in safe_paths):
        return VerificationPlan(
            paths=safe_paths,
            step_ids=(),
            reason="documentation_only",
        )

    server_only = all(path.startswith("server/") for path in safe_paths)
    client_only = all(path.startswith("client/") for path in safe_paths)
    has_test_changes = any(path.startswith("server/tests/") for path in safe_paths)

    steps: list[VerificationStepId] = []
    if server_only:
        steps.extend(_backend_steps(has_test_changes))
    elif client_only:
        steps.append(VerificationStepId.FRONTEND_BUILD)
    else:
        steps.extend(_backend_steps(has_test_changes))
        steps.append(VerificationStepId.FRONTEND_BUILD)
    return VerificationPlan(paths=safe_paths, step_ids=tuple(steps))


def initial_verification_report(
    revision: int,
    plan: VerificationPlan,
    *,
    now: float | None = None,
) -> VerificationReport:
    steps = tuple(VerificationStep(step_id=step_id) for step_id in plan.step_ids)
    if plan.runnable:
        return VerificationReport(
            revision=revision,
            state=VerificationState.NOT_STARTED,
            result=VerificationResult.NOT_RUN,
            steps=steps,
        )
    result = (
        VerificationResult.NOT_APPLICABLE
        if plan.reason in {"documentation_only", "no_changes"}
        else VerificationResult.NOT_RUN
    )
    return VerificationReport(
        revision=revision,
        state=VerificationState.COMPLETED,
        result=result,
        steps=(),
        reason=plan.reason,
        finished_at=0.0 if now is None else now,
    )


def sanitize_verification_output(
    value: Any,
    *,
    limit: int = MAX_VERIFICATION_DETAIL_CHARS,
    keep_tail: bool = True,
) -> SanitizedOutput:
    if limit < 1:
        raise ValueError("Verification output limit must be positive.")
    text = _CONTROL_CHARACTERS.sub("", str(value or ""))
    text = text.replace("\\workspace", "[workspace]")
    text = text.replace("/workspace", "[workspace]")
    text = text.replace("/opt/modelmirror-source", "[source]")
    text = _WINDOWS_ABSOLUTE_PATH.sub("[redacted-path]", text)
    text = _CONTAINER_ABSOLUTE_PATH.sub("[redacted-path]", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted-secret]", text)
    if len(text) <= limit:
        return SanitizedOutput(text=text, truncated=False)
    marker = "\n…输出已截断…\n"
    available = max(1, limit - len(marker))
    rendered = (
        f"{marker}{text[-available:]}"
        if keep_tail
        else f"{text[:available]}{marker}"
    )
    return SanitizedOutput(text=rendered[:limit], truncated=True)


def _backend_steps(has_test_changes: bool) -> tuple[VerificationStepId, ...]:
    if has_test_changes:
        return (
            VerificationStepId.BACKEND_BASELINE_TESTS,
            VerificationStepId.BACKEND_DRAFT_TESTS,
        )
    return (VerificationStepId.BACKEND_TESTS,)


def _is_documentation_path(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", maxsplit=1)[-1]
    return (
        lowered.startswith("docs/")
        or name.startswith(("readme", "license", "notice"))
        or name
        in {
            "authors",
            "changelog",
            "third_party_notices.md",
            "third-party-notices.md",
        }
    )
