import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatFileComposer, {
  computeChatFileDrawerRect,
  deriveReadyChatFileFormats,
  formatFileFormatLabel,
  formatPreviewSectionSource,
  formatPreviewWarning,
  validateChatFileBatch,
  type ChatFileComposerState,
} from "./ChatFileComposer";

function response(payload: unknown, status = 200) {
  return new Response(status === 204 ? null : JSON.stringify(payload), {
    status,
    headers: status === 204 ? undefined : { "Content-Type": "application/json" },
  });
}

function capability(interactionStatus: "ready" | "disabled" = "ready") {
  return {
    version: "modelmirror-file-capabilities-v1",
    registry_version: "modelmirror-file-formats-v4",
    requested_purpose: "chat",
    requested_model_id: "openai/file-model",
    model_specific: true,
    capabilities: [
      {
        purpose: "chat",
        input_kind: "document",
        families: ["document"],
        max_bytes_per_file: 10 * 1024 * 1024,
        max_files_per_request: 5,
        max_total_bytes_per_request: 25 * 1024 * 1024,
        size_measure: "binary",
        transport: "multipart",
        retention: "temporary",
        support_level: "converted",
        interaction_status: interactionStatus,
        parser_id: "chat.local_document_parser",
        ui_entrypoint: "/chat/:modelId",
        status_reason:
          interactionStatus === "disabled" ? "文件输入功能开关未开启。" : null,
        handling_options: [
          {
            handling: "extract",
            format_ids: ["markdown", "pdf", "plain_text", "xlsx"],
            support_level: "converted",
            interaction_status: "ready",
            status_reason: null,
          },
          {
            handling: "native",
            format_ids: ["pdf"],
            support_level: "native",
            interaction_status: "ready",
            status_reason: null,
          },
        ],
        formats: [
          {
            format_id: "plain_text",
            family: "document",
            extensions: [".txt"],
            media_types: ["text/plain"],
            interaction_status: "ready",
            status_reason: null,
          },
          {
            format_id: "markdown",
            family: "document",
            extensions: [".md", ".markdown"],
            media_types: ["text/markdown"],
            interaction_status: "ready",
            status_reason: null,
          },
          {
            format_id: "pdf",
            family: "document",
            extensions: [".pdf"],
            media_types: ["application/pdf"],
            interaction_status: "ready",
            status_reason: null,
          },
          {
            format_id: "xlsx",
            family: "document",
            extensions: [".xlsx"],
            media_types: [
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ],
            interaction_status: "ready",
            status_reason: null,
          },
          {
            format_id: "docx",
            family: "document",
            extensions: [".docx"],
            media_types: [
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ],
            interaction_status: "planned",
            status_reason: "隔离 Office 解析桥尚未通过验收。",
          },
        ],
      },
    ],
  };
}

function asset(name = "brief.md") {
  return {
    asset_id: "file_12345678901234567890123456789012",
    purpose: "chat",
    scope_id: "chat-test-scope",
    display_name: name,
    format: "markdown",
    media_type: "text/markdown",
    byte_size: 12,
    status: "ready",
    expires_at: "2026-08-08T00:00:00Z",
    created_at: "2026-08-07T00:00:00Z",
    updated_at: "2026-08-07T00:00:00Z",
  };
}

function preview() {
  return {
    asset_id: "file_12345678901234567890123456789012",
    artifact_id: "artifact_123456789012345678901234",
    artifact_expires_at: "2026-08-08T00:00:00Z",
    format: "markdown",
    title: "brief.md",
    sections: [
      {
        text: "A concise local preview.",
        page: null,
        line_range: "1-1",
      },
    ],
    warnings: [],
    extracted_chars: 24,
    truncated: false,
  };
}

