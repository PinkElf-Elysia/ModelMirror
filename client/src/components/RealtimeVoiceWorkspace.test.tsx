import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RealtimeVoiceWorkspace from "./RealtimeVoiceWorkspace";

class FakeDataChannel {
  private listeners = new Map<string, Set<EventListener>>();

  addEventListener = vi.fn((type: string, listener: EventListener) => {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  });
  close = vi.fn();

  emit(type: string, data?: string) {
    const event = type === "message"
      ? new MessageEvent(type, { data })
      : new Event(type);
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

class FakePeerConnection {
  static latest: FakePeerConnection | null = null;
  static autoCompleteIce = true;

  connectionState: RTCPeerConnectionState = "new";
  iceGatheringState: RTCIceGatheringState = "complete";
  localDescription: RTCSessionDescription | null = null;
  onconnectionstatechange: ((this: RTCPeerConnection, ev: Event) => unknown) | null = null;
  ontrack: ((this: RTCPeerConnection, ev: RTCTrackEvent) => unknown) | null = null;
  dataChannel = new FakeDataChannel();
  private iceListeners = new Set<EventListener>();

  constructor() {
    FakePeerConnection.latest = this;
    this.iceGatheringState = FakePeerConnection.autoCompleteIce
      ? "complete"
      : "gathering";
  }

  addTrack = vi.fn();
  close = vi.fn(() => {
    this.connectionState = "closed";
  });
  createDataChannel = vi.fn(() => this.dataChannel);
  getStats = vi.fn(async () => ({
    forEach: (visit: (entry: Record<string, unknown>) => void) => {
      visit({
        type: "media-source",
        kind: "audio",
        audioLevel: 0.1,
        totalAudioEnergy: 1,
      });
      visit({
        type: "outbound-rtp",
        kind: "audio",
        packetsSent: 4,
      });
      visit({
        type: "inbound-rtp",
        kind: "audio",
        packetsReceived: 4,
        audioLevel: 0.1,
        totalAudioEnergy: 1,
      });
    },
  }));
  createOffer = vi.fn(async () => ({ type: "offer" as const, sdp: "v=0\r\no=browser-runtime-offer" }));
  setLocalDescription = vi.fn(async (description: RTCSessionDescriptionInit) => {
    this.localDescription = {
      type: description.type,
      sdp: FakePeerConnection.autoCompleteIce
        ? `${description.sdp}\r\na=candidate:local-description`
        : description.sdp,
    } as RTCSessionDescription;
  });
  setRemoteDescription = vi.fn(async () => undefined);
  addEventListener = vi.fn((type: string, listener: EventListener) => {
    if (type === "icegatheringstatechange") this.iceListeners.add(listener);
  });
  removeEventListener = vi.fn((type: string, listener: EventListener) => {
    if (type === "icegatheringstatechange") this.iceListeners.delete(listener);
  });

  emitIceComplete() {
    this.iceGatheringState = "complete";
    if (this.localDescription) {
      this.localDescription = {
        ...this.localDescription,
        sdp: `${this.localDescription.sdp}\r\na=candidate:local-description`,
      } as RTCSessionDescription;
    }
    for (const listener of this.iceListeners) listener(new Event("icegatheringstatechange"));
  }

  changeConnectionState(state: RTCPeerConnectionState) {
    this.connectionState = state;
    this.onconnectionstatechange?.call(
      this as unknown as RTCPeerConnection,
      new Event("connectionstatechange"),
    );
  }
}

class FakeMediaStream {
  constructor(readonly tracks: MediaStreamTrack[] = []) {}
}

class FakeAudioContext {
  state: AudioContextState = "running";
  close = vi.fn(async () => {
    this.state = "closed";
  });
  resume = vi.fn(async () => undefined);
  createAnalyser = vi.fn(() => ({
    fftSize: 0,
    getFloatTimeDomainData: (samples: Float32Array) => {
      samples.fill(0);
      samples[0] = 1;
    },
  }));
  createMediaStreamSource = vi.fn(() => ({
    connect: vi.fn(),
    disconnect: vi.fn(),
  }));
}

function jsonResponse(payload: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => payload,
  } as Response;
}

function catalog() {
  return {
    status: "online",
    stale: false,
    profiles: [{
      model_id: "gpt-realtime-2.1-mini",
      display_name: "GPT Realtime 2.1 Mini",
      provider: "openai",
      invocable: true,
      interaction_status: "ready",
      status_reason: null,
      operations: ["realtime_voice"],
      voices: ["marin"],
      supports_streaming_input: true,
      supports_streaming_output: true,
    }],
  };
}

describe("RealtimeVoiceWorkspace managed session safety", () => {
  const stop = vi.fn();
  const getUserMedia = vi.fn(async () => {
    const track = {
      stop,
      enabled: true,
      getSettings: () => ({ deviceId: "mic-default" }),
    };
    return {
      getAudioTracks: () => [track],
      getTracks: () => [track],
    } as unknown as MediaStream;
  });
  const enumerateDevices = vi.fn(async () => [
    {
      deviceId: "mic-default",
      groupId: "group-1",
      kind: "audioinput" as const,
      label: "内置麦克风",
      toJSON: () => ({}),
    },
    {
      deviceId: "mic-usb",
      groupId: "group-2",
      kind: "audioinput" as const,
      label: "USB 麦克风",
      toJSON: () => ({}),
    },
  ] as MediaDeviceInfo[]);

  beforeEach(() => {
    FakePeerConnection.latest = null;
    FakePeerConnection.autoCompleteIce = true;
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
    vi.stubGlobal("MediaStream", FakeMediaStream);
    vi.stubGlobal("AudioContext", FakeAudioContext);
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { enumerateDevices, getUserMedia },
    });
    vi.spyOn(window.crypto, "randomUUID").mockReturnValue(
      "fedcba98-7654-4abc-8123-456789abcdef",
    );
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    getUserMedia.mockClear();
    enumerateDevices.mockClear();
    stop.mockClear();
  });

