import { useState } from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  SkillCreatorSession,
  SkillCreatorStatus,
  SkillResourcePlan,
  SkillTriggerDescriptionAttempt,
  SkillTriggerReceipt,
  SkillTriggerSuite,
} from "../../utils/skillCreatorApi";
import SkillTriggerOptimizationPanel, { skillTriggerGateReady } from "./SkillTriggerOptimizationPanel";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const plan: SkillResourcePlan = {
  plan_id: "resourceplan_1",
  session_id: "creator_1",
  revision: 2,
  digest: "b".repeat(64),
  state: "ready",
  session_revision: 3,
  skill_name: "review-incidents",
  skill_description: "整理事故事实并生成可追溯复盘，不用于普通文字润色。",
  workflow_steps: [{ step_id: "review", instruction: "Review evidence." }],
  output_contract: ["Return a factual report."],
  failure_modes: ["Mark unknown facts."],
  resources: [],
  clarifications: [],
  clarification_answers: {},
  created_at: 1,
  updated_at: 2,
};

const baseSession: SkillCreatorSession = {
  session_id: "creator_1",
  session_revision: 3,
  draft_state_revision: 1,
  authoring_flow: "resource",
  mode: "blank",
  assistant_agent_id: "skill-creator-assistant-v1",
  intent: "把事故记录整理成可追溯复盘。",
  positive_examples: ["根据部署日志整理事故复盘"],
  near_miss_examples: ["润色这段普通文字"],
  expected_output: "事实复盘",
  success_criteria: ["不编造原因"],
  selected_evidence: [],
  evidence_confirmed: true,
  state: "editing_draft",
  resource_plan: plan,
  trigger_required: true,
  trigger_stale_reason: "skill_trigger_suite_required",
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
  trigger_optimization_enabled: true,
  trigger_optimizer_available: false,
  trigger_store_available: true,
};

const suite: SkillTriggerSuite = {
  suite_id: "triggersuite_1",
  version: "skill-trigger-suite-v1",
  suite_revision: 2,
  suite_digest: "c".repeat(64),
  session_id: baseSession.session_id,
  session_revision: baseSession.session_revision,
  definition_digest: "d".repeat(64),
  skill_name: plan.skill_name,
  state: "confirmed",
  cases: [
    ["positive-1", "should_trigger", "根据部署日志整理事故复盘"],
    ["positive-2", "should_trigger", "从事件时间线生成根因待确认清单"],
    ["negative-1", "should_not_trigger", "润色这段普通文字"],
    ["negative-2", "should_not_trigger", "概括一篇新闻"],
  ].map(([case_id, kind, text]) => ({
    case_id,
    kind: kind as "should_trigger" | "should_not_trigger",
    text,
    source: "user" as const,
    case_hash: "e".repeat(64),
  })),
  change_reason: "用户确认边界",
  created_at: 3,
};

const attempt: SkillTriggerDescriptionAttempt = {
  attempt_id: "triggerattempt_1",
  version: "skill-trigger-description-attempt-v1",
  revision: 1,
  digest: "f".repeat(64),
  session_id: baseSession.session_id,
  session_revision: baseSession.session_revision,
  plan_id: plan.plan_id,
  plan_revision: plan.revision,
  plan_digest: plan.digest,
  suite_id: suite.suite_id,
  suite_revision: suite.suite_revision,
  suite_digest: suite.suite_digest,
  state: "evaluated",
  candidates: [{
    description: "用于根据事故日志生成可追溯复盘；普通润色或新闻摘要不应使用。",
    description_digest: "1".repeat(64),
    receipt_id: "triggerreceipt_1",
    passed: true,
    worst_positive_rank: 2,
    positive_rank_sum: 3,
    negative_safety_distance: 19,
  }],
  recommended_description_digest: "1".repeat(64),
  created_at: 4,
};

