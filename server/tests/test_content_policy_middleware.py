from __future__ import annotations

import pytest

from server.xpert_runtime.content_policy import (
    ContentPolicyError,
    apply_content_policy,
    validate_content_policy_config,
)
from server.xpert_runtime.core_middlewares import (
    RuntimeMiddlewareSpec,
    build_content_policy_middleware,
)
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import (
    MiddlewareContext,
    ModelCallRequest,
    ToolCallRequest,
    ToolCallResponse,
)


def policy_config(*rules: dict[str, object], phase: str = "both") -> dict[str, object]:
    return {
        "phase": phase,
        "rules": list(rules)
        or [
            {
                "id": "rule_1",
                "label": "Credential",
                "detector": "secret_pattern",
                "action": "block",
                "terms": [],
                "caseSensitive": False,
            }
        ],
    }


def literal_rule(
    rule_id: str,
    term: str,
    *,
    action: str = "redact",
) -> dict[str, object]:
    return {
        "id": rule_id,
        "label": rule_id,
        "detector": "literal_terms",
        "action": action,
        "terms": [term],
        "caseSensitive": False,
    }


def test_content_policy_block_never_contains_matched_secret() -> None:
    marker = "api_key=R19_SYNTHETIC_SECRET_12345"
    config = validate_content_policy_config(policy_config())

    with pytest.raises(ContentPolicyError) as exc_info:
        apply_content_policy(marker, config, phase="input")

    assert exc_info.value.code == "content_policy_blocked_input"
    assert exc_info.value.rule_id == "rule_1"
    assert marker not in str(exc_info.value)
    assert "R19_SYNTHETIC_SECRET" not in str(exc_info.value)


def test_content_policy_redacts_email_phone_and_overlapping_literals() -> None:
    config = validate_content_policy_config(
        policy_config(
            literal_rule("rule_1", "secret"),
            literal_rule("rule_2", "secret-value"),
            {
                "id": "rule_3",
                "label": "Email",
                "detector": "email_address",
                "action": "redact",
                "terms": [],
                "caseSensitive": False,
            },
            {
                "id": "rule_4",
                "label": "Phone",
                "detector": "phone_number",
                "action": "redact",
                "terms": [],
                "caseSensitive": False,
            },
        )
    )

    result = apply_content_policy(
        "secret-value contact user@example.com or +1 (602) 555-0123",
        config,
        phase="output",
    )

    assert result.text == "[已脱敏] contact [已脱敏] or [已脱敏]"
    assert result.match_count == 3
    assert result.rule_ids == ("rule_2", "rule_3", "rule_4")


@pytest.mark.parametrize(
    "marker",
    [
        "token=R19SYNTHETICTOKEN12345",
        "client_secret=R19SYNTHETICSECRET12345",
        "-----BEGIN PRIVATE KEY-----",
        "eyJabcdefghi.abcdefghijk.abcdefghijkl",
    ],
)
def test_content_policy_credential_detector_covers_common_safe_shapes(
    marker: str,
) -> None:
    config = validate_content_policy_config(
        policy_config(
            {
                "id": "rule_1",
                "label": "Credential",
                "detector": "secret_pattern",
                "action": "redact",
                "terms": [],
                "caseSensitive": False,
            }
        )
    )

    result = apply_content_policy(marker, config, phase="input")

    assert result.text == "[已脱敏]"


def test_content_policy_block_takes_precedence_over_redaction() -> None:
    config = validate_content_policy_config(
        policy_config(
            literal_rule("rule_1", "marker", action="redact"),
            literal_rule("rule_2", "marker", action="block"),
        )
    )

    with pytest.raises(ContentPolicyError) as exc_info:
        apply_content_policy("marker", config, phase="output")
    assert exc_info.value.code == "content_policy_blocked_output"
    assert exc_info.value.rule_id == "rule_2"


@pytest.mark.asyncio
async def test_content_policy_pipeline_redacts_non_system_model_input() -> None:
    spec = RuntimeMiddlewareSpec(
        node_id="policy-1",
        middleware_id="content_policy",
        config=policy_config(literal_rule("rule_1", "sentinel")),
    )
    pipeline = MiddlewarePipeline([build_content_policy_middleware(spec)])
    context = MiddlewareContext()

    prepared = await pipeline.before_model(
        ModelCallRequest(
            model_id="test/model",
            messages=[
                {"role": "system", "content": "sentinel stays in system"},
                {"role": "user", "content": "sentinel leaves user"},
                {"role": "tool", "content": [{"type": "text", "text": "sentinel tool"}]},
            ],
        ),
        context,
    )
    assert prepared.messages[0]["content"] == "sentinel stays in system"
    assert prepared.messages[1]["content"] == "[已脱敏] leaves user"
    assert prepared.messages[2]["content"][0]["text"] == "[已脱敏] tool"
    assert context.metadata["content_policy"] == [
        {"phase": "input", "rule_ids": ["rule_1"], "match_count": 2},
    ]


