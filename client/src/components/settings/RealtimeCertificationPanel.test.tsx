import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RealtimeCertificationPanel from "./RealtimeCertificationPanel";

class FakeDataChannel {
  readyState: RTCDataChannelState = "connecting";
  onopen: ((this: RTCDataChannel, ev: Event) => unknown) | null = null;
  onmessage: ((this: RTCDataChannel, ev: MessageEvent) => unknown) | null = null;
  onerror: ((this: RTCDataChannel, ev: Event) => unknown) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = "closed";
  });

  emitOpen() {
    this.readyState = "open";
    this.onopen?.call(this as unknown as RTCDataChannel, new Event("open"));
  }

  emitMessage(payload: unknown) {
    this.onmessage?.call(
      this as unknown as RTCDataChannel,
      new MessageEvent("message", { data: JSON.stringify(payload) }),
    );
  }
}

class FakePeerConnection {
  static latest: FakePeerConnection | null = null;
  static autoCompleteIce = true;
  static rejectRemoteDescription = false;
  static streamlessRemoteTrack = false;

  connectionState: RTCPeerConnectionState = "new";
  iceGatheringState: RTCIceGatheringState = "new";
  localDescription: RTCSessionDescription | null = null;
  onconnectionstatechange: ((this: RTCPeerConnection, ev: Event) => unknown) | null = null;
  ontrack: ((this: RTCPeerConnection, ev: RTCTrackEvent) => unknown) | null = null;
  remoteDescription: RTCSessionDescriptionInit | null = null;
  dataChannel: FakeDataChannel | null = null;
  private iceListeners = new Set<EventListenerOrEventListenerObject>();

  constructor() {
    FakePeerConnection.latest = this;
  }

  addTrack = vi.fn();
  close = vi.fn(() => {
    this.connectionState = "closed";
  });
  addEventListener = vi.fn(
    (type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "icegatheringstatechange") this.iceListeners.add(listener);
    },
  );
  removeEventListener = vi.fn(
    (type: string, listener: EventListenerOrEventListenerObject) => {
      if (type === "icegatheringstatechange") this.iceListeners.delete(listener);
    },
  );
  createDataChannel = vi.fn(() => {
    this.dataChannel = new FakeDataChannel();
    return this.dataChannel as unknown as RTCDataChannel;
  });
  createOffer = vi.fn(async () => ({ type: "offer" as const, sdp: "v=0\r\no=browser-offer" }));
  setLocalDescription = vi.fn(async (description: RTCSessionDescriptionInit) => {
    this.localDescription = {
      type: description.type,
      sdp: `${description.sdp}\r\na=candidate:local-description`,
    } as RTCSessionDescription;
    if (FakePeerConnection.autoCompleteIce) this.emitIceComplete();
  });
  setRemoteDescription = vi.fn(async (description: RTCSessionDescriptionInit) => {
    if (FakePeerConnection.rejectRemoteDescription) {
      throw new DOMException("invalid remote SDP", "InvalidAccessError");
    }
    this.remoteDescription = description;
    this.emitRemoteTrack();
  });

  emitIceComplete() {
    this.iceGatheringState = "complete";
    const event = new Event("icegatheringstatechange");
    for (const listener of this.iceListeners) {
      if (typeof listener === "function") listener(event);
      else listener.handleEvent(event);
    }
  }

  emitRemoteTrack() {
    this.ontrack?.call(
      this as unknown as RTCPeerConnection,
      {
        streams: FakePeerConnection.streamlessRemoteTrack
          ? []
          : [{} as MediaStream],
        track: {} as MediaStreamTrack,
      } as unknown as RTCTrackEvent,
    );
  }
}

class FakeMediaStream {
  constructor(readonly tracks: MediaStreamTrack[]) {}
}

function response(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => payload,
  } as Response;
}

