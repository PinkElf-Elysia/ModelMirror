import {
  ArrowLeft,
  Mic,
  MicOff,
  PhoneOff,
  RefreshCw,
  Radio,
  Volume2,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import BrandLogo from "./BrandLogo";
import ResourceNav from "./ResourceNav";

const DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1-mini";
const DEFAULT_REALTIME_VOICE = "marin";

type RealtimeStatus =
  | "idle"
  | "requesting_permission"
  | "connecting"
  | "listening"
  | "user_speaking"
  | "thinking"
  | "assistant_speaking"
  | "interrupted"
  | "reconnect_required"
  | "ending"
  | "ended"
  | "error";

interface RealtimeVoiceProfile {
  model_id: string;
  display_name: string;
  provider: "openai" | "openrouter";
  invocable: boolean;
  interaction_status: "ready" | "planned" | "disabled";
  status_reason: string | null;
  operations: string[];
  voices: string[];
  supports_streaming_input: boolean;
  supports_streaming_output: boolean;
}

interface AudioCatalogResponse {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: RealtimeVoiceProfile[];
}

interface RealtimeCallResponse {
  session_id: string;
  sdp_answer: string;
  expires_at: string;
  model_id: string;
  voice: string;
}

interface RealtimeVoiceWorkspaceProps {
  initialModelId?: string;
}

const STATUS_CONTENT: Record<
  RealtimeStatus,
  { title: string; detail: string; tone: string }
> = {
  idle: {
    title: "准备开始",
    detail: "点击开始后才会申请麦克风权限。",
    tone: "text-slate-200",
  },
  requesting_permission: {
    title: "正在请求麦克风权限",
    detail: "请在浏览器提示中允许模镜使用麦克风。",
    tone: "text-cyan-100",
  },
  connecting: {
    title: "正在建立安全连接",
    detail: "模型服务密钥只在后端使用。",
    tone: "text-cyan-100",
  },
  listening: {
    title: "正在聆听",
    detail: "可以直接说话，停顿后模型会自然回答。",
    tone: "text-emerald-100",
  },
  user_speaking: {
    title: "正在听你说",
    detail: "继续说即可，语义停顿会自动结束本轮输入。",
    tone: "text-emerald-100",
  },
  thinking: {
    title: "正在组织回答",
    detail: "保持连接，模型即将开始说话。",
    tone: "text-amber-100",
  },
  assistant_speaking: {
    title: "模型正在回答",
    detail: "你可以随时开口，自然打断当前回答。",
    tone: "text-violet-100",
  },
  interrupted: {
    title: "已打断模型",
    detail: "正在听你继续说。",
    tone: "text-emerald-100",
  },
  reconnect_required: {
    title: "连接已中断",
    detail: "不会自动建立新的付费会话，请手动重新连接。",
    tone: "text-amber-100",
  },
  ending: {
    title: "正在结束会话",
    detail: "正在关闭麦克风和模型连接。",
    tone: "text-slate-300",
  },
  ended: {
    title: "会话已结束",
    detail: "本次音频没有保存在模镜中。",
    tone: "text-slate-300",
  },
  error: {
    title: "未能建立会话",
    detail: "检查提示后可以重新尝试。",
    tone: "text-rose-100",
  },
};

function modelLabel(modelId: string) {
  return modelId === "gpt-realtime-2.1"
    ? "质量优先 · GPT Realtime 2.1"
    : "均衡推荐 · GPT Realtime 2.1 Mini";
}

function formatRemaining(seconds: number | null) {
  if (seconds === null) return "10:00";
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  return `${minutes}:${String(safe % 60).padStart(2, "0")}`;
}

async function apiError(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as {
      detail?: { message?: string } | string;
    };
    if (
      payload.detail &&
      typeof payload.detail === "object" &&
      typeof payload.detail.message === "string"
    ) {
      return payload.detail.message;
    }
    if (typeof payload.detail === "string") return payload.detail;
  } catch {
    // The UI intentionally does not expose upstream response bodies.
  }
  return fallback;
}

