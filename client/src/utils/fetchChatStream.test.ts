import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchChatStream, type ChatApiMessage } from "./fetchChatStream";

function streamResponse(body: string) {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

const textMessages: ChatApiMessage[] = [
  { role: "user", content: "hello" },
];

const fileMessages: ChatApiMessage[] = [
  {
    role: "user",
    content: [
      { type: "text", text: "summarize" },
      {
        type: "input_file",
        asset_id: "file_1",
        handling: "extract",
        confirmation_revision: 1,
      },
    ],
  },
];

describe("fetchChatStream file completion gate", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("keeps ordinary text compatible with a bare DONE marker", async () => {
    const onMessageEnd = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse("data: [DONE]\n\n")));

    await expect(
      fetchChatStream({
        modelId: "openai/text-model",
        messages: textMessages,
        onDelta: vi.fn(),
        onMessageEnd,
      }),
    ).resolves.toBeUndefined();
    expect(onMessageEnd).toHaveBeenCalledTimes(1);
  });

  it("keeps ordinary text compatible when a scoped output context is present", async () => {
    const onMessageEnd = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(streamResponse("data: [DONE]\n\n"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchChatStream({
        modelId: "openai/text-model",
        messages: textMessages,
        fileScopeId: "chat-scope-1",
        outputContextId: "assistant-1",
        onDelta: vi.fn(),
        onMessageEnd,
      }),
    ).resolves.toBeUndefined();
    expect(onMessageEnd).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toMatchObject({
      file_scope_id: "chat-scope-1",
      output_context_id: "assistant-1",
      output_mode: "none",
    });
  });

  it("preserves server-confirmed output bindings on existing media inputs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse("data: [DONE]\n\n"));
    vi.stubGlobal("fetch", fetchMock);
    const outputId = `output_${"m".repeat(32)}`;
    const assetId = `file_${"n".repeat(32)}`;
    const attachmentId = `att_${"p".repeat(32)}`;

    await fetchChatStream({
      modelId: "provider/media-model",
      fileScopeId: "chat-scope-1",
      messages: [
        {
          role: "user",
          content: [
            {
              type: "image_url",
              image_url: { url: "data:image/png;base64,Y2xpZW50" },
              output_id: outputId,
              output_asset_id: assetId,
              output_confirmation_revision: 4,
            },
            {
              type: "input_audio",
              attachment_id: attachmentId,
              output_id: outputId,
              output_asset_id: assetId,
              output_confirmation_revision: 4,
            },
          ],
        },
      ],
      onDelta: vi.fn(),
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.messages[0].content).toEqual([
      {
        type: "image_url",
        image_url: { url: "data:image/png;base64,Y2xpZW50" },
        output_id: outputId,
        output_asset_id: assetId,
        output_confirmation_revision: 4,
      },
      {
        type: "input_audio",
        attachment_id: attachmentId,
        output_id: outputId,
        output_asset_id: assetId,
        output_confirmation_revision: 4,
      },
    ]);
  });

  it("rejects a file stream that ends with DONE but no message_end", async () => {
    const onMessageEnd = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(streamResponse("data: [DONE]\n\n")));

    await expect(
      fetchChatStream({
        modelId: "openai/file-model",
        messages: fileMessages,
        fileScopeId: "chat-scope-1",
        onDelta: vi.fn(),
        onMessageEnd,
      }),
    ).rejects.toThrow("文件处理未收到完成确认，附件已保留");
    expect(onMessageEnd).not.toHaveBeenCalled();
  });

  it("accepts one explicit message_end for a file stream", async () => {
    const onMessageEnd = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          streamResponse("event: message_end\ndata: {}\n\ndata: [DONE]\n\n"),
        ),
    );

    await expect(
      fetchChatStream({
        modelId: "openai/file-model",
        messages: fileMessages,
        fileScopeId: "chat-scope-1",
        onDelta: vi.fn(),
        onMessageEnd,
      }),
    ).resolves.toBeUndefined();
    expect(onMessageEnd).toHaveBeenCalledTimes(1);
  });

  it("emits a validated output_file before accepting the explicit terminal event", async () => {
    const onOutputFile = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse(
        [
          'data: {"choices":[{"delta":{"content":"done"}}]}',
          `event: output_file\ndata: ${JSON.stringify({
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
          })}`,
          "event: message_end\ndata: {}",
          "data: [DONE]",
          "",
        ].join("\n\n"),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchChatStream({
      modelId: "provider/tool-model",
      messages: textMessages,
      fileScopeId: "chat-scope-1",
      outputMode: "allowlisted",
      outputContextId: "assistant-1",
      onDelta: vi.fn(),
      onOutputFile,
      onMessageEnd: vi.fn(),
    });

    expect(onOutputFile).toHaveBeenCalledTimes(1);
    expect(onOutputFile.mock.calls[0][0].display_name).toBe("report.txt");
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body).toMatchObject({
      file_scope_id: "chat-scope-1",
      output_mode: "allowlisted",
      output_context_id: "assistant-1",
    });
  });

  it("fails closed on malformed output metadata", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        streamResponse(
          "event: output_file\ndata: {\"output_id\":\"output_bad\"}\n\nevent: message_end\ndata: {}\n\ndata: [DONE]\n\n",
        ),
      ),
    );
    await expect(
      fetchChatStream({
        modelId: "provider/tool-model",
        messages: textMessages,
        fileScopeId: "chat-scope-1",
        outputMode: "allowlisted",
        outputContextId: "assistant-1",
        onDelta: vi.fn(),
      }),
    ).rejects.toThrow("文件输出事件无效");
  });
});
