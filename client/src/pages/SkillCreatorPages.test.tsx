import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  SkillCreatorDraft,
  SkillCreatorProposal,
  SkillCreatorSession,
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
    fireEvent.change(screen.getByLabelText("想沉淀什么做法"), { target: { value: "新目标" } });
    await userEvent.click(screen.getByRole("button", { name: /进入工作台/ }));

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

    expect(await screen.findByRole("heading", { name: "分析竞品 PDF 并保留页码" })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认素材" }));
    expect(screen.getByText("模型网关未配置 Key。")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成可评测初稿" })).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Skill ID"), "compare-pdf");
    await userEvent.type(
      screen.getByLabelText("能力、触发场景与不适用边界"),
      "比较 PDF 并保留证据页码。用于竞品分析，不用于仅做文字转换。",
    );
    expect(screen.getByRole("button", { name: "创建结构化手工模板" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "确认无需运行素材" }));
    expect(await screen.findByText("已确认无需运行素材")).toBeVisible();
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

    await screen.findByRole("heading", { name: "分析竞品 PDF 并保留页码" });
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认素材" }));

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

    await screen.findByRole("heading", { name: "分析竞品 PDF 并保留页码" });
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认素材" }));
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
    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认素材" }));
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

    await userEvent.click(screen.getByRole("button", { name: "第 2 步：确认素材" }));
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeVisible();
    expect(confirm).toHaveBeenCalledTimes(2);

    await userEvent.click(screen.getByRole("link", { name: "Creator 会话" }));
    expect(screen.queryByText("Creator session list")).not.toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(3);

    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByRole("link", { name: "Creator 会话" }));
    expect(await screen.findByText("Creator session list")).toBeVisible();
  });
});
