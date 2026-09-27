import { CircleAlert, Mic, PhoneOff, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

interface RealtimeCertificationPanelProps {
  csrfToken: string;
  connectionId: string;
  modelId: string;
  disabled?: boolean;
  onComplete: () => Promise<void> | void;
}

interface CertificationSessionResponse {
  certification_id: string;
  status: string;
  answer_sdp: string | null;
  expires_at: string | null;
  provider_dispatch_state: string;
  retry_allowed: false;
}

interface CertificationSummary {
  status: string;
  error_code?: string | null;
}

type BrowserFailureCode =
  | "provider_realtime_browser_answer_missing"
  | "provider_realtime_browser_component_disposed"
  | "provider_realtime_browser_remote_description_failed"
  | "provider_realtime_browser_session_setup_failed";

type BrowserDiagnosticCode =
  | "provider_realtime_browser_exception_invalid_access_error"
  | "provider_realtime_browser_exception_invalid_modification_error"
  | "provider_realtime_browser_exception_operation_error"
  | "provider_realtime_browser_exception_type_error"
  | "provider_realtime_browser_exception_unknown"
  | "provider_realtime_answer_audio_media_missing"
  | "provider_realtime_answer_application_media_missing"
  | "provider_realtime_answer_bundle_missing"
  | "provider_realtime_answer_dtls_fingerprint_missing"
  | "provider_realtime_answer_ice_credentials_missing"
  | "provider_realtime_answer_setup_missing"
  | "provider_realtime_answer_rtpmap_missing"
  | "provider_realtime_answer_candidate_missing"
  | "provider_realtime_answer_terminal_crlf_missing"
  | "provider_realtime_answer_non_crlf_line_endings";

type BrowserFailure = Error & {
  browserCode: BrowserFailureCode;
  browserDiagnostics: BrowserDiagnosticCode[];
};

type Phase =
  | "idle"
  | "confirming"
  | "requesting_permission"
  | "connecting"
  | "active"
  | "completing"
  | "passed"
  | "failed"
  | "uncertain";

interface SafeResponseError {
  code: string;
  message: string;
}

async function readError(
  response: Response,
  fallback: string,
): Promise<SafeResponseError> {
  try {
    const payload = (await response.json()) as {
      detail?: string | { code?: string; message?: string };
    };
    if (typeof payload.detail === "string") {
      return { code: "", message: payload.detail };
    }
    if (payload.detail && typeof payload.detail.message === "string") {
      return {
        code: typeof payload.detail.code === "string" ? payload.detail.code : "",
        message: payload.detail.message,
      };
    }
  } catch {
    // Never expose an upstream body from this browser-assisted flow.
  }
  return { code: "", message: fallback };
}

function certificationKey() {
  return `realtime-cert-${window.crypto.randomUUID()}`;
}

function browserFailure(
  browserCode: BrowserFailureCode,
  message: string,
  browserDiagnostics: BrowserDiagnosticCode[] = [],
): BrowserFailure {
  return Object.assign(new Error(message), {
    browserCode,
    browserDiagnostics,
  });
}

function remoteDescriptionDiagnostics(
  answerSdp: string,
  reason: unknown,
): BrowserDiagnosticCode[] {
  const codes: BrowserDiagnosticCode[] = [];
  if (reason instanceof DOMException) {
    const exceptionCodes: Record<string, BrowserDiagnosticCode> = {
      InvalidAccessError:
        "provider_realtime_browser_exception_invalid_access_error",
      InvalidModificationError:
        "provider_realtime_browser_exception_invalid_modification_error",
      OperationError: "provider_realtime_browser_exception_operation_error",
    };
    codes.push(
      exceptionCodes[reason.name] ??
        "provider_realtime_browser_exception_unknown",
    );
  } else if (reason instanceof TypeError) {
    codes.push("provider_realtime_browser_exception_type_error");
  } else {
    codes.push("provider_realtime_browser_exception_unknown");
  }

  const lines = answerSdp.split(/\r\n|\n|\r/);
  const hasLine = (prefix: string) =>
    lines.some((line) => line.startsWith(prefix));
  if (!hasLine("m=audio ")) {
    codes.push("provider_realtime_answer_audio_media_missing");
  }
  if (!hasLine("m=application ")) {
    codes.push("provider_realtime_answer_application_media_missing");
  }
  if (!hasLine("a=group:BUNDLE")) {
    codes.push("provider_realtime_answer_bundle_missing");
  }
  if (!hasLine("a=fingerprint:")) {
    codes.push("provider_realtime_answer_dtls_fingerprint_missing");
  }
  if (!hasLine("a=ice-ufrag:") || !hasLine("a=ice-pwd:")) {
    codes.push("provider_realtime_answer_ice_credentials_missing");
  }
  if (!hasLine("a=setup:")) {
    codes.push("provider_realtime_answer_setup_missing");
  }
  if (!hasLine("a=rtpmap:")) {
    codes.push("provider_realtime_answer_rtpmap_missing");
  }
  if (!hasLine("a=candidate:")) {
    codes.push("provider_realtime_answer_candidate_missing");
  }
  if (!answerSdp.endsWith("\r\n")) {
    codes.push("provider_realtime_answer_terminal_crlf_missing");
  }
  if (!answerSdp.includes("\r\n")) {
    codes.push("provider_realtime_answer_non_crlf_line_endings");
  }
  return codes;
}

function waitForIceGatheringComplete(peer: RTCPeerConnection) {
  if (peer.iceGatheringState === "complete") return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      window.clearTimeout(timeoutId);
      peer.removeEventListener("icegatheringstatechange", onStateChange);
    };
    const onStateChange = () => {
      if (peer.iceGatheringState !== "complete") return;
      cleanup();
      resolve();
    };
    const timeoutId = window.setTimeout(() => {
      cleanup();
      reject(new Error("浏览器 ICE 候选收集超时；未创建 Provider 会话。"));
    }, 10_000);
    peer.addEventListener("icegatheringstatechange", onStateChange);
    onStateChange();
  });
}

