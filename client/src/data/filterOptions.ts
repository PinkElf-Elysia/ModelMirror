import {
  type InputModality,
  type JobCapability,
  type Provider,
  type SupportedParameter,
  models,
} from "./models";
import { getProviderDisplayName } from "../utils/userFriendlyText";

export interface Option<T extends string> {
  value: T;
  label: string;
  icon?: string;
}

export interface RangeValue {
  min: number;
  max: number;
}

export const CONTEXT_RANGE_LIMIT: RangeValue = { min: 0, max: 1_000_000 };
export const PROMPT_PRICE_CNY_LIMIT: RangeValue = { min: 0, max: 600 };

export const inputModalityOptions: Option<InputModality>[] = [
  { value: "text", label: "文字输入", icon: "文" },
  { value: "image", label: "图片输入", icon: "图" },
  { value: "audio", label: "音频输入", icon: "音" },
  { value: "video", label: "视频输入", icon: "影" },
  { value: "file", label: "文件输入（看卡片状态）", icon: "档" },
];

export const contextQuickOptions = [
  { label: "4K+", value: { min: 4_000, max: CONTEXT_RANGE_LIMIT.max } },
  { label: "8K+", value: { min: 8_000, max: CONTEXT_RANGE_LIMIT.max } },
  { label: "32K+", value: { min: 32_000, max: CONTEXT_RANGE_LIMIT.max } },
  { label: "128K+", value: { min: 128_000, max: CONTEXT_RANGE_LIMIT.max } },
];

export const priceQuickOptions = [
  { label: "免费", value: { min: 0, max: 0 } },
  { label: "≤¥1", value: { min: 0, max: 1 } },
  { label: "¥1-5", value: { min: 1, max: 5 } },
  { label: "¥5+", value: { min: 5, max: PROMPT_PRICE_CNY_LIMIT.max } },
];

export const seriesOptions: Option<string>[] = [
  "GPT-5",
  "GPT-4.5",
  "GPT-4o",
  "o3",
  "Claude 4",
  "Gemini",
  "Gemma 3",
  "Llama 4",
  "Llama 3",
  "DeepSeek V4",
  "DeepSeek V3",
  "Mistral Large",
  "Mistral Small",
  "Mistral Medium",
  "Pixtral",
  "Voxtral",
  "Phi-4",
  "Command R",
  "Qwen 3",
  "Nemotron 3",
  "Hy3",
  "Grok",
  "Hermes",
  "MiniMax",
].map((series) => ({ value: series, label: series }));

export const jobCapabilityOptions: Option<JobCapability>[] = [
  { value: "text_chat", label: "文字对话" },
  { value: "coding", label: "编程开发" },
  { value: "reasoning", label: "推理分析" },
  { value: "tool_use", label: "工具调用" },
  { value: "document_understanding", label: "文档理解（看卡片状态）" },
  { value: "image_understanding", label: "图片识别" },
  { value: "image_generation", label: "图片生成/编辑" },
  { value: "audio_understanding", label: "音频理解" },
  { value: "transcription", label: "语音转写" },
  { value: "speech_synthesis", label: "语音合成" },
  { value: "music_generation", label: "音乐生成" },
  { value: "realtime_voice", label: "实时语音" },
  { value: "video_understanding", label: "视频理解" },
  { value: "video_generation", label: "视频生成" },
  { value: "embedding", label: "向量化" },
  { value: "rerank", label: "检索重排" },
  { value: "translation", label: "翻译" },
  { value: "safety", label: "安全审核" },
  { value: "world_generation", label: "3D 世界生成" },
];

export const supportedParameterOptions: Option<SupportedParameter>[] = [
  { value: "tools", label: "tools" },
  { value: "tool_choice", label: "tool_choice" },
  { value: "max_tokens", label: "max_tokens" },
  { value: "temperature", label: "temperature" },
  { value: "top_p", label: "top_p" },
  { value: "top_k", label: "top_k" },
  { value: "frequency_penalty", label: "frequency_penalty" },
  { value: "presence_penalty", label: "presence_penalty" },
  { value: "stop", label: "stop" },
  { value: "response_format", label: "response_format" },
  { value: "structured_outputs", label: "structured_outputs" },
  { value: "seed", label: "seed" },
  { value: "logprobs", label: "logprobs" },
  { value: "logit_bias", label: "logit_bias" },
  { value: "top_logprobs", label: "top_logprobs" },
  { value: "reasoning", label: "reasoning" },
  { value: "include_reasoning", label: "include_reasoning" },
  { value: "reasoning_effort", label: "reasoning_effort" },
  { value: "max_completion_tokens", label: "max_completion_tokens" },
  { value: "parallel_tool_calls", label: "parallel_tool_calls" },
  { value: "min_p", label: "min_p" },
  { value: "top_a", label: "top_a" },
  { value: "repetition_penalty", label: "repetition_penalty" },
  { value: "verbosity", label: "verbosity" },
  { value: "web_search_options", label: "web_search_options" },
];

const majorProviderValues: Provider[] = [
  "OpenAI",
  "Anthropic",
  "Google",
  "Meta",
  "DeepSeek",
  "深度求索",
  "Mistral AI",
  "Microsoft",
  "xAI",
  "Nous Research",
  "通义千问",
  "NVIDIA",
  "Cohere",
  "Perplexity",
  "MiniMax",
  "月之暗面",
  "阿里巴巴",
  "腾讯",
  "字节跳动",
  "百度",
  "小米",
  "Z.ai",
];

const modelProviderValues: Provider[] = Array.from(
  new Set(
    models.flatMap((model) => [
      model.provider,
      model.model_author,
      getProviderDisplayName(model.provider),
      getProviderDisplayName(model.model_author),
    ]),
  ),
).filter((provider) => provider.length > 0);

export const providerOptions: Option<Provider>[] = Array.from(
  new Set([...majorProviderValues, ...modelProviderValues]),
)
  .sort((left, right) => left.localeCompare(right, "zh-CN"))
  .map((provider) => ({ value: provider, label: getProviderDisplayName(provider) }));

export const modelAuthorOptions: Option<string>[] = [
  "OpenAI",
  "Anthropic",
  "Google",
  "Meta",
  "DeepSeek",
  "Mistral AI",
  "Microsoft",
  "Cohere",
  "Qwen",
  "NVIDIA",
  "Tencent",
  "xAI",
  "Nous Research",
  "模镜",
  "MiniMax",
].map((author) => ({ value: author, label: author }));
