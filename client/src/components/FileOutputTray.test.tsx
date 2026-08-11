import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FileOutput } from "../data/fileOutputs";
import FileOutputTray from "./FileOutputTray";

function output(overrides: Partial<FileOutput> = {}): FileOutput {
  return {
    output_id: `output_${"a".repeat(32)}`,
    asset_id: `file_${"b".repeat(32)}`,
    purpose: "chat",
    scope_id: "chat-scope-1",
    producer_kind: "chat_tool",
    display_name: "report.txt",
    format: "plain_text",
    media_type: "text/plain",
    byte_size: 12,
    preview_kind: "text",
    status: "completed",
    expires_at: "2026-08-16T00:00:00+00:00",
    warnings: [],
    error_code: null,
    source_run_id: null,
    source_message_id: "assistant-1",
    source_node_id: null,
    created_at: "2026-08-09T00:00:00+00:00",
    updated_at: "2026-08-09T00:00:00+00:00",
    ...overrides,
  };
}

describe("FileOutputTray", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("opens an inline safe preview and restores focus on Escape", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            output_id: `output_${"a".repeat(32)}`,
            preview_kind: "text",
            text: "safe preview",
            document: null,
            truncated: false,
            warnings: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const { container } = render(
      <FileOutputTray outputs={[output()]} purpose="chat" scopeId="chat-scope-1" />,
    );
    const trigger = screen.getByRole("button", { name: "预览" });
    await user.click(trigger);
    expect(await screen.findByText("safe preview")).toBeInTheDocument();
    const region = screen.getByRole("region", { name: "report.txt 预览" });
    expect(region.className).not.toContain("fixed");
    expect(container.querySelector("section")?.className).not.toContain("fixed");
    const closeButton = region.querySelector<HTMLButtonElement>("button");
    expect(closeButton).not.toBeNull();
    await waitFor(() => expect(closeButton).toHaveFocus());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("shows cleanup_pending instead of claiming deletion completed", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "cleanup_pending" }), { status: 202 })));
    const onChange = vi.fn();
    render(
      <FileOutputTray
        onChange={onChange}
        outputs={[output()]}
        purpose="chat"
        scopeId="chat-scope-1"
      />,
    );
    await user.click(screen.getByRole("button", { name: "删除" }));
    await screen.findByText(/物理清理尚未完成/);
    expect(onChange.mock.calls[0][0][0].status).toBe("deleting");
  });

  it("requires a server revision before handing a file to Chat reuse", async () => {
    const user = userEvent.setup();
    const onReuse = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            output_id: `output_${"a".repeat(32)}`,
            asset_id: `file_${"c".repeat(32)}`,
            handling: "extract",
            target_id: "provider/tool-model",
            confirmation_revision: 3,
            output_confirmation_revision: 5,
            expires_at: "2026-08-09T00:10:00+00:00",
            confirmed_at: "2026-08-09T00:00:00+00:00",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    render(
      <FileOutputTray
        modelId="provider/tool-model"
        onReuse={onReuse}
        outputs={[output()]}
        purpose="chat"
        scopeId="chat-scope-1"
      />,
    );
    await user.click(screen.getByRole("button", { name: "下轮复用" }));
    await waitFor(() => expect(onReuse).toHaveBeenCalledTimes(1));
    expect(onReuse.mock.calls[0][1]).toMatchObject({
      confirmation_revision: 3,
      output_confirmation_revision: 5,
    });
  });

  it("uses the exact Workflow scope and target before adding a reusable copy", async () => {
    const user = userEvent.setup();
    const onReuse = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          output_id: `output_${"a".repeat(32)}`,
          asset_id: `file_${"d".repeat(32)}`,
          handling: "extract",
          target_id: "workflow-1",
          confirmation_revision: 2,
          output_confirmation_revision: 2,
          expires_at: "2026-08-09T00:10:00+00:00",
          confirmed_at: "2026-08-09T00:00:00+00:00",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <FileOutputTray
        onReuse={onReuse}
        outputs={[output({ purpose: "workflow", scope_id: "workflow:workflow-1" })]}
        purpose="workflow"
        reuseTargetId="workflow-1"
        scopeId="workflow:workflow-1"
      />,
    );

    await user.click(screen.getByRole("button", { name: "下轮复用" }));
    await waitFor(() => expect(onReuse).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("purpose=workflow&scope_id=workflow%3Aworkflow-1"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          handling: "extract",
          target_id: "workflow-1",
          gateway: "default",
        }),
      }),
    );
  });

  it("renders a visible failed state and retry action", () => {
    render(
      <FileOutputTray
        outputs={[output({ status: "failed", asset_id: null, error_code: "output_render_failed" })]}
        purpose="chat"
        scopeId="chat-scope-1"
      />,
    );
    expect(screen.getByText(/生成失败/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toHaveClass("min-h-11");
    expect(screen.getByText(/output_render_failed/)).toBeInTheDocument();
  });

  it("saves a scoped output to a selected knowledge base without extending the output", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/rag/knowledge_bases") {
        return new Response(
          JSON.stringify({
            knowledge_bases: [
              { id: "kb-1", name: "项目资料", deletion_status: "active" },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === "/api/rag/knowledge_bases/kb-1/documents/from-file-output") {
        return new Response(JSON.stringify({ id: "doc-output-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <FileOutputTray outputs={[output()]} purpose="chat" scopeId="chat-scope-1" />,
    );

    await user.click(screen.getByRole("button", { name: "保存资料库" }));
    expect(await screen.findByRole("region", { name: "选择保存资料库" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "确认保存" }));
    await screen.findByText(/重复保存会返回同一份文档/);

    const call = fetchMock.mock.calls.find(
      ([url]) => String(url).endsWith("/documents/from-file-output"),
    );
    expect(call).toBeDefined();
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      output_id: `output_${"a".repeat(32)}`,
      purpose: "chat",
      scope_id: "chat-scope-1",
    });
  });

  it("allows Chat media reuse but keeps save-to-RAG disabled", () => {
    render(
      <FileOutputTray
        modelId="provider/media-model"
        onReuse={vi.fn()}
        outputs={[output({ preview_kind: "image", format: "png", media_type: "image/png" })]}
        purpose="chat"
        scopeId="chat-scope-1"
      />,
    );
    expect(screen.getByRole("button", { name: "保存资料库" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存资料库" })).toHaveAttribute(
      "title",
      "媒体或未知格式需在资料库入口另行确认处理。",
    );
    expect(screen.getByRole("button", { name: "下轮复用" })).toBeEnabled();
  });

  it("keeps media reuse fail-closed outside Chat", () => {
    render(
      <FileOutputTray
        onReuse={vi.fn()}
        outputs={[
          output({
            purpose: "workflow",
            scope_id: "workflow:workflow-1",
            preview_kind: "video",
            format: "mp4",
            media_type: "video/mp4",
          }),
        ]}
        purpose="workflow"
        reuseTargetId="workflow-1"
        scopeId="workflow:workflow-1"
      />,
    );
    expect(screen.getByRole("button", { name: "下轮复用" })).toHaveAttribute(
      "title",
      "该模块没有与此输出类型对应的输入流程。",
    );
    expect(screen.getByRole("button", { name: "下轮复用" })).toBeDisabled();
  });
});
