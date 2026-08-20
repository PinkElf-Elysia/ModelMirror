// Merged with OpenRouter model catalog on 2026-08-20T10:16:33.909Z.
// Current OpenRouter refresh verified on 2026-08-20 against the live all-modalities catalog.
// Refreshed with entries published through 2026-08-19T14:50:53.000Z.
// Source: https://openrouter.ai/api/v1/models?output_modalities=all&sort=newest&offset=0&limit=1000
// Full OpenRouter catalog audit refresh: 2026-08-16. Batch catalog entries are
// attached to their canonical models as serving variants and excluded from
// snapshot totals; media-only records are cross-checked against their dedicated
// Images, Speech, and Video API catalogs instead of the text-only model list.
// Image source: https://openrouter.ai/api/v1/images/models
// Speech source: https://openrouter.ai/api/v1/models?output_modalities=speech
// Video source: https://openrouter.ai/api/v1/videos/models
// Prices are stored as USD per 1M tokens and CNY per 1M tokens.
import {
  EMPTY_OPENROUTER_MARKET_SNAPSHOT,
  type OpenRouterMarketSeries,
  type OpenRouterMarketSnapshot,
} from "./openRouterMarket";
import { openRouterMarketSnapshotByModelId } from "./openRouterMarketSnapshot";

export const USD_TO_CNY = 6.77;

export type Provider = string;

export type Capability =
  | "text"
  | "image"
  | "code"
  | "tool"
  | "audio"
  | "video"
  | "reasoning";

export type InputModality = "text" | "image" | "audio" | "video" | "file";
export type OutputModality =
  | "text"
  | "image"
  | "audio"
  | "speech"
  | "transcription"
  | "video"
  | "world"
  | "embeddings"
  | "rerank";
export type ModelOperation =
  | "chat"
  | "analyze_document"
  | "analyze_image"
  | "generate_image"
  | "transcribe"
  | "synthesize_speech"
  | "generate_audio"
  | "analyze_audio"
  | "realtime_voice"
  | "analyze_video"
  | "generate_video"
  | "generate_world"
  | "embed"
  | "rerank";
export type JobCapability =
  | "text_chat"
  | "coding"
  | "reasoning"
  | "tool_use"
  | "document_understanding"
  | "image_understanding"
  | "image_generation"
  | "audio_understanding"
  | "transcription"
  | "speech_synthesis"
  | "music_generation"
  | "realtime_voice"
  | "video_understanding"
  | "video_generation"
  | "embedding"
  | "rerank"
  | "safety"
  | "world_generation";
export type InteractionStatus = "ready" | "planned" | "unsupported";
export type ModelUiEntrypoint = "chat" | "rag" | "multimodal" | "planned";
export type CatalogStatus =
  | "live"
  | "curated"
  | "uncertain"
  | "expired";
export type Category = string;
export type SupportedParameter = string;
export type PricingTier = "free" | "dynamic" | "low" | "medium" | "high";
export type PricingStatus = "fixed" | "free" | "dynamic";
export type PricingBasis = "token" | "media" | "request" | "dynamic" | "free";
export type ModelServingVariantType = "realtime" | "batch";
export type ModelServingEndpoint =
  | "synchronous"
  | "/v1/chat/completions"
  | "/v1/embeddings";

export interface TokenPricing {
  input: number;
  output: number;
}

export interface TokenPricingOverride {
  min_prompt_tokens: number;
  pricing: TokenPricing;
  price_cny: TokenPricing;
}

export interface TimeWindowPricingOverride {
  /** Inclusive UTC start, encoded as an HHMM clock value. */
  utc_start: number;
  /** Exclusive UTC end, encoded as HHMM; values at or before start wrap overnight. */
  utc_end: number;
  pricing: TokenPricing;
  price_cny: TokenPricing;
}

export interface ModelServingVariant {
  type: ModelServingVariantType;
  catalog_id: string;
  request_model_id: string;
  endpoint: ModelServingEndpoint;
  pricing: TokenPricing;
  pricing_overrides: TokenPricingOverride[];
  pricing_time_windows: TimeWindowPricingOverride[];
  price_cny: {
    input: number;
    output: number;
  };
  input_modalities: InputModality[];
  output_modalities: OutputModality[];
  completion_window?: "24h";
  data_retention_days?: number;
}

export interface Model {
  id: string;
  canonical_slug: string;
  name: string;
  provider: Provider;
  model_author: string;
  description: string;
  context_length: number;
  pricing: TokenPricing;
  pricing_overrides: TokenPricingOverride[];
  pricing_time_windows: TimeWindowPricingOverride[];
  price_cny: {
    input: number;
    output: number;
  };
  pricing_status: PricingStatus;
  pricing_basis: PricingBasis;
  pricing_tier: PricingTier;
  capabilities: Capability[];
  input_modalities: InputModality[];
  output_modalities: OutputModality[];
  operations: ModelOperation[];
  job_capabilities: JobCapability[];
  primary_operation: ModelOperation;
  interaction_status: InteractionStatus;
  ui_entrypoint: ModelUiEntrypoint;
  series: string;
  categories: Category[];
  /** Snapshot of the filters exposed by OpenRouter's /models sidebar. */
  openrouter_market: OpenRouterMarketSnapshot;
  supported_parameters: SupportedParameter[];
  reasoning_declared: boolean;
  distillable: boolean;
  zero_data_retention: boolean;
  in_region_routing: boolean;
  catalog_status: CatalogStatus;
  /** True only for records that belong to the OpenRouter model snapshot. */
  catalog_counted: boolean;
  serving_variants: ModelServingVariant[];
  active: boolean;
  tags: string[];
  note?: string;
  /** True when this entry is a world-generation model (3D), not a chat model. */
  worldModel?: boolean;
}

interface RawCatalogModel {
  id: string;
  canonical_slug: string;
  name: string;
  raw_description: string;
  context_length: number;
  pricing: TokenPricing & {
    overrides?: Array<{
      min_prompt_tokens: number;
      input: number;
      output: number;
    }>;
    time_overrides?: Array<{
      utc_start: number;
      utc_end: number;
      input: number;
      output: number;
    }>;
  };
  input_modalities: InputModality[];
  output_modalities: OutputModality[];
  tokenizer: string;
  supported_parameters: SupportedParameter[];
  created: number;
  expiration_date: number | null;
  model_author: string;
  reasoning_declared?: boolean;
  note?: string;
}

const rawCatalogModels: RawCatalogModel[] = [
  {
    "id": "~z-ai/glm-latest",
    "canonical_slug": "~z-ai/glm-latest",
    "name": "Z.ai: GLM Latest",
    "raw_description": "This model always redirects to the latest GLM model from Z.ai.",
    "context_length": 1048576,
    "pricing": {
      "input": 1.4,
      "output": 4.4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1787151053,
    "expiration_date": 4070822400,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-5.3",
    "canonical_slug": "z-ai/glm-5.3-20260816",
    "name": "Z.ai: GLM 5.3",
    "raw_description": "GLM-5.3 is a large-scale reasoning model from Z.ai, built for complex software engineering and long-horizon agent tasks. It supports text input and output with a 1M-token context window.",
    "context_length": 1048576,
    "pricing": {
      "input": 1.4,
      "output": 4.4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1787086655,
    "expiration_date": 4070822400,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "liquid/lfm-2.5-embedding-350m:free",
    "canonical_slug": "liquid/lfm-2.5-embedding-350m-20260818",
    "name": "LiquidAI: LFM2.5-Embedding-350M (free)",
    "raw_description": "LFM2.5-Embedding-350M is a text embedding model from Liquid AI. It produces 1,024-dimensional embeddings for retrieval and semantic search. Successful OpenRouter requests and embeddings may be retained and used to train models.",
    "context_length": 512,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1787077908,
    "expiration_date": null,
    "model_author": "LiquidAI"
  },
  {
    "id": "qwen/qwen3.8-27b",
    "canonical_slug": "qwen/qwen3.8-27b-20260814",
    "name": "Qwen: Qwen3.8 27B",
    "raw_description": "Qwen3.8 27B is an open-weight dense vision-language model from Qwen. It is suited for coding, professional workflows, research, multimodal interaction, and long-running agent tasks, with flexible thinking that can be...",
    "context_length": 262144,
    "pricing": {
      "input": 0.44999999999999996,
      "output": 3.1999999999999997
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786722910,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "dots-studio/dots-3-note-preview:free",
    "canonical_slug": "dots-studio/dots-3-note-preview-20260813",
    "name": "Dots Studio: Dots3-Note Preview (free)",
    "raw_description": "Dots3-Note Preview is an open-weight mixture-of-experts model from Dots Studio, with 16B active parameters out of 280B total. It is the lightest model in the Dots 3 family and is...",
    "context_length": 512000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1786680361,
    "expiration_date": null,
    "model_author": "Dots Studio",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
    "canonical_slug": "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b-20260813",
    "name": "NVIDIA: Nemotron 3.5 ASR Streaming Multilingual 0.6B",
    "raw_description": "Nemotron 3.5 ASR Streaming Multilingual 0.6B is a speech recognition model from NVIDIA. Its prompt-conditioned, cache-aware FastConformer-RNNT design targets low-latency transcription across more than 40 languages for real-time captioning, voice...",
    "context_length": 0,
    "pricing": {
      "input": 3.33,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1786654371,
    "expiration_date": null,
    "model_author": "NVIDIA"
  },
  {
    "id": "mistralai/voxtral-small-24b-2507-stt",
    "canonical_slug": "mistralai/voxtral-small-24b-2507-stt-20260813",
    "name": "Mistral: Voxtral Small 24B 2507 STT",
    "raw_description": "Voxtral Small 24B 2507 STT is a speech transcription model from Mistral AI. It is suited for transcription, translation, and audio understanding workloads that benefit from its larger model capacity.",
    "context_length": 0,
    "pricing": {
      "input": 50,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1786654002,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "mistralai/voxtral-mini-3b-2507",
    "canonical_slug": "mistralai/voxtral-mini-3b-2507-20260813",
    "name": "Mistral: Voxtral Mini 3B 2507",
    "raw_description": "Voxtral Mini 3B 2507 is a speech and audio understanding model from Mistral AI. It is suited for transcription, translation, and compact audio processing workloads.",
    "context_length": 0,
    "pricing": {
      "input": 16.666700000000002,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1786653980,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "bytedance-seed/seedream-5-0-lite",
    "canonical_slug": "bytedance-seed/seedream-5-0-lite-20260812",
    "name": "ByteDance Seed: Seedream 5.0 Lite",
    "raw_description": "Seedream 5.0 Lite is an image generation model from ByteDance Seed. It is suited for professional visual creation that benefits from web-connected retrieval, complex-prompt comprehension, visual references, and broad knowledge...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Media",
    "supported_parameters": [
      "resolution",
      "aspect_ratio",
      "n",
      "input_references",
      "seed"
    ],
    "created": 1786650094,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "note": "OpenRouter 专用 Images API：非流式；支持 2K/4K、18 种宽高比、1–4 张输出、最多 14 张参考图和 seed；当前输出价格为 $0.035/张。"
  },
  {
    "id": "google/gemini-3.7-flash",
    "canonical_slug": "google/gemini-3.7-flash-20260813",
    "name": "Google: Gemini 3.7 Flash",
    "raw_description": "Gemini 3.7 Flash is a multimodal model from Google for fast agentic workflows, coding, and complex multi-step reasoning. It is designed for tasks that require responsive performance and reliable multi-step...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.375,
      "output": 1.875
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1786640581,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "voyageai/voyage-code-4",
    "canonical_slug": "voyageai/voyage-code-4-20260812",
    "name": "VoyageAI by MongoDB: voyage-code-4",
    "raw_description": "voyage-code-4 is a code embedding model from Voyage AI, a MongoDB company. It is designed for coding agents and code retrieval, with Matryoshka embeddings at 2048, 1024, 512, and 256...",
    "context_length": 32000,
    "pricing": {
      "input": 0.12,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1786636912,
    "expiration_date": null,
    "model_author": "Voyageai"
  },
  {
    "id": "qwen/qwen3-reranker-8b",
    "canonical_slug": "qwen/qwen3-reranker-8b",
    "name": "Qwen3 Reranker 8B",
    "raw_description": "Qwen3 Reranker 8B is a text reranking model from Alibaba Cloud built on the Qwen3 architecture. It evaluates query-document pairs to produce relevance scores for use in retrieval and RAG...",
    "context_length": 40960,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "rerank"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786597684,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen3-asr-1.7b",
    "canonical_slug": "qwen/qwen3-asr-1.7b-20260813",
    "name": "Qwen: Qwen3 ASR 1.7B",
    "raw_description": "Qwen3 ASR 1.7B is an automatic speech recognition model from Qwen. It supports multilingual language identification and transcription across 30 languages and 22 Chinese dialects, with streaming and offline inference...",
    "context_length": 0,
    "pricing": {
      "input": 7.5,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1786592646,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen3-asr-0.6b",
    "canonical_slug": "qwen/qwen3-asr-0.6b-20260813",
    "name": "Qwen: Qwen3 ASR 0.6B",
    "raw_description": "Qwen3 ASR 0.6B is a compact automatic speech recognition model from Qwen. It supports multilingual language identification and transcription across 30 languages and 22 Chinese dialects, with streaming and offline...",
    "context_length": 0,
    "pricing": {
      "input": 3.33,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1786591833,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "bytedance-seed/seedream-5-0-pro",
    "canonical_slug": "bytedance-seed/seedream-5-0-pro-20260812",
    "name": "ByteDance Seed: Seedream 5.0 Pro",
    "raw_description": "Seedream 5.0 Pro is ByteDance Seed's professional image generation and editing model for natural, lifelike commercial visuals and precise edits. It accepts text and up to 14 image references and returns one image per request.",
    "context_length": 0,
    "pricing": {
      "input": -1,
      "output": -1
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "resolution",
      "aspect_ratio",
      "n",
      "input_references",
      "seed"
    ],
    "created": 1786578139,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "note": "OpenRouter 专用 Images API：支持 1K/2K、18 种宽高比、单次 1 张输出、最多 14 张参考图和 seed；参考图 $0.003/张，输出约 $0.045/张，高分辨率约 $0.09/张。"
  },
  {
    "id": "deepgram/flux-tts:free",
    "canonical_slug": "deepgram/flux-tts-20260812",
    "name": "Deepgram: Flux TTS (free)",
    "raw_description": "Flux TTS is Deepgram's free text-to-speech model for English voice synthesis. It exposes 36 Flux voices through OpenRouter's dedicated speech endpoint and returns binary audio rather than chat-completion text.",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "voice",
      "response_format",
      "speed"
    ],
    "created": 1786574888,
    "expiration_date": null,
    "model_author": "Deepgram",
    "note": "通过 OpenRouter /api/v1/audio/speech 调用；当前目录价格为免费，提供 36 个英文 Flux 音色，响应为原始音频字节。"
  },
  {
    "id": "bytedance/seedance-2.0-mini",
    "canonical_slug": "bytedance/seedance-2.0-mini-20260811",
    "name": "ByteDance: Seedance 2.0 Mini",
    "raw_description": "Seedance 2.0 Mini is a compact ByteDance video generation model supporting text, image, video, and audio guidance. It supports first- and last-frame control, optional generated audio, and 4–15 second video jobs at 480p or 720p.",
    "context_length": 0,
    "pricing": {
      "input": -1,
      "output": -1
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "audio"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Media",
    "supported_parameters": [
      "resolution",
      "aspect_ratio",
      "duration",
      "frame_images",
      "input_references",
      "generate_audio",
      "seed"
    ],
    "created": 1786552600,
    "expiration_date": null,
    "model_author": "ByteDance",
    "note": "通过 OpenRouter 异步 Video API 提交并轮询；支持 480p/720p、4–15 秒、首尾帧与生成音频。价格按视频 token 与输入类型动态结算。"
  },
  {
    "id": "bytedance-seed/seed-2-1-turbo",
    "canonical_slug": "bytedance-seed/seed-2-1-turbo-20260810",
    "name": "ByteDance Seed: Seed 2.1 Turbo",
    "raw_description": "Seed 2.1 Turbo is a multimodal model from ByteDance Seed for coding and long-horizon agent workflows. It is suited for end-to-end software delivery, multi-step task execution, and understanding visual and...",
    "context_length": 262144,
    "pricing": {
      "input": 0.5,
      "output": 2.5
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1786552176,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.8-2.4t-a95b",
    "canonical_slug": "qwen/qwen3.8-2.4t-a95b-20260812",
    "name": "Qwen: Qwen3.8 2.4T A95B",
    "raw_description": "Qwen3.8 2.4T A95B is an open-weight sparse mixture-of-experts model from Qwen and the open-weight variant of [Qwen3.8 Max](/qwen/qwen3.8-max), with 95 billion active parameters out of 2.4 trillion total. It is...",
    "context_length": 1048576,
    "pricing": {
      "input": 2,
      "output": 6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786551702,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "bytedance-seed/seed-2.0-code",
    "canonical_slug": "bytedance-seed/seed-2.0-code-20260730",
    "name": "ByteDance Seed: Seed-2.0-Code",
    "raw_description": "Seed 2.0 Code is a model from ByteDance Seed optimized for agentic coding. It is suited for frontend development, multilingual programming tasks, and coding-agent workflows in tools such as Claude...",
    "context_length": 262144,
    "pricing": {
      "input": 0.5,
      "output": 3,
      "overrides": [
        {
          "min_prompt_tokens": 128000,
          "input": 1,
          "output": 6
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1786550701,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-v4-pro-0813",
    "canonical_slug": "deepseek/deepseek-v4-pro-20260813",
    "name": "DeepSeek: DeepSeek V4 Pro 0813",
    "raw_description": "DeepSeek V4 Pro 0813 is a large-scale mixture-of-experts model from DeepSeek. This is the GA release of DeepSeek V4 Pro.",
    "context_length": 1048576,
    "pricing": {
      "input": 0.66,
      "output": 1.9800000000000002,
      "time_overrides": [
        {
          "utc_start": 1000,
          "utc_end": 100,
          "input": 0.66,
          "output": 1.9800000000000002
        },
        {
          "utc_start": 100,
          "utc_end": 400,
          "input": 1.32,
          "output": 3.9600000000000004
        },
        {
          "utc_start": 400,
          "utc_end": 600,
          "input": 0.66,
          "output": 1.9800000000000002
        },
        {
          "utc_start": 600,
          "utc_end": 1000,
          "input": 1.32,
          "output": 3.9600000000000004
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786549364,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-4.6",
    "canonical_slug": "x-ai/grok-4.6-20260810",
    "name": "SpaceXAI: Grok 4.6",
    "raw_description": "Grok 4.6 is SpaceXAI's smartest model with frontier performance on coding, knowledge work, and STEM.",
    "context_length": 500000,
    "pricing": {
      "input": 2,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 4,
          "output": 12
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786548957,
    "expiration_date": null,
    "model_author": "xAI",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-imagine-image-2.0",
    "canonical_slug": "x-ai/grok-imagine-image-2.0",
    "name": "xAI: Grok Imagine Image 2.0",
    "raw_description": "Grok Imagine Image 2.0 is an image generation and editing model from xAI. It is suited for creating images from text prompts and editing images from references, with low and medium quality modes at 1K or 2K resolution.",
    "context_length": 0,
    "pricing": {
      "input": -1,
      "output": -1
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "resolution",
      "aspect_ratio",
      "quality",
      "n",
      "input_references"
    ],
    "created": 1786486044,
    "expiration_date": null,
    "model_author": "xAI",
    "note": "OpenRouter 专用图片 API：非流式返回完整 base64 图片；每次输出 1 张，最多 3 张参考图。参考图约 $0.01/张，输出按 low/medium 与 1K/2K 约 $0.04–$0.08/张。"
  },
  {
    "id": "liquid/lfm-2.5-2.6b:free",
    "canonical_slug": "liquid/lfm-2.5-2.6b-20260811",
    "name": "LiquidAI: LFM2.5-2.6B (free)",
    "raw_description": "LFM2.5-2.6B is a compact reasoning model from Liquid AI. It is suited for agent workflows, data extraction, RAG, and long-context processing. Liquid advises against using it for agentic coding or...",
    "context_length": 128000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786470519,
    "expiration_date": null,
    "model_author": "LiquidAI",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3.5-lightning",
    "canonical_slug": "nvidia/nemotron-3.5-lightning-20260807",
    "name": "NVIDIA: Nemotron 3.5 Lightning",
    "raw_description": "NVIDIA Nemotron 3.5 Lightning is an open mixture-of-experts model from NVIDIA, with 3B active parameters out of 30B total. It is suited for high-throughput agentic workloads and specialized tasks that...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.08,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786452751,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3.5-lightning:free",
    "canonical_slug": "nvidia/nemotron-3.5-lightning-20260807",
    "name": "NVIDIA: Nemotron 3.5 Lightning (free)",
    "raw_description": "NVIDIA Nemotron 3.5 Lightning is an open mixture-of-experts model from NVIDIA, with 3B active parameters out of 30B total. It is suited for high-throughput agentic workloads and specialized tasks that...",
    "context_length": 1000000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1786452751,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "sakana/sakana-namazu",
    "canonical_slug": "sakana/namazu-20260811",
    "name": "Sakana: Sakana Namazu",
    "raw_description": "Sakana Namazu is a Japanese-specialized reasoning model from Sakana AI, based on Kimi K2.6 with additional training for Japanese language and business contexts. It is suited for Japanese instruction following,...",
    "context_length": 262144,
    "pricing": {
      "input": 0.95,
      "output": 4
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "reasoning",
      "reasoning_effort",
      "structured_outputs",
      "tool_choice",
      "tools",
      "web_search_options"
    ],
    "created": 1786410129,
    "expiration_date": null,
    "model_author": "Sakana",
    "reasoning_declared": true
  },
  {
    "id": "upstage/solar-pro4",
    "canonical_slug": "upstage/solar-pro4-20260810",
    "name": "Upstage: Solar Pro 4",
    "raw_description": "Solar Pro 4 is Upstage's cost-efficient large language model, featuring a 524K context window. It is built for long-horizon tasks and agentic workflows, with strong capabilities in office productivity, document-intensive...",
    "context_length": 524288,
    "pricing": {
      "input": 0.03,
      "output": 0.12
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1786371636,
    "expiration_date": null,
    "model_author": "Upstage",
    "reasoning_declared": true
  },
  {
    "id": "meta/muse-glimmer-30b",
    "canonical_slug": "meta/muse-glimmer-30b-20260810",
    "name": "Meta: Muse Glimmer 30B",
    "raw_description": "Muse Glimmer 30B is a dense, open-weight multimodal model from Meta Superintelligence Labs, distilled from Muse Spark and optimized for autonomous agents on consumer hardware. It is suited for long-horizon...",
    "context_length": 131072,
    "pricing": {
      "input": 0.35,
      "output": 1.5
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786302394,
    "expiration_date": null,
    "model_author": "Meta",
    "reasoning_declared": true
  },
  {
    "id": "bytedance/seedance-2.5",
    "canonical_slug": "bytedance/seedance-2.5-20260807",
    "name": "ByteDance: Seedance 2.5",
    "raw_description": "Seedance 2.5 is a video generation model from ByteDance. It is suited for long-form storytelling, multimodal reference-based generation, video editing, and video extension. It supports first-frame and first-and-last-frame control, up...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "audio"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Media",
    "supported_parameters": [
      "frequency_penalty"
    ],
    "created": 1786141253,
    "expiration_date": null,
    "model_author": "ByteDance"
  },
  {
    "id": "inclusionai/ling-3.0-tiny:free",
    "canonical_slug": "inclusionai/ling-3.0-tiny-20260806",
    "name": "inclusionAI: Ling 3.0 Tiny (free)",
    "raw_description": "Ling 3.0 Tiny is a mixture-of-experts model from InclusionAI, with 1.3B active parameters out of 7.9B total. It is designed for responsive agents, instruction following, and multi-turn conversations, with switchable...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1786034890,
    "expiration_date": 1786579200,
    "model_author": "InclusionAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-transcribe",
    "canonical_slug": "openai/gpt-transcribe-20260805",
    "name": "OpenAI: GPT Transcribe",
    "raw_description": "GPT Transcribe is a high-accuracy speech-to-text model from OpenAI. It is suited for recorded audio, streamed file transcription, and committed Realtime turns, with free-form context, keyword hints, and multiple language...",
    "context_length": 0,
    "pricing": {
      "input": 4500,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1785973897,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "meta/muse-spark-1.2",
    "canonical_slug": "meta/muse-spark-1.2-20260805",
    "name": "Meta: Muse Spark 1.2",
    "raw_description": "Muse Spark 1.2 is a reasoning model from Meta, designed for complex agentic tasks. It accepts text, images, video, audio, and PDF documents, returns text, and offers a 1M-token context...",
    "context_length": 1048576,
    "pricing": {
      "input": 1.25,
      "output": 4.25
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1785959287,
    "expiration_date": null,
    "model_author": "Meta",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen-image-3",
    "canonical_slug": "qwen/qwen-image-3-20260805",
    "name": "Qwen: Qwen Image 3",
    "raw_description": "Qwen Image 3 is a unified image generation and editing model from Qwen. It supports precise rendering of text and details as small as 10px, along with a richer world...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1785894548,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen-image-3-pro",
    "canonical_slug": "qwen/qwen-image-3-pro-20260805",
    "name": "Qwen: Qwen Image 3 Pro",
    "raw_description": "Qwen Image 3 Pro is an image generation and editing model from Qwen. It supports precise rendering of text and details as small as 10px, along with richer world knowledge...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1785894548,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "black-forest-labs/flux-3-video",
    "canonical_slug": "black-forest-labs/flux-3-video-20260804",
    "name": "Black Forest Labs: FLUX.3 Video",
    "raw_description": "FLUX.3 Video is a video generation model from Black Forest Labs. It supports text-to-video, image-guided generation with opening and closing keyframes, and video continuation workflows, making it suited for controlled...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Media",
    "supported_parameters": [],
    "created": 1785858831,
    "expiration_date": null,
    "model_author": "Black Forest Labs"
  },
  {
    "id": "qwen/qwen3.8-max",
    "canonical_slug": "qwen/qwen3.8-max-20260803",
    "name": "Qwen: Qwen3.8 Max",
    "raw_description": "Qwen3.8 Max is the flagship model in Alibaba's Qwen3.8 series, the general-availability successor to the Qwen3.8 Max Preview. It is a multimodal reasoning model intended for complex reasoning, visual understanding,...",
    "context_length": 1000000,
    "pricing": {
      "input": 2,
      "output": 6
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1785731612,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "~deepseek/deepseek-v4-flash-latest",
    "canonical_slug": "~deepseek/deepseek-v4-flash-latest",
    "name": "DeepSeek V4 Flash Latest",
    "raw_description": "This model always redirects to the latest model in the DeepSeek V4 Flash family.",
    "context_length": 1048576,
    "pricing": {
      "input": 0.060300000000000006,
      "output": 0.12060000000000001
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "parallel_tool_calls",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1785606009,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-v4-flash-0731",
    "canonical_slug": "deepseek/deepseek-v4-flash-20260731",
    "name": "DeepSeek: DeepSeek V4 Flash 0731",
    "raw_description": "DeepSeek V4 Flash 0731 is a sparse mixture-of-experts model from DeepSeek, with 13B active parameters out of 284B total. This re-post-trained revision is suited for coding, reasoning, and agent workflows....",
    "context_length": 1048576,
    "pricing": {
      "input": 0.14,
      "output": 0.28
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "parallel_tool_calls",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1785478908,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "thinkingmachines/inkling-small",
    "canonical_slug": "thinkingmachines/inkling-small-20260730",
    "name": "Thinking Machines: Inkling Small",
    "raw_description": "Inkling Small is an open-weight multimodal mixture-of-experts model from Thinking Machines Lab, with 12B active parameters out of 276B total. It is positioned as the smaller, more efficient member of...",
    "context_length": 524288,
    "pricing": {
      "input": 0.44999999999999996,
      "output": 1.2
    },
    "input_modalities": [
      "text",
      "image",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1785443117,
    "expiration_date": null,
    "model_author": "Thinkingmachines",
    "reasoning_declared": true
  },
  {
    "id": "minimax/hailuo-3",
    "canonical_slug": "minimax/hailuo-03-20260730",
    "name": "MiniMax: H3",
    "raw_description": "MiniMax H3 is a lightweight, open-weights video generation model from MiniMax. It is designed for precise multimodal editing and controlled content generation, including instruction-guided edits, text and brand rendering, and...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "audio"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1785366648,
    "expiration_date": null,
    "model_author": "MiniMax"
  },
  {
    "id": "fish-audio/transcribe-1",
    "canonical_slug": "fish-audio/transcribe-1-20260729",
    "name": "Fish Audio: Transcribe 1",
    "raw_description": "Transcribe 1 is a speech-to-text model from Fish Audio. It is suited for audio transcription with automatic language detection and can return timestamped word-level segments when alignment details are requested.",
    "context_length": 0,
    "pricing": {
      "input": 100,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785353735,
    "expiration_date": null,
    "model_author": "Fish Audio"
  },
  {
    "id": "fish-audio/s1",
    "canonical_slug": "fish-audio/s1-20260729",
    "name": "Fish Audio: S1",
    "raw_description": "S1 is a multilingual text-to-speech model from Fish Audio. It is suited for voice applications that need broad emotional expression, using parenthetical controls to guide speaking style across its supported...",
    "context_length": 0,
    "pricing": {
      "input": 15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785353734,
    "expiration_date": null,
    "model_author": "Fish Audio"
  },
  {
    "id": "fish-audio/s2-pro",
    "canonical_slug": "fish-audio/s2-pro-20260729",
    "name": "Fish Audio: S2 Pro",
    "raw_description": "S2 Pro is a multilingual text-to-speech model from Fish Audio. It is suited for expressive narration and multi-speaker dialogue, with natural-language controls for speaking style and emotion.",
    "context_length": 0,
    "pricing": {
      "input": 15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785353734,
    "expiration_date": null,
    "model_author": "Fish Audio"
  },
  {
    "id": "fish-audio/s2.1-pro-free:free",
    "canonical_slug": "fish-audio/s2.1-pro-free-20260729",
    "name": "Fish Audio: S2.1 Pro Free (free)",
    "raw_description": "S2.1 Pro Free is the no-cost variant of Fish Audio S2.1 Pro, intended for testing, prototyping, and low-volume applications. It provides the same synthesis capabilities without production latency or availability...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785353733,
    "expiration_date": null,
    "model_author": "Fish Audio"
  },
  {
    "id": "fish-audio/s2.1-pro",
    "canonical_slug": "fish-audio/s2.1-pro-20260729",
    "name": "Fish Audio: S2.1 Pro",
    "raw_description": "S2.1 Pro is a production-oriented text-to-speech model from Fish Audio. It is suited for multilingual voice applications, expressive narration, and dialogue synthesis, with open-ended natural-language controls for speaking style and...",
    "context_length": 0,
    "pricing": {
      "input": 15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785353732,
    "expiration_date": null,
    "model_author": "Fish Audio"
  },
  {
    "id": "runway/aleph-2",
    "canonical_slug": "runway/aleph-2-20260729",
    "name": "Runway: Aleph 2.0",
    "raw_description": "Runway Aleph 2.0 is an in-context video editing model from Runway. It applies text instructions and keyframe-guided edits across existing footage while preserving details that are not meant to change....",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Media",
    "supported_parameters": [],
    "created": 1785339484,
    "expiration_date": null,
    "model_author": "Runway"
  },
  {
    "id": "runway/gen-4.5",
    "canonical_slug": "runway/gen-4.5-20260729",
    "name": "Runway: Gen-4.5",
    "raw_description": "Runway Gen-4.5 is a video generation model from Runway for text-to-video and image-to-video workflows. It is designed for cinematic scene creation with strong motion quality, visual fidelity, and prompt adherence....",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Media",
    "supported_parameters": [],
    "created": 1785339483,
    "expiration_date": null,
    "model_author": "Runway"
  },
  {
    "id": "qwen/qwen3.7-flash",
    "canonical_slug": "qwen/qwen3.7-flash-20260727",
    "name": "Qwen: Qwen3.7 Flash",
    "raw_description": "Qwen3.7 Flash is a vision-language reasoning model from Alibaba. It is suited for multimodal agents, visual coding, search, and computer interaction, with strengths in object recognition, spatial understanding, and real-world...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.03,
      "output": 0.13,
      "overrides": [
        {
          "min_prompt_tokens": 32000,
          "input": 0.09999999999999999,
          "output": 0.39999999999999997
        },
        {
          "min_prompt_tokens": 256000,
          "input": 0.19999999999999998,
          "output": 0.7999999999999999
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1785190561,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "voyageai/rerank-2.5-lite",
    "canonical_slug": "voyageai/rerank-2.5-lite-20260727",
    "name": "VoyageAI by MongoDB: rerank-2.5-lite",
    "raw_description": "rerank-2.5-lite is a reranker optimized for both latency and quality, delivering a 7.16% improvement in retrieval accuracy over Cohere Rerank v3.5 across 93 datasets. It also outperformed Cohere Rerank v3.5...",
    "context_length": 32000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "rerank"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785188631,
    "expiration_date": null,
    "model_author": "Voyageai"
  },
  {
    "id": "voyageai/rerank-2.5",
    "canonical_slug": "voyageai/rerank-2.5-20260727",
    "name": "VoyageAI by MongoDB: rerank-2.5",
    "raw_description": "rerank-2.5 is a cutting-edge reranker optimized for quality, delivering a 7.94% improvement in retrieval accuracy over Cohere Rerank v3.5 across 93 datasets. It also outperformed Cohere Rerank v3.5 by 12.70%...",
    "context_length": 32000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "rerank"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785188630,
    "expiration_date": null,
    "model_author": "Voyageai"
  },
  {
    "id": "voyageai/voyage-multimodal-3.5",
    "canonical_slug": "voyageai/voyage-multimodal-3.5-20260727",
    "name": "VoyageAI by MongoDB: voyage-multimodal-3.5",
    "raw_description": "voyage-multimodal-3.5 is a state-of-the-art multimodal embedding model capable of vectorizing not only text, images, and video individually, but also content that interleaves all three modalities. It delivers excellent performance for...",
    "context_length": 32000,
    "pricing": {
      "input": 0.12,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785188629,
    "expiration_date": null,
    "model_author": "Voyageai"
  },
  {
    "id": "voyageai/voyage-4-lite",
    "canonical_slug": "voyageai/voyage-4-lite-20260727",
    "name": "VoyageAI by MongoDB: voyage-4-lite",
    "raw_description": "voyage-4-lite is a lightweight, general-purpose embedding model optimized for low latency and cost. Enabled by Matryoshka learning and quantization-aware training, voyage-4-lite supports embeddings in 2048, 1024, 512, and 256 dimensions,...",
    "context_length": 32000,
    "pricing": {
      "input": 0.02,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785188627,
    "expiration_date": null,
    "model_author": "Voyageai"
  },
  {
    "id": "voyageai/voyage-4",
    "canonical_slug": "voyageai/voyage-4-20260727",
    "name": "VoyageAI by MongoDB: voyage-4",
    "raw_description": "voyage-4 is a general-purpose (including multilingual) embedding model optimized for retrieval/search and AI applications. voyage-4 supports embeddings in 2048, 1024, 512, and 256 dimensions, with multiple quantization options. Learn more...",
    "context_length": 32000,
    "pricing": {
      "input": 0.06,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785188626,
    "expiration_date": null,
    "model_author": "Voyageai"
  },
  {
    "id": "voyageai/voyage-4-large",
    "canonical_slug": "voyageai/voyage-4-large-20260727",
    "name": "VoyageAI by MongoDB: voyage-4-large",
    "raw_description": "voyage-4-large is a state-of-the-art general-purpose and multilingual embedding optimized for retrieval quality. Enabled by Matryoshka learning and quantization-aware training, voyage-4-large supports embeddings in 2048, 1024, 512, and 256 dimensions, with...",
    "context_length": 32000,
    "pricing": {
      "input": 0.12,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1785188624,
    "expiration_date": null,
    "model_author": "Voyageai"
  },
  {
    "id": "anthropic/claude-opus-5-fast",
    "canonical_slug": "anthropic/claude-opus-5-fast-20260723",
    "name": "Claude Opus 5 (Fast)",
    "raw_description": "Fast-mode variant of [Opus 5](/anthropic/claude-opus-5) - identical capabilities with higher output speed at 2x pricing relative to regular Opus 5.\n\nLearn more in Anthropic's docs: https://platform.claude.com/docs/en/build-with-claude/fast-mode",
    "context_length": 1000000,
    "pricing": {
      "input": 10,
      "output": 50
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1784912546,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-5",
    "canonical_slug": "anthropic/claude-opus-5-20260723",
    "name": "Claude Opus 5",
    "raw_description": "Claude Opus 5 is Anthropic’s flagship model for demanding reasoning, coding, and long-horizon agentic work. It is particularly strong at end-to-end software tasks, code review and bug finding, visual analysis...",
    "context_length": 1000000,
    "pricing": {
      "input": 5,
      "output": 25
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1784912544,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "microsoft/mai-image-2.5-pro",
    "canonical_slug": "microsoft/mai-image-2.5-pro-20260723",
    "name": "Microsoft: MAI-Image-2.5 Pro",
    "raw_description": "Microsoft's MAI-Image-2.5 is a high-quality image generation model available via Azure AI Foundry. It produces photorealistic and artistic images from text prompts with support for various aspect ratios.",
    "context_length": 4096,
    "pricing": {
      "input": 5,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "temperature"
    ],
    "created": 1784827701,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "microsoft/mai-voice-2-flash",
    "canonical_slug": "microsoft/mai-voice-2-flash-20260723",
    "name": "Microsoft: MAI-Voice-2-Flash",
    "raw_description": "MAI-Voice-2-Flash is a low-latency text-to-speech model from Microsoft for voice agents, assistants, call centers, accessibility, narration, and other interactive applications. It generates expressive 24 kHz mono speech across 15 languages...",
    "context_length": 0,
    "pricing": {
      "input": 15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1784822080,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "inclusionai/ling-3.0-flash",
    "canonical_slug": "inclusionai/ling-3.0-flash-20260723",
    "name": "Ling-3.0-flash",
    "raw_description": "*Ling-3.0-flash* is a *124B-parameter Mixture-of-Experts (MoE) model*, with approximately *5.1B parameters activated per token*. The model is designed with *token efficiency and production-scale agentic inference* as key priorities, enabling developers...",
    "context_length": 262144,
    "pricing": {
      "input": 0.020999999999999998,
      "output": 0.063
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1784818580,
    "expiration_date": null,
    "model_author": "InclusionAI",
    "reasoning_declared": true
  },
  {
    "id": "inclusionai/ling-3.0-flash:free",
    "canonical_slug": "inclusionai/ling-3.0-flash-20260723",
    "name": "Ling-3.0-flash (free)",
    "raw_description": "*Ling-3.0-flash* is a *124B-parameter Mixture-of-Experts (MoE) model*, with approximately *5.1B parameters activated per token*. The model is designed with *token efficiency and production-scale agentic inference* as key priorities, enabling developers...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1784818580,
    "expiration_date": null,
    "model_author": "inclusionAI"
  },
  {
    "id": "qwen/qwen-audio-3.0-tts-flash",
    "canonical_slug": "qwen/qwen-audio-3.0-tts-flash-20260723",
    "name": "Qwen: Qwen-Audio-3.0-TTS Flash",
    "raw_description": "Qwen-Audio-3.0-TTS Flash is Alibaba's fast, cost-efficient text-to-speech model, generating spoken audio from text via the DashScope Speech Synthesizer API.",
    "context_length": 0,
    "pricing": {
      "input": 15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1784817207,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen-audio-3.0-tts-plus",
    "canonical_slug": "qwen/qwen-audio-3.0-tts-plus-20260723",
    "name": "Qwen: Qwen-Audio-3.0-TTS Plus",
    "raw_description": "Qwen-Audio-3.0-TTS Plus is Alibaba's higher-quality text-to-speech model, generating spoken audio from text via the DashScope Speech Synthesizer API.",
    "context_length": 0,
    "pricing": {
      "input": 20,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1784817207,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "x-ai/grok-stt-1.0",
    "canonical_slug": "x-ai/grok-stt-20260723",
    "name": "SpaceXAI: Grok STT 1.0",
    "raw_description": "Grok STT is SpaceXAI's speech-to-text model, available via the REST /v1/stt endpoint. It supports transcription with word-level timestamps, optional speaker diarization, and multichannel audio.",
    "context_length": 0,
    "pricing": {
      "input": 100000,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1784817014,
    "expiration_date": null,
    "model_author": "xAI"
  },
  {
    "id": "poolside/laguna-s-2.1",
    "canonical_slug": "poolside/laguna-s-2.1-20260720",
    "name": "Poolside: Laguna S 2.1",
    "raw_description": "Laguna S 2.1 is the latest coding agent model from [Poolside](<https://poolside.ai/>). Laguna S 2.1 is a 118B total parameter model with 8B active parameters, scoring 70.2% on Terminal-Bench 2.1 and...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.09,
      "output": 0.18
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1784652683,
    "expiration_date": null,
    "model_author": "Poolside",
    "reasoning_declared": true
  },
  {
    "id": "poolside/laguna-s-2.1:free",
    "canonical_slug": "poolside/laguna-s-2.1-20260720",
    "name": "Poolside: Laguna S 2.1 (free)",
    "raw_description": "Laguna S 2.1 is the latest coding agent model from [Poolside](<https://poolside.ai/>). Laguna S 2.1 is a 118B total parameter model with 8B active parameters, scoring 70.2% on Terminal-Bench 2.1 and...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1784652683,
    "expiration_date": null,
    "model_author": "Poolside",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.6-flash",
    "canonical_slug": "google/gemini-3.6-flash-20260721",
    "name": "Google: Gemini 3.6 Flash",
    "raw_description": "Gemini 3.6 Flash is a high-efficiency model from Google for coding, agentic workflows, and web and app development. It is designed to produce polished outputs with fewer unnecessary edits and...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.75,
      "output": 3.75
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1784646733,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.5-flash-lite",
    "canonical_slug": "google/gemini-3.5-flash-lite-20260721",
    "name": "Google: Gemini 3.5 Flash Lite",
    "raw_description": "Gemini 3.5 Flash Lite is a high-efficiency model from Google with upgraded agentic capabilities. It is suited for subagents that execute focused tasks within complex, multi-agent workflows.",
    "context_length": 1048576,
    "pricing": {
      "input": 0.3,
      "output": 2.5
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1784646726,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "krea/krea-2-large",
    "canonical_slug": "krea/krea-2-large-20260720",
    "name": "Krea: Krea 2 Large",
    "raw_description": "Krea 2 Large is Krea's high-capability image generation model, more than twice the size of Krea 2 Medium. Its lighter post-training gives images a rawer, more textured, and flexible character,...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Media",
    "supported_parameters": [],
    "created": 1784574931,
    "expiration_date": null,
    "model_author": "Krea"
  },
  {
    "id": "krea/krea-2-medium",
    "canonical_slug": "krea/krea-2-medium-20260720",
    "name": "Krea: Krea 2 Medium",
    "raw_description": "Krea 2 Medium is Krea's balanced, cost-efficient image generation model and a practical starting point for a broad range of use cases. Its extensive post-training supports stable, consistent generations, with...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Media",
    "supported_parameters": [],
    "created": 1784574928,
    "expiration_date": null,
    "model_author": "Krea"
  },
  {
    "id": "krea/krea-2-medium-turbo",
    "canonical_slug": "krea/krea-2-medium-turbo-20260720",
    "name": "Krea: Krea 2 Medium Turbo",
    "raw_description": "Krea 2 Medium Turbo is a distilled, speed-focused variant of Krea 2 Medium from Krea. It is designed for rapid iteration and graphic design exploration where fast generation is the...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Media",
    "supported_parameters": [],
    "created": 1784574923,
    "expiration_date": null,
    "model_author": "Krea"
  },
  {
    "id": "meituan/longcat-2.0",
    "canonical_slug": "meituan/longcat-2.0-20260720",
    "name": "Meituan: LongCat 2.0",
    "raw_description": "LongCat 2.0 is a sparse mixture-of-experts language model from Meituan, with 48B active parameters out of 1.6T total. It is suited for coding, repository-level changes, long-horizon problem solving, and agentic...",
    "context_length": 1048756,
    "pricing": {
      "input": 0.3,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1784554658,
    "expiration_date": null,
    "model_author": "Meituan",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-imagine-video-1.5",
    "canonical_slug": "x-ai/grok-imagine-video-1.5-20260719",
    "name": "SpaceXAI: Grok Imagine Video 1.5",
    "raw_description": "Grok Imagine Video 1.5 is a video generation model from SpaceXAI. It creates videos from text prompts, with an optional starting image to guide the scene. It can direct subject...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1784548100,
    "expiration_date": null,
    "model_author": "xAI"
  },
  {
    "id": "thinkingmachines/inkling",
    "canonical_slug": "thinkingmachines/inkling-20260715",
    "name": "Thinking Machines: Inkling",
    "raw_description": "Inkling is an open-weight multimodal mixture-of-experts model from Thinking Machines Lab, with 41B active parameters out of 975B total. It is designed for general-purpose reasoning, coding, agentic and tool-use systems,...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.95,
      "output": 4.05
    },
    "input_modalities": [
      "text",
      "image",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1784325956,
    "expiration_date": null,
    "model_author": "Thinkingmachines",
    "reasoning_declared": true
  },
  {
    "id": "openrouter/auto-beta",
    "canonical_slug": "openrouter/auto-beta",
    "name": "Auto Router (Beta)",
    "raw_description": "Auto Router (Beta) is a task-aware router from OpenRouter. It classifies each request, then routes it the [most popular model](/rankings#task-spend) for that task based on aggregate spend, filtered by your...",
    "context_length": 2000000,
    "pricing": {
      "input": -1000000,
      "output": -1000000
    },
    "input_modalities": [
      "text",
      "image",
      "audio",
      "file",
      "video"
    ],
    "output_modalities": [
      "text",
      "image"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "prediction",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1784311165,
    "expiration_date": null,
    "model_author": "OpenRouter"
  },
  {
    "id": "deepgram/aura-2",
    "canonical_slug": "deepgram/aura-2-20260716",
    "name": "Deepgram: Aura-2",
    "raw_description": "Aura-2 is a multilingual text-to-speech model from Deepgram. It supports Deepgram’s canonical Aura-2 voice catalog for speech synthesis across multiple languages.",
    "context_length": 0,
    "pricing": {
      "input": 30,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1784237167,
    "expiration_date": null,
    "model_author": "Deepgram"
  },
  {
    "id": "moonshotai/kimi-k3",
    "canonical_slug": "moonshotai/kimi-k3-20260715",
    "name": "MoonshotAI: Kimi K3",
    "raw_description": "Kimi K3 is a 2.8T parameter open-weight multimodal reasoning model from Moonshot AI. It is suited for complex coding, knowledge work, and long-horizon agentic workflows, and is particularly strong at...",
    "context_length": 1048576,
    "pricing": {
      "input": 3,
      "output": 15
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1784215858,
    "expiration_date": null,
    "model_author": "MoonshotAI",
    "reasoning_declared": true
  },
  {
    "id": "meta/muse-spark-1.1",
    "canonical_slug": "meta/muse-spark-1.1-20260709",
    "name": "Meta: Muse Spark 1.1",
    "raw_description": "Muse Spark 1.1 is a multimodal reasoning model from Meta, built for agentic tasks. It accepts text, images, video, audio, and PDF documents and returns text, with a 1M-token context...",
    "context_length": 1048576,
    "pricing": {
      "input": 1.25,
      "output": 4.25
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1784215741,
    "expiration_date": null,
    "model_author": "Meta",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3-embed-1b:free",
    "canonical_slug": "nvidia/nemotron-3-embed-1b-20260716",
    "name": "NVIDIA: Nemotron 3 Embed 1B (free)",
    "raw_description": "NVIDIA Nemotron 3 Embed 1B is an open text embedding model from NVIDIA, optimized for high-throughput, low-latency retrieval. It is suited for enterprise search, RAG, code retrieval, and agentic retrieval...",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1784203294,
    "expiration_date": null,
    "model_author": "NVIDIA"
  },
  {
    "id": "minimax/speech-2.8-hd",
    "canonical_slug": "minimax/speech-2.8-hd-20260716",
    "name": "MiniMax: Speech 2.8 HD",
    "raw_description": "MiniMax Speech 2.8 HD is a text-to-speech model from MiniMax. It is suited for applications that generate spoken audio from text and accepts arbitrary MiniMax voice IDs.",
    "context_length": 0,
    "pricing": {
      "input": 100,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1784164001,
    "expiration_date": null,
    "model_author": "MiniMax"
  },
  {
    "id": "minimax/speech-2.8-turbo",
    "canonical_slug": "minimax/speech-2.8-turbo-20260716",
    "name": "MiniMax: Speech 2.8 Turbo",
    "raw_description": "MiniMax Speech 2.8 Turbo is a text-to-speech model from MiniMax. It is suited for applications that generate spoken audio from text and accepts arbitrary MiniMax voice IDs.",
    "context_length": 0,
    "pricing": {
      "input": 60,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1784164000,
    "expiration_date": null,
    "model_author": "MiniMax"
  },
  {
    "id": "deepgram/nova-3",
    "canonical_slug": "deepgram/nova-3-20260714",
    "name": "Deepgram: Nova-3",
    "raw_description": "Deepgram Nova-3 general-purpose speech-to-text model with monolingual and multilingual transcription support.",
    "context_length": 0,
    "pricing": {
      "input": 4300,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1784075761,
    "expiration_date": null,
    "model_author": "Deepgram"
  },
  {
    "id": "kwaipilot/kat-coder-air-v2.5",
    "canonical_slug": "kwaipilot/kat-coder-air-v2.5-20260710",
    "name": "Kwaipilot: KAT-Coder-Air V2.5",
    "raw_description": "KAT-Coder-Air V2.5 is a flagship-level Agentic Coding model that can directly hand over an entire issue or an entire business workflow to it, allowing it to autonomously locate and make...",
    "context_length": 256000,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1783714590,
    "expiration_date": null,
    "model_author": "Kwaipilot"
  },
  {
    "id": "kwaipilot/kat-coder-pro-v2.5",
    "canonical_slug": "kwaipilot/kat-coder-pro-v2.5-20260710",
    "name": "Kwaipilot: KAT-Coder-Pro V2.5",
    "raw_description": "KAT-Coder-Pro V2.5 is a flagship-level Agentic Coding model that can directly hand over an entire issue or an entire business workflow to it, allowing it to autonomously locate and make...",
    "context_length": 256000,
    "pricing": {
      "input": 0.74,
      "output": 2.96
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1783714589,
    "expiration_date": null,
    "model_author": "Kwaipilot"
  },
  {
    "id": "openai/gpt-5.6-luna-pro",
    "canonical_slug": "openai/gpt-5.6-luna-pro-20260709",
    "name": "OpenAI: GPT-5.6 Luna Pro",
    "raw_description": "GPT-5.6 Luna Pro is the same underlying model as [GPT-5.6 Luna](https://openrouter.ai/openai/gpt-5.6-luna), served with `reasoning.mode` set to `pro` for higher-quality responses on complex tasks.\n\nLearn more in OpenAI's docs: https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode",
    "context_length": 1050000,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 0.19999999999999998,
          "output": 0.8999999999999999
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590867,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-luna",
    "canonical_slug": "openai/gpt-5.6-luna-20260709",
    "name": "OpenAI: GPT-5.6 Luna",
    "raw_description": "GPT-5.6 Luna is a fast, cost-efficient model in OpenAI's GPT-5.6 series. It is suited for high-volume, latency-sensitive tasks such as chat, classification, and lightweight agentic workflows, providing capable reasoning for...",
    "context_length": 1050000,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 0.19999999999999998,
          "output": 0.8999999999999999
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590864,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-terra-pro",
    "canonical_slug": "openai/gpt-5.6-terra-pro-20260709",
    "name": "OpenAI: GPT-5.6 Terra Pro",
    "raw_description": "GPT-5.6 Terra Pro is the same underlying model as [GPT-5.6 Terra](https://openrouter.ai/openai/gpt-5.6-terra), served with `reasoning.mode` set to `pro` for higher-quality responses on complex tasks.\n\nLearn more in OpenAI's docs: https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode",
    "context_length": 1050000,
    "pricing": {
      "input": 1,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 2,
          "output": 9
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590861,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-terra",
    "canonical_slug": "openai/gpt-5.6-terra-20260709",
    "name": "OpenAI: GPT-5.6 Terra",
    "raw_description": "GPT-5.6 Terra is a balanced model in OpenAI's GPT-5.6 series, positioned between the flagship Sol tier and the cost-efficient Luna tier. It is suited for everyday coding, reasoning, and agentic...",
    "context_length": 1050000,
    "pricing": {
      "input": 1,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 2,
          "output": 9
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590857,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-sol-pro",
    "canonical_slug": "openai/gpt-5.6-sol-pro-20260709",
    "name": "OpenAI: GPT-5.6 Sol Pro",
    "raw_description": "GPT-5.6 Sol Pro is the same underlying model as [GPT-5.6 Sol](https://openrouter.ai/openai/gpt-5.6-sol), served with `reasoning.mode` set to `pro` for higher-quality responses on complex tasks.\n\nLearn more in OpenAI's docs: https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode",
    "context_length": 1050000,
    "pricing": {
      "input": 5,
      "output": 30,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 10,
          "output": 45
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590854,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-sol",
    "canonical_slug": "openai/gpt-5.6-sol-20260709",
    "name": "OpenAI: GPT-5.6 Sol",
    "raw_description": "GPT-5.6 Sol is the flagship model in OpenAI's GPT-5.6 series. It is suited for complex reasoning, coding, and agentic workflows, and is particularly strong at command-line and multi-step coding tasks...",
    "context_length": 1050000,
    "pricing": {
      "input": 5,
      "output": 30,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 10,
          "output": 45
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590850,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-4.5",
    "canonical_slug": "x-ai/grok-4.5-20260708",
    "name": "SpaceXAI: Grok 4.5",
    "raw_description": "Grok 4.5 is a model from SpaceXAI with frontier performance on coding, knowledge work, and STEM.",
    "context_length": 500000,
    "pricing": {
      "input": 2,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 4,
          "output": 12
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1783523154,
    "expiration_date": null,
    "model_author": "xAI",
    "reasoning_declared": true
  },
  {
    "id": "~x-ai/grok-latest",
    "canonical_slug": "~x-ai/grok-latest",
    "name": "xAI: Grok Latest",
    "raw_description": "This model always redirects to the latest Grok model from xAI.",
    "context_length": 500000,
    "pricing": {
      "input": 2,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 4,
          "output": 12
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1783519360,
    "expiration_date": null,
    "model_author": "xAI",
    "reasoning_declared": true
  },
  {
    "id": "aion-labs/aion-3.0-mini",
    "canonical_slug": "aion-labs/aion-3.0-mini-20260707",
    "name": "AionLabs: Aion-3.0-Mini",
    "raw_description": "Aion-3.0 Mini is a multi-model roleplaying and storytelling system from AionLabs, built on the DeepSeek family of models. It uses a collaborative generation process in which multiple specialized models each...",
    "context_length": 131072,
    "pricing": {
      "input": 0.7,
      "output": 1.4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1783443096,
    "expiration_date": null,
    "model_author": "AionLabs",
    "reasoning_declared": true
  },
  {
    "id": "aion-labs/aion-3.0",
    "canonical_slug": "aion-labs/aion-3.0-20260707",
    "name": "AionLabs: Aion-3.0",
    "raw_description": "Aion-3.0 is a multi-model roleplaying and storytelling system from AionLabs, built on the GLM family of models. It uses a collaborative generation process in which multiple specialized models each contribute...",
    "context_length": 131072,
    "pricing": {
      "input": 3,
      "output": 6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1783443095,
    "expiration_date": null,
    "model_author": "AionLabs",
    "reasoning_declared": true
  },
  {
    "id": "tencent/hy3",
    "canonical_slug": "tencent/hy3-20260706",
    "name": "Tencent: Hy3",
    "raw_description": "Hy3 is a 295B-parameter Mixture-of-Experts model from Tencent (21B active, 192 experts with top-8 routing) built for reasoning, agentic workflows, and real-world production use. It supports a configurable reasoning effort:...",
    "context_length": 262144,
    "pricing": {
      "input": 0.13199999999999998,
      "output": 0.5279999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_completion_tokens",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1783344048,
    "expiration_date": null,
    "model_author": "Tencent",
    "reasoning_declared": true
  },
  {
    "id": "tencent/hy3:free",
    "canonical_slug": "tencent/hy3-20260706",
    "name": "Tencent: Hy3 (free)",
    "raw_description": "Hy3 is a 295B-parameter Mixture-of-Experts model from Tencent (21B active, 192 experts with top-8 routing) built for reasoning, agentic workflows, and real-world production use. It supports a configurable reasoning effort:...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1783344048,
    "expiration_date": 1784592000,
    "model_author": "Tencent"
  },
  {
    "id": "poolside/laguna-xs-2.1",
    "canonical_slug": "poolside/laguna-xs-2.1-20260625",
    "name": "Poolside: Laguna XS 2.1",
    "raw_description": "Laguna XS 2.1 is the latest coding agent model in the 33B-A3B category from [Poolside](https://poolside.ai/) and a step forward from their Laguna XS.2 model (released in April 2026). It combines...",
    "context_length": 262144,
    "pricing": {
      "input": 0.06,
      "output": 0.12
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1783002429,
    "expiration_date": null,
    "model_author": "Poolside",
    "reasoning_declared": true
  },
  {
    "id": "poolside/laguna-xs-2.1:free",
    "canonical_slug": "poolside/laguna-xs-2.1-20260625",
    "name": "Poolside: Laguna XS 2.1 (free)",
    "raw_description": "Laguna XS 2.1 is the latest coding agent model in the 33B-A3B category from [Poolside](https://poolside.ai/) and a step forward from their Laguna XS.2 model (released in April 2026). It combines...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1783002429,
    "expiration_date": null,
    "model_author": "Poolside",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-sonnet-5",
    "canonical_slug": "anthropic/claude-sonnet-5-20260630",
    "name": "Anthropic: Claude Sonnet 5",
    "raw_description": "Sonnet 5 is Anthropic's most capable Sonnet-class model, with frontier performance across coding, agents, and professional work. It supports adaptive thinking with selectable reasoning effort levels (low, medium, high, max,...",
    "context_length": 1000000,
    "pricing": {
      "input": 2,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1782843083,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.1-flash-lite-image",
    "canonical_slug": "google/gemini-3.1-flash-lite-image-20260630",
    "name": "Google: Nano Banana 2 Lite (Gemini 3.1 Flash Lite Image)",
    "raw_description": "Nano Banana 2 Lite (Gemini 3.1 Flash Lite Image) is Google's fastest, most cost-efficient Gemini image model, built for high-velocity developer pipelines and rapid-fire visual exploration. It delivers text-to-image generation...",
    "context_length": 65536,
    "pricing": {
      "input": 0.25,
      "output": 1.5
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1782837225,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "nex-agi/nex-n2-mini",
    "canonical_slug": "nex-agi/nex-n2-mini",
    "name": "Nex AGI: Nex-N2-Mini",
    "raw_description": "Nex-N2-Mini is an open-source agentic mixture-of-experts model from Nex AGI, the smaller sibling in the Nex-N2 series. It accepts text and image input and is built for coding, tool use,...",
    "context_length": 262144,
    "pricing": {
      "input": 0.024999999999999998,
      "output": 0.09999999999999999
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "reasoning",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1782312964,
    "expiration_date": null,
    "model_author": "Nex AGI",
    "reasoning_declared": true
  },
  {
    "id": "sakana/fugu-ultra",
    "canonical_slug": "sakana/fugu-ultra-20260615",
    "name": "Sakana: Fugu Ultra",
    "raw_description": "Fugu Ultra is the higher-performance model in Sakana AI's Fugu family. Rather than a single monolithic model, Fugu is a learned multi-agent orchestration system: a language model trained to route...",
    "context_length": 1000000,
    "pricing": {
      "input": 5,
      "output": 30,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 10,
          "output": 45
        }
      ]
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "reasoning",
      "reasoning_effort",
      "structured_outputs",
      "tool_choice",
      "tools",
      "web_search_options"
    ],
    "created": 1782276303,
    "expiration_date": null,
    "model_author": "Sakana",
    "reasoning_declared": true
  },
  {
    "id": "alibaba/happyhorse-1.1",
    "canonical_slug": "alibaba/happyhorse-1.1-20260624",
    "name": "Alibaba: HappyHorse 1.1",
    "raw_description": "HappyHorse 1.1 is a video generation model from Alibaba. It generates short videos from a text prompt, a single starting image, or a set of reference images, with output up...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1782269643,
    "expiration_date": null,
    "model_author": "Alibaba"
  },
  {
    "id": "openai/gpt-image-2",
    "canonical_slug": "openai/gpt-image-2",
    "name": "OpenAI: GPT Image 2",
    "raw_description": "OpenAI's latest image generation model. Supports high-fidelity image generation and editing via the dedicated Images API.",
    "context_length": 400000,
    "pricing": {
      "input": 8,
      "output": 8
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1782264714,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-image-1",
    "canonical_slug": "openai/gpt-image-1",
    "name": "OpenAI: GPT Image 1",
    "raw_description": "OpenAI's GPT Image 1 generates and edits images via the dedicated Images API. Features accurate text rendering, transparent backgrounds, and up to 16 reference images for edits.",
    "context_length": 400000,
    "pricing": {
      "input": 10,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1782264713,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-image-1-mini",
    "canonical_slug": "openai/gpt-image-1-mini",
    "name": "OpenAI: GPT Image 1 Mini",
    "raw_description": "A cost-efficient variant of GPT Image 1 for high-quality image generation at reduced latency and cost via OpenAI's dedicated Images API.",
    "context_length": 400000,
    "pricing": {
      "input": 2.5,
      "output": 2.5
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1782264713,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "alibaba/happyhorse-1.0",
    "canonical_slug": "alibaba/happyhorse-1.0-20260624",
    "name": "Alibaba: HappyHorse 1.0",
    "raw_description": "HappyHorse 1.0 is a video generation model from Alibaba. It generates short videos from a text prompt, a single starting image, or a set of reference images, with output up...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1782260324,
    "expiration_date": null,
    "model_author": "Alibaba"
  },
  {
    "id": "google/gemini-3.1-flash-image",
    "canonical_slug": "google/gemini-3.1-flash-image-20260528",
    "name": "Google: Nano Banana 2 (Gemini 3.1 Flash Image)",
    "raw_description": "Gemini 3.1 Flash Image, a.k.a. \"Nano Banana 2,\" is Google’s latest state of the art image generation and editing model, delivering Pro-level visual quality at Flash speed. It combines advanced...",
    "context_length": 131072,
    "pricing": {
      "input": 0.5,
      "output": 3
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1781754065,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3-pro-image",
    "canonical_slug": "google/gemini-3-pro-image-20260528",
    "name": "Google: Nano Banana Pro (Gemini 3 Pro Image)",
    "raw_description": "Nano Banana Pro is Google’s most advanced image-generation and editing model, built on Gemini 3 Pro. It extends the original Nano Banana with significantly improved multimodal reasoning, real-world grounding, and...",
    "context_length": 131072,
    "pricing": {
      "input": 2,
      "output": 12
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1781754054,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "cohere/north-mini-code:free",
    "canonical_slug": "cohere/north-mini-code-20260617",
    "name": "Cohere: North Mini Code (free)",
    "raw_description": "North Mini Code is Cohere's first agentic coding model and the debut of its North family. A sparse mixture-of-experts model with 30B total parameters and 3B active, it is optimized...",
    "context_length": 256000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Cohere",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1781723748,
    "expiration_date": null,
    "model_author": "Cohere",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-5.2",
    "canonical_slug": "z-ai/glm-5.2-20260616",
    "name": "Z.ai: GLM 5.2",
    "raw_description": "GLM 5.2 is a large-scale reasoning model from Z.ai. It supports text input and output with a 1M-token context window, and is suited for long-horizon agent workflows, project-level software engineering,...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.76,
      "output": 2.42
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "parallel_tool_calls",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1781631930,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "openrouter/fusion",
    "canonical_slug": "openrouter/fusion",
    "name": "OpenRouter: Fusion",
    "raw_description": "Fusion turns your prompt into a small multi-model deliberation. A panel of expert models (see below) analyzes your prompt in parallel with web search and web fetch enabled, then a...",
    "context_length": 1000000,
    "pricing": {
      "input": -1000000,
      "output": -1000000
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [],
    "created": 1781371647,
    "expiration_date": null,
    "model_author": "OpenRouter"
  },
  {
    "id": "moonshotai/kimi-k2.7-code",
    "canonical_slug": "moonshotai/kimi-k2.7-code-20260612",
    "name": "MoonshotAI: Kimi K2.7 Code",
    "raw_description": "MoonshotAI: Kimi K2.7 Code is a coding-focused model in Moonshot AI's Kimi K2 family, built to complete end-to-end programming tasks reliably over long contexts. It uses a native multimodal mixture-of-experts...",
    "context_length": 262144,
    "pricing": {
      "input": 0.71,
      "output": 3.5
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "parallel_tool_calls",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1781266361,
    "expiration_date": null,
    "model_author": "MoonshotAI",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/llama-nemotron-rerank-vl-1b-v2:free",
    "canonical_slug": "nvidia/llama-nemotron-rerank-vl-1b-v2",
    "name": "NVIDIA: Llama Nemotron Rerank VL 1B V2 (free)",
    "raw_description": "Llama Nemotron Rerank VL 1B V2 is a 1.7B multimodal reranking model from NVIDIA. It evaluates the relevance of document images and text against user queries, designed for vision RAG...",
    "context_length": 10240,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "rerank"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1781036054,
    "expiration_date": null,
    "model_author": "NVIDIA"
  },
  {
    "id": "~anthropic/claude-fable-latest",
    "canonical_slug": "~anthropic/claude-fable-latest",
    "name": "Anthropic: Claude Fable Latest",
    "raw_description": "This model always redirects to the latest model in the Claude Fable family.",
    "context_length": 1000000,
    "pricing": {
      "input": 10,
      "output": 50
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1781029944,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-fable-5",
    "canonical_slug": "anthropic/claude-5-fable-20260609",
    "name": "Anthropic: Claude Fable 5",
    "raw_description": "Claude Fable 5 is a Mythos-class model from Anthropic, built for autonomous knowledge work and coding. It supports text, image, and file inputs with text output, with reasoning support and...",
    "context_length": 1000000,
    "pricing": {
      "input": 10,
      "output": 50
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1781007515,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "nex-agi/nex-n2-pro",
    "canonical_slug": "nex-agi/nex-n2-pro",
    "name": "Nex AGI: Nex-N2-Pro",
    "raw_description": "Nex-N2-Pro is an agentic mixture-of-experts model from Nex AGI, with 17B active parameters out of 397B total. Built on the Qwen3.5 architecture, it accepts text and image input and produces...",
    "context_length": 262144,
    "pricing": {
      "input": 0.25,
      "output": 1
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1780937140,
    "expiration_date": null,
    "model_author": "Nex AGI",
    "reasoning_declared": true
  },
  {
    "id": "sourceful/riverflow-v2.5-pro",
    "canonical_slug": "sourceful/riverflow-v2.5-pro-20260605",
    "name": "Sourceful: Riverflow V2.5 Pro",
    "raw_description": "Riverflow V2.5 Pro is the most powerful variant of Sourceful's Riverflow 2.5 lineup, best for top-tier control and quality-sensitive outputs. The Riverflow 2.5 series is a unified text-to-image and image-to-image...",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "reasoning",
      "reasoning_effort"
    ],
    "created": 1780584991,
    "expiration_date": null,
    "model_author": "Sourceful",
    "reasoning_declared": true
  },
  {
    "id": "sourceful/riverflow-v2.5-pro:free",
    "canonical_slug": "sourceful/riverflow-v2.5-pro-20260605",
    "name": "Sourceful: Riverflow V2.5 Pro (free)",
    "raw_description": "Riverflow V2.5 Pro is the most powerful variant of Sourceful's Riverflow 2.5 lineup, best for top-tier control and quality-sensitive outputs. The Riverflow 2.5 series is a unified text-to-image and image-to-image...",
    "context_length": 8192,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "reasoning"
    ],
    "created": 1780584991,
    "expiration_date": null,
    "model_author": "Sourceful"
  },
  {
    "id": "sourceful/riverflow-v2.5-fast",
    "canonical_slug": "sourceful/riverflow-v2.5-fast-20260605",
    "name": "Sourceful: Riverflow V2.5 Fast",
    "raw_description": "Riverflow V2.5 Fast is the speed-optimized variant of Sourceful's Riverflow 2.5 lineup, best for production deployments and latency-critical workflows. The Riverflow 2.5 series is a unified text-to-image and image-to-image family...",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "reasoning",
      "reasoning_effort"
    ],
    "created": 1780584983,
    "expiration_date": null,
    "model_author": "Sourceful",
    "reasoning_declared": true
  },
  {
    "id": "sourceful/riverflow-v2.5-fast:free",
    "canonical_slug": "sourceful/riverflow-v2.5-fast-20260605",
    "name": "Sourceful: Riverflow V2.5 Fast (free)",
    "raw_description": "Riverflow V2.5 Fast is the speed-optimized variant of Sourceful's Riverflow 2.5 lineup, best for production deployments and latency-critical workflows. The Riverflow 2.5 series is a unified text-to-image and image-to-image family...",
    "context_length": 8192,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "reasoning"
    ],
    "created": 1780584983,
    "expiration_date": null,
    "model_author": "Sourceful"
  },
  {
    "id": "nvidia/nemotron-3.5-content-safety:free",
    "canonical_slug": "nvidia/nemotron-3.5-content-safety-20260604",
    "name": "NVIDIA: Nemotron 3.5 Content Safety (free)",
    "raw_description": "NVIDIA Nemotron 3.5 Content Safety is a compact 4B-parameter multimodal guardrail model from NVIDIA, fine-tuned from Google Gemma-3-4B. It moderates both inputs to and responses from LLMs and VLMs, accepting...",
    "context_length": 128000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1780581864,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3-ultra-550b-a55b",
    "canonical_slug": "nvidia/nemotron-3-ultra-550b-a55b-20260604",
    "name": "NVIDIA: Nemotron 3 Ultra",
    "raw_description": "NVIDIA Nemotron 3 Ultra is an open frontier-reasoning and orchestration model from NVIDIA, with 55B active parameters out of 550B total (MoE). Built on a hybrid Transformer-Mamba mixture-of-experts architecture, it...",
    "context_length": 512288,
    "pricing": {
      "input": 0.6,
      "output": 3.5999999999999996
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1780551208,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "canonical_slug": "nvidia/nemotron-3-ultra-550b-a55b-20260604",
    "name": "NVIDIA: Nemotron 3 Ultra (free)",
    "raw_description": "NVIDIA Nemotron 3 Ultra is an open frontier-reasoning and orchestration model from NVIDIA, with 55B active parameters out of 550B total (MoE). Built on a hybrid Transformer-Mamba mixture-of-experts architecture, it...",
    "context_length": 1000000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1780551208,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.7-plus",
    "canonical_slug": "qwen/qwen3.7-plus-20260602",
    "name": "Qwen: Qwen3.7 Plus",
    "raw_description": "Qwen3.7-Plus is a cost-effective model in Alibaba's Qwen3.7 series. It supports text and image input with text output, building on the series' text capabilities with a comprehensive upgrade to its...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.32,
      "output": 1.28,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 0.96,
          "output": 3.84
        }
      ]
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1780491783,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "microsoft/mai-voice-2",
    "canonical_slug": "microsoft/mai-voice-2",
    "name": "Microsoft: MAI-Voice-2",
    "raw_description": "MAI-Voice-2 is an expressive text-to-speech model from Microsoft. It is suited for conversational assistants, media narration, accessibility, education, and other long-form voice applications. It supports 15 languages across 18 locales,...",
    "context_length": 0,
    "pricing": {
      "input": 22,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1780425097,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "microsoft/mai-transcribe-1.5",
    "canonical_slug": "microsoft/mai-transcribe-1.5",
    "name": "Microsoft: MAI-Transcribe 1.5",
    "raw_description": "MAI-Transcribe 1.5 is a multilingual speech-to-text model from Microsoft AI. It is suited for captions, call transcription, subtitling, accessibility, and other voice-enabled applications, with reliable transcription across 43 languages, diverse...",
    "context_length": 0,
    "pricing": {
      "input": 360000,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1780425095,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "microsoft/mai-image-2.5",
    "canonical_slug": "microsoft/mai-image-2.5",
    "name": "Microsoft: MAI-Image-2.5",
    "raw_description": "Microsoft's MAI-Image-2.5 is a high-quality image generation model available via Azure AI Foundry. It produces photorealistic and artistic images from text prompts with support for various aspect ratios.",
    "context_length": 4096,
    "pricing": {
      "input": 5,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "temperature"
    ],
    "created": 1780424896,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "minimax/minimax-m3",
    "canonical_slug": "minimax/minimax-m3-20260531",
    "name": "MiniMax: MiniMax M3",
    "raw_description": "MiniMax-M3 is a multimodal foundation model from MiniMax. It supports text, image, and video inputs with text output, a 1M-token context window, and is suited for long-horizon agentic work, coding,...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.3,
      "output": 1.2
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1780245374,
    "expiration_date": null,
    "model_author": "MiniMax",
    "reasoning_declared": true
  },
  {
    "id": "stepfun/step-3.7-flash",
    "canonical_slug": "stepfun/step-3.7-flash-20260528",
    "name": "StepFun: Step 3.7 Flash",
    "raw_description": "Step 3.7 Flash is StepFun's latest high-efficiency multimodal Mixture-of-Experts model. It pairs a 196B-parameter language backbone with a vision encoder for native image and video understanding, activating roughly 11B parameters...",
    "context_length": 262144,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 1.15
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1779985069,
    "expiration_date": null,
    "model_author": "StepFun",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.8-fast",
    "canonical_slug": "anthropic/claude-4.8-opus-fast-20260528",
    "name": "Anthropic: Claude Opus 4.8 (Fast)",
    "raw_description": "Fast-mode variant of [Opus 4.8](/anthropic/claude-opus-4.8) - identical capabilities with higher output speed at 2x pricing relative to regular Opus 4.8.\n\nLearn more in Anthropic's docs: https://platform.claude.com/docs/en/build-with-claude/fast-mode",
    "context_length": 1000000,
    "pricing": {
      "input": 10,
      "output": 50
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1779913703,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.8",
    "canonical_slug": "anthropic/claude-4.8-opus-20260528",
    "name": "Anthropic: Claude Opus 4.8",
    "raw_description": "Claude Opus 4.8 is Anthropic's most capable generally available model in the Opus family. It supports text, image, and file inputs with text output, with reasoning support and a 1M-token...",
    "context_length": 1000000,
    "pricing": {
      "input": 5,
      "output": 25
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1779905091,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/parakeet-tdt-0.6b-v3",
    "canonical_slug": "nvidia/parakeet-tdt-0.6b-v3",
    "name": "NVIDIA: Parakeet TDT 0.6B v3",
    "raw_description": "Parakeet TDT 0.6B v3 is NVIDIA's 600M-parameter multilingual speech-to-text model built on the FastConformer-TDT architecture. Trained on the Granary dataset (670,000+ hours of audio), it supports automatic language detection across...",
    "context_length": 0,
    "pricing": {
      "input": 1500,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1779848335,
    "expiration_date": null,
    "model_author": "NVIDIA"
  },
  {
    "id": "qwen/qwen3.7-max",
    "canonical_slug": "qwen/qwen3.7-max-20260520",
    "name": "Qwen: Qwen3.7 Max",
    "raw_description": "Qwen3.7-Max is the flagship model in Alibaba's Qwen3.7 series. It supports text input and output and is designed for agent-centric workloads, with particular strengths in coding, office and productivity tasks,...",
    "context_length": 1000000,
    "pricing": {
      "input": 1.475,
      "output": 4.425
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1779376861,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-build-0.1",
    "canonical_slug": "x-ai/grok-build-0.1-20260520",
    "name": "SpaceXAI: Grok Build 0.1",
    "raw_description": "Grok Build 0.1 is SpaceXAI’s fast coding model trained specifically for agentic software engineering workflows. It supports text and image inputs with text output, and is optimized for interactive coding...",
    "context_length": 256000,
    "pricing": {
      "input": 1,
      "output": 2,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2,
          "output": 4
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1779298123,
    "expiration_date": null,
    "model_author": "xAI",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-embedding-2",
    "canonical_slug": "google/gemini-embedding-2",
    "name": "Google: Gemini Embedding 2",
    "raw_description": "Gemini Embedding 2 is Google's first multimodal embedding model. We currently support mapping text and images into a unified vector space for semantic search and retrieval-augmented generation (RAG). It supports...",
    "context_length": 8192,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1779290135,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "google/gemini-3.5-flash",
    "canonical_slug": "google/gemini-3.5-flash-20260519",
    "name": "Google: Gemini 3.5 Flash",
    "raw_description": "Gemini 3.5 Flash is Google's high-efficiency multimodal model, bringing near-Pro level coding and reasoning at Flash-tier cost and speed. It is highly optimized for coding proficiency and parallel agentic execution...",
    "context_length": 1048576,
    "pricing": {
      "input": 1.5,
      "output": 9
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1779193800,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-imagine-video",
    "canonical_slug": "x-ai/grok-imagine-video-20260512",
    "name": "SpaceXAI: Grok Imagine Video",
    "raw_description": "Grok Imagine Video is SpaceXAI's fast, text-, image-, and reference-conditioned video generation model. It produces short videos (1–15 seconds, 24 fps) at 480p or 720p across seven aspect ratios -...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1779117586,
    "expiration_date": null,
    "model_author": "xAI"
  },
  {
    "id": "x-ai/grok-imagine-image-quality",
    "canonical_slug": "x-ai/grok-imagine-image-quality-20260512",
    "name": "SpaceXAI: Grok Imagine Image Quality",
    "raw_description": "Grok Imagine Image Quality is SpaceXAI's fast, high-fidelity image generation and editing model. It accepts text prompts and optional reference images, producing photorealistic outputs at 1K or 2K across a...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1779117584,
    "expiration_date": null,
    "model_author": "xAI"
  },
  {
    "id": "mistralai/voxtral-mini-transcribe",
    "canonical_slug": "mistralai/voxtral-mini-transcribe-2602",
    "name": "Mistral: Voxtral Mini Transcribe",
    "raw_description": "Voxtral Mini Transcribe is Mistral's speech-to-text model, derived from the Voxtral Mini family. It accepts audio input and returns transcribed text via the standard transcription API. Suited for transcribing meetings,...",
    "context_length": 0,
    "pricing": {
      "input": 3000,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1778877024,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "x-ai/grok-voice-tts-1.0",
    "canonical_slug": "x-ai/grok-voice-tts-1.0",
    "name": "SpaceXAI: Grok Voice TTS 1.0",
    "raw_description": "Grok Voice TTS 1.0 is a text-to-speech model from SpaceXAI. It converts text into spoken audio across 20+ languages with automatic language detection, and offers five built-in voices (Eve, Ara,...",
    "context_length": 15000,
    "pricing": {
      "input": 15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1778805456,
    "expiration_date": null,
    "model_author": "xAI"
  },
  {
    "id": "qwen/qwen3-asr-flash-2026-02-10",
    "canonical_slug": "qwen/qwen3-asr-flash-2026-02-10",
    "name": "Qwen: Qwen3 ASR Flash",
    "raw_description": "Qwen3-ASR-Flash is Alibaba's automatic speech recognition service, built on the Qwen3-Omni foundation and trained on tens of millions of hours of multimodal speech data. The model handles 11 languages —...",
    "context_length": 0,
    "pricing": {
      "input": 35,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1778732776,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "recraft/recraft-v4.1-pro-vector",
    "canonical_slug": "recraft/recraft-v4.1-pro-vector-20260514",
    "name": "Recraft: Recraft V4.1 Pro Vector",
    "raw_description": "Recraft V4.1 Pro Vector is the vector (SVG) variant of Recraft V4.1 Pro, tuned for high aesthetics. It supports text and image inputs and produces higher-resolution SVG image output across...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707395,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4.1-vector",
    "canonical_slug": "recraft/recraft-v4.1-vector-20260514",
    "name": "Recraft: Recraft V4.1 Vector",
    "raw_description": "Recraft V4.1 Vector is the vector (SVG) variant of Recraft V4.1, tuned for high aesthetics. It supports text and image inputs and produces SVG image output across multiple aspect ratios,...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707392,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4.1-utility-pro",
    "canonical_slug": "recraft/recraft-v4.1-utility-pro-20260514",
    "name": "Recraft: Recraft V4.1 Utility Pro",
    "raw_description": "Recraft V4.1 Utility Pro is a general-purpose image generation model from Recraft. It supports text and image inputs with image output at ~2K resolution across multiple aspect ratios — double...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707389,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4.1-utility",
    "canonical_slug": "recraft/recraft-v4.1-utility-20260514",
    "name": "Recraft: Recraft V4.1 Utility",
    "raw_description": "Recraft V4.1 Utility is a general-purpose image generation model from Recraft. It supports text and image inputs with image output at ~1K resolution across multiple aspect ratios, with typical generation...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707387,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4.1-pro",
    "canonical_slug": "recraft/recraft-v4.1-pro-20260514",
    "name": "Recraft: Recraft V4.1 Pro",
    "raw_description": "Recraft V4.1 Pro is an image generation model from Recraft tuned for high aesthetics. It supports text and image inputs with image output at ~2K resolution across multiple aspect ratios...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707384,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4.1",
    "canonical_slug": "recraft/recraft-v4.1-20260514",
    "name": "Recraft: Recraft V4.1",
    "raw_description": "Recraft V4.1 is an image generation model from Recraft tuned for high aesthetics. It supports text and image inputs with image output at ~1K resolution across multiple aspect ratios, with...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707381,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4-pro-vector",
    "canonical_slug": "recraft/recraft-v4-pro-vector-20260514",
    "name": "Recraft: Recraft V4 Pro Vector",
    "raw_description": "Recraft V4 Pro Vector is the vector (SVG) variant of Recraft V4 Pro. It supports text and image inputs and produces vector image output across multiple aspect ratios at the...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707334,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4-vector",
    "canonical_slug": "recraft/recraft-v4-vector-20260514",
    "name": "Recraft: Recraft V4 Vector",
    "raw_description": "Recraft V4 Vector is the vector (SVG) variant of Recraft V4. It supports text and image inputs and produces vector image output across multiple aspect ratios. Compared to the raster...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778707333,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "anthropic/claude-opus-4.7-fast",
    "canonical_slug": "anthropic/claude-4.7-opus-fast-20260512",
    "name": "Anthropic: Claude Opus 4.7 (Fast)",
    "raw_description": "Fast-mode variant of [Opus 4.7](/anthropic/claude-opus-4.7) - identical capabilities with higher output speed at premium 6x pricing.\n\nLearn more in Anthropic's docs: https://platform.claude.com/docs/en/build-with-claude/fast-mode",
    "context_length": 1000000,
    "pricing": {
      "input": 30,
      "output": 150
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1778613011,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "perceptron/perceptron-mk1",
    "canonical_slug": "perceptron/perceptron-mk1-20260512",
    "name": "Perceptron: Perceptron Mk1",
    "raw_description": "Perceptron Mk1 (Mark One) is Perceptron's highest-quality vision-language model for video and embodied reasoning.** It accepts image and video inputs paired with natural language queries, and produces detailed visual understanding...",
    "context_length": 32768,
    "pricing": {
      "input": 0.15,
      "output": 1.5
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1778597029,
    "expiration_date": null,
    "model_author": "Perceptron",
    "reasoning_declared": true
  },
  {
    "id": "inclusionai/ring-2.6-1t",
    "canonical_slug": "inclusionai/ring-2.6-1t-20260508",
    "name": "inclusionAI: Ring-2.6-1T",
    "raw_description": "Ring-2.6-1T is a 1T-parameter-scale thinking model with 63B active parameters, built for real-world agent workflows that require both strong capability and operational efficiency. It is optimized for coding agents, tool...",
    "context_length": 262144,
    "pricing": {
      "input": 0.075,
      "output": 0.625
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1778247440,
    "expiration_date": null,
    "model_author": "InclusionAI",
    "reasoning_declared": true
  },
  {
    "id": "recraft/recraft-v4-pro",
    "canonical_slug": "recraft/recraft-v4-pro-20260413",
    "name": "Recraft: Recraft V4 Pro",
    "raw_description": "Recraft V4 Pro is an image generation model from Recraft. It supports text and image inputs with image output at ~2K resolution across multiple aspect ratios, double the resolution of...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778185441,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v4",
    "canonical_slug": "recraft/recraft-v4-20260413",
    "name": "Recraft: Recraft V4",
    "raw_description": "Recraft V4 is an image generation model from Recraft. It supports text and image inputs with image output at ~1K resolution across multiple aspect ratios. It delivers stronger compositional judgment,...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778185437,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "recraft/recraft-v3",
    "canonical_slug": "recraft/recraft-v3-20260413",
    "name": "Recraft: Recraft V3",
    "raw_description": "Recraft V3 is an image generation model from Recraft. It supports text and image inputs with image output at ~1K resolution across multiple aspect ratios. Supports the following `image_config` parameters:...",
    "context_length": 65536,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1778185433,
    "expiration_date": null,
    "model_author": "Recraft"
  },
  {
    "id": "google/gemini-3.1-flash-lite",
    "canonical_slug": "google/gemini-3.1-flash-lite-20260507",
    "name": "Google: Gemini 3.1 Flash Lite",
    "raw_description": "Gemini 3.1 Flash Lite is Google’s GA high-efficiency multimodal model optimized for low-latency, high-volume workloads. It supports text, image, video, audio, and PDF inputs, and is designed for lightweight agentic...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.25,
      "output": 1.5
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1778168828,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-chat-latest",
    "canonical_slug": "openai/gpt-chat-latest-20260505",
    "name": "OpenAI: GPT Chat Latest",
    "raw_description": "GPT Chat Latest points to OpenAI's stable API alias `chat-latest` that always resolves to the latest Instant chat model used in ChatGPT. As OpenAI rolls out new Instant model updates...",
    "context_length": 400000,
    "pricing": {
      "input": 5,
      "output": 30
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1778000212,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "google/chirp-3",
    "canonical_slug": "google/chirp-3",
    "name": "Google: Chirp 3",
    "raw_description": "Chirp 3 is Google's latest multilingual speech-to-text model. It offers enhanced transcription accuracy across 24 GA languages and 77+ preview languages, with support for automatic language detection, automatic punctuation, and...",
    "context_length": 0,
    "pricing": {
      "input": 16000,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1777997783,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "openai/gpt-4o-mini-transcribe",
    "canonical_slug": "openai/gpt-4o-mini-transcribe",
    "name": "OpenAI: GPT-4o Mini Transcribe",
    "raw_description": "GPT-4o Mini Transcribe is OpenAI's smaller, cost-efficient speech-to-text model built on GPT-4o Mini audio capabilities. It's priced per token (input and output), making it suitable for high-volume transcription workflows that...",
    "context_length": 128000,
    "pricing": {
      "input": 1.25,
      "output": 5
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777658151,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/whisper-large-v3",
    "canonical_slug": "openai/whisper-large-v3",
    "name": "OpenAI: Whisper Large V3",
    "raw_description": "Whisper Large V3 is OpenAI's open-source automatic speech recognition model offering both audio transcription and translation. It supports 99+ languages and accepts common audio formats including mp3, mp4, wav, webm,...",
    "context_length": 0,
    "pricing": {
      "input": 7.5,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1777642266,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/whisper-large-v3-turbo",
    "canonical_slug": "openai/whisper-large-v3-turbo",
    "name": "OpenAI: Whisper Large V3 Turbo",
    "raw_description": "Whisper Large V3 Turbo is an optimized version of OpenAI's Whisper Large V3 speech recognition model, designed for speed and cost efficiency. It supports transcription across 99+ languages with a...",
    "context_length": 0,
    "pricing": {
      "input": 3.33,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1777642266,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "x-ai/grok-4.3",
    "canonical_slug": "x-ai/grok-4.3-20260430",
    "name": "SpaceXAI: Grok 4.3",
    "raw_description": "Grok 4.3 is a reasoning model from SpaceXAI. It accepts text and image inputs with text output, and is suited for agentic workflows, instruction-following tasks, and applications requiring high factual...",
    "context_length": 1000000,
    "pricing": {
      "input": 1.25,
      "output": 2.5,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2.5,
          "output": 5
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777591821,
    "expiration_date": null,
    "model_author": "xAI",
    "reasoning_declared": true
  },
  {
    "id": "ibm-granite/granite-4.1-8b",
    "canonical_slug": "ibm-granite/granite-4.1-8b-20260429",
    "name": "IBM: Granite 4.1 8B",
    "raw_description": "Granite 4.1 8B is a dense, decoder-only 8-billion-parameter language model from IBM, part of the Granite 4.1 family. It supports a 131K-token context window and is designed for enterprise tasks...",
    "context_length": 131072,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.09999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777577071,
    "expiration_date": null,
    "model_author": "IBM"
  },
  {
    "id": "mistralai/mistral-medium-3-5",
    "canonical_slug": "mistralai/mistral-medium-3.5-20260430",
    "name": "Mistral: Mistral Medium 3.5",
    "raw_description": "Mistral Medium 3.5 is a dense 128B instruction-following model from Mistral AI. It supports text and image inputs with text output, and is designed for agentic workflows, coding, and complex...",
    "context_length": 262144,
    "pricing": {
      "input": 1.5,
      "output": 7.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1777570439,
    "expiration_date": null,
    "model_author": "Mistral AI",
    "reasoning_declared": true
  },
  {
    "id": "kwaivgi/kling-v3.0-pro",
    "canonical_slug": "kwaivgi/kling-v3.0-pro-20260429",
    "name": "Kling: Video v3.0 Pro",
    "raw_description": "Kling v3.0 Pro is Kuaishou's premium video generation model, offering higher visual quality than the Standard tier. It supports text-to-video and image-to-video workflows, with first-frame and last-frame control for precise...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1777496206,
    "expiration_date": null,
    "model_author": "Kling"
  },
  {
    "id": "kwaivgi/kling-v3.0-std",
    "canonical_slug": "kwaivgi/kling-v3.0-std-20260429",
    "name": "Kling: Video v3.0 Standard",
    "raw_description": "Kling v3.0 Standard is a video generation model from Kuaishou. It supports text-to-video and image-to-video workflows, with first-frame and last-frame control for guided scene composition. Clips range from 3 to...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1777496205,
    "expiration_date": null,
    "model_author": "Kling"
  },
  {
    "id": "openrouter/owl-alpha",
    "canonical_slug": "openrouter/owl-alpha",
    "name": "Owl Alpha",
    "raw_description": "Owl Alpha is a high-performance foundation model designed for agentic workloads. Natively supports tool use, and long-context tasks, with strong performance in code generation, automated workflows, and complex instruction execution....",
    "context_length": 1048756,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1777398589,
    "expiration_date": null,
    "model_author": "模镜"
  },
  {
    "id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "canonical_slug": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning-20260428",
    "name": "NVIDIA: Nemotron 3 Nano Omni (free)",
    "raw_description": "NVIDIA Nemotron™ 3 Nano Omni is a 30B-A3B open multimodal model designed to function as a perception and context sub-agent in enterprise agent systems. It accepts text, image, video, and...",
    "context_length": 256000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "audio",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1777393095,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "poolside/laguna-xs.2:free",
    "canonical_slug": "poolside/laguna-xs.2-20260421",
    "name": "Poolside: Laguna XS.2 (free)",
    "raw_description": "Laguna XS.2 is the second-generation model in the XS size class from [Poolside](https://poolside.ai), their efficient coding agent series. It combines tool calling and reasoning capabilities with a compact footprint, offering...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1777389604,
    "expiration_date": null,
    "model_author": "Poolside"
  },
  {
    "id": "poolside/laguna-m.1",
    "canonical_slug": "poolside/laguna-m.1-20260312",
    "name": "Poolside: Laguna M.1",
    "raw_description": "Laguna M.1 is the flagship coding agent model from [Poolside](https://poolside.ai/), optimized for complex software engineering tasks. Designed for agentic coding workflows, it supports tool calling and reasoning, with a 256K...",
    "context_length": 262144,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1777388504,
    "expiration_date": 1785196800,
    "model_author": "Poolside"
  },
  {
    "id": "poolside/laguna-m.1:free",
    "canonical_slug": "poolside/laguna-m.1-20260312",
    "name": "Poolside: Laguna M.1 (free)",
    "raw_description": "Laguna M.1 is the flagship coding agent model from [Poolside](https://poolside.ai/), optimized for complex software engineering tasks. Designed for agentic coding workflows, it supports tool calling and reasoning, with a 256K...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1777388504,
    "expiration_date": 1785196800,
    "model_author": "Poolside"
  },
  {
    "id": "openai/whisper-1",
    "canonical_slug": "openai/whisper-1",
    "name": "OpenAI: Whisper 1",
    "raw_description": "Whisper is OpenAI's open-source automatic speech recognition model, available via API as `whisper-1`. It supports transcription and translation across 50+ languages from audio files up to 25 MB. Accepts formats...",
    "context_length": 0,
    "pricing": {
      "input": 6000,
      "output": 0
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777332905,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4o-transcribe",
    "canonical_slug": "openai/gpt-4o-transcribe",
    "name": "OpenAI: GPT-4o Transcribe",
    "raw_description": "GPT-4o Transcribe is OpenAI's high-quality speech-to-text model built on GPT-4o audio capabilities. It's priced per token (input and output), making it suitable for workflows that benefit from token-level billing transparency.",
    "context_length": 128000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "audio"
    ],
    "output_modalities": [
      "transcription"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777332895,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "~anthropic/claude-haiku-latest",
    "canonical_slug": "~anthropic/claude-haiku-latest",
    "name": "Anthropic Claude Haiku Latest",
    "raw_description": "This model always redirects to the latest model in the Anthropic Claude Haiku family.",
    "context_length": 200000,
    "pricing": {
      "input": 1,
      "output": 5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1777318492,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "~openai/gpt-mini-latest",
    "canonical_slug": "~openai/gpt-mini-latest",
    "name": "OpenAI GPT Mini Latest",
    "raw_description": "This model always redirects to the latest model in the OpenAI GPT Mini family.",
    "context_length": 400000,
    "pricing": {
      "input": 0.75,
      "output": 4.5
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1777318471,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "~google/gemini-pro-latest",
    "canonical_slug": "~google/gemini-pro-latest",
    "name": "Google Gemini Pro Latest",
    "raw_description": "This model always redirects to the latest model in the Google Gemini Pro family.",
    "context_length": 1048576,
    "pricing": {
      "input": 2,
      "output": 12,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 4,
          "output": 18
        }
      ]
    },
    "input_modalities": [
      "audio",
      "file",
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1777318451,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "~moonshotai/kimi-latest",
    "canonical_slug": "~moonshotai/kimi-latest",
    "name": "MoonshotAI Kimi Latest",
    "raw_description": "This model always redirects to the latest model in the MoonshotAI Kimi family.",
    "context_length": 1048576,
    "pricing": {
      "input": 2.8,
      "output": 14
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777318428,
    "expiration_date": null,
    "model_author": "MoonshotAI",
    "reasoning_declared": true
  },
  {
    "id": "~google/gemini-flash-latest",
    "canonical_slug": "~google/gemini-flash-latest",
    "name": "Google Gemini Flash Latest",
    "raw_description": "This model always redirects to the latest model in the Google Gemini Flash family.",
    "context_length": 1048576,
    "pricing": {
      "input": 0.375,
      "output": 1.875
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1777318398,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "~anthropic/claude-sonnet-latest",
    "canonical_slug": "~anthropic/claude-sonnet-latest",
    "name": "Anthropic Claude Sonnet Latest",
    "raw_description": "This model always redirects to the latest model in the Anthropic Claude Sonnet family.",
    "context_length": 1000000,
    "pricing": {
      "input": 2,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1777318368,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "~openai/gpt-latest",
    "canonical_slug": "~openai/gpt-latest",
    "name": "OpenAI GPT Latest",
    "raw_description": "This model always redirects to the latest model in the OpenAI GPT family.",
    "context_length": 1050000,
    "pricing": {
      "input": 5,
      "output": 30,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 10,
          "output": 45
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1777318334,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-plus-20260420",
    "canonical_slug": "qwen/qwen3.5-plus-20260420",
    "name": "Qwen: Qwen3.5 Plus 2026-04-20",
    "raw_description": "Qwen3.5 Plus (April 2026) is a large-scale multimodal language model from Alibaba. It accepts text, image, and video input and produces text output, with a 1M token context window. This...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.3,
      "output": 1.7999999999999998,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 0.375,
          "output": 2.25
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777261368,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.6-flash",
    "canonical_slug": "qwen/qwen3.6-flash",
    "name": "Qwen: Qwen3.6 Flash",
    "raw_description": "Qwen3.6 Flash is a fast, efficient language model from Alibaba's Qwen 3.6 series. It supports text, image, and video input with a 1M token context window. Tiered pricing kicks in...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.1875,
      "output": 1.125,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 0.75,
          "output": 3
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777261362,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.6-35b-a3b",
    "canonical_slug": "qwen/qwen3.6-35b-a3b-20260415",
    "name": "Qwen: Qwen3.6 35B A3B",
    "raw_description": "Qwen3.6-35B-A3B is an open-weight multimodal model from Alibaba Cloud with 35 billion total parameters and 3 billion active parameters per token. It uses a hybrid sparse mixture-of-experts architecture combining Gated...",
    "context_length": 262144,
    "pricing": {
      "input": 0.14,
      "output": 1
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777260255,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.6-max-preview",
    "canonical_slug": "qwen/qwen3.6-max-preview-20260420",
    "name": "Qwen: Qwen3.6 Max Preview",
    "raw_description": "Qwen3.6-Max-Preview is a proprietary frontier model from Alibaba Cloud built on a sparse mixture-of-experts architecture with approximately 1 trillion total parameters. It is optimized for agentic coding, tool use, and...",
    "context_length": 262144,
    "pricing": {
      "input": 1.0270000000000001,
      "output": 6.162,
      "overrides": [
        {
          "min_prompt_tokens": 128000,
          "input": 1.5799999999999998,
          "output": 9.48
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777260242,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.6-27b",
    "canonical_slug": "qwen/qwen3.6-27b-20260422",
    "name": "Qwen: Qwen3.6 27B",
    "raw_description": "Qwen3.6 27B is a dense 27-billion-parameter language model from the Qwen Team at Alibaba, released in April 2026. It features hybrid multimodal capabilities — accepting text, image, and video inputs...",
    "context_length": 262144,
    "pricing": {
      "input": 0.3,
      "output": 2
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777255064,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.5-pro",
    "canonical_slug": "openai/gpt-5.5-pro-20260423",
    "name": "OpenAI: GPT-5.5 Pro",
    "raw_description": "GPT-5.5 Pro is OpenAI’s high-capability model optimized for deep reasoning and accuracy on complex, high-stakes workloads. It features a 1M+ token context window (922K input, 128K output) with support for...",
    "context_length": 1050000,
    "pricing": {
      "input": 30,
      "output": 180,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 60,
          "output": 270
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1777051896,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.5",
    "canonical_slug": "openai/gpt-5.5-20260423",
    "name": "OpenAI: GPT-5.5",
    "raw_description": "GPT-5.5 is OpenAI’s frontier model designed for complex professional workloads, building on GPT-5.4 with stronger reasoning, higher reliability, and improved token efficiency on hard tasks. It features a 1M+ token...",
    "context_length": 1050000,
    "pricing": {
      "input": 5,
      "output": 30,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 10,
          "output": 45
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1777051893,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-v4-pro",
    "canonical_slug": "deepseek/deepseek-v4-pro-20260423",
    "name": "DeepSeek: DeepSeek V4 Pro 0423",
    "raw_description": "DeepSeek V4 Pro is a large-scale Mixture-of-Experts model from DeepSeek with 1.6T total parameters and 49B activated parameters, supporting a 1M-token context window. It is designed for advanced reasoning, coding,...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.66,
      "output": 1.9800000000000002,
      "time_overrides": [
        {
          "utc_start": 1000,
          "utc_end": 100,
          "input": 0.66,
          "output": 1.9800000000000002
        },
        {
          "utc_start": 100,
          "utc_end": 400,
          "input": 1.32,
          "output": 3.9600000000000004
        },
        {
          "utc_start": 400,
          "utc_end": 600,
          "input": 0.66,
          "output": 1.9800000000000002
        },
        {
          "utc_start": 600,
          "utc_end": 1000,
          "input": 1.32,
          "output": 3.9600000000000004
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777000679,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-v4-flash",
    "canonical_slug": "deepseek/deepseek-v4-flash-20260423",
    "name": "DeepSeek: DeepSeek V4 Flash 0423",
    "raw_description": "DeepSeek V4 Flash is an efficiency-optimized Mixture-of-Experts model from DeepSeek with 284B total parameters and 13B activated parameters, supporting a 1M-token context window. It is designed for fast inference and...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.08386,
      "output": 0.16772
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1777000666,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.1-flash-tts-preview",
    "canonical_slug": "google/gemini-3.1-flash-tts-preview",
    "name": "Google: Gemini 3.1 Flash TTS Preview",
    "raw_description": "Gemini 3.1 Flash TTS Preview is a text-to-speech model from Google, and a substantial generational step up from Gemini 2.5 Flash TTS. It takes text input and produces audio output...",
    "context_length": 32768,
    "pricing": {
      "input": 1,
      "output": 20
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1776999308,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "google/veo-3.1-fast",
    "canonical_slug": "google/veo-3.1-fast-20260320",
    "name": "Google: Veo 3.1 Fast",
    "raw_description": "Google's mid-tier video generation model balancing speed and quality. Veo 3.1 Fast generates high-quality video from text or image prompts with native synchronized audio, offering faster turnaround than Veo 3.1...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1776994666,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "zyphra/zonos-v0.1-transformer",
    "canonical_slug": "zyphra/zonos-v0.1-transformer",
    "name": "Zyphra: Zonos v0.1 Transformer",
    "raw_description": "Zonos v0.1 Transformer is a text-to-speech model from Zyphra built on a pure transformer architecture. It offers the same American and British English voice coverage as the Hybrid variant, and...",
    "context_length": 4096,
    "pricing": {
      "input": 7,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1776983170,
    "expiration_date": null,
    "model_author": "Zyphra"
  },
  {
    "id": "zyphra/zonos-v0.1-hybrid",
    "canonical_slug": "zyphra/zonos-v0.1-hybrid",
    "name": "Zyphra: Zonos v0.1 Hybrid",
    "raw_description": "Zonos v0.1 Hybrid is a text-to-speech model from Zyphra built on a hybrid architecture. It produces English speech output with coverage across American and British accents in male and female...",
    "context_length": 4096,
    "pricing": {
      "input": 7,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1776983169,
    "expiration_date": null,
    "model_author": "Zyphra"
  },
  {
    "id": "canopylabs/orpheus-3b-0.1-ft",
    "canonical_slug": "canopylabs/orpheus-3b-0.1-ft",
    "name": "Canopy Labs: Orpheus 3B",
    "raw_description": "Orpheus 3B is an English text-to-speech model from Canopy Labs, fine-tuned for natural prosody and expressive delivery. It offers 7 preset voices and is suited for narration, voice assistants, and...",
    "context_length": 4096,
    "pricing": {
      "input": 7,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1776983168,
    "expiration_date": null,
    "model_author": "Canopy Labs"
  },
  {
    "id": "sesame/csm-1b",
    "canonical_slug": "sesame/csm-1b",
    "name": "Sesame: CSM 1B",
    "raw_description": "CSM 1B is a conversational speech model from Sesame. It accepts text input and produces English speech output, with voice options spanning conversational and read-speech styles. At 1B parameters, it...",
    "context_length": 4096,
    "pricing": {
      "input": 7,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1776983168,
    "expiration_date": null,
    "model_author": "Sesame"
  },
  {
    "id": "hexgrad/kokoro-82m",
    "canonical_slug": "hexgrad/kokoro-82m",
    "name": "hexgrad: Kokoro 82M",
    "raw_description": "Kokoro 82M is a lightweight, open-weight text-to-speech model from hexgrad. It converts text to speech across 8 languages (American and British English, Spanish, French, Hindi, Italian, Japanese, Portuguese, and Chinese)...",
    "context_length": 4096,
    "pricing": {
      "input": 0.62,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1776983167,
    "expiration_date": null,
    "model_author": "hexgrad"
  },
  {
    "id": "google/veo-3.1-lite",
    "canonical_slug": "google/veo-3.1-lite-20260331",
    "name": "Google: Veo 3.1 Lite",
    "raw_description": "Google's most cost-effective video generation model, designed for high-volume applications and rapid iteration. Veo 3.1 Lite generates 720p and 1080p video from text or image prompts with native synchronized audio...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1776978818,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "inclusionai/ling-2.6-1t",
    "canonical_slug": "inclusionai/ling-2.6-1t-20260423",
    "name": "inclusionAI: Ling-2.6-1T",
    "raw_description": "Ling-2.6-1T is an instant (instruct) model from inclusionAI and the company’s trillion-parameter flagship, designed for real-world agents that require fast execution and high efficiency at scale. It uses a “fast...",
    "context_length": 262144,
    "pricing": {
      "input": 0.075,
      "output": 0.625
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1776948238,
    "expiration_date": null,
    "model_author": "InclusionAI"
  },
  {
    "id": "tencent/hy3-preview",
    "canonical_slug": "tencent/hy3-preview-20260421",
    "name": "Tencent: Hy3 preview",
    "raw_description": "Hy3 preview is a high-efficiency Mixture-of-Experts model from Tencent designed for agentic workflows and production use. It supports configurable reasoning levels across disabled, low, and high modes, allowing it to...",
    "context_length": 262144,
    "pricing": {
      "input": 0.18,
      "output": 0.6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1776878150,
    "expiration_date": null,
    "model_author": "Tencent",
    "reasoning_declared": true
  },
  {
    "id": "xiaomi/mimo-v2.5-pro",
    "canonical_slug": "xiaomi/mimo-v2.5-pro-20260422",
    "name": "Xiaomi: MiMo-V2.5-Pro",
    "raw_description": "MiMo-V2.5-Pro is Xiaomi’s flagship model, delivering strong performance in general agentic capabilities, complex software engineering, and long-horizon tasks, with top rankings on benchmarks such as ClawEval, GDPVal, and SWE-bench Pro....",
    "context_length": 1050000,
    "pricing": {
      "input": 0.435,
      "output": 0.87
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1776874273,
    "expiration_date": null,
    "model_author": "Xiaomi",
    "reasoning_declared": true
  },
  {
    "id": "xiaomi/mimo-v2.5",
    "canonical_slug": "xiaomi/mimo-v2.5-20260422",
    "name": "Xiaomi: MiMo-V2.5",
    "raw_description": "MiMo-V2.5 is a native omnimodal model by Xiaomi. It delivers Pro-level agentic performance at roughly half the inference cost, while surpassing MiMo-V2-Omni in multimodal perception across image and video understanding...",
    "context_length": 1050000,
    "pricing": {
      "input": 0.14,
      "output": 0.28
    },
    "input_modalities": [
      "text",
      "audio",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1776874269,
    "expiration_date": null,
    "model_author": "Xiaomi",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4-image-2",
    "canonical_slug": "openai/gpt-5.4-image-2-20260421",
    "name": "OpenAI: GPT-5.4 Image 2",
    "raw_description": "[GPT-5.4](https://openrouter.ai/openai/gpt-5.4) Image 2 combines OpenAI's GPT-5.4 model with state-of-the-art image generation capabilities from GPT Image 2. It enables rich multimodal workflows, allowing users to seamlessly move between reasoning, coding, and...",
    "context_length": 272000,
    "pricing": {
      "input": 8,
      "output": 15
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "top_logprobs"
    ],
    "created": 1776797528,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "inclusionai/ling-2.6-flash",
    "canonical_slug": "inclusionai/ling-2.6-flash-20260421",
    "name": "inclusionAI: Ling-2.6-flash",
    "raw_description": "Ling-2.6-flash is an instant (instruct) model from inclusionAI with 104B total parameters and 7.4B active parameters, designed for real-world agents that require fast responses, strong execution, and high token efficiency....",
    "context_length": 262144,
    "pricing": {
      "input": 0.01,
      "output": 0.03
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1776795886,
    "expiration_date": null,
    "model_author": "InclusionAI"
  },
  {
    "id": "~anthropic/claude-opus-latest",
    "canonical_slug": "~anthropic/claude-opus-latest",
    "name": "Anthropic: Claude Opus Latest",
    "raw_description": "This model always redirects to the latest model in the Claude Opus family.",
    "context_length": 1000000,
    "pricing": {
      "input": 5,
      "output": 25
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1776795361,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "openrouter/pareto-code",
    "canonical_slug": "openrouter/pareto-code",
    "name": "Pareto Code Router",
    "raw_description": "The Pareto Router maintains a tiered shortlist of strong coding models, ranked by [Artificial Analysis](https://artificialanalysis.ai/) coding percentiles. Set min_coding_score between 0 and 1 on the [pareto-router plugin](https://openrouter.ai/docs/guides/routing/routers/pareto-router#the-min_coding_score-parameter) to control how...",
    "context_length": 2000000,
    "pricing": {
      "input": -1000000,
      "output": -1000000
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [],
    "created": 1776747900,
    "expiration_date": null,
    "model_author": "OpenRouter"
  },
  {
    "id": "kwaivgi/kling-video-o1",
    "canonical_slug": "kwaivgi/kling-video-o1-20260420",
    "name": "Kling: Video O1",
    "raw_description": "Kling Video O1 is a video generation model from Kuaishou. It supports text and image inputs with video output, enabling text-to-video and image-to-video workflows. It is suited for cinematic content...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1776704777,
    "expiration_date": null,
    "model_author": "Kling"
  },
  {
    "id": "minimax/hailuo-2.3",
    "canonical_slug": "minimax/hailuo-2.3-20260420",
    "name": "MiniMax: Hailuo 2.3",
    "raw_description": "Hailuo 2.3 is a video generation model from MiniMax. It accepts text prompts and reference images as input and generates video output, supporting both text-to-video and image-to-video workflows. It is...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1776702740,
    "expiration_date": null,
    "model_author": "MiniMax"
  },
  {
    "id": "moonshotai/kimi-k2.6",
    "canonical_slug": "moonshotai/kimi-k2.6-20260420",
    "name": "MoonshotAI: Kimi K2.6",
    "raw_description": "Kimi K2.6 is Moonshot AI's next-generation multimodal model, designed for long-horizon coding, coding-driven UI/UX generation, and multi-agent orchestration. It handles complex end-to-end coding tasks across Python, Rust, and Go, and...",
    "context_length": 262144,
    "pricing": {
      "input": 0.95,
      "output": 4
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "parallel_tool_calls",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1776699402,
    "expiration_date": null,
    "model_author": "MoonshotAI",
    "reasoning_declared": true
  },
  {
    "id": "moonshotai/kimi-k2.6:free",
    "canonical_slug": "moonshotai/kimi-k2.6-20260420",
    "name": "MoonshotAI: Kimi K2.6 (free)",
    "raw_description": "Kimi K2.6 is Moonshot AI's next-generation multimodal model, designed for long-horizon coding, coding-driven UI/UX generation, and multi-agent orchestration. It handles complex end-to-end coding tasks across Python, Rust, and Go, and...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "reasoning",
      "tool_choice",
      "tools"
    ],
    "created": 1776699402,
    "expiration_date": null,
    "model_author": "Moonshot AI"
  },
  {
    "id": "mistralai/voxtral-mini-tts-2603",
    "canonical_slug": "mistralai/voxtral-mini-tts-2603",
    "name": "Mistral: Voxtral Mini TTS",
    "raw_description": "Voxtral Mini TTS is Mistral's text-to-speech model featuring zero-shot voice cloning and multilingual support. It converts text input into natural-sounding audio output.",
    "context_length": 4096,
    "pricing": {
      "input": 16,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "speech"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1776571337,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "google/gemini-embedding-2-preview",
    "canonical_slug": "google/gemini-embedding-2-preview",
    "name": "Google: Gemini Embedding 2 Preview",
    "raw_description": "Gemini Embedding 2 Preview is Google's first multimodal embedding model. We currently support mapping text and images into a unified vector space for semantic search and retrieval-augmented generation (RAG). It...",
    "context_length": 8192,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1776436465,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "anthropic/claude-opus-4.7",
    "canonical_slug": "anthropic/claude-4.7-opus-20260416",
    "name": "Anthropic: Claude Opus 4.7",
    "raw_description": "Opus 4.7 is the next generation of Anthropic's Opus family, built for long-running, asynchronous agents. Building on the coding and agentic strengths of Opus 4.6, it delivers stronger performance on...",
    "context_length": 1000000,
    "pricing": {
      "input": 5,
      "output": 25
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1776351100,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "alibaba/wan-2.7",
    "canonical_slug": "alibaba/wan-2.7-20260414",
    "name": "Alibaba: Wan 2.7",
    "raw_description": "Wan 2.7 is a video generation model from Alibaba. It supports text-to-video, image-to-video with first and last frame control, and reference-to-video, where multiple reference images guide the style and content...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1776211362,
    "expiration_date": null,
    "model_author": "Alibaba"
  },
  {
    "id": "bytedance/seedance-2.0",
    "canonical_slug": "bytedance/seedance-2.0-20260414",
    "name": "ByteDance: Seedance 2.0",
    "raw_description": "Seedance 2.0 is a video generation model from ByteDance. It supports text-to-video, image-to-video with first and last frame control, and multimodal reference-to-video. It is particularly strong at preserving character consistency,...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "audio"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty"
    ],
    "created": 1776211362,
    "expiration_date": null,
    "model_author": "ByteDance"
  },
  {
    "id": "bytedance/seedance-2.0-fast",
    "canonical_slug": "bytedance/seedance-2.0-fast-20260414",
    "name": "ByteDance: Seedance 2.0 Fast",
    "raw_description": "Seedance 2.0 Fast is a video generation model from ByteDance. It supports text-to-video, image-to-video with first and last frame control, and multimodal reference-to-video. It prioritizes generation speed and lower cost...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "audio"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty"
    ],
    "created": 1776211362,
    "expiration_date": null,
    "model_author": "ByteDance"
  },
  {
    "id": "anthropic/claude-opus-4.6-fast",
    "canonical_slug": "anthropic/claude-4.6-opus-fast-20260407",
    "name": "Anthropic: Claude Opus 4.6 (Fast)",
    "raw_description": "Fast-mode variant of [Opus 4.6](/anthropic/claude-opus-4.6) - identical capabilities with higher output speed at premium 6x pricing.\n\nLearn more in Anthropic's docs: https://platform.claude.com/docs/en/build-with-claude/fast-mode",
    "context_length": 1000000,
    "pricing": {
      "input": 30,
      "output": 150
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p",
      "verbosity"
    ],
    "created": 1775592472,
    "expiration_date": null,
    "model_author": "Anthropic"
  },
  {
    "id": "z-ai/glm-5.1",
    "canonical_slug": "z-ai/glm-5.1-20260406",
    "name": "Z.ai: GLM 5.1",
    "raw_description": "GLM-5.1 delivers a major leap in coding capability, with particularly significant gains in handling long-horizon tasks. Unlike previous models built around minute-level interactions, GLM-5.1 can work independently and continuously on...",
    "context_length": 204800,
    "pricing": {
      "input": 0.966,
      "output": 3.036
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1775578025,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "cohere/rerank-4-pro",
    "canonical_slug": "cohere/rerank-4-pro",
    "name": "Cohere: Rerank 4 Pro",
    "raw_description": "Cohere's AI search foundation model for enhancing the relevance of information surfaced within search and RAG systems. Features a 32K context window, multilingual support across 100+ languages, no data pre-processing...",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "rerank"
    ],
    "tokenizer": "Cohere",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1775446247,
    "expiration_date": null,
    "model_author": "Cohere"
  },
  {
    "id": "cohere/rerank-4-fast",
    "canonical_slug": "cohere/rerank-4-fast",
    "name": "Cohere: Rerank 4 Fast",
    "raw_description": "Cohere's AI search foundation model for enhancing the relevance of information surfaced within search and RAG systems. Features a 32K context window, multilingual support across 100+ languages, no data pre-processing...",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "rerank"
    ],
    "tokenizer": "Cohere",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1775442269,
    "expiration_date": null,
    "model_author": "Cohere"
  },
  {
    "id": "cohere/rerank-v3.5",
    "canonical_slug": "cohere/rerank-v3.5",
    "name": "Cohere: Rerank v3.5",
    "raw_description": "Rerank v3.5 is designed to reorder search results for improved relevance. It supports multi-aspect and semi-structured data reranking over 100+ languages. Ideal for refining results from semantic or keyword search...",
    "context_length": 4096,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "rerank"
    ],
    "tokenizer": "Cohere",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1775416158,
    "expiration_date": null,
    "model_author": "Cohere"
  },
  {
    "id": "google/gemma-4-26b-a4b-it",
    "canonical_slug": "google/gemma-4-26b-a4b-it-20260403",
    "name": "Google: Gemma 4 26B A4B ",
    "raw_description": "Gemma 4 26B A4B IT is an instruction-tuned Mixture-of-Experts (MoE) model from Google DeepMind. Despite 25.2B total parameters, only 3.8B activate per token during inference — delivering near-31B quality at...",
    "context_length": 262144,
    "pricing": {
      "input": 0.07,
      "output": 0.33999999999999997
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemma",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1775227989,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemma-4-26b-a4b-it:free",
    "canonical_slug": "google/gemma-4-26b-a4b-it-20260403",
    "name": "Google: Gemma 4 26B A4B  (free)",
    "raw_description": "Gemma 4 26B A4B IT is an instruction-tuned Mixture-of-Experts (MoE) model from Google DeepMind. Despite 25.2B total parameters, only 3.8B activate per token during inference — delivering near-31B quality at...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemma",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1775227989,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemma-4-31b-it",
    "canonical_slug": "google/gemma-4-31b-it-20260402",
    "name": "Google: Gemma 4 31B",
    "raw_description": "Gemma 4 31B Instruct is Google DeepMind's 30.7B dense multimodal model supporting text and image input with text output. Features a 256K token context window, configurable thinking/reasoning mode, native function...",
    "context_length": 262144,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.33999999999999997
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemma",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1775148486,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemma-4-31b-it:free",
    "canonical_slug": "google/gemma-4-31b-it-20260402",
    "name": "Google: Gemma 4 31B (free)",
    "raw_description": "Gemma 4 31B Instruct is Google DeepMind's 30.7B dense multimodal model supporting text and image input with text output. Features a 256K token context window, configurable thinking/reasoning mode, native function...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemma",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1775148486,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.6-plus",
    "canonical_slug": "qwen/qwen3.6-plus-04-02",
    "name": "Qwen: Qwen3.6 Plus",
    "raw_description": "Qwen 3.6 Plus builds on a hybrid architecture that combines efficient linear attention with sparse mixture-of-experts routing, enabling strong scalability and high-performance inference. Compared to the 3.5 series, it delivers...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.325,
      "output": 1.95,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 1.3,
          "output": 3.9
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1775133557,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-5v-turbo",
    "canonical_slug": "z-ai/glm-5v-turbo-20260401",
    "name": "Z.ai: GLM 5V Turbo",
    "raw_description": "GLM-5V-Turbo is Z.ai’s first native multimodal agent foundation model, built for vision-based coding and agent-driven tasks. It natively handles image, video, and text inputs, excels at long-horizon planning, complex coding,...",
    "context_length": 202752,
    "pricing": {
      "input": 1.2,
      "output": 4
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1775061458,
    "expiration_date": 4070822400,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "arcee-ai/trinity-large-thinking",
    "canonical_slug": "arcee-ai/trinity-large-thinking",
    "name": "Arcee AI: Trinity Large Thinking",
    "raw_description": "Trinity Large Thinking is a powerful open source reasoning model from the team at Arcee AI. It shows strong performance in PinchBench, agentic workloads, and reasoning tasks. Launch video: https://youtu.be/Gc82AXLa0Rg?si=4RLn6WBz33qT--B7...",
    "context_length": 262144,
    "pricing": {
      "input": 0.22,
      "output": 0.85
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1775058318,
    "expiration_date": null,
    "model_author": "Arcee AI",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-4.20-multi-agent",
    "canonical_slug": "x-ai/grok-4.20-multi-agent-20260309",
    "name": "SpaceXAI: Grok 4.20 Multi-Agent",
    "raw_description": "Grok 4.20 Multi-Agent is a variant of SpaceXAI’s Grok 4.20 designed for collaborative, agent-based workflows. Multiple agents operate in parallel to conduct deep research, coordinate tool use, and synthesize information...",
    "context_length": 2000000,
    "pricing": {
      "input": 1.25,
      "output": 2.5,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2.5,
          "output": 5
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1774979158,
    "expiration_date": null,
    "model_author": "xAI",
    "reasoning_declared": true
  },
  {
    "id": "x-ai/grok-4.20",
    "canonical_slug": "x-ai/grok-4.20-20260309",
    "name": "SpaceXAI: Grok 4.20",
    "raw_description": "Grok 4.20 is a reasoning model from SpaceXAI with industry-leading speed and agentic tool calling capabilities. It combines the lowest hallucination rate on the market with strict prompt adherance, delivering...",
    "context_length": 2000000,
    "pricing": {
      "input": 1.25,
      "output": 2.5,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2.5,
          "output": 5
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Grok",
    "supported_parameters": [
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1774979019,
    "expiration_date": null,
    "model_author": "xAI",
    "reasoning_declared": true
  },
  {
    "id": "google/lyria-3-pro-preview",
    "canonical_slug": "google/lyria-3-pro-preview-20260330",
    "name": "Google: Lyria 3 Pro Preview",
    "raw_description": "Full-length songs are priced at $0.08 per song. Lyria 3 is Google's family of music generation models, available through the Gemini API. With Lyria 3, you can generate high-quality, 48kHz...",
    "context_length": 1048576,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text",
      "audio"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1774907286,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "google/lyria-3-clip-preview",
    "canonical_slug": "google/lyria-3-clip-preview-20260330",
    "name": "Google: Lyria 3 Clip Preview",
    "raw_description": "30 second duration clips are priced at $0.04 per clip. Lyria 3 is Google's family of music generation models, available through the Gemini API. With Lyria 3, you can generate...",
    "context_length": 1048576,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text",
      "audio"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1774907255,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "alibaba/wan-2.6",
    "canonical_slug": "alibaba/wan-2.6-20260327",
    "name": "Alibaba: Wan 2.6",
    "raw_description": "Alibaba's most advanced video generation model, supporting over 10 visual creation capabilities in a unified system. Wan 2.6 generates 1080p video at 24fps from text, images, reference videos, or audio,...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1774659190,
    "expiration_date": null,
    "model_author": "Alibaba"
  },
  {
    "id": "kwaipilot/kat-coder-pro-v2",
    "canonical_slug": "kwaipilot/kat-coder-pro-v2-20260327",
    "name": "Kwaipilot: KAT-Coder-Pro V2",
    "raw_description": "KAT-Coder-Pro V2 is the latest high-performance model in KwaiKAT’s KAT-Coder series, designed for complex enterprise-grade software engineering and SaaS integration. It builds on the agentic coding strengths of earlier versions,...",
    "context_length": 262144,
    "pricing": {
      "input": 0.3,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1774649310,
    "expiration_date": null,
    "model_author": "Kwaipilot"
  },
  {
    "id": "bytedance/seedance-1-5-pro",
    "canonical_slug": "bytedance/seedance-1-5-pro-20260320",
    "name": "ByteDance: Seedance 1.5 Pro",
    "raw_description": "ByteDance's next-generation audio-visual generation model with a 4.5B parameter Dual-Branch Diffusion Transformer architecture. Seedance 1.5 Pro generates video and audio simultaneously in a single unified pass — eliminating the timing...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty"
    ],
    "created": 1774277608,
    "expiration_date": null,
    "model_author": "ByteDance"
  },
  {
    "id": "openai/sora-2-pro",
    "canonical_slug": "openai/sora-2-pro-20260320",
    "name": "OpenAI: Sora 2 Pro",
    "raw_description": "OpenAI's flagship video generation model, delivering production-quality video with physics-accurate motion, synchronized audio, and world-state persistence across shots. Sora 2 Pro follows intricate multi-shot instructions while maintaining consistent spatial relationships...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "presence_penalty",
      "stop",
      "top_logprobs"
    ],
    "created": 1774277521,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "google/veo-3.1",
    "canonical_slug": "google/veo-3.1-20260320",
    "name": "Google: Veo 3.1",
    "raw_description": "Google's state-of-the-art video generation model, built for maximum visual fidelity in final production cuts. Veo 3.1 generates high-quality 1080p video from text or image prompts with native synchronized audio —...",
    "context_length": 0,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "video"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1774277148,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "rekaai/reka-edge",
    "canonical_slug": "rekaai/reka-edge-2603",
    "name": "Reka Edge",
    "raw_description": "Reka Edge is an extremely efficient 7B multimodal vision-language model that accepts image/video+text inputs and generates text outputs. This model is optimized specifically to deliver industry-leading performance in image understanding,...",
    "context_length": 16384,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.09999999999999999
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1774026965,
    "expiration_date": null,
    "model_author": "rekaai",
    "reasoning_declared": true
  },
  {
    "id": "minimax/minimax-m2.7",
    "canonical_slug": "minimax/minimax-m2.7-20260318",
    "name": "MiniMax: MiniMax M2.7",
    "raw_description": "MiniMax-M2.7 is a next-generation large language model designed for autonomous, real-world productivity and continuous improvement. Built to actively participate in its own evolution, M2.7 integrates advanced agentic capabilities through multi-agent...",
    "context_length": 204800,
    "pricing": {
      "input": 0.3,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1773836697,
    "expiration_date": null,
    "model_author": "MiniMax",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4-nano",
    "canonical_slug": "openai/gpt-5.4-nano-20260317",
    "name": "OpenAI: GPT-5.4 Nano",
    "raw_description": "GPT-5.4 nano is the most lightweight and cost-efficient variant of the GPT-5.4 family, optimized for speed-critical and high-volume tasks. It supports text and image inputs and is designed for low-latency...",
    "context_length": 400000,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 1.25
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1773748187,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4-mini",
    "canonical_slug": "openai/gpt-5.4-mini-20260317",
    "name": "OpenAI: GPT-5.4 Mini",
    "raw_description": "GPT-5.4 mini brings the core capabilities of GPT-5.4 to a faster, more efficient model optimized for high-throughput workloads. It supports text and image inputs with strong performance across reasoning, coding,...",
    "context_length": 400000,
    "pricing": {
      "input": 0.75,
      "output": 4.5
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1773748178,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/mistral-small-2603",
    "canonical_slug": "mistralai/mistral-small-2603",
    "name": "Mistral: Mistral Small 4",
    "raw_description": "Mistral Small 4 is the next major release in the Mistral Small family, unifying the capabilities of several flagship Mistral models into a single system. It combines strong reasoning from...",
    "context_length": 262144,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1773695685,
    "expiration_date": null,
    "model_author": "Mistral AI",
    "reasoning_declared": true
  },
  {
    "id": "perplexity/pplx-embed-v1-4b",
    "canonical_slug": "perplexity/pplx-embed-v1-4B",
    "name": "Perplexity: Embed V1 4B",
    "raw_description": "pplx-embed-v1 -4B is one of Perplexity's state-of-the-art text embedding models built for real-world, web-scale retrieval. pplx-embed-v1 is optimized for standard dense text retrieval with the 4B parameter model maximizing retrieval...",
    "context_length": 32000,
    "pricing": {
      "input": 0.03,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "temperature",
      "top_k",
      "top_p",
      "web_search_options"
    ],
    "created": 1773625372,
    "expiration_date": null,
    "model_author": "Perplexity"
  },
  {
    "id": "perplexity/pplx-embed-v1-0.6b",
    "canonical_slug": "perplexity/pplx-embed-v1-0.6B",
    "name": "Perplexity: Embed V1 0.6B",
    "raw_description": "pplx-embed-v1-0.6B is one of Perplexity's state-of-the-art text embedding models built for real-world, web-scale retrieval. pplx-embed-v1 is optimized for standard dense text retrieval with the 0.6B parameter model targeting lightweight, low-latency...",
    "context_length": 32000,
    "pricing": {
      "input": 0.004,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "temperature",
      "top_k",
      "top_p",
      "web_search_options"
    ],
    "created": 1773624868,
    "expiration_date": null,
    "model_author": "Perplexity"
  },
  {
    "id": "z-ai/glm-5-turbo",
    "canonical_slug": "z-ai/glm-5-turbo-20260315",
    "name": "Z.ai: GLM 5 Turbo",
    "raw_description": "GLM-5 Turbo is a new model from Z.ai designed for fast inference and strong performance in agent-driven environments such as OpenClaw scenarios. It is deeply optimized for real-world agent workflows...",
    "context_length": 202752,
    "pricing": {
      "input": 1.2,
      "output": 4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1773583573,
    "expiration_date": 4070822400,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3-super-120b-a12b",
    "canonical_slug": "nvidia/nemotron-3-super-120b-a12b-20230311",
    "name": "NVIDIA: Nemotron 3 Super",
    "raw_description": "NVIDIA Nemotron 3 Super is a 120B-parameter open hybrid MoE model, activating just 12B parameters for maximum compute efficiency and accuracy in complex multi-agent applications. Built on a hybrid Mamba-Transformer...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.08499999999999999,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1773245239,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3-super-120b-a12b:free",
    "canonical_slug": "nvidia/nemotron-3-super-120b-a12b-20230311",
    "name": "NVIDIA: Nemotron 3 Super (free)",
    "raw_description": "NVIDIA Nemotron 3 Super is a 120B-parameter open hybrid MoE model, activating just 12B parameters for maximum compute efficiency and accuracy in complex multi-agent applications. Built on a hybrid Mamba-Transformer...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1773245239,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "bytedance-seed/seed-2.0-lite",
    "canonical_slug": "bytedance-seed/seed-2.0-lite-20260309",
    "name": "ByteDance Seed: Seed-2.0-Lite",
    "raw_description": "Seed-2.0-Lite is a versatile, cost‑efficient enterprise workhorse that delivers strong multimodal and agent capabilities while offering noticeably lower latency, making it a practical default choice for most production workloads across...",
    "context_length": 262144,
    "pricing": {
      "input": 0.25,
      "output": 2,
      "overrides": [
        {
          "min_prompt_tokens": 128000,
          "input": 0.5,
          "output": 4
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1773157231,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-9b",
    "canonical_slug": "qwen/qwen3.5-9b-20260310",
    "name": "Qwen: Qwen3.5-9B",
    "raw_description": "Qwen3.5-9B is a multimodal foundation model from the Qwen3.5 family, designed to deliver strong reasoning, coding, and visual understanding in an efficient 9B-parameter architecture. It uses a unified vision-language design...",
    "context_length": 262144,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.15
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1773152396,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4-pro",
    "canonical_slug": "openai/gpt-5.4-pro-20260305",
    "name": "OpenAI: GPT-5.4 Pro",
    "raw_description": "GPT-5.4 Pro is OpenAI's most advanced model, building on GPT-5.4's unified architecture with enhanced reasoning capabilities for complex, high-stakes tasks. It features a 1M+ token context window (922K input, 128K...",
    "context_length": 1050000,
    "pricing": {
      "input": 30,
      "output": 180,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 60,
          "output": 270
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1772734366,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4",
    "canonical_slug": "openai/gpt-5.4-20260305",
    "name": "OpenAI: GPT-5.4",
    "raw_description": "GPT-5.4 is OpenAI’s latest frontier model, unifying the Codex and GPT lines into a single system. It features a 1M+ token context window (922K input, 128K output) with support for...",
    "context_length": 1050000,
    "pricing": {
      "input": 2.5,
      "output": 15,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 5,
          "output": 22.5
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1772734352,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "inception/mercury-2",
    "canonical_slug": "inception/mercury-2-20260304",
    "name": "Inception: Mercury 2",
    "raw_description": "Mercury 2 is an extremely fast reasoning LLM, and the first reasoning diffusion LLM (dLLM). Instead of generating tokens sequentially, Mercury 2 produces and refines multiple tokens in parallel, achieving...",
    "context_length": 128000,
    "pricing": {
      "input": 0.25,
      "output": 0.75
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1772636275,
    "expiration_date": null,
    "model_author": "Inception",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.3-chat",
    "canonical_slug": "openai/gpt-5.3-chat-20260303",
    "name": "OpenAI: GPT-5.3 Chat",
    "raw_description": "GPT-5.3 Chat is an update to ChatGPT's most-used model that makes everyday conversations smoother, more useful, and more directly helpful. It delivers more accurate answers with better contextualization and significantly...",
    "context_length": 128000,
    "pricing": {
      "input": 1.75,
      "output": 14
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1772564061,
    "expiration_date": 1786320000,
    "model_author": "OpenAI"
  },
  {
    "id": "google/gemini-3.1-flash-lite-preview",
    "canonical_slug": "google/gemini-3.1-flash-lite-preview-20260303",
    "name": "Google: Gemini 3.1 Flash Lite Preview",
    "raw_description": "Gemini 3.1 Flash Lite Preview is Google's high-efficiency model optimized for high-volume use cases. It outperforms Gemini 2.5 Flash Lite on overall quality and approaches Gemini 2.5 Flash performance across...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.25,
      "output": 1.5
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1772512673,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "bytedance-seed/seed-2.0-mini",
    "canonical_slug": "bytedance-seed/seed-2.0-mini-20260224",
    "name": "ByteDance Seed: Seed-2.0-Mini",
    "raw_description": "Seed-2.0-mini targets latency-sensitive, high-concurrency, and cost-sensitive scenarios, emphasizing fast response and flexible inference deployment. It delivers performance comparable to ByteDance-Seed-1.6, supports 256k context, four reasoning effort modes (minimal/low/medium/high), multimodal understanding,...",
    "context_length": 262144,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.39999999999999997,
      "overrides": [
        {
          "min_prompt_tokens": 128000,
          "input": 0.19999999999999998,
          "output": 0.7999999999999999
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1772131107,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.1-flash-image-preview",
    "canonical_slug": "google/gemini-3.1-flash-image-preview-20260226",
    "name": "Google: Nano Banana 2 (Gemini 3.1 Flash Image Preview)",
    "raw_description": "Gemini 3.1 Flash Image Preview, a.k.a. \"Nano Banana 2,\" is Google’s latest state of the art image generation and editing model, delivering Pro-level visual quality at Flash speed. It combines...",
    "context_length": 65536,
    "pricing": {
      "input": 0.5,
      "output": 3
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1772119558,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-35b-a3b",
    "canonical_slug": "qwen/qwen3.5-35b-a3b-20260224",
    "name": "Qwen: Qwen3.5-35B-A3B",
    "raw_description": "The Qwen3.5 Series 35B-A3B is a native vision-language model designed with a hybrid architecture that integrates linear attention mechanisms and a sparse mixture-of-experts model, achieving higher inference efficiency. Its overall...",
    "context_length": 262144,
    "pricing": {
      "input": 0.22499999999999998,
      "output": 1.7999999999999998
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1772053822,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-27b",
    "canonical_slug": "qwen/qwen3.5-27b-20260224",
    "name": "Qwen: Qwen3.5-27B",
    "raw_description": "The Qwen3.5 27B native vision-language Dense model incorporates a linear attention mechanism, delivering fast response times while balancing inference speed and performance. Its overall capabilities are comparable to those of...",
    "context_length": 262144,
    "pricing": {
      "input": 0.195,
      "output": 1.56
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1772053810,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-122b-a10b",
    "canonical_slug": "qwen/qwen3.5-122b-a10b-20260224",
    "name": "Qwen: Qwen3.5-122B-A10B",
    "raw_description": "The Qwen3.5 122B-A10B native vision-language model is built on a hybrid architecture that integrates a linear attention mechanism with a sparse mixture-of-experts model, achieving higher inference efficiency. In terms of...",
    "context_length": 262144,
    "pricing": {
      "input": 0.29,
      "output": 2.4
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1772053789,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-flash-02-23",
    "canonical_slug": "qwen/qwen3.5-flash-20260224",
    "name": "Qwen: Qwen3.5-Flash",
    "raw_description": "The Qwen3.5 native vision-language Flash models are built on a hybrid architecture that integrates a linear attention mechanism with a sparse mixture-of-experts model, achieving higher inference efficiency. Compared to the...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.065,
      "output": 0.26
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1772053776,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "liquid/lfm-2-24b-a2b",
    "canonical_slug": "liquid/lfm-2-24b-a2b-20260224",
    "name": "LiquidAI: LFM2-24B-A2B",
    "raw_description": "LFM2-24B-A2B is the largest model in the LFM2 family of hybrid architectures designed for efficient on-device deployment. Built as a 24B parameter Mixture-of-Experts model with only 2B active parameters per...",
    "context_length": 128000,
    "pricing": {
      "input": 0.03,
      "output": 0.12
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1772048711,
    "expiration_date": null,
    "model_author": "LiquidAI"
  },
  {
    "id": "google/gemini-3.1-pro-preview-customtools",
    "canonical_slug": "google/gemini-3.1-pro-preview-customtools-20260219",
    "name": "Google: Gemini 3.1 Pro Preview Custom Tools",
    "raw_description": "Gemini 3.1 Pro Preview Custom Tools is a variant of Gemini 3.1 Pro that improves tool selection behavior by preventing overuse of a general bash tool when more efficient third-party...",
    "context_length": 1048576,
    "pricing": {
      "input": 2,
      "output": 12,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 4,
          "output": 18
        }
      ]
    },
    "input_modalities": [
      "text",
      "audio",
      "image",
      "video",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1772045923,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/llama-nemotron-embed-vl-1b-v2:free",
    "canonical_slug": "nvidia/llama-nemotron-embed-vl-1b-v2-20260224",
    "name": "NVIDIA: Llama Nemotron Embed VL 1B V2 (free)",
    "raw_description": "The Llama Nemotron Embed VL 1B V2 embedding model is optimized for multimodal question-answering retrieval. The model can embed 'documents' in the form of image, text, or image and text...",
    "context_length": 131072,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1772045017,
    "expiration_date": null,
    "model_author": "NVIDIA"
  },
  {
    "id": "openai/gpt-5.3-codex",
    "canonical_slug": "openai/gpt-5.3-codex-20260224",
    "name": "OpenAI: GPT-5.3-Codex",
    "raw_description": "GPT-5.3-Codex is OpenAI’s most advanced agentic coding model, combining the frontier software engineering performance of GPT-5.2-Codex with the broader reasoning and professional knowledge capabilities of GPT-5.2. It achieves state-of-the-art results...",
    "context_length": 400000,
    "pricing": {
      "input": 1.75,
      "output": 14
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1771959164,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "aion-labs/aion-2.0",
    "canonical_slug": "aion-labs/aion-2.0-20260223",
    "name": "AionLabs: Aion-2.0",
    "raw_description": "Aion-2.0 is a variant of DeepSeek V3.2 optimized for immersive roleplaying and storytelling. It is particularly strong at introducing tension, crises, and conflict into stories, making narratives feel more engaging....",
    "context_length": 131072,
    "pricing": {
      "input": 0.7999999999999999,
      "output": 1.5999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1771881306,
    "expiration_date": null,
    "model_author": "AionLabs",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.1-pro-preview",
    "canonical_slug": "google/gemini-3.1-pro-preview-20260219",
    "name": "Google: Gemini 3.1 Pro Preview",
    "raw_description": "Gemini 3.1 Pro Preview is Google’s frontier reasoning model, delivering enhanced software engineering performance, improved agentic reliability, and more efficient token usage across complex workflows. Building on the multimodal foundation...",
    "context_length": 1048576,
    "pricing": {
      "input": 2,
      "output": 12,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 4,
          "output": 18
        }
      ]
    },
    "input_modalities": [
      "audio",
      "file",
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1771509627,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-sonnet-4.6",
    "canonical_slug": "anthropic/claude-4.6-sonnet-20260217",
    "name": "Anthropic: Claude Sonnet 4.6",
    "raw_description": "Sonnet 4.6 is Anthropic's most capable Sonnet-class model yet, with frontier performance across coding, agents, and professional work. It excels at iterative development, complex codebase navigation, end-to-end project management with...",
    "context_length": 1000000,
    "pricing": {
      "input": 3,
      "output": 15
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p",
      "verbosity"
    ],
    "created": 1771342990,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-plus-02-15",
    "canonical_slug": "qwen/qwen3.5-plus-20260216",
    "name": "Qwen: Qwen3.5 Plus 2026-02-15",
    "raw_description": "The Qwen3.5 native vision-language series Plus models are built on a hybrid architecture that integrates linear attention mechanisms with sparse mixture-of-experts models, achieving higher inference efficiency. In a variety of...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.26,
      "output": 1.56,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 0.325,
          "output": 1.95
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1771229416,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3.5-397b-a17b",
    "canonical_slug": "qwen/qwen3.5-397b-a17b-20260216",
    "name": "Qwen: Qwen3.5 397B A17B",
    "raw_description": "The Qwen3.5 series 397B-A17B native vision-language model is built on a hybrid architecture that integrates a linear attention mechanism with a sparse mixture-of-experts model, achieving higher inference efficiency. It delivers...",
    "context_length": 262144,
    "pricing": {
      "input": 0.39,
      "output": 2.34
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1771223018,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "minimax/minimax-m2.5",
    "canonical_slug": "minimax/minimax-m2.5-20260211",
    "name": "MiniMax: MiniMax M2.5",
    "raw_description": "MiniMax-M2.5 is a SOTA large language model designed for real-world productivity. Trained in a diverse range of complex real-world digital working environments, M2.5 builds upon the coding expertise of M2.1...",
    "context_length": 204800,
    "pricing": {
      "input": 0.22,
      "output": 0.8999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "parallel_tool_calls",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1770908502,
    "expiration_date": null,
    "model_author": "MiniMax",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-5",
    "canonical_slug": "z-ai/glm-5-20260211",
    "name": "Z.ai: GLM 5",
    "raw_description": "GLM-5 is Z.ai’s flagship open-source foundation model engineered for complex systems design and long-horizon agent workflows. Built for expert developers, it delivers production-grade performance on large-scale programming tasks, rivaling leading...",
    "context_length": 204800,
    "pricing": {
      "input": 0.6,
      "output": 1.92
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1770829182,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-max-thinking",
    "canonical_slug": "qwen/qwen3-max-thinking-20260123",
    "name": "Qwen: Qwen3 Max Thinking",
    "raw_description": "Qwen3-Max-Thinking is the flagship reasoning model in the Qwen3 series, designed for high-stakes cognitive tasks that require deep, multi-step reasoning. By significantly scaling model capacity and reinforcement learning compute, it...",
    "context_length": 262144,
    "pricing": {
      "input": 0.78,
      "output": 3.9,
      "overrides": [
        {
          "min_prompt_tokens": 32000,
          "input": 1.56,
          "output": 7.8
        },
        {
          "min_prompt_tokens": 128000,
          "input": 1.95,
          "output": 9.75
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1770671901,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.6",
    "canonical_slug": "anthropic/claude-4.6-opus-20260205",
    "name": "Anthropic: Claude Opus 4.6",
    "raw_description": "Opus 4.6 is Anthropic’s strongest model for coding and long-running professional tasks. It is built for agents that operate across entire workflows rather than single prompts, making it especially effective...",
    "context_length": 1000000,
    "pricing": {
      "input": 5,
      "output": 25
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p",
      "verbosity"
    ],
    "created": 1770219050,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-coder-next",
    "canonical_slug": "qwen/qwen3-coder-next-2025-02-03",
    "name": "Qwen: Qwen3 Coder Next",
    "raw_description": "Qwen3-Coder-Next is an open-weight causal language model optimized for coding agents and local development workflows. It uses a sparse MoE design with 80B total parameters and only 3B activated per...",
    "context_length": 262144,
    "pricing": {
      "input": 0.12,
      "output": 0.7999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1770164101,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "sourceful/riverflow-v2-pro",
    "canonical_slug": "sourceful/riverflow-v2-pro-20260130",
    "name": "Sourceful: Riverflow V2 Pro",
    "raw_description": "Riverflow V2 Pro is the most powerful variant of Sourceful's Riverflow 2.0 lineup, best for top-tier control and perfect text rendering. The Riverflow 2.0 series represents SOTA performance on image...",
    "context_length": 8192,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1770051427,
    "expiration_date": null,
    "model_author": "Sourceful"
  },
  {
    "id": "sourceful/riverflow-v2-fast",
    "canonical_slug": "sourceful/riverflow-v2-fast-20260130",
    "name": "Sourceful: Riverflow V2 Fast",
    "raw_description": "Riverflow V2 Fast is the fastest variant of Sourceful's Riverflow 2.0 lineup, best for production deployments and latency-critical workflows. The Riverflow 2.0 series represents SOTA performance on image generation and...",
    "context_length": 8192,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1770051423,
    "expiration_date": null,
    "model_author": "Sourceful"
  },
  {
    "id": "openrouter/free",
    "canonical_slug": "openrouter/free",
    "name": "Free Models Router",
    "raw_description": "The simplest way to get free inference. openrouter/free is a router that selects free models at random from the models available on OpenRouter. The router smartly filters for models that...",
    "context_length": 200000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1769917427,
    "expiration_date": null,
    "model_author": "OpenRouter"
  },
  {
    "id": "stepfun/step-3.5-flash",
    "canonical_slug": "stepfun/step-3.5-flash",
    "name": "StepFun: Step 3.5 Flash",
    "raw_description": "Step 3.5 Flash is StepFun's most capable open-source foundation model. Built on a sparse Mixture of Experts (MoE) architecture, it selectively activates only 11B of its 196B parameters per token....",
    "context_length": 262144,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1769728337,
    "expiration_date": null,
    "model_author": "StepFun",
    "reasoning_declared": true
  },
  {
    "id": "moonshotai/kimi-k2.5",
    "canonical_slug": "moonshotai/kimi-k2.5-0127",
    "name": "MoonshotAI: Kimi K2.5",
    "raw_description": "Kimi K2.5 is Moonshot AI's native multimodal model, delivering state-of-the-art visual coding capability and a self-directed agent swarm paradigm. Built on Kimi K2 with continued pretraining over approximately 15T mixed...",
    "context_length": 262144,
    "pricing": {
      "input": 0.5700000000000001,
      "output": 2.8499999999999996
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1769487076,
    "expiration_date": null,
    "model_author": "MoonshotAI",
    "reasoning_declared": true
  },
  {
    "id": "upstage/solar-pro-3",
    "canonical_slug": "upstage/solar-pro-3",
    "name": "Upstage: Solar Pro 3",
    "raw_description": "Solar Pro 3 is Upstage's powerful Mixture-of-Experts (MoE) language model. With 102B total parameters and 12B active parameters per forward pass, it delivers exceptional performance while maintaining computational efficiency. Optimized...",
    "context_length": 131072,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1769481200,
    "expiration_date": null,
    "model_author": "Upstage",
    "reasoning_declared": true
  },
  {
    "id": "minimax/minimax-m2-her",
    "canonical_slug": "minimax/minimax-m2-her-20260123",
    "name": "MiniMax: MiniMax M2-her",
    "raw_description": "MiniMax M2-her is a dialogue-first large language model built for immersive roleplay, character-driven chat, and expressive multi-turn conversations. Designed to stay consistent in tone and personality, it supports rich message...",
    "context_length": 65536,
    "pricing": {
      "input": 0.3,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1769177239,
    "expiration_date": null,
    "model_author": "MiniMax"
  },
  {
    "id": "writer/palmyra-x5",
    "canonical_slug": "writer/palmyra-x5-20250428",
    "name": "Writer: Palmyra X5",
    "raw_description": "Palmyra X5 is Writer's most advanced model, purpose-built for building and scaling AI agents across the enterprise. It delivers industry-leading speed and efficiency on context windows up to 1 million...",
    "context_length": 1040000,
    "pricing": {
      "input": 0.6,
      "output": 6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1769003823,
    "expiration_date": null,
    "model_author": "Writer"
  },
  {
    "id": "liquid/lfm-2.5-1.2b-thinking:free",
    "canonical_slug": "liquid/lfm-2.5-1.2b-thinking-20260120",
    "name": "LiquidAI: LFM2.5-1.2B-Thinking (free)",
    "raw_description": "LFM2.5-1.2B-Thinking is a lightweight reasoning-focused model optimized for agentic tasks, data extraction, and RAG—while still running comfortably on edge devices. It supports long context (up to 32K tokens) and is...",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1768927527,
    "expiration_date": null,
    "model_author": "LiquidAI"
  },
  {
    "id": "liquid/lfm-2.5-1.2b-instruct:free",
    "canonical_slug": "liquid/lfm-2.5-1.2b-instruct-20260120",
    "name": "LiquidAI: LFM2.5-1.2B-Instruct (free)",
    "raw_description": "LFM2.5-1.2B-Instruct is a compact, high-performance instruction-tuned model built for fast on-device AI. It delivers strong chat quality in a 1.2B parameter footprint, with efficient edge inference and broad runtime support.",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1768927521,
    "expiration_date": null,
    "model_author": "LiquidAI"
  },
  {
    "id": "openai/gpt-audio",
    "canonical_slug": "openai/gpt-audio",
    "name": "OpenAI: GPT Audio",
    "raw_description": "The gpt-audio model is OpenAI's first generally available audio model. The new snapshot features an upgraded decoder for more natural sounding voices and maintains better voice consistency. Audio is priced...",
    "context_length": 128000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text",
      "audio"
    ],
    "output_modalities": [
      "text",
      "audio"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1768862569,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-audio-mini",
    "canonical_slug": "openai/gpt-audio-mini",
    "name": "OpenAI: GPT Audio Mini",
    "raw_description": "A cost-efficient version of GPT Audio. The new snapshot features an upgraded decoder for more natural sounding voices and maintains better voice consistency. Input is priced at $0.60 per million...",
    "context_length": 128000,
    "pricing": {
      "input": 0.6,
      "output": 2.4
    },
    "input_modalities": [
      "text",
      "audio"
    ],
    "output_modalities": [
      "text",
      "audio"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1768859419,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "z-ai/glm-4.7-flash",
    "canonical_slug": "z-ai/glm-4.7-flash-20260119",
    "name": "Z.ai: GLM 4.7 Flash",
    "raw_description": "As a 30B-class SOTA model, GLM-4.7-Flash offers a new option that balances performance and efficiency. It is further optimized for agentic coding use cases, strengthening coding capabilities, long-horizon task planning,...",
    "context_length": 202752,
    "pricing": {
      "input": 0.06,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1768833913,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "black-forest-labs/flux.2-klein-4b",
    "canonical_slug": "black-forest-labs/flux.2-klein-4b",
    "name": "Black Forest Labs: FLUX.2 Klein 4B",
    "raw_description": "FLUX.2 [klein] 4B is the fastest and most cost-effective model in the FLUX.2 family, optimized for high-throughput use cases while maintaining excellent image quality. Pricing is based on the output...",
    "context_length": 40960,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "seed"
    ],
    "created": 1768429228,
    "expiration_date": null,
    "model_author": "Black Forest Labs"
  },
  {
    "id": "openai/gpt-5.2-codex",
    "canonical_slug": "openai/gpt-5.2-codex-20260114",
    "name": "OpenAI: GPT-5.2-Codex",
    "raw_description": "GPT-5.2-Codex is an upgraded version of GPT-5.1-Codex optimized for software engineering and coding workflows. It is designed for both interactive development sessions and long, independent execution of complex engineering tasks....",
    "context_length": 400000,
    "pricing": {
      "input": 1.75,
      "output": 14
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1768409315,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "bytedance-seed/seedream-4.5",
    "canonical_slug": "bytedance-seed/seedream-4.5-20251203",
    "name": "ByteDance Seed: Seedream 4.5",
    "raw_description": "Seedream 4.5 is the latest in-house image generation model developed by ByteDance. Compared with Seedream 4.0, it delivers comprehensive improvements, especially in editing consistency, including better preservation of subject details,...",
    "context_length": 4096,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1766519506,
    "expiration_date": null,
    "model_author": "ByteDance Seed"
  },
  {
    "id": "bytedance-seed/seed-1.6-flash",
    "canonical_slug": "bytedance-seed/seed-1.6-flash-20250625",
    "name": "ByteDance Seed: Seed 1.6 Flash",
    "raw_description": "Seed 1.6 Flash is an ultra-fast multimodal deep thinking model by ByteDance Seed, supporting both text and visual understanding. It features a 256k context window and can generate outputs of...",
    "context_length": 262144,
    "pricing": {
      "input": 0.075,
      "output": 0.3,
      "overrides": [
        {
          "min_prompt_tokens": 128000,
          "input": 0.09999999999999999,
          "output": 0.7999999999999999
        }
      ]
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1766505011,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "reasoning_declared": true
  },
  {
    "id": "bytedance-seed/seed-1.6",
    "canonical_slug": "bytedance-seed/seed-1.6-20250625",
    "name": "ByteDance Seed: Seed 1.6",
    "raw_description": "Seed 1.6 is a general-purpose model released by the ByteDance Seed team. It incorporates multimodal capabilities and adaptive deep thinking with a 256K context window.",
    "context_length": 262144,
    "pricing": {
      "input": 0.25,
      "output": 2,
      "overrides": [
        {
          "min_prompt_tokens": 128000,
          "input": 0.5,
          "output": 4
        }
      ]
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1766504997,
    "expiration_date": null,
    "model_author": "ByteDance Seed",
    "reasoning_declared": true
  },
  {
    "id": "minimax/minimax-m2.1",
    "canonical_slug": "minimax/minimax-m2.1",
    "name": "MiniMax: MiniMax M2.1",
    "raw_description": "MiniMax-M2.1 is a lightweight, state-of-the-art large language model optimized for coding, agentic workflows, and modern application development. With only 10 billion activated parameters, it delivers a major jump in real-world...",
    "context_length": 204800,
    "pricing": {
      "input": 0.3,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1766454997,
    "expiration_date": null,
    "model_author": "MiniMax",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-4.7",
    "canonical_slug": "z-ai/glm-4.7-20251222",
    "name": "Z.ai: GLM 4.7",
    "raw_description": "GLM-4.7 is Z.ai’s latest flagship model, featuring upgrades in two key areas: enhanced programming capabilities and more stable multi-step reasoning/execution. It demonstrates significant improvements in executing complex agent tasks while...",
    "context_length": 204800,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 1.75
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1766378014,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3-flash-preview",
    "canonical_slug": "google/gemini-3-flash-preview-20251217",
    "name": "Google: Gemini 3 Flash Preview",
    "raw_description": "Gemini 3 Flash Preview is a high speed, high value thinking model designed for agentic workflows, multi turn chat, and coding assistance. It delivers near Pro level reasoning and tool...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.5,
      "output": 3
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1765987078,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "black-forest-labs/flux.2-max",
    "canonical_slug": "black-forest-labs/flux.2-max",
    "name": "Black Forest Labs: FLUX.2 Max",
    "raw_description": "FLUX.2 [max] is the new top-tier image model from Black Forest Labs, pushing image quality, prompt understanding, and editing consistency to the highest level yet. Pricing is as follows, [per...",
    "context_length": 46864,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "seed"
    ],
    "created": 1765857570,
    "expiration_date": null,
    "model_author": "Black Forest Labs"
  },
  {
    "id": "xiaomi/mimo-v2-flash",
    "canonical_slug": "xiaomi/mimo-v2-flash-20251210",
    "name": "Xiaomi: MiMo-V2-Flash",
    "raw_description": "MiMo-V2-Flash is an open-source foundation language model developed by Xiaomi. It is a Mixture-of-Experts model with 309B total parameters and 15B active parameters, adopting hybrid attention architecture. MiMo-V2-Flash supports a...",
    "context_length": 262144,
    "pricing": {
      "input": 0.1,
      "output": 0.3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1765731308,
    "expiration_date": null,
    "model_author": "Xiaomi"
  },
  {
    "id": "nvidia/nemotron-3-nano-30b-a3b",
    "canonical_slug": "nvidia/nemotron-3-nano-30b-a3b",
    "name": "NVIDIA: Nemotron 3 Nano 30B A3B",
    "raw_description": "NVIDIA Nemotron 3 Nano 30B A3B is a small language MoE model with highest compute efficiency and accuracy for developers to build specialized agentic AI systems. The model is fully...",
    "context_length": 262144,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1765731275,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3-nano-30b-a3b:free",
    "canonical_slug": "nvidia/nemotron-3-nano-30b-a3b",
    "name": "NVIDIA: Nemotron 3 Nano 30B A3B (free)",
    "raw_description": "NVIDIA Nemotron 3 Nano 30B A3B is a small language MoE model with highest compute efficiency and accuracy for developers to build specialized agentic AI systems. The model is fully...",
    "context_length": 256000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1765731275,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.2-chat",
    "canonical_slug": "openai/gpt-5.2-chat-20251211",
    "name": "OpenAI: GPT-5.2 Chat",
    "raw_description": "GPT-5.2 Chat (AKA Instant) is the fast, lightweight member of the 5.2 family, optimized for low-latency chat while retaining strong general intelligence. It uses adaptive reasoning to selectively “think” on...",
    "context_length": 128000,
    "pricing": {
      "input": 1.75,
      "output": 14
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_completion_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1765389783,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-5.2-pro",
    "canonical_slug": "openai/gpt-5.2-pro-20251211",
    "name": "OpenAI: GPT-5.2 Pro",
    "raw_description": "GPT-5.2 Pro is OpenAI’s most advanced model, offering major improvements in agentic coding and long context performance over GPT-5 Pro. It is optimized for complex tasks that require step-by-step reasoning,...",
    "context_length": 400000,
    "pricing": {
      "input": 21,
      "output": 168
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1765389780,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.2",
    "canonical_slug": "openai/gpt-5.2-20251211",
    "name": "OpenAI: GPT-5.2",
    "raw_description": "GPT-5.2 is the latest frontier-grade model in the GPT-5 series, offering stronger agentic and long context perfomance compared to GPT-5.1. It uses adaptive reasoning to allocate computation dynamically, responding quickly...",
    "context_length": 400000,
    "pricing": {
      "input": 1.75,
      "output": 14
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1765389775,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/devstral-2512",
    "canonical_slug": "mistralai/devstral-2512",
    "name": "Mistral: Devstral 2 2512",
    "raw_description": "Devstral 2 is a state-of-the-art open-source model by Mistral AI specializing in agentic coding. It is a 123B-parameter dense transformer model supporting a 256K context window. Devstral 2 supports exploring...",
    "context_length": 262144,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 2
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1765285419,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "sourceful/riverflow-v2-max-preview",
    "canonical_slug": "sourceful/riverflow-v2-max-preview",
    "name": "Sourceful: Riverflow V2 Max Preview",
    "raw_description": "Riverflow V2 Max Preview is the most powerful variant of Sourceful's Riverflow V2 preview lineup. This preview version exceeds the performance of Riverflow 1 Family and is Sourceful's first unified...",
    "context_length": 8192,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1765237849,
    "expiration_date": null,
    "model_author": "Sourceful"
  },
  {
    "id": "sourceful/riverflow-v2-standard-preview",
    "canonical_slug": "sourceful/riverflow-v2-standard-preview",
    "name": "Sourceful: Riverflow V2 Standard Preview",
    "raw_description": "Riverflow V2 Standard Preview is the standard variant of Sourceful's Riverflow V2 preview lineup. This preview version exceeds the performance of Riverflow 1 Family and is Sourceful's first unified text-to-image...",
    "context_length": 8192,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1765237836,
    "expiration_date": null,
    "model_author": "Sourceful"
  },
  {
    "id": "sourceful/riverflow-v2-fast-preview",
    "canonical_slug": "sourceful/riverflow-v2-fast-preview",
    "name": "Sourceful: Riverflow V2 Fast Preview",
    "raw_description": "Riverflow V2 Fast Preview is the fastest variant of Sourceful's Riverflow V2 preview lineup. This preview version exceeds the performance of Riverflow 1 Family and is Sourceful's first unified text-to-image...",
    "context_length": 8192,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [],
    "created": 1765237820,
    "expiration_date": null,
    "model_author": "Sourceful"
  },
  {
    "id": "relace/relace-search",
    "canonical_slug": "relace/relace-search-20251208",
    "name": "Relace: Relace Search",
    "raw_description": "The relace-search model uses 4-12 `view_file` and `grep` tools in parallel to explore a codebase and return relevant files to the user request. In contrast to RAG, relace-search performs agentic...",
    "context_length": 256000,
    "pricing": {
      "input": 1,
      "output": 3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1765213560,
    "expiration_date": null,
    "model_author": "Relace"
  },
  {
    "id": "z-ai/glm-4.6v",
    "canonical_slug": "z-ai/glm-4.6-20251208",
    "name": "Z.ai: GLM 4.6V",
    "raw_description": "GLM-4.6V is a large multimodal model designed for high-fidelity visual understanding and long-context reasoning across images, documents, and mixed media. It supports up to 128K tokens, processes complex page layouts...",
    "context_length": 131072,
    "pricing": {
      "input": 0.3,
      "output": 0.8999999999999999
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1765207462,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "nex-agi/deepseek-v3.1-nex-n1",
    "canonical_slug": "nex-agi/deepseek-v3.1-nex-n1",
    "name": "Nex AGI: DeepSeek V3.1 Nex N1",
    "raw_description": "DeepSeek V3.1 Nex-N1 is the flagship release of the Nex-N1 series — a post-trained model designed to highlight agent autonomy, tool use, and real-world productivity. Nex-N1 demonstrates competitive performance across...",
    "context_length": 131072,
    "pricing": {
      "input": 0.135,
      "output": 0.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1765204393,
    "expiration_date": null,
    "model_author": "Nex AGI"
  },
  {
    "id": "essentialai/rnj-1-instruct",
    "canonical_slug": "essentialai/rnj-1-instruct",
    "name": "EssentialAI: Rnj 1 Instruct",
    "raw_description": "Rnj-1 is an 8B-parameter, dense, open-weight model family developed by Essential AI and trained from scratch with a focus on programming, math, and scientific reasoning. The model demonstrates strong performance...",
    "context_length": 32768,
    "pricing": {
      "input": 0.15,
      "output": 0.15
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1765094847,
    "expiration_date": null,
    "model_author": "EssentialAI"
  },
  {
    "id": "openrouter/bodybuilder",
    "canonical_slug": "openrouter/bodybuilder",
    "name": "Body Builder (beta)",
    "raw_description": "Transform your natural language requests into structured OpenRouter API request objects. Describe what you want to accomplish with AI models, and Body Builder will construct the appropriate API calls. Example:...",
    "context_length": 128000,
    "pricing": {
      "input": -1000000,
      "output": -1000000
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Router",
    "supported_parameters": [],
    "created": 1764903653,
    "expiration_date": null,
    "model_author": "OpenRouter"
  },
  {
    "id": "openai/gpt-5.1-codex-max",
    "canonical_slug": "openai/gpt-5.1-codex-max-20251204",
    "name": "OpenAI: GPT-5.1-Codex-Max",
    "raw_description": "GPT-5.1-Codex-Max is OpenAI’s latest agentic coding model, designed for long-running, high-context software development tasks. It is based on an updated version of the 5.1 reasoning stack and trained on agentic...",
    "context_length": 400000,
    "pricing": {
      "input": 1.25,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1764878934,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "amazon/nova-2-lite-v1",
    "canonical_slug": "amazon/nova-2-lite-v1",
    "name": "Amazon: Nova 2 Lite",
    "raw_description": "Nova 2 Lite is a fast, cost-effective reasoning model for everyday workloads that can process text, images, and videos to generate text. Nova 2 Lite demonstrates standout capabilities in processing...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.3,
      "output": 2.5
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Nova",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1764696672,
    "expiration_date": null,
    "model_author": "Amazon",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/ministral-14b-2512",
    "canonical_slug": "mistralai/ministral-14b-2512",
    "name": "Mistral: Ministral 3 14B 2512",
    "raw_description": "The largest model in the Ministral 3 family, Ministral 3 14B offers frontier capabilities and performance comparable to its larger Mistral Small 3.2 24B counterpart. A powerful and efficient language...",
    "context_length": 262144,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1764681735,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "mistralai/ministral-8b-2512",
    "canonical_slug": "mistralai/ministral-8b-2512",
    "name": "Mistral: Ministral 3 8B 2512",
    "raw_description": "A balanced model in the Ministral 3 family, Ministral 3 8B is a powerful, efficient tiny language model with vision capabilities.",
    "context_length": 262144,
    "pricing": {
      "input": 0.15,
      "output": 0.15
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1764681654,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "mistralai/ministral-3b-2512",
    "canonical_slug": "mistralai/ministral-3b-2512",
    "name": "Mistral: Ministral 3 3B 2512",
    "raw_description": "The smallest model in the Ministral 3 family, Ministral 3 3B is a powerful, efficient tiny language model with vision capabilities.",
    "context_length": 131072,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.09999999999999999
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1764681560,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "mistralai/mistral-large-2512",
    "canonical_slug": "mistralai/mistral-large-2512",
    "name": "Mistral: Mistral Large 3 2512",
    "raw_description": "Mistral Large 3 2512 is Mistral’s most capable model to date, featuring a sparse mixture-of-experts architecture with 41B active parameters (675B total), and released under the Apache 2.0 license.",
    "context_length": 262144,
    "pricing": {
      "input": 0.5,
      "output": 1.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1764624472,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "arcee-ai/trinity-mini",
    "canonical_slug": "arcee-ai/trinity-mini-20251201",
    "name": "Arcee AI: Trinity Mini",
    "raw_description": "Trinity Mini is a 26B-parameter (3B active) sparse mixture-of-experts language model featuring 128 experts with 8 active per token. Engineered for efficient reasoning over long contexts (131k) with robust function...",
    "context_length": 131072,
    "pricing": {
      "input": 0.045,
      "output": 0.15
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1764601720,
    "expiration_date": null,
    "model_author": "Arcee AI"
  },
  {
    "id": "deepseek/deepseek-v3.2",
    "canonical_slug": "deepseek/deepseek-v3.2-20251201",
    "name": "DeepSeek: DeepSeek V3.2",
    "raw_description": "DeepSeek-V3.2 is a large language model designed to harmonize high computational efficiency with strong reasoning and agentic tool-use performance. It introduces DeepSeek Sparse Attention (DSA), a fine-grained sparse attention mechanism...",
    "context_length": 163840,
    "pricing": {
      "input": 0.26899999999999996,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1764594642,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "prime-intellect/intellect-3",
    "canonical_slug": "prime-intellect/intellect-3-20251126",
    "name": "Prime Intellect: INTELLECT-3",
    "raw_description": "INTELLECT-3 is a 106B-parameter Mixture-of-Experts model (12B active) post-trained from GLM-4.5-Air-Base using supervised fine-tuning (SFT) followed by large-scale reinforcement learning (RL). It offers state-of-the-art performance for its size across math,...",
    "context_length": 131072,
    "pricing": {
      "input": 0.2,
      "output": 1.1
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1764212534,
    "expiration_date": null,
    "model_author": "Prime Intellect"
  },
  {
    "id": "black-forest-labs/flux.2-flex",
    "canonical_slug": "black-forest-labs/flux.2-flex",
    "name": "Black Forest Labs: FLUX.2 Flex",
    "raw_description": "FLUX.2 [flex] excels at rendering complex text, typography, and fine details, and supports multi-reference editing in the same unified architecture. Pricing is as follows, [per the docs](https://bfl.ai/pricing?category=flux.2): We charge $0.06...",
    "context_length": 67344,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "seed"
    ],
    "created": 1764045987,
    "expiration_date": null,
    "model_author": "Black Forest Labs"
  },
  {
    "id": "black-forest-labs/flux.2-pro",
    "canonical_slug": "black-forest-labs/flux.2-pro",
    "name": "Black Forest Labs: FLUX.2 Pro",
    "raw_description": "A high-end image generation and editing model focused on frontier-level visual quality and reliability. It delivers strong prompt adherence, stable lighting, sharp textures, and consistent character/style reproduction across multi-reference inputs....",
    "context_length": 46864,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "image"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "seed"
    ],
    "created": 1764030274,
    "expiration_date": null,
    "model_author": "Black Forest Labs"
  },
  {
    "id": "anthropic/claude-opus-4.5",
    "canonical_slug": "anthropic/claude-4.5-opus-20251124",
    "name": "Anthropic: Claude Opus 4.5",
    "raw_description": "Claude Opus 4.5 is Anthropic’s frontier reasoning model optimized for complex software engineering, agentic workflows, and long-horizon computer use. It offers strong multimodal capabilities, competitive performance across real-world coding and...",
    "context_length": 200000,
    "pricing": {
      "input": 5,
      "output": 25
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "verbosity"
    ],
    "created": 1764010580,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "allenai/olmo-3-32b-think",
    "canonical_slug": "allenai/olmo-3-32b-think-20251121",
    "name": "AllenAI: Olmo 3 32B Think",
    "raw_description": "Olmo 3 32B Think is a large-scale, 32-billion-parameter model purpose-built for deep reasoning, complex logic chains and advanced instruction-following scenarios. Its capacity enables strong performance on demanding evaluation tasks and...",
    "context_length": 65536,
    "pricing": {
      "input": 0.15,
      "output": 0.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763758276,
    "expiration_date": null,
    "model_author": "AllenAI",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3-pro-image-preview",
    "canonical_slug": "google/gemini-3-pro-image-preview-20251120",
    "name": "Google: Nano Banana Pro (Gemini 3 Pro Image Preview)",
    "raw_description": "Nano Banana Pro is Google’s most advanced image-generation and editing model, built on Gemini 3 Pro. It extends the original Nano Banana with significantly improved multimodal reasoning, real-world grounding, and...",
    "context_length": 65536,
    "pricing": {
      "input": 2,
      "output": 12
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1763653797,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "thenlper/gte-base",
    "canonical_slug": "thenlper/gte-base-20251117",
    "name": "Thenlper: GTE-Base",
    "raw_description": "The gte-base embedding model encodes English sentences and paragraphs into a 768-dimensional dense vector space, delivering efficient and effective semantic embeddings optimized for textual similarity, semantic search, and clustering applications.",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763433820,
    "expiration_date": null,
    "model_author": "Thenlper"
  },
  {
    "id": "thenlper/gte-large",
    "canonical_slug": "thenlper/gte-large-20251117",
    "name": "Thenlper: GTE-Large",
    "raw_description": "The gte-large embedding model converts English sentences, paragraphs and moderate-length documents into a 1024-dimensional dense vector space, delivering high-quality semantic embeddings optimized for information retrieval, semantic textual similarity, reranking and...",
    "context_length": 512,
    "pricing": {
      "input": 0.01,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763433655,
    "expiration_date": null,
    "model_author": "Thenlper"
  },
  {
    "id": "intfloat/e5-large-v2",
    "canonical_slug": "intfloat/e5-large-v2-20251117",
    "name": "Intfloat: E5-Large-v2",
    "raw_description": "The e5-large-v2 embedding model maps English sentences, paragraphs, and documents into a 1024-dimensional dense vector space, delivering high-accuracy semantic embeddings optimized for retrieval, semantic search, reranking, and similarity-scoring tasks.",
    "context_length": 512,
    "pricing": {
      "input": 0.01,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763433432,
    "expiration_date": null,
    "model_author": "Intfloat"
  },
  {
    "id": "intfloat/e5-base-v2",
    "canonical_slug": "intfloat/e5-base-v2-20251117",
    "name": "Intfloat: E5-Base-v2",
    "raw_description": "The e5-base-v2 embedding model encodes English sentences and paragraphs into a 768-dimensional dense vector space, producing efficient and high-quality semantic embeddings optimized for tasks such as semantic search, similarity scoring,...",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763433192,
    "expiration_date": null,
    "model_author": "Intfloat"
  },
  {
    "id": "intfloat/multilingual-e5-large",
    "canonical_slug": "intfloat/multilingual-e5-large-20251117",
    "name": "Intfloat: Multilingual-E5-Large",
    "raw_description": "The multilingual-e5-large embedding model encodes sentences, paragraphs, and documents across over 90 languages into a 1024-dimensional dense vector space, delivering robust semantic embeddings optimized for multilingual retrieval, cross-language similarity, and...",
    "context_length": 512,
    "pricing": {
      "input": 0.01,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763433047,
    "expiration_date": null,
    "model_author": "Intfloat"
  },
  {
    "id": "sentence-transformers/paraphrase-minilm-l6-v2",
    "canonical_slug": "sentence-transformers/paraphrase-minilm-l6-v2-20251117",
    "name": "Sentence Transformers: paraphrase-MiniLM-L6-v2",
    "raw_description": "The paraphrase-MiniLM-L6-v2 embedding model converts sentences and short paragraphs into a 384-dimensional dense vector space, producing high-quality semantic embeddings optimized for paraphrase detection, semantic similarity scoring, clustering, and lightweight retrieval...",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763432454,
    "expiration_date": null,
    "model_author": "Sentence Transformers"
  },
  {
    "id": "sentence-transformers/all-minilm-l12-v2",
    "canonical_slug": "sentence-transformers/all-minilm-l12-v2-20251117",
    "name": "Sentence Transformers: all-MiniLM-L12-v2",
    "raw_description": "The all-MiniLM-L12-v2 embedding model maps sentences and short paragraphs into a 384-dimensional dense vector space, producing efficient and high-quality semantic embeddings optimized for tasks such as semantic search, clustering, and...",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763432155,
    "expiration_date": null,
    "model_author": "Sentence Transformers"
  },
  {
    "id": "baai/bge-base-en-v1.5",
    "canonical_slug": "baai/bge-base-en-v1.5-20251117",
    "name": "BAAI: bge-base-en-v1.5",
    "raw_description": "The bge-base-en-v1.5 embedding model converts English sentences and paragraphs into 768-dimensional dense vectors, delivering efficient, high-quality semantic embeddings optimized for retrieval, semantic search, and document-matching workflows. This version (v1.5) features...",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763431837,
    "expiration_date": null,
    "model_author": "BAAI"
  },
  {
    "id": "sentence-transformers/multi-qa-mpnet-base-dot-v1",
    "canonical_slug": "sentence-transformers/multi-qa-mpnet-base-dot-v1-20251117",
    "name": "Sentence Transformers: multi-qa-mpnet-base-dot-v1",
    "raw_description": "The multi-qa-mpnet-base-dot-v1 embedding model transforms sentences and short paragraphs into a 768-dimensional dense vector space, generating high-quality semantic embeddings optimized for question-and-answer retrieval, semantic search, and similarity-scoring across diverse content.",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763431339,
    "expiration_date": null,
    "model_author": "Sentence Transformers"
  },
  {
    "id": "baai/bge-large-en-v1.5",
    "canonical_slug": "baai/bge-large-en-v1.5-20251117",
    "name": "BAAI: bge-large-en-v1.5",
    "raw_description": "The bge-large-en-v1.5 embedding model maps English sentences, paragraphs, and documents into a 1024-dimensional dense vector space, delivering high-fidelity semantic embeddings optimized for semantic search, document retrieval, and downstream NLP tasks...",
    "context_length": 512,
    "pricing": {
      "input": 0.01,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763431087,
    "expiration_date": null,
    "model_author": "BAAI"
  },
  {
    "id": "baai/bge-m3",
    "canonical_slug": "baai/bge-m3-20251117",
    "name": "BAAI: bge-m3",
    "raw_description": "The bge-m3 embedding model encodes sentences, paragraphs, and long documents into a 1024-dimensional dense vector space, delivering high-quality semantic embeddings optimized for multilingual retrieval, semantic search, and large-context applications.",
    "context_length": 8194,
    "pricing": {
      "input": 0.01,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763424372,
    "expiration_date": null,
    "model_author": "BAAI"
  },
  {
    "id": "sentence-transformers/all-mpnet-base-v2",
    "canonical_slug": "sentence-transformers/all-mpnet-base-v2-20251117",
    "name": "Sentence Transformers: all-mpnet-base-v2",
    "raw_description": "The all-mpnet-base-v2 embedding model encodes sentences and short paragraphs into a 768-dimensional dense vector space, providing high-fidelity semantic embeddings well suited for tasks like information retrieval, clustering, similarity scoring, and...",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763421830,
    "expiration_date": null,
    "model_author": "Sentence Transformers"
  },
  {
    "id": "sentence-transformers/all-minilm-l6-v2",
    "canonical_slug": "sentence-transformers/all-minilm-l6-v2-20251117",
    "name": "Sentence Transformers: all-MiniLM-L6-v2",
    "raw_description": "The all-MiniLM-L6-v2 embedding model maps sentences and short paragraphs into a 384-dimensional dense vector space, enabling high-quality semantic representations that are ideal for downstream tasks such as information retrieval, clustering,...",
    "context_length": 512,
    "pricing": {
      "input": 0.005,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763421176,
    "expiration_date": null,
    "model_author": "Sentence Transformers"
  },
  {
    "id": "deepcogito/cogito-v2.1-671b",
    "canonical_slug": "deepcogito/cogito-v2.1-671b-20251118",
    "name": "Deep Cogito: Cogito v2.1 671B",
    "raw_description": "Cogito v2.1 671B MoE represents one of the strongest open models globally, matching performance of frontier closed and open models. This model is trained using self play with reinforcement learning...",
    "context_length": 128000,
    "pricing": {
      "input": 1.25,
      "output": 1.25
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1763071233,
    "expiration_date": null,
    "model_author": "Deep Cogito",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.1",
    "canonical_slug": "openai/gpt-5.1-20251113",
    "name": "OpenAI: GPT-5.1",
    "raw_description": "GPT-5.1 is the latest frontier-grade model in the GPT-5 series, offering stronger general-purpose reasoning, improved instruction adherence, and a more natural conversational style compared to GPT-5. It uses adaptive reasoning...",
    "context_length": 400000,
    "pricing": {
      "input": 1.25,
      "output": 10
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1763060305,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.1-chat",
    "canonical_slug": "openai/gpt-5.1-chat-20251113",
    "name": "OpenAI: GPT-5.1 Chat",
    "raw_description": "GPT-5.1 Chat (AKA Instant is the fast, lightweight member of the 5.1 family, optimized for low-latency chat while retaining strong general intelligence. It uses adaptive reasoning to selectively “think” on...",
    "context_length": 128000,
    "pricing": {
      "input": 1.25,
      "output": 10
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_completion_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1763060302,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-5.1-codex",
    "canonical_slug": "openai/gpt-5.1-codex-20251113",
    "name": "OpenAI: GPT-5.1-Codex",
    "raw_description": "GPT-5.1-Codex is a specialized version of GPT-5.1 optimized for software engineering and coding workflows. It is designed for both interactive development sessions and long, independent execution of complex engineering tasks....",
    "context_length": 400000,
    "pricing": {
      "input": 1.25,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1763060298,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.1-codex-mini",
    "canonical_slug": "openai/gpt-5.1-codex-mini-20251113",
    "name": "OpenAI: GPT-5.1-Codex-Mini",
    "raw_description": "GPT-5.1-Codex-Mini is a smaller and faster version of GPT-5.1-Codex",
    "context_length": 400000,
    "pricing": {
      "input": 0.25,
      "output": 2
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1763057820,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "moonshotai/kimi-k2-thinking",
    "canonical_slug": "moonshotai/kimi-k2-thinking-20251106",
    "name": "MoonshotAI: Kimi K2 Thinking",
    "raw_description": "Kimi K2 Thinking is Moonshot AI’s most advanced open reasoning model to date, extending the K2 series into agentic, long-horizon reasoning. Built on the trillion-parameter Mixture-of-Experts (MoE) architecture introduced in...",
    "context_length": 262144,
    "pricing": {
      "input": 0.6,
      "output": 2.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1762440622,
    "expiration_date": null,
    "model_author": "MoonshotAI",
    "reasoning_declared": true
  },
  {
    "id": "amazon/nova-premier-v1",
    "canonical_slug": "amazon/nova-premier-v1",
    "name": "Amazon: Nova Premier 1.0",
    "raw_description": "Amazon Nova Premier is the most capable of Amazon’s multimodal models for complex reasoning tasks and for use as the best teacher for distilling custom models.",
    "context_length": 1000000,
    "pricing": {
      "input": 2.5,
      "output": 12.5
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Nova",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1761950332,
    "expiration_date": null,
    "model_author": "Amazon"
  },
  {
    "id": "mistralai/mistral-embed-2312",
    "canonical_slug": "mistralai/mistral-embed-2312",
    "name": "Mistral: Mistral Embed 2312",
    "raw_description": "Mistral Embed is a specialized embedding model for text data, optimized for semantic search and RAG applications. Developed by Mistral AI in late 2023, it produces 1024-dimensional vectors that effectively...",
    "context_length": 8192,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1761944622,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "google/gemini-embedding-001",
    "canonical_slug": "google/gemini-embedding-001",
    "name": "Google: Gemini Embedding 001",
    "raw_description": "gemini-embedding-001 provides a unified cutting edge experience across domains, including science, legal, finance, and coding. This embedding model has consistently held a top spot on the Massive Text Embedding Benchmark...",
    "context_length": 20000,
    "pricing": {
      "input": 0.15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "temperature",
      "top_p"
    ],
    "created": 1761943410,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "openai/text-embedding-ada-002",
    "canonical_slug": "openai/text-embedding-ada-002",
    "name": "OpenAI: Text Embedding Ada 002",
    "raw_description": "text-embedding-ada-002 is OpenAI's legacy text embedding model.",
    "context_length": 8192,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761865798,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "mistralai/codestral-embed-2505",
    "canonical_slug": "mistralai/codestral-embed-2505",
    "name": "Mistral: Codestral Embed 2505",
    "raw_description": "Mistral Codestral Embed is specially designed for code, perfect for embedding code databases, repositories, and powering coding assistants with state-of-the-art retrieval.",
    "context_length": 8192,
    "pricing": {
      "input": 0.15,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1761864460,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "openai/text-embedding-3-large",
    "canonical_slug": "openai/text-embedding-3-large",
    "name": "OpenAI: Text Embedding 3 Large",
    "raw_description": "text-embedding-3-large is OpenAI's most capable embedding model for both english and non-english tasks. Embeddings are a numerical representation of text that can be used to measure the relatedness between two...",
    "context_length": 8192,
    "pricing": {
      "input": 0.13,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761862866,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/text-embedding-3-small",
    "canonical_slug": "openai/text-embedding-3-small",
    "name": "OpenAI: Text Embedding 3 Small",
    "raw_description": "text-embedding-3-small is OpenAI's improved, more performant version of the ada embedding model. Embeddings are a numerical representation of text that can be used to measure the relatedness between two pieces...",
    "context_length": 8192,
    "pricing": {
      "input": 0.02,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761857455,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "perplexity/sonar-pro-search",
    "canonical_slug": "perplexity/sonar-pro-search",
    "name": "Perplexity: Sonar Pro Search",
    "raw_description": "Exclusively available on the OpenRouter API, Sonar Pro's new Pro Search mode is Perplexity's most advanced agentic search system. It is designed for deeper reasoning and analysis. Pricing is based...",
    "context_length": 200000,
    "pricing": {
      "input": 3,
      "output": 15
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p",
      "web_search_options"
    ],
    "created": 1761854366,
    "expiration_date": null,
    "model_author": "Perplexity",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/voxtral-small-24b-2507",
    "canonical_slug": "mistralai/voxtral-small-24b-2507",
    "name": "Mistral: Voxtral Small 24B 2507",
    "raw_description": "Voxtral Small is an enhancement of Mistral Small 3, incorporating state-of-the-art audio input capabilities while retaining best-in-class text performance. It excels at speech transcription, translation and audio understanding. Input audio...",
    "context_length": 32000,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.3
    },
    "input_modalities": [
      "text",
      "audio",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1761835144,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "openai/gpt-oss-safeguard-20b",
    "canonical_slug": "openai/gpt-oss-safeguard-20b",
    "name": "OpenAI: gpt-oss-safeguard-20b",
    "raw_description": "gpt-oss-safeguard-20b is a safety reasoning model from OpenAI built upon gpt-oss-20b. This open-weight, 21B-parameter Mixture-of-Experts (MoE) model offers lower latency for safety tasks like content classification, LLM filtering, and trust...",
    "context_length": 131072,
    "pricing": {
      "input": 0.075,
      "output": 0.3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1761752836,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-embedding-8b",
    "canonical_slug": "qwen/qwen3-embedding-8b",
    "name": "Qwen: Qwen3 Embedding 8B",
    "raw_description": "The Qwen3 Embedding model series is the latest proprietary model of the Qwen family, specifically designed for text embedding and ranking tasks. This series inherits the exceptional multilingual capabilities, long-text...",
    "context_length": 32768,
    "pricing": {
      "input": 0.01,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761680622,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "nvidia/nemotron-nano-12b-v2-vl:free",
    "canonical_slug": "nvidia/nemotron-nano-12b-v2-vl",
    "name": "NVIDIA: Nemotron Nano 12B 2 VL (free)",
    "raw_description": "NVIDIA Nemotron Nano 2 VL is a 12-billion-parameter open multimodal reasoning model designed for video understanding and document intelligence. It introduces a hybrid Transformer-Mamba architecture, combining transformer-level accuracy with Mamba’s...",
    "context_length": 128000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "seed",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1761675565,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-embedding-4b",
    "canonical_slug": "qwen/qwen3-embedding-4b",
    "name": "Qwen: Qwen3 Embedding 4B",
    "raw_description": "The Qwen3 Embedding model series is the latest proprietary model of the Qwen family, specifically designed for text embedding and ranking tasks. This series inherits the exceptional multilingual capabilities, long-text...",
    "context_length": 32768,
    "pricing": {
      "input": 0.02,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1761662922,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "minimax/minimax-m2",
    "canonical_slug": "minimax/minimax-m2",
    "name": "MiniMax: MiniMax M2",
    "raw_description": "MiniMax-M2 is a compact, high-efficiency large language model optimized for end-to-end coding and agentic workflows. With 10 billion activated parameters (230 billion total), it delivers near-frontier intelligence across general reasoning,...",
    "context_length": 204800,
    "pricing": {
      "input": 0.255,
      "output": 1.02
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761252093,
    "expiration_date": null,
    "model_author": "MiniMax",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-vl-32b-instruct",
    "canonical_slug": "qwen/qwen3-vl-32b-instruct",
    "name": "Qwen: Qwen3 VL 32B Instruct",
    "raw_description": "Qwen3-VL-32B-Instruct is a large-scale multimodal vision-language model designed for high-precision understanding and reasoning across text, images, and video. With 32 billion parameters, it combines deep visual perception with advanced text...",
    "context_length": 131072,
    "pricing": {
      "input": 0.10400000000000001,
      "output": 0.41600000000000004
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761231332,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "ibm-granite/granite-4.0-h-micro",
    "canonical_slug": "ibm-granite/granite-4.0-h-micro",
    "name": "IBM: Granite 4.0 Micro",
    "raw_description": "Granite-4.0-H-Micro is a 3B parameter from the Granite 4 family of models. These models are the latest in a series of models released by IBM. They are fine-tuned for long...",
    "context_length": 131000,
    "pricing": {
      "input": 0.017,
      "output": 0.112
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1760927695,
    "expiration_date": null,
    "model_author": "IBM"
  },
  {
    "id": "microsoft/phi-4-mini-instruct",
    "canonical_slug": "microsoft/phi-4-mini-instruct",
    "name": "Microsoft: Phi 4 Mini Instruct",
    "raw_description": "Phi-4-mini-instruct is a lightweight open model built upon synthetic data and filtered publicly available websites - with a focus on high-quality, reasoning dense data. The model belongs to the Phi-4...",
    "context_length": 131072,
    "pricing": {
      "input": 0.08,
      "output": 0.35
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1760726049,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "openai/gpt-5-image-mini",
    "canonical_slug": "openai/gpt-5-image-mini",
    "name": "OpenAI: GPT-5 Image Mini",
    "raw_description": "GPT-5 Image Mini combines OpenAI's advanced language capabilities, powered by [GPT-5 Mini](https://openrouter.ai/openai/gpt-5-mini), with GPT Image 1 Mini for efficient image generation. This natively multimodal model features superior instruction following, text...",
    "context_length": 400000,
    "pricing": {
      "input": 2.5,
      "output": 2
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1760624583,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-haiku-4.5",
    "canonical_slug": "anthropic/claude-4.5-haiku-20251001",
    "name": "Anthropic: Claude Haiku 4.5",
    "raw_description": "Claude Haiku 4.5 is Anthropic’s fastest and most efficient model, delivering near-frontier intelligence at a fraction of the cost and latency of larger Claude models. Matching Claude Sonnet 4’s performance...",
    "context_length": 200000,
    "pricing": {
      "input": 1,
      "output": 5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1760547638,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-vl-8b-thinking",
    "canonical_slug": "qwen/qwen3-vl-8b-thinking",
    "name": "Qwen: Qwen3 VL 8B Thinking",
    "raw_description": "Qwen3-VL-8B-Thinking is the reasoning-optimized variant of the Qwen3-VL-8B multimodal model, designed for advanced visual and textual reasoning across complex scenes, documents, and temporal sequences. It integrates enhanced multimodal alignment and...",
    "context_length": 131072,
    "pricing": {
      "input": 0.18,
      "output": 2.0999999999999996
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1760463746,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-vl-8b-instruct",
    "canonical_slug": "qwen/qwen3-vl-8b-instruct",
    "name": "Qwen: Qwen3 VL 8B Instruct",
    "raw_description": "Qwen3-VL-8B-Instruct is a multimodal vision-language model from the Qwen3-VL series, built for high-fidelity understanding and reasoning across text, images, and video. It features improved multimodal fusion with Interleaved-MRoPE for long-horizon...",
    "context_length": 262144,
    "pricing": {
      "input": 0.117,
      "output": 0.45499999999999996
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1760463308,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "openai/gpt-5-image",
    "canonical_slug": "openai/gpt-5-image",
    "name": "OpenAI: GPT-5 Image",
    "raw_description": "[GPT-5](https://openrouter.ai/openai/gpt-5) Image combines OpenAI's GPT-5 model with state-of-the-art image generation capabilities. It offers major improvements in reasoning, code quality, and user experience while incorporating GPT Image 1's superior instruction following,...",
    "context_length": 400000,
    "pricing": {
      "input": 10,
      "output": 10
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1760447986,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o3-deep-research",
    "canonical_slug": "openai/o3-deep-research-2025-06-26",
    "name": "OpenAI: o3 Deep Research",
    "raw_description": "o3-deep-research is OpenAI's advanced model for deep research, designed to tackle complex, multi-step research tasks.\n\nNote: This model always uses the 'web_search' tool which adds additional cost.",
    "context_length": 200000,
    "pricing": {
      "input": 10,
      "output": 40
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1760129661,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/o4-mini-deep-research",
    "canonical_slug": "openai/o4-mini-deep-research-2025-06-26",
    "name": "OpenAI: o4 Mini Deep Research",
    "raw_description": "o4-mini-deep-research is OpenAI's faster, more affordable deep research model—ideal for tackling complex, multi-step research tasks.\n\nNote: This model always uses the 'web_search' tool which adds additional cost.",
    "context_length": 200000,
    "pricing": {
      "input": 2,
      "output": 8
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1760129642,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "canonical_slug": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "name": "NVIDIA: Llama 3.3 Nemotron Super 49B V1.5",
    "raw_description": "Llama-3.3-Nemotron-Super-49B-v1.5 is a 49B-parameter, English-centric reasoning/chat model derived from Meta’s Llama-3.3-70B-Instruct with a 128K context. It’s post-trained for agentic workflows (RAG, tool calling) via SFT across math, code, science, and...",
    "context_length": 131072,
    "pricing": {
      "input": 0.1,
      "output": 0.4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1760101395,
    "expiration_date": null,
    "model_author": "NVIDIA"
  },
  {
    "id": "google/gemini-2.5-flash-image",
    "canonical_slug": "google/gemini-2.5-flash-image",
    "name": "Google: Nano Banana (Gemini 2.5 Flash Image)",
    "raw_description": "Gemini 2.5 Flash Image, a.k.a. \"Nano Banana,\" is now generally available. It is a state of the art image generation model with contextual understanding. It is capable of image generation,...",
    "context_length": 32768,
    "pricing": {
      "input": 0.3,
      "output": 2.5
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "image",
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1759870431,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "qwen/qwen3-vl-30b-a3b-thinking",
    "canonical_slug": "qwen/qwen3-vl-30b-a3b-thinking",
    "name": "Qwen: Qwen3 VL 30B A3B Thinking",
    "raw_description": "Qwen3-VL-30B-A3B-Thinking is a multimodal model that unifies strong text generation with visual understanding for images and videos. Its Thinking variant enhances reasoning in STEM, math, and complex tasks. It excels...",
    "context_length": 262144,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 2.4
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1759794479,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-vl-30b-a3b-instruct",
    "canonical_slug": "qwen/qwen3-vl-30b-a3b-instruct",
    "name": "Qwen: Qwen3 VL 30B A3B Instruct",
    "raw_description": "Qwen3-VL-30B-A3B-Instruct is a multimodal model that unifies strong text generation with visual understanding for images and videos. Its Instruct variant optimizes instruction-following for general multimodal tasks. It excels in perception...",
    "context_length": 262144,
    "pricing": {
      "input": 0.13,
      "output": 0.52
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1759794476,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "openai/gpt-5-pro",
    "canonical_slug": "openai/gpt-5-pro-2025-10-06",
    "name": "OpenAI: GPT-5 Pro",
    "raw_description": "GPT-5 Pro is OpenAI’s most advanced model, offering major improvements in reasoning, code quality, and user experience. It is optimized for complex tasks that require step-by-step reasoning, instruction following, and...",
    "context_length": 400000,
    "pricing": {
      "input": 15,
      "output": 120
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1759776663,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-4.6",
    "canonical_slug": "z-ai/glm-4.6",
    "name": "Z.ai: GLM 4.6",
    "raw_description": "Compared with GLM-4.5, this generation brings several key improvements: Longer context window: The context window has been expanded from 128K to 200K tokens, enabling the model to handle more complex...",
    "context_length": 204800,
    "pricing": {
      "input": 0.5,
      "output": 2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1759235576,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-sonnet-4.5",
    "canonical_slug": "anthropic/claude-4.5-sonnet-20250929",
    "name": "Anthropic: Claude Sonnet 4.5",
    "raw_description": "Claude Sonnet 4.5 is Anthropic’s most advanced Sonnet model to date, optimized for real-world agents and coding workflows. It delivers state-of-the-art performance on coding benchmarks such as SWE-bench Verified, with...",
    "context_length": 1000000,
    "pricing": {
      "input": 3,
      "output": 15,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 6,
          "output": 22.5
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1759161676,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-v3.2-exp",
    "canonical_slug": "deepseek/deepseek-v3.2-exp",
    "name": "DeepSeek: DeepSeek V3.2 Exp",
    "raw_description": "DeepSeek-V3.2-Exp is an experimental large language model released by DeepSeek as an intermediate step between V3.1 and future architectures. It introduces DeepSeek Sparse Attention (DSA), a fine-grained sparse attention mechanism...",
    "context_length": 163840,
    "pricing": {
      "input": 0.27,
      "output": 0.41
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1759150481,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "thedrummer/cydonia-24b-v4.1",
    "canonical_slug": "thedrummer/cydonia-24b-v4.1",
    "name": "TheDrummer: Cydonia 24B V4.1",
    "raw_description": "Uncensored and creative writing model based on Mistral Small 3.2 24B with good recall, prompt adherence, and intelligence.",
    "context_length": 131072,
    "pricing": {
      "input": 0.3,
      "output": 0.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1758931878,
    "expiration_date": null,
    "model_author": "TheDrummer"
  },
  {
    "id": "relace/relace-apply-3",
    "canonical_slug": "relace/relace-apply-3",
    "name": "Relace: Relace Apply 3",
    "raw_description": "Relace Apply 3 is a specialized code-patching LLM that merges AI-suggested edits straight into your source files. It can apply updates from GPT-4o, Claude, and others into your files at...",
    "context_length": 256000,
    "pricing": {
      "input": 0.85,
      "output": 1.25
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "seed",
      "stop"
    ],
    "created": 1758891572,
    "expiration_date": null,
    "model_author": "Relace"
  },
  {
    "id": "google/gemini-2.5-flash-lite-preview-09-2025",
    "canonical_slug": "google/gemini-2.5-flash-lite-preview-09-2025",
    "name": "Google: Gemini 2.5 Flash Lite Preview 09-2025",
    "raw_description": "Gemini 2.5 Flash-Lite is a lightweight reasoning model in the Gemini 2.5 family, optimized for ultra-low latency and cost efficiency. It offers improved throughput, faster token generation, and better performance...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.1,
      "output": 0.4
    },
    "input_modalities": [
      "audio",
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1758819686,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "qwen/qwen3-vl-235b-a22b-thinking",
    "canonical_slug": "qwen/qwen3-vl-235b-a22b-thinking",
    "name": "Qwen: Qwen3 VL 235B A22B Thinking",
    "raw_description": "Qwen3-VL-235B-A22B Thinking is a multimodal model that unifies strong text generation with visual understanding across images and video. The Thinking model is optimized for multimodal reasoning in STEM and math....",
    "context_length": 131072,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 4
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1758668690,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-vl-235b-a22b-instruct",
    "canonical_slug": "qwen/qwen3-vl-235b-a22b-instruct",
    "name": "Qwen: Qwen3 VL 235B A22B Instruct",
    "raw_description": "Qwen3-VL-235B-A22B Instruct is an open-weight multimodal model that unifies strong text generation with visual understanding across images and video. The Instruct model targets general vision-language use (VQA, document parsing, chart/table...",
    "context_length": 262144,
    "pricing": {
      "input": 0.21,
      "output": 1.9
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1758668687,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen3-max",
    "canonical_slug": "qwen/qwen3-max",
    "name": "Qwen: Qwen3 Max",
    "raw_description": "Qwen3-Max is an updated release built on the Qwen3 series, offering major improvements in reasoning, instruction following, multilingual support, and long-tail knowledge coverage compared to the January 2025 version. It...",
    "context_length": 262144,
    "pricing": {
      "input": 0.78,
      "output": 3.9,
      "overrides": [
        {
          "min_prompt_tokens": 32000,
          "input": 1.56,
          "output": 7.8
        },
        {
          "min_prompt_tokens": 128000,
          "input": 1.95,
          "output": 9.75
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1758662808,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-coder-plus",
    "canonical_slug": "qwen/qwen3-coder-plus",
    "name": "Qwen: Qwen3 Coder Plus",
    "raw_description": "Qwen3 Coder Plus is Alibaba's proprietary version of the Open Source Qwen3 Coder 480B A35B. It is a powerful coding agent model specializing in autonomous programming via tool calling and...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.65,
      "output": 3.25,
      "overrides": [
        {
          "min_prompt_tokens": 32000,
          "input": 1.17,
          "output": 5.85
        },
        {
          "min_prompt_tokens": 128000,
          "input": 1.95,
          "output": 9.75
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1758662707,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5-codex",
    "canonical_slug": "openai/gpt-5-codex",
    "name": "OpenAI: GPT-5 Codex",
    "raw_description": "GPT-5-Codex is a specialized version of GPT-5 optimized for software engineering and coding workflows. It is designed for both interactive development sessions and long, independent execution of complex engineering tasks....",
    "context_length": 400000,
    "pricing": {
      "input": 1.25,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1758643403,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "deepseek/deepseek-v3.1-terminus",
    "canonical_slug": "deepseek/deepseek-v3.1-terminus",
    "name": "DeepSeek: DeepSeek V3.1 Terminus",
    "raw_description": "DeepSeek-V3.1 Terminus is an update to [DeepSeek V3.1](/deepseek/deepseek-chat-v3.1) that maintains the model's original capabilities while addressing issues reported by users, including language consistency and agent capabilities, further optimizing the model's...",
    "context_length": 163840,
    "pricing": {
      "input": 0.27,
      "output": 0.95
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1758548275,
    "expiration_date": 1786924800,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-coder-flash",
    "canonical_slug": "qwen/qwen3-coder-flash",
    "name": "Qwen: Qwen3 Coder Flash",
    "raw_description": "Qwen3 Coder Flash is Alibaba's fast and cost efficient version of their proprietary Qwen3 Coder Plus. It is a powerful coding agent model specializing in autonomous programming via tool calling...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.195,
      "output": 0.975,
      "overrides": [
        {
          "min_prompt_tokens": 32000,
          "input": 0.325,
          "output": 1.625
        },
        {
          "min_prompt_tokens": 128000,
          "input": 0.52,
          "output": 2.6
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1758115536,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen3-next-80b-a3b-thinking",
    "canonical_slug": "qwen/qwen3-next-80b-a3b-thinking-2509",
    "name": "Qwen: Qwen3 Next 80B A3B Thinking",
    "raw_description": "Qwen3-Next-80B-A3B-Thinking is a reasoning-first chat model in the Qwen3-Next line that outputs structured “thinking” traces by default. It’s designed for hard multi-step problems; math proofs, code synthesis/debugging, logic, and agentic...",
    "context_length": 262144,
    "pricing": {
      "input": 0.15,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1757612284,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-next-80b-a3b-instruct",
    "canonical_slug": "qwen/qwen3-next-80b-a3b-instruct-2509",
    "name": "Qwen: Qwen3 Next 80B A3B Instruct",
    "raw_description": "Qwen3-Next-80B-A3B-Instruct is an instruction-tuned chat model in the Qwen3-Next series optimized for fast, stable responses without “thinking” traces. It targets complex tasks across reasoning, code generation, knowledge QA, and multilingual...",
    "context_length": 262144,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 1.1
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1757612213,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen3-next-80b-a3b-instruct:free",
    "canonical_slug": "qwen/qwen3-next-80b-a3b-instruct-2509",
    "name": "Qwen: Qwen3 Next 80B A3B Instruct (free)",
    "raw_description": "Qwen3-Next-80B-A3B-Instruct is an instruction-tuned chat model in the Qwen3-Next series optimized for fast, stable responses without “thinking” traces. It targets complex tasks across reasoning, code generation, knowledge QA, and multilingual...",
    "context_length": 262144,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1757612213,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen-plus-2025-07-28",
    "canonical_slug": "qwen/qwen-plus-2025-07-28",
    "name": "Qwen: Qwen Plus 0728",
    "raw_description": "Qwen Plus 0728, based on the Qwen3 foundation model, is a 1 million context hybrid reasoning model with a balanced performance, speed, and cost combination.",
    "context_length": 1000000,
    "pricing": {
      "input": 0.26,
      "output": 0.78,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 0.78,
          "output": 2.34
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1757347599,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen-plus-2025-07-28:thinking",
    "canonical_slug": "qwen/qwen-plus-2025-07-28",
    "name": "Qwen: Qwen Plus 0728 (thinking)",
    "raw_description": "Qwen Plus 0728, based on the Qwen3 foundation model, is a 1 million context hybrid reasoning model with a balanced performance, speed, and cost combination.",
    "context_length": 1000000,
    "pricing": {
      "input": 0.26,
      "output": 0.78,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 0.78,
          "output": 2.34
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1757347599,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-nano-9b-v2",
    "canonical_slug": "nvidia/nemotron-nano-9b-v2",
    "name": "NVIDIA: Nemotron Nano 9B V2",
    "raw_description": "NVIDIA-Nemotron-Nano-9B-v2 is a large language model (LLM) trained from scratch by NVIDIA, and designed as a unified model for both reasoning and non-reasoning tasks. It responds to user queries and...",
    "context_length": 131072,
    "pricing": {
      "input": 0.04,
      "output": 0.16
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1757106807,
    "expiration_date": null,
    "model_author": "NVIDIA"
  },
  {
    "id": "nvidia/nemotron-nano-9b-v2:free",
    "canonical_slug": "nvidia/nemotron-nano-9b-v2",
    "name": "NVIDIA: Nemotron Nano 9B V2 (free)",
    "raw_description": "NVIDIA-Nemotron-Nano-9B-v2 is a large language model (LLM) trained from scratch by NVIDIA, and designed as a unified model for both reasoning and non-reasoning tasks. It responds to user queries and...",
    "context_length": 128000,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1757106807,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "moonshotai/kimi-k2-0905",
    "canonical_slug": "moonshotai/kimi-k2-0905",
    "name": "MoonshotAI: Kimi K2 0905",
    "raw_description": "Kimi K2 0905 is the September update of [Kimi K2 0711](moonshotai/kimi-k2). It is a large-scale Mixture-of-Experts (MoE) language model developed by Moonshot AI, featuring 1 trillion total parameters with 32...",
    "context_length": 262144,
    "pricing": {
      "input": 0.6,
      "output": 2.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1757021147,
    "expiration_date": null,
    "model_author": "MoonshotAI"
  },
  {
    "id": "qwen/qwen3-30b-a3b-thinking-2507",
    "canonical_slug": "qwen/qwen3-30b-a3b-thinking-2507",
    "name": "Qwen: Qwen3 30B A3B Thinking 2507",
    "raw_description": "Qwen3-30B-A3B-Thinking-2507 is a 30B parameter Mixture-of-Experts reasoning model optimized for complex tasks requiring extended multi-step thinking. The model is designed specifically for “thinking mode,” where internal reasoning traces are separated...",
    "context_length": 81920,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 2.4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1756399192,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "nousresearch/hermes-4-70b",
    "canonical_slug": "nousresearch/hermes-4-70b",
    "name": "Nous: Hermes 4 70B",
    "raw_description": "Hermes 4 70B is a hybrid reasoning model from Nous Research, built on Meta-Llama-3.1-70B. It introduces the same hybrid mode as the larger 405B release, allowing the model to either...",
    "context_length": 131072,
    "pricing": {
      "input": 0.13,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1756236182,
    "expiration_date": null,
    "model_author": "Nous",
    "reasoning_declared": true
  },
  {
    "id": "nousresearch/hermes-4-405b",
    "canonical_slug": "nousresearch/hermes-4-405b",
    "name": "Nous: Hermes 4 405B",
    "raw_description": "Hermes 4 is a large-scale reasoning model built on Meta-Llama-3.1-405B and released by Nous Research. It introduces a hybrid reasoning mode, where the model can choose to deliberate internally with...",
    "context_length": 131072,
    "pricing": {
      "input": 1,
      "output": 3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1756235463,
    "expiration_date": null,
    "model_author": "Nous",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-chat-v3.1",
    "canonical_slug": "deepseek/deepseek-chat-v3.1",
    "name": "DeepSeek: DeepSeek V3.1",
    "raw_description": "DeepSeek-V3.1 is a large hybrid reasoning model (671B parameters, 37B active) that supports both thinking and non-thinking modes via prompt templates. It extends the DeepSeek-V3 base with a two-phase long-context...",
    "context_length": 163840,
    "pricing": {
      "input": 0.25,
      "output": 0.95
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1755779628,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/mistral-medium-3.1",
    "canonical_slug": "mistralai/mistral-medium-3.1",
    "name": "Mistral: Mistral Medium 3.1",
    "raw_description": "Mistral Medium 3.1 is an updated version of Mistral Medium 3, which is a high-performance enterprise-grade language model designed to deliver frontier-level capabilities at significantly reduced operational cost. It balances...",
    "context_length": 131072,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 2
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1755095639,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "z-ai/glm-4.5v",
    "canonical_slug": "z-ai/glm-4.5v",
    "name": "Z.ai: GLM 4.5V",
    "raw_description": "GLM-4.5V is a vision-language foundation model for multimodal agent applications. Built on a Mixture-of-Experts (MoE) architecture with 106B parameters and 12B activated parameters, it achieves state-of-the-art results in video understanding,...",
    "context_length": 65536,
    "pricing": {
      "input": 0.6,
      "output": 1.7999999999999998
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1754922288,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "ai21/jamba-large-1.7",
    "canonical_slug": "ai21/jamba-large-1.7",
    "name": "AI21: Jamba Large 1.7",
    "raw_description": "Jamba Large 1.7 is the latest model in the Jamba open family, offering improvements in grounding, instruction-following, and overall efficiency. Built on a hybrid SSM-Transformer architecture with a 256K context...",
    "context_length": 256000,
    "pricing": {
      "input": 2,
      "output": 8
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1754669020,
    "expiration_date": null,
    "model_author": "AI21"
  },
  {
    "id": "openai/gpt-5-chat",
    "canonical_slug": "openai/gpt-5-chat-2025-08-07",
    "name": "OpenAI: GPT-5 Chat",
    "raw_description": "GPT-5 Chat is designed for advanced, natural, multimodal, and context-aware conversations for enterprise applications.",
    "context_length": 128000,
    "pricing": {
      "input": 1.25,
      "output": 10
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs"
    ],
    "created": 1754587837,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-5",
    "canonical_slug": "openai/gpt-5-2025-08-07",
    "name": "OpenAI: GPT-5",
    "raw_description": "GPT-5 is OpenAI’s most advanced model, offering major improvements in reasoning, code quality, and user experience. It is optimized for complex tasks that require step-by-step reasoning, instruction following, and accuracy...",
    "context_length": 400000,
    "pricing": {
      "input": 1.25,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1754587413,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5-mini",
    "canonical_slug": "openai/gpt-5-mini-2025-08-07",
    "name": "OpenAI: GPT-5 Mini",
    "raw_description": "GPT-5 Mini is a compact version of GPT-5, designed to handle lighter-weight reasoning tasks. It provides the same instruction-following and safety-tuning benefits as GPT-5, but with reduced latency and cost....",
    "context_length": 400000,
    "pricing": {
      "input": 0.25,
      "output": 2
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1754587407,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5-nano",
    "canonical_slug": "openai/gpt-5-nano-2025-08-07",
    "name": "OpenAI: GPT-5 Nano",
    "raw_description": "GPT-5-Nano is the smallest and fastest variant in the GPT-5 system, optimized for developer tools, rapid interactions, and ultra-low latency environments. While limited in reasoning depth compared to its larger...",
    "context_length": 400000,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_completion_tokens",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1754587402,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-oss-120b",
    "canonical_slug": "openai/gpt-oss-120b",
    "name": "OpenAI: gpt-oss-120b",
    "raw_description": "gpt-oss-120b is an open-weight, 117B-parameter Mixture-of-Experts (MoE) language model from OpenAI designed for high-reasoning, agentic, and general-purpose production use cases. It activates 5.1B parameters per forward pass and is optimized...",
    "context_length": 131072,
    "pricing": {
      "input": 0.03,
      "output": 0.16999999999999998
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1754414231,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-oss-120b:free",
    "canonical_slug": "openai/gpt-oss-120b",
    "name": "OpenAI: gpt-oss-120b (free)",
    "raw_description": "gpt-oss-120b is an open-weight, 117B-parameter Mixture-of-Experts (MoE) language model from OpenAI designed for high-reasoning, agentic, and general-purpose production use cases. It activates 5.1B parameters per forward pass and is optimized...",
    "context_length": 131072,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1754414231,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-oss-20b",
    "canonical_slug": "openai/gpt-oss-20b",
    "name": "OpenAI: gpt-oss-20b",
    "raw_description": "gpt-oss-20b is an open-weight 21B parameter model released by OpenAI under the Apache 2.0 license. It uses a Mixture-of-Experts (MoE) architecture with 3.6B active parameters per forward pass, optimized for...",
    "context_length": 131072,
    "pricing": {
      "input": 0.03,
      "output": 0.13
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1754414229,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-oss-20b:free",
    "canonical_slug": "openai/gpt-oss-20b",
    "name": "OpenAI: gpt-oss-20b (free)",
    "raw_description": "gpt-oss-20b is an open-weight 21B parameter model released by OpenAI under the Apache 2.0 license. It uses a Mixture-of-Experts (MoE) architecture with 3.6B active parameters per forward pass, optimized for...",
    "context_length": 131072,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1754414229,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.1",
    "canonical_slug": "anthropic/claude-4.1-opus-20250805",
    "name": "Anthropic: Claude Opus 4.1",
    "raw_description": "Claude Opus 4.1 is an updated version of Anthropic’s flagship model, offering improved performance in coding, reasoning, and agentic tasks. It achieves 74.5% on SWE-bench Verified and shows notable gains...",
    "context_length": 200000,
    "pricing": {
      "input": 15,
      "output": 75
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1754411591,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/codestral-2508",
    "canonical_slug": "mistralai/codestral-2508",
    "name": "Mistral: Codestral 2508",
    "raw_description": "Mistral's cutting-edge language model for coding released end of July 2025. Codestral specializes in low-latency, high-frequency tasks such as fill-in-the-middle (FIM), code correction and test generation.\n\n[Blog Post](https://mistral.ai/news/codestral-25-08)",
    "context_length": 256000,
    "pricing": {
      "input": 0.3,
      "output": 0.8999999999999999
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1754079630,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "qwen/qwen3-coder-30b-a3b-instruct",
    "canonical_slug": "qwen/qwen3-coder-30b-a3b-instruct",
    "name": "Qwen: Qwen3 Coder 30B A3B Instruct",
    "raw_description": "Qwen3-Coder-30B-A3B-Instruct is a 30.5B parameter Mixture-of-Experts (MoE) model with 128 experts (8 active per forward pass), designed for advanced code generation, repository-scale understanding, and agentic tool use. Built on the...",
    "context_length": 262144,
    "pricing": {
      "input": 0.07,
      "output": 0.28
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1753972379,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen3-30b-a3b-instruct-2507",
    "canonical_slug": "qwen/qwen3-30b-a3b-instruct-2507",
    "name": "Qwen: Qwen3 30B A3B Instruct 2507",
    "raw_description": "Qwen3-30B-A3B-Instruct-2507 is a 30.5B-parameter mixture-of-experts language model from Qwen, with 3.3B active parameters per inference. It operates in non-thinking mode and is designed for high-quality instruction following, multilingual understanding, and...",
    "context_length": 262144,
    "pricing": {
      "input": 0.04815,
      "output": 0.19305
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1753806965,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "z-ai/glm-4.5",
    "canonical_slug": "z-ai/glm-4.5",
    "name": "Z.ai: GLM 4.5",
    "raw_description": "GLM-4.5 is our latest flagship foundation model, purpose-built for agent-based applications. It leverages a Mixture-of-Experts (MoE) architecture and supports a context length of up to 128k tokens. GLM-4.5 delivers significantly...",
    "context_length": 131072,
    "pricing": {
      "input": 0.6,
      "output": 2.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1753471347,
    "expiration_date": 1798675200,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-4.5-air",
    "canonical_slug": "z-ai/glm-4.5-air",
    "name": "Z.ai: GLM 4.5 Air",
    "raw_description": "GLM-4.5-Air is the lightweight variant of our latest flagship model family, also purpose-built for agent-centric applications. Like GLM-4.5, it adopts the Mixture-of-Experts (MoE) architecture but with a more compact parameter...",
    "context_length": 131072,
    "pricing": {
      "input": 0.13,
      "output": 0.85
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1753471258,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-4.5-air:free",
    "canonical_slug": "z-ai/glm-4.5-air",
    "name": "Z.ai: GLM 4.5 Air (free)",
    "raw_description": "GLM-4.5-Air is the lightweight variant of our latest flagship model family, also purpose-built for agent-centric applications. Like GLM-4.5, it adopts the Mixture-of-Experts (MoE) architecture but with a more compact parameter...",
    "context_length": 131072,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1753471258,
    "expiration_date": null,
    "model_author": "Z.ai"
  },
  {
    "id": "qwen/qwen3-235b-a22b-thinking-2507",
    "canonical_slug": "qwen/qwen3-235b-a22b-thinking-2507",
    "name": "Qwen: Qwen3 235B A22B Thinking 2507",
    "raw_description": "Qwen3-235B-A22B-Thinking-2507 is a high-performance, open-weight Mixture-of-Experts (MoE) language model optimized for complex reasoning tasks. It activates 22B of its 235B parameters per forward pass and natively supports up to 262,144...",
    "context_length": 262144,
    "pricing": {
      "input": 0.22999999999999998,
      "output": 2.3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1753449557,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-4-32b",
    "canonical_slug": "z-ai/glm-4-32b-0414",
    "name": "Z.ai: GLM 4 32B ",
    "raw_description": "GLM 4 32B is a cost-effective foundation language model. It can efficiently perform complex tasks and has significantly enhanced capabilities in tool use, online search, and code-related intelligent tasks. It...",
    "context_length": 128000,
    "pricing": {
      "input": 0.1,
      "output": 0.1
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1753376617,
    "expiration_date": null,
    "model_author": "Z.ai"
  },
  {
    "id": "qwen/qwen3-coder",
    "canonical_slug": "qwen/qwen3-coder-480b-a35b-07-25",
    "name": "Qwen: Qwen3 Coder 480B A35B",
    "raw_description": "Qwen3-Coder-480B-A35B-Instruct is a Mixture-of-Experts (MoE) code generation model developed by the Qwen team. It is optimized for agentic coding tasks such as function calling, tool use, and long-context reasoning over...",
    "context_length": 262144,
    "pricing": {
      "input": 0.3,
      "output": 1
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1753230546,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen3-coder:free",
    "canonical_slug": "qwen/qwen3-coder-480b-a35b-07-25",
    "name": "Qwen: Qwen3 Coder 480B A35B (free)",
    "raw_description": "Qwen3-Coder-480B-A35B-Instruct is a Mixture-of-Experts (MoE) code generation model developed by the Qwen team. It is optimized for agentic coding tasks such as function calling, tool use, and long-context reasoning over...",
    "context_length": 1048576,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1753230546,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "bytedance/ui-tars-1.5-7b",
    "canonical_slug": "bytedance/ui-tars-1.5-7b",
    "name": "ByteDance: UI-TARS 7B ",
    "raw_description": "UI-TARS-1.5 is a multimodal vision-language agent optimized for GUI-based environments, including desktop interfaces, web browsers, mobile systems, and games. Built by ByteDance, it builds upon the UI-TARS framework with reinforcement...",
    "context_length": 128000,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1753205056,
    "expiration_date": null,
    "model_author": "ByteDance"
  },
  {
    "id": "google/gemini-2.5-flash-lite",
    "canonical_slug": "google/gemini-2.5-flash-lite",
    "name": "Google: Gemini 2.5 Flash Lite",
    "raw_description": "Gemini 2.5 Flash-Lite is a lightweight reasoning model in the Gemini 2.5 family, optimized for ultra-low latency and cost efficiency. It offers improved throughput, faster token generation, and better performance...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1753200276,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-235b-a22b-2507",
    "canonical_slug": "qwen/qwen3-235b-a22b-07-25",
    "name": "Qwen: Qwen3 235B A22B Instruct 2507",
    "raw_description": "Qwen3-235B-A22B-Instruct-2507 is a multilingual, instruction-tuned mixture-of-experts language model based on the Qwen3-235B architecture, with 22B active parameters per forward pass. It is optimized for general-purpose text generation, including instruction following,...",
    "context_length": 262144,
    "pricing": {
      "input": 0.09,
      "output": 0.55
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1753119555,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "switchpoint/router",
    "canonical_slug": "switchpoint/router",
    "name": "Switchpoint Router",
    "raw_description": "Switchpoint AI's router instantly analyzes your request and directs it to the optimal AI from an ever-evolving library. As the world of LLMs advances, our router gets smarter, ensuring you...",
    "context_length": 131072,
    "pricing": {
      "input": 0.85,
      "output": 3.4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1752272899,
    "expiration_date": null,
    "model_author": "switchpoint"
  },
  {
    "id": "moonshotai/kimi-k2",
    "canonical_slug": "moonshotai/kimi-k2",
    "name": "MoonshotAI: Kimi K2 0711",
    "raw_description": "Kimi K2 Instruct is a large-scale Mixture-of-Experts (MoE) language model developed by Moonshot AI, featuring 1 trillion total parameters with 32 billion active per forward pass. It is optimized for...",
    "context_length": 131072,
    "pricing": {
      "input": 0.5700000000000001,
      "output": 2.3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1752263252,
    "expiration_date": null,
    "model_author": "MoonshotAI"
  },
  {
    "id": "cognitivecomputations/dolphin-mistral-24b-venice-edition",
    "canonical_slug": "venice/uncensored",
    "name": "Venice: Uncensored",
    "raw_description": "Venice Uncensored Dolphin Mistral 24B Venice Edition is a fine-tuned variant of Mistral-Small-24B-Instruct-2501, developed by dphn.ai in collaboration with Venice.ai. This model is designed as an “uncensored” instruct-tuned LLM, preserving...",
    "context_length": 128000,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0.8999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1752094966,
    "expiration_date": null,
    "model_author": "Venice"
  },
  {
    "id": "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "canonical_slug": "venice/uncensored",
    "name": "Venice: Uncensored (free)",
    "raw_description": "Venice Uncensored Dolphin Mistral 24B Venice Edition is a fine-tuned variant of Mistral-Small-24B-Instruct-2501, developed by dphn.ai in collaboration with Venice.ai. This model is designed as an “uncensored” instruct-tuned LLM, preserving...",
    "context_length": 32768,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1752094966,
    "expiration_date": null,
    "model_author": "Venice"
  },
  {
    "id": "tencent/hunyuan-a13b-instruct",
    "canonical_slug": "tencent/hunyuan-a13b-instruct",
    "name": "Tencent: Hunyuan A13B Instruct",
    "raw_description": "Hunyuan-A13B is a 13B active parameter Mixture-of-Experts (MoE) language model developed by Tencent, with a total parameter count of 80B and support for reasoning via Chain-of-Thought. It offers competitive benchmark...",
    "context_length": 131072,
    "pricing": {
      "input": 0.14,
      "output": 0.5700000000000001
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1751987664,
    "expiration_date": null,
    "model_author": "Tencent",
    "reasoning_declared": true
  },
  {
    "id": "morph/morph-v3-large",
    "canonical_slug": "morph/morph-v3-large",
    "name": "Morph: Morph V3 Large",
    "raw_description": "Morph's high-accuracy apply model for complex code edits. ~4,500 tokens/sec with 98% accuracy for precise code transformations. The model requires the prompt to be in the following format: <instruction>{instruction}</instruction> <code>{initial_code}</code>...",
    "context_length": 262144,
    "pricing": {
      "input": 0.8999999999999999,
      "output": 1.9
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "logprobs",
      "max_tokens",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs"
    ],
    "created": 1751910858,
    "expiration_date": null,
    "model_author": "Morph"
  },
  {
    "id": "morph/morph-v3-fast",
    "canonical_slug": "morph/morph-v3-fast",
    "name": "Morph: Morph V3 Fast",
    "raw_description": "Morph's fastest apply model for code edits. ~10,500 tokens/sec with 96% accuracy for rapid code transformations. The model requires the prompt to be in the following format: <instruction>{instruction}</instruction> <code>{initial_code}</code> <update>{edit_snippet}</update>...",
    "context_length": 81920,
    "pricing": {
      "input": 0.7999999999999999,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature"
    ],
    "created": 1751910002,
    "expiration_date": null,
    "model_author": "Morph"
  },
  {
    "id": "baidu/ernie-4.5-vl-424b-a47b",
    "canonical_slug": "baidu/ernie-4.5-vl-424b-a47b",
    "name": "Baidu: ERNIE 4.5 VL 424B A47B ",
    "raw_description": "ERNIE-4.5-VL-424B-A47B is a multimodal Mixture-of-Experts (MoE) model from Baidu’s ERNIE 4.5 series, featuring 424B total parameters with 47B active per token. It is trained jointly on text and image data...",
    "context_length": 123000,
    "pricing": {
      "input": 0.42,
      "output": 1.25
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1751300903,
    "expiration_date": null,
    "model_author": "Baidu",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/mistral-small-3.2-24b-instruct",
    "canonical_slug": "mistralai/mistral-small-3.2-24b-instruct-2506",
    "name": "Mistral: Mistral Small 3.2 24B",
    "raw_description": "Mistral-Small-3.2-24B-Instruct-2506 is an updated 24B parameter model from Mistral optimized for instruction following, repetition reduction, and improved function calling. Compared to the 3.1 release, version 3.2 significantly improves accuracy on...",
    "context_length": 256000,
    "pricing": {
      "input": 0.09375,
      "output": 0.25
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1750443016,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "minimax/minimax-m1",
    "canonical_slug": "minimax/minimax-m1",
    "name": "MiniMax: MiniMax M1",
    "raw_description": "MiniMax-M1 is a large-scale, open-weight reasoning model designed for extended context and high-efficiency inference. It leverages a hybrid Mixture-of-Experts (MoE) architecture paired with a custom \"lightning attention\" mechanism, allowing it...",
    "context_length": 1000000,
    "pricing": {
      "input": 0.55,
      "output": 2.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1750200414,
    "expiration_date": null,
    "model_author": "MiniMax",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-2.5-flash",
    "canonical_slug": "google/gemini-2.5-flash",
    "name": "Google: Gemini 2.5 Flash",
    "raw_description": "Gemini 2.5 Flash is Google's state-of-the-art workhorse model, specifically designed for advanced reasoning, coding, mathematics, and scientific tasks. It includes built-in \"thinking\" capabilities, enabling it to provide responses with greater...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.3,
      "output": 2.5
    },
    "input_modalities": [
      "file",
      "image",
      "text",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1750172488,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-2.5-pro",
    "canonical_slug": "google/gemini-2.5-pro",
    "name": "Google: Gemini 2.5 Pro",
    "raw_description": "Gemini 2.5 Pro is Google’s state-of-the-art AI model designed for advanced reasoning, coding, mathematics, and scientific tasks. It employs “thinking” capabilities, enabling it to reason through responses with enhanced accuracy...",
    "context_length": 1048576,
    "pricing": {
      "input": 1.25,
      "output": 10,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2.5,
          "output": 15
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1750169544,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "openai/o3-pro",
    "canonical_slug": "openai/o3-pro-2025-06-10",
    "name": "OpenAI: o3 Pro",
    "raw_description": "The o-series of models are trained with reinforcement learning to think before they answer and perform complex reasoning. The o3-pro model uses more compute to think harder and provide consistently...",
    "context_length": 200000,
    "pricing": {
      "input": 20,
      "output": 80
    },
    "input_modalities": [
      "text",
      "file",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1749598352,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-2.5-pro-preview",
    "canonical_slug": "google/gemini-2.5-pro-preview-06-05",
    "name": "Google: Gemini 2.5 Pro Preview 06-05",
    "raw_description": "Gemini 2.5 Pro is Google’s state-of-the-art AI model designed for advanced reasoning, coding, mathematics, and scientific tasks. It employs “thinking” capabilities, enabling it to reason through responses with enhanced accuracy...",
    "context_length": 1048576,
    "pricing": {
      "input": 1.25,
      "output": 10,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2.5,
          "output": 15
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1749137257,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-r1-0528",
    "canonical_slug": "deepseek/deepseek-r1-0528",
    "name": "DeepSeek: R1 0528",
    "raw_description": "May 28th update to the [original DeepSeek R1](/deepseek/deepseek-r1) Performance on par with [OpenAI o1](/openai/o1), but open-sourced and with fully open reasoning tokens. It's 671B parameters in size, with 37B active...",
    "context_length": 163840,
    "pricing": {
      "input": 0.5,
      "output": 2.1500000000000004
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1748455170,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4",
    "canonical_slug": "anthropic/claude-4-opus-20250522",
    "name": "Anthropic: Claude Opus 4",
    "raw_description": "Claude Opus 4 is benchmarked as the world’s best coding model, at time of release, bringing sustained performance on complex, long-running tasks and agent workflows. It sets new benchmarks in...",
    "context_length": 200000,
    "pricing": {
      "input": 15,
      "output": 75
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1747931245,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-sonnet-4",
    "canonical_slug": "anthropic/claude-4-sonnet-20250522",
    "name": "Anthropic: Claude Sonnet 4",
    "raw_description": "Claude Sonnet 4 significantly enhances the capabilities of its predecessor, Sonnet 3.7, excelling in both coding and reasoning tasks with improved precision and controllability. Achieving state-of-the-art performance on SWE-bench (72.7%),...",
    "context_length": 1000000,
    "pricing": {
      "input": 3,
      "output": 15,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 6,
          "output": 22.5
        }
      ]
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1747930371,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "google/gemma-3n-e4b-it",
    "canonical_slug": "google/gemma-3n-e4b-it",
    "name": "Google: Gemma 3n 4B",
    "raw_description": "Gemma 3n E4B-it is optimized for efficient execution on mobile and low-resource devices, such as phones, laptops, and tablets. It supports multimodal inputs—including text, visual data, and audio—enabling diverse tasks...",
    "context_length": 32768,
    "pricing": {
      "input": 0.06,
      "output": 0.12
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1747776824,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "mistralai/mistral-medium-3",
    "canonical_slug": "mistralai/mistral-medium-3",
    "name": "Mistral: Mistral Medium 3",
    "raw_description": "Mistral Medium 3 is a high-performance enterprise-grade language model designed to deliver frontier-level capabilities at significantly reduced operational cost. It balances state-of-the-art reasoning and multimodal performance with 8× lower cost...",
    "context_length": 131072,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 2
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1746627341,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "google/gemini-2.5-pro-preview-05-06",
    "canonical_slug": "google/gemini-2.5-pro-preview-03-25",
    "name": "Google: Gemini 2.5 Pro Preview 05-06",
    "raw_description": "Gemini 2.5 Pro is Google’s state-of-the-art AI model designed for advanced reasoning, coding, mathematics, and scientific tasks. It employs “thinking” capabilities, enabling it to reason through responses with enhanced accuracy...",
    "context_length": 1048576,
    "pricing": {
      "input": 1.25,
      "output": 10,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2.5,
          "output": 15
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1746578513,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "arcee-ai/maestro-reasoning",
    "canonical_slug": "arcee-ai/maestro-reasoning",
    "name": "Arcee AI: Maestro Reasoning",
    "raw_description": "Maestro Reasoning is Arcee's flagship analysis model: a 32 B‑parameter derivative of Qwen 2.5‑32 B tuned with DPO and chain‑of‑thought RL for step‑by‑step logic. Compared to the earlier 7 B...",
    "context_length": 131072,
    "pricing": {
      "input": 0.9,
      "output": 3.3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1746481269,
    "expiration_date": null,
    "model_author": "Arcee AI"
  },
  {
    "id": "arcee-ai/virtuoso-large",
    "canonical_slug": "arcee-ai/virtuoso-large",
    "name": "Arcee AI: Virtuoso Large",
    "raw_description": "Virtuoso‑Large is Arcee's top‑tier general‑purpose LLM at 72 B parameters, tuned to tackle cross‑domain reasoning, creative writing and enterprise QA. Unlike many 70 B peers, it retains the 128 k...",
    "context_length": 131072,
    "pricing": {
      "input": 0.75,
      "output": 1.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1746478885,
    "expiration_date": null,
    "model_author": "Arcee AI"
  },
  {
    "id": "arcee-ai/coder-large",
    "canonical_slug": "arcee-ai/coder-large",
    "name": "Arcee AI: Coder Large",
    "raw_description": "Coder‑Large is a 32 B‑parameter offspring of Qwen 2.5‑Instruct that has been further trained on permissively‑licensed GitHub, CodeSearchNet and synthetic bug‑fix corpora. It supports a 32k context window, enabling multi‑file...",
    "context_length": 32768,
    "pricing": {
      "input": 0.5,
      "output": 0.8
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1746478663,
    "expiration_date": null,
    "model_author": "Arcee AI"
  },
  {
    "id": "meta-llama/llama-guard-4-12b",
    "canonical_slug": "meta-llama/llama-guard-4-12b",
    "name": "Meta: Llama Guard 4 12B",
    "raw_description": "Llama Guard 4 is a Llama 4 Scout-derived multimodal pretrained model, fine-tuned for content safety classification. Similar to previous versions, it can be used to classify content in both LLM...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.18,
      "output": 0.18
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1745975193,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "qwen/qwen3-30b-a3b",
    "canonical_slug": "qwen/qwen3-30b-a3b-04-28",
    "name": "Qwen: Qwen3 30B A3B",
    "raw_description": "Qwen3, the latest generation in the Qwen large language model series, features both dense and mixture-of-experts (MoE) architectures to excel in reasoning, multilingual support, and advanced agent tasks. Its unique...",
    "context_length": 131072,
    "pricing": {
      "input": 0.13,
      "output": 0.52
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1745878604,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-8b",
    "canonical_slug": "qwen/qwen3-8b-04-28",
    "name": "Qwen: Qwen3 8B",
    "raw_description": "Qwen3-8B is a dense 8.2B parameter causal language model from the Qwen3 series, designed for both reasoning-heavy tasks and efficient dialogue. It supports seamless switching between \"thinking\" mode for math,...",
    "context_length": 131072,
    "pricing": {
      "input": 0.117,
      "output": 0.45499999999999996
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1745876632,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-14b",
    "canonical_slug": "qwen/qwen3-14b-04-28",
    "name": "Qwen: Qwen3 14B",
    "raw_description": "Qwen3-14B is a dense 14.8B parameter causal language model from the Qwen3 series, designed for both complex reasoning and efficient dialogue. It supports seamless switching between a \"thinking\" mode for...",
    "context_length": 131072,
    "pricing": {
      "input": 0.12,
      "output": 0.24
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1745876478,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-32b",
    "canonical_slug": "qwen/qwen3-32b-04-28",
    "name": "Qwen: Qwen3 32B",
    "raw_description": "Qwen3-32B is a dense 32.8B parameter causal language model from the Qwen3 series, optimized for both complex reasoning and efficient dialogue. It supports seamless switching between a \"thinking\" mode for...",
    "context_length": 131072,
    "pricing": {
      "input": 0.08,
      "output": 0.28
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1745875945,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "qwen/qwen3-235b-a22b",
    "canonical_slug": "qwen/qwen3-235b-a22b-04-28",
    "name": "Qwen: Qwen3 235B A22B",
    "raw_description": "Qwen3-235B-A22B is a 235B parameter mixture-of-experts (MoE) model developed by Qwen, activating 22B parameters per forward pass. It supports seamless switching between a \"thinking\" mode for complex reasoning, math, and...",
    "context_length": 131072,
    "pricing": {
      "input": 0.45499999999999996,
      "output": 1.8199999999999998
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1745875757,
    "expiration_date": null,
    "model_author": "Qwen",
    "reasoning_declared": true
  },
  {
    "id": "openai/o4-mini-high",
    "canonical_slug": "openai/o4-mini-high-2025-04-16",
    "name": "OpenAI: o4 Mini High",
    "raw_description": "OpenAI o4-mini-high is the same model as [o4-mini](/openai/o4-mini) with reasoning_effort set to high. OpenAI o4-mini is a compact reasoning model in the o-series, optimized for fast, cost-efficient performance while retaining...",
    "context_length": 200000,
    "pricing": {
      "input": 1.1,
      "output": 4.4
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1744824212,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o3",
    "canonical_slug": "openai/o3-2025-04-16",
    "name": "OpenAI: o3",
    "raw_description": "o3 is a well-rounded and powerful model across domains. It sets a new standard for math, science, coding, and visual reasoning tasks. It also excels at technical writing and instruction-following....",
    "context_length": 200000,
    "pricing": {
      "input": 2,
      "output": 8
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1744823457,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o4-mini",
    "canonical_slug": "openai/o4-mini-2025-04-16",
    "name": "OpenAI: o4 Mini",
    "raw_description": "OpenAI o4-mini is a compact reasoning model in the o-series, optimized for fast, cost-efficient performance while retaining strong multimodal and agentic capabilities. It supports tool use and demonstrates competitive reasoning...",
    "context_length": 200000,
    "pricing": {
      "input": 1.1,
      "output": 4.4
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1744820942,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-4.1",
    "canonical_slug": "openai/gpt-4.1-2025-04-14",
    "name": "OpenAI: GPT-4.1",
    "raw_description": "GPT-4.1 is a flagship large language model optimized for advanced instruction following, real-world software engineering, and long-context reasoning. It supports a 1 million token context window and outperforms GPT-4o and...",
    "context_length": 1047576,
    "pricing": {
      "input": 2,
      "output": 8
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1744651385,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4.1-mini",
    "canonical_slug": "openai/gpt-4.1-mini-2025-04-14",
    "name": "OpenAI: GPT-4.1 Mini",
    "raw_description": "GPT-4.1 Mini is a mid-sized model delivering performance competitive with GPT-4o at substantially lower latency and cost. It retains a 1 million token context window and scores 45.1% on hard...",
    "context_length": 1047576,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 1.5999999999999999
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1744651381,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4.1-nano",
    "canonical_slug": "openai/gpt-4.1-nano-2025-04-14",
    "name": "OpenAI: GPT-4.1 Nano",
    "raw_description": "For tasks that demand low latency, GPT‑4.1 nano is the fastest and cheapest model in the GPT-4.1 series. It delivers exceptional performance at a small size with its 1 million...",
    "context_length": 1047576,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_completion_tokens",
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1744651369,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "meta-llama/llama-4-maverick",
    "canonical_slug": "meta-llama/llama-4-maverick-17b-128e-instruct",
    "name": "Meta: Llama 4 Maverick",
    "raw_description": "Llama 4 Maverick 17B Instruct (128E) is a high-capacity multimodal language model from Meta, built on a mixture-of-experts (MoE) architecture with 128 experts and 17 billion active parameters per forward...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0.7999999999999999
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama4",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1743881822,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "meta-llama/llama-4-scout",
    "canonical_slug": "meta-llama/llama-4-scout-17b-16e-instruct",
    "name": "Meta: Llama 4 Scout",
    "raw_description": "Llama 4 Scout 17B Instruct (16E) is a mixture-of-experts (MoE) language model developed by Meta, activating 17 billion parameters out of a total of 109B. It supports native multimodal input...",
    "context_length": 1310720,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.3
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama4",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1743881519,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "deepseek/deepseek-chat-v3-0324",
    "canonical_slug": "deepseek/deepseek-chat-v3-0324",
    "name": "DeepSeek: DeepSeek V3 0324",
    "raw_description": "DeepSeek V3, a 685B-parameter, mixture-of-experts model, is the latest iteration of the flagship chat model family from the DeepSeek team. It succeeds the [DeepSeek V3](/deepseek/deepseek-chat-v3) model and performs really well...",
    "context_length": 163840,
    "pricing": {
      "input": 0.27,
      "output": 1.12
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1742824755,
    "expiration_date": null,
    "model_author": "DeepSeek"
  },
  {
    "id": "openai/o1-pro",
    "canonical_slug": "openai/o1-pro",
    "name": "OpenAI: o1-pro",
    "raw_description": "The o1 series of models are trained with reinforcement learning to think before they answer and perform complex reasoning. The o1-pro model uses more compute to think harder and provide...",
    "context_length": 200000,
    "pricing": {
      "input": 150,
      "output": 600
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs"
    ],
    "created": 1742423211,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/mistral-small-3.1-24b-instruct",
    "canonical_slug": "mistralai/mistral-small-3.1-24b-instruct-2503",
    "name": "Mistral: Mistral Small 3.1 24B",
    "raw_description": "Mistral Small 3.1 24B Instruct is an upgraded variant of Mistral Small 3 (2501), featuring 24 billion parameters with advanced multimodal capabilities. It provides state-of-the-art performance in text-based reasoning and...",
    "context_length": 128000,
    "pricing": {
      "input": 0.351,
      "output": 0.5549999999999999
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1742238937,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "google/gemma-3-4b-it",
    "canonical_slug": "google/gemma-3-4b-it",
    "name": "Google: Gemma 3 4B",
    "raw_description": "Gemma 3 introduces multimodality, supporting vision-language input and text outputs. It handles context windows up to 128k tokens, understands over 140 languages, and offers improved math, reasoning, and chat capabilities,...",
    "context_length": 131072,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.09999999999999999
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1741905510,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "google/gemma-3-12b-it",
    "canonical_slug": "google/gemma-3-12b-it",
    "name": "Google: Gemma 3 12B",
    "raw_description": "Gemma 3 introduces multimodality, supporting vision-language input and text outputs. It handles context windows up to 128k tokens, understands over 140 languages, and offers improved math, reasoning, and chat capabilities,...",
    "context_length": 131072,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.15
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1741902625,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "cohere/command-a",
    "canonical_slug": "cohere/command-a-03-2025",
    "name": "Cohere: Command A",
    "raw_description": "Command A is an open-weights 111B parameter model with a 256k context window focused on delivering great performance across agentic, multilingual, and coding use cases. Compared to other leading proprietary...",
    "context_length": 256000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1741894342,
    "expiration_date": null,
    "model_author": "Cohere"
  },
  {
    "id": "openai/gpt-4o-mini-search-preview",
    "canonical_slug": "openai/gpt-4o-mini-search-preview-2025-03-11",
    "name": "OpenAI: GPT-4o-mini Search Preview",
    "raw_description": "GPT-4o mini Search Preview is a specialized model for web search in Chat Completions. It is trained to understand and execute web search queries.",
    "context_length": 128000,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "structured_outputs",
      "web_search_options"
    ],
    "created": 1741818122,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4o-search-preview",
    "canonical_slug": "openai/gpt-4o-search-preview-2025-03-11",
    "name": "OpenAI: GPT-4o Search Preview",
    "raw_description": "GPT-4o Search Previewis a specialized model for web search in Chat Completions. It is trained to understand and execute web search queries.",
    "context_length": 128000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "structured_outputs",
      "web_search_options"
    ],
    "created": 1741817949,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "rekaai/reka-flash-3",
    "canonical_slug": "rekaai/reka-flash-3",
    "name": "Reka Flash 3",
    "raw_description": "Reka Flash 3 is a general-purpose, instruction-tuned large language model with 21 billion parameters, developed by Reka. It excels at general chat, coding tasks, instruction-following, and function calling. Featuring a...",
    "context_length": 65536,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1741812813,
    "expiration_date": null,
    "model_author": "rekaai",
    "reasoning_declared": true
  },
  {
    "id": "google/gemma-3-27b-it",
    "canonical_slug": "google/gemma-3-27b-it",
    "name": "Google: Gemma 3 27B",
    "raw_description": "Gemma 3 introduces multimodality, supporting vision-language input and text outputs. It handles context windows up to 128k tokens, understands over 140 languages, and offers improved math, reasoning, and chat capabilities,...",
    "context_length": 262144,
    "pricing": {
      "input": 0.08,
      "output": 0.44999999999999996
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1741756359,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "thedrummer/skyfall-36b-v2",
    "canonical_slug": "thedrummer/skyfall-36b-v2",
    "name": "TheDrummer: Skyfall 36B V2",
    "raw_description": "Skyfall 36B v2 is an enhanced iteration of Mistral Small 2501, specifically fine-tuned for improved creativity, nuanced writing, role-playing, and coherent storytelling.",
    "context_length": 32768,
    "pricing": {
      "input": 0.55,
      "output": 0.7999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1741636566,
    "expiration_date": null,
    "model_author": "TheDrummer"
  },
  {
    "id": "perplexity/sonar-reasoning-pro",
    "canonical_slug": "perplexity/sonar-reasoning-pro",
    "name": "Perplexity: Sonar Reasoning Pro",
    "raw_description": "Note: Sonar Pro pricing includes Perplexity search pricing. See [details here](https://docs.perplexity.ai/guides/pricing#detailed-pricing-breakdown-for-sonar-reasoning-pro-and-sonar-pro) Sonar Reasoning Pro is a premier reasoning model powered by DeepSeek R1 with Chain of Thought (CoT). Designed for...",
    "context_length": 128000,
    "pricing": {
      "input": 2,
      "output": 8
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "temperature",
      "top_k",
      "top_p",
      "web_search_options"
    ],
    "created": 1741313308,
    "expiration_date": null,
    "model_author": "Perplexity",
    "reasoning_declared": true
  },
  {
    "id": "perplexity/sonar-pro",
    "canonical_slug": "perplexity/sonar-pro",
    "name": "Perplexity: Sonar Pro",
    "raw_description": "Note: Sonar Pro pricing includes Perplexity search pricing. See [details here](https://docs.perplexity.ai/guides/pricing#detailed-pricing-breakdown-for-sonar-reasoning-pro-and-sonar-pro) For enterprises seeking more advanced capabilities, the Sonar Pro API can handle in-depth, multi-step queries with added extensibility, like...",
    "context_length": 200000,
    "pricing": {
      "input": 3,
      "output": 15
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "temperature",
      "top_k",
      "top_p",
      "web_search_options"
    ],
    "created": 1741312423,
    "expiration_date": null,
    "model_author": "Perplexity"
  },
  {
    "id": "perplexity/sonar-deep-research",
    "canonical_slug": "perplexity/sonar-deep-research",
    "name": "Perplexity: Sonar Deep Research",
    "raw_description": "Sonar Deep Research is a research-focused model designed for multi-step retrieval, synthesis, and reasoning across complex topics. It autonomously searches, reads, and evaluates sources, refining its approach as it gathers...",
    "context_length": 128000,
    "pricing": {
      "input": 2,
      "output": 8
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "temperature",
      "top_k",
      "top_p",
      "web_search_options"
    ],
    "created": 1741311246,
    "expiration_date": null,
    "model_author": "Perplexity",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/mistral-saba",
    "canonical_slug": "mistralai/mistral-saba-2502",
    "name": "Mistral: Saba",
    "raw_description": "Mistral Saba is a 24B-parameter language model specifically designed for the Middle East and South Asia, delivering accurate and contextually relevant responses while maintaining efficient performance. Trained on curated regional...",
    "context_length": 32768,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0.6
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1739803239,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "meta-llama/llama-guard-3-8b",
    "canonical_slug": "meta-llama/llama-guard-3-8b",
    "name": "Llama Guard 3 8B",
    "raw_description": "Llama Guard 3 is a Llama-3.1-8B pretrained model, fine-tuned for content safety classification. Similar to previous versions, it can be used to classify content in both LLM inputs (prompt classification)...",
    "context_length": 131072,
    "pricing": {
      "input": 0.484,
      "output": 0.03
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1739401318,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "openai/o3-mini-high",
    "canonical_slug": "openai/o3-mini-high-2025-01-31",
    "name": "OpenAI: o3 Mini High",
    "raw_description": "OpenAI o3-mini-high is the same model as [o3-mini](/openai/o3-mini) with reasoning_effort set to high. o3-mini is a cost-efficient language model optimized for STEM reasoning tasks, particularly excelling in science, mathematics, and...",
    "context_length": 200000,
    "pricing": {
      "input": 1.1,
      "output": 4.4
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1739372611,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "aion-labs/aion-1.0",
    "canonical_slug": "aion-labs/aion-1.0",
    "name": "AionLabs: Aion-1.0",
    "raw_description": "Aion-1.0 is a multi-model system designed for high performance across various tasks, including reasoning and coding. It is built on DeepSeek-R1, augmented with additional models and techniques such as Tree...",
    "context_length": 131072,
    "pricing": {
      "input": 4,
      "output": 8
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "top_p"
    ],
    "created": 1738697557,
    "expiration_date": null,
    "model_author": "AionLabs"
  },
  {
    "id": "aion-labs/aion-1.0-mini",
    "canonical_slug": "aion-labs/aion-1.0-mini",
    "name": "AionLabs: Aion-1.0-Mini",
    "raw_description": "Aion-1.0-Mini 32B parameter model is a distilled version of the DeepSeek-R1 model, designed for strong performance in reasoning domains such as mathematics, coding, and logic. It is a modified variant...",
    "context_length": 131072,
    "pricing": {
      "input": 0.7,
      "output": 1.4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "temperature",
      "top_p"
    ],
    "created": 1738697107,
    "expiration_date": null,
    "model_author": "AionLabs"
  },
  {
    "id": "aion-labs/aion-rp-llama-3.1-8b",
    "canonical_slug": "aion-labs/aion-rp-llama-3.1-8b",
    "name": "AionLabs: Aion-RP 1.0 (8B)",
    "raw_description": "Aion-RP-Llama-3.1-8B ranks the highest in the character evaluation portion of the RPBench-Auto benchmark, a roleplaying-specific variant of Arena-Hard-Auto, where LLMs evaluate each other’s responses. It is a fine-tuned base model...",
    "context_length": 32768,
    "pricing": {
      "input": 0.7999999999999999,
      "output": 1.5999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1738696718,
    "expiration_date": null,
    "model_author": "AionLabs"
  },
  {
    "id": "qwen/qwen2.5-vl-72b-instruct",
    "canonical_slug": "qwen/qwen2.5-vl-72b-instruct",
    "name": "Qwen: Qwen2.5 VL 72B Instruct",
    "raw_description": "Qwen2.5-VL is proficient in recognizing common objects such as flowers, birds, fish, and insects. It is also highly capable of analyzing texts, charts, icons, graphics, and layouts within images.",
    "context_length": 128000,
    "pricing": {
      "input": 0.7999999999999999,
      "output": 1
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1738410311,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "qwen/qwen-plus",
    "canonical_slug": "qwen/qwen-plus-2025-01-25",
    "name": "Qwen: Qwen-Plus",
    "raw_description": "Qwen-Plus, based on the Qwen2.5 foundation model, is a 131K context model with a balanced performance, speed, and cost combination.",
    "context_length": 1000000,
    "pricing": {
      "input": 0.26,
      "output": 0.78,
      "overrides": [
        {
          "min_prompt_tokens": 256000,
          "input": 0.78,
          "output": 2.34
        }
      ]
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1738409840,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "openai/o3-mini",
    "canonical_slug": "openai/o3-mini-2025-01-31",
    "name": "OpenAI: o3 Mini",
    "raw_description": "OpenAI o3-mini is a cost-efficient language model optimized for STEM reasoning tasks, particularly excelling in science, mathematics, and coding. This model supports the `reasoning_effort` parameter, which can be set to...",
    "context_length": 200000,
    "pricing": {
      "input": 1.1,
      "output": 4.4
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1738351721,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "mistralai/mistral-small-24b-instruct-2501",
    "canonical_slug": "mistralai/mistral-small-24b-instruct-2501",
    "name": "Mistral: Mistral Small 3",
    "raw_description": "Mistral Small 3 is a 24B-parameter language model optimized for low-latency performance across common AI tasks. Released under the Apache 2.0 license, it features both pre-trained and instruction-tuned versions designed...",
    "context_length": 32768,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.08
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1738255409,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "deepseek/deepseek-r1-distill-qwen-32b",
    "canonical_slug": "deepseek/deepseek-r1-distill-qwen-32b",
    "name": "DeepSeek: R1 Distill Qwen 32B",
    "raw_description": "DeepSeek R1 Distill Qwen 32B is a distilled large language model based on [Qwen 2.5 32B](https://huggingface.co/Qwen/Qwen2.5-32B), using outputs from [DeepSeek R1](/deepseek/deepseek-r1). It outperforms OpenAI's o1-mini across various benchmarks, achieving new...",
    "context_length": 128000,
    "pricing": {
      "input": 0.29,
      "output": 0.29
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1738194830,
    "expiration_date": null,
    "model_author": "DeepSeek"
  },
  {
    "id": "perplexity/sonar",
    "canonical_slug": "perplexity/sonar",
    "name": "Perplexity: Sonar",
    "raw_description": "Sonar is lightweight, affordable, fast, and simple to use — now featuring citations and the ability to customize sources. It is designed for companies seeking to integrate lightweight question-and-answer features...",
    "context_length": 127072,
    "pricing": {
      "input": 1,
      "output": 1
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "temperature",
      "top_k",
      "top_p",
      "web_search_options"
    ],
    "created": 1738013808,
    "expiration_date": null,
    "model_author": "Perplexity"
  },
  {
    "id": "deepseek/deepseek-r1-distill-llama-70b",
    "canonical_slug": "deepseek/deepseek-r1-distill-llama-70b",
    "name": "DeepSeek: R1 Distill Llama 70B",
    "raw_description": "DeepSeek R1 Distill Llama 70B is a distilled large language model based on [Llama-3.3-70B-Instruct](/meta-llama/llama-3.3-70b-instruct), using outputs from [DeepSeek R1](/deepseek/deepseek-r1). The model combines advanced distillation techniques to achieve high performance across...",
    "context_length": 8192,
    "pricing": {
      "input": 0.7999999999999999,
      "output": 0.7999999999999999
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1737663169,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "deepseek/deepseek-r1",
    "canonical_slug": "deepseek/deepseek-r1",
    "name": "DeepSeek: R1",
    "raw_description": "DeepSeek R1 is here: Performance on par with [OpenAI o1](/openai/o1), but open-sourced and with fully open reasoning tokens. It's 671B parameters in size, with 37B active in an inference pass....",
    "context_length": 64000,
    "pricing": {
      "input": 0.7,
      "output": 2.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "max_tokens",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1737381095,
    "expiration_date": null,
    "model_author": "DeepSeek",
    "reasoning_declared": true
  },
  {
    "id": "minimax/minimax-01",
    "canonical_slug": "minimax/minimax-01",
    "name": "MiniMax: MiniMax-01",
    "raw_description": "MiniMax-01 is a combines MiniMax-Text-01 for text generation and MiniMax-VL-01 for image understanding. It has 456 billion parameters, with 45.9 billion parameters activated per inference, and can handle a context...",
    "context_length": 1000192,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 1.1
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "temperature",
      "top_p"
    ],
    "created": 1736915462,
    "expiration_date": null,
    "model_author": "MiniMax"
  },
  {
    "id": "microsoft/phi-4",
    "canonical_slug": "microsoft/phi-4",
    "name": "Microsoft: Phi 4",
    "raw_description": "[Microsoft Research](/microsoft) Phi-4 is designed to perform well in complex reasoning tasks and can operate efficiently in situations with limited memory or where quick responses are needed. At 14 billion...",
    "context_length": 16384,
    "pricing": {
      "input": 0.07,
      "output": 0.14
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1736489872,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "sao10k/l3.1-70b-hanami-x1",
    "canonical_slug": "sao10k/l3.1-70b-hanami-x1",
    "name": "Sao10K: Llama 3.1 70B Hanami x1",
    "raw_description": "This is [Sao10K](/sao10k)'s experiment over [Euryale v2.2](/sao10k/l3.1-euryale-70b).",
    "context_length": 16000,
    "pricing": {
      "input": 3,
      "output": 3
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1736302854,
    "expiration_date": null,
    "model_author": "Sao10K"
  },
  {
    "id": "deepseek/deepseek-chat",
    "canonical_slug": "deepseek/deepseek-chat-v3",
    "name": "DeepSeek: DeepSeek V3",
    "raw_description": "DeepSeek-V3 is the latest model from the DeepSeek team, building upon the instruction following and coding abilities of the previous versions. Pre-trained on nearly 15 trillion tokens, the reported evaluations...",
    "context_length": 163840,
    "pricing": {
      "input": 0.2574,
      "output": 1.0287
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "DeepSeek",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1735241320,
    "expiration_date": null,
    "model_author": "DeepSeek"
  },
  {
    "id": "sao10k/l3.3-euryale-70b",
    "canonical_slug": "sao10k/l3.3-euryale-70b-v2.3",
    "name": "Sao10K: Llama 3.3 Euryale 70B",
    "raw_description": "Euryale L3.3 70B is a model focused on creative roleplay from [Sao10k](https://ko-fi.com/sao10k). It is the successor of [Euryale L3 70B v2.2](/models/sao10k/l3-euryale-70b).",
    "context_length": 131072,
    "pricing": {
      "input": 0.65,
      "output": 0.75
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1734535928,
    "expiration_date": null,
    "model_author": "Sao10K"
  },
  {
    "id": "openai/o1",
    "canonical_slug": "openai/o1-2024-12-17",
    "name": "OpenAI: o1",
    "raw_description": "The latest and strongest model family from OpenAI, o1 is designed to spend more time thinking before responding. The o1 model series is trained with large-scale reinforcement learning to reason...",
    "context_length": 200000,
    "pricing": {
      "input": 15,
      "output": 60
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1734459999,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "cohere/command-r7b-12-2024",
    "canonical_slug": "cohere/command-r7b-12-2024",
    "name": "Cohere: Command R7B (12-2024)",
    "raw_description": "Command R7B (12-2024) is a small, fast update of the Command R+ model, delivered in December 2024. It excels at RAG, tool use, agents, and similar tasks requiring complex reasoning...",
    "context_length": 128000,
    "pricing": {
      "input": 0.0375,
      "output": 0.15
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Cohere",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1734158152,
    "expiration_date": null,
    "model_author": "Cohere"
  },
  {
    "id": "meta-llama/llama-3.3-70b-instruct",
    "canonical_slug": "meta-llama/llama-3.3-70b-instruct",
    "name": "Meta: Llama 3.3 70B Instruct",
    "raw_description": "The Meta Llama 3.3 multilingual large language model (LLM) is a pretrained and instruction tuned generative model in 70B (text in/text out). The Llama 3.3 instruction tuned text only model...",
    "context_length": 131072,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.32
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1733506137,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "meta-llama/llama-3.3-70b-instruct:free",
    "canonical_slug": "meta-llama/llama-3.3-70b-instruct",
    "name": "Meta: Llama 3.3 70B Instruct (free)",
    "raw_description": "The Meta Llama 3.3 multilingual large language model (LLM) is a pretrained and instruction tuned generative model in 70B (text in/text out). The Llama 3.3 instruction tuned text only model...",
    "context_length": 131072,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1733506137,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "amazon/nova-lite-v1",
    "canonical_slug": "amazon/nova-lite-v1",
    "name": "Amazon: Nova Lite 1.0",
    "raw_description": "Amazon Nova Lite 1.0 is a very low-cost multimodal model from Amazon that focused on fast processing of image, video, and text inputs to generate text output. Amazon Nova Lite...",
    "context_length": 300000,
    "pricing": {
      "input": 0.06,
      "output": 0.24
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Nova",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1733437363,
    "expiration_date": null,
    "model_author": "Amazon"
  },
  {
    "id": "amazon/nova-micro-v1",
    "canonical_slug": "amazon/nova-micro-v1",
    "name": "Amazon: Nova Micro 1.0",
    "raw_description": "Amazon Nova Micro 1.0 is a text-only model that delivers the lowest latency responses in the Amazon Nova family of models at a very low cost. With a context length...",
    "context_length": 128000,
    "pricing": {
      "input": 0.035,
      "output": 0.14
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Nova",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1733437237,
    "expiration_date": null,
    "model_author": "Amazon"
  },
  {
    "id": "amazon/nova-pro-v1",
    "canonical_slug": "amazon/nova-pro-v1",
    "name": "Amazon: Nova Pro 1.0",
    "raw_description": "Amazon Nova Pro 1.0 is a capable multimodal model from Amazon focused on providing a combination of accuracy, speed, and cost for a wide range of tasks. As of December...",
    "context_length": 300000,
    "pricing": {
      "input": 0.7999999999999999,
      "output": 3.1999999999999997
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Nova",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1733436303,
    "expiration_date": null,
    "model_author": "Amazon"
  },
  {
    "id": "openai/gpt-4o-2024-11-20",
    "canonical_slug": "openai/gpt-4o-2024-11-20",
    "name": "OpenAI: GPT-4o (2024-11-20)",
    "raw_description": "The 2024-11-20 version of GPT-4o offers a leveled-up creative writing ability with more natural, engaging, and tailored writing to improve relevance & readability. It’s also better at working with uploaded...",
    "context_length": 128000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1732127594,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "mistralai/mistral-large-2407",
    "canonical_slug": "mistralai/mistral-large-2407",
    "name": "Mistral Large 2407",
    "raw_description": "This is Mistral AI's flagship model, Mistral Large 2 (version mistral-large-2407). It's a proprietary weights-available model and excels at reasoning, code, JSON, chat, and more. Read the launch announcement [here](https://mistral.ai/news/mistral-large-2407/)....",
    "context_length": 131072,
    "pricing": {
      "input": 2,
      "output": 6
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1731978415,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "qwen/qwen-2.5-coder-32b-instruct",
    "canonical_slug": "qwen/qwen-2.5-coder-32b-instruct",
    "name": "Qwen2.5 Coder 32B Instruct",
    "raw_description": "Qwen2.5-Coder is the latest series of Code-Specific Qwen large language models (formerly known as CodeQwen). Qwen2.5-Coder brings the following improvements upon CodeQwen1.5: - Significantly improvements in **code generation**, **code reasoning**...",
    "context_length": 32768,
    "pricing": {
      "input": 0.66,
      "output": 1
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1731368400,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "thedrummer/unslopnemo-12b",
    "canonical_slug": "thedrummer/unslopnemo-12b",
    "name": "TheDrummer: UnslopNemo 12B",
    "raw_description": "UnslopNemo v4.1 is the latest addition from the creator of Rocinante, designed for adventure writing and role-play scenarios.",
    "context_length": 1024000,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1731103448,
    "expiration_date": null,
    "model_author": "TheDrummer"
  },
  {
    "id": "anthropic/claude-3.5-haiku",
    "canonical_slug": "anthropic/claude-3-5-haiku",
    "name": "Anthropic: Claude 3.5 Haiku",
    "raw_description": "Claude 3.5 Haiku features offers enhanced capabilities in speed, coding accuracy, and tool use. Engineered to excel in real-time applications, it delivers quick response times that are essential for dynamic...",
    "context_length": 200000,
    "pricing": {
      "input": 0.8,
      "output": 4
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1730678400,
    "expiration_date": null,
    "model_author": "Anthropic"
  },
  {
    "id": "anthracite-org/magnum-v4-72b",
    "canonical_slug": "anthracite-org/magnum-v4-72b",
    "name": "Magnum v4 72B",
    "raw_description": "This is a series of models designed to replicate the prose quality of the Claude 3 models, specifically Sonnet(https://openrouter.ai/anthropic/claude-3.5-sonnet) and Opus(https://openrouter.ai/anthropic/claude-3-opus).\n\nThe model is fine-tuned on top of [Qwen2.5 72B](https://openrouter.ai/qwen/qwen-2.5-72b-instruct).",
    "context_length": 32768,
    "pricing": {
      "input": 3,
      "output": 5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1729555200,
    "expiration_date": null,
    "model_author": "anthracite-org"
  },
  {
    "id": "qwen/qwen-2.5-7b-instruct",
    "canonical_slug": "qwen/qwen-2.5-7b-instruct",
    "name": "Qwen: Qwen2.5 7B Instruct",
    "raw_description": "Qwen2.5 7B is the latest series of Qwen large language models. Qwen2.5 brings the following improvements upon Qwen2: - Significantly more knowledge and has greatly improved capabilities in coding and...",
    "context_length": 32768,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1729036800,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "inflection/inflection-3-pi",
    "canonical_slug": "inflection/inflection-3-pi",
    "name": "Inflection: Inflection 3 Pi",
    "raw_description": "Inflection 3 Pi powers Inflection's [Pi](https://pi.ai) chatbot, including backstory, emotional intelligence, productivity, and safety. It has access to recent news, and excels in scenarios like customer support and roleplay. Pi...",
    "context_length": 8000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "top_p"
    ],
    "created": 1728604800,
    "expiration_date": null,
    "model_author": "Inflection"
  },
  {
    "id": "inflection/inflection-3-productivity",
    "canonical_slug": "inflection/inflection-3-productivity",
    "name": "Inflection: Inflection 3 Productivity",
    "raw_description": "Inflection 3 Productivity is optimized for following instructions. It is better for tasks requiring JSON output or precise adherence to provided guidelines. It has access to recent news. For emotional...",
    "context_length": 8000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "top_p"
    ],
    "created": 1728604800,
    "expiration_date": null,
    "model_author": "Inflection"
  },
  {
    "id": "thedrummer/rocinante-12b",
    "canonical_slug": "thedrummer/rocinante-12b",
    "name": "TheDrummer: Rocinante 12B",
    "raw_description": "Rocinante 12B is designed for engaging storytelling and rich prose. Early testers have reported: - Expanded vocabulary with unique and expressive word choices - Enhanced creativity for vivid narratives -...",
    "context_length": 65536,
    "pricing": {
      "input": 0.25,
      "output": 0.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1727654400,
    "expiration_date": null,
    "model_author": "TheDrummer"
  },
  {
    "id": "meta-llama/llama-3.2-11b-vision-instruct",
    "canonical_slug": "meta-llama/llama-3.2-11b-vision-instruct",
    "name": "Meta: Llama 3.2 11B Vision Instruct",
    "raw_description": "Llama 3.2 11B Vision is a multimodal model with 11 billion parameters, designed to handle tasks combining visual and textual data. It excels in tasks such as image captioning and...",
    "context_length": 131072,
    "pricing": {
      "input": 0.245,
      "output": 0.245
    },
    "input_modalities": [
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1727222400,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "meta-llama/llama-3.2-1b-instruct",
    "canonical_slug": "meta-llama/llama-3.2-1b-instruct",
    "name": "Meta: Llama 3.2 1B Instruct",
    "raw_description": "Llama 3.2 1B is a 1-billion-parameter language model focused on efficiently performing natural language tasks, such as summarization, dialogue, and multilingual text analysis. Its smaller size allows it to operate...",
    "context_length": 60000,
    "pricing": {
      "input": 0.027,
      "output": 0.201
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1727222400,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "meta-llama/llama-3.2-3b-instruct",
    "canonical_slug": "meta-llama/llama-3.2-3b-instruct",
    "name": "Meta: Llama 3.2 3B Instruct",
    "raw_description": "Llama 3.2 3B is a 3-billion-parameter multilingual large language model, optimized for advanced natural language processing tasks like dialogue generation, reasoning, and summarization. Designed with the latest transformer architecture, it...",
    "context_length": 131072,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.33
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1727222400,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "meta-llama/llama-3.2-3b-instruct:free",
    "canonical_slug": "meta-llama/llama-3.2-3b-instruct",
    "name": "Meta: Llama 3.2 3B Instruct (free)",
    "raw_description": "Llama 3.2 3B is a 3-billion-parameter multilingual large language model, optimized for advanced natural language processing tasks like dialogue generation, reasoning, and summarization. Designed with the latest transformer architecture, it...",
    "context_length": 131072,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1727222400,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "qwen/qwen-2.5-72b-instruct",
    "canonical_slug": "qwen/qwen-2.5-72b-instruct",
    "name": "Qwen2.5 72B Instruct",
    "raw_description": "Qwen2.5 72B is the latest series of Qwen large language models. Qwen2.5 brings the following improvements upon Qwen2: - Significantly more knowledge and has greatly improved capabilities in coding and...",
    "context_length": 32768,
    "pricing": {
      "input": 0.36,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Qwen",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1726704000,
    "expiration_date": null,
    "model_author": "Qwen"
  },
  {
    "id": "cohere/command-r-08-2024",
    "canonical_slug": "cohere/command-r-08-2024",
    "name": "Cohere: Command R (08-2024)",
    "raw_description": "command-r-08-2024 is an update of the [Command R](/models/cohere/command-r) with improved performance for multilingual retrieval-augmented generation (RAG) and tool use. More broadly, it is better at math, code and reasoning and...",
    "context_length": 128000,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Cohere",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1724976000,
    "expiration_date": null,
    "model_author": "Cohere"
  },
  {
    "id": "cohere/command-r-plus-08-2024",
    "canonical_slug": "cohere/command-r-plus-08-2024",
    "name": "Cohere: Command R+ (08-2024)",
    "raw_description": "command-r-plus-08-2024 is an update of the [Command R+](/models/cohere/command-r-plus) with roughly 50% higher throughput and 25% lower latencies as compared to the previous Command R+ version, while keeping the hardware footprint...",
    "context_length": 128000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Cohere",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1724976000,
    "expiration_date": null,
    "model_author": "Cohere"
  },
  {
    "id": "sao10k/l3.1-euryale-70b",
    "canonical_slug": "sao10k/l3.1-euryale-70b",
    "name": "Sao10K: Llama 3.1 Euryale 70B v2.2",
    "raw_description": "Euryale L3.1 70B v2.2 is a model focused on creative roleplay from [Sao10k](https://ko-fi.com/sao10k). It is the successor of [Euryale L3 70B v2.1](/models/sao10k/l3-euryale-70b).",
    "context_length": 131072,
    "pricing": {
      "input": 0.85,
      "output": 0.85
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1724803200,
    "expiration_date": null,
    "model_author": "Sao10K"
  },
  {
    "id": "nousresearch/hermes-3-llama-3.1-70b",
    "canonical_slug": "nousresearch/hermes-3-llama-3.1-70b",
    "name": "Nous: Hermes 3 70B Instruct",
    "raw_description": "Hermes 3 is a generalist language model with many improvements over [Hermes 2](/models/nousresearch/nous-hermes-2-mistral-7b-dpo), including advanced agentic capabilities, much better roleplaying, reasoning, multi-turn conversation, long context coherence, and improvements across the...",
    "context_length": 131072,
    "pricing": {
      "input": 0.7,
      "output": 0.7
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1723939200,
    "expiration_date": null,
    "model_author": "Nous"
  },
  {
    "id": "nousresearch/hermes-3-llama-3.1-405b",
    "canonical_slug": "nousresearch/hermes-3-llama-3.1-405b",
    "name": "Nous: Hermes 3 405B Instruct",
    "raw_description": "Hermes 3 is a generalist language model with many improvements over Hermes 2, including advanced agentic capabilities, much better roleplaying, reasoning, multi-turn conversation, long context coherence, and improvements across the...",
    "context_length": 131072,
    "pricing": {
      "input": 1,
      "output": 1
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1723766400,
    "expiration_date": null,
    "model_author": "Nous"
  },
  {
    "id": "nousresearch/hermes-3-llama-3.1-405b:free",
    "canonical_slug": "nousresearch/hermes-3-llama-3.1-405b",
    "name": "Nous: Hermes 3 405B Instruct (free)",
    "raw_description": "Hermes 3 is a generalist language model with many improvements over Hermes 2, including advanced agentic capabilities, much better roleplaying, reasoning, multi-turn conversation, long context coherence, and improvements across the...",
    "context_length": 131072,
    "pricing": {
      "input": 0,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1723766400,
    "expiration_date": null,
    "model_author": "Nous"
  },
  {
    "id": "sao10k/l3-lunaris-8b",
    "canonical_slug": "sao10k/l3-lunaris-8b",
    "name": "Sao10K: Llama 3 8B Lunaris",
    "raw_description": "Lunaris 8B is a versatile generalist and roleplaying model based on Llama 3. It's a strategic merge of multiple models, designed to balance creativity with improved logic and general knowledge....",
    "context_length": 8192,
    "pricing": {
      "input": 0.04,
      "output": 0.049999999999999996
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1723507200,
    "expiration_date": null,
    "model_author": "Sao10K"
  },
  {
    "id": "openai/gpt-4o-2024-08-06",
    "canonical_slug": "openai/gpt-4o-2024-08-06",
    "name": "OpenAI: GPT-4o (2024-08-06)",
    "raw_description": "The 2024-08-06 version of GPT-4o offers improved performance in structured outputs, with the ability to supply a JSON schema in the respone_format. Read more [here](https://openai.com/index/introducing-structured-outputs-in-the-api/). GPT-4o (\"o\" for \"omni\") is...",
    "context_length": 128000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1722902400,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "meta-llama/llama-3.1-70b-instruct",
    "canonical_slug": "meta-llama/llama-3.1-70b-instruct",
    "name": "Meta: Llama 3.1 70B Instruct",
    "raw_description": "Meta's latest class of model (Llama 3.1) launched with a variety of sizes & flavors. This 70B instruct-tuned version is optimized for high quality dialogue usecases. It has demonstrated strong...",
    "context_length": 131072,
    "pricing": {
      "input": 0.39999999999999997,
      "output": 0.39999999999999997
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1721692800,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "meta-llama/llama-3.1-8b-instruct",
    "canonical_slug": "meta-llama/llama-3.1-8b-instruct",
    "name": "Meta: Llama 3.1 8B Instruct",
    "raw_description": "Meta's latest class of model (Llama 3.1) launched with a variety of sizes & flavors. This 8B instruct-tuned version is fast and efficient. It has demonstrated strong performance compared to...",
    "context_length": 131072,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.08
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1721692800,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "mistralai/mistral-nemo",
    "canonical_slug": "mistralai/mistral-nemo",
    "name": "Mistral: Mistral Nemo",
    "raw_description": "A 12B parameter model with a 128k token context length built by Mistral in collaboration with NVIDIA. The model is multilingual, supporting English, French, German, Spanish, Italian, Portuguese, Chinese, Japanese,...",
    "context_length": 131072,
    "pricing": {
      "input": 0.019000000000000003,
      "output": 0.03
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1721347200,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "openai/gpt-4o-mini",
    "canonical_slug": "openai/gpt-4o-mini",
    "name": "OpenAI: GPT-4o-mini",
    "raw_description": "GPT-4o mini is OpenAI's newest model after [GPT-4 Omni](/models/openai/gpt-4o), supporting both text and image inputs with text outputs. As their most advanced small model, it is many multiples more affordable...",
    "context_length": 128000,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1721260800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4o-mini-2024-07-18",
    "canonical_slug": "openai/gpt-4o-mini-2024-07-18",
    "name": "OpenAI: GPT-4o-mini (2024-07-18)",
    "raw_description": "GPT-4o mini is OpenAI's newest model after [GPT-4 Omni](/models/openai/gpt-4o), supporting both text and image inputs with text outputs. As their most advanced small model, it is many multiples more affordable...",
    "context_length": 128000,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1721260800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "google/gemma-2-27b-it",
    "canonical_slug": "google/gemma-2-27b-it",
    "name": "Google: Gemma 2 27B",
    "raw_description": "Gemma 2 27B by Google is an open model built from the same research and technology used to create the [Gemini models](/models?q=gemini). Gemma models are well-suited for a variety of...",
    "context_length": 8192,
    "pricing": {
      "input": 0.65,
      "output": 0.65
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_p"
    ],
    "created": 1720828800,
    "expiration_date": null,
    "model_author": "Google"
  },
  {
    "id": "openai/gpt-4o",
    "canonical_slug": "openai/gpt-4o",
    "name": "OpenAI: GPT-4o",
    "raw_description": "GPT-4o (\"o\" for \"omni\") is OpenAI's latest AI model, supporting both text and image inputs with text outputs. It maintains the intelligence level of [GPT-4 Turbo](/models/openai/gpt-4-turbo) while being twice as...",
    "context_length": 128000,
    "pricing": {
      "input": 2.5,
      "output": 10
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1715558400,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4o-2024-05-13",
    "canonical_slug": "openai/gpt-4o-2024-05-13",
    "name": "OpenAI: GPT-4o (2024-05-13)",
    "raw_description": "GPT-4o (\"o\" for \"omni\") is OpenAI's latest AI model, supporting both text and image inputs with text outputs. It maintains the intelligence level of [GPT-4 Turbo](/models/openai/gpt-4-turbo) while being twice as...",
    "context_length": 128000,
    "pricing": {
      "input": 5,
      "output": 15
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1715558400,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "meta-llama/llama-3-70b-instruct",
    "canonical_slug": "meta-llama/llama-3-70b-instruct",
    "name": "Meta: Llama 3 70B Instruct",
    "raw_description": "Meta's latest class of model (Llama 3) launched with a variety of sizes & flavors. This 70B instruct-tuned version was optimized for high quality dialogue usecases. It has demonstrated strong...",
    "context_length": 8192,
    "pricing": {
      "input": 0.51,
      "output": 0.74
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1713398400,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "meta-llama/llama-3-8b-instruct",
    "canonical_slug": "meta-llama/llama-3-8b-instruct",
    "name": "Meta: Llama 3 8B Instruct",
    "raw_description": "Meta's latest class of model (Llama 3) launched with a variety of sizes & flavors. This 8B instruct-tuned version was optimized for high quality dialogue usecases. It has demonstrated strong...",
    "context_length": 8192,
    "pricing": {
      "input": 0.14,
      "output": 0.14
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama3",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1713398400,
    "expiration_date": null,
    "model_author": "Meta"
  },
  {
    "id": "mistralai/mixtral-8x22b-instruct",
    "canonical_slug": "mistralai/mixtral-8x22b-instruct",
    "name": "Mistral: Mixtral 8x22B Instruct",
    "raw_description": "Mistral's official instruct fine-tuned version of [Mixtral 8x22B](/models/mistralai/mixtral-8x22b). It uses 39B active parameters out of 141B, offering unparalleled cost efficiency for its size. Its strengths include: - strong math, coding,...",
    "context_length": 65536,
    "pricing": {
      "input": 2,
      "output": 6
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1713312000,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "microsoft/wizardlm-2-8x22b",
    "canonical_slug": "microsoft/wizardlm-2-8x22b",
    "name": "WizardLM-2 8x22B",
    "raw_description": "WizardLM-2 8x22B is Microsoft AI's most advanced Wizard model. It demonstrates highly competitive performance compared to leading proprietary models, and it consistently outperforms all existing state-of-the-art opensource models. It is...",
    "context_length": 65535,
    "pricing": {
      "input": 0.62,
      "output": 0.62
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "temperature",
      "top_k",
      "top_p"
    ],
    "created": 1713225600,
    "expiration_date": null,
    "model_author": "Microsoft"
  },
  {
    "id": "openai/gpt-4-turbo",
    "canonical_slug": "openai/gpt-4-turbo",
    "name": "OpenAI: GPT-4 Turbo",
    "raw_description": "The latest GPT-4 Turbo model with vision capabilities. Vision requests can now use JSON mode and function calling.\n\nTraining data: up to December 2023.",
    "context_length": 128000,
    "pricing": {
      "input": 10,
      "output": 30
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1712620800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "anthropic/claude-3-haiku",
    "canonical_slug": "anthropic/claude-3-haiku",
    "name": "Anthropic: Claude 3 Haiku",
    "raw_description": "Claude 3 Haiku is Anthropic's fastest and most compact model for\nnear-instant responsiveness. Quick and accurate targeted performance.\n\nSee the launch announcement and benchmark results [here](https://www.anthropic.com/news/claude-3-haiku)\n\n#multimodal",
    "context_length": 200000,
    "pricing": {
      "input": 0.25,
      "output": 1.25
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "max_tokens",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1710288000,
    "expiration_date": null,
    "model_author": "Anthropic"
  },
  {
    "id": "mistralai/mistral-large",
    "canonical_slug": "mistralai/mistral-large",
    "name": "Mistral Large",
    "raw_description": "This is Mistral AI's flagship model, Mistral Large 2 (version `mistral-large-2407`). It's a proprietary weights-available model and excels at reasoning, code, JSON, chat, and more. Read the launch announcement [here](https://mistral.ai/news/mistral-large-2407/)....",
    "context_length": 128000,
    "pricing": {
      "input": 2,
      "output": 6
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Mistral",
    "supported_parameters": [
      "frequency_penalty",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1708905600,
    "expiration_date": null,
    "model_author": "Mistral AI"
  },
  {
    "id": "openai/gpt-3.5-turbo-0613",
    "canonical_slug": "openai/gpt-3.5-turbo-0613",
    "name": "OpenAI: GPT-3.5 Turbo (older v0613)",
    "raw_description": "GPT-3.5 Turbo is OpenAI's fastest model. It can understand and generate natural language or code, and is optimized for chat and traditional completion tasks.\n\nTraining data up to Sep 2021.",
    "context_length": 4095,
    "pricing": {
      "input": 1,
      "output": 2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1706140800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4-turbo-preview",
    "canonical_slug": "openai/gpt-4-turbo-preview",
    "name": "OpenAI: GPT-4 Turbo Preview",
    "raw_description": "The preview GPT-4 model with improved instruction following, JSON mode, reproducible outputs, parallel function calling, and more. Training data: up to Dec 2023. **Note:** heavily rate limited by OpenAI while...",
    "context_length": 128000,
    "pricing": {
      "input": 10,
      "output": 30
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1706140800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openrouter/auto",
    "canonical_slug": "openrouter/auto",
    "name": "Auto Router",
    "raw_description": "Your prompt will be processed by a meta-model and routed to one of dozens of models (see below), optimizing for the best possible output. To see which model was used,...",
    "context_length": 2000000,
    "pricing": {
      "input": -1000000,
      "output": -1000000
    },
    "input_modalities": [
      "text",
      "image",
      "audio",
      "file",
      "video"
    ],
    "output_modalities": [
      "text",
      "image"
    ],
    "tokenizer": "Router",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "prediction",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1699401600,
    "expiration_date": null,
    "model_author": "OpenRouter"
  },
  {
    "id": "openai/gpt-3.5-turbo-instruct",
    "canonical_slug": "openai/gpt-3.5-turbo-instruct",
    "name": "OpenAI: GPT-3.5 Turbo Instruct",
    "raw_description": "This model is a variant of GPT-3.5 Turbo tuned for instructional prompts and omitting chat-related optimizations. Training data: up to Sep 2021.",
    "context_length": 4095,
    "pricing": {
      "input": 1.5,
      "output": 2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1695859200,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-3.5-turbo-16k",
    "canonical_slug": "openai/gpt-3.5-turbo-16k",
    "name": "OpenAI: GPT-3.5 Turbo 16k",
    "raw_description": "This model offers four times the context length of gpt-3.5-turbo, allowing it to support approximately 20 pages of text in a single request at a higher cost. Training data: up...",
    "context_length": 16385,
    "pricing": {
      "input": 3,
      "output": 4
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1693180800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "mancer/weaver",
    "canonical_slug": "mancer/weaver",
    "name": "Mancer: Weaver (alpha)",
    "raw_description": "An attempt to recreate Claude-style verbosity, but don't expect the same level of coherence or memory. Meant for use in roleplay/narrative situations.",
    "context_length": 8000,
    "pricing": {
      "input": 0.5,
      "output": 0.75
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama2",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1690934400,
    "expiration_date": null,
    "model_author": "Mancer"
  },
  {
    "id": "undi95/remm-slerp-l2-13b",
    "canonical_slug": "undi95/remm-slerp-l2-13b",
    "name": "ReMM SLERP 13B",
    "raw_description": "A recreation trial of the original MythoMax-L2-B13 but with updated models. #merge",
    "context_length": 6144,
    "pricing": {
      "input": 0.44999999999999996,
      "output": 0.65
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama2",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1689984000,
    "expiration_date": null,
    "model_author": "undi95"
  },
  {
    "id": "gryphe/mythomax-l2-13b",
    "canonical_slug": "gryphe/mythomax-l2-13b",
    "name": "MythoMax 13B",
    "raw_description": "One of the highest performing and most popular fine-tunes of Llama 2 13B, with rich descriptions and roleplay. #merge",
    "context_length": 8192,
    "pricing": {
      "input": 0.06,
      "output": 0.06
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Llama2",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "repetition_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_a",
      "top_k",
      "top_logprobs",
      "top_p"
    ],
    "created": 1688256000,
    "expiration_date": null,
    "model_author": "gryphe"
  },
  {
    "id": "openai/gpt-3.5-turbo",
    "canonical_slug": "openai/gpt-3.5-turbo",
    "name": "OpenAI: GPT-3.5 Turbo",
    "raw_description": "GPT-3.5 Turbo is OpenAI's fastest model. It can understand and generate natural language or code, and is optimized for chat and traditional completion tasks.\n\nTraining data up to Sep 2021.",
    "context_length": 16385,
    "pricing": {
      "input": 0.5,
      "output": 1.5
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1685232000,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4",
    "canonical_slug": "openai/gpt-4",
    "name": "OpenAI: GPT-4",
    "raw_description": "OpenAI's flagship model, GPT-4 is a large-scale multimodal language model capable of solving difficult problems with greater accuracy than previous models due to its broader general knowledge and advanced reasoning...",
    "context_length": 8191,
    "pricing": {
      "input": 30,
      "output": 60
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_completion_tokens",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1685232000,
    "expiration_date": null,
    "model_author": "OpenAI"
  }
];

const rawBatchServingVariants: RawCatalogModel[] = [
  {
    "id": "google/gemini-3.7-flash:batch",
    "canonical_slug": "google/gemini-3.7-flash-20260813",
    "name": "Google: Gemini 3.7 Flash (batch)",
    "raw_description": "Gemini 3.7 Flash is a multimodal model from Google for fast agentic workflows, coding, and complex multi-step reasoning. It is designed for tasks that require responsive performance and reliable multi-step...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.1875,
      "output": 0.9375
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1786640581,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-5:batch",
    "canonical_slug": "anthropic/claude-opus-5-20260723",
    "name": "Claude Opus 5 (batch)",
    "raw_description": "Claude Opus 5 is Anthropic’s flagship model for demanding reasoning, coding, and long-horizon agentic work. It is particularly strong at end-to-end software tasks, code review and bug finding, visual analysis...",
    "context_length": 1000000,
    "pricing": {
      "input": 2.5,
      "output": 12.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1784912544,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.6-flash:batch",
    "canonical_slug": "google/gemini-3.6-flash-20260721",
    "name": "Google: Gemini 3.6 Flash (batch)",
    "raw_description": "Gemini 3.6 Flash is a high-efficiency model from Google for coding, agentic workflows, and web and app development. It is designed to produce polished outputs with fewer unnecessary edits and...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.375,
      "output": 1.875
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1784646733,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.5-flash-lite:batch",
    "canonical_slug": "google/gemini-3.5-flash-lite-20260721",
    "name": "Google: Gemini 3.5 Flash Lite (batch)",
    "raw_description": "Gemini 3.5 Flash Lite is a high-efficiency model from Google with upgraded agentic capabilities. It is suited for subagents that execute focused tasks within complex, multi-agent workflows.",
    "context_length": 1048576,
    "pricing": {
      "input": 0.15,
      "output": 1.25
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1784646726,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "thinkingmachines/inkling:batch",
    "canonical_slug": "thinkingmachines/inkling-20260715",
    "name": "Thinking Machines: Inkling (batch)",
    "raw_description": "Inkling is an open-weight multimodal mixture-of-experts model from Thinking Machines Lab, with 41B active parameters out of 975B total. It is designed for general-purpose reasoning, coding, agentic and tool-use systems,...",
    "context_length": 524288,
    "pricing": {
      "input": 0.5,
      "output": 2.025
    },
    "input_modalities": [
      "text",
      "image",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "stop",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1784325956,
    "expiration_date": null,
    "model_author": "Thinkingmachines",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-luna-pro:batch",
    "canonical_slug": "openai/gpt-5.6-luna-pro-20260709",
    "name": "OpenAI: GPT-5.6 Luna Pro (batch)",
    "raw_description": "GPT-5.6 Luna Pro is the same underlying model as [GPT-5.6 Luna](https://openrouter.ai/openai/gpt-5.6-luna), served with `reasoning.mode` set to `pro` for higher-quality responses on complex tasks.\n\nLearn more in OpenAI's docs: https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode",
    "context_length": 1050000,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 0.19999999999999998,
          "output": 0.8999999999999999
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590867,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-luna:batch",
    "canonical_slug": "openai/gpt-5.6-luna-20260709",
    "name": "OpenAI: GPT-5.6 Luna (batch)",
    "raw_description": "GPT-5.6 Luna is a fast, cost-efficient model in OpenAI's GPT-5.6 series. It is suited for high-volume, latency-sensitive tasks such as chat, classification, and lightweight agentic workflows, providing capable reasoning for...",
    "context_length": 1050000,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 0.19999999999999998,
          "output": 0.8999999999999999
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590864,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-terra-pro:batch",
    "canonical_slug": "openai/gpt-5.6-terra-pro-20260709",
    "name": "OpenAI: GPT-5.6 Terra Pro (batch)",
    "raw_description": "GPT-5.6 Terra Pro is the same underlying model as [GPT-5.6 Terra](https://openrouter.ai/openai/gpt-5.6-terra), served with `reasoning.mode` set to `pro` for higher-quality responses on complex tasks.\n\nLearn more in OpenAI's docs: https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode",
    "context_length": 1050000,
    "pricing": {
      "input": 1,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 2,
          "output": 9
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590861,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-terra:batch",
    "canonical_slug": "openai/gpt-5.6-terra-20260709",
    "name": "OpenAI: GPT-5.6 Terra (batch)",
    "raw_description": "GPT-5.6 Terra is a balanced model in OpenAI's GPT-5.6 series, positioned between the flagship Sol tier and the cost-efficient Luna tier. It is suited for everyday coding, reasoning, and agentic...",
    "context_length": 1050000,
    "pricing": {
      "input": 1,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 2,
          "output": 9
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590857,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-sol-pro:batch",
    "canonical_slug": "openai/gpt-5.6-sol-pro-20260709",
    "name": "OpenAI: GPT-5.6 Sol Pro (batch)",
    "raw_description": "GPT-5.6 Sol Pro is the same underlying model as [GPT-5.6 Sol](https://openrouter.ai/openai/gpt-5.6-sol), served with `reasoning.mode` set to `pro` for higher-quality responses on complex tasks.\n\nLearn more in OpenAI's docs: https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode",
    "context_length": 1050000,
    "pricing": {
      "input": 2.5,
      "output": 15,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 5,
          "output": 22.5
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590854,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.6-sol:batch",
    "canonical_slug": "openai/gpt-5.6-sol-20260709",
    "name": "OpenAI: GPT-5.6 Sol (batch)",
    "raw_description": "GPT-5.6 Sol is the flagship model in OpenAI's GPT-5.6 series. It is suited for complex reasoning, coding, and agentic workflows, and is particularly strong at command-line and multi-step coding tasks...",
    "context_length": 1050000,
    "pricing": {
      "input": 2.5,
      "output": 15,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 5,
          "output": 22.5
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1783590850,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-sonnet-5:batch",
    "canonical_slug": "anthropic/claude-sonnet-5-20260630",
    "name": "Anthropic: Claude Sonnet 5 (batch)",
    "raw_description": "Sonnet 5 is Anthropic's most capable Sonnet-class model, with frontier performance across coding, agents, and professional work. It supports adaptive thinking with selectable reasoning effort levels (low, medium, high, max,...",
    "context_length": 1000000,
    "pricing": {
      "input": 1,
      "output": 5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1782843083,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "z-ai/glm-5.2:batch",
    "canonical_slug": "z-ai/glm-5.2-20260616",
    "name": "Z.ai: GLM 5.2 (batch)",
    "raw_description": "GLM 5.2 is a large-scale reasoning model from Z.ai. It supports text input and output with a 1M-token context window, and is suited for long-horizon agent workflows, project-level software engineering,...",
    "context_length": 512000,
    "pricing": {
      "input": 0.7,
      "output": 2.2
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1781631930,
    "expiration_date": null,
    "model_author": "Z.ai",
    "reasoning_declared": true
  },
  {
    "id": "moonshotai/kimi-k2.7-code:batch",
    "canonical_slug": "moonshotai/kimi-k2.7-code-20260612",
    "name": "MoonshotAI: Kimi K2.7 Code (batch)",
    "raw_description": "MoonshotAI: Kimi K2.7 Code is a coding-focused model in Moonshot AI's Kimi K2 family, built to complete end-to-end programming tasks reliably over long contexts. It uses a native multimodal mixture-of-experts...",
    "context_length": 262144,
    "pricing": {
      "input": 0.475,
      "output": 2
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1781266361,
    "expiration_date": null,
    "model_author": "MoonshotAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-fable-5:batch",
    "canonical_slug": "anthropic/claude-5-fable-20260609",
    "name": "Anthropic: Claude Fable 5 (batch)",
    "raw_description": "Claude Fable 5 is a Mythos-class model from Anthropic, built for autonomous knowledge work and coding. It supports text, image, and file inputs with text output, with reasoning support and...",
    "context_length": 1000000,
    "pricing": {
      "input": 5,
      "output": 25
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1781007515,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "nvidia/nemotron-3-ultra-550b-a55b:batch",
    "canonical_slug": "nvidia/nemotron-3-ultra-550b-a55b-20260604",
    "name": "NVIDIA: Nemotron 3 Ultra (batch)",
    "raw_description": "NVIDIA Nemotron 3 Ultra is an open frontier-reasoning and orchestration model from NVIDIA, with 55B active parameters out of 550B total (MoE). Built on a hybrid Transformer-Mamba mixture-of-experts architecture, it...",
    "context_length": 512288,
    "pricing": {
      "input": 0.3,
      "output": 1.7999999999999998
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "reasoning_effort",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1780551208,
    "expiration_date": null,
    "model_author": "NVIDIA",
    "reasoning_declared": true
  },
  {
    "id": "minimax/minimax-m3:batch",
    "canonical_slug": "minimax/minimax-m3-20260531",
    "name": "MiniMax: MiniMax M3 (batch)",
    "raw_description": "MiniMax-M3 is a multimodal foundation model from MiniMax. It supports text, image, and video inputs with text output, a 1M-token context window, and is suited for long-horizon agentic work, coding,...",
    "context_length": 524288,
    "pricing": {
      "input": 0.15,
      "output": 0.6
    },
    "input_modalities": [
      "text",
      "image",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "include_reasoning",
      "logit_bias",
      "max_tokens",
      "min_p",
      "presence_penalty",
      "reasoning",
      "repetition_penalty",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1780245374,
    "expiration_date": null,
    "model_author": "MiniMax",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.8:batch",
    "canonical_slug": "anthropic/claude-4.8-opus-20260528",
    "name": "Anthropic: Claude Opus 4.8 (batch)",
    "raw_description": "Claude Opus 4.8 is Anthropic's most capable generally available model in the Opus family. It supports text, image, and file inputs with text output, with reasoning support and a 1M-token...",
    "context_length": 1000000,
    "pricing": {
      "input": 2.5,
      "output": 12.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1779905091,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.5-flash:batch",
    "canonical_slug": "google/gemini-3.5-flash-20260519",
    "name": "Google: Gemini 3.5 Flash (batch)",
    "raw_description": "Gemini 3.5 Flash is Google's high-efficiency multimodal model, bringing near-Pro level coding and reasoning at Flash-tier cost and speed. It is highly optimized for coding proficiency and parallel agentic execution...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.75,
      "output": 4.5
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1779193800,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.1-flash-lite:batch",
    "canonical_slug": "google/gemini-3.1-flash-lite-20260507",
    "name": "Google: Gemini 3.1 Flash Lite (batch)",
    "raw_description": "Gemini 3.1 Flash Lite is Google’s GA high-efficiency multimodal model optimized for low-latency, high-volume workloads. It supports text, image, video, audio, and PDF inputs, and is designed for lightweight agentic...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.125,
      "output": 0.75
    },
    "input_modalities": [
      "text",
      "image",
      "video",
      "file",
      "audio"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1778168828,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.5-pro:batch",
    "canonical_slug": "openai/gpt-5.5-pro-20260423",
    "name": "OpenAI: GPT-5.5 Pro (batch)",
    "raw_description": "GPT-5.5 Pro is OpenAI’s high-capability model optimized for deep reasoning and accuracy on complex, high-stakes workloads. It features a 1M+ token context window (922K input, 128K output) with support for...",
    "context_length": 1050000,
    "pricing": {
      "input": 15,
      "output": 90,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 30,
          "output": 135
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1777051896,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.5:batch",
    "canonical_slug": "openai/gpt-5.5-20260423",
    "name": "OpenAI: GPT-5.5 (batch)",
    "raw_description": "GPT-5.5 is OpenAI’s frontier model designed for complex professional workloads, building on GPT-5.4 with stronger reasoning, higher reliability, and improved token efficiency on hard tasks. It features a 1M+ token...",
    "context_length": 1050000,
    "pricing": {
      "input": 2.5,
      "output": 15,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 5,
          "output": 22.5
        }
      ]
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1777051893,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.7:batch",
    "canonical_slug": "anthropic/claude-4.7-opus-20260416",
    "name": "Anthropic: Claude Opus 4.7 (batch)",
    "raw_description": "Opus 4.7 is the next generation of Anthropic's Opus family, built for long-running, asynchronous agents. Building on the coding and agentic strengths of Opus 4.6, it delivers stronger performance on...",
    "context_length": 1000000,
    "pricing": {
      "input": 2.5,
      "output": 12.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1776351100,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4-nano:batch",
    "canonical_slug": "openai/gpt-5.4-nano-20260317",
    "name": "OpenAI: GPT-5.4 Nano (batch)",
    "raw_description": "GPT-5.4 nano is the most lightweight and cost-efficient variant of the GPT-5.4 family, optimized for speed-critical and high-volume tasks. It supports text and image inputs and is designed for low-latency...",
    "context_length": 400000,
    "pricing": {
      "input": 0.09999999999999999,
      "output": 0.625
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1773748187,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4-mini:batch",
    "canonical_slug": "openai/gpt-5.4-mini-20260317",
    "name": "OpenAI: GPT-5.4 Mini (batch)",
    "raw_description": "GPT-5.4 mini brings the core capabilities of GPT-5.4 to a faster, more efficient model optimized for high-throughput workloads. It supports text and image inputs with strong performance across reasoning, coding,...",
    "context_length": 400000,
    "pricing": {
      "input": 0.375,
      "output": 2.25
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1773748178,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4-pro:batch",
    "canonical_slug": "openai/gpt-5.4-pro-20260305",
    "name": "OpenAI: GPT-5.4 Pro (batch)",
    "raw_description": "GPT-5.4 Pro is OpenAI's most advanced model, building on GPT-5.4's unified architecture with enhanced reasoning capabilities for complex, high-stakes tasks. It features a 1M+ token context window (922K input, 128K...",
    "context_length": 1050000,
    "pricing": {
      "input": 15,
      "output": 90,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 30,
          "output": 135
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1772734366,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.4:batch",
    "canonical_slug": "openai/gpt-5.4-20260305",
    "name": "OpenAI: GPT-5.4 (batch)",
    "raw_description": "GPT-5.4 is OpenAI’s latest frontier model, unifying the Codex and GPT lines into a single system. It features a 1M+ token context window (922K input, 128K output) with support for...",
    "context_length": 1050000,
    "pricing": {
      "input": 1.25,
      "output": 7.5,
      "overrides": [
        {
          "min_prompt_tokens": 272000,
          "input": 2.5,
          "output": 11.25
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1772734352,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3.1-pro-preview:batch",
    "canonical_slug": "google/gemini-3.1-pro-preview-20260219",
    "name": "Google: Gemini 3.1 Pro Preview (batch)",
    "raw_description": "Gemini 3.1 Pro Preview is Google’s frontier reasoning model, delivering enhanced software engineering performance, improved agentic reliability, and more efficient token usage across complex workflows. Building on the multimodal foundation...",
    "context_length": 1048576,
    "pricing": {
      "input": 1,
      "output": 6,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 2,
          "output": 9
        }
      ]
    },
    "input_modalities": [
      "audio",
      "file",
      "image",
      "text",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1771509627,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-sonnet-4.6:batch",
    "canonical_slug": "anthropic/claude-4.6-sonnet-20260217",
    "name": "Anthropic: Claude Sonnet 4.6 (batch)",
    "raw_description": "Sonnet 4.6 is Anthropic's most capable Sonnet-class model yet, with frontier performance across coding, agents, and professional work. It excels at iterative development, complex codebase navigation, end-to-end project management with...",
    "context_length": 1000000,
    "pricing": {
      "input": 1.5,
      "output": 7.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p",
      "verbosity"
    ],
    "created": 1771342990,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.6:batch",
    "canonical_slug": "anthropic/claude-4.6-opus-20260205",
    "name": "Anthropic: Claude Opus 4.6 (batch)",
    "raw_description": "Opus 4.6 is Anthropic’s strongest model for coding and long-running professional tasks. It is built for agents that operate across entire workflows rather than single prompts, making it especially effective...",
    "context_length": 1000000,
    "pricing": {
      "input": 2.5,
      "output": 12.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p",
      "verbosity"
    ],
    "created": 1770219050,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-3-flash-preview:batch",
    "canonical_slug": "google/gemini-3-flash-preview-20251217",
    "name": "Google: Gemini 3 Flash Preview (batch)",
    "raw_description": "Gemini 3 Flash Preview is a high speed, high value thinking model designed for agentic workflows, multi turn chat, and coding assistance. It delivers near Pro level reasoning and tool...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.25,
      "output": 1.5
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1765987078,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.2-pro:batch",
    "canonical_slug": "openai/gpt-5.2-pro-20251211",
    "name": "OpenAI: GPT-5.2 Pro (batch)",
    "raw_description": "GPT-5.2 Pro is OpenAI’s most advanced model, offering major improvements in agentic coding and long context performance over GPT-5 Pro. It is optimized for complex tasks that require step-by-step reasoning,...",
    "context_length": 400000,
    "pricing": {
      "input": 10.5,
      "output": 84
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1765389780,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.2:batch",
    "canonical_slug": "openai/gpt-5.2-20251211",
    "name": "OpenAI: GPT-5.2 (batch)",
    "raw_description": "GPT-5.2 is the latest frontier-grade model in the GPT-5 series, offering stronger agentic and long context perfomance compared to GPT-5.1. It uses adaptive reasoning to allocate computation dynamically, responding quickly...",
    "context_length": 400000,
    "pricing": {
      "input": 0.875,
      "output": 7
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1765389775,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.5:batch",
    "canonical_slug": "anthropic/claude-4.5-opus-20251124",
    "name": "Anthropic: Claude Opus 4.5 (batch)",
    "raw_description": "Claude Opus 4.5 is Anthropic’s frontier reasoning model optimized for complex software engineering, agentic workflows, and long-horizon computer use. It offers strong multimodal capabilities, competitive performance across real-world coding and...",
    "context_length": 200000,
    "pricing": {
      "input": 2.5,
      "output": 12.5
    },
    "input_modalities": [
      "file",
      "image",
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "verbosity"
    ],
    "created": 1764010580,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5.1:batch",
    "canonical_slug": "openai/gpt-5.1-20251113",
    "name": "OpenAI: GPT-5.1 (batch)",
    "raw_description": "GPT-5.1 is the latest frontier-grade model in the GPT-5 series, offering stronger general-purpose reasoning, improved instruction adherence, and a more natural conversational style compared to GPT-5. It uses adaptive reasoning...",
    "context_length": 400000,
    "pricing": {
      "input": 0.625,
      "output": 5
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1763060305,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/text-embedding-ada-002:batch",
    "canonical_slug": "openai/text-embedding-ada-002",
    "name": "OpenAI: Text Embedding Ada 002 (batch)",
    "raw_description": "text-embedding-ada-002 is OpenAI's legacy text embedding model.",
    "context_length": 8192,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761865798,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/text-embedding-3-large:batch",
    "canonical_slug": "openai/text-embedding-3-large",
    "name": "OpenAI: Text Embedding 3 Large (batch)",
    "raw_description": "text-embedding-3-large is OpenAI's most capable embedding model for both english and non-english tasks. Embeddings are a numerical representation of text that can be used to measure the relatedness between two...",
    "context_length": 8192,
    "pricing": {
      "input": 0.065,
      "output": 0
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "embeddings"
    ],
    "tokenizer": "Other",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "top_logprobs",
      "top_p"
    ],
    "created": 1761862866,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "anthropic/claude-haiku-4.5:batch",
    "canonical_slug": "anthropic/claude-4.5-haiku-20251001",
    "name": "Anthropic: Claude Haiku 4.5 (batch)",
    "raw_description": "Claude Haiku 4.5 is Anthropic’s fastest and most efficient model, delivering near-frontier intelligence at a fraction of the cost and latency of larger Claude models. Matching Claude Sonnet 4’s performance...",
    "context_length": 200000,
    "pricing": {
      "input": 0.5,
      "output": 2.5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1760547638,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5-pro:batch",
    "canonical_slug": "openai/gpt-5-pro-2025-10-06",
    "name": "OpenAI: GPT-5 Pro (batch)",
    "raw_description": "GPT-5 Pro is OpenAI’s most advanced model, offering major improvements in reasoning, code quality, and user experience. It is optimized for complex tasks that require step-by-step reasoning, instruction following, and...",
    "context_length": 400000,
    "pricing": {
      "input": 7.5,
      "output": 60
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1759776663,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-sonnet-4.5:batch",
    "canonical_slug": "anthropic/claude-4.5-sonnet-20250929",
    "name": "Anthropic: Claude Sonnet 4.5 (batch)",
    "raw_description": "Claude Sonnet 4.5 is Anthropic’s most advanced Sonnet model to date, optimized for real-world agents and coding workflows. It delivers state-of-the-art performance on coding benchmarks such as SWE-bench Verified, with...",
    "context_length": 1000000,
    "pricing": {
      "input": 1.5,
      "output": 7.5,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 3,
          "output": 11.25
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_k",
      "top_p"
    ],
    "created": 1759161676,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5-codex:batch",
    "canonical_slug": "openai/gpt-5-codex",
    "name": "OpenAI: GPT-5 Codex (batch)",
    "raw_description": "GPT-5-Codex is a specialized version of GPT-5 optimized for software engineering and coding workflows. It is designed for both interactive development sessions and long, independent execution of complex engineering tasks....",
    "context_length": 400000,
    "pricing": {
      "input": 0.625,
      "output": 5
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1758643403,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5:batch",
    "canonical_slug": "openai/gpt-5-2025-08-07",
    "name": "OpenAI: GPT-5 (batch)",
    "raw_description": "GPT-5 is OpenAI’s most advanced model, offering major improvements in reasoning, code quality, and user experience. It is optimized for complex tasks that require step-by-step reasoning, instruction following, and accuracy...",
    "context_length": 400000,
    "pricing": {
      "input": 0.625,
      "output": 5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1754587413,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5-mini:batch",
    "canonical_slug": "openai/gpt-5-mini-2025-08-07",
    "name": "OpenAI: GPT-5 Mini (batch)",
    "raw_description": "GPT-5 Mini is a compact version of GPT-5, designed to handle lighter-weight reasoning tasks. It provides the same instruction-following and safety-tuning benefits as GPT-5, but with reduced latency and cost....",
    "context_length": 400000,
    "pricing": {
      "input": 0.125,
      "output": 1
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1754587407,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-5-nano:batch",
    "canonical_slug": "openai/gpt-5-nano-2025-08-07",
    "name": "OpenAI: GPT-5 Nano (batch)",
    "raw_description": "GPT-5-Nano is the smallest and fastest variant in the GPT-5 system, optimized for developer tools, rapid interactions, and ultra-low latency environments. While limited in reasoning depth compared to its larger...",
    "context_length": 400000,
    "pricing": {
      "input": 0.024999999999999998,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1754587402,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "anthropic/claude-opus-4.1:batch",
    "canonical_slug": "anthropic/claude-4.1-opus-20250805",
    "name": "Anthropic: Claude Opus 4.1 (batch)",
    "raw_description": "Claude Opus 4.1 is an updated version of Anthropic’s flagship model, offering improved performance in coding, reasoning, and agentic tasks. It achieves 74.5% on SWE-bench Verified and shows notable gains...",
    "context_length": 200000,
    "pricing": {
      "input": 7.5,
      "output": 37.5
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Claude",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools"
    ],
    "created": 1754411591,
    "expiration_date": null,
    "model_author": "Anthropic",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-2.5-flash-lite:batch",
    "canonical_slug": "google/gemini-2.5-flash-lite",
    "name": "Google: Gemini 2.5 Flash Lite (batch)",
    "raw_description": "Gemini 2.5 Flash-Lite is a lightweight reasoning model in the Gemini 2.5 family, optimized for ultra-low latency and cost efficiency. It offers improved throughput, faster token generation, and better performance...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1753200276,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-2.5-flash:batch",
    "canonical_slug": "google/gemini-2.5-flash",
    "name": "Google: Gemini 2.5 Flash (batch)",
    "raw_description": "Gemini 2.5 Flash is Google's state-of-the-art workhorse model, specifically designed for advanced reasoning, coding, mathematics, and scientific tasks. It includes built-in \"thinking\" capabilities, enabling it to provide responses with greater...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.15,
      "output": 1.25
    },
    "input_modalities": [
      "file",
      "image",
      "text",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1750172488,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "google/gemini-2.5-pro:batch",
    "canonical_slug": "google/gemini-2.5-pro",
    "name": "Google: Gemini 2.5 Pro (batch)",
    "raw_description": "Gemini 2.5 Pro is Google’s state-of-the-art AI model designed for advanced reasoning, coding, mathematics, and scientific tasks. It employs “thinking” capabilities, enabling it to reason through responses with enhanced accuracy...",
    "context_length": 1048576,
    "pricing": {
      "input": 0.625,
      "output": 5,
      "overrides": [
        {
          "min_prompt_tokens": 200000,
          "input": 1.25,
          "output": 7.5
        }
      ]
    },
    "input_modalities": [
      "text",
      "image",
      "file",
      "audio",
      "video"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "Gemini",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1750169544,
    "expiration_date": null,
    "model_author": "Google",
    "reasoning_declared": true
  },
  {
    "id": "openai/o3-pro:batch",
    "canonical_slug": "openai/o3-pro-2025-06-10",
    "name": "OpenAI: o3 Pro (batch)",
    "raw_description": "The o-series of models are trained with reinforcement learning to think before they answer and perform complex reasoning. The o3-pro model uses more compute to think harder and provide consistently...",
    "context_length": 200000,
    "pricing": {
      "input": 10,
      "output": 40
    },
    "input_modalities": [
      "text",
      "file",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1749598352,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o4-mini-high:batch",
    "canonical_slug": "openai/o4-mini-high-2025-04-16",
    "name": "OpenAI: o4 Mini High (batch)",
    "raw_description": "OpenAI o4-mini-high is the same model as [o4-mini](/openai/o4-mini) with reasoning_effort set to high. OpenAI o4-mini is a compact reasoning model in the o-series, optimized for fast, cost-efficient performance while retaining...",
    "context_length": 200000,
    "pricing": {
      "input": 0.55,
      "output": 2.2
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1744824212,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o3:batch",
    "canonical_slug": "openai/o3-2025-04-16",
    "name": "OpenAI: o3 (batch)",
    "raw_description": "o3 is a well-rounded and powerful model across domains. It sets a new standard for math, science, coding, and visual reasoning tasks. It also excels at technical writing and instruction-following....",
    "context_length": 200000,
    "pricing": {
      "input": 1,
      "output": 4
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1744823457,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o4-mini:batch",
    "canonical_slug": "openai/o4-mini-2025-04-16",
    "name": "OpenAI: o4 Mini (batch)",
    "raw_description": "OpenAI o4-mini is a compact reasoning model in the o-series, optimized for fast, cost-efficient performance while retaining strong multimodal and agentic capabilities. It supports tool use and demonstrates competitive reasoning...",
    "context_length": 200000,
    "pricing": {
      "input": 0.55,
      "output": 2.2
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1744820942,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-4.1:batch",
    "canonical_slug": "openai/gpt-4.1-2025-04-14",
    "name": "OpenAI: GPT-4.1 (batch)",
    "raw_description": "GPT-4.1 is a flagship large language model optimized for advanced instruction following, real-world software engineering, and long-context reasoning. It supports a 1 million token context window and outperforms GPT-4o and...",
    "context_length": 1047576,
    "pricing": {
      "input": 1,
      "output": 4
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1744651385,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4.1-mini:batch",
    "canonical_slug": "openai/gpt-4.1-mini-2025-04-14",
    "name": "OpenAI: GPT-4.1 Mini (batch)",
    "raw_description": "GPT-4.1 Mini is a mid-sized model delivering performance competitive with GPT-4o at substantially lower latency and cost. It retains a 1 million token context window and scores 45.1% on hard...",
    "context_length": 1047576,
    "pricing": {
      "input": 0.19999999999999998,
      "output": 0.7999999999999999
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1744651381,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4.1-nano:batch",
    "canonical_slug": "openai/gpt-4.1-nano-2025-04-14",
    "name": "OpenAI: GPT-4.1 Nano (batch)",
    "raw_description": "For tasks that demand low latency, GPT‑4.1 nano is the fastest and cheapest model in the GPT-4.1 series. It delivers exceptional performance at a small size with its 1 million...",
    "context_length": 1047576,
    "pricing": {
      "input": 0.049999999999999996,
      "output": 0.19999999999999998
    },
    "input_modalities": [
      "image",
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "max_tokens",
      "response_format",
      "seed",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_p"
    ],
    "created": 1744651369,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/o1-pro:batch",
    "canonical_slug": "openai/o1-pro",
    "name": "OpenAI: o1-pro (batch)",
    "raw_description": "The o1 series of models are trained with reinforcement learning to think before they answer and perform complex reasoning. The o1-pro model uses more compute to think harder and provide...",
    "context_length": 200000,
    "pricing": {
      "input": 75,
      "output": 300
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs"
    ],
    "created": 1742423211,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o3-mini-high:batch",
    "canonical_slug": "openai/o3-mini-high-2025-01-31",
    "name": "OpenAI: o3 Mini High (batch)",
    "raw_description": "OpenAI o3-mini-high is the same model as [o3-mini](/openai/o3-mini) with reasoning_effort set to high. o3-mini is a cost-efficient language model optimized for STEM reasoning tasks, particularly excelling in science, mathematics, and...",
    "context_length": 200000,
    "pricing": {
      "input": 0.55,
      "output": 2.2
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "reasoning_effort",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1739372611,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o3-mini:batch",
    "canonical_slug": "openai/o3-mini-2025-01-31",
    "name": "OpenAI: o3 Mini (batch)",
    "raw_description": "OpenAI o3-mini is a cost-efficient language model optimized for STEM reasoning tasks, particularly excelling in science, mathematics, and coding. This model supports the `reasoning_effort` parameter, which can be set to...",
    "context_length": 200000,
    "pricing": {
      "input": 0.55,
      "output": 2.2
    },
    "input_modalities": [
      "text",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1738351721,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/o1:batch",
    "canonical_slug": "openai/o1-2024-12-17",
    "name": "OpenAI: o1 (batch)",
    "raw_description": "The latest and strongest model family from OpenAI, o1 is designed to spend more time thinking before responding. The o1 model series is trained with large-scale reinforcement learning to reason...",
    "context_length": 200000,
    "pricing": {
      "input": 7.5,
      "output": 30
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "include_reasoning",
      "max_tokens",
      "reasoning",
      "response_format",
      "seed",
      "structured_outputs",
      "tool_choice",
      "tools"
    ],
    "created": 1734459999,
    "expiration_date": null,
    "model_author": "OpenAI",
    "reasoning_declared": true
  },
  {
    "id": "openai/gpt-4o-mini:batch",
    "canonical_slug": "openai/gpt-4o-mini",
    "name": "OpenAI: GPT-4o-mini (batch)",
    "raw_description": "GPT-4o mini is OpenAI's newest model after [GPT-4 Omni](/models/openai/gpt-4o), supporting both text and image inputs with text outputs. As their most advanced small model, it is many multiples more affordable...",
    "context_length": 128000,
    "pricing": {
      "input": 0.075,
      "output": 0.3
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1721260800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4o:batch",
    "canonical_slug": "openai/gpt-4o",
    "name": "OpenAI: GPT-4o (batch)",
    "raw_description": "GPT-4o (\"o\" for \"omni\") is OpenAI's latest AI model, supporting both text and image inputs with text outputs. It maintains the intelligence level of [GPT-4 Turbo](/models/openai/gpt-4-turbo) while being twice as...",
    "context_length": 128000,
    "pricing": {
      "input": 1.25,
      "output": 5
    },
    "input_modalities": [
      "text",
      "image",
      "file"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "prediction",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p",
      "web_search_options"
    ],
    "created": 1715558400,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-4-turbo:batch",
    "canonical_slug": "openai/gpt-4-turbo",
    "name": "OpenAI: GPT-4 Turbo (batch)",
    "raw_description": "The latest GPT-4 Turbo model with vision capabilities. Vision requests can now use JSON mode and function calling.\n\nTraining data: up to December 2023.",
    "context_length": 128000,
    "pricing": {
      "input": 5,
      "output": 15
    },
    "input_modalities": [
      "text",
      "image"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1712620800,
    "expiration_date": null,
    "model_author": "OpenAI"
  },
  {
    "id": "openai/gpt-3.5-turbo:batch",
    "canonical_slug": "openai/gpt-3.5-turbo",
    "name": "OpenAI: GPT-3.5 Turbo (batch)",
    "raw_description": "GPT-3.5 Turbo is OpenAI's fastest model. It can understand and generate natural language or code, and is optimized for chat and traditional completion tasks.\n\nTraining data up to Sep 2021.",
    "context_length": 16385,
    "pricing": {
      "input": 0.25,
      "output": 0.75
    },
    "input_modalities": [
      "text"
    ],
    "output_modalities": [
      "text"
    ],
    "tokenizer": "GPT",
    "supported_parameters": [
      "frequency_penalty",
      "logit_bias",
      "logprobs",
      "max_tokens",
      "presence_penalty",
      "response_format",
      "seed",
      "stop",
      "structured_outputs",
      "temperature",
      "tool_choice",
      "tools",
      "top_logprobs",
      "top_p"
    ],
    "created": 1685232000,
    "expiration_date": null,
    "model_author": "OpenAI"
  }
];

// Generated by scripts/update-openrouter-models.mjs. Entries remain callable
// but carry a warning because they are absent from the current live catalog.
const uncertainCatalogModelIds = new Set<string>([
  "aion-labs/aion-1.0",
  "aion-labs/aion-1.0-mini",
  "anthropic/claude-3.5-haiku",
  "anthropic/claude-opus-4.6-fast",
  "arcee-ai/coder-large",
  "arcee-ai/maestro-reasoning",
  "arcee-ai/trinity-mini",
  "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
  "deepseek/deepseek-r1-distill-qwen-32b",
  "essentialai/rnj-1-instruct",
  "google/gemini-2.5-flash-lite-preview-09-2025",
  "inclusionai/ling-3.0-flash:free",
  "inflection/inflection-3-pi",
  "inflection/inflection-3-productivity",
  "liquid/lfm-2-24b-a2b",
  "liquid/lfm-2.5-1.2b-instruct:free",
  "liquid/lfm-2.5-1.2b-thinking:free",
  "meta-llama/llama-3-70b-instruct",
  "meta-llama/llama-3-8b-instruct",
  "meta-llama/llama-3.2-11b-vision-instruct",
  "meta-llama/llama-3.2-3b-instruct:free",
  "meta-llama/llama-3.3-70b-instruct:free",
  "meta-llama/llama-guard-3-8b",
  "microsoft/phi-4-mini-instruct",
  "mistralai/devstral-2512",
  "moonshotai/kimi-k2.6:free",
  "nex-agi/deepseek-v3.1-nex-n1",
  "nousresearch/hermes-3-llama-3.1-405b:free",
  "nvidia/llama-3.3-nemotron-super-49b-v1.5",
  "nvidia/nemotron-nano-9b-v2",
  "openai/gpt-4o-mini-search-preview",
  "openai/gpt-4o-search-preview",
  "openai/gpt-5-chat",
  "openai/gpt-5-codex",
  "openai/gpt-5.1-chat",
  "openai/gpt-oss-120b:free",
  "openai/o3-deep-research",
  "openai/o4-mini-deep-research",
  "openrouter/owl-alpha",
  "poolside/laguna-xs.2:free",
  "prime-intellect/intellect-3",
  "qwen/qwen3-coder:free",
  "qwen/qwen3-next-80b-a3b-instruct:free",
  "sao10k/l3.1-70b-hanami-x1",
  "sourceful/riverflow-v2-fast-preview",
  "sourceful/riverflow-v2-max-preview",
  "sourceful/riverflow-v2-standard-preview",
  "sourceful/riverflow-v2.5-fast:free",
  "sourceful/riverflow-v2.5-pro:free",
  "switchpoint/router",
  "xiaomi/mimo-v2-flash",
  "z-ai/glm-4-32b",
  "z-ai/glm-4.5-air:free",
  "zyphra/zonos-v0.1-hybrid",
  "zyphra/zonos-v0.1-transformer"
]);

const CURRENT_TIME_SECONDS = Math.floor(Date.now() / 1000);

function roundMoney(value: number) {
  return Number(value.toFixed(2));
}

function toCny(usdPerMillion: number) {
  return roundMoney(usdPerMillion * USD_TO_CNY);
}

function pricingOverrides(raw: RawCatalogModel): TokenPricingOverride[] {
  return (raw.pricing.overrides ?? []).map((override) => ({
    min_prompt_tokens: override.min_prompt_tokens,
    pricing: {
      input: override.input,
      output: override.output,
    },
    price_cny: {
      input: toCny(override.input),
      output: toCny(override.output),
    },
  }));
}

function pricingTimeWindows(
  raw: RawCatalogModel,
): TimeWindowPricingOverride[] {
  return (raw.pricing.time_overrides ?? []).map((override) => ({
    utc_start: override.utc_start,
    utc_end: override.utc_end,
    pricing: {
      input: override.input,
      output: override.output,
    },
    price_cny: {
      input: toCny(override.input),
      output: toCny(override.output),
    },
  }));
}

function getPricingBasis(raw: RawCatalogModel): PricingBasis {
  if (raw.id.endsWith(":free") || raw.id === "openrouter/free") return "free";
  if (
    raw.output_modalities.some((modality) =>
      ["image", "video", "audio", "speech"].includes(modality),
    )
  ) {
    return "media";
  }
  if (
    raw.output_modalities.some((modality) =>
      ["embeddings", "rerank"].includes(modality),
    )
  ) {
    return "request";
  }
  if (raw.pricing.input < 0 || raw.pricing.output < 0) return "dynamic";
  if (raw.pricing.input !== 0 || raw.pricing.output !== 0) return "token";
  return "dynamic";
}

function getPricingStatus(pricingBasis: PricingBasis): PricingStatus {
  if (pricingBasis === "free") return "free";
  if (pricingBasis === "token") return "fixed";
  return "dynamic";
}

function getPricingTier(
  inputUsdPerMillion: number,
  pricingStatus: PricingStatus,
): PricingTier {
  if (pricingStatus === "free") return "free";
  if (pricingStatus === "dynamic") return "dynamic";
  const inputCny = inputUsdPerMillion * USD_TO_CNY;
  if (inputCny <= 1) return "low";
  if (inputCny <= 5) return "medium";
  return "high";
}

function normalizeProvider(author: string): Provider {
  const normalized = author.toLowerCase();
  if (normalized.includes("openai")) return "OpenAI";
  if (normalized.includes("anthropic")) return "Anthropic";
  if (normalized.includes("google") || normalized.includes("gemma")) return "Google";
  if (normalized.includes("meta") || normalized.includes("llama")) return "Meta";
  if (normalized.includes("deepseek")) return "DeepSeek";
  if (normalized.includes("mistral")) return "Mistral AI";
  if (normalized.includes("microsoft") || normalized.includes("phi")) return "Microsoft";
  return author.trim() || "其他";
}

function inferCapabilities(raw: RawCatalogModel): Capability[] {
  const capabilities = new Set<Capability>();
  const haystack = (raw.id + " " + raw.name + " " + raw.raw_description).toLowerCase();
  if (raw.input_modalities.includes("text") || raw.output_modalities.includes("text")) capabilities.add("text");
  if (raw.input_modalities.includes("image") || raw.output_modalities.includes("image")) capabilities.add("image");
  if (raw.input_modalities.includes("audio") || raw.output_modalities.includes("audio") || raw.output_modalities.includes("speech") || raw.output_modalities.includes("transcription")) capabilities.add("audio");
  if (raw.input_modalities.includes("video") || raw.output_modalities.includes("video")) capabilities.add("video");
  if (
    raw.output_modalities.includes("text") &&
    (haystack.includes("code") ||
      haystack.includes("coder") ||
      haystack.includes("codex") ||
      haystack.includes("programming"))
  ) capabilities.add("code");
  if (raw.supported_parameters.includes("tools") || raw.supported_parameters.includes("tool_choice")) capabilities.add("tool");
  if (
    raw.reasoning_declared === true ||
    raw.supported_parameters.includes("reasoning") ||
    raw.supported_parameters.includes("include_reasoning") ||
    raw.supported_parameters.includes("reasoning_effort")
  ) capabilities.add("reasoning");
  if (capabilities.size === 0) capabilities.add("text");
  return Array.from(capabilities);
}

function inferSeries(raw: RawCatalogModel): string {
  const haystack = (raw.id + " " + raw.name).toLowerCase();
  const rules: Array<[string, string]> = [
    ["gpt-5.6", "GPT-5.6"],
    ["gpt-5.5", "GPT-5.5"], ["gpt-5.4", "GPT-5.4"], ["gpt-5.3", "GPT-5.3"], ["gpt-5.2", "GPT-5.2"], ["gpt-5.1", "GPT-5.1"], ["gpt-5", "GPT-5"],
    ["gpt-4.5", "GPT-4.5"], ["gpt-4o", "GPT-4o"], ["gpt-4", "GPT-4"], ["o4", "o4"], ["o3", "o3"], ["o1", "o1"],
    ["claude-opus-5", "Claude 5"], ["claude-fable-5", "Claude 5"],
    ["claude-opus-4", "Claude 4"], ["claude-sonnet-4", "Claude 4"], ["claude-haiku-4", "Claude 4"], ["claude-3.5", "Claude 3.5"], ["claude-3", "Claude 3"],
    ["gemini-3", "Gemini 3"], ["gemini-2.5", "Gemini 2.5"], ["gemini-2", "Gemini 2"], ["gemma-3", "Gemma 3"],
    ["llama-4", "Llama 4"], ["llama-3.3", "Llama 3.3"], ["llama-3.2", "Llama 3.2"], ["llama-3.1", "Llama 3.1"], ["llama-3", "Llama 3"],
    ["deepseek-v4", "DeepSeek V4"], ["deepseek-v3", "DeepSeek V3"], ["deepseek-r1", "DeepSeek R1"],
    ["mistral-large", "Mistral Large"], ["mistral-small", "Mistral Small"], ["mistral-medium", "Mistral Medium"], ["pixtral", "Pixtral"], ["voxtral", "Voxtral"], ["ministral", "Ministral"],
    ["phi-4", "Phi-4"], ["command-r", "Command R"], ["qwen3.7", "Qwen3.7"], ["qwen3.6", "Qwen3.6"], ["qwen3.5", "Qwen3.5"], ["qwen3", "Qwen3"],
    ["kimi-k3", "Kimi K3"], ["kimi-k2", "Kimi K2"],
    ["nemotron", "Nemotron"], ["hy3", "Hy3"], ["grok", "Grok"], ["hermes", "Hermes"], ["minimax", "MiniMax"], ["recraft", "Recraft"], ["perplexity", "Sonar"], ["command", "Command"], ["nova", "Nova"]
  ];
  return rules.find(([needle]) => haystack.includes(needle))?.[1] ?? raw.model_author;
}

function inferOpenRouterMarketSeries(
  raw: RawCatalogModel,
): OpenRouterMarketSeries {
  const haystack = `${raw.id} ${raw.name} ${raw.model_author}`.toLowerCase();
  const rules: Array<[RegExp, OpenRouterMarketSeries]> = [
    [/openrouter\/|router/, "Router"],
    [/\bgpt[-\s]|openai\/o[134](?:[-:/]|$)/, "GPT"],
    [/claude|anthropic/, "Claude"],
    [/gemini|google\/gemini/, "Gemini"],
    [/gemma/, "Gemma"],
    [/grok|x-ai\//, "Grok"],
    [/cohere|command[-\s]/, "Cohere"],
    [/amazon\/nova|\bnova[-\s]/, "Nova"],
    [/qwen3/, "Qwen3"],
    [/qwen|alibaba|tongyi/, "Qwen"],
    [/(^|[/\s-])yi(?:[/\s:-]|$)/, "Yi"],
    [/deepseek/, "DeepSeek"],
    [/mistral|ministral|pixtral|voxtral/, "Mistral"],
    [/llama-?4/, "Llama4"],
    [/llama-?3/, "Llama3"],
    [/llama-?2/, "Llama2"],
    [/rwkv/, "RWKV"],
    [/palm/, "PaLM"],
    [
      /image|video|audio|speech|tts|asr|transcri|embed|rerank|recraft|seedream|seedance/,
      "Media",
    ],
  ];
  return rules.find(([pattern]) => pattern.test(haystack))?.[1] ?? "Other";
}

function marketSnapshotFor(raw: RawCatalogModel): OpenRouterMarketSnapshot {
  const snapshot =
    openRouterMarketSnapshotByModelId[raw.id] ??
    openRouterMarketSnapshotByModelId[raw.canonical_slug];
  if (snapshot) return snapshot;

  return {
    ...EMPTY_OPENROUTER_MARKET_SNAPSHOT,
    series: inferOpenRouterMarketSeries(raw),
    author: raw.id.replace(/^~/, "").split("/", 1)[0] ?? "",
    created_at: raw.created > 0 ? raw.created : null,
    artificial_analysis: {},
    design_arena: {},
  };
}

function curatedMarketSnapshot(
  author: string,
  series: OpenRouterMarketSeries = "Other",
): OpenRouterMarketSnapshot {
  return {
    ...EMPTY_OPENROUTER_MARKET_SNAPSHOT,
    author,
    series,
    artificial_analysis: {},
    design_arena: {},
  };
}

function inferCategories(raw: RawCatalogModel, capabilities: Capability[]): Category[] {
  const categories = new Set<Category>();
  const haystack = (raw.id + " " + raw.name + " " + raw.raw_description).toLowerCase();
  if (raw.output_modalities.includes("text")) categories.add("chat");
  if (capabilities.includes("code")) categories.add("coding");
  if (capabilities.includes("reasoning")) categories.add("reasoning");
  if (raw.input_modalities.includes("image")) { categories.add("vision"); categories.add("multimodal"); }
  if (raw.output_modalities.includes("image")) categories.add("image_generation");
  if (capabilities.includes("audio")) categories.add("audio");
  if (raw.output_modalities.includes("speech")) categories.add("speech");
  if (raw.output_modalities.includes("transcription") || haystack.includes("asr") || haystack.includes("transcribe")) categories.add("transcription");
  if (capabilities.includes("video")) categories.add("video");
  if (raw.output_modalities.includes("embeddings") || haystack.includes("embedding")) categories.add("embeddings");
  if (raw.output_modalities.includes("rerank") || haystack.includes("rerank")) categories.add("rerank");
  if (raw.context_length >= 200000) categories.add("long_context");
  if (
    raw.output_modalities.includes("text") &&
    (haystack.includes("moderation") || haystack.includes("guard"))
  ) categories.add("safety");
  if (categories.size === 0) categories.add("chat");
  return Array.from(categories);
}

function inferTags(
  raw: RawCatalogModel,
  capabilities: Capability[],
  categories: Category[],
  active: boolean,
  pricingStatus: PricingStatus,
): string[] {
  const tags = new Set<string>();
  const haystack = (raw.id + " " + raw.name).toLowerCase();
  const ageDays = raw.created > 0 ? (CURRENT_TIME_SECONDS - raw.created) / 86400 : Number.POSITIVE_INFINITY;
  if (ageDays <= 45) tags.add("新");
  if (pricingStatus === "free") tags.add("免费");
  if (pricingStatus === "dynamic") tags.add("动态计费");
  if (capabilities.includes("image") || capabilities.includes("audio") || capabilities.includes("video")) tags.add("多模态");
  if (capabilities.includes("audio")) tags.add("音频");
  if (capabilities.includes("video")) tags.add("视频");
  if (categories.includes("image_generation")) tags.add("图片生成");
  if (categories.includes("embeddings")) tags.add("向量");
  if (categories.includes("coding")) tags.add("代码");
  if (categories.includes("reasoning")) tags.add("推理");
  if (
    pricingStatus === "fixed" &&
    raw.pricing.input * USD_TO_CNY <= 1
  ) tags.add("低价");
  if (categories.includes("long_context")) tags.add("长上下文");
  if (/gpt|claude|gemini|llama|deepseek|qwen|grok|mistral|command-r|sonar/.test(haystack)) tags.add("热门");
  if (/gpt-5|claude|gemini-3|llama-4|deepseek-v4|grok-4|qwen3.7/.test(haystack)) tags.add("精选");
  if (!active) tags.add("历史");
  return Array.from(tags).slice(0, 6);
}

function describeModalities(values: string[]) {
  const labels: Record<string, string> = { text: "文本", image: "图片", audio: "音频", video: "视频", embeddings: "向量", speech: "语音", transcription: "转写", rerank: "重排序" };
  return values.map((value) => labels[value] ?? value).join("、") || "文本";
}

function describeCategories(categories: Category[]) {
  const labels: Record<string, string> = { chat: "对话", coding: "编程", math: "数学", reasoning: "推理", roleplay: "角色扮演", translation: "翻译", analysis: "分析", vision: "视觉理解", multimodal: "多模态", image_generation: "图片生成", audio: "音频理解", speech: "语音合成", transcription: "语音转写", video: "视频理解", embeddings: "向量检索", rerank: "重排序", low_cost: "低成本", long_context: "长上下文", safety: "安全审核" };
  return categories.slice(0, 3).map((category) => labels[category] ?? category).join("、") || "通用";
}

const VERIFIED_SPEECH_MODEL_IDS = new Set([
  "deepgram/flux-tts:free",
  "fish-audio/s1",
  "fish-audio/s2-pro",
  "fish-audio/s2.1-pro-free:free",
  "fish-audio/s2.1-pro",
  "microsoft/mai-voice-2",
]);

const VERIFIED_VIDEO_MODEL_IDS = new Set([
  "bytedance/seedance-2.0",
  "bytedance/seedance-2.0-mini",
  "bytedance/seedance-2.5",
  "runway/aleph-2",
  "runway/gen-4.5",
]);

function inferOperations(raw: RawCatalogModel): ModelOperation[] {
  const operations = new Set<ModelOperation>();
  const inputs = new Set(raw.input_modalities);
  const outputs = new Set(raw.output_modalities);

  if (inputs.has("image") && outputs.has("text")) operations.add("analyze_image");
  if (inputs.has("file") && outputs.has("text")) operations.add("analyze_document");
  if (
    outputs.has("image") &&
    raw.id !== "openrouter/auto" &&
    raw.id !== "openrouter/auto-beta"
  ) {
    operations.add("generate_image");
  }
  if (outputs.has("transcription")) operations.add("transcribe");
  if (outputs.has("speech")) operations.add("synthesize_speech");
  if (outputs.has("audio")) operations.add("generate_audio");
  if (outputs.has("video")) operations.add("generate_video");
  if (outputs.has("embeddings")) operations.add("embed");
  if (outputs.has("rerank")) operations.add("rerank");
  if (inputs.has("audio") && outputs.has("text")) operations.add("analyze_audio");
  if (inputs.has("video") && outputs.has("text")) operations.add("analyze_video");
  if (
    inputs.has("text") &&
    outputs.has("text")
  ) {
    operations.add("chat");
  }

  return operations.size > 0 ? Array.from(operations) : ["chat"];
}

function primaryOperation(operations: ModelOperation[]): ModelOperation {
  const priority: ModelOperation[] = [
    "generate_image",
    "transcribe",
    "synthesize_speech",
    "generate_audio",
    "generate_video",
    "embed",
    "rerank",
    "chat",
    "analyze_document",
    "analyze_image",
    "analyze_audio",
    "analyze_video",
  ];
  return priority.find((operation) => operations.includes(operation)) ?? "chat";
}

function interactionForOperation(
  operation: ModelOperation,
  modelId: string,
): {
  status: InteractionStatus;
  entrypoint: ModelUiEntrypoint;
} {
  if (
    operation === "chat" ||
    operation === "analyze_image" ||
    operation === "transcribe" ||
    (
      operation === "synthesize_speech" &&
      VERIFIED_SPEECH_MODEL_IDS.has(modelId)
    )
  ) {
    return { status: "ready", entrypoint: "chat" };
  }
  if (operation === "embed" || operation === "rerank") {
    return { status: "ready", entrypoint: "rag" };
  }
  if (
    operation === "generate_video" &&
    VERIFIED_VIDEO_MODEL_IDS.has(modelId)
  ) {
    return { status: "ready", entrypoint: "multimodal" };
  }
  return { status: "planned", entrypoint: "planned" };
}

function inferJobCapabilities(
  raw: RawCatalogModel,
  capabilities: Capability[],
  operations: ModelOperation[],
): JobCapability[] {
  const result = new Set<JobCapability>();
  const haystack = (raw.id + " " + raw.name + " " + raw.raw_description).toLowerCase();

  if (operations.includes("chat")) result.add("text_chat");
  if (capabilities.includes("code")) result.add("coding");
  if (capabilities.includes("reasoning")) result.add("reasoning");
  if (capabilities.includes("tool")) result.add("tool_use");
  if (raw.input_modalities.includes("file") && raw.output_modalities.includes("text")) {
    result.add("document_understanding");
  }
  if (operations.includes("analyze_image")) result.add("image_understanding");
  if (operations.includes("generate_image")) result.add("image_generation");
  if (operations.includes("analyze_audio")) result.add("audio_understanding");
  if (operations.includes("transcribe")) result.add("transcription");
  if (operations.includes("synthesize_speech")) result.add("speech_synthesis");
  if (operations.includes("generate_audio")) result.add("music_generation");
  if (operations.includes("realtime_voice")) result.add("realtime_voice");
  if (operations.includes("analyze_video")) result.add("video_understanding");
  if (operations.includes("generate_video")) result.add("video_generation");
  if (operations.includes("embed")) result.add("embedding");
  if (operations.includes("rerank")) result.add("rerank");
  if (haystack.includes("moderation") || haystack.includes("guard")) {
    result.add("safety");
  }
  return Array.from(result);
}

function buildChineseDescription(
  raw: RawCatalogModel,
  categories: Category[],
  active: boolean,
  priceCny: Model["price_cny"],
  pricingStatus: PricingStatus,
  pricingBasis: PricingBasis,
) {
  const inputs = describeModalities(raw.input_modalities);
  const outputs = describeModalities(raw.output_modalities);
  const scenes = describeCategories(categories);
  const context = raw.context_length > 0 ? raw.context_length.toLocaleString("zh-CN") + " tokens" : "未公开";
  const price =
    pricingStatus === "free"
      ? "当前目录价格为免费，后续以平台结算为准。"
      : pricingBasis === "media"
        ? "按图片、音频或视频规格计费，费用以专用生成接口为准。"
        : pricingBasis === "request"
          ? "按请求或专用端点计费，费用以对应接口为准。"
          : pricingStatus === "dynamic"
            ? "费用由实际路由模型或组合调用决定。"
        : "输入约 ¥" + priceCny.input.toFixed(2) + "，输出约 ¥" + priceCny.output.toFixed(2) + " / 百万 token。";
  const lifecycle = active ? "" : "\u8be5\u6761\u76ee\u5df2\u6309\u5e73\u53f0\u76ee\u5f55\u6807\u8bb0\u4e3a\u975e\u6d3b\u8dc3\u3002";
  return raw.name + " \u662f模镜\u76ee\u5f55\u6536\u5f55\u7684 " + raw.model_author + " 模型，支持" + inputs + "输入并输出" + outputs + "，适合" + scenes + "等场景。上下文长度为 " + context + "，" + price + lifecycle;
}

function enrichModel(
  raw: RawCatalogModel,
  options: {
    catalogStatus?: CatalogStatus;
    catalogCounted?: boolean;
    note?: string;
  } = {},
): Model {
  const expired =
    raw.expiration_date !== null &&
    raw.expiration_date <= CURRENT_TIME_SECONDS;
  const catalog_status: CatalogStatus =
    options.catalogStatus ??
    (expired
      ? "expired"
      : uncertainCatalogModelIds.has(raw.id)
        ? "uncertain"
        : "live");
  const active = catalog_status !== "expired";
  const pricing_basis = getPricingBasis(raw);
  const pricing_status = getPricingStatus(pricing_basis);
  const price_cny =
    pricing_status === "fixed"
      ? { input: toCny(raw.pricing.input), output: toCny(raw.pricing.output) }
      : { input: 0, output: 0 };
  const capabilities = inferCapabilities(raw);
  const categories = inferCategories(raw, capabilities);
  const openrouter_market = marketSnapshotFor(raw);
  const operations = inferOperations(raw);
  const job_capabilities = inferJobCapabilities(
    raw,
    capabilities,
    operations,
  );
  const primary_operation = primaryOperation(operations);
  const interaction = interactionForOperation(primary_operation, raw.id);
  const basePricing: TokenPricing = {
    input: raw.pricing.input,
    output: raw.pricing.output,
  };
  const tieredPricing = pricingOverrides(raw);
  const timeWindowPricing = pricingTimeWindows(raw);
  const realtimeVariant: ModelServingVariant = {
    type: "realtime",
    catalog_id: raw.id,
    request_model_id: raw.id,
    endpoint: "synchronous",
    pricing: basePricing,
    pricing_overrides: tieredPricing,
    pricing_time_windows: timeWindowPricing,
    price_cny,
    input_modalities: raw.input_modalities,
    output_modalities: raw.output_modalities,
  };
  return {
    id: raw.id,
    canonical_slug: raw.canonical_slug,
    name: raw.name,
    provider: normalizeProvider(raw.model_author),
    model_author: raw.model_author,
    description: buildChineseDescription(
      raw,
      categories,
      active,
      price_cny,
      pricing_status,
      pricing_basis,
    ),
    context_length: raw.context_length,
    pricing: basePricing,
    pricing_overrides: tieredPricing,
    pricing_time_windows: timeWindowPricing,
    price_cny,
    pricing_status,
    pricing_basis,
    pricing_tier: getPricingTier(raw.pricing.input, pricing_status),
    capabilities,
    input_modalities: raw.input_modalities,
    output_modalities: raw.output_modalities,
    operations,
    job_capabilities,
    primary_operation,
    interaction_status: interaction.status,
    ui_entrypoint: interaction.entrypoint,
    series: inferSeries(raw),
    categories,
    openrouter_market,
    supported_parameters: raw.supported_parameters,
    reasoning_declared: raw.reasoning_declared === true,
    distillable: openrouter_market.distillable,
    zero_data_retention: openrouter_market.zero_data_retention,
    in_region_routing: openrouter_market.regions.length > 0,
    catalog_status,
    catalog_counted: options.catalogCounted ?? true,
    serving_variants: [realtimeVariant],
    active,
    tags: inferTags(
      raw,
      capabilities,
      categories,
      active,
      pricing_status,
    ),
    note: options.note ?? raw.note,
  };
}

const worldModelEntry: Model = {
  id: "worldlabs/marble",
  canonical_slug: "worldlabs/marble",
  name: "World Labs Marble",
  provider: "World Labs",
  model_author: "World Labs",
  description:
    "上传现实场景的图片或视频，异步生成可探索的 3D 世界，支持全景图、GLB、SPZ 预览和显式 PLY 导出。",
  context_length: 0,
  pricing: { input: -1, output: -1 },
  pricing_overrides: [],
  pricing_time_windows: [],
  price_cny: { input: 0, output: 0 },
  pricing_status: "dynamic",
  pricing_basis: "dynamic",
  pricing_tier: "dynamic",
  capabilities: ["image", "video"],
  input_modalities: ["image", "video"],
  output_modalities: ["world"],
  operations: ["generate_world"],
  job_capabilities: ["world_generation"],
  primary_operation: "generate_world",
  interaction_status: "ready",
  ui_entrypoint: "multimodal",
  series: "世界模型",
  categories: ["3D", "空间智能"],
  openrouter_market: curatedMarketSnapshot("worldlabs"),
  supported_parameters: [],
  reasoning_declared: false,
  distillable: false,
  zero_data_retention: false,
  in_region_routing: false,
  catalog_status: "curated",
  catalog_counted: false,
  serving_variants: [],
  active: true,
  tags: ["3D", "空间智能", "新"],
  note: "真实 Marble 生成和 PLY 导出可能消耗 World Labs Credits。",
  worldModel: true,
};

const FEATURED_MODEL_IDS = [
  "openai/gpt-5.6-sol",
  "anthropic/claude-opus-5",
  "deepseek/deepseek-v4-pro-0813",
  "anthropic/claude-fable-5",
  "moonshotai/kimi-k3",
  "anthropic/claude-opus-5-fast",
  "openai/gpt-5.6-luna",
  "openai/gpt-5.6-terra",
  "google/gemini-3.6-flash",
  "qwen/qwen3.7-flash",
  "openrouter/auto",
];

function catalogSort(left: RawCatalogModel, right: RawCatalogModel) {
  const lifecycleRank = (model: RawCatalogModel) => {
    if (
      model.expiration_date !== null &&
      model.expiration_date <= CURRENT_TIME_SECONDS
    ) {
      return 2;
    }
    return uncertainCatalogModelIds.has(model.id) ? 1 : 0;
  };
  const leftLifecycleRank = lifecycleRank(left);
  const rightLifecycleRank = lifecycleRank(right);
  if (leftLifecycleRank !== rightLifecycleRank) {
    return leftLifecycleRank - rightLifecycleRank;
  }

  const leftDeprioritized = left.id.startsWith("sourceful/riverflow");
  const rightDeprioritized = right.id.startsWith("sourceful/riverflow");
  if (leftDeprioritized !== rightDeprioritized) {
    return leftDeprioritized ? 1 : -1;
  }

  const leftFeatured = FEATURED_MODEL_IDS.indexOf(left.id);
  const rightFeatured = FEATURED_MODEL_IDS.indexOf(right.id);
  if (leftFeatured !== rightFeatured) {
    if (leftFeatured < 0) return 1;
    if (rightFeatured < 0) return -1;
    return leftFeatured - rightFeatured;
  }
  return right.created - left.created || left.id.localeCompare(right.id);
}

const DIRECT_OPENAI_AUDIO_MODELS: Model[] = [
  {
    id: "gpt-4o-mini-tts",
    canonical_slug: "openai/gpt-4o-mini-tts",
    name: "OpenAI: GPT-4o Mini TTS",
    provider: "OpenAI",
    model_author: "OpenAI",
    description:
      "OpenAI 的轻量文字转语音模型，适合朗读助手回答和生成普通语音。通过独立 OpenAI 音频连接调用；自定义音色与声音克隆仍保持关闭。",
    context_length: 0,
    pricing: { input: -1, output: -1 },
    pricing_overrides: [],
    pricing_time_windows: [],
    price_cny: { input: 0, output: 0 },
    pricing_status: "dynamic",
    pricing_basis: "dynamic",
    pricing_tier: "dynamic",
    capabilities: ["text", "audio"],
    input_modalities: ["text"],
    output_modalities: ["speech"],
    operations: ["synthesize_speech"],
    job_capabilities: ["speech_synthesis"],
    primary_operation: "synthesize_speech",
    interaction_status: "planned",
    ui_entrypoint: "multimodal",
    series: "GPT-4o Audio",
    categories: ["audio"],
    openrouter_market: curatedMarketSnapshot("openai", "GPT"),
    supported_parameters: ["voice", "speed"],
    reasoning_declared: false,
    distillable: false,
    zero_data_retention: false,
    in_region_routing: false,
    catalog_status: "curated",
    catalog_counted: false,
    serving_variants: [],
    active: true,
    tags: ["新", "音频"],
    note: "需要配置 OpenAI 音频连接；声音克隆仍未开放。",
  },
  {
    id: "gpt-realtime-2.1-mini",
    canonical_slug: "openai/gpt-realtime-2.1-mini",
    name: "OpenAI: GPT Realtime 2.1 Mini",
    provider: "OpenAI",
    model_author: "OpenAI",
    description:
      "OpenAI 实时双向语音模型的均衡版本，适合低延迟连续语音对话、自然停顿和随时打断。该模型通过独立 OpenAI 音频连接调用，不进入普通聊天或智能调度候选池。",
    context_length: 0,
    pricing: { input: -1, output: -1 },
    pricing_overrides: [],
    pricing_time_windows: [],
    price_cny: { input: 0, output: 0 },
    pricing_status: "dynamic",
    pricing_basis: "dynamic",
    pricing_tier: "dynamic",
    capabilities: ["audio"],
    input_modalities: ["audio"],
    output_modalities: ["audio"],
    operations: ["realtime_voice"],
    job_capabilities: ["realtime_voice"],
    primary_operation: "realtime_voice",
    interaction_status: "planned",
    ui_entrypoint: "multimodal",
    series: "GPT Realtime 2.1",
    categories: ["audio"],
    openrouter_market: curatedMarketSnapshot("openai", "GPT"),
    supported_parameters: [],
    reasoning_declared: false,
    distillable: false,
    zero_data_retention: false,
    in_region_routing: false,
    catalog_status: "curated",
    catalog_counted: false,
    serving_variants: [],
    active: true,
    tags: ["新", "音频", "热门"],
    note: "需要配置 OpenAI 音频与实时语音连接。",
  },
  {
    id: "gpt-realtime-2.1",
    canonical_slug: "openai/gpt-realtime-2.1",
    name: "OpenAI: GPT Realtime 2.1",
    provider: "OpenAI",
    model_author: "OpenAI",
    description:
      "OpenAI 实时双向语音模型的质量版本，适合更重视回答质量的连续语音对话，支持自然停顿和随时打断。该模型通过独立 OpenAI 音频连接调用，不进入普通聊天或智能调度候选池。",
    context_length: 0,
    pricing: { input: -1, output: -1 },
    pricing_overrides: [],
    pricing_time_windows: [],
    price_cny: { input: 0, output: 0 },
    pricing_status: "dynamic",
    pricing_basis: "dynamic",
    pricing_tier: "dynamic",
    capabilities: ["audio"],
    input_modalities: ["audio"],
    output_modalities: ["audio"],
    operations: ["realtime_voice"],
    job_capabilities: ["realtime_voice"],
    primary_operation: "realtime_voice",
    interaction_status: "planned",
    ui_entrypoint: "multimodal",
    series: "GPT Realtime 2.1",
    categories: ["audio"],
    openrouter_market: curatedMarketSnapshot("openai", "GPT"),
    supported_parameters: [],
    reasoning_declared: false,
    distillable: false,
    zero_data_retention: false,
    in_region_routing: false,
    catalog_status: "curated",
    catalog_counted: false,
    serving_variants: [],
    active: true,
    tags: ["新", "音频", "热门"],
    note: "需要配置 OpenAI 音频与实时语音连接。",
  },
];

function batchServingVariant(raw: RawCatalogModel): ModelServingVariant {
  const requestModelId = raw.id.replace(/:batch$/, "");
  const outputIsEmbedding = raw.output_modalities.includes("embeddings");
  const basePricing: TokenPricing = {
    input: raw.pricing.input,
    output: raw.pricing.output,
  };
  return {
    type: "batch",
    catalog_id: raw.id,
    request_model_id: requestModelId,
    endpoint: outputIsEmbedding ? "/v1/embeddings" : "/v1/chat/completions",
    pricing: basePricing,
    pricing_overrides: pricingOverrides(raw),
    pricing_time_windows: pricingTimeWindows(raw),
    price_cny: {
      input: toCny(raw.pricing.input),
      output: toCny(raw.pricing.output),
    },
    // OpenRouter currently validates Batch inputs independently of the model
    // catalog's inherited multimodal architecture. Batch input is text-only.
    input_modalities: ["text"],
    output_modalities: outputIsEmbedding ? ["embeddings"] : ["text"],
    completion_window: "24h",
    data_retention_days: 30,
  };
}

const batchVariantsByModelId = new Map<string, ModelServingVariant[]>();
for (const raw of rawBatchServingVariants) {
  const variant = batchServingVariant(raw);
  const variants = batchVariantsByModelId.get(variant.request_model_id) ?? [];
  variants.push(variant);
  batchVariantsByModelId.set(variant.request_model_id, variants);
}

const sortedCatalogModels = [...rawCatalogModels]
  .sort(catalogSort)
  .map((raw) => enrichModel(raw))
  .map((model) => ({
    ...model,
    serving_variants: [
      ...model.serving_variants,
      ...(batchVariantsByModelId.get(model.id) ?? []),
    ],
  }));

const SEEDANCE_2_5_MODEL_ID = "bytedance/seedance-2.5";
const DEEPSEEK_V4_PRO_MODEL_ID = "deepseek/deepseek-v4-pro-0813";
const DEEPSEEK_V4_FLASH_MODEL_ID = "deepseek/deepseek-v4-flash-0731";
const SEEDREAM_5_PRO_MODEL_ID = "bytedance-seed/seedream-5-0-pro";
const MID_CATALOG_MODEL_IDS = [
  "sakana/sakana-namazu",
  "upstage/solar-pro4",
  "meta/muse-glimmer-30b",
  "inclusionai/ling-3.0-tiny:free",
];
const LATEST_REFRESH_MODEL_IDS = [
  "~z-ai/glm-latest",
  "z-ai/glm-5.3",
  "liquid/lfm-2.5-embedding-350m:free",
  "qwen/qwen3.8-27b",
  "dots-studio/dots-3-note-preview:free",
  "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
  "mistralai/voxtral-small-24b-2507-stt",
  "mistralai/voxtral-mini-3b-2507",
  "bytedance-seed/seedream-5-0-lite",
  "google/gemini-3.7-flash",
  "voyageai/voyage-code-4",
  "qwen/qwen3-reranker-8b",
  "qwen/qwen3-asr-1.7b",
  "qwen/qwen3-asr-0.6b",
  "x-ai/grok-imagine-image-2.0",
  "liquid/lfm-2.5-2.6b:free",
  "nvidia/nemotron-3.5-lightning",
  "nvidia/nemotron-3.5-lightning:free",
  "x-ai/grok-4.6",
  "deepgram/flux-tts:free",
  "qwen/qwen3.8-2.4t-a95b",
  "bytedance-seed/seed-2-1-turbo",
  "bytedance-seed/seed-2.0-code",
  "bytedance/seedance-2.0-mini",
];
const reservedCatalogModelIds = new Set([
  SEEDANCE_2_5_MODEL_ID,
  DEEPSEEK_V4_PRO_MODEL_ID,
  DEEPSEEK_V4_FLASH_MODEL_ID,
  SEEDREAM_5_PRO_MODEL_ID,
  ...MID_CATALOG_MODEL_IDS,
  ...LATEST_REFRESH_MODEL_IDS,
]);
const seedance25Model = sortedCatalogModels.find(
  (model) => model.id === SEEDANCE_2_5_MODEL_ID,
);
const deepseekV4ProModel = sortedCatalogModels.find(
  (model) => model.id === DEEPSEEK_V4_PRO_MODEL_ID,
);
const deepseekV4FlashModel = sortedCatalogModels.find(
  (model) => model.id === DEEPSEEK_V4_FLASH_MODEL_ID,
);
const seedream5ProModel = sortedCatalogModels.find(
  (model) => model.id === SEEDREAM_5_PRO_MODEL_ID,
);
const midCatalogModels = MID_CATALOG_MODEL_IDS.map((modelId) =>
  sortedCatalogModels.find((model) => model.id === modelId),
).filter((model): model is Model => Boolean(model));
const latestRefreshModels = LATEST_REFRESH_MODEL_IDS.map((modelId) =>
  sortedCatalogModels.find((model) => model.id === modelId),
).filter((model): model is Model => Boolean(model));
const normallyOrderedCatalogModels = sortedCatalogModels.filter(
  (model) => !reservedCatalogModelIds.has(model.id),
);

// The list has one router card followed by two model cards in its first row.
// Keep the stable V4 Flash default at row 2 column 1, place V4 Pro one row
// later, and reserve row 4 column 1 for Seedream 5 Pro.
const primaryCatalogModels: Model[] = [
  ...normallyOrderedCatalogModels.slice(0, 2),
  ...(deepseekV4FlashModel ? [deepseekV4FlashModel] : []),
  ...normallyOrderedCatalogModels.slice(2, 4),
  ...(deepseekV4ProModel ? [deepseekV4ProModel] : []),
  ...normallyOrderedCatalogModels.slice(4, 6),
  ...(seedream5ProModel ? [seedream5ProModel] : []),
  ...normallyOrderedCatalogModels.slice(6, 8),
  ...(seedance25Model ? [seedance25Model] : []),
  ...DIRECT_OPENAI_AUDIO_MODELS,
  ...normallyOrderedCatalogModels.slice(8),
];
const catalogMidpoint = Math.floor(primaryCatalogModels.length / 2);

const baseCatalogModels: Model[] = [
  ...primaryCatalogModels.slice(0, catalogMidpoint),
  ...midCatalogModels,
  ...primaryCatalogModels.slice(catalogMidpoint),
];

function stableCatalogHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function scatterCatalogModels(
  baseModels: Model[],
  additions: Model[],
  minimumIndex: number,
) {
  if (additions.length === 0) return [...baseModels];

  const lowerBound = Math.min(minimumIndex, baseModels.length);
  const tailSlots = Math.max(1, baseModels.length - lowerBound + 1);
  const placements = additions
    .map((model, additionIndex) => {
      const bucketStart = Math.floor(
        (tailSlots * additionIndex) / additions.length,
      );
      const bucketEnd = Math.floor(
        (tailSlots * (additionIndex + 1)) / additions.length,
      );
      const bucketSize = Math.max(1, bucketEnd - bucketStart);
      return {
        model,
        index:
          lowerBound +
          bucketStart +
          (stableCatalogHash(model.id) % bucketSize),
      };
    })
    .sort((left, right) => left.index - right.index);

  const result = [...baseModels];
  placements.forEach((placement, offset) => {
    result.splice(placement.index + offset, 0, placement.model);
  });
  return result;
}

// The list page renders two spotlight cards, then a three-column gallery.
// Keep this refresh below the first six complete gallery rows and scatter it
// deterministically so cards do not jump between renders.
const LATEST_REFRESH_MIN_INDEX = 2 + 6 * 3;
const assembledModels: Model[] = [
  ...scatterCatalogModels(
    baseCatalogModels,
    latestRefreshModels,
    LATEST_REFRESH_MIN_INDEX,
  ),
  worldModelEntry,
];

export const models: Model[] = assembledModels.sort((left, right) => {
  const lifecycleRank = (model: Model) => {
    if (model.catalog_status === "expired") return 2;
    if (model.catalog_status === "uncertain") return 1;
    return 0;
  };
  return lifecycleRank(left) - lifecycleRank(right);
});
