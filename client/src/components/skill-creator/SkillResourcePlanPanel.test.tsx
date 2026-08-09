import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  SkillCreatorSession,
  SkillCreatorStatus,
  SkillResourcePlan,
} from "../../utils/skillCreatorApi";
import SkillResourcePlanPanel from "./SkillResourcePlanPanel";

function jsonResponse(payload: unknown) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));
}

const status: SkillCreatorStatus = {
  enabled: true,
  version: "skill-creator-v2",
  model_available: true,
  assistant_agent_id: "skill-creator-assistant-v1",
  supported_sources: ["blank"],
  resource_authoring_enabled: true,
  resource_authoring_version: "resource-authoring-v2",
  resource_planner_available: true,
};

const plan: SkillResourcePlan = {
  plan_id: "resourceplan_1",
  session_id: "creator_1",
  revision: 2,
  digest: "b".repeat(64),
  state: "ready",
  session_revision: 3,
  draft_id: "draft_1",
  draft_revision: 1,
  draft_digest: "a".repeat(64),
  skill_name: "review-incidents",
  skill_description: "Review incidents when users need a factual timeline; do not invent root causes.",
  workflow_steps: [
    { step_id: "collect", instruction: "Collect the incident facts." },
    { step_id: "normalize", instruction: "Normalize the timeline." },
    { step_id: "review", instruction: "Review unsupported claims." },
    { step_id: "render", instruction: "Render the final report." },
  ],
  output_contract: ["Return a factual incident report."],
  failure_modes: ["Mark missing facts as unconfirmed."],
  resources: [{
    resource_id: "resource_existing",
    spec_digest: "c".repeat(64),
    kind: "reference",
    action: "update",
    generation_cost: "medium",
    path: "references/policy.md",
    purpose: "Keep the evidence policy separate from the main workflow.",
    source_ids: ["intent"],
    used_by_steps: ["review"],
    depends_on: [],
    acceptance_checks: ["Every claim is traceable."],
  }],
  clarifications: [],
  clarification_answers: {},
  created_at: 1,
  updated_at: 2,
};

const session: SkillCreatorSession = {
  session_id: "creator_1",
  session_revision: 3,
  draft_state_revision: 1,
  mode: "blank",
  assistant_agent_id: "skill-creator-assistant-v1",
  intent: "Create factual incident reviews.",
  positive_examples: ["Turn this incident log into a review."],
  near_miss_examples: ["Rewrite this sentence."],
  expected_output: "A factual report.",
  success_criteria: ["Do not invent facts."],
  selected_evidence: [],
  evidence_confirmed: true,
  state: "editing_draft",
  resource_plan: plan,
  created_at: 1,
  updated_at: 2,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SkillResourcePlanPanel", () => {
  it("generates a plan without writing resource files", async () => {
    const emptySession = { ...session, resource_plan: null };
    const plannedSession = { ...session, resource_plan: { ...plan, resources: [] } };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: plannedSession, resource_plan: plannedSession.resource_plan }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSession = vi.fn();

    render(<SkillResourcePlanPanel onSession={onSession} session={emptySession} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "生成资源计划" }));

    await waitFor(() => expect(onSession).toHaveBeenCalledWith(plannedSession));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url).endsWith("/resource-plan/generate")).toBe(true);
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_session_revision: 3,
      expected_plan_revision: null,
      expected_plan_digest: null,
    });
  });

  it("turns removal of an existing resource into an explicit delete action", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: { ...session, resource_plan: { ...plan, revision: 3 } } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourcePlanPanel onSession={vi.fn()} session={session} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "移除资源" }));
    await userEvent.click(screen.getByRole("button", { name: "保存计划修改" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.resources).toHaveLength(1);
    expect(body.resources[0]).toMatchObject({
      action: "delete",
      path: "references/policy.md",
    });
  });

  it("lets the user add a missing resource and keeps its kind and path aligned", async () => {
    const zeroResourceSession = {
      ...session,
      resource_plan: { ...plan, resources: [] },
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: { ...zeroResourceSession, resource_plan: { ...plan, revision: 3 } } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourcePlanPanel onSession={vi.fn()} session={zeroResourceSession} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "添加必要资源" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "资源 1 类型" }), "asset");
    await userEvent.click(screen.getByRole("button", { name: "保存计划修改" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.resources).toHaveLength(1);
    expect(body.resources[0]).toMatchObject({
      action: "create",
      kind: "asset",
      path: "assets/resource-1.md",
      source_ids: ["intent"],
      used_by_steps: ["collect"],
    });
  });
});
