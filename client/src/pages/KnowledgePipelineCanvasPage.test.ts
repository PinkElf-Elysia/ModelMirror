import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import KnowledgePipelineCanvasPage, {
  canvasDraftExecutionDisposition,
  chunkBudgetUnitLabel,
  chunkOverlapLabel,
  chunkerConfigForKind,
  headingOverlapReceiptSummary,
  nodeSummary,
  numericConfigValue,
  RunPanel,
} from "./KnowledgePipelineCanvasPage";

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("KnowledgePipelineCanvasPage 4A execution gate", () => {
  const draft = {
    index_schema_version: 3,
    retrieval_profile: { mode: "hybrid" },
    index_contract: {
      index_schema_version: 3,
      retrieval_mode: "hybrid",
    },
    content_index_contract: {
      status: "legacy_read_only",
      components: {
        chunker: "current",
        lexical: "legacy_read_only",
        parser: "legacy_read_only",
      },
    },
  };
  const vectorDraft = {
    ...draft,
    retrieval_profile: { mode: "vector" },
    index_contract: {
      index_schema_version: 3,
      retrieval_mode: "vector",
    },
  };

  function graphPayload(mode: "hybrid" | "vector", revision = 1) {
    return {
      kb_id: "kb-default",
      graph_id: "graph-default",
      graph_revision: revision,
      compiled_draft_version: revision,
      updated_at: revision,
      valid: true,
      issues: [],
      graph: {
        version: "knowledge-pipeline-graph-v1",
        kb_id: "kb-default",
        nodes: [{
          id: "retrieval",
          kind: "retrieval",
          title: mode === "vector" ? "向量检索" : "混合检索",
          position: { x: 0, y: 0 },
          config: { mode, top_k: 5 },
          enabled: true,
        }],
        edges: [],
      },
    };
  }

  function renderCanvasPage() {
    class TestResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    render(createElement(
      MemoryRouter,
      { initialEntries: ["/rag/kb-default/pipeline"] },
      createElement(
        Routes,
        null,
        createElement(Route, {
          path: "/rag/:kbId/pipeline",
          element: createElement(KnowledgePipelineCanvasPage),
        }),
      ),
    ));
  }

  it("blocks the default hybrid graph before the 4B fulltext contract", () => {
    expect(canvasDraftExecutionDisposition(draft)).toEqual({
      status: "blocked",
      canExecute: false,
      message: "4A 仅 vector diagnostic 可执行；全文合同待4B。",
    });
  });

  it("allows vector diagnostic execution without claiming a dual index", () => {
    const disposition = canvasDraftExecutionDisposition(draft, "vector");

    expect(disposition).toEqual({
      status: "diagnostic_only",
      canExecute: true,
      message: "当前仅允许 vector diagnostic 候选；不能首次激活或晋级。",
    });
    expect(disposition.message).not.toMatch(/双索引|全文/);
  });

  it("keeps the default hybrid execute button disabled without sending an execute POST", async () => {
    class TestResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/rag/knowledge_bases") {
        return jsonResponse({ knowledge_bases: [{ id: "kb-default", name: "默认库", document_count: 1 }] });
      }
      if (input.endsWith("/knowledge_bases/kb-default/documents")) {
        return jsonResponse({ documents: [{ id: "doc-1", filename: "source.md", size: 10 }] });
      }
      if (input.includes("/pipeline/graph?kb_id=kb-default")) {
        return jsonResponse({
          kb_id: "kb-default",
          graph_id: "graph-default",
          graph_revision: 1,
          compiled_draft_version: 1,
          updated_at: 1,
          valid: true,
          issues: [],
          graph: {
            version: "knowledge-pipeline-graph-v1",
            kb_id: "kb-default",
            nodes: [{
              id: "retrieval",
              kind: "retrieval",
              title: "混合检索",
              position: { x: 0, y: 0 },
              config: { mode: "hybrid", top_k: 5 },
              enabled: true,
            }],
            edges: [],
          },
        });
      }
      if (input.includes("/pipeline/draft?kb_id=kb-default")) {
        return jsonResponse({
          kb_id: "kb-default",
          draft_id: "draft-kb-default",
          version: 1,
          updated_at: 1,
          ...draft,
        });
      }
      if (input.includes("/pipeline/jobs?kb_id=kb-default")) {
        return jsonResponse({ jobs: [] });
      }
      if (input.includes("/pipeline/versions?kb_id=kb-default")) {
        return jsonResponse({ versions: [] });
      }
      return jsonResponse({ detail: "unexpected request" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(createElement(
      MemoryRouter,
      { initialEntries: ["/rag/kb-default/pipeline"] },
      createElement(
        Routes,
        null,
        createElement(Route, {
          path: "/rag/:kbId/pipeline",
          element: createElement(KnowledgePipelineCanvasPage),
        }),
      ),
    ));

    const status = await screen.findByRole("status", { name: "4A 执行范围" });
    expect(status).toHaveTextContent("4A 仅 vector diagnostic 可执行；全文合同待4B。");
    const executeButton = screen.getByRole("button", { name: "执行流水线" });
    await waitFor(() => {
      expect(executeButton).toBeDisabled();
      expect(executeButton).toHaveAttribute(
        "title",
        "4A 仅 vector diagnostic 可执行；全文合同待4B。",
      );
    });

    fireEvent.click(executeButton);
    expect(fetchMock.mock.calls.filter(([url, init]) => (
      String(url).includes("/pipeline/graph/kb-default/execute")
      && (init as RequestInit | undefined)?.method === "POST"
    ))).toHaveLength(0);
  });

  it("saves and refreshes a vector contract before sending the execute POST", async () => {
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/rag/knowledge_bases") {
        return jsonResponse({ knowledge_bases: [{ id: "kb-default", name: "默认库", document_count: 1 }] });
      }
      if (input.endsWith("/knowledge_bases/kb-default/documents")) {
        return jsonResponse({ documents: [{ id: "doc-1", filename: "source.md", size: 10 }] });
      }
      if (input.includes("/pipeline/graph?kb_id=kb-default")) {
        return jsonResponse(graphPayload("vector"));
      }
      if (input.endsWith("/pipeline/graph/kb-default") && init?.method === "PUT") {
        return jsonResponse(graphPayload("vector", 2));
      }
      if (input.includes("/pipeline/draft?kb_id=kb-default")) {
        return jsonResponse({
          kb_id: "kb-default",
          draft_id: "draft-kb-default",
          version: 2,
          updated_at: 2,
          ...vectorDraft,
        });
      }
      if (input.endsWith("/pipeline/graph/kb-default/execute") && init?.method === "POST") {
        return jsonResponse({ candidate_version: 2 });
      }
      if (input.includes("/pipeline/jobs?kb_id=kb-default")) {
        return jsonResponse({ jobs: [] });
      }
      if (input.includes("/pipeline/versions?kb_id=kb-default")) {
        return jsonResponse({ versions: [] });
      }
      return jsonResponse({ detail: "unexpected request" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderCanvasPage();

    const executeButton = await screen.findByRole("button", { name: "执行流水线" });
    await waitFor(() => expect(executeButton).toBeEnabled());
    fireEvent.click(executeButton);
    await screen.findByText("候选版本 v2 已进入执行队列。");

    const calls = fetchMock.mock.calls.map(([url, init]) => ({
      url: String(url),
      method: (init as RequestInit | undefined)?.method ?? "GET",
    }));
    const saveIndex = calls.findIndex(({ url, method }) => (
      url.endsWith("/pipeline/graph/kb-default") && method === "PUT"
    ));
    const refreshIndex = calls.findIndex(({ url }, index) => (
      index > saveIndex && url.includes("/pipeline/draft?kb_id=kb-default")
    ));
    const executeIndex = calls.findIndex(({ url, method }) => (
      url.endsWith("/pipeline/graph/kb-default/execute") && method === "POST"
    ));
    expect(saveIndex).toBeGreaterThanOrEqual(0);
    expect(refreshIndex).toBeGreaterThan(saveIndex);
    expect(executeIndex).toBeGreaterThan(refreshIndex);
  });

  it("does not execute when the post-save vector contract refresh fails", async () => {
    let draftReads = 0;
    const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/rag/knowledge_bases") {
        return jsonResponse({ knowledge_bases: [{ id: "kb-default", name: "默认库", document_count: 1 }] });
      }
      if (input.endsWith("/knowledge_bases/kb-default/documents")) {
        return jsonResponse({ documents: [{ id: "doc-1", filename: "source.md", size: 10 }] });
      }
      if (input.includes("/pipeline/graph?kb_id=kb-default")) {
        return jsonResponse(graphPayload("vector"));
      }
      if (input.endsWith("/pipeline/graph/kb-default") && init?.method === "PUT") {
        return jsonResponse(graphPayload("vector", 2));
      }
      if (input.includes("/pipeline/draft?kb_id=kb-default")) {
        draftReads += 1;
        if (draftReads > 1) {
          return jsonResponse({ detail: "draft unavailable" }, 503);
        }
        return jsonResponse({
          kb_id: "kb-default",
          draft_id: "draft-kb-default",
          version: 1,
          updated_at: 1,
          ...vectorDraft,
        });
      }
      if (input.includes("/pipeline/jobs?kb_id=kb-default")) {
        return jsonResponse({ jobs: [] });
      }
      if (input.includes("/pipeline/versions?kb_id=kb-default")) {
        return jsonResponse({ versions: [] });
      }
      return jsonResponse({ detail: "unexpected request" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    renderCanvasPage();

    const executeButton = await screen.findByRole("button", { name: "执行流水线" });
    await waitFor(() => expect(executeButton).toBeEnabled());
    fireEvent.click(executeButton);
    await screen.findByText("图已保存，但执行合同刷新失败；执行保持禁用。");

    expect(fetchMock.mock.calls.filter(([url, init]) => (
      String(url).endsWith("/pipeline/graph/kb-default/execute")
      && (init as RequestInit | undefined)?.method === "POST"
    ))).toHaveLength(0);
  });
});

describe("KnowledgePipelineCanvasPage content-index activation guard", () => {
  it("blocks first activation from version evidence even when its source job is outside the job page", () => {
    const onActivate = vi.fn();
    render(createElement(RunPanel, {
      jobs: [],
      versions: [{
        version_id: "legacy-first-activation",
        version: 21,
        status: "ready",
        active: false,
        chunk_count: 1,
        created_at: 1,
        activated_at: null,
        index_schema_version: 3,
        content_index_contract: { status: "legacy_read_only" },
      }],
      onActivate,
    }));

    expect(screen.getByRole("button", { name: "激活" })).toBeDisabled();
    expect(screen.getByText(/内容合同：历史\/未完整内容合同/)).toBeVisible();
    expect(screen.getByText(/只能预览诊断，不能首次激活/)).toBeVisible();
    expect(onActivate).not.toHaveBeenCalled();
  });

  it("keeps a previously activated legacy version available as a rollback target", () => {
    const onActivate = vi.fn();
    render(createElement(RunPanel, {
      jobs: [],
      versions: [{
        version_id: "legacy-rollback",
        version: 2,
        status: "ready",
        active: false,
        chunk_count: 1,
        created_at: 1,
        activated_at: 1,
        index_schema_version: 2,
        content_index_contract: { status: "legacy_read_only" },
      }],
      onActivate,
    }));

    const rollbackButton = screen.getByRole("button", { name: "回滚/切换" });
    expect(rollbackButton).toBeEnabled();
    expect(screen.getByText(/该版本曾激活，仅可作为回滚\/切换目标/)).toBeVisible();
    fireEvent.click(rollbackButton);
    expect(onActivate).toHaveBeenCalledOnce();
    expect(onActivate).toHaveBeenCalledWith("legacy-rollback");
  });
});

describe("KnowledgePipelineCanvasPage chunk budget units", () => {
  it("labels current token-aware strategies as estimated tokens", () => {
    expect(chunkBudgetUnitLabel("recursive_estimated_token")).toBe("估算 Token");
    expect(chunkBudgetUnitLabel("parent_child_estimated_token")).toBe("估算 Token");
    expect(nodeSummary({
      kind: "recursive_chunker",
      title: "递归分块",
      enabled: true,
      config: {
        strategy: "recursive_estimated_token",
        chunk_size: 500,
        chunk_overlap: 50,
      },
    })).toBe("500 估算 Token · overlap 50 估算 Token");
    expect(nodeSummary({
      kind: "parent_child_chunker",
      title: "父子分块",
      enabled: true,
      config: {
        strategy: "parent_child_estimated_token",
        parent_chunk_size: 1500,
        child_chunk_size: 400,
      },
    })).toBe("parent 1500 估算 Token · child 400 估算 Token");
  });

  it("labels legacy character strategies as historical read-only", () => {
    expect(chunkBudgetUnitLabel("recursive_character")).toBe("字符，历史只读");
    expect(chunkBudgetUnitLabel("parent_child")).toBe("字符，历史只读");
  });

  it("describes estimated-token overlap as a total target with a structural-prefix floor", () => {
    expect(chunkOverlapLabel("recursive_estimated_token")).toBe("目标总重叠（估算 Token）");
    expect(chunkOverlapLabel("parent_child_estimated_token", "父段")).toBe("父段目标总重叠（估算 Token）");
    expect(chunkOverlapLabel("recursive_character")).toBe("重叠（字符，历史只读）");
  });

  it("shows only a validated dynamic structural-prefix floor summary", () => {
    expect(headingOverlapReceiptSummary({
      heading_overlap_policy: "structural_prefix_floor_v1",
      raw_candidate_count: 4,
      prefix_exceeds_configured_overlap_count: 2,
      max_heading_prefix_tokens: 26,
      max_effective_index_overlap_budget_tokens: 26,
      max_effective_context_overlap_budget_tokens: 30,
    })).toContain("2 个有效内容单元");
    expect(headingOverlapReceiptSummary({
      heading_overlap_policy: "structural_prefix_floor_v1",
      raw_candidate_count: 4,
      prefix_exceeds_configured_overlap_count: 0,
      max_heading_prefix_tokens: 5,
      max_effective_index_overlap_budget_tokens: 20,
      max_effective_context_overlap_budget_tokens: 20,
    })).toBeNull();
    expect(headingOverlapReceiptSummary({
      prefix_exceeds_configured_overlap_count: "2",
    })).toBeNull();
    expect(headingOverlapReceiptSummary({
      heading_overlap_policy: "structural_prefix_floor_v1",
      raw_candidate_count: 1,
      prefix_exceeds_configured_overlap_count: 2,
      max_heading_prefix_tokens: 26,
      max_effective_index_overlap_budget_tokens: 20,
      max_effective_context_overlap_budget_tokens: 30,
    })).toBeNull();
  });

  it("switches chunker families into the current estimated-token contract", () => {
    const recursive = chunkerConfigForKind("recursive_chunker", {
      strategy: "parent_child_estimated_token",
      chunk_overlap: 0,
    });
    expect(recursive).toMatchObject({
      strategy: "recursive_estimated_token",
      size_unit: "estimated_tokens",
      token_estimator: "mixed_cjk_latin_v1",
      chunk_contract_version: "rag-chunker-estimated-token-v1",
      chunk_overlap: 0,
    });

    const parentChild = chunkerConfigForKind("parent_child_chunker", recursive);
    expect(parentChild).toMatchObject({
      strategy: "parent_child_estimated_token",
      size_unit: "estimated_tokens",
      token_estimator: "mixed_cjk_latin_v1",
      chunk_contract_version: "rag-chunker-estimated-token-v1",
      chunk_overlap: 0,
    });
  });

  it("resets all six budgets when either legacy character strategy upgrades", () => {
    const staleBudgets = {
      chunk_size: 1,
      chunk_overlap: 2,
      parent_chunk_size: 3,
      parent_chunk_overlap: 4,
      child_chunk_size: 5,
      child_chunk_overlap: 6,
    };
    const expectedBudgets = {
      chunk_size: 500,
      chunk_overlap: 50,
      parent_chunk_size: 1500,
      parent_chunk_overlap: 100,
      child_chunk_size: 400,
      child_chunk_overlap: 50,
    };

    expect(chunkerConfigForKind("recursive_chunker", {
      ...staleBudgets,
      strategy: "recursive_character",
    })).toMatchObject(expectedBudgets);
    expect(chunkerConfigForKind("parent_child_chunker", {
      ...staleBudgets,
      strategy: "parent_child",
    })).toMatchObject(expectedBudgets);
  });

  it("preserves an explicit zero overlap instead of displaying a default", () => {
    expect(numericConfigValue(0, 50)).toBe(0);
    expect(numericConfigValue(undefined, 50)).toBe(50);
    expect(numericConfigValue("invalid", 50)).toBe(50);
  });
});
