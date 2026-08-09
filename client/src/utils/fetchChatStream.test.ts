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
});
