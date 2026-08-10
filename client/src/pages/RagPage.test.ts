import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import RagPage, {
  formatPipelineSourceLocation,
  formatRagSourceLocation,
  formatRagSheetLocation,
  getRagOfficeWarningSummary,
  isRagFileSelectionDisabled,
  ragUploadStatusLabel,
  readError,
} from "./RagPage";

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const officeCapabilities = {
  version: "modelmirror-file-capabilities-v2",
  registry_version: "modelmirror-file-formats-v5",
  requested_purpose: null,
  requested_model_id: null,
  model_specific: false,
  capabilities: [
    {
      purpose: "rag",
      input_kind: "document",
      families: ["document"],
      max_bytes_per_file: 10 * 1024 * 1024,
      max_files_per_request: 1,
      max_total_bytes_per_request: null,
      size_measure: "binary",
      transport: "multipart",
      retention: "persistent",
      support_level: "converted",
      interaction_status: "ready",
      parser_id: "office-parser-mcp",
      ui_entrypoint: "/rag",
      status_reason: null,
      handling_options: [],
      analysis_options: [],
      formats: [
        {
          format_id: "docx",
          family: "document",
          extensions: [".docx"],
          media_types: [
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          ],
          interaction_status: "ready",
          status_reason: null,
        },
        {
          format_id: "pptx",
          family: "document",
          extensions: [".pptx"],
          media_types: [
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          ],
          interaction_status: "ready",
          status_reason: null,
        },
      ],
    },
  ],
};

const knowledgeBases = {
  knowledge_bases: [
    {
      id: "kb_default",
      name: "默认资料库",
      document_count: 0,
      created_at: 1,
      updated_at: 1,
    },
    {
      id: "kb_office",
      name: "Office 验收库",
      document_count: 0,
      created_at: 1,
      updated_at: 1,
    },
  ],
};