function microphoneError(error: unknown) {
  if (error instanceof DOMException) {
    if (
      error.name === "NotAllowedError" ||
      error.name === "PermissionDeniedError"
    ) {
      return "麦克风权限被拒绝。请在浏览器地址栏中允许麦克风，然后重试。";
    }
    if (
      error.name === "NotFoundError" ||
      error.name === "DevicesNotFoundError"
    ) {
      return "没有找到可用麦克风。请连接设备后重试。";
    }
    if (
      error.name === "NotReadableError" ||
      error.name === "TrackStartError"
    ) {
      return "麦克风正被其他应用占用。关闭占用程序后重试。";
    }
  }
  return "无法使用麦克风。请检查浏览器权限和音频设备。";
}

export default function RealtimeVoiceWorkspace({
  initialModelId = DEFAULT_REALTIME_MODEL,
}: RealtimeVoiceWorkspaceProps) {
  const [profiles, setProfiles] = useState<RealtimeVoiceProfile[]>([]);
  const [catalogStatus, setCatalogStatus] =
    useState<AudioCatalogResponse["status"]>("offline");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [selectedModelId, setSelectedModelId] = useState(initialModelId);
  const [voice, setVoice] = useState(DEFAULT_REALTIME_VOICE);
  const [language, setLanguage] = useState("zh-CN");
  const [status, setStatus] = useState<RealtimeStatus>("idle");
  const [error, setError] = useState("");
  const [muted, setMuted] = useState(false);
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const [playbackBlocked, setPlaybackBlocked] = useState(false);

  const peerRef = useRef<RTCPeerConnection | null>(null);
  const localStreamRef = useRef<MediaStream | null>(null);
  const dataChannelRef = useRef<RTCDataChannel | null>(null);
  const remoteAudioRef = useRef<HTMLAudioElement | null>(null);
  const sessionIdRef = useRef("");
  const expiresAtRef = useRef<number | null>(null);
  const statusRef = useRef<RealtimeStatus>("idle");
  const attemptRef = useRef(0);
  const disposedRef = useRef(false);
  const endingRef = useRef(false);

  const realtimeProfiles = useMemo(
    () =>
      profiles.filter(
        (profile) =>
          profile.provider === "openai" &&
          profile.operations.includes("realtime_voice") &&
          profile.supports_streaming_input &&
          profile.supports_streaming_output,
      ),
    [profiles],
  );
  const selectedProfile = useMemo(
    () =>
      realtimeProfiles.find(
        (profile) => profile.model_id === selectedModelId,
      ) ??
      realtimeProfiles.find(
        (profile) => profile.model_id === DEFAULT_REALTIME_MODEL,
      ) ??
      realtimeProfiles[0] ??
      null,
    [realtimeProfiles, selectedModelId],
  );
  const isActive = [
    "listening",
    "user_speaking",
    "thinking",
    "assistant_speaking",
    "interrupted",
  ].includes(status);
  const isBusy = [
    "requesting_permission",
    "connecting",
    "ending",
  ].includes(status);
  const canStart =
    !catalogLoading &&
    Boolean(selectedProfile?.invocable) &&
    selectedProfile?.interaction_status === "ready" &&
    !isActive &&
    !isBusy;
  const statusContent = STATUS_CONTENT[status];
  const nearingLimit =
    remainingSeconds !== null &&
    remainingSeconds > 0 &&
    remainingSeconds <= 60;
  const browserSupported =
    typeof window !== "undefined" &&
    "RTCPeerConnection" in window &&
    Boolean(navigator.mediaDevices?.getUserMedia);

  const updateStatus = useCallback((next: RealtimeStatus) => {
    statusRef.current = next;
    if (!disposedRef.current) setStatus(next);
  }, []);

  const closeLocalMedia = useCallback(() => {
    dataChannelRef.current?.close();
    dataChannelRef.current = null;
    const peer = peerRef.current;
    peerRef.current = null;
    if (peer) {
      peer.ontrack = null;
      peer.onconnectionstatechange = null;
      peer.close();
    }
    for (const track of localStreamRef.current?.getTracks() ?? []) {
      track.stop();
    }
    localStreamRef.current = null;
    const remoteAudio = remoteAudioRef.current;
    if (remoteAudio) {
      remoteAudio.pause();
      remoteAudio.srcObject = null;
    }
  }, []);

  const endRemoteSession = useCallback(async (sessionId: string) => {
    if (!sessionId) return;
    try {
      await fetch(
        `/api/multimodal/realtime/calls/${encodeURIComponent(sessionId)}`,
        { method: "DELETE", keepalive: true },
      );
    } catch {
      // The server also enforces the hard expiry, so local cleanup can finish.
    }
  }, []);

  const finishSession = useCallback(
    async (
      nextStatus: "ended" | "reconnect_required",
      nextError = "",
    ) => {
      endingRef.current = true;
      attemptRef.current += 1;
      if (nextStatus === "ended") updateStatus("ending");
      const sessionId = sessionIdRef.current;
      sessionIdRef.current = "";
      expiresAtRef.current = null;
      setRemainingSeconds(null);
      closeLocalMedia();
      await endRemoteSession(sessionId);
      if (disposedRef.current) return;
      setMuted(false);
      setError(nextError);
      updateStatus(nextStatus);
    },
    [closeLocalMedia, endRemoteSession, updateStatus],
  );

  const handleServerEvent = useCallback(
    (event: MessageEvent<string>) => {
      let payload: { type?: string };
      try {
        payload = JSON.parse(event.data) as { type?: string };
      } catch {
        return;
      }
      const eventType = payload.type ?? "";
      if (
        eventType === "session.created" ||
        eventType === "session.updated"
      ) {
        if (statusRef.current === "connecting") updateStatus("listening");
        return;
      }
      if (eventType === "input_audio_buffer.speech_started") {
        updateStatus(
          statusRef.current === "assistant_speaking"
            ? "interrupted"
            : "user_speaking",
        );
        return;
      }
      if (eventType === "input_audio_buffer.speech_stopped") {
        updateStatus("thinking");
        return;
      }
      if (
        eventType === "response.created" ||
        eventType === "response.output_audio.delta" ||
        eventType === "response.audio.delta" ||
        eventType === "output_audio_buffer.started"
      ) {
        updateStatus("assistant_speaking");
        return;
      }
      if (
        eventType === "response.done" ||
        eventType === "output_audio_buffer.stopped"
      ) {
        updateStatus("listening");
        return;
      }
      if (eventType === "error") {
        setError("实时语音服务返回错误。请结束会话后重新连接。");
      }
    },
    [updateStatus],
  );

  const refreshCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError("");
    try {
      const response = await fetch(
        "/api/multimodal/audio/models?refresh=true",
      );
      if (!response.ok) {
        throw new Error(
          await apiError(response, "暂时无法读取实时语音能力。"),
        );
      }
      const payload = (await response.json()) as AudioCatalogResponse;
      if (disposedRef.current) return;
      setProfiles(payload.profiles);
      setCatalogStatus(payload.status);
    } catch (loadError) {
      if (disposedRef.current) return;
      setProfiles([]);
      setCatalogStatus("offline");
      setCatalogError(
        loadError instanceof Error
          ? loadError.message
          : "暂时无法读取实时语音能力。",
      );
    } finally {
      if (!disposedRef.current) setCatalogLoading(false);
    }
  }, []);

  const startSession = useCallback(async () => {
    if (!canStart || !selectedProfile || !browserSupported) {
      if (!browserSupported) {
        setError(
          "当前浏览器不支持所需的麦克风或 WebRTC 能力，请使用最新版 Chrome、Edge 或 Safari。",
        );
        updateStatus("error");
      }
      return;
    }

    const attempt = attemptRef.current + 1;
    attemptRef.current = attempt;
    endingRef.current = false;
    sessionIdRef.current = "";
    expiresAtRef.current = null;
    setRemainingSeconds(null);
    setMuted(false);
    setError("");
    setPlaybackBlocked(false);
    updateStatus("requesting_permission");

    let localStream: MediaStream | null = null;
    let peer: RTCPeerConnection | null = null;
    let createdSessionId = "";
    try {
      localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (attempt !== attemptRef.current || disposedRef.current) {
        localStream.getTracks().forEach((track) => track.stop());
        return;
      }

      updateStatus("connecting");
      peer = new RTCPeerConnection();
      peerRef.current = peer;
      localStreamRef.current = localStream;
      for (const track of localStream.getAudioTracks()) {
        peer.addTrack(track, localStream);
      }

      peer.ontrack = (trackEvent) => {
        const remoteAudio = remoteAudioRef.current;
        const remoteStream = trackEvent.streams[0];
        if (!remoteAudio || !remoteStream) return;
        remoteAudio.srcObject = remoteStream;
        void remoteAudio.play().catch(() => setPlaybackBlocked(true));
      };

      const dataChannel = peer.createDataChannel("oai-events");
      dataChannelRef.current = dataChannel;
      dataChannel.addEventListener("message", handleServerEvent);
      dataChannel.addEventListener("open", () => {
        if (!endingRef.current) updateStatus("listening");
      });

      peer.onconnectionstatechange = () => {
        if (endingRef.current || peerRef.current !== peer) return;
        if (peer?.connectionState === "connected") {
          if (statusRef.current === "connecting") updateStatus("listening");
          return;
        }
        if (
          peer?.connectionState === "failed" ||
          peer?.connectionState === "disconnected" ||
          peer?.connectionState === "closed"
        ) {
          void finishSession(
            "reconnect_required",
            "网络或音频连接已中断。模镜没有自动创建新会话。",
          );
        }
      };

      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      const sdp = peer.localDescription?.sdp;
      if (!sdp) {
        throw new Error("浏览器没有生成有效的语音连接信息。");
      }

      const response = await fetch("/api/multimodal/realtime/calls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp,
          model_id: selectedProfile.model_id,
          voice,
          vad_mode: "semantic_vad",
          language,
        }),
      });
      if (!response.ok) {
        throw new Error(
          await apiError(response, "实时语音连接失败，请稍后重试。"),
        );
      }
      const call = (await response.json()) as RealtimeCallResponse;
      createdSessionId = call.session_id;
      if (
        attempt !== attemptRef.current ||
        endingRef.current ||
        disposedRef.current
      ) {
        await endRemoteSession(createdSessionId);
        return;
      }

      sessionIdRef.current = call.session_id;
      const expiresAt = Date.parse(call.expires_at);
      expiresAtRef.current = Number.isFinite(expiresAt) ? expiresAt : null;
      if (expiresAtRef.current !== null) {
        setRemainingSeconds(
          Math.max(
            0,
            Math.ceil((expiresAtRef.current - Date.now()) / 1000),
          ),
        );
      }
      await peer.setRemoteDescription({
        type: "answer",
        sdp: call.sdp_answer,
      });
    } catch (startError) {
      if (createdSessionId) await endRemoteSession(createdSessionId);
      if (sessionIdRef.current === createdSessionId) {
        sessionIdRef.current = "";
      }
      expiresAtRef.current = null;
      closeLocalMedia();
      if (
        attempt !== attemptRef.current ||
        disposedRef.current ||
        endingRef.current
      ) {
        return;
      }
      setRemainingSeconds(null);
      setError(
        statusRef.current === "requesting_permission"
          ? microphoneError(startError)
          : startError instanceof Error
            ? startError.message
            : "实时语音连接失败，请稍后重试。",
      );
      updateStatus("error");
    }
  }, [
    browserSupported,
    canStart,
    closeLocalMedia,
    endRemoteSession,
    finishSession,
    handleServerEvent,
    language,
    selectedProfile,
    updateStatus,
    voice,
  ]);

  useEffect(() => {
    document.title = "实时语音 · 模镜";
    void refreshCatalog();
  }, [refreshCatalog]);

  useEffect(() => {
    if (!selectedProfile) return;
    if (selectedProfile.model_id !== selectedModelId) {
      setSelectedModelId(selectedProfile.model_id);
    }
    setVoice((current) =>
      selectedProfile.voices.includes(current)
        ? current
        : selectedProfile.voices[0] ?? DEFAULT_REALTIME_VOICE,
    );
  }, [selectedModelId, selectedProfile]);

  useEffect(() => {
    if (!isActive || expiresAtRef.current === null) return undefined;
    const timer = window.setInterval(() => {
      const expiresAt = expiresAtRef.current;
      if (expiresAt === null) return;
      const next = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      setRemainingSeconds(next);
      if (next === 0) {
        void finishSession(
          "ended",
          "本次实时语音已达到 10 分钟上限。",
        );
      }
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [finishSession, isActive]);

  useEffect(() => {
    const handlePageHide = () => {
      endingRef.current = true;
      attemptRef.current += 1;
      closeLocalMedia();
      const sessionId = sessionIdRef.current;
      sessionIdRef.current = "";
      if (sessionId) {
        void fetch(
          `/api/multimodal/realtime/calls/${encodeURIComponent(sessionId)}`,
          { method: "DELETE", keepalive: true },
        );
      }
    };
    window.addEventListener("pagehide", handlePageHide);
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
    };
  }, [closeLocalMedia]);

  useEffect(
    () => {
      disposedRef.current = false;
      return () => {
        disposedRef.current = true;
        endingRef.current = true;
        attemptRef.current += 1;
        closeLocalMedia();
        const sessionId = sessionIdRef.current;
        sessionIdRef.current = "";
        if (sessionId) void endRemoteSession(sessionId);
      };
    },
    [closeLocalMedia, endRemoteSession],
  );

  function toggleMute() {
    const nextMuted = !muted;
    for (const track of localStreamRef.current?.getAudioTracks() ?? []) {
      track.enabled = !nextMuted;
    }
    setMuted(nextMuted);
  }

  const availabilityReason =
    selectedProfile?.interaction_status === "ready"
      ? null
      : selectedProfile?.status_reason ??
        (
          realtimeProfiles.length === 0
            ? "尚未配置可用于实时语音的 OpenAI 连接。"
            : "实时语音当前未启用。"
        );

  return (
    <main className="museum-grid min-h-screen pb-20 pt-5 text-slate-100 lg:pb-12 lg:pt-24">
      <ResourceNav activeResource="models" />
      <div className="mx-auto w-full max-w-[1120px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="border-y border-cyan-300/20 bg-ink-950/72 py-5 backdrop-blur-xl">
          <BrandLogo className="mb-4 lg:hidden" />
          <Link
            className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-sm font-medium text-slate-200 transition hover:border-cyan-300/35 hover:bg-cyan-300/10 hover:text-cyan-100"
            to="/models"
          >
            <ArrowLeft aria-hidden="true" size={15} />
            返回模型招聘会
          </Link>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-cyan-300/30 bg-cyan-300/10 px-3 py-1.5 text-xs font-semibold text-cyan-100">
              实时双向语音
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs text-slate-300">
              纯语音对话 · 单次最多 10 分钟
            </span>
          </div>
          <h1 className="mt-4 text-2xl font-semibold text-white sm:text-4xl">
            与模型实时交谈
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            这是连续语音通话。单轮麦克风转写仍在普通聊天输入框中，两者不会混用。
          </p>
        </header>

        <section className="surface-panel mt-6 overflow-hidden rounded-lg">
          <div className="grid lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-h-[460px] px-5 py-6 sm:px-8 sm:py-8">
              <div
                aria-live="polite"
                className="flex min-h-[330px] flex-col items-center justify-center text-center"
              >
                <div
                  className={`flex h-20 w-20 items-center justify-center rounded-full border ${
                    isActive
                      ? "border-cyan-300/45 bg-cyan-300/12 text-cyan-100"
                      : "border-white/12 bg-white/[0.055] text-slate-300"
                  }`}
                >
                  {muted ? (
                    <MicOff aria-hidden="true" size={30} />
                  ) : (
                    <Radio aria-hidden="true" size={30} />
                  )}
                </div>
                <p className={`mt-5 text-2xl font-semibold ${statusContent.tone}`}>
                  {muted && isActive ? "麦克风已静音" : statusContent.title}
                </p>
                <p className="mt-2 max-w-md text-sm leading-6 text-slate-400">
                  {muted && isActive
                    ? "模型仍可完成当前回答，取消静音后才能继续说话。"
                    : statusContent.detail}
                </p>

                {isActive || status === "ending" ? (
                  <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
                    <button
                      className="inline-flex min-w-28 items-center justify-center gap-2 rounded-full border border-white/12 bg-white/[0.06] px-4 py-2.5 text-sm font-semibold text-white transition hover:border-cyan-300/40 hover:bg-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={status === "ending"}
                      onClick={toggleMute}
                      type="button"
                    >
                      {muted ? (
                        <Mic aria-hidden="true" size={17} />
                      ) : (
                        <MicOff aria-hidden="true" size={17} />
                      )}
                      {muted ? "取消静音" : "静音"}
                    </button>
                    <button
                      className="inline-flex min-w-28 items-center justify-center gap-2 rounded-full bg-rose-300 px-4 py-2.5 text-sm font-semibold text-rose-950 transition hover:bg-rose-200 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={status === "ending"}
                      onClick={() => void finishSession("ended")}
                      type="button"
                    >
                      <PhoneOff aria-hidden="true" size={17} />
                      结束通话
                    </button>
                  </div>
                ) : (
                  <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
                    <button
                      className="inline-flex min-w-40 items-center justify-center gap-2 rounded-full bg-cyan-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                      disabled={!canStart || !browserSupported}
                      onClick={() => void startSession()}
                      type="button"
                    >
                      {status === "reconnect_required" ? (
                        <RefreshCw aria-hidden="true" size={17} />
                      ) : (
                        <Mic aria-hidden="true" size={17} />
                      )}
                      {status === "reconnect_required"
                        ? "重新连接新会话"
                        : "开始实时语音"}
                    </button>
                  </div>
                )}

                {remainingSeconds !== null ? (
                  <p
                    className={`mt-5 text-sm font-medium ${
                      nearingLimit ? "text-amber-100" : "text-slate-400"
                    }`}
                  >
                    剩余 {formatRemaining(remainingSeconds)}
                    {nearingLimit ? "，会话即将自动结束" : ""}
                  </p>
                ) : null}
                {playbackBlocked ? (
                  <button
                    className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-cyan-100 underline decoration-cyan-300/30 underline-offset-4"
                    onClick={() => {
                      void remoteAudioRef.current
                        ?.play()
                        .then(() => setPlaybackBlocked(false));
                    }}
                    type="button"
                  >
                    <Volume2 aria-hidden="true" size={16} />
                    恢复模型声音
                  </button>
                ) : null}
                {error ? (
                  <p
                    className="mt-5 max-w-lg rounded-md bg-rose-300/10 px-4 py-3 text-sm leading-6 text-rose-100"
                    role="alert"
                  >
                    {error}
                  </p>
                ) : null}
              </div>
              <audio
                autoPlay
                className="hidden"
                onCanPlay={() => setPlaybackBlocked(false)}
                playsInline
                ref={remoteAudioRef}
              />
            </div>

            <aside className="border-t border-white/10 bg-ink-950/36 px-5 py-6 lg:border-l lg:border-t-0">
              <h2 className="text-base font-semibold text-white">通话设置</h2>
              <p className="mt-1 text-sm leading-6 text-slate-400">
                建连后模型与声音会锁定。网络中断不会自动重连。
              </p>

              <div className="mt-5 space-y-4">
                <label className="block">
                  <span className="text-sm font-medium text-slate-200">模型</span>
                  <select
                    className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none transition focus:border-cyan-300/50"
                    disabled={isActive || isBusy || catalogLoading}
                    onChange={(event) => setSelectedModelId(event.target.value)}
                    value={selectedProfile?.model_id ?? selectedModelId}
                  >
                    {(realtimeProfiles.length > 0
                      ? realtimeProfiles
                      : [
                          {
                            model_id: DEFAULT_REALTIME_MODEL,
                            display_name: modelLabel(DEFAULT_REALTIME_MODEL),
                          },
                          {
                            model_id: "gpt-realtime-2.1",
                            display_name: modelLabel("gpt-realtime-2.1"),
                          },
                        ]
                    ).map((profile) => (
                      <option key={profile.model_id} value={profile.model_id}>
                        {modelLabel(profile.model_id)}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block">
                  <span className="text-sm font-medium text-slate-200">声音</span>
                  <select
                    className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none transition focus:border-cyan-300/50"
                    disabled={isActive || isBusy}
                    onChange={(event) => setVoice(event.target.value)}
                    value={voice}
                  >
                    {(selectedProfile?.voices.length
                      ? selectedProfile.voices
                      : ["marin", "cedar"]
                    ).map((item) => (
                      <option key={item} value={item}>
                        {item === "marin" ? "Marin（推荐）" : "Cedar"}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block">
                  <span className="text-sm font-medium text-slate-200">主要语言</span>
                  <select
                    className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none transition focus:border-cyan-300/50"
                    disabled={isActive || isBusy}
                    onChange={(event) => setLanguage(event.target.value)}
                    value={language}
                  >
                    <option value="zh-CN">简体中文</option>
                    <option value="en-US">English</option>
                    <option value="ja-JP">日本語</option>
                    <option value="ko-KR">한국어</option>
                  </select>
                </label>
              </div>

              {catalogLoading ? (
                <p className="mt-5 text-sm text-slate-400">正在检查实时语音能力...</p>
              ) : availabilityReason || catalogError ? (
                <div className="mt-5 rounded-md bg-amber-300/[0.08] px-3 py-3 text-sm leading-6 text-amber-100">
                  <p>{catalogError || availabilityReason}</p>
                  <div className="mt-2 flex flex-wrap gap-3">
                    <Link
                      className="font-semibold underline decoration-amber-200/30 underline-offset-4"
                      to="/settings"
                    >
                      检查模型服务连接
                    </Link>
                    <button
                      className="font-semibold underline decoration-amber-200/30 underline-offset-4"
                      onClick={() => void refreshCatalog()}
                      type="button"
                    >
                      重新检查
                    </button>
                  </div>
                </div>
              ) : catalogStatus === "stale" ? (
                <p className="mt-5 text-sm leading-6 text-amber-100">
                  当前使用最近一次能力结果，建议在开始前重新检查。
                </p>
              ) : null}

              <div className="mt-6 border-t border-white/10 pt-5 text-sm leading-6 text-slate-400">
                <p>实时语音可能产生额外费用，费用以 OpenAI 结算为准。</p>
                <p className="mt-2">
                  本批次不组合资料库、Skill、工具、附件或智能调度。
                </p>
                <p className="mt-2">
                  模镜不保存原始音频和实时字幕。
                </p>
              </div>
            </aside>
          </div>
        </section>
      </div>
    </main>
  );
}