const receipt: SkillTriggerReceipt = {
  receipt_id: "triggerreceipt_1",
  version: "skill-trigger-receipt-v1",
  suite_id: suite.suite_id,
  suite_revision: suite.suite_revision,
  suite_digest: suite.suite_digest,
  session_id: baseSession.session_id,
  skill_name: plan.skill_name,
  description_digest: "1".repeat(64),
  ranker_version: "skill-need-local-v3",
  runtime_index_fingerprint: "2".repeat(64),
  directory_fingerprint: "3".repeat(64),
  trust_index_fingerprint: "4".repeat(64),
  candidate_fingerprint: "5".repeat(64),
  candidate_set_fingerprint: "6".repeat(64),
  passed: true,
  case_results: suite.cases.map((item) => ({
    case_id: item.case_id,
    case_hash: item.case_hash,
    kind: item.kind,
    passed: true,
    finder: {
      rank_top_6: item.kind === "should_trigger" ? 2 : null,
      rank_top_24: item.kind === "should_trigger" ? 2 : null,
      in_top_6: item.kind === "should_trigger",
      in_top_24: item.kind === "should_trigger",
      reasons: [{ reason_type: "term", origin: "description", matched_terms: ["事故", "复盘"] }],
      competitors: [{ candidate_id: "generic-summary", candidate_fingerprint: "7".repeat(64), rank: 1 }],
    },
    router: {
      rank_top_6: item.kind === "should_trigger" ? 1 : null,
      rank_top_24: item.kind === "should_trigger" ? 1 : null,
      in_top_6: item.kind === "should_trigger",
      in_top_24: item.kind === "should_trigger",
      reasons: [],
      competitors: [],
    },
  })),
  created_at: 5,
};