@pytest.mark.asyncio
async def test_content_policy_redacts_tool_output_before_agent_observes_it() -> None:
    spec = RuntimeMiddlewareSpec(
        node_id="policy-1",
        middleware_id="content_policy",
        config=policy_config(literal_rule("rule_1", "sentinel"), phase="input"),
    )
    pipeline = MiddlewarePipeline([build_content_policy_middleware(spec)])
    context = MiddlewareContext()
    original_raw = [
        {"type": "text", "text": "sentinel in raw text"},
        {"type": "resource", "content": "sentinel in textual content"},
        {"type": "image", "data": "sentinel-is-not-scanned-binary"},
    ]

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        return ToolCallResponse(output="sentinel in output", raw=original_raw)

    guarded = await pipeline.run_tool_call(
        ToolCallRequest(tool_name="lookup"),
        handler,
        context,
    )

    assert guarded.output == "[已脱敏] in output"
    assert guarded.raw[0]["text"] == "[已脱敏] in raw text"
    assert guarded.raw[1]["content"] == "[已脱敏] in textual content"
    assert guarded.raw[2]["data"] == "sentinel-is-not-scanned-binary"
    assert original_raw[0]["text"] == "sentinel in raw text"
    assert context.metadata["content_policy"] == [
        {"phase": "input", "rule_ids": ["rule_1"], "match_count": 3},
    ]


@pytest.mark.asyncio
async def test_content_policy_blocks_tool_output_without_fail_open() -> None:
    spec = RuntimeMiddlewareSpec(
        node_id="policy-1",
        middleware_id="content_policy",
        config=policy_config(literal_rule("rule_1", "sentinel", action="block")),
    )
    pipeline = MiddlewarePipeline([build_content_policy_middleware(spec)])

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        return ToolCallResponse(output="sentinel")

    with pytest.raises(ContentPolicyError) as exc_info:
        await pipeline.run_tool_call(
            ToolCallRequest(tool_name="lookup"),
            handler,
            MiddlewareContext(),
        )
    assert exc_info.value.code == "content_policy_blocked_input"


def test_content_policy_rejects_empty_rules_and_unexpected_terms() -> None:
    with pytest.raises(ContentPolicyError, match="1 to 20"):
        validate_content_policy_config({"phase": "both", "rules": []})
    with pytest.raises(ContentPolicyError) as exc_info:
        validate_content_policy_config(
            policy_config(
                {
                    "id": "rule_1",
                    "label": "Email",
                    "detector": "email_address",
                    "action": "redact",
                    "terms": ["not-used"],
                    "caseSensitive": False,
                }
            )
        )
    assert exc_info.value.code == "content_policy_unexpected_terms"


def test_content_policy_rejects_custom_replacement_and_loose_booleans() -> None:
    custom_replacement = literal_rule("rule_1", "marker")
    custom_replacement["replacementTemplate"] = "unsafe {{match}}"
    with pytest.raises(ContentPolicyError) as exc_info:
        validate_content_policy_config(policy_config(custom_replacement))
    assert exc_info.value.code == "content_policy_unexpected_rule_field"

    loose_boolean = literal_rule("rule_1", "marker")
    loose_boolean["caseSensitive"] = "false"
    with pytest.raises(ContentPolicyError) as exc_info:
        validate_content_policy_config(policy_config(loose_boolean))
    assert exc_info.value.code == "content_policy_invalid_case_sensitive"


def test_content_policy_fails_closed_above_text_limit() -> None:
    config = validate_content_policy_config(
        policy_config(literal_rule("rule_1", "marker"))
    )
    with pytest.raises(ContentPolicyError) as input_error:
        apply_content_policy("x" * 200_001, config, phase="input")
    with pytest.raises(ContentPolicyError) as output_error:
        apply_content_policy("x" * 200_001, config, phase="output")
    assert input_error.value.code == "content_policy_input_too_large"
    assert output_error.value.code == "content_policy_output_too_large"
