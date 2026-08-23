import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  SkillCreatorDraft,
  SkillCreatorProposal,
  SkillCreatorSession,
  SkillEvaluationCase,
  SkillEvaluationRun,
  SkillResourceBuild,
  SkillResourcePlan,
} from "../utils/skillCreatorApi";
import SkillCreatorIndexPage from "./SkillCreatorIndexPage";
import SkillCreatorStudioPage from "./SkillCreatorStudioPage";

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  }));
}

const status = {
  enabled: true,
  version: "skill-creator-v2",
  model_available: false,
  assistant_agent_id: "skill-creator-assistant-v1",
  supported_sources: ["xpert_chat", "workflow_classic"],
  model_unavailable_reason: "模型网关未配置 Key。",
};

const baseSession: SkillCreatorSession = {
  session_id: "creator_1",
  session_revision: 2,
  draft_state_revision: 0,
  mode: "blank",
  assistant_agent_id: "skill-creator-assistant-v1",
  intent: "分析竞品 PDF 并保留页码",
  positive_examples: ["比较两份竞品 PDF"],
  near_miss_examples: ["仅转换 PDF 文字"],
  expected_output: "中文对比表",
  success_criteria: ["每项结论包含页码"],
  selected_evidence: [],
  state: "selecting_evidence",
  created_at: 1,
  updated_at: 2,
};

const draft: SkillCreatorDraft = {
  draft_id: "draft_1",
  root_name: "compare-pdf",
  name: "compare-pdf",
  slug: "compare-pdf",
  description: "比较 PDF 并保留证据页码。",
  skill_markdown: "---\nname: compare-pdf\ndescription: 比较 PDF 并保留证据页码。\n---\n\n# PDF 对比\n\n逐项记录页码。",
  files: { "references/output.md": "# 输出格式" },
  status: "draft",
  revision: 1,
  content_revision: 1,
  content_digest: "a".repeat(64),
  quality_required: true,
  quality_status: "not_evaluated",
  validation: {
    valid: true,
    validator_version: "skill-package-v2.1",
    issues: [],
    content_digest: "a".repeat(64),
  },
};

