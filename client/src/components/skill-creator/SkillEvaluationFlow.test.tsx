import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  SkillCreatorDraft,
  SkillCreatorSession,
  SkillEvaluationCase,
  SkillEvaluationRun,
  SkillEvaluationSuite,
  SkillEvolutionPlan,
  SkillResourcePlan,
} from "../../utils/skillCreatorApi";
import SkillEvaluationDesigner from "./SkillEvaluationDesigner";
import SkillEvaluationReview from "./SkillEvaluationReview";
import SkillCreatorFinish from "./SkillCreatorFinish";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const evaluationCases: SkillEvaluationCase[] = [1, 2, 3].map((number) => ({
  case_id: `case-${number}`,
  name: `真实用例 ${number}`,
  prompt: `分析第 ${number} 份材料`,
  expected_behavior: "返回带页码的中文摘要",
  fixtures: [],
  assertions: [],
}));

const draft: SkillCreatorDraft = {
  draft_id: "draft-1",
  root_name: "compare-pdf",
  name: "compare-pdf",
  slug: "compare-pdf",
  description: "比较 PDF 并保留页码。",
  skill_markdown: "---\nname: compare-pdf\ndescription: 比较 PDF 并保留页码。\n---\n\n# Compare",
  files: {},
  status: "draft",
  revision: 2,
  content_revision: 2,
  content_digest: "a".repeat(64),
  quality_required: true,
  quality_status: "not_evaluated",
};

const session: SkillCreatorSession = {
  session_id: "creator-1",
  session_revision: 3,
  draft_state_revision: 2,
  mode: "blank",
  assistant_agent_id: "skill-creator-assistant-v1",
  intent: "整理 PDF 证据",
  positive_examples: evaluationCases.map((item) => item.prompt),
  near_miss_examples: ["只转换文本"],
  expected_output: "中文摘要",
  success_criteria: ["包含页码"],
  selected_evidence: [],
  draft_id: draft.draft_id,
  current_revision: draft.revision,
  current_digest: draft.content_digest,
  state: "designing_tests",
  quality_mode: "objective",
  cases_revision: 1,
  evaluation_cases: evaluationCases,
  review_state: "none",
  review_revision: 0,
  draft,
  created_at: 1,
  updated_at: 2,
};

const evaluationSuite: SkillEvaluationSuite = {
  suite_id: "suite-1",
  version: "skill-evaluation-suite-v2",
  suite_revision: 2,
  suite_digest: "b".repeat(64),
  session_id: session.session_id,
  draft_id: draft.draft_id,
  draft_revision: draft.revision,
  draft_digest: draft.content_digest,
  quality_mode: "objective",
  state: "confirmed",
  cases: evaluationCases.map((item, index) => ({
    ...item,
    role: (["normal", "ambiguous", "boundary"] as const)[index],
    source: "user",
    requirement_ids: [],
    required_resource_paths: [],
    workflow_step_ids: [],
  })),
};

