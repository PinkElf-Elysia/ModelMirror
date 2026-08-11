import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  SkillCreatorSession,
  SkillCreatorStatus,
  SkillResourceBuild,
  SkillResourcePlan,
} from "../../utils/skillCreatorApi";
import SkillResourceBuildPanel from "./SkillResourceBuildPanel";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const plan: SkillResourcePlan = {
  plan_id: "plan_1",
  session_id: "creator_1",
  revision: 2,
  digest: "b".repeat(64),
  state: "confirmed",
  session_revision: 3,
  skill_name: "review-incidents",
  skill_description: "Create incident reviews when users need a factual timeline.",
  workflow_steps: [{ step_id: "collect", instruction: "Collect facts." }],
  output_contract: ["Return Markdown."],
  failure_modes: ["Mark missing facts."],
  resources: [],
  clarifications: [],
  clarification_answers: {},
  created_at: 1,
  updated_at: 2,
};

const status: SkillCreatorStatus = {
  enabled: true,
  version: "skill-creator-v2",
  model_available: true,
  assistant_agent_id: "skill-creator-assistant-v1",
  supported_sources: ["blank"],
  resource_authoring_enabled: true,
  resource_planner_available: true,
  resource_build_enabled: true,
  resource_builder_available: true,
  script_sandbox_configured: true,
};

const baseSession: SkillCreatorSession = {
  session_id: "creator_1",
  session_revision: 3,
  draft_state_revision: 0,
  mode: "blank",
  assistant_agent_id: "skill-creator-assistant-v1",
  authoring_flow: "resource",
  intent: "Create factual incident reviews.",
  positive_examples: ["Review this incident."],
  near_miss_examples: ["Rewrite this sentence."],
  expected_output: "Markdown report.",
  success_criteria: ["Do not invent facts."],
  selected_evidence: [],
  evidence_confirmed: true,
  state: "selecting_evidence",
  resource_plan: plan,
  created_at: 1,
  updated_at: 2,
};

const build: SkillResourceBuild = {
  build_id: "build_1",
  session_id: "creator_1",
  revision: 5,
  digest: "c".repeat(64),
  state: "awaiting_review",
  phase: "resources",
  session_revision: 3,
  plan_id: plan.plan_id,
  plan_revision: plan.revision,
  plan_digest: plan.digest,
  skill_name: plan.skill_name,
  skill_description: plan.skill_description,
  workflow_steps: plan.workflow_steps,
  output_contract: plan.output_contract,
  failure_modes: plan.failure_modes,
  resources: [{
    resource_id: "resource_1",
    spec_digest: "d".repeat(64),
    kind: "reference",
    action: "create",
    path: "references/evidence-policy.md",
    purpose: "Keep detailed evidence rules separate.",
    source_ids: ["intent"],
    used_by_steps: ["collect"],
    depends_on: [],
    acceptance_checks: ["Defines unsupported claims."],
    state: "awaiting_review",
    attempt: 1,
    repair_count: 0,
    chunks: ["# Evidence policy\n\nUse facts.\n"],
    content: "# Evidence policy\n\nUse facts.\n",
    content_digest: "e".repeat(64),
    script_tests: [],
    validation_issues: [],
    feedback: "",
  }],
  current_resource_id: "resource_1",
  skill_chunks: [],
  skill_attempt: 1,
  skill_repair_count: 0,
  skill_validation_issues: [],
  skill_feedback: "",
  requirement_coverage: [],
  created_at: 1,
  updated_at: 2,
};

afterEach(() => vi.unstubAllGlobals());

describe("SkillResourceBuildPanel", () => {
  it("starts a build only after the immutable plan is confirmed", async () => {
    const started = { ...build, state: "planned" as const, phase: "resources" as const };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((_input, _init) => jsonResponse({ resource_build: started }, 201));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel onProposal={vi.fn()} onSessionRefresh={vi.fn()} session={baseSession} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "创建资源构建" }));

    expect(await screen.findByRole("heading", { name: "资源构建工作台" })).toBeVisible();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url).endsWith("/resource-build")).toBe(true);
    expect(JSON.parse(String(init?.body))).toMatchObject({
      plan_id: plan.plan_id,
      expected_session_revision: baseSession.session_revision,
      expected_plan_revision: plan.revision,
      expected_plan_digest: plan.digest,
    });
  });

  it("saves a complete direct edit as a new server revision", async () => {
    const edited = {
      ...build,
      revision: 6,
      digest: "f".repeat(64),
      resources: [{
        ...build.resources[0],
        content: "# Evidence policy\n\nUse facts and mark gaps.\n",
      }],
    };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((_input, _init) => jsonResponse({ resource_build: edited }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel onProposal={vi.fn()} onSessionRefresh={vi.fn()} session={{ ...baseSession, resource_build: build }} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "直接编辑完整资源" }));
    const editor = screen.getByLabelText("编辑完整资源");
    await userEvent.clear(editor);
    await userEvent.type(editor, "# Evidence policy\n\nUse facts and mark gaps.\n");
    await userEvent.click(screen.getByRole("button", { name: "保存资源 revision" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url).endsWith("/resources/resource_1")).toBe(true);
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toMatchObject({
      expected_revision: build.revision,
      expected_digest: build.digest,
      content: "# Evidence policy\n\nUse facts and mark gaps.\n",
    });
    expect(await screen.findByText(/已保存 references\/evidence-policy.md 的新构建 revision/)).toBeVisible();
  });

  it("follows the server-frozen current resource after advancing", async () => {
    const nextResource = {
      ...build,
      revision: 6,
      digest: "9".repeat(64),
      current_resource_id: "resource_2",
      resources: [
        { ...build.resources[0], state: "accepted" as const },
        {
          ...build.resources[0],
          resource_id: "resource_2",
          path: "assets/report-template.md",
          kind: "asset" as const,
          state: "awaiting_review" as const,
          content: "# Report template\n",
          content_digest: "8".repeat(64),
        },
      ],
    };
    const fetchMock = vi.fn<
      (_input: RequestInfo | URL, _init?: RequestInit) => Promise<Response>
    >((_input, _init) => jsonResponse({ resource_build: nextResource }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourceBuildPanel onProposal={vi.fn()} onSessionRefresh={vi.fn()} session={{ ...baseSession, resource_build: { ...build, state: "planned" } }} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "生成下一个资源" }));

    expect(await screen.findByRole("heading", { name: "assets/report-template.md" })).toBeVisible();
    expect(screen.getByText("# Report template")).toBeVisible();
  });
});
