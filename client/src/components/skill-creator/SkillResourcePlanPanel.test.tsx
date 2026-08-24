import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  SkillCreatorSession,
  SkillCreatorStatus,
  SkillResourcePlan,
} from "../../utils/skillCreatorApi";
import SkillResourcePlanPanel from "./SkillResourcePlanPanel";

function jsonResponse(payload: unknown, responseStatus = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status: responseStatus,
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
  output_contract: ["- Return a factual incident report."],
  failure_modes: ["• Mark missing facts as unconfirmed."],
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
  authoring_flow: "resource",
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

function Harness({ initial, currentStatus = status }: { initial: SkillCreatorSession; currentStatus?: SkillCreatorStatus }) {
  const [current, setCurrent] = useState(initial);
  return <SkillResourcePlanPanel onSession={setCurrent} session={current} status={currentStatus} />;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SkillResourcePlanPanel", () => {
  it("shows the output and failure contract before technical details are expanded", () => {
    render(<SkillResourcePlanPanel onSession={vi.fn()} session={session} status={status} />);

    expect(screen.getByRole("heading", { name: "交付结果" })).toBeVisible();
    expect(screen.getByText(/Return a factual incident report\./)).toBeVisible();
    expect(screen.getByRole("heading", { name: "遇到信息不足时" })).toBeVisible();
    expect(screen.getByText(/Mark missing facts as unconfirmed\./)).toBeVisible();
    expect(screen.getByText("共 4 步执行流程")).toBeVisible();
  });

  it("keeps plan confirmation disabled until a required trigger check passes", () => {
    render(<SkillResourcePlanPanel
      onSession={vi.fn()}
      session={{ ...session, trigger_required: true, trigger_stale_reason: "skill_trigger_suite_required" }}
      status={{
        ...status,
        trigger_optimization_enabled: true,
        trigger_optimizer_available: false,
        trigger_store_available: true,
      }}
    />);

    expect(screen.getByRole("button", { name: "先完成触发检查" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "先确认什么时候该使用这个 Skill" })).toBeVisible();
  });

  it("requires unsaved plan edits to be saved before trigger mutations are available", async () => {
    const triggerSession = {
      ...session,
      trigger_required: true,
      trigger_stale_reason: "skill_trigger_suite_required",
    };
    const savedPlan = {
      ...plan,
      revision: plan.revision + 1,
      digest: "9".repeat(64),
      skill_description: "Review incidents from evidence; do not use for ordinary summaries.",
    };
    const savedSession = {
      ...triggerSession,
      session_revision: triggerSession.session_revision + 1,
      resource_plan: savedPlan,
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: savedSession, resource_plan: savedPlan }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness
      currentStatus={{
        ...status,
        trigger_optimization_enabled: true,
        trigger_optimizer_available: true,
        trigger_store_available: true,
      }}
      initial={triggerSession}
    />);
    await userEvent.click(screen.getByRole("button", { name: "手工填写" }));
    const unsavedTriggerCase = screen.getByRole("textbox", { name: "应该触发用例 1" });
    await userEvent.type(unsavedTriggerCase, "，并列出证据缺口");
    await userEvent.click(screen.getByText("查看并调整完整方案（可选）"));
    const description = screen.getByRole("textbox", { name: "能力、触发场景与边界" });
    await userEvent.clear(description);
    await userEvent.type(description, savedPlan.skill_description);

    expect(screen.getByText("先保存方案调整")).toBeVisible();
    expect(screen.getByRole("heading", { name: "先确认什么时候该使用这个 Skill" })).toBeVisible();
    expect(screen.getByRole("button", { name: "AI 提出测试边界" })).toBeDisabled();
    expect(unsavedTriggerCase).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "保存我的调整" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/resource-plan$/);
    expect(await screen.findByRole("heading", { name: "先确认什么时候该使用这个 Skill" })).toBeVisible();
    expect(screen.getByRole("button", { name: "AI 提出测试边界" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "应该触发用例 1" })).toHaveValue("Turn this incident log into a review.，并列出证据缺口");
  });

  it("generates a plan without writing resource files", async () => {
    const emptySession = { ...session, resource_plan: null };
    const plannedSession = { ...session, resource_plan: { ...plan, resources: [] } };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: plannedSession, resource_plan: plannedSession.resource_plan }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSession = vi.fn();

    render(<SkillResourcePlanPanel onSession={onSession} session={emptySession} status={status} />);
    await userEvent.click(screen.getByRole("button", { name: "让 AI 生成方案" }));

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
    await userEvent.click(screen.getByRole("button", { name: "保存我的调整" }));

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
    await userEvent.click(screen.getByRole("button", { name: "保存我的调整" }));

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

  it("adds a structured Hook only after a script resource exists", async () => {
    const script = {
      resource_id: "resource_script",
      spec_digest: "d".repeat(64),
      kind: "script" as const,
      action: "keep" as const,
      generation_cost: "medium" as const,
      path: "scripts/check_release.py",
      purpose: "Validate release filenames deterministically.",
      source_ids: ["intent"],
      used_by_steps: ["render"],
      depends_on: [],
      acceptance_checks: ["Returns a typed result."],
    };
    const hookSession = {
      ...session,
      resource_plan: { ...plan, resources: [script], hooks: [] },
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse({ session: { ...hookSession, resource_plan: { ...hookSession.resource_plan, revision: 3 } } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillResourcePlanPanel onSession={vi.fn()} session={hookSession} status={status} />);
    await userEvent.click(screen.getByText("查看并调整完整方案（可选）"));
    await userEvent.click(screen.getByRole("button", { name: "添加 Hook" }));
    await userEvent.click(screen.getByRole("button", { name: "保存我的调整" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.hooks).toHaveLength(1);
    expect(body.hooks[0]).toMatchObject({
      event: "pre_tool_use",
      mode: "validation",
      tool_names: ["sandbox_write_file"],
      script_path: "scripts/check_release.py",
      action: "create",
    });
    expect(body.resources[0]).toMatchObject({
      path: "scripts/check_release.py",
      action: "update",
    });
  });

  it("moves forward only after the resource plan is confirmed successfully", async () => {
    const confirmedSession = {
      ...session,
      session_revision: 4,
      resource_plan: { ...plan, revision: 3, state: "confirmed" as const },
    };
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      session: confirmedSession,
      resource_plan: confirmedSession.resource_plan,
    })));
    const onSession = vi.fn();
    const onPlanConfirmed = vi.fn();

    render(<SkillResourcePlanPanel
      onPlanConfirmed={onPlanConfirmed}
      onSession={onSession}
      session={session}
      status={status}
    />);
    await userEvent.click(screen.getByRole("button", { name: "确认方案，进入生成" }));

    await waitFor(() => expect(onSession).toHaveBeenCalledWith(confirmedSession));
    expect(onPlanConfirmed).toHaveBeenCalledWith(confirmedSession);
  });

  it("does not move forward when plan confirmation fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      detail: { code: "skill_creator_revision_conflict", message: "Creator session changed." },
    }, 409)));
    const onPlanConfirmed = vi.fn();

    render(<SkillResourcePlanPanel
      onPlanConfirmed={onPlanConfirmed}
      onSession={vi.fn()}
      session={session}
      status={status}
    />);
    await userEvent.click(screen.getByRole("button", { name: "确认方案，进入生成" }));

    expect(await screen.findByText("会话或方案已更新，请重新加载页面后再继续。")).toBeVisible();
    expect(onPlanConfirmed).not.toHaveBeenCalled();
  });
});
