from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from server.orchestration_worker import (
    AGENCY_BRIDGE_PROTOCOL,
    AGENCY_UPSTREAM_REVISION,
    AgencyAgentDefinition,
    AgencyModelRequest,
    AgencyWorkerClient,
    AgencyWorkerError,
    adapt_expert_catalog,
)


def run(awaitable):
    return asyncio.run(awaitable)


def agents() -> list[AgencyAgentDefinition]:
    return [
        AgencyAgentDefinition(
            id="agent-alpha",
            path="agent-alpha",
            name="研究专家",
            department="研究部",
            description="负责调研和证据整理",
            system_prompt="你是研究专家。",
            emoji="🔎",
        ),
        AgencyAgentDefinition(
            id="agent-beta",
            path="agent-beta",
            name="交付专家",
            department="产品部",
            description="负责整合结论和验收",
            system_prompt="你是交付专家。",
            emoji="📦",
        ),
    ]


def three_agents() -> list[AgencyAgentDefinition]:
    return [
        *agents(),
        AgencyAgentDefinition(
            id="agent-gamma",
            path="agent-gamma",
            name="增长专家",
            department="增长部",
            description="负责增长策略",
            system_prompt="你是增长专家。",
            emoji="📈",
        ),
    ]


def test_expert_adapter_uses_real_id_and_bounds_prompt() -> None:
    adapted = adapt_expert_catalog(
        [
            {
                "id": "real-agent-id",
                "name": "专家",
                "department": "工程部",
                "expertise": "完成工程任务",
                "prompt": "P" * 8_000,
            }
        ]
    )
    assert adapted[0].path == "real-agent-id"
    assert adapted[0].id == "real-agent-id"
    assert len(adapted[0].system_prompt) == 2_048


def test_health_reports_pinned_protocol_and_fixed_argv() -> None:
    client = AgencyWorkerClient(timeout_seconds=10)
    health = run(client.health())
    assert health["status"] == "ok"
    assert health["protocol"] == AGENCY_BRIDGE_PROTOCOL
    assert health["upstream_revision"] == AGENCY_UPSTREAM_REVISION
    assert health["max_message_bytes"] == 2 * 1024 * 1024
    assert health["max_model_calls"] == 3
    assert client.argv[1] == "--max-old-space-size=256"
    assert len(client.argv) == 3


def test_asset_bridge_uses_only_host_owned_root_and_upstream_records(tmp_path) -> None:
    client = AgencyWorkerClient(asset_root=tmp_path, timeout_seconds=10)
    saved = run(
        client.assets(
            "save_team",
            {
                "team": {
                    "name": "发布阵容",
                    "description": "跨浏览器复用",
                    "roles": [
                        {"role": "agent-alpha", "name": "研究专家"},
                        {"role": "agent-beta", "name": "交付专家"},
                    ],
                }
            },
        )
    )
    assert saved["team"]["name"] == "发布阵容"
    run(
        client.assets(
            "save_template",
            {
                "template": {
                    "name": "发布任务",
                    "content": "请形成一份包含验收标准的发布计划。",
                    "note": "first",
                }
            },
        )
    )
    listed = run(client.assets("list"))
    assert listed["teams"][0]["roles"][0]["role"] == "agent-alpha"
    assert listed["templates"][0]["version_count"] == 1
    assert listed["garden"]
    assert (tmp_path / "teams").is_dir()
    assert (tmp_path / "prompts").is_dir()

    unavailable = AgencyWorkerClient(timeout_seconds=10)
    with pytest.raises(AgencyWorkerError) as exc_info:
        run(unavailable.assets("list"))
    assert exc_info.value.code == "agency_asset_store_unavailable"


