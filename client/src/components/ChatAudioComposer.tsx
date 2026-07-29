import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import {
  FileAudio,
  Loader2,
  Mic,
  RotateCcw,
  Send,
  Square,
  Trash2,
  X,
} from "lucide-react";
import {
  AUDIO_ACCEPT,
  LANGUAGE_OPTIONS,
  MAX_AUDIO_BYTES,
  fileExtension,
  formatBytes,
  requestTranscription,
  validateAudioFile,
  type TranscriptionResponse,
} from "./TranscriptionWorkspace";

type AudioSource = "upload" | "record";
type AudioComposerPanel = "upload" | "settings";
type AudioProcessingMode = "transcribe" | "direct";
type AudioActivity =
  | "idle"
  | "recording"
  | "uploading"
  | "transcribing"
  | "sending";

interface AudioChatProfile {
  model_id: string;
  display_name: string;
  invocable: boolean;
  interaction_status: "ready" | "planned" | "disabled";
  chat_modes: Array<
    | "direct_audio_input"
    | "native_streaming_audio_output"
    | "transcribe"
    | "synthesize_speech"
  >;
  input_formats: string[];
  output_formats: string[];
  voices: string[];
}

interface AudioCatalogResponse {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  microphone_enabled: boolean;
  profiles: AudioChatProfile[];
}

interface AttachmentResponse {
  attachment_id: string;
  kind: "audio";
  format: string;
  bytes: number;
}

interface PendingAttachment {
  attachmentId: string;
  file: File;
}

interface ChatAudioComposerProps {
  currentModelId: string;
  initialSource: AudioSource;
  initialTranscriptionModelId?: string;
  isAutoRoute: boolean;
  isSending: boolean;
  prompt: string;
  directBlockedReason?: string;
  onClose: () => void;
  onFillTranscript: (text: string) => void;
  onSendTranscript: (text: string) => Promise<boolean>;
  onSendDirectAudio: (
    attachmentId: string,
    audioName: string,
  ) => Promise<boolean>;
}

const MAX_RECORDING_SECONDS = 5 * 60;
const STT_MODEL_SESSION_KEY = "modelmirror-chat-stt-model";
const STT_LANGUAGE_SESSION_KEY = "modelmirror-chat-stt-language";
const AUDIO_PROCESSING_MODE_SESSION_KEY =
  "modelmirror-chat-audio-processing-mode";
const AUDIO_SETTINGS_CHANGED_EVENT = "modelmirror:audio-settings-changed";
const DEFAULT_STT_MODEL_ID = "x-ai/grok-stt-1.0";
const RECORDER_FORMATS = [
  { mime: "audio/webm;codecs=opus", extension: "webm" },
  { mime: "audio/ogg;codecs=opus", extension: "ogg" },
  { mime: "audio/mp4;codecs=mp4a.40.2", extension: "m4a" },
  { mime: "audio/mp4", extension: "m4a" },
] as const;

function formatDuration(totalSeconds: number) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function apiErrorMessage(payload: unknown, status: number) {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (typeof record.error === "string" && record.error.trim()) {
      return record.error;
    }
    if (typeof record.detail === "string" && record.detail.trim()) {
      return record.detail;
    }
  }
  if (status === 413) return "音频超过 25 MiB，请缩短或压缩后重试。";
  if (status === 429) return "请求较多，请稍后重试。";
  return "音频处理没有完成，请检查连接后重试。";
}

async function readApiPayload(response: Response) {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

function recorderErrorMessage(error: unknown) {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") {
      return "麦克风权限未开启。请在浏览器地址栏的权限设置中允许麦克风，然后重试。";
    }
    if (error.name === "NotFoundError") {
      return "没有检测到可用麦克风，请连接设备后重试。";
    }
    if (error.name === "NotReadableError") {
      return "麦克风正被其他应用占用，请关闭占用程序后重试。";
    }
    if (
      error.name === "SecurityError" ||
      error.name === "NotSupportedError"
    ) {
      return "当前浏览器环境不支持录音，请改用音频文件上传。";
    }
  }
  return "无法开始录音，请检查麦克风和浏览器权限后重试。";
}

function selectedRecorderFormat() {
  if (
    typeof MediaRecorder === "undefined" ||
    typeof MediaRecorder.isTypeSupported !== "function"
  ) {
    return null;
  }
  return (
    RECORDER_FORMATS.find(({ mime }) =>
      MediaRecorder.isTypeSupported(mime),
    ) ?? null
  );
}

interface QuickTranscriptionControlProps {
  currentModelId: string;
  disabled: boolean;
  enabled: boolean;
  isAutoRoute: boolean;
  directBlockedReason?: string;
  onError: (message: string) => void;
  onSendDirectAudio: (
    attachmentId: string,
    audioName: string,
  ) => Promise<boolean>;
  onTranscript: (text: string) => void;
}

type QuickTranscriptionActivity =
  | "idle"
  | "recording"
  | "transcribing"
  | "sending";

interface AudioSettingsEventDetail {
  language: string;
  processingMode: AudioProcessingMode;
  sttModelId: string;
}

function storedProcessingMode(): AudioProcessingMode {
  return window.sessionStorage.getItem(
    AUDIO_PROCESSING_MODE_SESSION_KEY,
  ) === "direct"
    ? "direct"
    : "transcribe";
}

