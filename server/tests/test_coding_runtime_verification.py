from __future__ import annotations

import pytest

from server.coding_runtime.draft_workspace import DraftPolicyError
from server.coding_runtime.verification import (
    VerificationResult,
    VerificationState,
    VerificationStepId,
    initial_verification_report,
    sanitize_verification_output,
    select_verification_plan,
)


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (
            ["server/coding_runtime/api.py"],
            (VerificationStepId.BACKEND_TESTS,),
        ),
        (
            ["client/src/pages/CodingPage.tsx"],
            (VerificationStepId.FRONTEND_BUILD,),
        ),
        (
            ["client/src/content.md"],
            (VerificationStepId.FRONTEND_BUILD,),
        ),
        (
            ["server/main.py", "client/src/App.tsx"],
            (
                VerificationStepId.BACKEND_TESTS,
                VerificationStepId.FRONTEND_BUILD,
            ),
        ),
        (
            ["docker-compose.yml"],
            (
                VerificationStepId.BACKEND_TESTS,
                VerificationStepId.FRONTEND_BUILD,
            ),
        ),
    ],
)
def test_verification_plan_selects_fixed_steps(
    paths: list[str],
    expected: tuple[VerificationStepId, ...],
) -> None:
    plan = select_verification_plan(paths)

    assert plan.step_ids == expected
    assert plan.reason is None
    assert plan.runnable is True


def test_test_changes_run_immutable_baseline_before_draft_tests() -> None:
    plan = select_verification_plan(
        ["server/coding_runtime/api.py", "server/tests/test_coding_runtime_api.py"]
    )

    assert plan.step_ids == (
        VerificationStepId.BACKEND_BASELINE_TESTS,
        VerificationStepId.BACKEND_DRAFT_TESTS,
    )


@pytest.mark.parametrize(
    "path",
    [
        "server/requirements.txt",
        "client/package.json",
        "client/package-lock.json",
    ],
)
def test_dependency_manifest_change_is_not_run(path: str) -> None:
    plan = select_verification_plan([path])
    report = initial_verification_report(4, plan, now=12.5)

    assert plan.reason == "dependency_change_unsupported"
    assert report.state is VerificationState.COMPLETED
    assert report.result is VerificationResult.NOT_RUN
    assert report.finished_at == 12.5


def test_documentation_only_change_is_not_applicable() -> None:
    plan = select_verification_plan(
        ["docs/CODING_AGENT_INTEGRATION.md", "README.md"]
    )
    report = initial_verification_report(2, plan, now=8.0)

    assert report.result is VerificationResult.NOT_APPLICABLE
    assert report.reason == "documentation_only"
    assert report.steps == ()


def test_report_marks_an_old_revision_stale() -> None:
    plan = select_verification_plan(["server/main.py"])
    report = initial_verification_report(3, plan)

    assert report.to_dict(current_revision=3)["stale"] is False
    assert report.to_dict(current_revision=4)["stale"] is True


@pytest.mark.parametrize(
    "path",
    ["/workspace/server/main.py", "../server/main.py", "server\\main.py"],
)
def test_plan_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(DraftPolicyError):
        select_verification_plan([path])


def test_verification_output_is_redacted_and_tail_bounded() -> None:
    secret = "sk-" + ("x" * 24)
    rendered = sanitize_verification_output(
        f"C:\\private\\repo\\server\\main.py\n"
        f"/opt/modelmirror-source/server/main.py\n{secret}\n"
        + ("a" * 200),
        limit=100,
    )

    assert rendered.truncated is True
    assert len(rendered.text) <= 100
    assert "private" not in rendered.text
    assert "modelmirror-source" not in rendered.text
    assert secret not in rendered.text
    assert rendered.text.endswith("a" * 20)


def test_verification_output_can_keep_the_summary_head() -> None:
    rendered = sanitize_verification_output(
        "important:" + ("x" * 100),
        limit=40,
        keep_tail=False,
    )

    assert rendered.truncated is True
    assert rendered.text.startswith("important:")