function renderRagPageWithOfficeUploadError(
  status: number,
  code: string,
  message: string,
  pendingDeletions: Array<{
    document_id: string;
    filename: string;
    status: "deleting" | "cleanup_pending" | "failed";
    error_code: string | null;
    requested_at: number;
  }> = [],
  options: {
    knowledgeBaseResponses?: Array<typeof knowledgeBases>;
    deleteResponses?: Array<{ status: number; payload: unknown }>;
  } = {},
) {
  let knowledgeBaseRequest = 0;
  let deleteRequest = 0;
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/rag/knowledge_bases") {
        const responses = options.knowledgeBaseResponses ?? [knowledgeBases];
        const payload = responses[Math.min(knowledgeBaseRequest, responses.length - 1)];
        knowledgeBaseRequest += 1;
        return jsonResponse(payload);
      }
      if (url === "/api/files/capabilities") {
        return jsonResponse(officeCapabilities);
      }
      if (url === "/api/rag/retrieval-capabilities") {
        return jsonResponse({
          version: "v1",
          index_schema_version: 1,
          modes: ["hybrid"],
          vector: { available: true, backend: "local" },
          fulltext: { available: true, backend: "local" },
          embedding: {
            provider: "local",
            model: "test",
            dimension: 8,
            degraded: false,
          },
          rerank: {
            api_configured: false,
            llm_configured: false,
            api_model: "",
            llm_model: "",
          },
        });
      }
      if (url === "/api/rag/processor-capabilities") {
        return jsonResponse({
          version: "v1",
          modes: ["general"],
          failure_policies: ["continue_on_error"],
          supported_extensions: [".docx", ".pptx"],
          block_types: ["paragraph", "table"],
          llm_configured: false,
          model_label: "",
          generation_targets: [],
          limits: {
            max_generated_items: 20,
            preview_items: 10,
            preview_text_characters: 10000,
          },
        });
      }
      if (
        url === "/api/rag/knowledge_bases/kb_office/documents" &&
        init?.method === "POST"
      ) {
        return jsonResponse({ detail: { code, message } }, status);
      }
      if (
        url === "/api/rag/knowledge_bases/kb_office" &&
        init?.method === "DELETE"
      ) {
        const responses = options.deleteResponses ?? [
          { status: 200, payload: { ok: true } },
        ];
        const selected = responses[Math.min(deleteRequest, responses.length - 1)];
        deleteRequest += 1;
        return jsonResponse(selected.payload, selected.status);
      }
      if (url.endsWith("/pending-deletions")) {
        const knowledgeBaseId = url.split("/").at(-2) ?? "";
        return jsonResponse({
          tenant_id: "local",
          knowledge_base_id: knowledgeBaseId,
          deletions:
            knowledgeBaseId === "kb_office" ? pendingDeletions : [],
        });
      }
      if (url.endsWith("/documents")) {
        return jsonResponse({ documents: [] });
      }
      throw new Error(`Unexpected request: ${url}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return {
    ...render(
      createElement(
        MemoryRouter,
        null,
        createElement(RagPage),
      ),
    ),
    fetchMock,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("RAG structured API errors", () => {
  it("reads only supported public message fields in their contract order", async () => {
    await expect(
      readError(
        jsonResponse(
          {
            detail: { code: "internal_code", message: "可安全显示的详情" },
            error: "不应覆盖详情",
            message: "也不应覆盖详情",
          },
          422,
        ),
      ),
    ).resolves.toBe("可安全显示的详情");
    await expect(
      readError(
        jsonResponse(
          { detail: "字符串详情", error: "不应覆盖详情" },
          400,
        ),
      ),
    ).resolves.toBe("字符串详情");
    await expect(
      readError(jsonResponse({ error: "顶层错误", message: "顶层消息" }, 500)),
    ).resolves.toBe("顶层错误");
    await expect(
      readError(jsonResponse({ message: "顶层消息" }, 500)),
    ).resolves.toBe("顶层消息");
  });

  it("falls back without stringifying objects or exposing internal fields", async () => {
    await expect(
      readError(
        jsonResponse(
          {
            detail: { code: "office_parse_failed", trace_id: "secret-trace" },
            error: { message: "内部错误" },
            message: 503,
          },
          503,
        ),
      ),
    ).resolves.toBe("请求失败：503");
    await expect(readError(jsonResponse(["内部数组"], 502))).resolves.toBe(
      "请求失败：502",
    );
  });

  it.each([
    {
      status: 422,
      code: "office_parse_failed",
      message: "Office 文件无法安全解析。",
      fileName: "损坏文档.docx",
      mediaType:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    {
      status: 503,
      code: "office_parser_unavailable",
      message: "Office 隔离解析暂不可用，请稍后重试。",
      fileName: "验收演示.pptx",
      mediaType:
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    },
  ])(
    "keeps the selected knowledge base and displays the $status Office message",
    async ({ status, code, message, fileName, mediaType }) => {
      const { container, fetchMock } = renderRagPageWithOfficeUploadError(
        status,
        code,
        message,
      );

      const officeKnowledgeBase = await screen.findByRole("button", {
        name: /Office 验收库/,
      });
      fireEvent.click(officeKnowledgeBase);
      expect(
        await screen.findByRole("heading", {
          level: 2,
          name: "Office 验收库",
        }),
      ).toBeVisible();
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "上传文档" })).toBeEnabled();
      });

      const input = container.querySelector<HTMLInputElement>(
        'input[type="file"]',
      );
      expect(input).not.toBeNull();
      fireEvent.change(input!, {
        target: {
          files: [new File(["office"], fileName, { type: mediaType })],
        },
      });

      expect(await screen.findByText(message)).toBeVisible();
      expect(
        screen.getByRole("heading", { level: 2, name: "Office 验收库" }),
      ).toBeVisible();
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "上传文档" })).toBeEnabled();
      });
      expect(container.textContent).not.toContain(code);
      expect(container.textContent).not.toContain("[object Object]");
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/rag/knowledge_bases/kb_office/documents",
        expect.objectContaining({ method: "POST" }),
      );
    },
  );
});

describe("RAG knowledge-base cascade deletion", () => {
  it("keeps cleanup-pending knowledge bases visible and retries before reporting deletion", async () => {
    const pendingKnowledgeBases = {
      knowledge_bases: [
        knowledgeBases.knowledge_bases[0],
        {
          ...knowledgeBases.knowledge_bases[1],
          document_count: 0,
          deletion_status: "cleanup_pending",
          deletion_error_code: "file_asset_cleanup_pending",
        },
      ],
    };
    const completedKnowledgeBases = {
      knowledge_bases: [knowledgeBases.knowledge_bases[0]],
    };
    const confirmMock = vi.fn((_message?: string) => true);
    vi.stubGlobal("confirm", confirmMock);
    const { fetchMock } = renderRagPageWithOfficeUploadError(
      422,
      "unused",
      "unused",
      [],
      {
        knowledgeBaseResponses: [
          knowledgeBases,
          pendingKnowledgeBases,
          completedKnowledgeBases,
        ],
        deleteResponses: [
          {
            status: 409,
            payload: {
              detail: {
                code: "rag_knowledge_base_cleanup_pending",
                message: "cleanup pending",
              },
            },
          },
          { status: 200, payload: { ok: true } },
        ],
      },
    );

    const officeCard = (await screen.findByText("Office 验收库")).closest("article");
    expect(officeCard).not.toBeNull();
    fireEvent.click(
      within(officeCard!).getByRole("button", {
        name: "彻底删除资料库",
      }),
    );

    expect(
      await screen.findByText(/已隔离，仍有文件或派生物等待清理/),
    ).toBeVisible();
    const retryButton = await screen.findByRole("button", {
      name: "重试彻底清理",
    });
    expect(retryButton).toBeVisible();
    await waitFor(() => expect(retryButton).toHaveFocus());
    const pendingCard = screen.getByText("Office 验收库").closest("article");
    expect(pendingCard).not.toBeNull();
    expect(within(pendingCard!).queryByRole("button", { name: /打开/ })).toBeNull();
    expect(String(confirmMock.mock.calls[0]?.[0])).toContain("不可撤销");
    expect(String(confirmMock.mock.calls[0]?.[0])).toContain("共享资产会保留");

    fireEvent.click(retryButton);

    await waitFor(() => {
      expect(screen.queryByText("Office 验收库")).toBeNull();
    });
    expect(await screen.findByText("知识库「Office 验收库」已删除。")).toBeVisible();
    expect(String(confirmMock.mock.calls[1]?.[0])).toContain("继续重试彻底清理");
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "新建知识库" })).toHaveFocus();
    });
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input) === "/api/rag/knowledge_bases/kb_office" &&
          (init as RequestInit | undefined)?.method === "DELETE",
      ),
    ).toHaveLength(2);
  });
});

describe("RAG structured source labels", () => {
  it("uses stable per-file upload queue labels", () => {
    expect(ragUploadStatusLabel("queued")).toBe("等待上传");
    expect(ragUploadStatusLabel("uploading")).toBe("正在入库");
    expect(ragUploadStatusLabel("succeeded")).toBe("已完成");
    expect(ragUploadStatusLabel("failed")).toBe("失败");
    expect(ragUploadStatusLabel("cancel_requested")).toBe(
      "已请求取消，请刷新确认",
    );
    expect(ragUploadStatusLabel("cancelled")).toBe("已取消");
  });

  it("restores KB-scoped pending deletion retries after switching libraries", async () => {
    const pendingDeletion = {
      document_id: "doc_pending_1",
      filename: "待清理文档.pdf",
      status: "cleanup_pending" as const,
      error_code: "file_asset_cleanup_pending",
      requested_at: 1_786_000_000,
    };
    const { fetchMock } = renderRagPageWithOfficeUploadError(
      422,
      "office_parse_failed",
      "Office 文件无法安全解析。",
      [pendingDeletion],
    );

    fireEvent.click(
      await screen.findByRole("button", { name: /Office 验收库/ }),
    );
    expect(
      await screen.findByRole("region", { name: "待重试的文档清理" }),
    ).toBeVisible();
    expect(screen.getByText("待清理文档.pdf")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/rag/knowledge_bases/kb_office/pending-deletions",
    );
  });

  it("queues multiple files independently and keeps failed items retryable", async () => {
    const { container, fetchMock } = renderRagPageWithOfficeUploadError(
      422,
      "office_parse_failed",
      "Office 文件无法安全解析。",
    );
    fireEvent.click(
      await screen.findByRole("button", { name: /Office 验收库/ }),
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "上传文档" })).toBeEnabled();
    });
    const input = container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    expect(input!.multiple).toBe(true);

    fireEvent.change(input!, {
      target: {
        files: [
          new File(["first"], "第一份.docx", {
            type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          }),
          new File(["second"], "第二份.pptx", {
            type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          }),
        ],
      },
    });

    expect(await screen.findByRole("region", { name: "上传队列" })).toBeVisible();
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "重试" })).toHaveLength(2);
    });
    const uploadCalls = fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input) === "/api/rag/knowledge_bases/kb_office/documents" &&
        init?.method === "POST",
    );
    expect(uploadCalls).toHaveLength(2);
  });

  it("formats XLSX sheet and cell-range metadata without inventing a page", () => {
    expect(
      formatRagSheetLocation({ sheet: "季度预算", row_range: "A1:B12" }),
    ).toBe("工作表「季度预算」· A1:B12");
    expect(formatRagSheetLocation({ sheet: "季度预算" })).toBe(
      "工作表「季度预算」",
    );
    expect(formatRagSheetLocation({ row_range: "A1:B12" })).toBe("范围 A1:B12");
    expect(formatRagSheetLocation({ sheet: 123, row_range: null })).toBe("");
  });

  it("reads pipeline source location from the real top-level response shape", () => {
    expect(
      formatPipelineSourceLocation({ sheet: "季度预算", row_range: "A1:B12" }),
    ).toBe("工作表「季度预算」· A1:B12");
    expect(
      formatPipelineSourceLocation({
        slide: 7,
        heading_path: ["季度回顾", "发布摘要"],
      }),
    ).toBe("第 7 张幻灯片 · 章节：季度回顾 / 发布摘要");
    expect(
      formatPipelineSourceLocation({
        page_number: 2,
        heading_path: ["安装"],
      }),
    ).toBe("第 2 页 · 章节：安装");
  });

  it("reads processor metadata without treating slides as pages", () => {
    expect(
      formatRagSourceLocation({
        slide: 3,
        page_number: null,
        heading_path: ["演示文稿", 12, "路线图"],
      }),
    ).toBe("第 3 张幻灯片 · 章节：演示文稿 / 路线图");
  });

  it("blocks a second upload while an XLSX destination is unresolved", () => {
    expect(
      isRagFileSelectionDisabled({
        capabilityReady: true,
        isUploading: false,
        hasPendingXlsx: true,
      }),
    ).toBe(true);
    expect(
      isRagFileSelectionDisabled({
        capabilityReady: true,
        isUploading: false,
        hasPendingXlsx: false,
      }),
    ).toBe(false);
  });
});

describe("RAG Office document warnings", () => {
  it("reads warnings from the real DocumentPayload response shape", () => {
    const payload = {
      id: "doc_office_1",
      kb_id: "kb_1",
      filename: "季度复盘.docx",
      size: 4096,
      chunk_count: 8,
      content_type:
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      ingestion_status: "indexed_legacy",
      visual_candidate: false,
      warnings: [
        "Document images are represented by inert placeholders; no vision model was called.",
        "Tracked revisions were detected; inserted, deleted, or moved revision content may not be extracted completely.",
      ],
      created_at: 1_786_000_000,
    };

    expect(getRagOfficeWarningSummary(payload)).toEqual({
      items: [
        "文档内图片仅以占位符保留，本次未调用视觉模型。",
        "检测到修订记录，插入、删除或移动的修订内容可能未完整提取。",
      ],
      hiddenCount: 0,
    });
  });

  it("keeps warnings bounded and leaves non-Office documents unchanged", () => {
    const summary = getRagOfficeWarningSummary({
      filename: "发布演示.pptx",
      content_type:
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      warnings: [
        "Slide images are represented by inert placeholders; no vision model was called.",
        "第一项额外提示",
        "第二项额外提示",
        "第三项额外提示",
        "x".repeat(500),
      ],
    });
    expect(summary.items).toHaveLength(3);
    expect(summary.items[0]).toBe(
      "幻灯片内图片仅以占位符保留，本次未调用视觉模型。",
    );
    expect(summary.items.every((warning) => warning.length <= 180)).toBe(true);
    expect(summary.hiddenCount).toBe(2);

    expect(
      getRagOfficeWarningSummary({
        filename: "普通文本.txt",
        content_type: "text/plain",
        warnings: ["不会改变非 Office 文档行布局"],
      }),
    ).toEqual({ items: [], hiddenCount: 0 });
  });
});