  it("blocks double-click duplication and never reconnects after the managed POST", async () => {
    FakePeerConnection.autoCompleteIce = false;
    const storageSpy = vi.spyOn(Storage.prototype, "setItem");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/multimodal/audio/models?refresh=true") {
        return jsonResponse(catalog());
      }
      if (url === "/api/multimodal/realtime/calls" && init?.method === "POST") {
        return jsonResponse({
          session_id: "realtime-managed-1",
          sdp_answer: "v=0\r\no=provider-answer",
          expires_at: new Date(Date.now() + 600_000).toISOString(),
          model_id: "gpt-realtime-2.1-mini",
          voice: "marin",
          execution_mode: "managed",
          provider_dispatch_state: "confirmed",
        });
      }
      if (url === "/api/multimodal/realtime/calls/realtime-managed-1" && init?.method === "DELETE") {
        return jsonResponse({ status: "interrupted" });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(
      <MemoryRouter><RealtimeVoiceWorkspace /></MemoryRouter>,
    );
    const start = await screen.findByRole("button", { name: "开始实时语音" });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);
    fireEvent.click(start);

    await waitFor(() => expect(FakePeerConnection.latest).not.toBeNull());
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
    FakePeerConnection.latest?.emitIceComplete();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/multimodal/realtime/calls",
      expect.objectContaining({ method: "POST" }),
    ));
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    const postCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(postCalls).toHaveLength(1);
    expect(postCalls[0][1]?.headers).toEqual(expect.objectContaining({
      "Idempotency-Key": "realtime-fedcba98-7654-4abc-8123-456789abcdef",
    }));
    const requestBody = JSON.parse(String(postCalls[0][1]?.body));
    expect(requestBody.sdp).toContain("v=0\r\no=browser-runtime-offer");
    expect(requestBody.sdp).toContain("a=candidate:local-description");
    expect(FakePeerConnection.latest?.localDescription?.sdp).toContain(
      "a=candidate:local-description",
    );
    expect(String(postCalls[0][1]?.body)).not.toContain("api_key");
    expect(storageSpy).not.toHaveBeenCalled();

    const remoteAudio = container.querySelector("audio");
    expect(remoteAudio).not.toBeNull();
    (remoteAudio as HTMLAudioElement).muted = true;
    (remoteAudio as HTMLAudioElement).volume = 0;
    FakePeerConnection.latest?.ontrack?.call(
      FakePeerConnection.latest as unknown as RTCPeerConnection,
      {
        streams: [],
        track: {} as MediaStreamTrack,
      } as unknown as RTCTrackEvent,
    );
    await waitFor(() =>
      expect(HTMLMediaElement.prototype.play).toHaveBeenCalledTimes(1),
    );
    expect((remoteAudio as HTMLAudioElement).muted).toBe(false);
    expect((remoteAudio as HTMLAudioElement).volume).toBe(1);

    FakePeerConnection.latest?.changeConnectionState("disconnected");
    expect(await screen.findByText(/没有自动创建新会话/)).toBeVisible();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/multimodal/realtime/calls/realtime-managed-1",
      expect.objectContaining({ method: "DELETE" }),
    ));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "重新连接新会话" })).toBeVisible();
  });

  it("renders an uncertain create result without an automatic second attempt", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/multimodal/audio/models?refresh=true") {
        return jsonResponse(catalog());
      }
      if (url === "/api/multimodal/realtime/calls" && init?.method === "POST") {
        return jsonResponse({
          detail: {
            code: "provider_realtime_create_result_uncertain",
            message: "创建结果不确定；请先核查 Provider，不要自动创建新会话。",
          },
        }, false, 503);
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryRouter><RealtimeVoiceWorkspace /></MemoryRouter>);
    const start = await screen.findByRole("button", { name: "开始实时语音" });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);

    expect(await screen.findByRole("alert")).toHaveTextContent("不要自动创建新会话");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "重新连接新会话" })).toBeVisible();
  });

  it("sends bounded runtime evidence without media or transcript content", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/multimodal/audio/models?refresh=true") {
        return jsonResponse(catalog());
      }
      if (url === "/api/multimodal/realtime/calls" && init?.method === "POST") {
        return jsonResponse({
          session_id: "realtime-managed-evidence",
          sdp_answer: "v=0\r\no=provider-answer",
          expires_at: new Date(Date.now() + 600_000).toISOString(),
          model_id: "gpt-realtime-2.1-mini",
          voice: "marin",
          execution_mode: "managed",
          provider_dispatch_state: "confirmed",
        });
      }
      if (
        url === "/api/multimodal/realtime/calls/realtime-managed-evidence" &&
        init?.method === "DELETE"
      ) {
        return jsonResponse({ status: "ended" });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(
      <MemoryRouter><RealtimeVoiceWorkspace /></MemoryRouter>,
    );
    const start = await screen.findByRole("button", { name: "开始实时语音" });
    await waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/multimodal/realtime/calls",
      expect.objectContaining({ method: "POST" }),
    ));

    const peer = FakePeerConnection.latest;
    expect(peer).not.toBeNull();
    peer?.dataChannel.emit("open");
    for (const type of [
      "input_audio_buffer.speech_started",
      "input_audio_buffer.speech_stopped",
      "response.created",
      "output_audio_buffer.started",
      "response.done",
    ]) {
      peer?.dataChannel.emit("message", JSON.stringify({ type }));
    }
    peer?.ontrack?.call(
      peer as unknown as RTCPeerConnection,
      {
        streams: [],
        track: {} as MediaStreamTrack,
      } as unknown as RTCTrackEvent,
    );
    const audio = container.querySelector("audio");
    expect(audio).not.toBeNull();
    fireEvent.playing(audio as HTMLAudioElement);

    expect(await screen.findByText(/Provider VAD已检测/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "结束通话" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/multimodal/realtime/calls/realtime-managed-evidence",
      expect.objectContaining({ method: "DELETE" }),
    ));

    const deleteCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/realtime-managed-evidence") &&
        init?.method === "DELETE",
    );
    expect(deleteCall).toBeDefined();
    expect(JSON.parse(String(deleteCall?.[1]?.body))).toEqual({
      data_channel_open: true,
      local_audio_observed: true,
      outbound_audio_sent: true,
      input_speech_started: true,
      input_speech_stopped: true,
      response_created: true,
      remote_track_observed: true,
      output_audio_started: true,
      inbound_audio_received: true,
      remote_audio_observed: true,
      response_done: true,
      playback_started: true,
    });
    expect(String(deleteCall?.[1]?.body)).not.toContain("sdp");
    expect(String(deleteCall?.[1]?.body)).not.toContain("transcript");
  });

  it("checks microphone energy locally without creating a Provider session", async () => {
    const fetchMock = vi.fn(async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ) => {
      const url = String(input);
      if (url === "/api/multimodal/audio/models?refresh=true") {
        return jsonResponse(catalog());
      }
      throw new Error(`Unexpected fetch ${url} ${String(init?.method ?? "GET")}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryRouter><RealtimeVoiceWorkspace /></MemoryRouter>);
    const check = await screen.findByRole("button", {
      name: "本地麦克风自检",
    });
    fireEvent.click(check);

    expect(await screen.findByText(/已检测到本地麦克风声音/)).toBeVisible();
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(enumerateDevices).toHaveBeenCalledTimes(1);
    expect(stop).toHaveBeenCalled();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(0);

    const device = await screen.findByRole("combobox", {
      name: "输入设备",
    });
    fireEvent.change(device, { target: { value: "mic-usb" } });
    fireEvent.click(screen.getByRole("button", { name: "本地麦克风自检" }));
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledTimes(2));
    expect(getUserMedia).toHaveBeenLastCalledWith({
      audio: expect.objectContaining({
        deviceId: { exact: "mic-usb" },
      }),
    });
  });
});
