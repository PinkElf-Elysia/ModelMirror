import { memo } from "react";
import { Link } from "react-router-dom";
import { useModelPreference } from "../context/ModelPreferenceContext";
import type { FileSurfaceSummary } from "../data/fileCapabilities";
import { type Model, type ModelOperation } from "../data/models";
import {
  getRecruitmentTag,
  getTalentStats,
} from "../theme/recruitmentTheme";
import {
  buildFriendlyTalentIntro,
  deriveProviderFromModel,
  getFriendlyJobCapabilityLabel,
} from "../utils/userFriendlyText";

interface ModelCardProps {
  model: Model;
  catalogInvocable?: boolean;
  confirmedAudioOperations?: ModelOperation[];
  adaptedAudioOperations?: ModelOperation[];
  confirmedImageOperations?: ModelOperation[];
  confirmedVideoOperations?: ModelOperation[];
  verificationVideoOperations?: ModelOperation[];
  audioCapabilityStatus?: AudioCapabilityStatus;
  audioCatalogStale?: boolean;
  imageCatalogStale?: boolean;
  videoCatalogStale?: boolean;
  fileSurfaceSummary?: FileSurfaceSummary;
}

export interface AudioCapabilityStatus {
  status: "ready" | "planned" | "disabled";
  operations: ModelOperation[];
  adaptedOperations: ModelOperation[];
  availabilityStatus:
    | "available"
    | "needs_configuration"
    | "verification_required"
    | "upstream_unavailable"
    | "disabled"
    | null;
  reason: string | null;
  pricePerGenerationUsd: number | null;
  fixedDurationSeconds: number | null;
}

export function deriveDocumentInputPresentation(
  fileSurfaceSummary: FileSurfaceSummary | undefined,
  canChat: boolean,
  canUseInRag: boolean,
) {
  if (!fileSurfaceSummary?.registryAvailable) {
    return {
      label: "文件输入 · 入口状态待确认",
      reason: "暂时无法读取文件能力清单，本卡不会开放未经确认的文件入口。",
    };
  }

  if (canUseInRag && fileSurfaceSummary.ragFormats.length > 0) {
    return {
      label: "文件处理 · 资料库可用",
      reason: "文件会先由资料库按已登记格式处理，再进入向量检索或重排。",
    };
  }

  if (
    canChat &&
    fileSurfaceSummary.chatDocumentDeclared &&
    fileSurfaceSummary.chatDocumentFormats.length > 0
  ) {
    return {
      label: "文件输入 · Chat 可用（提取后发送）",
      reason:
        "可在聊天中上传已登记的文档格式；发送前会提取内容并由你预览确认。",
    };
  }

  return {
    label: "文件输入 · 当前入口未开放",
    reason:
      "模型目录声明了文件能力，但当前连接或文件入口尚未满足调用条件。",
  };
}

const tagStyles: Record<string, string> = {
  历史: "border-amber-300/30 bg-amber-300/10 text-amber-100",
  精选: "border-brand-300/30 bg-brand-300/10 text-brand-100",
  新: "border-accent-300/30 bg-accent-300/10 text-accent-100",
  热门: "border-emerald-300/30 bg-emerald-300/10 text-emerald-100",
  多模态: "border-fuchsia-300/30 bg-fuchsia-300/10 text-fuchsia-100",
  开源: "border-amber-300/30 bg-amber-300/10 text-amber-100",
  免费: "border-lime-300/30 bg-lime-300/10 text-lime-100",
  动态计费: "border-sky-300/30 bg-sky-300/10 text-sky-100",
};

function formatCnyPrice(priceCnyPerMillion: number) {
  return `¥${priceCnyPerMillion.toFixed(2)}`;
}

const domesticProviderKeywords = [
  "DeepSeek",
  "Qwen",
  "Alibaba",
  "Moonshot",
  "Zhipu",
  "GLM",
  "Baichuan",
  "MiniMax",
  "StepFun",
  "Tencent",
  "Yi",
  "01.AI",
];

const restrictedProviderKeywords = [
  "OpenAI",
  "Anthropic",
  "xAI",
  "Google",
  "Meta",
  "Mistral",
];