def test_compose_runs_initial_role_repair_and_variable_repair_with_fake_gateway() -> None:
    responses = [
        """```yaml
name: 初稿
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: first
    role: zzzzzzzzzzzzzzzzzzzzzzzzzzz
    task: 先做调研
    output: research
```""",
        """```yaml
name: 二稿
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: 先做调研
    output: research_output
  - id: delivery
    role: agent-beta
    task: 基于 {{missing_output}} 交付结果
    output: final_output
```""",
        """```yaml
name: 最终稿
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: 先做调研
    output: research_output
  - id: delivery
    role: agent-beta
    depends_on: [research]
    task: 基于 {{research_output}} 交付结果
    acceptance: 结论必须可执行
    output: final_output
```""",
    ]
    requests = []

    async def fake_gateway(request):
        requests.append(request)
        return responses[len(requests) - 1]

    client = AgencyWorkerClient(model_runner=fake_gateway, timeout_seconds=20)
    result = run(
        client.compose(
            goal="为新产品制定研究与交付计划",
            model_id="fake-model",
            agents=agents(),
            mode="auto",
            max_agents=2,
            temperature=0.2,
        )
    )
    assert len(requests) == 3
    assert {request.max_tokens for request in requests} == {8192}
    assert result["model_calls"] == 3
    assert result["repair_used"] is True
    assert result["validation"]["valid"] is True
    assert result["selected_agent_ids"] == ["agent-alpha", "agent-beta"]
    assert "{{research_output}}" in result["yaml"]


def test_compose_uses_remaining_call_for_final_validation_repair() -> None:
    responses = [
        """```yaml
name: 重复输出初稿
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: 先做调研
    output: shared_output
  - id: delivery
    role: agent-beta
    depends_on: [research]
    task: 基于 {{shared_output}} 交付结果
    output: shared_output
```""",
        """```yaml
name: 已修复工作流
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: 先做调研
    output: research_output
  - id: delivery
    role: agent-beta
    depends_on: [research]
    task: 基于 {{research_output}} 交付结果
    acceptance: 结论必须可执行
    output: final_output
```""",
    ]
    requests = []

    async def fake_gateway(request):
        requests.append(request)
        return responses[len(requests) - 1]

    client = AgencyWorkerClient(model_runner=fake_gateway, timeout_seconds=20)
    result = run(
        client.compose(
            goal="为新产品制定研究与交付计划",
            model_id="fake-model",
            agents=agents(),
            mode="auto",
            max_agents=2,
            temperature=0.2,
        )
    )

    assert len(requests) == 2
    assert result["model_calls"] == 2
    assert result["repair_used"] is True
    assert result["validation"]["valid"] is True
    assert result["selected_agent_ids"] == ["agent-alpha", "agent-beta"]


def test_compose_repairs_auto_lineup_that_exceeds_max_agents() -> None:
    responses = [
        """```yaml
name: 超额初稿
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: 调研
    output: research_output
  - id: delivery
    role: agent-beta
    task: 交付
    output: delivery_output
  - id: growth
    role: agent-gamma
    depends_on: [research, delivery]
    task: 整合
    acceptance: 可执行
    output: final_output
```""",
        """```yaml
name: 限额修复稿
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: 调研
    output: research_output
  - id: delivery
    role: agent-beta
    depends_on: [research]
    task: 基于 {{research_output}} 交付
    acceptance: 可执行
    output: final_output
```""",
    ]
    requests = []

    async def fake_gateway(request):
        requests.append(request)
        return responses[len(requests) - 1]

    result = run(
        AgencyWorkerClient(model_runner=fake_gateway, timeout_seconds=20).compose(
            goal="为新产品制定研究与交付计划",
            model_id="fake-model",
            agents=three_agents(),
            mode="auto",
            max_agents=2,
            temperature=0.2,
        )
    )

    assert len(requests) == 2
    assert "at most 2 role ids" in requests[1].messages[0].content
    assert result["repair_used"] is True
    assert result["selected_agent_ids"] == ["agent-alpha", "agent-beta"]


def test_compose_repairs_top_level_inputs_into_self_contained_tasks() -> None:
    responses = [
        """```yaml
name: 需要输入的初稿
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
inputs:
  - name: product_description
    description: 产品描述
    required: true
steps:
  - id: research
    role: agent-alpha
    task: 分析 {{product_description}}
    output: research_output
```""",
        """```yaml
name: 自包含工作流
agents_dir: modelmirror-experts
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: 分析用户目标中描述的新产品，并整理证据
    acceptance: 至少列出三项证据
    output: research_output
```""",
    ]
    requests = []

    async def fake_gateway(request):
        requests.append(request)
        return responses[len(requests) - 1]

    client = AgencyWorkerClient(model_runner=fake_gateway, timeout_seconds=20)
    result = run(
        client.compose(
            goal="为新产品制定研究计划并整理证据",
            model_id="fake-model",
            agents=agents(),
            mode="auto",
            max_agents=2,
            temperature=0.2,
        )
    )

    assert len(requests) == 2
    assert "不要生成 inputs" in requests[0].messages[0].content
    assert "top-level workflow inputs" in requests[1].messages[1].content
    assert result["validation"]["valid"] is True
    assert result["repair_used"] is True
    assert "inputs:" not in result["yaml"]
    assert "product_description" not in result["yaml"]