function creatorProposal(
  proposalStatus: SkillCreatorProposal["status"],
): SkillCreatorProposal {
  return {
    proposal_id: `proposal_${proposalStatus}`,
    kind: "skill_update",
    title: "更新 PDF 对比 Skill",
    status: proposalStatus,
    revision: 2,
    creator_session_id: baseSession.session_id,
    apply_key: `apply_${proposalStatus}`,
    payload_digest: "c".repeat(64),
    content_digest: "d".repeat(64),
    base_digest: draft.content_digest,
    base_revision: draft.revision,
    target_id: draft.draft_id,
    payload: {
      ...draft,
      skill_markdown: `${draft.skill_markdown}\n\n增加审计步骤。`,
    },
    validation: {
      valid: true,
      validator_version: "skill-package-v2.1",
      issues: [],
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Skill Creator pages", () => {
  it("opens trusted workflow evidence after saving the handoff requirement", async () => {
    let currentSession: SkillCreatorSession = {
      ...baseSession,
      mode: "run",
      source_kind: "workflow_classic",
      source_task_id: "task-1",
      source_run_id: "run-1",
      evidence_confirmed: false,
      selected_evidence: [],
    };
    const preview = {
      preview_fingerprint: "f".repeat(64),
      source_kind: "workflow_classic",
      source_task_id: "task-1",
      source_run_id: "run-1",
      candidates: [
        {
          candidate_id: "intent-summary",
          kind: "intent_summary",
          title: "目标摘要",
          summary: "把客户访谈整理成决策简报。",
          content_hash: "1".repeat(64),
          default_selected: true,
        },
        {
          candidate_id: "workflow-analysis",
          kind: "final_output_excerpt",
          title: "最终输出片段",
          summary: "**缺失信息：** 请确认访谈格式。",
          content_hash: "2".repeat(64),
          default_selected: false,
        },
      ],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: currentSession });
      }
      if (url === "/api/skills/creator/sessions/creator_1" && init?.method === "PATCH") {
        currentSession = { ...currentSession, session_revision: 3 };
        return jsonResponse({ session: currentSession });
      }
      if (url.endsWith("/source-preview") && init?.method === "POST") {
        return jsonResponse(preview);
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "把你的做法变成可复用的 Skill" });
    await userEvent.click(screen.getByRole("button", { name: "保存需求，查看素材" }));

    expect(await screen.findByText("工作流分析不会自动写入方案")).toBeVisible();
    expect(screen.getByText("已选 1 项")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /目标摘要/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /工作流生成的需求分析/ })).not.toBeChecked();
    expect(screen.getByText("缺失信息： 请确认访谈格式。")).toBeVisible();
    expect(screen.getByRole("button", { name: "保存选中素材并继续" })).toBeEnabled();
    expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/source-preview") && (init as RequestInit | undefined)?.method === "POST"
    )).toBe(true);
  });

  it("fails closed on a disabled direct route", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({
      ...status,
      enabled: false,
      disabled_reason: "尚未开放质量评测闭环。",
    })));

    render(
      <MemoryRouter>
        <SkillCreatorIndexPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Skill Creator 尚未启用" })).toBeVisible();
    expect(screen.getByText("尚未开放质量评测闭环。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "进入工作台" })).not.toBeInTheDocument();
  });

  it("lists durable sessions and creates a new blank session", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url.startsWith("/api/skills/creator/sessions?") && !init?.method) {
        return jsonResponse({ items: [baseSession], total: 1 });
      }
      if (url === "/api/skills/creator/sessions" && init?.method === "POST") {
        return jsonResponse({ session: { ...baseSession, session_id: "creator_2", intent: "新目标" } }, 201);
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/skills/create"]}>
        <Routes>
          <Route element={<SkillCreatorIndexPage />} path="/skills/create" />
          <Route element={<p>studio destination</p>} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("分析竞品 PDF 并保留页码")).toBeVisible();
    expect(screen.getByText(/AI 先给方案/)).toBeVisible();
    expect(screen.getByText(/用真实任务试用/)).toBeVisible();
    fireEvent.change(screen.getByLabelText("你希望它帮你完成什么？"), { target: { value: "新目标" } });
    await userEvent.click(screen.getByRole("button", { name: /开始创建/ }));

    expect(await screen.findByText("studio destination")).toBeVisible();
    const createCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input) === "/api/skills/creator/sessions" && (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String((createCall?.[1] as RequestInit).body))).toEqual({ mode: "blank", intent: "新目标" });
  });

  it("supports the no-model blank draft path and Ctrl+S immutable save", async () => {
    let currentSession: SkillCreatorSession = baseSession;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: currentSession });
      }
      if (url.endsWith("/source-preview") && init?.method === "POST") {
        return jsonResponse({
          preview_fingerprint: "c".repeat(64),
          source_kind: "blank",
          source_task_id: null,
          source_run_id: null,
          candidates: [],
        });
      }
      if (url.endsWith("/evidence") && init?.method === "PUT") {
        currentSession = {
          ...currentSession,
          session_revision: currentSession.session_revision + 1,
          evidence_preview_fingerprint: "c".repeat(64),
          evidence_confirmed: true,
        };
        return jsonResponse({ session: currentSession });
      }
      if (url.endsWith("/draft") && init?.method === "POST") {
        currentSession = {
          ...currentSession,
          session_revision: currentSession.session_revision + 1,
          draft_state_revision: 1,
          draft_id: draft.draft_id,
          current_revision: 1,
          current_digest: draft.content_digest,
          state: "editing_draft",
          draft,
        };
        return jsonResponse({ session: currentSession }, 201);
      }
      if (url.endsWith("/draft") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { package: SkillCreatorDraft };
        const savedDraft = {
          ...draft,
          ...body.package,
          revision: 2,
          content_revision: 2,
          content_digest: "b".repeat(64),
        };
        currentSession = {
          ...currentSession,
          session_revision: 4,
          draft_state_revision: 2,
          current_revision: 2,
          current_digest: savedDraft.content_digest,
          draft: savedDraft,
        };
        return jsonResponse({ session: currentSession });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "把你的做法变成可复用的 Skill" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认方案" }));
    expect(screen.getByText("模型网关未配置 Key。")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成可评测初稿" })).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Skill ID"), "compare-pdf");
    await userEvent.type(
      screen.getByLabelText("能力、触发场景与不适用边界"),
      "比较 PDF 并保留证据页码。用于竞品分析，不用于仅做文字转换。",
    );
    expect(screen.getByRole("button", { name: "创建结构化手工模板" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "确认并继续" }));
    expect(await screen.findByText(/已确认不导入运行素材/)).toBeVisible();
    const evidenceCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith("/evidence") && (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(JSON.parse(String((evidenceCall?.[1] as RequestInit).body))).toMatchObject({
      candidate_ids: [],
      preview_fingerprint: "c".repeat(64),
    });
    await userEvent.click(screen.getByRole("button", { name: "创建结构化手工模板" }));
    await screen.findByRole("button", { name: "保存草稿" });
    const draftCreateCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith("/draft") && (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String((draftCreateCall?.[1] as RequestInit).body))).toMatchObject({
      skill_id: "compare-pdf",
      description: "比较 PDF 并保留证据页码。用于竞品分析，不用于仅做文字转换。",
    });
    expect(screen.queryByText("有未保存修改")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();

    await userEvent.click(screen.getByRole("tab", { name: "源码编辑" }));
    const source = screen.getByLabelText("编辑 SKILL.md");
    fireEvent.change(source, { target: { value: `${draft.skill_markdown}\n\n新增步骤。` } });
    expect(screen.getByText("有未保存修改")).toBeVisible();
    fireEvent.keyDown(window, { key: "s", ctrlKey: true });

    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith("/draft") && (init as RequestInit | undefined)?.method === "PUT",
    )).toBe(true));
    expect(await screen.findByText(/草稿已保存为新的不可变内容版本/)).toBeVisible();
  });

  it("requires all six readiness items before AI generation", async () => {
    const incompleteSession: SkillCreatorSession = {
      ...baseSession,
      near_miss_examples: [],
      evidence_confirmed: true,
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") {
        return jsonResponse({ ...status, model_available: true, model_unavailable_reason: null });
      }
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: incompleteSession });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    }));

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "把你的做法变成可复用的 Skill" });
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认方案" }));

    expect(screen.getByRole("heading", { name: "AI 生成准备度" })).toBeVisible();
    expect(screen.getByText("5/6 项已完成")).toBeVisible();
    expect(screen.getByText("AI 生成暂不可用，仍缺：近似反例。")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成可评测初稿" })).toBeDisabled();
  });

  it("restores a saved evidence selection instead of overwriting it with preview defaults", async () => {
    const selectedCandidate = {
      candidate_id: "evidence-saved",
      kind: "tool_names" as const,
      title: "已保存的工具",
      summary: "使用 pdfplumber 读取页码。",
      content_hash: "e".repeat(64),
    };
    const runSession: SkillCreatorSession = {
      ...baseSession,
      mode: "run",
      source_kind: "xpert_chat",
      source_task_id: "task-1",
      source_run_id: "run-1",
      evidence_confirmed: true,
      selected_evidence: [selectedCandidate],
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: runSession });
      }
      if (url.endsWith("/source-preview") && init?.method === "POST") {
        return jsonResponse({
          preview_fingerprint: "f".repeat(64),
          source_kind: "xpert_chat",
          source_task_id: "task-1",
          source_run_id: "run-1",
          candidates: [
            {
              candidate_id: "evidence-default",
              kind: "intent_summary",
              title: "默认目标",
              summary: "默认选中的目标摘要。",
              content_hash: "1".repeat(64),
              default_selected: true,
            },
            { ...selectedCandidate, default_selected: false },
          ],
        });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    }));

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "把你的做法变成可复用的 Skill" });
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认方案" }));
    await userEvent.click(screen.getByRole("button", { name: "读取脱敏素材候选" }));

    expect(await screen.findByRole("checkbox", { name: /默认目标/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /已保存的工具/ })).toBeChecked();
  });

  it("offers a typed update proposal for an existing draft and keeps retry visible after rejection", async () => {
    const rejected = creatorProposal("rejected");
    const pending = creatorProposal("pending");
    let currentSession: SkillCreatorSession = {
      ...baseSession,
      evidence_confirmed: true,
      draft_id: draft.draft_id,
      current_revision: draft.revision,
      current_digest: draft.content_digest,
      draft_state_revision: draft.revision,
      state: "editing_draft",
      draft,
      proposal_id: rejected.proposal_id,
      proposal: rejected,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") {
        return jsonResponse({ ...status, model_available: true, model_unavailable_reason: null });
      }
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: currentSession });
      }
      if (url.endsWith("/generate") && init?.method === "POST") {
        currentSession = {
          ...currentSession,
          proposal_id: pending.proposal_id,
          proposal: pending,
        };
        return jsonResponse({ session: currentSession, proposal: pending });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("button", { name: "保存草稿" });
    expect(screen.getByRole("button", { name: "生成可评测更新初稿" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认方案" }));
    expect(screen.getByText("该提案没有写入草稿。你可以保留当前草稿并重新生成提案。")).toBeVisible();

    const generateButton = screen.getByRole("button", { name: "生成可评测更新初稿" });
    expect(generateButton).toBeEnabled();
    await userEvent.click(generateButton);

    await screen.findByText("生成助手已提交类型化提案，请检查文件差异后再批准。");
    const generateCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith("/generate") && (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String((generateCall?.[1] as RequestInit).body))).toEqual({
      expected_session_revision: currentSession.session_revision,
    });
    expect(screen.queryByRole("button", { name: "生成可评测更新初稿" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准并写入草稿" })).toBeVisible();
  });

  it("shows only the pending proposal after a resource build is finalized", async () => {
    const pending = creatorProposal("pending");
    const resourcePlan: SkillResourcePlan = {
      plan_id: "plan_pending_proposal",
      session_id: baseSession.session_id,
      revision: 2,
      digest: "7".repeat(64),
      state: "confirmed",
      session_revision: baseSession.session_revision,
      draft_id: draft.draft_id,
      draft_revision: draft.content_revision,
      draft_digest: draft.content_digest,
      skill_name: draft.name,
      skill_description: draft.description,
      workflow_steps: [{ step_id: "collect", instruction: "Collect explicit evidence." }],
      output_contract: ["Return a cited comparison."],
      failure_modes: ["Mark missing evidence."],
      resources: [],
      clarifications: [],
      clarification_answers: {},
      created_at: 1,
      updated_at: 2,
    };
    const resourceSession: SkillCreatorSession = {
      ...baseSession,
      authoring_flow: "resource",
      evidence_confirmed: true,
      draft_id: draft.draft_id,
      draft,
      current_revision: draft.content_revision,
      current_digest: draft.content_digest,
      proposal_id: pending.proposal_id,
      proposal: pending,
      resource_plan: resourcePlan,
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse({
        ...status,
        resource_authoring_enabled: true,
        resource_builder_available: true,
      });
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({
          session: resourceSession,
          draft,
          proposal: pending,
          resource_plan: resourcePlan,
        });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    }));

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "批准并写入草稿" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "准备生成内容" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "按新方案开始生成" })).not.toBeInTheDocument();
  });

  it("restores the latest resource-build proposal instead of an older approved session proposal", async () => {
    const approved = creatorProposal("approved");
    const pending = { ...creatorProposal("pending"), proposal_id: "proposal_evolved_pending" };
    const completedBuild: SkillResourceBuild = {
      build_id: "build_evolved",
      session_id: baseSession.session_id,
      revision: 4,
      digest: "8".repeat(64),
      state: "accepted",
      phase: "proposal",
      session_revision: baseSession.session_revision,
      plan_id: "plan_evolved",
      plan_revision: 3,
      plan_digest: "7".repeat(64),
      draft_id: draft.draft_id,
      draft_revision: draft.content_revision,
      draft_digest: draft.content_digest,
      skill_name: draft.name,
      skill_description: draft.description,
      workflow_steps: [],
      output_contract: [],
      failure_modes: [],
      resources: [],
      hooks: [],
      current_resource_id: null,
      skill_chunks: [],
      skill_markdown: draft.skill_markdown,
      skill_markdown_digest: "6".repeat(64),
      skill_attempt: 1,
      skill_repair_count: 0,
      skill_validation_issues: [],
      skill_feedback: "",
      requirement_coverage: [],
      proposal_id: pending.proposal_id,
      created_at: 1,
      updated_at: 2,
    };
    const evolvedSession: SkillCreatorSession = {
      ...baseSession,
      authoring_flow: "resource",
      state: "iterating",
      review_state: "revise",
      evidence_confirmed: true,
      draft_id: draft.draft_id,
      draft,
      current_revision: draft.content_revision,
      current_digest: draft.content_digest,
      proposal_id: approved.proposal_id,
      proposal: approved,
      resource_build: completedBuild,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse({
        ...status,
        resource_authoring_enabled: true,
      });
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: evolvedSession, draft, proposal: approved, resource_build: completedBuild });
      }
      if (url === `/api/runtime/authoring-proposals/${pending.proposal_id}`) {
        return jsonResponse(pending);
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "批准并写入草稿" })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/runtime/authoring-proposals/${pending.proposal_id}`,
      undefined,
    );
  });

  it("rejects a pending Creator proposal, refreshes the Session, and restores retry", async () => {
    const pending = creatorProposal("pending");
    pending.validation = {
      valid: false,
      validator_version: "skill-package-v2.1",
      issues: [{
        code: "skill_package_invalid",
        message: "生成内容未通过校验。",
        severity: "error",
        path: "SKILL.md",
      }],
    };
    const rejected = { ...pending, status: "rejected" as const, revision: pending.revision + 1 };
    let currentSession: SkillCreatorSession = {
      ...baseSession,
      evidence_confirmed: true,
      draft_id: draft.draft_id,
      current_revision: draft.revision,
      current_digest: draft.content_digest,
      draft_state_revision: draft.revision,
      state: "editing_draft",
      draft,
      proposal_id: pending.proposal_id,
      proposal: pending,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") {
        return jsonResponse({ ...status, model_available: true, model_unavailable_reason: null });
      }
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: currentSession });
      }
      if (url.endsWith(`/authoring-proposals/${pending.proposal_id}/reject`) && init?.method === "POST") {
        currentSession = { ...currentSession, proposal: rejected };
        return jsonResponse(rejected);
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "丢弃提案" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批准并写入草稿" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "丢弃提案" }));
    await userEvent.type(screen.getByLabelText("简短原因"), "校验失败，重新生成");
    await userEvent.click(screen.getByRole("button", { name: "确认丢弃提案" }));

    expect(await screen.findByText("提案已丢弃，草稿未改变。你可以重新生成提案。")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成可评测更新初稿" })).toBeEnabled();
    const rejectCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input).endsWith(`/authoring-proposals/${pending.proposal_id}/reject`) &&
      (init as RequestInit | undefined)?.method === "POST",
    );
    expect(JSON.parse(String((rejectCall?.[1] as RequestInit).body))).toEqual({
      revision: pending.revision,
      reason: "校验失败，重新生成",
    });
    expect(fetchMock.mock.calls.filter(([input, init]) =>
      String(input) === "/api/skills/creator/sessions/creator_1" && !(init as RequestInit | undefined)?.method,
    )).toHaveLength(2);
  });

  it("blocks step and Creator-link navigation while the draft has unsaved changes", async () => {
    const editingSession: SkillCreatorSession = {
      ...baseSession,
      evidence_confirmed: true,
      draft_id: draft.draft_id,
      current_revision: draft.revision,
      current_digest: draft.content_digest,
      draft_state_revision: draft.revision,
      state: "editing_draft",
      draft,
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: editingSession });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes>
          <Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" />
          <Route element={<p>Creator session list</p>} path="/skills/create" />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("button", { name: "保存草稿" });
    fireEvent.change(screen.getByLabelText("编辑 SKILL.md"), {
      target: { value: `${draft.skill_markdown}\n\n尚未保存。` },
    });
    await screen.findByText("有未保存修改");
    expect(screen.getByRole("button", { name: "生成可评测更新初稿" })).toBeDisabled();

    const historyGo = vi.spyOn(window.history, "go").mockImplementation(() => undefined);
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(historyGo).toHaveBeenCalledWith(1);

    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认方案" }));
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeVisible();
    expect(confirm).toHaveBeenCalledTimes(2);

    await userEvent.click(screen.getByRole("link", { name: "返回我的 Skill" }));
    expect(screen.queryByText("Creator session list")).not.toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(3);

    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByRole("link", { name: "返回我的 Skill" }));
    expect(await screen.findByText("Creator session list")).toBeVisible();
  });

  it("restores an evaluation run after refresh and exposes the responsive final stages", async () => {
    const cases: SkillEvaluationCase[] = [1, 2, 3].map((number) => ({
      case_id: `case-${number}`,
      name: `恢复用例 ${number}`,
      prompt: `处理材料 ${number}`,
      expected_behavior: "返回摘要",
      fixtures: [],
      assertions: [],
    }));
    const run: SkillEvaluationRun = {
      run_id: "evaluation-recovered",
      session_id: baseSession.session_id,
      status: "completed",
      revision: 3,
      frozen_digest: draft.content_digest,
      model_id: "gateway/default-text",
      repetitions: 1,
      cases,
      items: cases.flatMap((item) => ([
        { item_id: `${item.case_id}-base`, case_id: item.case_id, target: "baseline" as const, repetition: 1, status: "completed" as const, output: "baseline", actual_model: "real-model" },
        { item_id: `${item.case_id}-candidate`, case_id: item.case_id, target: "candidate" as const, repetition: 1, status: "completed" as const, output: "candidate", actual_model: "real-model", skill_read: true },
      ])),
    };
    const recoveredSession: SkillCreatorSession = {
      ...baseSession,
      draft_id: draft.draft_id,
      draft,
      current_revision: draft.revision,
      current_digest: draft.content_digest,
      state: "reviewing_results",
      latest_evaluation_run_id: run.run_id,
      active_evaluation_run_id: null,
      quality_mode: "objective",
      cases_revision: 1,
      evaluation_cases: cases,
      review_state: "pending",
      review_revision: 0,
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) return jsonResponse({ session: recoveredSession, draft });
      if (url === `/api/skills/creator/evaluations/${run.run_id}` && !init?.method) return jsonResponse({ version: 1, run });
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    }));

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes><Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "看看 Skill 是否真的更好用" })).toBeVisible();
    expect(screen.getByText("当前步骤 5/6")).toBeVisible();
    expect(screen.getByText("展开全部步骤")).toBeVisible();
    expect(screen.getByRole("button", { name: "第 4 步：试一试" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "第 6 步：改进并安装" })).toBeEnabled();
  });

  it("refreshes a stale Creator session when its browser tab regains focus", async () => {
    const staleSession: SkillCreatorSession = {
      ...baseSession,
      draft_id: draft.draft_id,
      draft,
      current_revision: draft.revision,
      current_digest: draft.content_digest,
      state: "iterating",
      review_state: "none",
      review_revision: 0,
    };
    const revisedSession: SkillCreatorSession = {
      ...staleSession,
      session_revision: staleSession.session_revision + 1,
      review_state: "revise",
      review_revision: 1,
      regression_governance: {
        version: "skill-creator-regression-v1",
        enabled: true,
        max_items: 72,
        case_count: 3,
        target_count: 2,
        estimated_model_calls: 6,
        max_repetitions: 3,
        previous_revision: null,
        previous_digest: null,
        evolution_history_available: true,
        revisions: [],
        runs: [],
      },
    };
    let sessionReads = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        sessionReads += 1;
        return jsonResponse({
          session: sessionReads === 1 ? staleSession : revisedSession,
          draft,
          regression_governance: sessionReads === 1 ? null : revisedSession.regression_governance,
        });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    }));

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes><Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/选择“需要修改”后|选择“还要修改”后/)).toBeVisible();
    window.dispatchEvent(new Event("focus"));

    expect(await screen.findByRole("button", { name: "生成改进方案" })).toBeVisible();
    expect(sessionReads).toBeGreaterThanOrEqual(2);
  });

  it("offers a new evolution plan when the persisted plan is stale", async () => {
    const stalePlanSession: SkillCreatorSession = {
      ...baseSession,
      draft_id: draft.draft_id,
      draft,
      current_revision: draft.content_revision,
      current_digest: draft.content_digest,
      state: "iterating",
      review_state: "revise",
      review_revision: 2,
      evolution_plan: {
        plan_id: "stale-evolution-plan",
        state: "stale",
      } as unknown as NonNullable<SkillCreatorSession["evolution_plan"]>,
      regression_governance: {
        version: "skill-creator-regression-v1",
        enabled: true,
        max_items: 72,
        case_count: 3,
        target_count: 3,
        estimated_model_calls: 9,
        max_repetitions: 3,
        previous_revision: 1,
        previous_digest: "b".repeat(64),
        evolution_history_available: true,
        revisions: [],
        runs: [],
      },
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse(status);
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: stalePlanSession, draft });
      }
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    }));

    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes><Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("button", { name: "生成改进方案" })).toBeVisible();
    expect(screen.queryByText("方案 3")).not.toBeInTheDocument();
  });

  it("keeps the resource step active while starting a replacement build", async () => {
    const plan = {
      plan_id: "plan-current",
      session_id: baseSession.session_id,
      revision: 2,
      digest: "p".repeat(64),
      state: "confirmed" as const,
      session_revision: 7,
      draft_id: draft.draft_id,
      draft_revision: draft.content_revision,
      draft_digest: draft.content_digest,
      skill_name: draft.name,
      skill_description: draft.description,
      workflow_steps: [{ step_id: "step-1", instruction: "读取规则并生成报告" }],
      output_contract: ["返回带页码的中文报告"],
      failure_modes: ["资料不足时明确说明"],
      resources: [{
        resource_id: "resource-1",
        spec_digest: "s".repeat(64),
        kind: "reference" as const,
        action: "update" as const,
        generation_cost: "low" as const,
        path: "references/rules.md",
        purpose: "保存核对规则",
        source_ids: [],
        used_by_steps: ["step-1"],
        depends_on: [],
        acceptance_checks: ["包含页码规则"],
      }],
      clarifications: [],
      clarification_answers: {},
      created_at: 1,
      updated_at: 2,
    };
    const staleBuild = {
      build_id: "build-old",
      session_id: baseSession.session_id,
      revision: 1,
      digest: "b".repeat(64),
      state: "stale" as const,
      phase: "resources" as const,
      session_revision: 7,
      plan_id: "plan-old",
      plan_revision: 1,
      plan_digest: "o".repeat(64),
      draft_id: draft.draft_id,
      draft_revision: draft.content_revision,
      draft_digest: draft.content_digest,
      skill_name: draft.name,
      skill_description: draft.description,
      workflow_steps: plan.workflow_steps,
      output_contract: plan.output_contract,
      failure_modes: plan.failure_modes,
      resources: [],
      current_resource_id: null,
      skill_chunks: [],
      skill_markdown: null,
      skill_markdown_digest: null,
      skill_attempt: 0,
      skill_repair_count: 0,
      skill_validation_issues: [],
      skill_feedback: "",
      requirement_coverage: [],
      stale: true,
      created_at: 1,
      updated_at: 2,
    };
    const startedBuild = {
      ...staleBuild,
      build_id: "build-new",
      revision: 1,
      digest: "n".repeat(64),
      state: "planned" as const,
      plan_id: plan.plan_id,
      plan_revision: plan.revision,
      plan_digest: plan.digest,
      stale: false,
      resources: [{
        ...plan.resources[0],
        state: "planned" as const,
        attempt: 0,
        repair_count: 0,
        chunks: [],
        content: null,
        content_digest: null,
        base_content: null,
        base_digest: null,
        script_tests: [],
        script_receipt: null,
        validation_issues: [],
        feedback: "",
      }],
      current_resource_id: "resource-1",
    };
    const generatedBuild = {
      ...startedBuild,
      revision: 2,
      digest: "g".repeat(64),
      state: "awaiting_review" as const,
      resources: [{
        ...startedBuild.resources[0],
        state: "awaiting_review" as const,
        attempt: 1,
        content: "# 核对规则\n",
        content_digest: "c".repeat(64),
      }],
    };
    const resourceSession: SkillCreatorSession = {
      ...baseSession,
      session_revision: 7,
      authoring_flow: "resource",
      draft_id: draft.draft_id,
      draft,
      current_revision: draft.content_revision,
      current_digest: draft.content_digest,
      state: "iterating",
      review_state: "revise",
      review_revision: 1,
      resource_plan: plan,
      resource_build: staleBuild,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/skills/creator/status") return jsonResponse({
        ...status,
        resource_authoring_enabled: true,
        resource_builder_available: true,
      });
      if (url === "/api/skills/creator/sessions/creator_1" && !init?.method) {
        return jsonResponse({ session: resourceSession, draft, resource_plan: plan, resource_build: resourceSession.resource_build });
      }
      if (url.endsWith("/resource-build") && init?.method === "POST") return jsonResponse({ resource_build: startedBuild });
      if (url.endsWith("/resource-builds/build-new/next") && init?.method === "POST") return jsonResponse({ resource_build: generatedBuild });
      return jsonResponse({ detail: `not found: ${url}` }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const view = render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes><Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("当前步骤 6/6")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "第 3 步：生成内容" }));
    await userEvent.click(screen.getByRole("button", { name: "按新方案开始生成" }));

    expect(await screen.findByRole("heading", { name: "逐项生成内容" })).toBeVisible();
    expect(screen.getByText("当前步骤 3/6")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "按你的反馈继续改进" })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/skills/creator/resource-builds/build-new/next",
      expect.objectContaining({ method: "POST" }),
    );

    resourceSession.resource_build = generatedBuild;
    view.unmount();
    render(
      <MemoryRouter initialEntries={["/skills/create/creator_1"]}>
        <Routes><Route element={<SkillCreatorStudioPage />} path="/skills/create/:sessionId" /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "逐项生成内容" })).toBeVisible();
    expect(screen.getByText("当前步骤 3/6")).toBeVisible();
  });
});
