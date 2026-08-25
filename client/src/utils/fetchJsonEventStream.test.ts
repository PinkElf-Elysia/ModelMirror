import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchJsonEventStream } from "./fetchJsonEventStream";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("fetchJsonEventStream", () => {
  it("propagates a business error thrown by the event handler", async () => {
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'data: {"event":"error","message":"managed blocked"}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(body, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );

    await expect(
      fetchJsonEventStream({
        url: "/api/fusion/chat",
        payload: {},
        onEvent: (event) => {
          if (event.event === "error") throw new Error("managed blocked");
        },
      }),
    ).rejects.toThrow("managed blocked");
  });
});