def test_validate_rejects_top_level_inputs_without_model_call() -> None:
    client = AgencyWorkerClient(timeout_seconds=10)
    result = run(
        client.validate(
            yaml="""name: bad-inputs
llm:
  provider: modelmirror
  model: fake
inputs:
  - name: product_description
    description: 产品描述
    required: true
steps:
  - id: research
    role: agent-alpha
    task: 分析 {{product_description}}
    output: research_output
""",
            agents=agents(),
        )
    )

    assert result["valid"] is False
    assert any("top-level workflow inputs" in error for error in result["errors"])


def test_validate_requires_acceptance_on_final_step_without_model_call() -> None:
    client = AgencyWorkerClient(timeout_seconds=10)
    result = run(
        client.validate(
            yaml="""name: missing-acceptance
llm:
  provider: modelmirror
  model: fake
steps:
  - id: research
    role: agent-alpha
    task: 分析用户目标
    output: research_output
""",
            agents=agents(),
        )
    )

    assert result["valid"] is False
    assert any("must define non-empty acceptance" in error for error in result["errors"])


def test_pinned_mode_rejects_unknown_agent_before_model_call() -> None:
    calls = 0

    async def fake_gateway(_request):
        nonlocal calls
        calls += 1
        return "unused"

    client = AgencyWorkerClient(model_runner=fake_gateway, timeout_seconds=10)
    with pytest.raises(AgencyWorkerError) as exc_info:
        run(
            client.compose(
                goal="固定阵容",
                model_id="fake-model",
                agents=agents(),
                mode="pinned",
                pinned_agent_ids=["agent-alpha", "missing"],
            )
        )
    assert exc_info.value.code == "unknown_agent"
    assert calls == 0


def test_validate_reports_unknown_agent_without_model_call() -> None:
    client = AgencyWorkerClient(timeout_seconds=10)
    result = run(
        client.validate(
            yaml="""name: bad
llm:
  provider: modelmirror
  model: fake
steps:
  - id: bad
    role: missing-agent
    task: work
    output: out
""",
            agents=agents(),
        )
    )
    assert result["valid"] is False
    assert any("unknown ModelMirror agent" in error for error in result["errors"])


def write_worker(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "worker.mjs"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("process.stdout.write('{bad json}\\n')", "worker_invalid_json"),
        ("process.exit(7)", "worker_crashed"),
        (
            "process.stdout.write('x'.repeat(2 * 1024 * 1024 + 1) + '\\n')",
            "worker_message_too_large",
        ),
    ],
)
def test_worker_failures_have_stable_codes(tmp_path: Path, source: str, code: str) -> None:
    client = AgencyWorkerClient(
        worker_entry=write_worker(tmp_path, source), timeout_seconds=2
    )
    with pytest.raises(AgencyWorkerError) as exc_info:
        run(client.health())
    assert exc_info.value.code == code


def test_worker_timeout_interrupts_then_kills(tmp_path: Path) -> None:
    worker = write_worker(tmp_path, "setInterval(() => {}, 1000)")
    client = AgencyWorkerClient(worker_entry=worker, timeout_seconds=0.1)
    with pytest.raises(AgencyWorkerError) as exc_info:
        run(client.health())
    assert exc_info.value.code == "worker_timeout"


def test_stderr_is_isolated_and_environment_is_secret_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_GATEWAY_KEY", "gateway-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    worker = write_worker(
        tmp_path,
        """
let input = '';
for await (const chunk of process.stdin) { input += chunk; if (input.includes('\\n')) break; }
const request = JSON.parse(input.trim());
console.error('diagnostic-only');
process.stdout.write(JSON.stringify({
  protocol: 'mm-agency-bridge/v1', type: 'response', id: request.id, ok: true,
  result: {
    gateway: process.env.LLM_GATEWAY_KEY ?? null,
    openrouter: process.env.OPENROUTER_API_KEY ?? null,
    worker: process.env.MM_AGENCY_WORKER
  }
}) + '\\n');
""",
    )
    client = AgencyWorkerClient(worker_entry=worker, timeout_seconds=2)
    result = run(client.health())
    assert result == {"gateway": None, "openrouter": None, "worker": "1"}
    assert "LLM_GATEWAY_KEY" not in client.sanitized_environment(os.environ)
    assert "OPENROUTER_API_KEY" not in client.sanitized_environment(os.environ)