describe("RealtimeCertificationPanel", () => {
  const stop = vi.fn();
  const getUserMedia = vi.fn(async () => ({
    getAudioTracks: () => [{ stop }],
    getTracks: () => [{ stop }],
  } as unknown as MediaStream));

  beforeEach(() => {
    FakePeerConnection.latest = null;
    FakePeerConnection.autoCompleteIce = true;
    FakePeerConnection.rejectRemoteDescription = false;
    FakePeerConnection.streamlessRemoteTrack = false;
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
    vi.stubGlobal("MediaStream", FakeMediaStream);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    });
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue(
      "01234567-89ab-4def-8123-456789abcdef",
    );
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    getUserMedia.mockClear();
    stop.mockClear();
  });

  it("creates one SDP session and completes it only after an observed remote track", async () => {
    FakePeerConnection.streamlessRemoteTrack = true;
    const onComplete = vi.fn(async () => undefined);
    const storageSpy = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/certifications/realtime/session")) {
        return response({
          certification_id: "workcert-realtime-1",
          status: "running",
          answer_sdp: "v=0\r\no=provider-answer",
          expires_at: "2026-09-20T12:10:00Z",
          provider_dispatch_state: "confirmed",
          retry_allowed: false,
        });
      }
      if (url.endsWith("/certifications/realtime/workcert-realtime-1/complete")) {
        return response({ status: "passed", error_code: null });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RealtimeCertificationPanel
        connectionId="connection-openai"
        csrfToken="csrf-value"
        modelId="gpt-realtime-2.1-mini"
        onComplete={onComplete}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "开始浏览器辅助认证" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("最多创建一个 Provider 会话");
    const confirm = screen.getByRole("button", { name: "确认并建立会话" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await screen.findByText(/媒体会话已建立/);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const dataChannel = FakePeerConnection.latest?.dataChannel;
    expect(dataChannel).not.toBeNull();
    dataChannel?.emitOpen();
    dataChannel?.emitOpen();
    expect(dataChannel?.send).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(dataChannel?.send.mock.calls[0][0]))).toEqual({
      type: "response.create",
      response: {
        input: [],
        output_modalities: ["audio"],
        instructions:
          "Say exactly: ModelMirror realtime certification audio is working.",
      },
    });
    expect(document.querySelector("audio")?.controls).toBe(false);
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole("button", { name: "恢复当前实时播放" }),
    );
    await waitFor(() =>
      expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(2),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/不会重放已经过去的内容/)).toBeInTheDocument();
    const beginCall = fetchMock.mock.calls[0];
    expect(beginCall[0]).toBe(
      "/api/router/connections/connection-openai/certifications/realtime/session",
    );
    expect(beginCall[1]?.headers).toEqual(expect.objectContaining({
      "Idempotency-Key": "realtime-cert-01234567-89ab-4def-8123-456789abcdef",
      "X-ModelMirror-CSRF": "csrf-value",
    }));
    expect(JSON.parse(String(beginCall[1]?.body))).toEqual({
      model_id: "gpt-realtime-2.1-mini",
      adapter_contract: "openai_realtime_sdp_v1",
      offer_sdp:
        "v=0\r\no=browser-offer\r\na=candidate:local-description",
      acknowledge_billed_call: true,
    });
    expect(String(beginCall[1]?.body)).not.toContain("api_key");
    expect(storageSpy).not.toHaveBeenCalled();

    const passButton = screen.getByRole("button", {
      name: "确认听到音频并结束",
    });
    expect(passButton).toBeDisabled();
    dataChannel?.emitMessage({
      type: "response.done",
      response: { status: "completed" },
    });
    expect(await screen.findByText(/固定响应：已完成/)).toBeInTheDocument();
    await waitFor(() => expect(passButton).toBeEnabled());
    fireEvent.click(passButton);

    await screen.findByText(/Hangup 已完成人工认证/);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const completeCall = fetchMock.mock.calls[1];
    expect(completeCall[0]).toBe(
      "/api/router/certifications/realtime/workcert-realtime-1/complete",
    );
    expect(JSON.parse(String(completeCall[1]?.body))).toEqual({
      media_observed: true,
      hangup_observed: true,
      browser_error_code: null,
      browser_diagnostic_codes: [],
    });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("fails closed on pagehide without creating or completing twice", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/certifications/realtime/session")) {
        return response({
          certification_id: "workcert-realtime-pagehide",
          status: "running",
          answer_sdp: "v=0\r\no=provider-answer\r\n",
          expires_at: "2026-09-20T12:10:00Z",
          provider_dispatch_state: "confirmed",
          retry_allowed: false,
        });
      }
      if (
        url.endsWith(
          "/certifications/realtime/workcert-realtime-pagehide/complete",
        )
      ) {
        expect(init?.keepalive).toBe(true);
        return response({
          status: "failed",
          error_code: "provider_realtime_browser_component_disposed",
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const view = render(
      <RealtimeCertificationPanel
        connectionId="connection-openai"
        csrfToken="csrf-value"
        modelId="gpt-realtime-2.1-mini"
        onComplete={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "开始浏览器辅助认证" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并建立会话" }));
    await screen.findByText(/媒体会话已建立/);

    window.dispatchEvent(new Event("pagehide"));
    window.dispatchEvent(new Event("pagehide"));
    view.unmount();

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/certifications/realtime/session"),
      ),
    ).toHaveLength(1);
    const completeCall = fetchMock.mock.calls[1];
    expect(JSON.parse(String(completeCall[1]?.body))).toEqual({
      media_observed: false,
      hangup_observed: true,
      browser_error_code: "provider_realtime_browser_component_disposed",
    });
  });

  it("keeps a blocked resume local without creating another Provider session", async () => {
    vi.mocked(HTMLMediaElement.prototype.play)
      .mockResolvedValueOnce()
      .mockRejectedValueOnce(new DOMException("blocked", "NotAllowedError"));
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/certifications/realtime/session")) {
        return response({
          certification_id: "workcert-realtime-resume-blocked",
          status: "running",
          answer_sdp: "v=0\r\no=provider-answer\r\n",
          expires_at: "2026-09-20T12:10:00Z",
          provider_dispatch_state: "confirmed",
          retry_allowed: false,
        });
      }
      if (
        url.endsWith(
          "/certifications/realtime/workcert-realtime-resume-blocked/complete",
        )
      ) {
        return response({
          status: "failed",
          error_code: "provider_realtime_manual_check_failed",
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RealtimeCertificationPanel
        connectionId="connection-openai"
        csrfToken="csrf-value"
        modelId="gpt-realtime-2.1-mini"
        onComplete={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "开始浏览器辅助认证" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并建立会话" }));
    await screen.findByText(/媒体会话已建立/);

    fireEvent.click(
      screen.getByRole("button", { name: "恢复当前实时播放" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "不会创建新的 Provider 会话",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fireEvent.click(
      screen.getByRole("button", { name: /结束并标记未通过/ }),
    );
    await screen.findByText(/provider_realtime_manual_check_failed/);
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/certifications/realtime/session"),
      ),
    ).toHaveLength(1);
  });

  it("does not retry when creation fails", async () => {
    const fetchMock = vi.fn(async () => response({
      detail: {
        code: "provider_realtime_create_result_uncertain",
        message: "创建结果不确定；请先核查 Provider，会话不会自动重建。",
      },
    }, false, 503));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RealtimeCertificationPanel
        connectionId="connection-openai"
        csrfToken="csrf-value"
        modelId="gpt-realtime-2.1-mini"
        onComplete={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "开始浏览器辅助认证" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并建立会话" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("不会自动重建");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/请先在 Provider 侧核查会话/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "开始浏览器辅助认证" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认听到音频并结束" })).not.toBeInTheDocument();
  });

  it("waits for ICE and dispatches the final local description", async () => {
    FakePeerConnection.autoCompleteIce = false;
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => response({
        detail: {
          code: "realtime_request_rejected",
          message: "测试响应。",
        },
      }, false, 422),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RealtimeCertificationPanel
        connectionId="connection-openai"
        csrfToken="csrf-value"
        modelId="gpt-realtime-2.1-mini"
        onComplete={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "开始浏览器辅助认证" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并建立会话" }));

    await waitFor(() =>
      expect(FakePeerConnection.latest?.setLocalDescription).toHaveBeenCalledTimes(1),
    );
    expect(fetchMock).not.toHaveBeenCalled();
    FakePeerConnection.latest?.emitIceComplete();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(FakePeerConnection.latest?.localDescription?.sdp).toContain(
      "a=candidate:local-description",
    );
    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(requestBody.offer_sdp).toBe(
      "v=0\r\no=browser-offer\r\na=candidate:local-description",
    );
  });

  it("persists a safe browser stage when the SDP answer is rejected", async () => {
    FakePeerConnection.rejectRemoteDescription = true;
    const onComplete = vi.fn(async () => undefined);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/certifications/realtime/session")) {
        return response({
          certification_id: "workcert-realtime-rejected-answer",
          status: "running",
          answer_sdp: "v=0\r\no=provider-answer",
          expires_at: "2026-09-20T12:10:00Z",
          provider_dispatch_state: "confirmed",
          retry_allowed: false,
        });
      }
      if (
        url.endsWith(
          "/certifications/realtime/workcert-realtime-rejected-answer/complete",
        )
      ) {
        expect(JSON.parse(String(init?.body))).toEqual({
          media_observed: false,
          hangup_observed: true,
          browser_error_code:
            "provider_realtime_browser_remote_description_failed",
          browser_diagnostic_codes: [
            "provider_realtime_browser_exception_invalid_access_error",
            "provider_realtime_answer_audio_media_missing",
            "provider_realtime_answer_application_media_missing",
            "provider_realtime_answer_bundle_missing",
            "provider_realtime_answer_dtls_fingerprint_missing",
            "provider_realtime_answer_ice_credentials_missing",
            "provider_realtime_answer_setup_missing",
            "provider_realtime_answer_rtpmap_missing",
            "provider_realtime_answer_candidate_missing",
            "provider_realtime_answer_terminal_crlf_missing",
          ],
        });
        return response({
          status: "failed",
          error_code: "provider_realtime_browser_remote_description_failed",
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <RealtimeCertificationPanel
        connectionId="connection-openai"
        csrfToken="csrf-value"
        modelId="gpt-realtime-2.1-mini"
        onComplete={onComplete}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "开始浏览器辅助认证" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并建立会话" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "provider_realtime_browser_remote_description_failed",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/certifications/realtime/session"),
      ),
    ).toHaveLength(1);
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
