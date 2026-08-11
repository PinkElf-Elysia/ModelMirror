"""Manual smoke for the final service image's compiled Agency Worker.

Run by mounting this file into ``modelmirror-server:<tag>``. It deliberately
imports the package and Worker artifact already copied into the image.
"""

from __future__ import annotations

import asyncio

from orchestration_worker import AgencyAgentDefinition, AgencyWorkerClient


RESPONSES = [
    """name: initial
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: first
    role: missing-agent
    task: Draft the result
    output: first_output
""",
    """name: repaired-role
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: Research the goal
    output: research_output
  - id: delivery
    role: agent-beta
    task: Deliver from {{missing_output}}
    output: final_output
""",
    """name: repaired-variables
llm:
  provider: modelmirror
  model: fake-model
steps:
  - id: research
    role: agent-alpha
    task: Research the goal
    output: research_output
  - id: delivery
    role: agent-beta
    depends_on: [research]
    task: Deliver from {{research_output}}
    acceptance: The conclusion is actionable
    output: final_output
""",
]


async def main() -> None:
    calls = 0

    async def fake_gateway(_request):
        nonlocal calls
        response = RESPONSES[calls]
        calls += 1
        return response

    agents = [
        AgencyAgentDefinition(
            id="agent-alpha",
            path="agent-alpha",
            name="Researcher",
            department="Research",
            description="Collects and evaluates evidence.",
            system_prompt="You are a research specialist.",
        ),
        AgencyAgentDefinition(
            id="agent-beta",
            path="agent-beta",
            name="Delivery lead",
            department="Product",
            description="Synthesizes an actionable delivery.",
            system_prompt="You are a delivery specialist.",
        ),
    ]
    client = AgencyWorkerClient(model_runner=fake_gateway, timeout_seconds=20)
    health = await client.health()
    result = await client.compose(
        goal="Research and deliver a product recommendation",
        model_id="fake-model",
        agents=agents,
        mode="auto",
        max_agents=2,
    )
    assert health["status"] == "ok"
    assert calls == 3
    assert result["validation"]["valid"] is True
    assert result["repair_used"] is True
    assert result["selected_agent_ids"] == ["agent-alpha", "agent-beta"]
    print("agency worker docker smoke: ok")


asyncio.run(main())