function Harness({ initial, currentStatus = status }: { initial: SkillCreatorSession; currentStatus?: SkillCreatorStatus }) {
  const [session, setSession] = useState(initial);
  return <SkillTriggerOptimizationPanel onSession={setSession} session={session} status={currentStatus} />;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SkillTriggerOptimizationPanel", () => {
  it("fails closed when a required trigger Store status is missing", () => {
    const confirmedSession = {
      ...baseSession,
      trigger_suite: suite,
      trigger_attempt: { ...attempt, state: "confirmed" as const },
      trigger_receipt: receipt,
      trigger_stale_reason: null,
    };

    expect(skillTriggerGateReady(confirmedSession, {
      ...status,
      trigger_store_available: undefined,
    })).toBe(false);
  });

  it("does not retroactively block a legacy session when the trigger Store is unavailable", () => {
    expect(skillTriggerGateReady({
      ...baseSession,
      trigger_required: false,
    }, {
      ...status,
      trigger_store_available: false,
    })).toBe(true);
  });

  it("labels an optional legacy session as not enabled instead of passed", () => {
    render(<Harness initial={{ ...baseSession, trigger_required: false }} />);

    expect(screen.getByText("尚未启用")).toBeVisible();
    expect(screen.queryByText("已通过")).not.toBeInTheDocument();
  });

  it("prevents no-op suite revisions but keeps the confirmed next action available", () => {
    render(<Harness initial={{
      ...baseSession,
      trigger_suite: { ...suite, state: "draft" },
    }} />);

    expect(screen.getByRole("button", { name: "保存测试边界" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "边界没问题，继续" })).toBeEnabled();
  });

  it("completes the no-key path from manual cases through local description confirmation", async () => {
    const draftSuite = { ...suite, state: "draft" as const };
    const manualInitial = {
      ...baseSession,
      positive_examples: ["根据部署日志整理事故复盘"],
      near_miss_examples: ["润色这段普通文字"],
    };
    const draftSession = { ...manualInitial, trigger_suite: draftSuite };
    const confirmedSuite = {
      ...suite,
      suite_revision: suite.suite_revision + 1,
      suite_digest: "9".repeat(64),
      state: "confirmed" as const,
    };
    const evaluatedAttempt = {
      ...attempt,
      suite_revision: confirmedSuite.suite_revision,
      suite_digest: confirmedSuite.suite_digest,
    };
    const evaluatedReceipt = {
      ...receipt,
      suite_revision: confirmedSuite.suite_revision,
      suite_digest: confirmedSuite.suite_digest,
    };
    const suiteConfirmedSession = {
      ...manualInitial,
      trigger_suite: confirmedSuite,
      trigger_stale_reason: "description_unconfirmed",
    };
    const evaluatedSession = {
      ...suiteConfirmedSession,
      trigger_attempt: evaluatedAttempt,
      trigger_receipt: evaluatedReceipt,
    };
    const confirmedAttempt = {
      ...evaluatedAttempt,
      revision: 2,
      state: "confirmed" as const,
      selected_description_digest: evaluatedAttempt.candidates[0].description_digest,
    };
    const completedSession = {
      ...evaluatedSession,
      resource_plan: {
        ...plan,
        revision: 3,
        digest: "8".repeat(64),
        skill_description: evaluatedAttempt.candidates[0].description,
      },
      trigger_attempt: confirmedAttempt,
      trigger_stale_reason: null,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/trigger-suite") && init?.method === "PATCH") {
        return jsonResponse({ session: draftSession, resource_plan: plan, trigger_required: true, trigger_suite: draftSuite, trigger_stale_reason: "skill_trigger_suite_required" });
      }
      if (url.endsWith("/trigger-suite/confirm")) {
        return jsonResponse({ session: suiteConfirmedSession, resource_plan: plan, trigger_required: true, trigger_suite: confirmedSuite, trigger_stale_reason: "description_unconfirmed" });
      }
      if (url.endsWith("/trigger-descriptions/evaluate")) {
        return jsonResponse({ session: evaluatedSession, resource_plan: plan, trigger_required: true, trigger_suite: confirmedSuite, trigger_attempt: evaluatedAttempt, trigger_receipt: evaluatedReceipt, trigger_stale_reason: "description_unconfirmed" });
      }
      return jsonResponse({ session: completedSession, resource_plan: completedSession.resource_plan, trigger_required: true, trigger_suite: confirmedSuite, trigger_attempt: confirmedAttempt, trigger_receipt: evaluatedReceipt, trigger_stale_reason: null });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness initial={manualInitial} />);
    await userEvent.click(screen.getByRole("button", { name: "手工填写" }));
    const positiveGroup = screen.getByRole("group", { name: /应该触发/ });
    const negativeGroup = screen.getByRole("group", { name: /不该触发/ });
    await userEvent.click(within(positiveGroup).getByRole("button", { name: "再加一条" }));
    await userEvent.type(screen.getByRole("textbox", { name: "应该触发用例 2" }), "根据告警与变更记录复盘服务中断");
    await userEvent.click(within(negativeGroup).getByRole("button", { name: "再加一条" }));
    await userEvent.type(screen.getByRole("textbox", { name: "不该触发用例 2" }), "总结一篇行业新闻");
    expect(screen.getByRole("button", { name: "保存测试边界" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "保存测试边界" }));
    await userEvent.click(await screen.findByRole("button", { name: "边界没问题，继续" }));
    await userEvent.click(await screen.findByRole("button", { name: "验证这条描述" }));
    await userEvent.click(await screen.findByRole("button", { name: "采用第 1 条描述" }));

    expect(await screen.findByText("触发检查通过")).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.cases).toEqual(expect.arrayContaining([
      { kind: "should_trigger", text: "根据告警与变更记录复盘服务中断" },
      { kind: "should_not_trigger", text: "总结一篇行业新闻" },
    ]));
    expect(body.change_reason).toContain("手工定义");
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual(expect.arrayContaining([
      expect.stringMatching(/\/trigger-suite$/),
      expect.stringMatching(/\/trigger-suite\/confirm$/),
      expect.stringMatching(/\/trigger-descriptions\/evaluate$/),
      expect.stringMatching(/\/trigger-descriptions\/triggerattempt_1\/confirm$/),
    ]));
    const confirmSuiteCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/trigger-suite/confirm"));
    expect(JSON.parse(String(confirmSuiteCall?.[1]?.body))).toMatchObject({
      expected_suite_revision: draftSuite.suite_revision,
      expected_suite_digest: draftSuite.suite_digest,
    });
    const evaluateCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/trigger-descriptions/evaluate"));
    expect(JSON.parse(String(evaluateCall?.[1]?.body))).toMatchObject({
      expected_suite_revision: confirmedSuite.suite_revision,
      expected_suite_digest: confirmedSuite.suite_digest,
    });
  });

  it("locks suite inputs while a save is in flight", async () => {
    let resolveSave!: (response: Response) => void;
    const pendingSave = new Promise<Response>((resolve) => { resolveSave = resolve; });
    vi.stubGlobal("fetch", vi.fn(() => pendingSave));
    const draftSuite = { ...suite, state: "draft" as const };
    render(<Harness initial={{ ...baseSession, trigger_suite: draftSuite }} />);

    const firstCase = screen.getByRole("textbox", { name: "应该触发用例 1" });
    await userEvent.type(firstCase, "，并标记证据缺口");
    await userEvent.click(screen.getByRole("button", { name: "保存测试边界" }));

    expect(screen.getByRole("button", { name: "正在保存…" })).toBeDisabled();
    expect(firstCase).toBeDisabled();
    for (const button of screen.getAllByRole("button", { name: "再加一条" })) {
      expect(button).toBeDisabled();
    }
    resolveSave(await jsonResponse({
      session: { ...baseSession, trigger_suite: draftSuite },
      resource_plan: plan,
      trigger_required: true,
      trigger_suite: draftSuite,
      trigger_stale_reason: "skill_trigger_suite_required",
    }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "正在保存…" })).not.toBeInTheDocument());
  });

  it("shows one recommendation, diagnostics on demand, and adopts only a passing description", async () => {
    const evaluated = {
      ...baseSession,
      trigger_suite: suite,
      trigger_attempt: attempt,
      trigger_receipt: receipt,
      trigger_stale_reason: "description_unconfirmed",
    };
    const confirmedAttempt = { ...attempt, revision: 2, state: "confirmed" as const, selected_description_digest: attempt.candidates[0].description_digest };
    const confirmed = {
      ...evaluated,
      resource_plan: { ...plan, revision: 3, digest: "8".repeat(64), skill_description: attempt.candidates[0].description },
      trigger_attempt: confirmedAttempt,
      trigger_stale_reason: null,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/trigger-descriptions/optimize")) {
        return jsonResponse({ session: evaluated, resource_plan: plan, trigger_required: true, trigger_suite: suite, trigger_attempt: attempt, trigger_receipt: receipt, trigger_stale_reason: "description_unconfirmed" });
      }
      return jsonResponse({ session: confirmed, resource_plan: confirmed.resource_plan, trigger_required: true, trigger_suite: suite, trigger_attempt: confirmedAttempt, trigger_receipt: receipt, trigger_stale_reason: null });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness currentStatus={{ ...status, trigger_optimizer_available: true }} initial={{ ...baseSession, trigger_suite: suite, trigger_stale_reason: "description_unconfirmed" }} />);
    await userEvent.click(screen.getByRole("button", { name: "AI 优化并实测描述" }));

    expect(await screen.findByText(attempt.candidates[0].description)).toBeVisible();
    expect(screen.getByText("推荐 · 通过")).toBeVisible();
    const diagnosticSummary = screen.getByText("查看排名与竞争候选（诊断）");
    expect(diagnosticSummary.closest("details")).not.toHaveAttribute("open");
    await userEvent.click(diagnosticSummary);
    expect(diagnosticSummary.closest("details")).toHaveAttribute("open");
    expect(screen.getAllByText(/主要竞争：generic-summary/)[0]).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "采用第 1 条描述" }));

    expect(await screen.findByText("触发检查通过")).toBeVisible();
    expect(screen.getByText("应该触发：2/2 命中 · 不该触发：2/2 避开")).toBeVisible();
    const confirmCall = fetchMock.mock.calls.find(([input]) => String(input).includes(`/trigger-descriptions/${attempt.attempt_id}/confirm`));
    expect(confirmCall).toBeTruthy();
    expect(JSON.parse(String(confirmCall?.[1]?.body))).toMatchObject({
      expected_attempt_revision: 1,
      expected_attempt_digest: attempt.digest,
      selected_description_digest: attempt.candidates[0].description_digest,
    });
  });

  it("offers AI retry and a manual fallback when every description fails the hard gate", async () => {
    const failedAttempt = {
      ...attempt,
      candidates: attempt.candidates.map((candidate) => ({
        ...candidate,
        passed: false,
        worst_positive_rank: 25,
        negative_safety_distance: 2,
      })),
      recommended_description_digest: null,
    };
    const failedSession = {
      ...baseSession,
      trigger_suite: suite,
      trigger_attempt: failedAttempt,
      trigger_receipt: { ...receipt, passed: false },
      trigger_stale_reason: "description_unconfirmed",
    };
    const retriedAttempt = {
      ...attempt,
      attempt_id: "triggerattempt_retry",
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({
      session: { ...failedSession, trigger_attempt: retriedAttempt },
      resource_plan: plan,
      trigger_required: true,
      trigger_suite: suite,
      trigger_attempt: retriedAttempt,
      trigger_receipt: receipt,
      trigger_stale_reason: "description_unconfirmed",
    }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness currentStatus={{ ...status, trigger_optimizer_available: true }} initial={failedSession} />);

    expect(screen.queryByRole("button", { name: /采用第 \d+ 条描述/ })).not.toBeInTheDocument();
    expect(screen.getByText("1 条候选均未通过")).toBeVisible();
    expect(screen.getByText("最弱正例 未进入 Top 24 · 最接近反例 第 2 名")).toBeVisible();
    expect(screen.getByText(/没有同时命中全部正例并避开全部反例/)).toBeVisible();
    expect(screen.queryByText(/第 25 名/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新运行 AI 优化" })).toBeVisible();
    expect(screen.getByRole("button", { name: "手工改写描述" })).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "重新运行 AI 优化" }));

    expect(await screen.findByText(retriedAttempt.candidates[0].description)).toBeVisible();
    expect(screen.getByRole("button", { name: "采用第 1 条描述" })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/trigger-descriptions\/optimize$/);
  });

  it("translates conflicts into a reload action instead of exposing backend English", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      detail: { code: "skill_creator_revision_conflict", message: "Creator session changed." },
    }, 409)));

    render(<Harness currentStatus={{ ...status, trigger_optimizer_available: true }} initial={baseSession} />);
    await userEvent.click(screen.getByRole("button", { name: "AI 提出测试边界" }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/请先复制未保存内容，再重新加载继续/)).toBeVisible();
    expect(alert).not.toHaveTextContent("Creator session changed");

    await userEvent.click(screen.getByRole("button", { name: "手工填写" }));
    await userEvent.click(screen.getByRole("button", { name: "保存测试边界" }));
    expect(await screen.findByText(/都至少需要 2 条非空用例/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "重新加载会话" })).not.toBeInTheDocument();
  });

  it("reloads the latest server session after a revision conflict", async () => {
    const latest = {
      ...baseSession,
      session_revision: 4,
      positive_examples: ["使用最新部署记录生成复盘"],
      near_miss_examples: ["只润色一句普通文字"],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (String(input).endsWith(`/sessions/${baseSession.session_id}`)) {
        return jsonResponse({
          session: latest,
          resource_plan: plan,
          trigger_required: true,
          trigger_stale_reason: "skill_trigger_suite_required",
        });
      }
      return jsonResponse({
        detail: { code: "skill_creator_revision_conflict", message: "Creator session changed." },
      }, 409);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Harness currentStatus={{ ...status, trigger_optimizer_available: true }} initial={baseSession} />);
    await userEvent.click(screen.getByRole("button", { name: "AI 提出测试边界" }));
    await userEvent.click(await screen.findByRole("button", { name: "重新加载会话" }));

    expect(await screen.findByText("已加载服务端最新状态。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "重新加载会话" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "手工填写" }));
    expect(screen.getByRole("textbox", { name: "应该触发用例 1" })).toHaveValue("使用最新部署记录生成复盘");
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith(`/sessions/${baseSession.session_id}`))).toBe(true);
  });

  it("uses Chinese recovery copy for a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));

    render(<Harness currentStatus={{ ...status, trigger_optimizer_available: true }} initial={baseSession} />);
    await userEvent.click(screen.getByRole("button", { name: "AI 提出测试边界" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("触发检查失败，请重试");
    expect(alert).not.toHaveTextContent("Failed to fetch");
  });

  it("identifies an invalid suite response as a test-boundary failure", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      detail: {
        code: "skill_trigger_optimizer_invalid",
        message: "Creator trigger optimizer did not return valid JSON.",
      },
    }, 502)));

    render(<Harness currentStatus={{ ...status, trigger_optimizer_available: true }} initial={baseSession} />);
    await userEvent.click(screen.getByRole("button", { name: "AI 提出测试边界" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("AI 没有返回可用的测试边界");
    expect(alert).toHaveTextContent("手工填写正反例");
    expect(alert).not.toHaveTextContent("触发描述");
    expect(alert).not.toHaveTextContent("did not return valid JSON");
  });

  it("offers a deterministic reload path when the local trigger index is unavailable", async () => {
    const unavailable = {
      ...baseSession,
      trigger_suite: suite,
      trigger_attempt: {
        ...attempt,
        revision: 2,
        state: "confirmed" as const,
        selected_description_digest: attempt.candidates[0].description_digest,
      },
      trigger_receipt: null,
      trigger_stale_reason: "skill_trigger_index_unavailable",
    };
    const restored = {
      ...unavailable,
      trigger_receipt: receipt,
      trigger_stale_reason: null,
    };
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      session: restored,
      resource_plan: plan,
      trigger_required: true,
      trigger_suite: suite,
      trigger_attempt: restored.trigger_attempt,
      trigger_receipt: receipt,
      trigger_stale_reason: null,
    })));

    render(<Harness initial={unavailable} />);

    expect(screen.getByText("本地 Skill 索引暂不可用")).toBeVisible();
    expect(screen.queryByRole("button", { name: "AI 优化并实测描述" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新加载会话" }));

    expect(await screen.findByText("触发检查通过")).toBeVisible();
  });

  it("disables both assisted and manual entry when the trigger Store is unavailable", () => {
    render(<Harness currentStatus={{
      ...status,
      trigger_optimizer_available: true,
      trigger_store_available: false,
    }} initial={baseSession} />);

    expect(screen.getByText(/触发验证 Store 暂不可用/)).toBeVisible();
    expect(screen.getByRole("button", { name: "AI 提出测试边界" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "手工填写" })).toBeDisabled();
  });
});