export default function RealtimeCertificationPanel({
  csrfToken,
  connectionId,
  modelId,
  disabled = false,
  onComplete,
}: RealtimeCertificationPanelProps) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [remoteTrackObserved, setRemoteTrackObserved] = useState(false);
  const [responseDoneObserved, setResponseDoneObserved] = useState(false);
  const [playbackBlocked, setPlaybackBlocked] = useState(false);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const peerRef = useRef<RTCPeerConnection | null>(null);
  const dataChannelRef = useRef<RTCDataChannel | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const certificationIdRef = useRef("");
  const probeSentRef = useRef(false);
  const startingRef = useRef(false);
  const completingRef = useRef(false);
  const disposedRef = useRef(false);

  const closeLocalMedia = useCallback(() => {
    const dataChannel = dataChannelRef.current;
    dataChannelRef.current = null;
    if (dataChannel) {
      dataChannel.onopen = null;
      dataChannel.onmessage = null;
      dataChannel.onerror = null;
      dataChannel.close();
    }
    const peer = peerRef.current;
    peerRef.current = null;
    if (peer) {
      peer.ontrack = null;
      peer.onconnectionstatechange = null;
      peer.close();
    }
    for (const track of streamRef.current?.getTracks() ?? []) track.stop();
    streamRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.srcObject = null;
    }
  }, []);

  const resumeCurrentAudio = useCallback(async () => {
    const audio = audioRef.current;
    if (!audio) return;
    try {
      await audio.play();
      setPlaybackBlocked(false);
    } catch {
      setPlaybackBlocked(true);
      setError(
        "浏览器仍阻止当前实时音频播放；不会创建新的 Provider 会话。",
      );
    }
  }, []);

  const completeRemote = useCallback(
    async (
      certificationId: string,
      mediaObserved: boolean,
      keepalive = false,
      browserErrorCode: BrowserFailureCode | null = null,
      browserDiagnosticCodes: BrowserDiagnosticCode[] = [],
    ) => {
      if (!certificationId || completingRef.current) return;
      completingRef.current = true;
      certificationIdRef.current = "";
      closeLocalMedia();
      if (!disposedRef.current) setPhase("completing");
      try {
        const response = await fetch(
          `/api/router/certifications/realtime/${encodeURIComponent(certificationId)}/complete`,
          {
            method: "POST",
            keepalive,
            headers: {
              "Content-Type": "application/json",
              "X-ModelMirror-CSRF": csrfToken,
            },
            body: JSON.stringify({
              media_observed: mediaObserved,
              hangup_observed: true,
              browser_error_code: browserErrorCode,
              browser_diagnostic_codes: browserDiagnosticCodes,
            }),
          },
        );
        if (!response.ok) {
          const failure = await readError(
            response,
            "Realtime 认证结束状态无法确认；系统不会自动创建新会话。",
          );
          throw new Error(failure.message);
        }
        const result = (await response.json()) as CertificationSummary;
        if (disposedRef.current) return;
        if (result.status === "passed") {
          setPhase("passed");
          setMessage("Realtime SDP、远端媒体与 Hangup 已完成人工认证。");
        } else {
          setPhase("failed");
          setMessage("");
          setError(`Realtime 资格未通过：${result.error_code ?? result.status}`);
        }
        await onComplete();
      } catch (reason) {
        if (disposedRef.current) return;
        setPhase("uncertain");
        setMessage("");
        setError(
          reason instanceof Error
            ? reason.message
            : "Realtime 认证结束状态无法确认；系统不会自动创建新会话。",
        );
      } finally {
        completingRef.current = false;
      }
    },
    [closeLocalMedia, csrfToken, onComplete],
  );

  const start = useCallback(async () => {
    if (startingRef.current || disabled || !connectionId || !modelId.trim()) return;
    startingRef.current = true;
    setPhase("requesting_permission");
    setError("");
    setMessage("");
    setRemoteTrackObserved(false);
    setResponseDoneObserved(false);
    setPlaybackBlocked(false);
    probeSentRef.current = false;
    let peer: RTCPeerConnection | null = null;
    let stream: MediaStream | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (disposedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      setPhase("connecting");
      peer = new RTCPeerConnection();
      peerRef.current = peer;
      streamRef.current = stream;
      for (const track of stream.getAudioTracks()) peer.addTrack(track, stream);
      const dataChannel = peer.createDataChannel("oai-events");
      dataChannelRef.current = dataChannel;
      dataChannel.onopen = () => {
        if (probeSentRef.current || dataChannel.readyState !== "open") return;
        probeSentRef.current = true;
        dataChannel.send(JSON.stringify({
          type: "response.create",
          response: {
            input: [],
            output_modalities: ["audio"],
            instructions:
              "Say exactly: ModelMirror realtime certification audio is working.",
          },
        }));
      };
      dataChannel.onmessage = (event) => {
        try {
          const serverEvent = JSON.parse(String(event.data)) as {
            type?: unknown;
            response?: { status?: unknown };
          };
          if (serverEvent.type === "response.done") {
            if (
              typeof serverEvent.response?.status === "string" &&
              serverEvent.response.status !== "completed"
            ) {
              setError(
                "Provider 未完成固定认证响应；不会自动重试或创建新会话。",
              );
              return;
            }
            setResponseDoneObserved(true);
          } else if (serverEvent.type === "error") {
            setError(
              "Provider 拒绝固定认证响应；不会自动重试或创建新会话。",
            );
          }
        } catch {
          // Ignore unrelated or malformed event payloads without persisting them.
        }
      };
      dataChannel.onerror = () => {
        setError(
          "Realtime 控制通道异常；不会自动重试或创建新会话。",
        );
      };
      peer.ontrack = (event) => {
        if (!audioRef.current) return;
        const remoteStream =
          event.streams[0] ?? new MediaStream([event.track]);
        audioRef.current.srcObject = remoteStream;
        setRemoteTrackObserved(true);
        void audioRef.current.play().catch(() => setPlaybackBlocked(true));
      };
      peer.onconnectionstatechange = () => {
        if (
          peer?.connectionState === "failed" ||
          peer?.connectionState === "disconnected" ||
          peer?.connectionState === "closed"
        ) {
          setError("媒体连接已中断；不会自动重连或创建第二个 Provider 会话。");
        }
      };

      const offer = await peer.createOffer();
      await peer.setLocalDescription(offer);
      await waitForIceGatheringComplete(peer);
      const offerSdp = peer.localDescription?.sdp;
      if (!offerSdp) throw new Error("浏览器没有生成有效 SDP Offer。");

      const response = await fetch(
        `/api/router/connections/${encodeURIComponent(connectionId)}/certifications/realtime/session`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": certificationKey(),
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            model_id: modelId.trim(),
            adapter_contract: "openai_realtime_sdp_v1",
            offer_sdp: offerSdp,
            acknowledge_billed_call: true,
          }),
        },
      );
      if (!response.ok) {
        const failure = await readError(
          response,
          "Realtime 认证会话创建失败；系统不会自动重试。",
        );
        const responseError = new Error(failure.message) as Error & {
          code?: string;
        };
        responseError.code = failure.code;
        throw responseError;
      }
      const result = (await response.json()) as CertificationSessionResponse;
      certificationIdRef.current = result.certification_id;
      if (!result.answer_sdp) {
        throw browserFailure(
          "provider_realtime_browser_answer_missing",
          "Provider 未返回有效 SDP Answer；系统不会自动重试。",
        );
      }
      if (disposedRef.current) {
        await completeRemote(
          result.certification_id,
          false,
          true,
          "provider_realtime_browser_component_disposed",
        );
        return;
      }
      try {
        await peer.setRemoteDescription({
          type: "answer",
          sdp: result.answer_sdp,
        });
      } catch (reason) {
        throw browserFailure(
          "provider_realtime_browser_remote_description_failed",
          "浏览器无法应用 Provider SDP Answer；会话已结束且不会自动重试。",
          remoteDescriptionDiagnostics(result.answer_sdp, reason),
        );
      }
      setPhase("active");
      setMessage("媒体会话已建立。请确认能听到远端声音后再结束认证。");
    } catch (reason) {
      const certificationId = certificationIdRef.current;
      closeLocalMedia();
      if (certificationId) {
        await completeRemote(
          certificationId,
          false,
          false,
          (reason as Partial<BrowserFailure> | null)?.browserCode ??
            "provider_realtime_browser_session_setup_failed",
          (reason as Partial<BrowserFailure> | null)?.browserDiagnostics ?? [],
        );
      } else if (!disposedRef.current) {
        setPhase(
          (reason as { code?: string } | null)?.code ===
            "provider_realtime_create_result_uncertain"
            ? "uncertain"
            : "failed",
        );
        setError(
          reason instanceof Error
            ? reason.message
            : "Realtime 认证会话创建失败；系统不会自动重试。",
        );
      }
    } finally {
      startingRef.current = false;
    }
  }, [
    closeLocalMedia,
    completeRemote,
    connectionId,
    csrfToken,
    disabled,
    modelId,
  ]);

  useEffect(() => {
    disposedRef.current = false;
    const failClosed = () => {
      disposedRef.current = true;
      startingRef.current = false;
      const certificationId = certificationIdRef.current;
      certificationIdRef.current = "";
      closeLocalMedia();
      if (!certificationId || completingRef.current) return;
      completingRef.current = true;
      void fetch(
        `/api/router/certifications/realtime/${encodeURIComponent(certificationId)}/complete`,
        {
          method: "POST",
          keepalive: true,
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            media_observed: false,
            hangup_observed: true,
            browser_error_code:
              "provider_realtime_browser_component_disposed",
          }),
        },
      );
    };
    window.addEventListener("pagehide", failClosed);
    return () => {
      window.removeEventListener("pagehide", failClosed);
      failClosed();
    };
  }, [closeLocalMedia, csrfToken]);

  const busy = [
    "requesting_permission",
    "connecting",
    "active",
    "completing",
  ].includes(phase);

  return (
    <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.05] p-3">
      <p className="text-xs font-semibold text-cyan-100">浏览器辅助 Realtime SDP 认证</p>
      <p className="mt-1 text-xs leading-5 text-slate-300">
        仅支持官方 OpenAI `/v1/realtime/calls`。浏览器生成 SDP 并播放远端媒体；标准 Provider Key 只在后端内存中使用。SDP、音频和转录不写入控制面存储。
      </p>
      <audio
        aria-label="Realtime 认证远端音频"
        autoPlay
        className="hidden"
        ref={audioRef}
      />
      {phase === "confirming" ? (
        <div aria-modal="true" className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-3" role="dialog">
          <p className="text-xs font-semibold text-amber-100">确认一次真实付费 Realtime 会话</p>
          <p className="mt-1 text-xs leading-5 text-slate-300">
            最多创建一个 Provider 会话，不自动重试、重连或切换 Provider。请允许麦克风，并在听到远端音频后人工确认 Hangup。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button className="rounded-full bg-amber-200 px-3 py-1.5 text-xs font-semibold text-ink-950" onClick={() => void start()} type="button">确认并建立会话</button>
            <button className="rounded-full border border-white/15 px-3 py-1.5 text-xs text-slate-200" onClick={() => setPhase("idle")} type="button">取消</button>
          </div>
        </div>
      ) : null}
      {phase === "active" ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-slate-300">
            远端媒体轨道：{remoteTrackObserved ? "已观察" : "等待中"}
            ；固定响应：{responseDoneObserved ? "已完成" : "等待中"}
            {playbackBlocked ? "；浏览器阻止了自动播放" : ""}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              className="rounded-full border border-cyan-200/25 px-3 py-1.5 text-xs text-cyan-100 disabled:opacity-40"
              disabled={!remoteTrackObserved}
              onClick={() => void resumeCurrentAudio()}
              type="button"
            >
              {playbackBlocked
                ? "允许播放当前实时音频"
                : "恢复当前实时播放"}
            </button>
            <button className="rounded-full bg-emerald-200 px-3 py-1.5 text-xs font-semibold text-ink-950 disabled:opacity-40" disabled={!remoteTrackObserved || !responseDoneObserved} onClick={() => void completeRemote(certificationIdRef.current, true)} type="button">确认听到音频并结束</button>
            <button className="inline-flex items-center gap-1 rounded-full border border-rose-300/25 px-3 py-1.5 text-xs text-rose-100" onClick={() => void completeRemote(certificationIdRef.current, false)} type="button"><PhoneOff className="h-3.5 w-3.5" />结束并标记未通过</button>
          </div>
          <p className="text-xs leading-5 text-slate-400">
            系统只会请求一次固定合成短句，无需发送用户语音。当前音频是不可定位的实时流；恢复播放不会重放已经过去的内容，也不会创建新的 Provider 会话。
          </p>
        </div>
      ) : null}
      {phase !== "confirming" && phase !== "active" && phase !== "uncertain" ? (
        <button className="mt-3 inline-flex items-center gap-2 rounded-full bg-cyan-200 px-4 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40" disabled={disabled || busy || !connectionId || !modelId.trim()} onClick={() => setPhase("confirming")} type="button">
          {busy ? <Mic className="h-4 w-4 animate-pulse" /> : <ShieldCheck className="h-4 w-4" />}
          {busy ? "Realtime 认证进行中" : "开始浏览器辅助认证"}
        </button>
      ) : null}
      {message ? <p className="mt-3 text-xs text-emerald-100" role="status">{message}</p> : null}
      {error ? <p className="mt-3 flex items-start gap-2 text-xs leading-5 text-rose-100" role="alert"><CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />{error}</p> : null}
      {phase === "uncertain" ? <p className="mt-2 text-xs leading-5 text-amber-100">结果不确定时请先在 Provider 侧核查会话；不要使用同一认证键或自动创建新会话。</p> : null}
    </div>
  );
}