function includesProviderKeyword(identity: string, keywords: string[]) {
  const normalizedIdentity = identity.toLowerCase();

  return keywords.some((keyword) =>
    normalizedIdentity.includes(keyword.toLowerCase()),
  );
}

function modelIdentity(model: Model, providerName: string) {
  return `${model.id} ${model.provider} ${providerName} ${model.model_author}`;
}

function formatContextLength(contextLength: number) {
  if (contextLength >= 1_000_000) {
    return `${(contextLength / 1_000_000).toFixed(0)}M`;
  }

  return `${Math.round(contextLength / 1000)}K`;
}

const operationLabels: Record<ModelOperation, string> = {
  chat: "对话面试",
  analyze_document: "文档理解",
  analyze_image: "图片识别",
  generate_image: "图片生成/编辑",
  transcribe: "音频转文字",
  synthesize_speech: "文字转语音",
  generate_audio: "音频生成",
  analyze_audio: "音频理解",
  realtime_voice: "实时语音",
  analyze_video: "视频理解",
  generate_video: "视频生成",
  generate_world: "3D 世界生成",
  embed: "向量检索",
  rerank: "检索重排",
};

const ModelCard = memo(function ModelCard({
  model,
  catalogInvocable = false,
  confirmedAudioOperations = [],
  adaptedAudioOperations = [],
  confirmedImageOperations = [],
  confirmedVideoOperations = [],
  verificationVideoOperations = [],
  audioCapabilityStatus,
  audioCatalogStale = false,
  imageCatalogStale = false,
  videoCatalogStale = false,
  fileSurfaceSummary,
}: ModelCardProps) {
  const { preferredModelId, setPreferredModelId } = useModelPreference();
  const isFree = model.pricing_status === "free";
  const isDynamicPricing = model.pricing_status === "dynamic";
  const audioGenerationPriceUsd =
    model.primary_operation === "generate_audio"
      ? (audioCapabilityStatus?.pricePerGenerationUsd ?? null)
      : null;
  const hasAudioGenerationPrice = audioGenerationPriceUsd !== null;
  const talentStats = getTalentStats(model);
  const providerName = deriveProviderFromModel(model);
  const personaDescription = buildFriendlyTalentIntro(model);
  const identity = modelIdentity(model, providerName);
  const domesticFriendly = includesProviderKeyword(
    identity,
    domesticProviderKeywords,
  );
  const regionSensitive =
    !domesticFriendly &&
    includesProviderKeyword(identity, restrictedProviderKeywords);
  const isHistorical = model.catalog_status === "historical";
  const generalInvocationAllowed = model.active || catalogInvocable;
  const canChat =
    generalInvocationAllowed &&
    model.interaction_status === "ready" &&
    model.ui_entrypoint === "chat";
  const canUseInRag =
    generalInvocationAllowed &&
    model.interaction_status === "ready" &&
    model.ui_entrypoint === "rag";
  const declaresDocumentInput =
    model.operations.includes("analyze_document");
  const documentInputPresentation = deriveDocumentInputPresentation(
    fileSurfaceSummary,
    canChat,
    canUseInRag,
  );
  const documentInputStatusLabel = documentInputPresentation.label;
  const documentInputStatusReason = documentInputPresentation.reason;
  const canAnalyzeAudio =
    confirmedAudioOperations.includes("analyze_audio");
  const canSynthesizeSpeech =
    confirmedAudioOperations.includes("synthesize_speech");
  const canGenerateAudio =
    confirmedAudioOperations.includes("generate_audio");
  const isRealtimeVoiceModel =
    model.operations.includes("realtime_voice");
  const canOpenRealtimeVoice =
    model.active && isRealtimeVoiceModel;
  const realtimeVoiceReady =
    confirmedAudioOperations.includes("realtime_voice");
  const canTranscribe =
    confirmedAudioOperations.includes("transcribe");
  const canAnalyzeImage =
    confirmedImageOperations.includes("analyze_image");
  const canGenerateImage =
    confirmedImageOperations.includes("generate_image");
  const operationLabel = operationLabels[model.primary_operation];
  const canAnalyzeVideo =
    confirmedVideoOperations.includes("analyze_video");
  const canGenerateVideo =
    confirmedVideoOperations.includes("generate_video");
  const canManuallyVerifyVideo =
    verificationVideoOperations.includes("generate_video");
  const canGenerateWorld =
    model.active &&
    model.interaction_status === "ready" &&
    model.worldModel === true &&
    model.operations.includes("generate_world");
  const confirmedVideoLabels = confirmedVideoOperations
    .filter(
      (operation) =>
        operation === "analyze_video" ||
        operation === "generate_video",
    )
    .map((operation) => operationLabels[operation]);
  const confirmedAudioLabels = confirmedAudioOperations
    .filter(
      (operation) =>
        operation === "analyze_audio" ||
        operation === "transcribe" ||
        operation === "synthesize_speech" ||
        operation === "generate_audio" ||
        operation === "realtime_voice",
    )
    .map((operation) => operationLabels[operation]);
  const adaptedAudioLabels = adaptedAudioOperations
    .filter(
      (operation) =>
        operation === "analyze_audio" ||
        operation === "transcribe" ||
        operation === "synthesize_speech" ||
        operation === "generate_audio" ||
        operation === "realtime_voice",
    )
    .map((operation) => operationLabels[operation]);
  const isTranscriptionModel =
    model.primary_operation === "transcribe" &&
    model.operations.includes("transcribe");
  const staticAudioOperations = model.operations.filter(
    (operation) =>
      operation === "analyze_audio" ||
      operation === "transcribe" ||
      operation === "synthesize_speech" ||
      operation === "generate_audio" ||
      operation === "realtime_voice",
  );
  const unresolvedAudioOperations = (
    audioCapabilityStatus?.operations ?? staticAudioOperations
  ).filter(
    (operation) =>
      !adaptedAudioOperations.includes(operation),
  );
  const pendingAudioOperations =
    audioCapabilityStatus?.status === "ready"
      ? []
      : unresolvedAudioOperations;
  const pendingAudioLabels = pendingAudioOperations.map(
    (operation) => operationLabels[operation],
  );
  const pendingAudioStateLabel =
    audioCapabilityStatus?.availabilityStatus === "needs_configuration"
      ? "需要配置"
      : audioCapabilityStatus?.availabilityStatus === "upstream_unavailable"
        ? "上游暂不可用"
      : audioCapabilityStatus?.status === "disabled"
      ? "当前未启用"
      : "待适配";
  const pendingAudioReason =
    audioCapabilityStatus?.reason ??
    (
      pendingAudioLabels.length > 0
        ? isRealtimeVoiceModel
          ? "请在设置中添加“OpenAI 音频与实时语音”连接并完成测试。"
          : "静态目录已收录，实时能力尚未确认。"
        : null
    );
  const isPrimaryAudioModel =
    (
      isTranscriptionModel ||
      model.primary_operation === "synthesize_speech" ||
      model.primary_operation === "generate_audio" ||
      model.primary_operation === "realtime_voice"
    );
  const primaryAudioOperationBlocked =
    isPrimaryAudioModel &&
    Boolean(audioCapabilityStatus) &&
    !adaptedAudioOperations.includes(model.primary_operation);
  const isInteractionReady =
    (
      model.interaction_status === "ready" &&
      !primaryAudioOperationBlocked
    ) ||
    adaptedAudioLabels.length > 0 ||
    canAnalyzeImage ||
    canGenerateImage ||
    canAnalyzeVideo ||
    canGenerateVideo;
  const adaptedAudioUnavailable =
    isPrimaryAudioModel &&
    adaptedAudioLabels.length > 0 &&
    !confirmedAudioOperations.includes(model.primary_operation);
  const audioUnavailableLabel =
    audioCapabilityStatus?.availabilityStatus === "needs_configuration"
      ? "已适配 · 需配置"
      : audioCapabilityStatus?.availabilityStatus === "upstream_unavailable"
        ? "上游暂不可用"
        : "已适配 · 当前未启用";
  const showGeneralChatAction =
    canChat &&
    model.primary_operation !== "synthesize_speech" &&
    (!isTranscriptionModel || canTranscribe);
  const primaryChatPath = isTranscriptionModel
    ? `/chat/${encodeURIComponent(
        preferredModelId,
      )}?media=audio&sttModel=${encodeURIComponent(model.id)}`
    : `/chat/${encodeURIComponent(model.id)}`;

  return (
    <article className="group relative isolate flex h-full min-h-[340px] flex-col overflow-hidden rounded-lg border border-hire-300/20 bg-ink-950/76 p-0 shadow-prism backdrop-blur-xl transition duration-300 ease-out hover:-translate-y-1 hover:border-hire-300/55 hover:bg-surface-900/90 hover:shadow-[0_0_0_1px_rgba(251,146,60,0.32),0_20px_46px_rgba(124,45,18,0.22)]">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-28 bg-[linear-gradient(110deg,rgba(251,146,60,0.20),rgba(253,186,116,0.12),transparent)] opacity-80 transition duration-300 group-hover:opacity-100" />
      <div className="pointer-events-none absolute right-0 top-0 h-full w-1/3 bg-[linear-gradient(180deg,rgba(251,146,60,0.14),transparent_48%,rgba(124,58,237,0.10))] opacity-70" />

      <div className="relative border-b border-hire-300/20 bg-[linear-gradient(90deg,rgba(251,146,60,0.24),rgba(253,186,116,0.10),rgba(36,217,255,0.08))] px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <span className="rounded-full border border-hire-200/30 bg-hire-400/15 px-3 py-1 text-xs font-semibold text-hire-100">
            {isHistorical && !catalogInvocable
              ? "可能不可用"
              : canManuallyVerifyVideo
                ? "等待人工验收"
              : adaptedAudioUnavailable
              ? audioUnavailableLabel
              : !isInteractionReady
              ? isRealtimeVoiceModel
                ? "需要配置"
                : "交互待适配"
              : talentStats.urgent
                ? "急聘"
                : "可预约面试"}
          </span>
          <span className="text-xs font-medium text-hire-100">
            人气值 {talentStats.popularity}
          </span>
        </div>
      </div>

      <div className="relative flex items-start justify-between gap-4 p-5 pb-0">
        <div className="min-w-0">
          <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-hire-300/35 bg-hire-300/10 px-2.5 py-1 text-xs font-semibold text-hire-100 shadow-[0_0_18px_rgba(251,146,60,0.08)]">
            <span className="h-1.5 w-1.5 rounded-full bg-hire-300" />
            我来自 {providerName}
          </span>
            <span className="rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 text-xs text-slate-300">
              毕业院校：{model.series || "通用系列"}
            </span>
          </div>
          <h2 className="line-clamp-2 text-lg font-semibold leading-6 text-white">
            {model.name}
          </h2>
          <p className="mt-1 text-xs text-slate-500">候选人编号：{model.id}</p>
          <p className="mt-3 line-clamp-4 text-sm leading-6 text-slate-300">
            {personaDescription}
          </p>
        </div>

        {canAnalyzeImage ||
        canGenerateImage ||
        canAnalyzeAudio ||
        canSynthesizeSpeech ||
        canGenerateAudio ||
        canOpenRealtimeVoice ||
        canAnalyzeVideo ||
        canGenerateVideo ||
        canManuallyVerifyVideo ||
        canGenerateWorld ||
        showGeneralChatAction ? (
          <div className="flex shrink-0 flex-col items-stretch gap-2">
            {canGenerateWorld ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}`}
              >
                生成 3D 世界
              </Link>
            ) : null}
            {canGenerateImage ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?operation=generate_image`}
              >
                生成图片
              </Link>
            ) : null}
            {canAnalyzeImage && !showGeneralChatAction ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}`}
              >
                识别图片
              </Link>
            ) : null}
            {canAnalyzeAudio ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?media=audio`}
              >
                理解音频
              </Link>
            ) : null}
            {canSynthesizeSpeech ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?operation=synthesize_speech`}
              >
                生成语音
              </Link>
            ) : null}
            {canGenerateAudio ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?operation=generate_audio`}
              >
                生成音乐
              </Link>
            ) : null}
            {canOpenRealtimeVoice ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?operation=realtime_voice`}
              >
                {realtimeVoiceReady ? "开始实时语音" : "配置实时语音"}
              </Link>
            ) : null}
            {canAnalyzeVideo ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?operation=analyze_video`}
              >
                分析视频
              </Link>
            ) : null}
            {canGenerateVideo ? (
              <Link
                className="rounded-full bg-hire-300 px-3.5 py-2 text-center text-sm font-semibold text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] transition duration-200 hover:bg-hire-200 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?operation=generate_video`}
              >
                生成视频
              </Link>
            ) : null}
            {canManuallyVerifyVideo ? (
              <Link
                className="rounded-full border border-amber-300/40 bg-amber-300/10 px-3.5 py-2 text-center text-sm font-semibold text-amber-100 transition duration-200 hover:bg-amber-300/20 active:scale-[0.98]"
                to={`/chat/${encodeURIComponent(model.id)}?operation=generate_video&verification=manual`}
              >
                人工核验
              </Link>
            ) : null}
            {showGeneralChatAction ? (
              <Link
                className={`rounded-full px-3.5 py-2 text-center text-sm font-semibold transition duration-200 active:scale-[0.98] ${
                  canGenerateImage ||
                  canAnalyzeAudio ||
                  canSynthesizeSpeech ||
                  canGenerateAudio ||
                  canOpenRealtimeVoice ||
                  canAnalyzeVideo ||
                  canGenerateVideo
                    ? "border border-hire-300/35 bg-ink-950/70 text-hire-100 hover:bg-hire-300/10"
                    : "bg-hire-300 text-ink-950 shadow-[0_0_0_1px_rgba(253,186,116,0.28),0_0_26px_rgba(251,146,60,0.18)] hover:bg-hire-200"
                }`}
                onClick={() => {
                  if (model.primary_operation === "chat") {
                    setPreferredModelId(model.id);
                  }
                }}
                title={
                  declaresDocumentInput
                    ? documentInputStatusReason
                    : undefined
                }
                to={primaryChatPath}
              >
                {isTranscriptionModel
                  ? "用于聊天转写"
                  : model.primary_operation === "synthesize_speech"
                    ? "生成语音"
                    : "立即面试"}
              </Link>
            ) : null}
            {isTranscriptionModel ? (
              <Link
                className="text-center text-xs font-semibold text-slate-400 underline decoration-white/20 underline-offset-4 transition hover:text-hire-100"
                to={`/chat/${encodeURIComponent(model.id)}?operation=transcribe`}
              >
                仅转录文件
              </Link>
            ) : null}
          </div>
        ) : canUseInRag ? (
          <Link
            className="shrink-0 rounded-full border border-hire-300/35 bg-hire-300/10 px-3.5 py-2 text-sm font-semibold text-hire-100 transition duration-200 hover:border-hire-300/60 hover:bg-hire-300/15 active:scale-[0.98]"
            to="/rag"
          >
            用于资料库
          </Link>
        ) : (
          <button
            className="shrink-0 cursor-not-allowed rounded-full border border-white/10 bg-white/[0.045] px-3.5 py-2 text-sm font-semibold text-slate-400"
            disabled
            title={
              isHistorical && !catalogInvocable
                ? "当前未出现在 OpenRouter 实时目录，其他兼容渠道仍可能支持调用。"
                : adaptedAudioUnavailable
                ? audioCapabilityStatus?.reason ?? audioUnavailableLabel
                : pendingAudioLabels.length > 0
                ? pendingAudioReason ?? `${pendingAudioLabels.join("、")}尚未适配`
                : confirmedVideoLabels.length > 0
                ? `${confirmedVideoLabels.join("、")}能力已确认`
                : `${operationLabel}入口尚未适配`
            }
            type="button"
          >
            {isHistorical && !catalogInvocable
              ? "可能不可用"
              : adaptedAudioUnavailable
              ? audioUnavailableLabel
              : pendingAudioLabels.length > 0
              ? `${pendingAudioLabels.join("、")}${pendingAudioStateLabel}`
              : confirmedVideoLabels.length > 0
              ? `${confirmedVideoLabels.join("、")}已确认`
              : "交互待适配"}
          </button>
        )}
      </div>

      <div className="relative mt-5 flex flex-wrap gap-2 px-5">
        <span
          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
            isInteractionReady
              ? "border-emerald-300/30 bg-emerald-300/10 text-emerald-100"
              : "border-slate-400/20 bg-slate-400/10 text-slate-300"
          }`}
        >
          {operationLabel}
          {adaptedAudioUnavailable
            ? audioCapabilityStatus?.availabilityStatus === "needs_configuration"
              ? " · 需配置"
              : " · 当前未启用"
            : !isInteractionReady
            ? canManuallyVerifyVideo
              ? " · 人工核验"
              : isRealtimeVoiceModel
              ? " · 需要连接"
              : " · 待适配"
            : ""}
        </span>
        {confirmedVideoLabels.map((label) => (
          <span
            className="rounded-full border border-sky-300/30 bg-sky-300/10 px-2.5 py-1 text-xs font-medium text-sky-100"
            key={label}
          >
            {label}已确认
            {videoCatalogStale ? " · 缓存目录" : ""}
          </span>
        ))}
        {confirmedImageOperations.map((operation) => (
          <span
            className="rounded-full border border-fuchsia-300/30 bg-fuchsia-300/10 px-2.5 py-1 text-xs font-medium text-fuchsia-100"
            key={`image-${operation}`}
          >
            {operationLabels[operation]}已确认
            {imageCatalogStale ? " · 缓存目录" : ""}
          </span>
        ))}
        {confirmedAudioLabels.map((label) => (
          <span
            className="rounded-full border border-cyan-300/30 bg-cyan-300/10 px-2.5 py-1 text-xs font-medium text-cyan-100"
            key={`audio-${label}`}
          >
            {label}已适配
            {audioCatalogStale ? " · 缓存目录" : ""}
          </span>
        ))}
        {declaresDocumentInput ? (
          <span
            className="rounded-full border border-violet-300/25 bg-violet-300/[0.08] px-2.5 py-1 text-xs font-medium text-violet-100"
            title={documentInputStatusReason}
          >
            {documentInputStatusLabel}
          </span>
        ) : null}
        {canManuallyVerifyVideo ? (
          <span className="rounded-full border border-amber-300/30 bg-amber-300/10 px-2.5 py-1 text-xs font-medium text-amber-100">
            视频生成 · 待人工验收
          </span>
        ) : null}
        {adaptedAudioLabels
          .filter((label) => !confirmedAudioLabels.includes(label))
          .map((label) => (
            <span
              className="rounded-full border border-cyan-300/20 bg-cyan-300/[0.06] px-2.5 py-1 text-xs font-medium text-cyan-100"
              key={`audio-adapted-${label}`}
            >
              {label}已适配
              {audioCapabilityStatus?.availabilityStatus ===
              "needs_configuration"
                ? " · 需配置"
                : " · 当前未启用"}
            </span>
          ))}
        {model.tags
          .filter(
            (tag) => !(hasAudioGenerationPrice && tag === "免费"),
          )
          .map((tag) => (
          <span
            className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
              tagStyles[tag] ?? "border-white/10 bg-white/[0.06] text-slate-300"
            }`}
            key={tag}
          >
            {getRecruitmentTag(tag)}
          </span>
        ))}
        {domesticFriendly ? (
          <span className="rounded-full border border-emerald-300/30 bg-emerald-300/10 px-2.5 py-1 text-xs font-medium text-emerald-100">
            国内可用优先
          </span>
        ) : regionSensitive ? (
          <span className="rounded-full border border-amber-300/30 bg-amber-300/10 px-2.5 py-1 text-xs font-medium text-amber-100">
            当前地区可能不可用
          </span>
        ) : null}
      </div>
      {isHistorical ? (
        <p className="relative mt-2 px-5 text-xs leading-5 text-amber-100">
          当前未出现在 OpenRouter 实时目录，其他兼容渠道仍可能支持调用。
        </p>
      ) : null}
      {pendingAudioLabels.length > 0 ? (
        <p className="relative mt-2 line-clamp-2 px-5 text-xs leading-5 text-amber-100">
          <span className="font-semibold">
            {pendingAudioLabels.join("、")}{pendingAudioStateLabel}：
          </span>
          {pendingAudioReason}
        </p>
      ) : null}

      <div className="relative mx-5 mt-5 rounded-lg border border-white/10 bg-white/[0.045] p-3">
        <div className="flex items-center justify-between gap-3 border-b border-white/10 pb-3">
          <p className="text-[11px] text-slate-400">
            {hasAudioGenerationPrice ? "单次生成费用" : "期望薪资"}
          </p>
          <p
            className={`text-right text-sm font-semibold ${
              hasAudioGenerationPrice
                ? "text-hire-100"
                : isFree
                ? "text-lime-100"
                : isDynamicPricing
                  ? "text-sky-100"
                  : "text-white"
            }`}
          >
            {hasAudioGenerationPrice
              ? `约 $${audioGenerationPriceUsd.toFixed(2)} / 次`
              : isFree
              ? "当前免费"
              : isDynamicPricing
                ? "按实际调用计费"
                : `${formatCnyPrice(model.price_cny.input)} / ${formatCnyPrice(model.price_cny.output)}`}
            {!hasAudioGenerationPrice && !isFree && !isDynamicPricing ? (
              <span className="ml-1 text-xs font-normal text-slate-400">
                输入/输出
              </span>
            ) : null}
          </p>
        </div>

        <div className="mt-3 grid grid-cols-3 gap-3">
          <div>
            <p className="text-[11px] text-slate-400">
              {hasAudioGenerationPrice ? "目录估算" : "输入薪资"}
            </p>
            <p className="mt-1 text-sm font-semibold text-slate-100">
              {hasAudioGenerationPrice
                ? `$${audioGenerationPriceUsd.toFixed(2)}`
                : isDynamicPricing
                ? "动态"
                : isFree
                  ? "免费"
                  : formatCnyPrice(model.price_cny.input)}
            </p>
          </div>
          <div>
            <p className="text-[11px] text-slate-400">
              {hasAudioGenerationPrice ? "作品时长" : "输出薪资"}
            </p>
            <p className="mt-1 text-sm font-semibold text-slate-100">
              {hasAudioGenerationPrice
                ? audioCapabilityStatus?.fixedDurationSeconds
                  ? `约 ${audioCapabilityStatus.fixedDurationSeconds} 秒`
                  : "模型决定"
                : isDynamicPricing
                ? "动态"
                : isFree
                  ? "免费"
                  : formatCnyPrice(model.price_cny.output)}
            </p>
          </div>
          <div>
            <p className="text-[11px] text-slate-400">
              {hasAudioGenerationPrice
                ? "输出格式"
                : isRealtimeVoiceModel
                  ? "会话上限"
                  : "工作经验"}
            </p>
            <p className="mt-1 text-sm font-semibold text-slate-100">
              {hasAudioGenerationPrice
                ? "MP3"
                : isRealtimeVoiceModel
                  ? "10 分钟"
                : formatContextLength(model.context_length)}
            </p>
          </div>
        </div>
      </div>

      <div className="relative mx-5 mt-auto flex items-center justify-between gap-3 border-t border-white/10 pb-5 pt-4">
        <div className="flex flex-wrap items-center gap-2">
          {model.job_capabilities.slice(0, 4).map((capability) => (
            <span
              className="inline-flex h-8 items-center justify-center rounded-md border border-white/10 bg-white/[0.06] px-2 text-xs font-semibold text-slate-300 transition group-hover:border-brand-300/30 group-hover:bg-brand-300/10 group-hover:text-brand-100"
              key={capability}
              title={`岗位能力：${getFriendlyJobCapabilityLabel(capability)}`}
            >
              {getFriendlyJobCapabilityLabel(capability)}
            </span>
          ))}
        </div>
        <p className="shrink-0 text-xs text-slate-500">
          已录用 {talentStats.hiredCount} 次
        </p>
      </div>

      {model.note ? (
        <p className="relative mx-5 mb-5 rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
          {model.note}
        </p>
      ) : null}
    </article>
  );
});

export default ModelCard;
