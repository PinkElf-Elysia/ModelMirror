import { afterEach, describe, expect, it, vi } from "vitest";
import {
  synthesizeXpertSpeech,
  transcribeXpertAudio,
  XpertAudioRequestError,
  type XpertAudioCapabilities,
} from "../utils/xpertApi";
import {
  claimXpertAudioRequest,
  claimXpertResumeExecution,
  invalidateXpertAudioActivity,
  isCurrentXpertAudioRequest,
  isCurrentXpertPendingResume,
  isCurrentXpertResumeExecution,
  isCurrentXpertRouteContext,
  playXpertAudioBlob,
  xpertConversationNavigationLocked,
  xpertMessageInputLocked,
  xpertResumeTemporarilyLocked,
  xpertSpeechAvailable,
  xpertTranscriptionAvailable,
  type XpertAudioRequestIdentity,
  type XpertPendingResumeIdentity,
} from "./XpertChatPage";

const requestId = "00000000-0000-4000-8000-000000000001";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function routeReceipt(
  entryId: "xpert_transcription" | "xpert_speech",
  status: "passed" | "failed",
) {
  return {
    contract_version: "modelmirror-provider-workload-routing-v1",
    entry_id: entryId,
    routing_mode: "managed_required",
    run_reference: `${entryId}-run`,
    status,
    call_count: 1,
    reason_codes:
      status === "passed" ? [] : ["provider_workload_call_failed"],
    calls: [{
      call_sequence: 1,
      model_id: `provider/${entryId}`,
      actual_model: `provider/${entryId}`,
      dispatched: true,
      status,
      error_code: status === "passed" ? null : "provider_call_failed",
      prompt_tokens: null,
      completion_tokens: null,
      total_tokens: null,
      private_prompt: "must-not-enter-client-state",
    }],
    connection_id: "private-api-key",
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Xpert audio availability", () => {
  it("gates STT and TTS by their exact entry result, not gateway_configured", () => {
    const capabilities = {
      version: 1,
      gateway_configured: true,
      speech_to_text: {
        enabled: true,
        model_id: "unavailable-stt",
        max_file_bytes: 10 * 1024 * 1024,
        available: false,
        routing_mode: "legacy",
        reason_code: "xpert_audio_legacy_gateway_not_configured",
      },
      text_to_speech: {
        enabled: true,
        model_id: "qualified-tts",
        voice: "alloy",
        max_text_chars: 4_000,
        available: true,
        routing_mode: "managed_required",
        reason_code: "provider_workload_available",
      },
    } satisfies XpertAudioCapabilities;

    expect(xpertTranscriptionAvailable(capabilities)).toBe(false);
    expect(xpertSpeechAvailable(capabilities)).toBe(true);
  });

  it("locks conversation navigation and message sending for either audio operation", () => {
    expect(xpertConversationNavigationLocked(false, false, false, "", true, "")).toBe(true);
    expect(xpertConversationNavigationLocked(false, false, false, "", false, "message-a")).toBe(true);
    expect(xpertConversationNavigationLocked(false, false, false, "", false, "", true)).toBe(true);
    expect(xpertConversationNavigationLocked(false, false, false, "", false, "")).toBe(false);
    expect(xpertMessageInputLocked(false, false, true, "")).toBe(true);
    expect(xpertMessageInputLocked(false, false, false, "message-a")).toBe(true);
    expect(xpertMessageInputLocked(false, false, false, "", true)).toBe(true);
    expect(xpertMessageInputLocked(false, false, false, "")).toBe(false);
  });

  it("claims the audio request synchronously before React busy state can rerender", () => {
    const busy = { current: false };

    expect(claimXpertAudioRequest(busy)).toBe(true);
    expect(claimXpertAudioRequest(busy)).toBe(false);
    expect(busy.current).toBe(true);
  });

  it("invalidates the old generation and clears visible audio ownership", () => {
    const requestToken = { current: 6 };
    const busy = { current: true };
    const cleanup = vi.fn();
    const playbackCleanup = { current: cleanup as (() => void) | null };
    const audioInput = { value: "recording.wav" };

    invalidateXpertAudioActivity(
      requestToken,
      busy,
      playbackCleanup,
      audioInput,
    );

    expect(requestToken.current).toBe(7);
    expect(busy.current).toBe(false);
    expect(cleanup).toHaveBeenCalledTimes(1);
    expect(playbackCleanup.current).toBeNull();
    expect(audioInput.value).toBe("");
  });

  it("binds a queued approval resume to one task and conversation generation", () => {
    const pending: XpertPendingResumeIdentity = {
      taskId: "task-a",
      xpertId: "xpert-a",
      version: 2,
      conversationId: "conversation-a",
      conversationRequestToken: 8,
    };

    expect(isCurrentXpertPendingResume(pending, { ...pending })).toBe(true);
    expect(isCurrentXpertPendingResume(pending, {
      ...pending,
      taskId: "task-b",
    })).toBe(false);
    expect(isCurrentXpertPendingResume(pending, {
      ...pending,
      conversationRequestToken: 9,
    })).toBe(false);
  });

  it("claims one resume synchronously and queues every temporary lock class", () => {
    const inFlight = { current: false };
    expect(claimXpertResumeExecution(inFlight)).toBe(true);
    expect(claimXpertResumeExecution(inFlight)).toBe(false);
    expect(xpertResumeTemporarilyLocked(true, false, false, "", false)).toBe(true);
    expect(xpertResumeTemporarilyLocked(false, true, false, "", false)).toBe(true);
    expect(xpertResumeTemporarilyLocked(false, false, true, "", false)).toBe(true);
    expect(xpertResumeTemporarilyLocked(false, false, false, "message-a", false)).toBe(true);
    expect(xpertResumeTemporarilyLocked(false, false, false, "", true)).toBe(true);
    expect(xpertResumeTemporarilyLocked(false, false, false, "", false)).toBe(false);
  });

  it("invalidates a resume after unmount, abort replacement, or identity drift", () => {
    const expected: XpertPendingResumeIdentity = {
      taskId: "task-a",
      xpertId: "xpert-a",
      version: 2,
      conversationId: "conversation-a",
      conversationRequestToken: 8,
    };
    const validState = {
      mounted: true,
      requestToken: 4,
      currentRequestToken: 4,
      inFlight: true,
      activeController: true,
      routeValid: true,
    };

    expect(isCurrentXpertResumeExecution(expected, expected, validState)).toBe(true);
    expect(isCurrentXpertResumeExecution(expected, expected, {
      ...validState,
      mounted: false,
    })).toBe(false);
    expect(isCurrentXpertResumeExecution(expected, expected, {
      ...validState,
      activeController: false,
    })).toBe(false);
    expect(isCurrentXpertResumeExecution(expected, expected, {
      ...validState,
      currentRequestToken: 5,
    })).toBe(false);
    expect(isCurrentXpertResumeExecution(expected, {
      ...expected,
      conversationId: "conversation-b",
    }, validState)).toBe(false);
  });

  it("rejects stale route context until the loaded conversation belongs to the route Xpert", () => {
    expect(isCurrentXpertRouteContext(
      "xpert-b",
      "xpert-a",
      "conversation-a",
      "xpert-a",
    )).toBe(false);
    expect(isCurrentXpertRouteContext(
      "xpert-b",
      "xpert-b",
      "conversation-a",
      "xpert-a",
    )).toBe(false);
    expect(isCurrentXpertRouteContext(
      "xpert-b",
      "xpert-b",
      "conversation-b",
      "xpert-b",
    )).toBe(true);
  });

  it("drops a deferred audio completion after the conversation generation changes", async () => {
    const request: XpertAudioRequestIdentity = {
      requestToken: 4,
      xpertId: "xpert-a",
      version: 2,
      conversationId: "conversation-a",
      conversationRequestToken: 7,
    };
    let current = { ...request };
    const completion = deferred<{ text: string; receipt: string }>();
    const visible = {
      input: "new-conversation-draft",
      receipt: "new-conversation-receipt",
      busyOwner: "new-request",
    };
    const pending = completion.promise.then((payload) => {
      if (!isCurrentXpertAudioRequest(request, current)) return;
      visible.input = payload.text;
      visible.receipt = payload.receipt;
      visible.busyOwner = "";
    });

    current = {
      ...request,
      requestToken: 5,
      conversationId: "conversation-b",
      conversationRequestToken: 8,
    };
    completion.resolve({ text: "from-conversation-a", receipt: "receipt-a" });
    await pending;

    expect(visible).toEqual({
      input: "new-conversation-draft",
      receipt: "new-conversation-receipt",
      busyOwner: "new-request",
    });
  });

  it("releases the Blob URL exactly once when audio.play rejects", async () => {
    const createObjectURL = vi.fn(() => "blob:xpert-speech");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    class RejectingAudio {
      addEventListener = vi.fn();
      removeEventListener = vi.fn();
      pause = vi.fn();
      removeAttribute = vi.fn();
      play = vi.fn(() => Promise.reject(new Error("autoplay blocked")));
    }
    const audioInstances: RejectingAudio[] = [];
    class AudioConstructor extends RejectingAudio {
      constructor(_url: string) {
        super();
        audioInstances.push(this);
      }
    }
    vi.stubGlobal("Audio", AudioConstructor);

    await expect(playXpertAudioBlob(new Blob(["audio"]))).rejects.toThrow(
      "autoplay blocked",
    );

    const audio = audioInstances[0];
    expect(audio).toBeDefined();
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:xpert-speech");
    expect(audio?.pause).toHaveBeenCalledTimes(1);
    expect(audio?.removeAttribute).toHaveBeenCalledWith("src");
  });

  it.each([
    ["ended", "onEnded"],
    ["error", "onError"],
  ] as const)("releases playback on %s and calls only %s", async (eventName, callbackName) => {
    const createObjectURL = vi.fn(() => "blob:xpert-playback");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    class SettledAudio {
      listeners = new Map<string, EventListener>();
      addEventListener = vi.fn((name: string, listener: EventListener) => {
        this.listeners.set(name, listener);
      });
      removeEventListener = vi.fn((name: string) => {
        this.listeners.delete(name);
      });
      pause = vi.fn();
      removeAttribute = vi.fn();
      play = vi.fn(() => Promise.resolve());
    }
    const audioInstances: SettledAudio[] = [];
    class AudioConstructor extends SettledAudio {
      constructor(_url: string) {
        super();
        audioInstances.push(this);
      }
    }
    vi.stubGlobal("Audio", AudioConstructor);
    const onEnded = vi.fn();
    const onError = vi.fn();

    await playXpertAudioBlob(new Blob(["audio"]), { onEnded, onError });
    const audio = audioInstances[0];
    const listener = audio?.listeners.get(eventName);
    expect(listener).toBeDefined();
    listener?.(new Event(eventName));

    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(audio?.pause).toHaveBeenCalledTimes(1);
    expect(audio?.removeAttribute).toHaveBeenCalledWith("src");
    expect(callbackName === "onEnded" ? onEnded : onError).toHaveBeenCalledTimes(1);
    expect(callbackName === "onEnded" ? onError : onEnded).not.toHaveBeenCalled();
  });
});

