export const OPENROUTER_MARKET_SERIES = [
  "GPT",
  "Claude",
  "Gemini",
  "Gemma",
  "Grok",
  "Cohere",
  "Nova",
  "Qwen",
  "Yi",
  "DeepSeek",
  "Mistral",
  "Llama2",
  "Llama3",
  "Llama4",
  "RWKV",
  "Qwen3",
  "Router",
  "Media",
  "Other",
  "PaLM",
] as const;

export type OpenRouterMarketSeries =
  (typeof OPENROUTER_MARKET_SERIES)[number];

export const OPENROUTER_MARKET_CATEGORIES = [
  "programming",
  "roleplay",
  "marketing",
  "marketing/seo",
  "technology",
  "science",
  "translation",
  "legal",
  "finance",
  "health",
  "trivia",
  "academia",
] as const;

export type OpenRouterMarketCategory =
  (typeof OPENROUTER_MARKET_CATEGORIES)[number];

export const OPENROUTER_SUPPORTED_PARAMETER_FILTERS = [
  "tools",
  "temperature",
  "top_p",
  "top_k",
  "min_p",
  "top_a",
  "frequency_penalty",
  "presence_penalty",
  "repetition_penalty",
  "max_tokens",
  "max_completion_tokens",
  "logit_bias",
  "logprobs",
  "top_logprobs",
  "prediction",
  "seed",
  "response_format",
  "structured_outputs",
  "stop",
  "parallel_tool_calls",
  "include_reasoning",
  "reasoning",
  "reasoning_effort",
  "web_search_options",
  "verbosity",
] as const;

export type OpenRouterRegion = "eu" | "us";

export const OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS = [
  "intelligence_index",
  "coding_index",
  "agentic_index",
] as const;

export type OpenRouterArtificialAnalysisMetric =
  (typeof OPENROUTER_ARTIFICIAL_ANALYSIS_METRICS)[number];

export const OPENROUTER_DESIGN_ARENA_METRICS = [
  "code_categories",
  "ui_component",
  "game_development",
  "data_visualization",
  "3d",
  "image",
  "video",
  "svg",
] as const;

export type OpenRouterDesignArenaMetric =
  (typeof OPENROUTER_DESIGN_ARENA_METRICS)[number];

export interface OpenRouterMarketSnapshot {
  series: OpenRouterMarketSeries;
  author: string;
  providers: string[];
  categories: OpenRouterMarketCategory[];
  discounted: boolean;
  distillable: boolean;
  zero_data_retention: boolean;
  regions: OpenRouterRegion[];
  created_at: number | null;
  tool_call_success_rate: number | null;
  artificial_analysis: Partial<
    Record<OpenRouterArtificialAnalysisMetric, number>
  >;
  design_arena: Partial<Record<OpenRouterDesignArenaMetric, number>>;
}

export const EMPTY_OPENROUTER_MARKET_SNAPSHOT: OpenRouterMarketSnapshot = {
  series: "Other",
  author: "",
  providers: [],
  categories: [],
  discounted: false,
  distillable: false,
  zero_data_retention: false,
  regions: [],
  created_at: null,
  tool_call_success_rate: null,
  artificial_analysis: {},
  design_arena: {},
};
