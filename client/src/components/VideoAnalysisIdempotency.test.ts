import { afterEach, describe, expect, it, vi } from "vitest";
import { analyzeChatVideo } from "./ChatVideoComposer";
import { requestVideoAnalysis } from "./VideoAnalysisWorkspace";

describe("video analysis idempotency", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses one fresh idempotency key for each paid analysis request", async () => {
    const randomUUID = vi
      .spyOn(window.crypto, "randomUUID")
      .mockReturnValueOnce("11111111-1111-4111-8111-111111111111")
      .mockReturnValueOnce("22222222-2222-4222-8222-222222222222");
    const xhrHeaders = new Headers();

    class MockXMLHttpRequest {
      status = 0;
      response: unknown = null;
      responseText = "";
      responseType = "";
      upload = {
        onprogress: null as ((event: ProgressEvent) => void) | null,
        onload: null as ((event: ProgressEvent) => void) | null,
      };
      onload: ((event: ProgressEvent) => void) | null = null;
      onerror: ((event: ProgressEvent) => void) | null = null;
      onabort: ((event: ProgressEvent) => void) | null = null;

      open() {}

      setRequestHeader(name: string, value: string) {
        xhrHeaders.set(name, value);
      }

      send() {
        queueMicrotask(() => {
          this.status = 200;
          this.response = {
            text: "ok",
            requested_model: "openai/video-test",
            actual_model: "openai/video-test",
            provider: "openrouter",
            request_id: "analysis-1",
            source_kind: "url",
            usage: {
              input_tokens: null,
              output_tokens: null,
              total_tokens: null,
              cost_usd: null,
              cost_kind: "unavailable",
            },
          };
          this.onload?.(new ProgressEvent("load"));
        });
      }

      abort() {
        this.onabort?.(new ProgressEvent("abort"));
      }
    }

    vi.stubGlobal(
      "XMLHttpRequest",
      MockXMLHttpRequest as unknown as typeof XMLHttpRequest,
    );

    await requestVideoAnalysis({
      file: null,
      modelId: "openai/video-test",
      prompt: "Describe the video.",
      signal: new AbortController().signal,
      sourceMode: "url",
      videoUrl: "https://example.com/video.mp4",
      onProgress: () => undefined,
    });

    expect(xhrHeaders.get("Idempotency-Key")).toBe(
      "11111111-1111-4111-8111-111111111111",
    );

    let fetchHeaders = new Headers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        fetchHeaders = new Headers(init?.headers);
        return {
          ok: true,
          json: async () => ({
            text: "ok",
            requested_model: "openai/video-test",
            actual_model: "openai/video-test",
            provider: "openrouter",
            request_id: "analysis-2",
          }),
        } as Response;
      }),
    );

    await analyzeChatVideo(
      new File(["video"], "sample.mp4", { type: "video/mp4" }),
      "openai/video-test",
      "Describe the video.",
    );

    expect(fetchHeaders.get("Idempotency-Key")).toBe(
      "22222222-2222-4222-8222-222222222222",
    );
    expect(randomUUID).toHaveBeenCalledTimes(2);
  });
});