describe("Xpert audio Provider receipts", () => {
  it("projects the STT JSON receipt and drops unrecognized fields", async () => {
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue(requestId);
    const receipt = routeReceipt("xpert_transcription", "passed");
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        Promise.resolve(new Response(JSON.stringify({
          text: "transcribed",
          model_id: "provider/xpert_transcription",
          xpert_version: 1,
          execution_mode: "managed",
          provider_route_receipts: [receipt],
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await transcribeXpertAudio(
      "xpert-1",
      1,
      new File(["audio"], "request.wav", { type: "audio/wav" }),
    );

    expect(result.provider_route_receipts).toHaveLength(1);
    expect(result.provider_route_receipts?.[0]).toMatchObject({
      entry_id: "xpert_transcription",
      status: "passed",
      call_count: 1,
    });
    expect(JSON.stringify(result.provider_route_receipts)).not.toContain(
      "must-not-enter-client-state",
    );
    expect(JSON.stringify(result.provider_route_receipts)).not.toContain(
      "private-api-key",
    );
    const request = fetchMock.mock.calls[0]?.[1];
    expect(new Headers(request?.headers).get("Idempotency-Key")).toBe(requestId);
  });

  it("projects the TTS response header receipt", async () => {
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue(requestId);
    const receipt = routeReceipt("xpert_speech", "passed");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("audio", {
      status: 200,
      headers: {
        "Content-Type": "audio/mpeg",
        "X-ModelMirror-Execution-Mode": "managed",
        "X-ModelMirror-Provider-Route-Receipt": JSON.stringify(receipt),
      },
    }))));

    const result = await synthesizeXpertSpeech("xpert-1", 1, "Read this.");

    expect(result.executionMode).toBe("managed");
    expect(result.providerRouteReceipt).toMatchObject({
      entry_id: "xpert_speech",
      status: "passed",
      call_count: 1,
    });
    expect(JSON.stringify(result.providerRouteReceipt)).not.toContain(
      "private-api-key",
    );
  });

  it("keeps a failed detail receipt separate from the safe error message", async () => {
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue(requestId);
    const receipt = routeReceipt("xpert_speech", "failed");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify({
      detail: {
        code: "provider_call_failed",
        message: "Managed Provider call failed.",
        route_receipt: receipt,
      },
    }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    }))));

    let caught: unknown;
    try {
      await synthesizeXpertSpeech("xpert-1", 1, "Read this.");
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(XpertAudioRequestError);
    const error = caught as XpertAudioRequestError;
    expect(error.message).toBe("Managed Provider call failed.");
    expect(error.providerRouteReceipt).toMatchObject({
      entry_id: "xpert_speech",
      status: "failed",
      reason_codes: ["provider_workload_call_failed"],
    });
    expect(error.message).not.toContain("provider_workload_call_failed");
    expect(JSON.stringify(error.providerRouteReceipt)).not.toContain(
      "private-api-key",
    );
  });
});