function publishAudioSettings(detail: AudioSettingsEventDetail) {
  if (detail.sttModelId) {
    window.sessionStorage.setItem(
      STT_MODEL_SESSION_KEY,
      detail.sttModelId,
    );
  }
  window.sessionStorage.setItem(STT_LANGUAGE_SESSION_KEY, detail.language);
  window.sessionStorage.setItem(
    AUDIO_PROCESSING_MODE_SESSION_KEY,
    detail.processingMode,
  );
  window.dispatchEvent(
    new CustomEvent<AudioSettingsEventDetail>(
      AUDIO_SETTINGS_CHANGED_EVENT,
      { detail },
    ),
  );
}

export function QuickTranscriptionControl({
  currentModelId,
  disabled,
  enabled,
  isAutoRoute,
  directBlockedReason,
  onError,
  onSendDirectAudio,
  onTranscript,
}: QuickTranscriptionControlProps) {
  const [catalog, setCatalog] = useState<AudioCatalogResponse | null>(null);
  const [selectedModelId, setSelectedModelId] = useState(
    () =>
      window.sessionStorage.getItem(STT_MODEL_SESSION_KEY) ??
      DEFAULT_STT_MODEL_ID,
  );
  const [language, setLanguage] = useState(
    () =>
      window.sessionStorage.getItem(STT_LANGUAGE_SESSION_KEY) ?? "auto",
  );
  const [processingMode, setProcessingMode] =
    useState<AudioProcessingMode>(storedProcessingMode);
  const [activity, setActivity] =
    useState<QuickTranscriptionActivity>("idle");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const bytesRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);

  const sttProfiles = useMemo(
    () =>
      (catalog?.profiles ?? []).filter(
        (profile) =>
          profile.invocable &&
          profile.interaction_status === "ready" &&
          profile.chat_modes.includes("transcribe"),
      ),
    [catalog],
  );
  const directProfile = useMemo(
    () =>
      (catalog?.profiles ?? []).find(
        (profile) =>
          profile.model_id === currentModelId &&
          profile.invocable &&
          profile.interaction_status === "ready" &&
          profile.chat_modes.includes("direct_audio_input"),
      ),
    [catalog, currentModelId],
  );
  const directAvailable = Boolean(
    !isAutoRoute && directProfile && !directBlockedReason,
  );

  const stopTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/multimodal/audio/models", { signal: controller.signal })
      .then(async (response) => {
        const payload = await readApiPayload(response);
        if (!response.ok) {
          throw new Error(apiErrorMessage(payload, response.status));
        }
        return payload as AudioCatalogResponse;
      })
      .then((nextCatalog) => {
        if (mountedRef.current) setCatalog(nextCatalog);
      })
      .catch((loadError) => {
        if (
          loadError instanceof DOMException &&
          loadError.name === "AbortError"
        ) {
          return;
        }
        if (mountedRef.current) {
          onError(
            loadError instanceof Error
              ? loadError.message
              : "暂时无法加载转写模型，请稍后重试。",
          );
        }
      });
    return () => controller.abort();
  }, [onError]);

  useEffect(() => {
    if (sttProfiles.length === 0) return;
    const stored = window.sessionStorage.getItem(STT_MODEL_SESSION_KEY);
    const candidates = [
      stored,
      DEFAULT_STT_MODEL_ID,
      "microsoft/mai-transcribe-1.5",
      "openai/gpt-4o-mini-transcribe",
      sttProfiles[0]?.model_id,
    ].filter((value): value is string => Boolean(value));
    const nextId =
      candidates.find((candidate) =>
        sttProfiles.some((profile) => profile.model_id === candidate),
      ) ?? sttProfiles[0].model_id;
    setSelectedModelId((current) =>
      sttProfiles.some((profile) => profile.model_id === current)
        ? current
        : nextId,
    );
  }, [sttProfiles]);

  useEffect(() => {
    function handleSettingsChanged(event: Event) {
      const detail = (event as CustomEvent<AudioSettingsEventDetail>).detail;
      if (!detail) return;
      setSelectedModelId(detail.sttModelId);
      setLanguage(detail.language);
      setProcessingMode(detail.processingMode);
    }
    window.addEventListener(
      AUDIO_SETTINGS_CHANGED_EVENT,
      handleSettingsChanged,
    );
    return () =>
      window.removeEventListener(
        AUDIO_SETTINGS_CHANGED_EVENT,
        handleSettingsChanged,
      );
  }, []);

  useEffect(
    () => {
      mountedRef.current = true;
      return () => {
        mountedRef.current = false;
        abortRef.current?.abort();
        clearTimer();
        if (
          recorderRef.current &&
          recorderRef.current.state !== "inactive"
        ) {
          recorderRef.current.stop();
        }
        stopTracks();
      };
    },
    [clearTimer, stopTracks],
  );

  const processRecording = useCallback(
    async (recordedFile: File) => {
      if (processingMode === "transcribe" && !selectedModelId) {
        setActivity("idle");
        onError("请先在“转写设置”中选择可用模型。");
        return;
      }
      const controller = new AbortController();
      abortRef.current = controller;
      setActivity(
        processingMode === "transcribe" ? "transcribing" : "sending",
      );
      try {
        if (processingMode === "direct") {
          const format = fileExtension(recordedFile);
          if (!directAvailable || !directProfile) {
            throw new Error(
              directBlockedReason ||
                "当前模型不支持直接理解音频，请在音频设置中选择“先转成文字”。",
            );
          }
          if (!directProfile.input_formats.includes(format)) {
            throw new Error(
              `当前模型不能直接理解 ${format.toUpperCase()} 录音，请在音频设置中选择“先转成文字”。`,
            );
          }

          const body = new FormData();
          body.append("kind", "audio");
          body.append("file", recordedFile, recordedFile.name);
          const response = await fetch(
            "/api/multimodal/chat/attachments",
            {
              method: "POST",
              body,
              signal: controller.signal,
            },
          );
          const payload = await readApiPayload(response);
          if (!response.ok) {
            throw new Error(apiErrorMessage(payload, response.status));
          }
          const attachment = payload as AttachmentResponse;
          const succeeded = await onSendDirectAudio(
            attachment.attachment_id,
            recordedFile.name,
          );
          if (!succeeded) {
            void fetch(
              `/api/multimodal/chat/attachments/${encodeURIComponent(
                attachment.attachment_id,
              )}`,
              { method: "DELETE" },
            ).catch(() => undefined);
            throw new Error("音频发送没有完成，请重新录音后重试。");
          }
          onError("");
          return;
        }

        const result = await requestTranscription({
          file: recordedFile,
          language,
          modelId: selectedModelId,
          signal: controller.signal,
          onProgress: () => undefined,
        });
        if (!mountedRef.current) return;
        const text = result.text.trim();
        if (!text) {
          throw new Error("没有识别到可用文字，请重新录音。");
        }
        onTranscript(text);
        onError("");
      } catch (transcriptionError) {
        if (!mountedRef.current) return;
        if (
          transcriptionError instanceof DOMException &&
          transcriptionError.name === "AbortError"
        ) {
          onError("转写已取消，请重新录音。");
        } else {
          onError(
            transcriptionError instanceof Error
              ? transcriptionError.message
              : "转写没有完成，请重新录音或上传音频。",
          );
        }
      } finally {
        if (mountedRef.current) setActivity("idle");
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [
      directAvailable,
      directBlockedReason,
      directProfile,
      language,
      onError,
      onSendDirectAudio,
      onTranscript,
      processingMode,
      selectedModelId,
    ],
  );

  const startQuickRecording = useCallback(async () => {
    if (disabled || !enabled || activity !== "idle") return;
    const format = selectedRecorderFormat();
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined" ||
      !format
    ) {
      onError("当前浏览器不支持可用的录音格式，请改用音频文件上传。");
      return;
    }
    if (processingMode === "transcribe" && !selectedModelId) {
      onError("转写模型尚未就绪，请稍后重试或打开“转写设置”。");
      return;
    }
    if (processingMode === "direct" && !directAvailable) {
      onError(
        directBlockedReason ||
          "当前模型不支持直接理解音频，请在音频设置中选择“先转成文字”。",
      );
      return;
    }
    onError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      chunksRef.current = [];
      bytesRef.current = 0;
      setRecordingSeconds(0);
      const recorder = new MediaRecorder(stream, { mimeType: format.mime });
      recorderRef.current = recorder;
      recorder.ondataavailable = (event: BlobEvent) => {
        if (event.data.size <= 0) return;
        chunksRef.current.push(event.data);
        bytesRef.current += event.data.size;
        if (
          bytesRef.current >= MAX_AUDIO_BYTES &&
          recorder.state !== "inactive"
        ) {
          recorder.stop();
        }
      };
      recorder.onerror = () => {
        if (mountedRef.current) {
          onError("录音被浏览器中断，请重新录音或改用文件上传。");
        }
      };
      recorder.onstop = () => {
        clearTimer();
        stopTracks();
        recorderRef.current = null;
        if (!mountedRef.current) return;
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || format.mime,
        });
        if (blob.size <= 0) {
          setActivity("idle");
          onError("没有录到可用声音，请检查麦克风后重新录制。");
          return;
        }
        if (blob.size > MAX_AUDIO_BYTES) {
          setActivity("idle");
          onError("录音达到 25 MiB 上限，请缩短录音后重试。");
          return;
        }
        const recordedFile = new File(
          [blob],
          `recording-${Date.now()}.${format.extension}`,
          { type: format.mime },
        );
        void processRecording(recordedFile);
      };
      recorder.start(1_000);
      setActivity("recording");
      const startedAt = Date.now();
      timerRef.current = window.setInterval(() => {
        const elapsed = Math.min(
          MAX_RECORDING_SECONDS,
          Math.floor((Date.now() - startedAt) / 1_000),
        );
        setRecordingSeconds(elapsed);
        if (
          elapsed >= MAX_RECORDING_SECONDS &&
          recorder.state !== "inactive"
        ) {
          recorder.stop();
        }
      }, 250);
    } catch (recordingError) {
      clearTimer();
      stopTracks();
      setActivity("idle");
      onError(recorderErrorMessage(recordingError));
    }
  }, [
    activity,
    clearTimer,
    disabled,
    directAvailable,
    directBlockedReason,
    enabled,
    onError,
    processingMode,
    processRecording,
    selectedModelId,
    stopTracks,
  ]);

  function toggleRecording() {
    if (activity === "recording") {
      if (
        recorderRef.current &&
        recorderRef.current.state !== "inactive"
      ) {
        recorderRef.current.stop();
      }
      return;
    }
    void startQuickRecording();
  }

  const micDisabled =
    disabled ||
    !enabled ||
    (processingMode === "transcribe" && !selectedModelId) ||
    (processingMode === "direct" && !directAvailable) ||
    activity === "transcribing" ||
    activity === "sending";

  return (
    <div className="flex items-center gap-1.5">
      <button
        aria-label={
          activity === "recording"
            ? processingMode === "direct"
              ? "停止录音并发送"
              : "停止录音并转写"
            : activity === "transcribing"
              ? "正在转写语音"
              : activity === "sending"
                ? "正在发送录音"
                : processingMode === "direct"
                  ? "录音并由当前模型理解"
                  : "录音并转成文字"
        }
        className={`inline-flex h-9 w-9 items-center justify-center rounded-full border transition ${
          activity === "recording"
            ? "border-rose-300/60 bg-rose-300/15 text-rose-100 shadow-[0_0_0_4px_rgba(251,113,133,0.08)]"
            : "border-white/10 bg-white/[0.06] text-slate-200 hover:border-brand-300/45 hover:bg-brand-300/10 hover:text-brand-100"
        } disabled:cursor-not-allowed disabled:opacity-45`}
        disabled={micDisabled}
        onClick={toggleRecording}
        title={
          activity === "recording"
            ? processingMode === "direct"
              ? "再次点击，停止并由当前模型理解"
              : "再次点击，停止并开始转写"
            : processingMode === "direct"
              ? "录音完成后直接发送给当前模型"
              : "录音完成后自动转成文字，结果会填入输入框"
        }
        type="button"
      >
        {activity === "transcribing" || activity === "sending" ? (
          <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        ) : activity === "recording" ? (
          <Square aria-hidden="true" className="h-3.5 w-3.5 fill-current" />
        ) : (
          <Mic aria-hidden="true" className="h-4 w-4" />
        )}
      </button>
      {activity === "recording" ? (
        <span className="whitespace-nowrap text-xs font-semibold text-rose-100">
          {formatDuration(recordingSeconds)} · 再按停止
        </span>
      ) : null}
    </div>
  );
}

