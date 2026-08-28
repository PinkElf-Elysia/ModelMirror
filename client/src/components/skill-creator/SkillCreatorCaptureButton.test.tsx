import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { WorkflowRunEvent } from "../../types/workflow";
import type { XpertConversationMessage } from "../../types/xpert";
import SkillCreatorCaptureButton, {
  completedWorkflowCaptureSource,
  xpertMessageCaptureSource,
  type SkillCreatorCaptureSource,
} from "./SkillCreatorCaptureButton";
import {
  clearSkillExperienceApiCache,
  type SkillExperienceCandidate,
} from "../../utils/skillExperienceApi";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

function renderButton(source: SkillCreatorCaptureSource, enabled = true) {
  return render(
    <MemoryRouter initialEntries={["/origin"]}>
      <Routes>
        <Route
          element={<SkillCreatorCaptureButton enabled={enabled} source={source} />}
          path="/origin"
        />
        <Route element={<p>Creator destination</p>} path="/skills/create/:sessionId" />
      </Routes>
    </MemoryRouter>,
  );
}

function workflowCandidate(
  state: "captured" | "analyzing" | "awaiting_review" | "promotion_ready" | "promoted",
  revision: number,
): SkillExperienceCandidate {
  return {
    candidate_id: "experience_1",
    version: "skill-experience-candidate-v1",
    revision,
    digest: String(revision).repeat(64).slice(0, 64),
    state,
    source_kind: "workflow_classic",
    source_task_id: "task_2",
    source_run_id: "run_2",
    selected_evidence: revision > 1 ? [{
      evidence_id: "evidence_goal",
      kind: "intent_summary",
      title: "目标摘要",
      summary: "把发布检查做法沉淀为可复用流程",
      content_hash: "b".repeat(64),
    }] : [],
    analysis_attempt: state === "analyzing" ? {
      status: "running",
      executor_mode: "model",
      error_code: null,
    } : state === "awaiting_review" ? {
      status: "succeeded",
      executor_mode: "model",
      error_code: null,
    } : null,
    brief: state === "awaiting_review" ? {
      version: "distilled-skill-brief-v1",
      revision: 1,
      digest: "c".repeat(64),
      suggestion: "create",
      recommendation_reason: "这套检查步骤可在多个发布任务中复用。",
      no_skill_reason: null,
      intent: "发布前检查文件命名和扩展名",
      positive_examples: ["检查本次发布包", "核对静态资源扩展名"],
      negative_examples: ["写普通文件", "只列出目录"],
      expected_output: "输出问题清单和通过结论",
      success_criteria: ["指出不合规文件"],
      reusable_steps: ["读取发布清单", "检查命名"],
      failure_boundaries: ["缺少清单时停止"],
      resource_clues: [],
      overfitting_risk: "不要绑定某一次发布的文件名",
      source: "model",
      complete: true,
    } : null,
    overlaps: [],
    decision: state === "promotion_ready" || state === "promoted" ? {
      decision: "create",
      target_skill_id: null,
    } : null,
    promotion: state === "promoted" ? {
      session_id: "creator_from_experience",
      route: "/skills/create/creator_from_experience?step=2",
      decision: "create",
    } : null,
    updated_at: 1_700_000_000 + revision,
  };
}