function evaluationRun(candidateRead: boolean, errorCode?: string): SkillEvaluationRun {
  return {
    run_id: "eval-1",
    session_id: session.session_id,
    status: errorCode ? "failed" : "completed",
    revision: 2,
    frozen_digest: draft.content_digest,
    model_id: "gateway/default-text",
    repetitions: 1,
    cases: evaluationCases,
    review_revision: 0,
    items: evaluationCases.flatMap((evaluationCase) => ([
      {
        item_id: `${evaluationCase.case_id}-base`,
        pair_id: evaluationCase.case_id,
        case_id: evaluationCase.case_id,
        target: "baseline" as const,
        repetition: 1,
        status: errorCode ? "failed" as const : "completed" as const,
        output: "普通摘要",
        actual_model: "real-model-v1",
        skill_read: false,
        error_code: errorCode,
      },
      {
        item_id: `${evaluationCase.case_id}-candidate`,
        pair_id: evaluationCase.case_id,
        case_id: evaluationCase.case_id,
        target: "candidate" as const,
        repetition: 1,
        status: errorCode ? "failed" as const : "completed" as const,
        output: "带第 2 页证据的摘要",
        actual_model: "real-model-v1",
        skill_read: candidateRead,
        error_code: errorCode,
      },
    ])),
    error_code: errorCode,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Skill Creator evaluation flow", () => {
  it("can replace an unconfirmed generated suite instead of stranding it", async () => {
    const draftSuite = { ...evaluationSuite, state: "draft" as const };
    const suiteSession = { ...session, cases_revision: 0, evaluation_suite: draftSuite };
    const regenerated = {
      ...suiteSession,
      evaluation_suite: { ...draftSuite, suite_revision: 3, suite_digest: "c".repeat(64) },
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => (
      jsonResponse({ session: regenerated })
    ));
    vi.stubGlobal("fetch", fetchMock);
    const onSessionChange = vi.fn();

    render(
      <SkillEvaluationDesigner
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunStarted={vi.fn()}
        onSessionChange={onSessionChange}
        session={suiteSession}
        suiteEnabled
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "重新生成套件" }));

    expect(String(fetchMock.mock.calls[0][0])).toContain("/evaluation-suite/generate");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      expected_suite_revision: 2,
      expected_suite_digest: evaluationSuite.suite_digest,
    });
    expect(onSessionChange).toHaveBeenCalledWith(regenerated);
  });

  it("keeps objective evaluation at exactly three cases and starts a paired run", async () => {
    const run = evaluationRun(true);
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({
      session: { ...session, state: "reviewing_results", active_evaluation_run_id: run.run_id },
      draft,
      cases: evaluationCases,
      cases_revision: 1,
      evaluation_run: run,
    }));
    vi.stubGlobal("fetch", fetchMock);
    const started = vi.fn();

    render(
      <SkillEvaluationDesigner
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunStarted={started}
        onSessionChange={vi.fn()}
        session={session}
      />,
    );

    for (const number of [1, 2, 3]) {
      expect(screen.getByRole("heading", { name: `真实用例 ${number}` })).toBeVisible();
    }
    await userEvent.click(screen.getByRole("button", { name: "开始试用对比" }));

    expect(started).toHaveBeenCalledWith(run);
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      expected_session_revision: 3,
      expected_revision: 2,
      expected_digest: draft.content_digest,
      repetitions: 1,
    });
  });

  it("requires an explicit reason and confirmation before subjective waiver", async () => {
    const subjective = { ...session, session_revision: 4, quality_mode: "subjective" as const };
    const waived = { ...subjective, session_revision: 5, quality_status: "eval_waived" as const, review_state: "waived" as const };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/creator-1") && init?.method === "PATCH") return jsonResponse({ session: subjective });
      if (url.endsWith("/waive-evaluation") && init?.method === "POST") return jsonResponse({ session: waived });
      return jsonResponse({ detail: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SkillEvaluationDesigner
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunStarted={vi.fn()}
        onSessionChange={vi.fn()}
        session={session}
      />,
    );

    await userEvent.click(screen.getByRole("radio", { name: /主观创作任务/ }));
    const waiveButton = screen.getByRole("button", { name: "确认人工豁免" });
    expect(waiveButton).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/豁免原因/), "输出审美只能由人工进行最终判断");
    await userEvent.click(screen.getByRole("checkbox", { name: /我确认这是主观创作任务/ }));
    await userEvent.click(waiveButton);

    const waiverCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/waive-evaluation"));
    expect(JSON.parse(String(waiverCall?.[1]?.body))).toMatchObject({
      expected_session_revision: 4,
      reason: "输出审美只能由人工进行最终判断",
      confirmed: true,
    });
  });

  it("starts a confirmed suite with its frozen revision and projected call budget", async () => {
    const run = { ...evaluationRun(true), evaluation_suite_id: evaluationSuite.suite_id, evaluation_suite_revision: evaluationSuite.suite_revision, evaluation_suite_digest: evaluationSuite.suite_digest };
    const suiteSession: SkillCreatorSession = {
      ...session,
      evaluation_suite: evaluationSuite,
      regression_governance: {
        version: "skill-creator-regression-v1",
        enabled: true,
        max_items: 72,
        case_count: 3,
        target_count: 3,
        estimated_model_calls: 9,
        max_repetitions: 3,
        previous_revision: 1,
        previous_digest: "c".repeat(64),
        revisions: [],
        runs: [],
      },
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ session: suiteSession, draft, evaluation_suite: evaluationSuite, evaluation_run: run }));
    vi.stubGlobal("fetch", fetchMock);
    const started = vi.fn();

    render(<SkillEvaluationDesigner draft={draft} onError={vi.fn()} onNotice={vi.fn()} onRunStarted={started} onSessionChange={vi.fn()} session={suiteSession} />);

    expect(screen.getByText(/这次会运行约 9 次/)).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "开始试用对比" }));
    expect(started).toHaveBeenCalledWith(run);
    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({ evaluation_suite_revision: 2, evaluation_suite_digest: evaluationSuite.suite_digest });
  });

  it("fails closed when Candidate did not read the Skill or Sandbox is unavailable", () => {
    render(
      <SkillEvaluationReview
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunChange={vi.fn()}
        onSessionRefresh={vi.fn()}
        run={evaluationRun(false, "sandbox_unavailable")}
        session={session}
      />,
    );

    expect(screen.getAllByText("隔离运行环境暂不可用，本次试用已安全停止。").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "效果可以，继续" })).toBeDisabled();
  });

  it("fails closed on unresolved tool control and never presents unknown usage as zero", () => {
    const run = evaluationRun(true);
    run.items = run.items.map((item, index) => ({
      ...item,
      ...(index === 1 ? {
        status: "failed" as const,
        output: "",
        error_code: "skill_evaluation_unresolved_tool_call",
      } : {}),
      usage: index === 0 ? { total_tokens: 42 } : { estimated_tokens: 0 },
    }));

    render(
      <SkillEvaluationReview
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunChange={vi.fn()}
        onSessionRefresh={vi.fn()}
        run={run}
        session={session}
      />,
    );

    expect(screen.getAllByText("模型给出了工具指令但没有真正执行，本次结果已作废。").length).toBeGreaterThan(0);
    expect(screen.getAllByText("42 tokens").length).toBeGreaterThan(0);
    expect(screen.getAllByText("token 用量未提供").length).toBeGreaterThan(0);
    expect(screen.queryByText("0 tokens")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "效果可以，继续" })).toBeDisabled();
  });

  it("refreshes the session projection when polling first reaches a terminal run", async () => {
    vi.useFakeTimers();
    const completedRun = evaluationRun(true);
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({ version: 1, run: completedRun })));
    const onRunChange = vi.fn();
    const onSessionRefresh = vi.fn().mockResolvedValue(undefined);

    render(
      <SkillEvaluationReview
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunChange={onRunChange}
        onSessionRefresh={onSessionRefresh}
        run={{ ...completedRun, status: "running" }}
        session={session}
      />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });

    expect(onRunChange).toHaveBeenCalledWith(completedRun);
    expect(onSessionRefresh).toHaveBeenCalledTimes(1);
  });

  it("submits human acceptance only for a comparable completed run", async () => {
    const run = evaluationRun(true);
    const accepted = { ...run, revision: 3, review_state: "accepted" as const, review_revision: 1 };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ evaluation_run: accepted, session: { ...session, review_state: "accepted" }, draft }));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SkillEvaluationReview
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunChange={vi.fn()}
        onSessionRefresh={vi.fn()}
        run={run}
        session={session}
      />,
    );

    const accept = screen.getByRole("button", { name: "效果可以，继续" });
    expect(accept).toBeEnabled();
    await userEvent.click(accept);

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      decision: "accept",
      expected_review_revision: 0,
      expected_digest: draft.content_digest,
    });
  });

  it("requires item-level acknowledgement before accepting a three-side regression", async () => {
    const base = evaluationRun(true);
    const previousItems = evaluationCases.map((evaluationCase) => ({
      item_id: `${evaluationCase.case_id}-previous`,
      pair_id: evaluationCase.case_id,
      case_id: evaluationCase.case_id,
      target: "previous" as const,
      repetition: 1,
      status: "completed" as const,
      output: "带页码的上一版摘要",
      actual_model: "real-model-v1",
      skill_read: true,
    }));
    const regressionItemId = "case-2-candidate";
    const run: SkillEvaluationRun = {
      ...base,
      previous_overlay_id: "overlay-previous",
      items: [...base.items, ...previousItems],
      report: {
        eligible_for_accept: true,
        regression_item_ids: [regressionItemId],
        comparison_counts: { regressed: 1, improved: 0, flat: 2, inconclusive: 0 },
        pairs: evaluationCases.map((item, index) => ({
          pair_id: item.case_id,
          case_id: item.case_id,
          repetition: 1,
          classification: index === 1 ? "regressed" as const : "flat" as const,
          candidate_item_id: `${item.case_id}-candidate`,
          previous_item_id: `${item.case_id}-previous`,
        })),
      },
    };
    const accepted = { ...run, revision: 3, review_state: "accepted" as const };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({ evaluation_run: accepted, session: { ...session, review_state: "accepted" }, draft }));
    vi.stubGlobal("fetch", fetchMock);

    render(<SkillEvaluationReview draft={draft} onError={vi.fn()} onNotice={vi.fn()} onRunChange={vi.fn()} onSessionRefresh={vi.fn()} run={run} session={session} />);

    const accept = screen.getByRole("button", { name: "效果可以，继续" });
    expect(accept).toBeDisabled();
    await userEvent.type(screen.getByLabelText("你观察到了什么？"), "该退化是已知格式变化，仍保留事实完整性。 ");
    await userEvent.click(screen.getByRole("checkbox", { name: /真实用例 2/ }));
    expect(accept).toBeEnabled();
    await userEvent.click(accept);
    const body = JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body));
    expect(body.acknowledged_regression_item_ids).toEqual([regressionItemId]);
  });

  it("freezes saved feedback before submitting a revise decision", async () => {
    const run = evaluationRun(true);
    const withFeedback = { ...run, revision: 3, feedback: "第二个用例缺少失败处理", feedback_revision: 1 };
    const revised = { ...withFeedback, revision: 4, review_state: "revise" as const };
    const refreshedSession = { ...session, session_revision: 4, review_revision: 1 };
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") return jsonResponse({ evaluation_run: withFeedback, session: refreshedSession, draft });
      return jsonResponse({ evaluation_run: revised, session: { ...refreshedSession, review_state: "revise" }, draft });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <SkillEvaluationReview
        draft={draft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onRunChange={vi.fn()}
        onSessionRefresh={vi.fn()}
        run={run}
        session={session}
      />,
    );

    await userEvent.type(screen.getByLabelText("你观察到了什么？"), "第二个用例缺少失败处理");
    await userEvent.click(screen.getByRole("button", { name: "还要修改" }));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const feedbackBody = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(feedbackBody).toMatchObject({ expected_run_revision: 2, expected_review_revision: 0, feedback: "第二个用例缺少失败处理" });
    const reviewBody = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(reviewBody).toMatchObject({ expected_session_revision: 4, expected_run_revision: 3, expected_review_revision: 1, decision: "revise" });
    expect(reviewBody).not.toHaveProperty("feedback");
  });

  it("keeps installation as a separate confirmation after quality acceptance", async () => {
    const acceptedDraft: SkillCreatorDraft = { ...draft, quality_status: "accepted", install_state: "not_installed" };
    const acceptedSession: SkillCreatorSession = {
      ...session,
      quality_status: "accepted",
      review_state: "accepted",
      install_state: "not_installed",
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({
      draft: { ...acceptedDraft, install_state: "current" },
      installed: { skill_id: "workspace-compare-pdf" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <SkillCreatorFinish
        draft={acceptedDraft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onProposal={vi.fn()}
        onReload={vi.fn()}
        proposal={null}
        run={evaluationRun(true)}
        session={acceptedSession}
      />,
    );

    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认安装当前版本" }));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/skills/drafts/draft-1/install",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("turns a revise decision into a confirmed resource evolution plan", async () => {
    const resourcePlan: SkillResourcePlan = {
      plan_id: "resource-plan-1",
      session_id: session.session_id,
      revision: 2,
      digest: "c".repeat(64),
      state: "confirmed",
      session_revision: session.session_revision,
      draft_id: draft.draft_id,
      draft_revision: draft.revision,
      draft_digest: draft.content_digest,
      skill_name: draft.name,
      skill_description: draft.description,
      workflow_steps: [{ step_id: "step-1", instruction: "Read the reference." }],
      output_contract: ["Return a cited summary."],
      failure_modes: ["Ask for missing pages."],
      resources: [{
        resource_id: "reference-1",
        spec_digest: "d".repeat(64),
        kind: "reference",
        action: "keep",
        generation_cost: "low",
        path: "references/rules.md",
        purpose: "Keep citation rules.",
        source_ids: [],
        used_by_steps: ["step-1"],
        depends_on: [],
        acceptance_checks: ["Contains page citation rules."],
      }],
      clarifications: [],
      clarification_answers: {},
      created_at: 1,
      updated_at: 2,
    };
    const stateAdvancedDraft = { ...draft, revision: draft.revision + 1 };
    const plan: SkillEvolutionPlan = {
      plan_id: "evolution-plan-1",
      version: "skill-evolution-plan-v1",
      revision: 1,
      digest: "e".repeat(64),
      state: "ready",
      session_id: session.session_id,
      draft_id: draft.draft_id,
      draft_revision: stateAdvancedDraft.content_revision,
      draft_digest: draft.content_digest,
      evaluation_run_id: "eval-1",
      evaluation_run_revision: 2,
      review_revision: 1,
      suite_id: evaluationSuite.suite_id,
      suite_revision: evaluationSuite.suite_revision,
      suite_digest: evaluationSuite.suite_digest,
      diagnoses: [{
        case_id: "case-2",
        evidence_item_ids: ["case-2-candidate"],
        failure_types: ["assertion_failed"],
        requirement_ids: ["success_criterion:0"],
        resource_ids: ["reference-1"],
        sections: ["Workflow"],
        summary: "页码引用在歧义输入中丢失。",
      }],
      actions: [{
        action_id: "action-1",
        action: "update",
        resource_id: "reference-1",
        kind: "reference",
        path: "references/rules.md",
        purpose: "Clarify fallback citation behavior.",
        source_ids: [],
        used_by_steps: ["step-1"],
        depends_on: [],
        acceptance_checks: ["Ambiguous input retains page markers."],
        related_case_ids: ["case-2"],
        expected_improvement: "保留歧义输入中的页码。",
        non_regression_case_ids: ["case-1", "case-3"],
      }],
      expected_improvements: ["Fix case-2 without changing case-1."],
      acceptance_criteria: ["All confirmed suite cases complete."],
      non_goals: ["Do not change the output language."],
      overfitting_risks: ["Do not special-case the fixture wording."],
      clarifications: [],
      clarification_answers: {},
    };
    const confirmedPlan = { ...plan, revision: 2, state: "confirmed" as const };
    const revisedSession: SkillCreatorSession = {
      ...session,
      draft_state_revision: stateAdvancedDraft.revision,
      current_revision: stateAdvancedDraft.content_revision,
      draft: stateAdvancedDraft,
      review_state: "revise",
      review_revision: 2,
      review_feedback: "歧义输入丢失页码引用。",
      resource_plan: resourcePlan,
      evaluation_suite: evaluationSuite,
      regression_governance: {
        version: "skill-creator-regression-v1",
        enabled: true,
        max_items: 72,
        case_count: 3,
        target_count: 2,
        estimated_model_calls: 6,
        max_repetitions: 3,
        revisions: [],
        runs: [],
      },
    };
    const run = {
      ...evaluationRun(true),
      review_state: "revise" as const,
      review_revision: undefined,
      reviews: [{
        review_id: "review-1",
        review_revision: 1,
        decision: "revise" as const,
        reason: "歧义输入丢失页码引用。",
        feedback_revision: 1,
        feedback: "歧义输入丢失页码引用。",
        actor_kind: "local_console",
        acknowledge_failed_assertions: false,
        created_at: 3,
      }],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).endsWith("/evolution-plan/confirm")) {
        return jsonResponse({ evolution_plan: confirmedPlan, resource_plan: resourcePlan });
      }
      return jsonResponse({ evolution_plan: plan });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onReload = vi.fn();

    render(
      <SkillCreatorFinish
        draft={stateAdvancedDraft}
        onError={vi.fn()}
        onNotice={vi.fn()}
        onProposal={vi.fn()}
        onReload={onReload}
        proposal={null}
        run={run}
        session={revisedSession}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "生成改进方案" }));
    expect(await screen.findByText(/页码引用在歧义输入中丢失/)).toBeVisible();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      expected_draft_state_revision: stateAdvancedDraft.revision,
      expected_draft_revision: stateAdvancedDraft.content_revision,
      expected_draft_digest: stateAdvancedDraft.content_digest,
      expected_review_revision: 1,
    });
    await userEvent.click(screen.getByRole("button", { name: "确认方案并继续生成" }));
    expect(fetchMock).toHaveBeenLastCalledWith(
      `/api/skills/creator/sessions/${session.session_id}/evolution-plan/confirm`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(onReload).toHaveBeenCalledOnce();
  });
});