def test_oversized_request_is_rejected_before_spawn() -> None:
    client = AgencyWorkerClient(timeout_seconds=2)
    with pytest.raises(AgencyWorkerError) as exc_info:
        run(client.call("validate", {"yaml": "x" * (2 * 1024 * 1024), "agents": []}))
    assert exc_info.value.code == "worker_request_too_large"


def test_main_model_adapter_reuses_collect_chat_completion_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import server.main as main_module

    observed = {}

    async def fake_collect(model_id, messages, **kwargs):
        observed["model_id"] = model_id
        observed["messages"] = messages
        observed["reasoning"] = kwargs.get("reasoning")
        kwargs["usage_observer"]({"prompt_tokens": 12, "completion_tokens": 7})
        kwargs["completion_metadata_observer"]({"finish_reason": "stop"})
        return "gateway-result"

    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = run(
        main_module.collect_agency_worker_model(
            AgencyModelRequest(
                request_id="model-1",
                model_id="gateway-model",
                messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "user"},
                ],
                temperature=0.2,
                max_tokens=4096,
            )
        )
    )
    assert observed["model_id"] == "gateway-model"
    assert [message.role for message in observed["messages"]] == ["system", "user"]
    assert observed["reasoning"] == {"effort": "low"}
    assert response.content == "gateway-result"
    assert response.usage == {"input_tokens": 12, "output_tokens": 7}
    assert response.finish_reason == "stop"


def test_main_model_adapter_classifies_empty_truncated_response(monkeypatch) -> None:
    import server.main as main_module

    async def fake_collect(_model_id, _messages, **kwargs):
        kwargs["completion_metadata_observer"]({"finish_reason": "length"})
        raise RuntimeError("模型没有返回可用内容。")

    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    with pytest.raises(main_module.AgencyWorkerError) as exc_info:
        run(
            main_module.collect_agency_worker_model(
                AgencyModelRequest(
                    request_id="model-truncated",
                    model_id="gateway-model",
                    messages=[{"role": "user", "content": "user"}],
                    temperature=0.3,
                    max_tokens=4096,
                )
            )
        )
    assert exc_info.value.code == "model_output_truncated"
    assert "token 上限" in str(exc_info.value)


def test_main_model_adapter_enables_reasoning_fallback_for_json_review(
    monkeypatch,
) -> None:
    import server.main as main_module

    observed = {}

    async def fake_collect(_model_id, _messages, **kwargs):
        observed.update(kwargs)
        kwargs["usage_observer"]({"prompt_tokens": 9, "completion_tokens": 3})
        kwargs["completion_metadata_observer"]({"finish_reason": "stop"})
        return '{"pass":false,"failed":[{"criterion":"owner","why":"TBD"}]}'

    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    response = run(
        main_module.collect_agency_worker_model(
            AgencyModelRequest(
                request_id="model-json-review",
                model_id="gateway-model",
                messages=[{"role": "user", "content": "review"}],
                temperature=0,
                max_tokens=800,
                json_response=True,
            )
        )
    )
    assert observed["allow_json_reasoning_fallback"] is True
    assert observed["json_required_top_level_key"] == "pass"
    assert observed["response_format"] == {"type": "json_object"}
    assert response.content.startswith('{"pass":false')
    assert response.usage == {"input_tokens": 9, "output_tokens": 3}


def test_main_model_adapter_classifies_gateway_quota_without_leaking_body(monkeypatch) -> None:
    import server.main as main_module

    async def fake_collect(_model_id, _messages, **_kwargs):
        raise RuntimeError("OpenRouter 额度不足，请充值或更换可用连接后重试。 secret-body")

    monkeypatch.setattr(main_module, "collect_chat_completion_text", fake_collect)
    with pytest.raises(main_module.AgencyWorkerError) as exc_info:
        run(
            main_module.collect_agency_worker_model(
                AgencyModelRequest(
                    request_id="model-quota",
                    model_id="gateway-model",
                    messages=[{"role": "user", "content": "user"}],
                    temperature=0.3,
                    max_tokens=4096,
                )
            )
        )
    assert exc_info.value.code == "model_gateway_quota_exceeded"
    assert "充值或更换" in str(exc_info.value)
    assert "secret-body" not in str(exc_info.value)