function renderComposer(
  props: Partial<React.ComponentProps<typeof ChatFileComposer>> = {},
) {
  const onError = props.onError ?? vi.fn();
  const onStateChange = props.onStateChange ?? vi.fn();
  const baseProps: React.ComponentProps<typeof ChatFileComposer> = {
    modelId: "openai/file-model",
    scopeId: "chat-test-scope",
    isAutoRoute: false,
    disabled: false,
    knowledgeBaseSelected: false,
    drawerHost: document.body,
    resetVersion: 0,
    discardVersion: 0,
    onError,
    onStateChange,
    ...props,
  };
  const view = render(
    <MemoryRouter>
      <ChatFileComposer {...baseProps} />
    </MemoryRouter>,
  );
  return { ...view, baseProps, onError, onStateChange };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ChatFileComposer", () => {
  it("derives extended Chat formats and source labels only from registry data", () => {
    const formats = [
      {
        format_id: "json",
        family: "document" as const,
        extensions: [".json"],
        media_types: ["application/json"],
        interaction_status: "ready" as const,
        status_reason: null,
      },
      {
        format_id: "source_code",
        family: "document" as const,
        extensions: [".ts", ".tsx"],
        media_types: ["text/plain"],
        interaction_status: "ready" as const,
        status_reason: null,
      },
      {
        format_id: "log",
        family: "document" as const,
        extensions: [".log"],
        media_types: ["text/plain"],
        interaction_status: "planned" as const,
        status_reason: "日志解析正在核验。",
      },
    ];

    expect(
      deriveReadyChatFileFormats(formats, ["json", "source_code", "log"]).map(
        (format) => format.format_id,
      ),
    ).toEqual(["json", "source_code"]);
    expect(formatFileFormatLabel("source_code")).toBe("源码");
    expect(formatFileFormatLabel("docx")).toBe("Word 文档");
    expect(formatFileFormatLabel("pptx")).toBe("PowerPoint 演示文稿");
    expect(
      validateChatFileBatch({
        currentSizes: [],
        files: [{ name: "config.json", size: 10 }],
        capabilityReady: true,
        acceptedExtensions: formats.flatMap((format) => format.extensions),
        knowledgeBaseSelected: false,
      }),
    ).toBe("");
    expect(
      validateChatFileBatch({
        currentSizes: [],
        files: [{ name: "script.exe", size: 10 }],
        capabilityReady: true,
        acceptedExtensions: formats.flatMap((format) => format.extensions),
        knowledgeBaseSelected: false,
      }),
    ).toContain("能力清单");
    expect(
      formatPreviewSectionSource({
        text: "caption",
        page: null,
        line_range: null,
        time_range: "00:00:01.000 --> 00:00:03.000",
      }),
    ).toBe("时间 00:00:01.000 --> 00:00:03.000");
    expect(
      formatPreviewSectionSource({
        text: "heading",
        page: null,
        line_range: null,
        heading_path: ["指南", "安装"],
      }),
    ).toBe("章节：指南 / 安装");
    expect(
      formatPreviewSectionSource({
        text: "季度收入",
        page: null,
        line_range: null,
        sheet: "华东区",
        row_range: "A1:C8",
      }),
    ).toBe("工作表「华东区」· A1:C8");
    expect(
      formatPreviewSectionSource({
        text: "发布摘要",
        page: null,
        slide: 4,
        line_range: null,
        heading_path: ["季度回顾", "发布摘要"],
      }),
    ).toBe("第 4 张幻灯片 · 章节：季度回顾 / 发布摘要");
    expect(
      formatPreviewWarning(
        "Slide images are represented by inert placeholders; no vision model was called.",
      ),
    ).toBe("文档内图片仅以占位符出现在本地提取结果中，没有调用视觉模型。");
    expect(
      formatPreviewWarning(
        "Tracked revisions were detected; inserted content may be incomplete.",
      ),
    ).toContain("本地提取");
  });

  it("keeps an off-screen mobile host above the input boundary", () => {
    expect(
      computeChatFileDrawerRect(
        { left: 0, top: -216, width: 390, height: 245 },
        { top: 460 },
        390,
        844,
      ),
    ).toEqual({ left: 0, top: 0, width: 390, height: 460 });
  });

  it("fails closed for capability, count, per-file, total, RAG and media limits", () => {
    const file = (name: string, size: number) => ({ name, size });
    const acceptedExtensions = [".txt", ".md", ".pdf"];
    expect(
      validateChatFileBatch({
        currentSizes: [],
        files: [file("a.txt", 1)],
        capabilityReady: false,
        acceptedExtensions,
        knowledgeBaseSelected: false,
      }),
    ).toContain("未启用");
    expect(
      validateChatFileBatch({
        currentSizes: [1, 1, 1, 1, 1],
        files: [file("six.txt", 1)],
        capabilityReady: true,
        acceptedExtensions,
        knowledgeBaseSelected: false,
      }),
    ).toContain("最多添加 5 个");
    expect(
      validateChatFileBatch({
        currentSizes: [],
        files: [file("large.pdf", 10 * 1024 * 1024 + 1)],
        capabilityReady: true,
        acceptedExtensions,
        knowledgeBaseSelected: false,
      }),
    ).toContain("10 MiB");
    expect(
      validateChatFileBatch({
        currentSizes: [20 * 1024 * 1024],
        files: [file("total.md", 5 * 1024 * 1024 + 1)],
        capabilityReady: true,
        acceptedExtensions,
        knowledgeBaseSelected: false,
      }),
    ).toContain("25 MiB");
    expect(
      validateChatFileBatch({
        currentSizes: [],
        files: [file("a.txt", 1)],
        capabilityReady: true,
        acceptedExtensions,
        knowledgeBaseSelected: true,
      }),
    ).toContain("取消知识库");
    expect(
      validateChatFileBatch({
        currentSizes: [],
        files: [file("a.txt", 1)],
        capabilityReady: true,
        acceptedExtensions,
        mediaBlockedReason: "请先移除图片。",
        knowledgeBaseSelected: false,
      }),
    ).toBe("请先移除图片。");
  });

  it("does not upload when the Chat document capability is disabled", async () => {
    const fetchMock = vi.fn(async () => response(capability("disabled")));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = renderComposer();

    expect(await screen.findByRole("button", { name: "添加文件" })).toBeDisabled();
    expect(screen.getByText("文件未启用")).toBeVisible();
    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: { files: [new File(["x"], "brief.md", { type: "text/markdown" })] },
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("keeps planned Office formats out of the picker until readiness flips", async () => {
    const plannedFetch = vi.fn(async () => response(capability()));
    vi.stubGlobal("fetch", plannedFetch);
    const plannedView = renderComposer();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "添加文件" })).toBeEnabled(),
    );
    expect(
      plannedView.container.querySelector('input[type="file"]'),
    ).not.toHaveAttribute("accept", expect.stringContaining(".docx"));
    plannedView.unmount();

    const readyPayload = capability();
    const docx = readyPayload.capabilities[0].formats.find(
      (format) => format.format_id === "docx",
    )!;
    docx.interaction_status = "ready";
    docx.status_reason = null;
    readyPayload.capabilities[0].handling_options[0].format_ids.push("docx");
    const readyFetch = vi.fn(async () => response(readyPayload));
    vi.stubGlobal("fetch", readyFetch);
    const readyView = renderComposer();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "添加文件" })).toBeEnabled(),
    );
    expect(
      readyView.container.querySelector('input[type="file"]'),
    ).toHaveAttribute("accept", expect.stringContaining(".docx"));
    expect(screen.getByRole("button", { name: "添加文件" })).toHaveAttribute(
      "title",
      expect.stringContaining("Word 文档"),
    );
  });

  it("requires an explicit XLSX destination before uploading", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/files/capabilities")) return response(capability());
      if (url === "/api/files" && init?.method === "POST") {
        return response(
          {
            ...asset("forecast.xlsx"),
            format: "xlsx",
            media_type:
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          },
          201,
        );
      }
      if (url.includes("/parse") && init?.method === "POST") {
        return response({
          ...preview(),
          format: "xlsx",
          title: "forecast.xlsx",
          sections: [
            {
              text: "季度,收入\nQ1,100",
              page: null,
              line_range: null,
              sheet: "预算",
              row_range: "A1:B2",
            },
          ],
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = renderComposer();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "添加文件" })).toBeEnabled(),
    );

    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: {
        files: [
          new File(["xlsx"], "forecast.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });

    expect(
      await screen.findByRole("region", { name: "forecast.xlsx 的使用方式" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "加入资料库" })).toHaveAttribute(
      "href",
      "/rag",
    );
    expect(screen.getByRole("link", { name: "用 Data X 分析" })).toHaveAttribute(
      "href",
      "/datax",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "与模型讨论" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === "/api/files" && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(await screen.findByText(/工作表「预算」· A1:B2/)).toBeVisible();
  });

  it("returns focus to the visible file trigger when XLSX selection is cancelled", async () => {
    const fetchMock = vi.fn(async () => response(capability()));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = renderComposer();
    const fileButton = await screen.findByRole("button", { name: "添加文件" });
    await waitFor(() => expect(fileButton).toBeEnabled());

    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: {
        files: [
          new File(["xlsx"], "budget.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });
    const currentAction = await screen.findByRole("button", {
      name: "与模型讨论",
    });
    expect(currentAction).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "取消选择 budget.xlsx" }));
    await waitFor(() => expect(fileButton).toHaveFocus());
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("opens an accessible preview, confirms it, returns focus, and success-reset keeps no local asset", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/files/capabilities")) return response(capability());
      if (url === "/api/files" && init?.method === "POST") return response(asset(), 201);
      if (url.includes("/parse") && init?.method === "POST") return response(preview());
      if (url.includes("/confirm") && init?.method === "POST") {
        return response({
          asset_id: "file_12345678901234567890123456789012",
          handling: "extract",
          confirmation_revision: 3,
          confirmed_at: "2026-08-07T00:00:00Z",
        });
      }
      if (init?.method === "DELETE") return response(null, 204);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onStateChange = vi.fn<(state: ChatFileComposerState) => void>();
    const { container, rerender, baseProps } = renderComposer({ onStateChange });
    await waitFor(() => expect(screen.getByRole("button", { name: "添加文件" })).toBeEnabled());

    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: {
        files: [new File(["hello"], "brief.md", { type: "text/markdown" })],
      },
    });

    const region = await screen.findByRole("region", { name: "brief.md" });
    const close = screen.getByRole("button", { name: "关闭文件预览" });
    await waitFor(() => expect(close).toHaveFocus());
    const tray = screen
      .getAllByRole("button")
      .find((button) => button.getAttribute("aria-expanded") === "true")!;
    expect(tray).toHaveAttribute("aria-controls", region.id);

    fireEvent.click(screen.getByRole("button", { name: "确认用于本轮" }));
    await waitFor(() => expect(tray).toHaveFocus());
    expect(onStateChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        count: 1,
        busy: false,
        allConfirmed: true,
        files: [expect.objectContaining({ confirmationRevision: 3 })],
      }),
    );

    rerender(
      <MemoryRouter>
        <ChatFileComposer {...baseProps} resetVersion={1} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.queryByText("brief.md")).not.toBeInTheDocument());
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
    ).toBe(false);
  });

  it("keeps a failed upload visible for an explicit retry or removal", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/files/capabilities")) return response(capability());
      return response(
        { detail: { code: "file_invalid", message: "文件校验失败，请重新选择。" } },
        422,
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const onStateChange = vi.fn<(state: ChatFileComposerState) => void>();
    const { container } = renderComposer({ onStateChange });
    await waitFor(() => expect(screen.getByRole("button", { name: "添加文件" })).toBeEnabled());
    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: { files: [new File(["x"], "bad.md", { type: "text/markdown" })] },
    });

    expect((await screen.findAllByText(/处理失败/)).length).toBeGreaterThan(0);
    expect(screen.getByText("文件校验失败，请重新选择。")).toBeVisible();
    expect(onStateChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ count: 1, allConfirmed: false }),
    );
  });

  it("aborts discard in flight and deletes an asset that arrives late", async () => {
    let resolveUpload!: (value: Response) => void;
    const uploadResponse = new Promise<Response>((resolve) => {
      resolveUpload = resolve;
    });
    let uploadSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/files/capabilities")) {
        return Promise.resolve(response(capability()));
      }
      if (url === "/api/files" && init?.method === "POST") {
        uploadSignal = init.signal as AbortSignal;
        return uploadResponse;
      }
      if (init?.method === "DELETE") return Promise.resolve(response(null, 204));
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container, rerender, baseProps } = renderComposer();
    await waitFor(() => expect(screen.getByRole("button", { name: "添加文件" })).toBeEnabled());
    fireEvent.change(container.querySelector('input[type="file"]')!, {
      target: { files: [new File(["x"], "late.md", { type: "text/markdown" })] },
    });
    await waitFor(() => expect(uploadSignal).toBeDefined());

    rerender(
      <MemoryRouter>
        <ChatFileComposer {...baseProps} discardVersion={1} />
      </MemoryRouter>,
    );
    await waitFor(() => expect(uploadSignal?.aborted).toBe(true));
    await act(async () => resolveUpload(response(asset("late.md"), 201)));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE"),
      ).toBe(true),
    );
  });
});