const workflowPreview = {
  version: "creator-evidence-v1",
  source_kind: "workflow_classic",
  source_task_id: "task_2",
  source_run_id: "run_2",
  source_title: "发布检查",
  preview_fingerprint: "f".repeat(64),
  candidates: [
    {
      candidate_id: "evidence_goal",
      kind: "intent_summary",
      title: "目标摘要",
      summary: "把发布检查做法沉淀为可复用流程",
      content_hash: "b".repeat(64),
      default_selected: true,
    },
    {
      candidate_id: "evidence_output",
      kind: "final_output_excerpt",
      title: "最终输出片段",
      summary: "原始输出的有界片段",
      content_hash: "d".repeat(64),
      default_selected: false,
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
  clearSkillExperienceApiCache();
});

describe("trusted Skill Creator capture sources", () => {
  it("accepts only persisted assistant messages with complete trusted linkage", () => {
    const linked: XpertConversationMessage = {
      message_id: "message_1",
      role: "assistant",
      content: "done",
      source_task_id: "task_1",
      source_run_id: "run_1",
    };

    expect(xpertMessageCaptureSource(linked, "xpert_1", "conversation_1")).toEqual({
      sourceKind: "xpert_chat",
      taskId: "task_1",
      runId: "run_1",
      xpertId: "xpert_1",
      conversationId: "conversation_1",
      messageId: "message_1",
    });
    expect(xpertMessageCaptureSource({ ...linked, role: "user" }, "xpert_1", "conversation_1")).toBeNull();
    expect(xpertMessageCaptureSource({ ...linked, source_run_id: null }, "xpert_1", "conversation_1")).toBeNull();
    expect(xpertMessageCaptureSource({ role: "assistant", content: "legacy" }, "xpert_1", "conversation_1")).toBeNull();
  });

  it("accepts only a fully completed classic workflow", () => {
    const complete = [{ event: "workflow_end", final_output: "done" }] as WorkflowRunEvent[];
    const waiting = [{ event: "runtime_approval_pending" }] as WorkflowRunEvent[];
    const failed = [{ event: "error", message: "failed" }] as WorkflowRunEvent[];

    expect(completedWorkflowCaptureSource(complete, "task_1", "run_1", false)).toEqual({
      sourceKind: "workflow_classic",
      taskId: "task_1",
      runId: "run_1",
    });
    expect(completedWorkflowCaptureSource(complete, "task_1", "run_1", true)).toBeNull();
    expect(completedWorkflowCaptureSource(waiting, "task_1", "run_1", false)).toBeNull();
    expect(completedWorkflowCaptureSource(failed, "task_1", "run_1", false)).toBeNull();
    expect(completedWorkflowCaptureSource(complete, "task_1", null, false)).toBeNull();
  });

  it("preserves the legacy Xpert capture when experience promotion is disabled", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).endsWith("/api/skills/experience/status")) {
        return jsonResponse({ enabled: false, available: true, model_calls_enabled: false });
      }
      return jsonResponse({ session: { session_id: "creator_xpert" } }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderButton({
      sourceKind: "xpert_chat",
      taskId: "task_1",
      runId: "run_1",
      xpertId: "xpert_1",
      conversationId: "conversation_1",
      messageId: "message_1",
    });

    await userEvent.click(await screen.findByRole("button", { name: "沉淀为 Skill" }));

    expect(await screen.findByText("Creator destination")).toBeVisible();
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      mode: "run",
      source_kind: "xpert_chat",
      source_task_id: "task_1",
      source_run_id: "run_1",
      source_xpert_id: "xpert_1",
      source_conversation_id: "conversation_1",
      source_message_id: "message_1",
    });
  });

  it("preserves the legacy workflow capture without Xpert-only identifiers", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      if (String(input).endsWith("/api/skills/experience/status")) {
        return jsonResponse({ enabled: false, available: true, model_calls_enabled: false });
      }
      return jsonResponse({ session: { session_id: "creator_workflow" } }, 201);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderButton({
      sourceKind: "workflow_classic",
      taskId: "task_2",
      runId: "run_2",
    });

    await userEvent.click(await screen.findByRole("button", { name: "沉淀为 Skill" }));

    expect(await screen.findByText("Creator destination")).toBeVisible();
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      mode: "run",
      source_kind: "workflow_classic",
      source_task_id: "task_2",
      source_run_id: "run_2",
    });
  });

  it("requires evidence confirmation and an explicit decision before opening a prefilled Creator", async () => {
    let analysisReads = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/skills/experience/status")) {
        return jsonResponse({ enabled: true, available: true, model_calls_enabled: true });
      }
      if (url.includes("/api/skills/experience/candidates?")) {
        return jsonResponse({ candidates: [] });
      }
      if (url.endsWith("/api/skills/experience/candidates") && init?.method === "POST") {
        return jsonResponse({ candidate: workflowCandidate("captured", 1), evidence_preview: workflowPreview }, 201);
      }
      if (url.endsWith("/evidence")) {
        return jsonResponse({ candidate: workflowCandidate("captured", 2) });
      }
      if (url.endsWith("/analyze")) {
        return jsonResponse({ candidate: workflowCandidate("analyzing", 3) }, 202);
      }
      if (url.endsWith("/candidates/experience_1") && !init?.method) {
        analysisReads += 1;
        if (analysisReads === 1) {
          return jsonResponse({ candidate: workflowCandidate("analyzing", 3), evidence_preview: null });
        }
        return jsonResponse({ candidate: workflowCandidate("awaiting_review", 4), evidence_preview: null });
      }
      if (url.endsWith("/decision")) {
        return jsonResponse({ candidate: workflowCandidate("promotion_ready", 5) });
      }
      if (url.endsWith("/promote")) {
        return jsonResponse({
          candidate: workflowCandidate("promoted", 6),
          creator_session_id: "creator_from_experience",
          route: "/skills/create/creator_from_experience?step=2",
        });
      }
      return jsonResponse({ detail: { code: "unexpected", message: url } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderButton({ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" });

    await userEvent.click(await screen.findByRole("button", { name: "沉淀为 Skill" }));
    expect(await screen.findByText("确认可用于沉淀的素材")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /最终输出片段/ })).not.toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "确认这些素材" }));
    const evidenceCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/evidence"));
    expect(JSON.parse(String(evidenceCall?.[1]?.body)).evidence_ids).toEqual(["evidence_goal"]);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/analyze"))).toBe(false);

    await userEvent.click(await screen.findByRole("button", { name: "分析并预填" }));
    expect(await screen.findByText("建议新建 Skill", {}, { timeout: 2_500 })).toBeVisible();
    const decisionButton = screen.getByRole("button", { name: "确认并打开 Creator" });
    await waitFor(() => expect(decisionButton).toBeEnabled());
    await userEvent.click(decisionButton);

    expect(await screen.findByText("Creator destination")).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/decision"))).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/promote"))).toBe(true);
    expect(analysisReads).toBe(2);
  });

  it("stays hidden when Creator is disabled or the source is unsupported", () => {
    renderButton({
      sourceKind: "workflow_classic",
      taskId: "task_2",
      runId: "run_2",
    }, false);

    expect(screen.queryByRole("button", { name: "沉淀为 Skill" })).not.toBeInTheDocument();
  });

  it("fails closed instead of opening an empty Creator when the experience Store is unavailable", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/skills/experience/status")) {
        return jsonResponse({ enabled: true, available: false, model_calls_enabled: false });
      }
      if (url.endsWith("/api/skills/experience/candidates")) {
        return jsonResponse({ detail: { code: "skill_experience_store_unavailable", message: "unavailable" } }, 503);
      }
      return jsonResponse({ detail: "unexpected legacy request" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderButton({ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" });

    await userEvent.click(await screen.findByRole("button", { name: "沉淀为 Skill" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "运行经验或已安装 Skill 暂时无法读取",
    );
    expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/skills/creator/sessions")).toBe(false);
  });

  it("restores an editable manual brief when no model is configured", async () => {
    const manual = {
      ...workflowCandidate("awaiting_review", 4),
      analysis_attempt: {
        status: "manual_required" as const,
        executor_mode: "manual" as const,
        error_code: "skill_experience_analysis_unconfigured",
      },
      brief: {
        ...workflowCandidate("awaiting_review", 4).brief!,
        positive_examples: ["检查发布包"],
        negative_examples: [],
        complete: false,
        source: "manual" as const,
      },
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/skills/experience/status")) {
        return jsonResponse({ enabled: true, available: true, model_calls_enabled: false });
      }
      if (url.endsWith("/candidates/experience_1")) {
        return jsonResponse({ candidate: manual, evidence_preview: null });
      }
      return jsonResponse({ detail: `unexpected: ${url}` }, 500);
    }));

    render(
      <MemoryRouter>
        <SkillCreatorCaptureButton
          enabled
          initialCandidate={manual}
          source={{ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" }}
        />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/未使用外部模型/)).toBeVisible();
    expect(screen.getByText("需要补全")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认并打开 Creator" })).toBeDisabled();
    expect(screen.getByLabelText(/应该使用它的例子/)).toBeVisible();
  });

  it("saves a complete no-model brief before promoting a non-empty Creator session", async () => {
    const incomplete = {
      ...workflowCandidate("awaiting_review", 4),
      analysis_attempt: {
        status: "manual_required" as const,
        executor_mode: "manual" as const,
        error_code: "skill_experience_analysis_unconfigured",
      },
      brief: {
        ...workflowCandidate("awaiting_review", 4).brief!,
        positive_examples: ["检查发布包"],
        negative_examples: [],
        complete: false,
        source: "manual" as const,
      },
    };
    const saved = {
      ...workflowCandidate("awaiting_review", 5),
      analysis_attempt: incomplete.analysis_attempt,
      brief: {
        ...workflowCandidate("awaiting_review", 5).brief!,
        positive_examples: ["检查发布包", "验证扩展名"],
        negative_examples: ["普通文章摘要", "个人照片重命名"],
        expected_output: "输出问题、证据和未检查项",
        success_criteria: ["结论绑定路径"],
        reusable_steps: ["读取清单"],
        failure_boundaries: ["不编造缺失文件"],
        overfitting_risk: "不绑定本次文件名",
        source: "user" as const,
      },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/skills/experience/status")) {
        return jsonResponse({ enabled: true, available: true, model_calls_enabled: false });
      }
      if (url.endsWith("/brief") && init?.method === "PATCH") {
        return jsonResponse({ candidate: saved });
      }
      if (url.endsWith("/decision")) {
        return jsonResponse({ candidate: workflowCandidate("promotion_ready", 6) });
      }
      if (url.endsWith("/promote")) {
        return jsonResponse({
          candidate: workflowCandidate("promoted", 7),
          creator_session_id: "creator_from_experience",
          route: "/skills/create/creator_from_experience?step=2",
        });
      }
      return jsonResponse({ detail: `unexpected: ${url}` }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/origin"]}>
        <Routes>
          <Route
            element={(
              <SkillCreatorCaptureButton
                enabled
                initialCandidate={incomplete}
                source={{ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" }}
              />
            )}
            path="/origin"
          />
          <Route element={<p>Creator destination</p>} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.clear(await screen.findByLabelText(/应该使用它的例子/));
    await userEvent.type(screen.getByLabelText(/应该使用它的例子/), "检查发布包{enter}验证扩展名");
    await userEvent.type(screen.getByLabelText(/相似但不该使用的例子/), "普通文章摘要{enter}个人照片重命名");
    await userEvent.clear(screen.getByLabelText("预期输出"));
    await userEvent.type(screen.getByLabelText("预期输出"), "输出问题、证据和未检查项");
    await userEvent.clear(screen.getByLabelText(/成功标准/));
    await userEvent.type(screen.getByLabelText(/成功标准/), "结论绑定路径");
    await userEvent.clear(screen.getByLabelText(/可复用步骤/));
    await userEvent.type(screen.getByLabelText(/可复用步骤/), "读取清单");
    await userEvent.clear(screen.getByLabelText(/失败边界/));
    await userEvent.type(screen.getByLabelText(/失败边界/), "不编造缺失文件");
    await userEvent.clear(screen.getByLabelText("避免过拟合"));
    await userEvent.type(screen.getByLabelText("避免过拟合"), "不绑定本次文件名");
    await userEvent.click(screen.getByRole("button", { name: "保存提纲" }));

    const promoteButton = screen.getByRole("button", { name: "确认并打开 Creator" });
    await waitFor(() => expect(promoteButton).toBeEnabled());
    await userEvent.click(promoteButton);

    expect(await screen.findByText("Creator destination")).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/analyze"))).toBe(false);
    const briefCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/brief"));
    expect(JSON.parse(String(briefCall?.[1]?.body))).toMatchObject({
      positive_examples: ["检查发布包", "验证扩展名"],
      negative_examples: ["普通文章摘要", "个人照片重命名"],
      expected_output: "输出问题、证据和未检查项",
    });
  });

  it("supports an explicit retry label without changing the capture contract", () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      enabled: false,
      available: true,
      model_calls_enabled: false,
    })));
    render(
      <MemoryRouter>
        <SkillCreatorCaptureButton
          busyLabel="正在重试..."
          enabled
          label="重试创建 Creator 会话"
          source={{
            sourceKind: "workflow_classic",
            taskId: "task-2",
            runId: "run-2",
          }}
        />
      </MemoryRouter>,
    );

    return expect(
      screen.findByRole("button", { name: "重试创建 Creator 会话" }),
    ).resolves.toBeVisible();
  });

  it("allows declining an incomplete manual brief without saving or promoting it", async () => {
    const candidate = workflowCandidate("awaiting_review", 4);
    candidate.brief = { ...candidate.brief!, complete: false, positive_examples: [], negative_examples: [] };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/status")) return jsonResponse({ enabled: true, available: true, model_calls_enabled: false });
      if (url.endsWith("/candidates/experience_1")) return jsonResponse({ candidate, evidence_preview: null });
      if (url.endsWith("/dismiss") && init?.method === "POST") return jsonResponse({ candidate: { ...candidate, state: "dismissed" } });
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><SkillCreatorCaptureButton enabled initialCandidate={candidate} source={{ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" }} /></MemoryRouter>);

    await userEvent.click(await screen.findByRole("button", { name: "这次不沉淀" }));
    const confirm = screen.getByRole("button", { name: "确认暂不沉淀" });
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(await screen.findByText("这次运行已标记为暂不沉淀。")).toBeVisible();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/dismiss"));
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ expected_revision: 4, expected_digest: candidate.digest });
  });

  it("does not reuse the previous run candidate when the capture source changes", async () => {
    const candidate = workflowCandidate("promoted", 5);
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/status")) return jsonResponse({ enabled: true, available: true, model_calls_enabled: false });
      if (url.includes("/candidates?")) return jsonResponse({ candidates: [candidate] });
      if (url.endsWith("/candidates/experience_1")) return jsonResponse({ candidate, evidence_preview: null });
      return jsonResponse({ detail: "unexpected request" }, 500);
    }));
    const view = render(<MemoryRouter><SkillCreatorCaptureButton enabled source={{ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" }} /></MemoryRouter>);
    expect(await screen.findByText("经验已交给 Creator")).toBeVisible();

    view.rerender(<MemoryRouter><SkillCreatorCaptureButton enabled source={{ sourceKind: "workflow_classic", taskId: "task_new", runId: "run_new" }} /></MemoryRouter>);
    expect(await screen.findByRole("button", { name: "沉淀为 Skill" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "打开 Creator" })).not.toBeInTheDocument();
  });

  it("keeps third-party overlaps read-only and sends only the selected Creator update target", async () => {
    const candidate = workflowCandidate("awaiting_review", 4);
    candidate.brief = { ...candidate.brief!, suggestion: "update" };
    candidate.overlaps = [
      { candidate_id: "third-party", candidate_fingerprint: "a".repeat(64), name: "Third-party checker", source_type: "installed", source_kind: "git", installed_skill_id: "git-skill", update_target_eligible: false, best_rank: 1, major_overlap: true },
      { candidate_id: "creator-checker", candidate_fingerprint: "b".repeat(64), name: "Creator checker", source_type: "installed", source_kind: "workspace_draft", installed_skill_id: "stable-skill", creator_draft_id: "draft_1", update_target_eligible: true, best_rank: 2, major_overlap: true },
    ];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/status")) return jsonResponse({ enabled: true, available: true, model_calls_enabled: true });
      if (url.endsWith("/candidates/experience_1")) return jsonResponse({ candidate, evidence_preview: null });
      if (url.endsWith("/decision") && init?.method === "POST") return jsonResponse({ detail: { code: "skill_experience_promotion_stale", message: "private details must not be shown" } }, 409);
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><SkillCreatorCaptureButton enabled initialCandidate={candidate} source={{ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" }} /></MemoryRouter>);

    expect(await screen.findByRole("option", { name: "Creator checker" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Third-party checker" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "确认并打开 Creator" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("来源或目标版本已变化，请重新加载后检查。");
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/decision"));
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ decision: "update", target_skill_id: "stable-skill", expected_revision: 4 });
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/promote"))).toBe(false);
  });

  it("requires a reason to override no-skill but lets users accept it without creating a session", async () => {
    const candidate = workflowCandidate("awaiting_review", 4);
    candidate.brief = { ...candidate.brief!, suggestion: "no_skill", no_skill_reason: "one_off_task" };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/status")) return jsonResponse({ enabled: true, available: true, model_calls_enabled: true });
      if (url.endsWith("/candidates/experience_1")) return jsonResponse({ candidate, evidence_preview: null });
      if (url.endsWith("/decision") && init?.method === "POST") return jsonResponse({ candidate: { ...candidate, state: "dismissed" } });
      return jsonResponse({ detail: "unexpected request" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><SkillCreatorCaptureButton enabled initialCandidate={candidate} source={{ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" }} /></MemoryRouter>);

    expect(await screen.findByText("不建议沉淀")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "新建 Skill" }));
    await userEvent.click(screen.getByRole("button", { name: "确认并打开 Creator" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请说明为什么仍要继续沉淀。");
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "这次不沉淀" }));
    await userEvent.click(screen.getByRole("button", { name: "确认暂不沉淀" }));
    expect(await screen.findByText("这次运行已标记为暂不沉淀。")).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/promote"))).toBe(false);
  });

  it("shows a sparse no-skill result as a completed decision without fake missing fields", async () => {
    const candidate = workflowCandidate("awaiting_review", 4);
    candidate.brief = {
      ...candidate.brief!,
      suggestion: "no_skill",
      recommendation_reason: "",
      no_skill_reason: "one_off_task",
      intent: "",
      positive_examples: [],
      negative_examples: [],
      expected_output: "",
      success_criteria: [],
      reusable_steps: [],
      failure_boundaries: [],
      resource_clues: [],
      overfitting_risk: "",
      complete: true,
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/status")) return jsonResponse({ enabled: true, available: true, model_calls_enabled: true });
      if (url.endsWith("/candidates/experience_1")) return jsonResponse({ candidate, evidence_preview: null });
      return jsonResponse({ detail: "unexpected request" }, 500);
    }));
    render(<MemoryRouter><SkillCreatorCaptureButton enabled initialCandidate={candidate} source={{ sourceKind: "workflow_classic", taskId: "task_2", runId: "run_2" }} /></MemoryRouter>);

    expect(await screen.findByText("判断完成")).toBeVisible();
    expect(screen.getByText("这只是一次性任务")).toBeVisible();
    expect(screen.queryByText("待补充")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认暂不沉淀" })).toBeEnabled();
  });
});
