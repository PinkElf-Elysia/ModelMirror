import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  SkillCreatorDraft,
  SkillCreatorSession,
  SkillEvaluationCase,
  SkillEvaluationRun,
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
  vi.unstubAllGlobals();
});

describe("Skill Creator evaluation flow", () => {
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
    await userEvent.click(screen.getByRole("button", { name: "开始对照评测" }));

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

    expect(screen.getAllByText("Sandbox sidecar 不可用，评测已失败关闭。").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "接受当前评测" })).toBeDisabled();
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

    const accept = screen.getByRole("button", { name: "接受当前评测" });
    expect(accept).toBeEnabled();
    await userEvent.click(accept);

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toMatchObject({
      decision: "accept",
      expected_review_revision: 0,
      expected_digest: draft.content_digest,
    });
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

    await userEvent.type(screen.getByLabelText("反馈与判断依据"), "第二个用例缺少失败处理");
    await userEvent.click(screen.getByRole("button", { name: "需要修改" }));

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
});
