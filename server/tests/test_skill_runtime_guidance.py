from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.skills.application_receipts import build_application_contract
from server.skills.runtime_guidance import (
    SkillRuntimeGuidanceError,
    build_skill_runtime_guidance_plan,
    missing_required_skill_ids,
    skill_guidance_plan_status_events,
    skill_runtime_guidance_enabled,
    tool_requires_skill_application,
)


def _contract(skill_id: str, marker: str):
    return build_application_contract(
        skill_id=skill_id,
        source_kind="workspace_draft",
        version_id=f"skillversion_{marker}",
        content_digest=marker * 64,
        policy="require_read",
    )


def test_guidance_flag_defaults_on_and_accepts_explicit_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SKILL_RUNTIME_GUIDANCE_V2_ENABLED", raising=False)
    assert skill_runtime_guidance_enabled() is True
    monkeypatch.setenv("SKILL_RUNTIME_GUIDANCE_V2_ENABLED", "false")
    assert skill_runtime_guidance_enabled() is False


def test_plan_distinguishes_explicit_activated_plugin_and_auto_sources() -> None:
    contracts = {
        "explicit-skill": _contract("explicit-skill", "1"),
        "plugin-skill": _contract("plugin-skill", "2"),
        "activated-skill": _contract("activated-skill", "3"),
    }
    plan = build_skill_runtime_guidance_plan(
        task_id="task-1",
        run_id="run-1",
        node_id="agent-1",
        explicit_skill_ids={"explicit-skill"},
        plugin_skill_ids={"plugin-skill"},
        activated_skill_ids={"activated-skill"},
        auto_discover=True,
        contracts=contracts,
    )

    assert plan.required_skill_ids == ("activated-skill", "explicit-skill")
    assert plan.available_skill_ids == ("plugin-skill",)
    assert plan.auto_discover is True
    assert plan.fingerprint == build_skill_runtime_guidance_plan(
        task_id="task-1",
        run_id="run-1",
        node_id="agent-1",
        explicit_skill_ids={"explicit-skill"},
        plugin_skill_ids={"plugin-skill"},
        activated_skill_ids={"activated-skill"},
        auto_discover=True,
        contracts=contracts,
    ).fingerprint
    events = skill_guidance_plan_status_events(plan)
    assert [event["status"] for event in events] == [
        "required",
        "required",
        "available",
    ]
    assert all("content_digest" not in event for event in events)
    assert all("trust_fingerprint" not in event for event in events)


def test_required_skill_without_frozen_contract_fails_closed() -> None:
    with pytest.raises(SkillRuntimeGuidanceError) as exc_info:
        build_skill_runtime_guidance_plan(
            task_id="task-1",
            run_id="run-1",
            node_id="agent-1",
            explicit_skill_ids={"missing-skill"},
            plugin_skill_ids=set(),
            activated_skill_ids=set(),
            auto_discover=False,
            contracts={},
        )

    assert exc_info.value.code == "skill_application_contract_stale"


def test_missing_required_skills_requires_exact_verified_contract() -> None:
    contract = _contract("required-skill", "4")
    plan = build_skill_runtime_guidance_plan(
        task_id="task-1",
        run_id="run-1",
        node_id="agent-1",
        explicit_skill_ids={"required-skill"},
        plugin_skill_ids=set(),
        activated_skill_ids=set(),
        auto_discover=False,
        contracts={"required-skill": contract},
    )
    forged = SimpleNamespace(
        task_id="task-1",
        run_id="run-1",
        node_ids=("agent-1",),
        skill_id="required-skill",
        contract_fingerprint="f" * 64,
        compliance_status="verified",
    )
    verified = SimpleNamespace(
        task_id="task-1",
        run_id="run-1",
        node_ids=("agent-1",),
        skill_id="required-skill",
        contract_fingerprint=contract.fingerprint,
        compliance_status="verified",
    )

    assert missing_required_skill_ids(plan, [forged]) == ("required-skill",)
    assert missing_required_skill_ids(plan, [forged, verified]) == ()


@pytest.mark.parametrize(
    ("tool_name", "read_only", "sensitive", "requires_approval", "terminal", "expected"),
    [
        ("skill_read", True, False, False, False, False),
        ("skill_stage", False, False, False, False, False),
        ("skill_enable", False, False, False, False, False),
        ("skill_install", False, True, True, False, True),
        ("sandbox_read_file", True, False, False, False, False),
        ("write_record", False, False, False, False, True),
        ("sensitive_read", True, True, False, False, True),
        ("approved_read", True, False, True, False, True),
        ("terminal_read", True, False, False, True, True),
    ],
)
def test_tool_gate_uses_server_tool_semantics(
    tool_name: str,
    read_only: bool,
    sensitive: bool,
    requires_approval: bool,
    terminal: bool,
    expected: bool,
) -> None:
    assert tool_requires_skill_application(
        tool_name=tool_name,
        read_only=read_only,
        sensitive=sensitive,
        requires_approval=requires_approval,
        terminal=terminal,
    ) is expected