export default function ChatAudioComposer({
  currentModelId,
  initialSource,
  initialTranscriptionModelId,
  isAutoRoute,
  isSending,
  prompt,
  directBlockedReason,
  onClose,
  onFillTranscript,
  onSendTranscript,
  onSendDirectAudio,
}: ChatAudioComposerProps) {
  const [source, setSource] = useState<AudioSource>(initialSource);
  const [panel, setPanel] = useState<AudioComposerPanel>("upload");
  const [processingMode, setProcessingMode] =
    useState<AudioProcessingMode>(storedProcessingMode);
  const [catalog, setCatalog] = useState<AudioCatalogResponse | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [selectedSttModelId, setSelectedSttModelId] = useState("");
  const [language, setLanguage] = useState(
    () =>
      window.sessionStorage.getItem(STT_LANGUAGE_SESSION_KEY) ?? "auto",
  );
  const [file, setFile] = useState<File | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [activity, setActivity] = useState<AudioActivity>("idle");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [progress, setProgress] = useState(0);
  const [transcription, setTranscription] =
    useState<TranscriptionResponse | null>(null);
  const [transcriptDraft, setTranscriptDraft] = useState("");
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recordingChunksRef = useRef<Blob[]>([]);
  const recordingBytesRef = useRef(0);
  const recordingTimerRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const pendingAttachmentRef = useRef<PendingAttachment | null>(null);
  const mountedRef = useRef(true);
  const discardRecordingRef = useRef(false);

  const sttProfiles = useMemo(
    () =>
      (catalog?.profiles ?? []).filter(
        (profile) =>
          profile.invocable &&
          profile.interaction_status === "ready" &&
          profile.chat_modes.includes("transcribe"),
      ),
    [catalog],
  );
  const directProfile = useMemo(
    () =>
      (catalog?.profiles ?? []).find(
        (profile) =>
          profile.model_id === currentModelId &&
          profile.invocable &&
          profile.interaction_status === "ready" &&
          profile.chat_modes.includes("direct_audio_input"),
      ),
    [catalog, currentModelId],
  );
  const selectedFormat = file ? fileExtension(file) : "";
  const directFormatSupported =
    !selectedFormat ||
    Boolean(directProfile?.input_formats.includes(selectedFormat));
  const directAvailable = Boolean(
    !isAutoRoute &&
      directProfile &&
      directFormatSupported &&
      !directBlockedReason,
  );
  const busy =
    activity !== "idle" || isSending;

  const stopMediaTracks = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const clearRecordingTimer = useCallback(() => {
    if (recordingTimerRef.current !== null) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
  }, []);

  const discardPendingAttachment = useCallback(() => {
    const pending = pendingAttachmentRef.current;
    pendingAttachmentRef.current = null;
    if (!pending) return;
    void fetch(
      `/api/multimodal/chat/attachments/${encodeURIComponent(
        pending.attachmentId,
      )}`,
      { method: "DELETE" },
    ).catch(() => undefined);
  }, []);

  const resetResult = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setTranscription(null);
    setTranscriptDraft("");
    setProgress(0);
    setError("");
    setActivity("idle");
  }, []);

  const chooseFile = useCallback(
    (nextFile: File | null) => {
      if (busy) return;
      if (nextFile) {
        const validationError = validateAudioFile(nextFile);
        if (validationError) {
          setError(validationError);
          return;
        }
      }
      discardPendingAttachment();
      resetResult();
      setFile(nextFile);
      if (
        nextFile &&
        processingMode === "direct" &&
        !directProfile?.input_formats.includes(fileExtension(nextFile))
      ) {
        setError(
          `当前模型不能直接理解 ${fileExtension(nextFile).toUpperCase()} 音频，请在转写设置中选择“先转成文字”。`,
        );
      }
    },
    [
      busy,
      directProfile,
      discardPendingAttachment,
      processingMode,
      resetResult,
    ],
  );

  useEffect(() => {
    setSource(initialSource);
  }, [initialSource]);

  useEffect(() => {
    const controller = new AbortController();
    setCatalogError("");
    fetch("/api/multimodal/audio/models", { signal: controller.signal })
      .then(async (response) => {
        const payload = await readApiPayload(response);
        if (!response.ok) {
          throw new Error(apiErrorMessage(payload, response.status));
        }
        return payload as AudioCatalogResponse;
      })
      .then((nextCatalog) => {
        if (!mountedRef.current) return;
        setCatalog(nextCatalog);
      })
      .catch((loadError) => {
        if (
          loadError instanceof DOMException &&
          loadError.name === "AbortError"
        ) {
          return;
        }
        if (!mountedRef.current) return;
        setCatalogError(
          loadError instanceof Error
            ? loadError.message
            : "暂时无法加载语音模型，请稍后重试。",
        );
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (sttProfiles.length === 0) return;
    const stored = window.sessionStorage.getItem(STT_MODEL_SESSION_KEY);
    const candidates = [
      initialTranscriptionModelId,
      stored,
      DEFAULT_STT_MODEL_ID,
      "microsoft/mai-transcribe-1.5",
      "openai/gpt-4o-mini-transcribe",
      sttProfiles[0]?.model_id,
    ].filter((value): value is string => Boolean(value));
    const nextId =
      candidates.find((candidate) =>
        sttProfiles.some((profile) => profile.model_id === candidate),
      ) ?? sttProfiles[0].model_id;
    setSelectedSttModelId((current) =>
      sttProfiles.some((profile) => profile.model_id === current)
        ? current
        : nextId,
    );
  }, [initialTranscriptionModelId, sttProfiles]);

  useEffect(() => {
    publishAudioSettings({
      language,
      processingMode,
      sttModelId: selectedSttModelId,
    });
  }, [language, processingMode, selectedSttModelId]);

  useEffect(() => {
    if (
      catalog &&
      processingMode === "direct" &&
      (isAutoRoute || !directProfile || directBlockedReason)
    ) {
      setProcessingMode("transcribe");
    }
  }, [
    catalog,
    directBlockedReason,
    directProfile,
    isAutoRoute,
    processingMode,
  ]);

  useEffect(() => {
    if (!file) {
      setAudioUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(file);
    setAudioUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [file]);

  useEffect(
    () => {
      mountedRef.current = true;
      return () => {
        mountedRef.current = false;
        discardRecordingRef.current = true;
        abortRef.current?.abort();
        clearRecordingTimer();
        if (
          recorderRef.current &&
          recorderRef.current.state !== "inactive"
        ) {
          recorderRef.current.stop();
        }
        stopMediaTracks();
        discardPendingAttachment();
      };
    },
    [clearRecordingTimer, discardPendingAttachment, stopMediaTracks],
  );

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    chooseFile(event.target.files?.[0] ?? null);
    event.target.value = "";
  }

  async function startRecording() {
    if (busy) return;
    const format = selectedRecorderFormat();
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined" ||
      !format
    ) {
      setError("当前浏览器不支持可用的录音格式，请改用音频文件上传。");
      return;
    }
    setError("");
    setTranscription(null);
    setTranscriptDraft("");
    discardPendingAttachment();
    discardRecordingRef.current = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      recordingChunksRef.current = [];
      recordingBytesRef.current = 0;
      setRecordingSeconds(0);
      const recorder = new MediaRecorder(stream, {
        mimeType: format.mime,
      });
      recorderRef.current = recorder;
      recorder.ondataavailable = (event: BlobEvent) => {
        if (event.data.size <= 0) return;
        recordingChunksRef.current.push(event.data);
        recordingBytesRef.current += event.data.size;
        if (
          recordingBytesRef.current >= MAX_AUDIO_BYTES &&
          recorder.state !== "inactive"
        ) {
          recorder.stop();
        }
      };
      recorder.onerror = () => {
        if (mountedRef.current) {
          setError("录音被浏览器中断，请重新录制或改用文件上传。");
        }
      };
      recorder.onstop = () => {
        clearRecordingTimer();
        stopMediaTracks();
        recorderRef.current = null;
        if (!mountedRef.current || discardRecordingRef.current) return;
        const blob = new Blob(recordingChunksRef.current, {
          type: recorder.mimeType || format.mime,
        });
        if (blob.size <= 0) {
          setError("没有录到可用声音，请检查麦克风后重新录制。");
          setActivity("idle");
          return;
        }
        if (blob.size > MAX_AUDIO_BYTES) {
          setError("录音达到 25 MiB 上限，请缩短录音后重试。");
          setActivity("idle");
          return;
        }
        const recordedFile = new File(
          [blob],
          `recording-${Date.now()}.${format.extension}`,
          { type: format.mime },
        );
        setFile(recordedFile);
        setActivity("idle");
        if (
          processingMode === "direct" &&
          !directProfile?.input_formats.includes(format.extension)
        ) {
          setError(
            `当前模型不能直接理解 ${format.extension.toUpperCase()} 录音，请在转写设置中选择“先转成文字”。`,
          );
        }
      };
      recorder.start(1_000);
      setActivity("recording");
      const startedAt = Date.now();
      recordingTimerRef.current = window.setInterval(() => {
        const elapsed = Math.min(
          MAX_RECORDING_SECONDS,
          Math.floor((Date.now() - startedAt) / 1_000),
        );
        setRecordingSeconds(elapsed);
        if (
          elapsed >= MAX_RECORDING_SECONDS &&
          recorder.state !== "inactive"
        ) {
          recorder.stop();
        }
      }, 250);
    } catch (recordingError) {
      stopMediaTracks();
      clearRecordingTimer();
      setActivity("idle");
      setError(recorderErrorMessage(recordingError));
    }
  }

  function stopRecording() {
    if (
      recorderRef.current &&
      recorderRef.current.state !== "inactive"
    ) {
      recorderRef.current.stop();
    }
  }

  function discardRecording() {
    discardRecordingRef.current = true;
    stopRecording();
    clearRecordingTimer();
    stopMediaTracks();
    setActivity("idle");
    setRecordingSeconds(0);
  }

  async function transcribe() {
    if (!file || !selectedSttModelId || busy) return;
    const validationError = validateAudioFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setError("");
    setProgress(2);
    setActivity("uploading");
    try {
      const result = await requestTranscription({
        file,
        language,
        modelId: selectedSttModelId,
        signal: controller.signal,
        onProgress: ({ phase, percent }) => {
          if (!mountedRef.current) return;
          setActivity(
            phase === "uploading" ? "uploading" : "transcribing",
          );
          setProgress(percent);
        },
      });
      if (!mountedRef.current) return;
      setTranscription(result);
      setTranscriptDraft(result.text);
      setProgress(100);
      setActivity("idle");
    } catch (transcriptionError) {
      if (!mountedRef.current) return;
      setProgress(0);
      setActivity("idle");
      if (
        transcriptionError instanceof DOMException &&
        transcriptionError.name === "AbortError"
      ) {
        setError("转写已取消，音频仍保留在本页。");
      } else {
        setError(
          transcriptionError instanceof Error
            ? transcriptionError.message
            : "转写没有完成，请稍后重试。",
        );
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }

  async function uploadDirectAttachment() {
    if (!file) return "";
    const pending = pendingAttachmentRef.current;
    if (pending?.file === file) return pending.attachmentId;
    discardPendingAttachment();
    const body = new FormData();
    body.append("kind", "audio");
    body.append("file", file, file.name);
    const response = await fetch("/api/multimodal/chat/attachments", {
      method: "POST",
      body,
    });
    const payload = await readApiPayload(response);
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, response.status));
    }
    const attachment = payload as AttachmentResponse;
    pendingAttachmentRef.current = {
      attachmentId: attachment.attachment_id,
      file,
    };
    return attachment.attachment_id;
  }

  async function sendDirectAudio() {
    if (!file || busy || !directAvailable) return;
    setError("");
    setActivity("sending");
    try {
      const attachmentId = await uploadDirectAttachment();
      const succeeded = await onSendDirectAudio(attachmentId, file.name);
      if (succeeded) {
        pendingAttachmentRef.current = null;
        setFile(null);
        onClose();
      } else {
        setError("发送未完成，音频仍保留，可以直接重试。");
      }
    } catch (sendError) {
      setError(
        sendError instanceof Error
          ? sendError.message
          : "音频发送没有完成，请稍后重试。",
      );
    } finally {
      if (mountedRef.current) setActivity("idle");
    }
  }

  async function sendTranscript() {
    const cleanTranscript = transcriptDraft.trim();
    if (!cleanTranscript || busy) return;
    setError("");
    setActivity("sending");
    const succeeded = await onSendTranscript(cleanTranscript);
    if (succeeded) {
      setFile(null);
      setTranscription(null);
      setTranscriptDraft("");
      onClose();
    } else {
      setError("消息没有发送成功，转写文字仍保留，可以修改后重试。");
    }
    if (mountedRef.current) setActivity("idle");
  }

  function fillTranscript() {
    const cleanTranscript = transcriptDraft.trim();
    if (!cleanTranscript || busy) return;
    onFillTranscript(cleanTranscript);
    setFile(null);
    setTranscription(null);
    setTranscriptDraft("");
    onClose();
  }

  const selectedSttProfile = sttProfiles.find(
    (profile) => profile.model_id === selectedSttModelId,
  );
  const activityLabel =
    activity === "uploading"
      ? `正在上传 ${progress}%`
      : activity === "transcribing"
        ? "正在识别语音"
        : activity === "sending"
          ? "正在发送"
          : "";

  return (
    <section
      aria-label="语音输入"
      className="mb-3 overflow-hidden rounded-lg border border-brand-300/20 bg-ink-950/70"
    >
      <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
            <FileAudio
              aria-hidden="true"
              className="h-4 w-4 text-brand-200"
            />
            音频
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            上传文件和麦克风共用下方转写设置。
          </p>
        </div>
        <div className="flex items-center justify-between gap-2">
          <div
            aria-label="音频面板"
            className="inline-flex rounded-full border border-white/10 bg-white/[0.035] p-1"
          >
            <button
              aria-pressed={panel === "upload"}
              className={`whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                panel === "upload"
                  ? "bg-brand-300 text-ink-950"
                  : "text-slate-300 hover:text-white"
              }`}
              disabled={busy}
              onClick={() => setPanel("upload")}
              type="button"
            >
              上传音频
            </button>
            <button
              aria-pressed={panel === "settings"}
              className={`whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                panel === "settings"
                  ? "bg-brand-300 text-ink-950"
                  : "text-slate-300 hover:text-white"
              }`}
              disabled={busy}
              onClick={() => setPanel("settings")}
              type="button"
            >
              转写设置
            </button>
          </div>
          <button
            aria-label="关闭语音输入"
            className="rounded-full p-1.5 text-slate-400 transition hover:bg-white/10 hover:text-white disabled:opacity-40"
            disabled={busy}
            onClick={onClose}
            type="button"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="space-y-4 p-4">
        {panel === "settings" ? (
          <div className="space-y-4">
            <div className="grid gap-3">
              <label className="text-xs font-semibold text-slate-300">
                转写模型
                <select
                  className="mt-1.5 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none focus:border-brand-300/55"
                  disabled={busy || sttProfiles.length === 0}
                  onChange={(event) =>
                    setSelectedSttModelId(event.target.value)
                  }
                  value={selectedSttModelId}
                >
                  {sttProfiles.length === 0 ? (
                    <option value="">暂无可用模型</option>
                  ) : null}
                  {sttProfiles.map((profile) => (
                    <option key={profile.model_id} value={profile.model_id}>
                      {profile.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs font-semibold text-slate-300">
                语言
                <select
                  className="mt-1.5 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none focus:border-brand-300/55"
                  disabled={busy}
                  onChange={(event) => setLanguage(event.target.value)}
                  value={language}
                >
                  {LANGUAGE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.value === "auto"
                        ? "自动识别（中文优先简体）"
                        : option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="block text-xs font-semibold text-slate-300">
              处理方式
              <select
                className="mt-1.5 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none focus:border-brand-300/55"
                disabled={busy}
                onChange={(event) =>
                  setProcessingMode(
                    event.target.value as AudioProcessingMode,
                  )
                }
                value={processingMode}
              >
                <option value="transcribe">先转成文字（推荐）</option>
                <option
                  disabled={
                    isAutoRoute ||
                    !directProfile ||
                    Boolean(directBlockedReason)
                  }
                  value="direct"
                >
                  由当前模型直接理解
                </option>
              </select>
              <span className="mt-1.5 block text-[11px] font-normal leading-5 text-slate-500">
                {processingMode === "direct"
                  ? "原始音频只参与本轮，不保存到对话历史。"
                  : "转写后可检查、修改，并组合知识库或智能调度。"}
              </span>
            </label>
            <p className="text-[11px] leading-5 text-slate-500">
              设置保存在本次浏览器会话，上传音频与麦克风都会按此执行。
            </p>
          </div>
        ) : (
          <>
        {!file && source === "upload" ? (
          <div className="rounded-lg border border-dashed border-white/20 bg-white/[0.035] px-4 py-5 text-center">
            <input
              accept={AUDIO_ACCEPT}
              className="hidden"
              onChange={handleFileInput}
              ref={fileInputRef}
              type="file"
            />
            <FileAudio
              aria-hidden="true"
              className="mx-auto h-6 w-6 text-slate-400"
            />
            <p className="mt-2 text-sm font-semibold text-white">
              选择一段音频
            </p>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              WAV、MP3、FLAC、M4A、OGG、WebM 或 AAC，最大 25 MiB。
            </p>
            <button
              className="mt-3 rounded-full bg-brand-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-brand-200"
              onClick={() => fileInputRef.current?.click()}
              type="button"
            >
              选择音频
            </button>
          </div>
        ) : null}

        {!file && source === "record" ? (
          <div className="rounded-lg bg-white/[0.04] p-4">
            {activity === "recording" ? (
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div aria-live="polite">
                  <p className="flex items-center gap-2 text-sm font-semibold text-rose-100">
                    <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-rose-400" />
                    正在录音 {formatDuration(recordingSeconds)}
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    最长 5 分钟，达到时长或 25 MiB 会自动停止。
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    className="inline-flex items-center gap-2 rounded-full bg-rose-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-rose-200"
                    onClick={stopRecording}
                    type="button"
                  >
                    <Square aria-hidden="true" className="h-3.5 w-3.5" />
                    停止录音
                  </button>
                  <button
                    className="rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/10"
                    onClick={discardRecording}
                    type="button"
                  >
                    放弃
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm font-semibold text-white">
                    点击后浏览器才会申请麦克风权限
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    录制完成后可试听、重录或删除，不会边录边上传。
                  </p>
                </div>
                <button
                  className="inline-flex shrink-0 items-center justify-center gap-2 rounded-full bg-brand-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-brand-200"
                  onClick={() => void startRecording()}
                  type="button"
                >
                  <Mic aria-hidden="true" className="h-4 w-4" />
                  开始录音
                </button>
              </div>
            )}
          </div>
        ) : null}

        {file ? (
          <div className="rounded-lg bg-white/[0.045] p-3">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-white">
                  {file.name}
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  {fileExtension(file).toUpperCase()} ·{" "}
                  {formatBytes(file.size)}
                </p>
              </div>
              {audioUrl ? (
                <audio
                  aria-label={`试听 ${file.name}`}
                  className="w-full sm:max-w-[280px]"
                  controls
                  preload="metadata"
                  src={audioUrl}
                />
              ) : null}
              <div className="flex gap-2">
                {source === "record" ? (
                  <button
                    className="inline-flex items-center gap-1.5 rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/10"
                    disabled={busy}
                    onClick={() => {
                      chooseFile(null);
                      void startRecording();
                    }}
                    type="button"
                  >
                    <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
                    重录
                  </button>
                ) : null}
                <button
                  aria-label="删除音频"
                  className="inline-flex items-center gap-1.5 rounded-full border border-rose-300/25 px-3 py-2 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/10"
                  disabled={busy}
                  onClick={() => chooseFile(null)}
                  type="button"
                >
                  <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                  删除
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {activityLabel ? (
          <div aria-live="polite" className="rounded-lg bg-brand-300/[0.08] p-3">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="inline-flex items-center gap-2 font-semibold text-brand-100">
                <Loader2
                  aria-hidden="true"
                  className="h-4 w-4 animate-spin"
                />
                {activityLabel}
              </span>
              {activity === "uploading" ||
              activity === "transcribing" ? (
                <button
                  className="font-semibold text-slate-300 underline decoration-white/25 underline-offset-4 hover:text-white"
                  onClick={() => abortRef.current?.abort()}
                  type="button"
                >
                  取消转写
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        {catalogError || error ? (
          <p
            className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100"
            role="alert"
          >
            {error || catalogError}
          </p>
        ) : null}

        {transcription ? (
          <div className="space-y-3 border-t border-white/10 pt-4">
            <label className="block text-xs font-semibold text-slate-300">
              检查并修改转写文字
              <textarea
                className="mt-2 min-h-32 w-full resize-y rounded-lg border border-white/15 bg-ink-950/80 px-3 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/55 focus:ring-4 focus:ring-brand-300/10"
                disabled={busy}
                onChange={(event) => setTranscriptDraft(event.target.value)}
                value={transcriptDraft}
              />
            </label>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs leading-5 text-slate-400">
                {selectedSttProfile?.display_name ??
                  transcription.actual_model}
                {" · "}
                请求 {transcription.request_id}
              </p>
              <div className="flex flex-wrap gap-2">
                <button
                  className="rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/10 disabled:opacity-45"
                  disabled={!transcriptDraft.trim() || busy}
                  onClick={fillTranscript}
                  type="button"
                >
                  填入问题
                </button>
                <button
                  className="inline-flex items-center gap-2 rounded-full bg-brand-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-brand-200 disabled:opacity-45"
                  disabled={!transcriptDraft.trim() || busy}
                  onClick={() => void sendTranscript()}
                  type="button"
                >
                  <Send aria-hidden="true" className="h-3.5 w-3.5" />
                  确认并发送
                </button>
              </div>
            </div>
          </div>
        ) : file ? (
          <div className="flex flex-col gap-2 border-t border-white/10 pt-4 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs leading-5 text-slate-400">
              {processingMode === "transcribe"
                ? "转写结果不会自动发送，你可以先检查和修改。"
                : prompt.trim()
                  ? "将使用输入框中的问题分析这段音频。"
                  : "未填写问题时，将默认请模型概括音频。"}
            </p>
            {processingMode === "transcribe" ? (
              <button
                className="shrink-0 rounded-full bg-brand-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-brand-200 disabled:cursor-not-allowed disabled:opacity-45"
                disabled={!selectedSttModelId || busy}
                onClick={() => void transcribe()}
                type="button"
              >
                开始转写
              </button>
            ) : (
              <button
                className="shrink-0 rounded-full bg-brand-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-brand-200 disabled:cursor-not-allowed disabled:opacity-45"
                disabled={!directAvailable || busy}
                onClick={() => void sendDirectAudio()}
                type="button"
              >
                发送音频问题
              </button>
            )}
          </div>
        ) : null}
          </>
        )}
      </div>
    </section>
  );
}
