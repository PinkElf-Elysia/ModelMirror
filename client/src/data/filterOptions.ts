import {
  type InputModality,
  type JobCapability,
  type SupportedParameter,
  models,
} from "./models";
import {
  OPENROUTER_MARKET_CATEGORIES,
  OPENROUTER_MARKET_SERIES,
  OPENROUTER_SUPPORTED_PARAMETER_FILTERS,
  type OpenRouterArtificialAnalysisMetric,
  type OpenRouterDesignArenaMetric,
  type OpenRouterMarketCategory,
  type OpenRouterMarketSeries,
  type OpenRouterRegion,
} from "./openRouterMarket";

export interface Option<T extends string> {
  value: T;
  label: string;
  icon?: string;
}

export interface RangeValue {
  min: number;
  max: number;
}

export const CONTEXT_MIN_LIMIT = 1_000_000;
export const PROMPT_PRICE_USD_LIMIT: RangeValue = { min: 0, max: 10 };
export const OUTPUT_PRICE_USD_LIMIT: RangeValue = { min: 0, max: 100 };
export const MODEL_AGE_DAYS_LIMIT: RangeValue = { min: 0, max: 365 };
export const ARTIFICIAL_ANALYSIS_RANGE_LIMIT: RangeValue = {
  min: 0,
  max: 100,
};
export const DESIGN_ARENA_RANGE_LIMIT: RangeValue = { min: 0, max: 2_000 };

export const inputModalityOptions: Option<InputModality>[] = [
  { value: "text", label: "文本" },
  { value: "image", label: "图片" },
  { value: "file", label: "文件" },
  { value: "audio", label: "音频" },
  { value: "video", label: "视频" },
];

export const contextQuickOptions = [
  { label: "4K+", value: 4_000 },
  { label: "8K+", value: 8_000 },
  { label: "16K+", value: 16_000 },
  { label: "32K+", value: 32_000 },
  { label: "128K+", value: 128_000 },
  { label: "1M", value: CONTEXT_MIN_LIMIT },
];

export const promptPriceQuickOptions = [
  { label: "免费", value: { min: 0, max: 0 } },
  { label: "$0–0.5", value: { min: 0, max: 0.5 } },
  { label: "$0.5–10", value: { min: 0.5, max: 10 } },
  { label: "$10+", value: { min: 10, max: PROMPT_PRICE_USD_LIMIT.max } },
];

export const jobCapabilityOptions: Option<JobCapability>[] = [
  { value: "text_chat", label: "文字对话" },
  { value: "coding", label: "编程开发" },
  { value: "reasoning", label: "推理分析" },
  { value: "tool_use", label: "工具调用" },
  { value: "document_understanding", label: "文档理解" },
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
  { value: "safety", label: "安全审核" },
  { value: "world_generation", label: "3D 世界生成" },
];

const openRouterCategoryLabels: Record<OpenRouterMarketCategory, string> = {
  programming: "编程",
  roleplay: "角色扮演",
  marketing: "市场营销",
  "marketing/seo": "SEO 营销",
  technology: "技术",
  science: "科学",
  translation: "翻译",
  legal: "法律",
  finance: "金融",
  health: "健康",
  trivia: "知识问答",
  academia: "学术",
};

const seriesLabels: Record<OpenRouterMarketSeries, string> = {
  GPT: "GPT",
  Claude: "Claude",
  Gemini: "Gemini",
  Gemma: "Gemma",
  Grok: "Grok",
  Cohere: "Cohere",
  Nova: "Nova",
  Qwen: "Qwen",
  Yi: "零一万物 Yi",
  DeepSeek: "深度求索",
  Mistral: "Mistral",
  Llama2: "Llama 2",
  Llama3: "Llama 3",
  Llama4: "Llama 4",
  RWKV: "RWKV",
  Qwen3: "Qwen 3",
  Router: "路由模型",
  Media: "多媒体",
  Other: "其他",
  PaLM: "PaLM",
};

const seriesPriority: OpenRouterMarketSeries[] = [
  "GPT",
  "Claude",
  "DeepSeek",
  "Gemini",
  "Grok",
  "Qwen",
  "Qwen3",
  "Llama4",
  "Llama3",
  "Mistral",
  "Gemma",
];

const providerLabels: Record<string, string> = {
  Alibaba: "阿里云",
  DeepSeek: "深度求索",
  Minimax: "MiniMax（稀宇科技）",
  Seed: "字节跳动 Seed",
  SiliconFlow: "硅基流动",
  StepFun: "阶跃星辰",
  xAI: "Grok（xAI）",
  Xiaomi: "小米",
  "Z.AI": "智谱",
};

const providerPriority = [
  "OpenAI",
  "Anthropic",
  "DeepSeek",
  "Google",
  "Google AI Studio",
  "xAI",
  "Alibaba",
];

const modelAuthorLabels: Record<string, string> = {
  alibaba: "阿里巴巴",
  baai: "北京智源",
  baidu: "百度",
  bytedance: "字节跳动",
  "bytedance-seed": "字节跳动 Seed",
  deepseek: "深度求索",
  "deepseek-ai": "深度求索 AI",
  inclusionai: "蚂蚁集团 InclusionAI",
  kwaipilot: "快手 Kwaipilot",
  kwaivgi: "快手可灵",
  meituan: "美团",
  minimax: "MiniMax（稀宇科技）",
  moonshotai: "Kimi（月之暗面）",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  "x-ai": "Grok（xAI）",
  qwen: "Qwen（通义千问）",
  meta: "Meta",
  "meta-llama": "Meta Llama",
  mistralai: "Mistral",
  nvidia: "NVIDIA",
  openrouter: "平台路由",
  stepfun: "阶跃星辰",
  tencent: "腾讯",
  xiaomi: "小米",
  "z-ai": "智谱",
};

const modelAuthorPriority = [
  "openai",
  "anthropic",
  "deepseek",
  "deepseek-ai",
  "google",
  "x-ai",
  "moonshotai",
  "qwen",
  "alibaba",
  "bytedance-seed",
  "bytedance",
];

const optionCollator = new Intl.Collator("zh-CN", {
  numeric: true,
  sensitivity: "base",
});

function priorityOf(value: string, priorities: readonly string[]) {
  const index = priorities.indexOf(value);
  return index === -1 ? Number.MAX_SAFE_INTEGER : index;
}

function compareFacetOptions<T extends string>(
  left: Option<T>,
  right: Option<T>,
  priorities: readonly string[],
) {
  const priorityDifference =
    priorityOf(left.value, priorities) - priorityOf(right.value, priorities);
  return priorityDifference || optionCollator.compare(left.label, right.label);
}

export const openRouterCategoryOptions: Option<OpenRouterMarketCategory>[] =
  OPENROUTER_MARKET_CATEGORIES.map((category) => ({
    value: category,
    label: openRouterCategoryLabels[category],
  }));

export const seriesOptions: Option<OpenRouterMarketSeries>[] =
  OPENROUTER_MARKET_SERIES.map((series) => ({
    value: series,
    label: seriesLabels[series],
  })).sort((left, right) => compareFacetOptions(left, right, seriesPriority));

const supportedParameterLabels: Record<string, string> = {
  tools: "工具调用（tools）",
  temperature: "随机性（temperature）",
  top_p: "核采样（top_p）",
  top_k: "候选数（top_k）",
  min_p: "最低概率（min_p）",
  top_a: "动态采样（top_a）",
  frequency_penalty: "频率惩罚（frequency_penalty）",
  presence_penalty: "存在惩罚（presence_penalty）",
  repetition_penalty: "重复惩罚（repetition_penalty）",
  max_tokens: "最大令牌数（max_tokens）",
  max_completion_tokens: "最大输出令牌数（max_completion_tokens）",
  logit_bias: "令牌偏置（logit_bias）",
  logprobs: "对数概率（logprobs）",
  top_logprobs: "最高对数概率（top_logprobs）",
  prediction: "预测内容（prediction）",
  seed: "随机种子（seed）",
  response_format: "响应格式（response_format）",
  structured_outputs: "结构化输出（structured_outputs）",
  stop: "停止序列（stop）",
  parallel_tool_calls: "并行工具调用（parallel_tool_calls）",
  include_reasoning: "包含推理（include_reasoning）",
  reasoning: "推理配置（reasoning）",
  reasoning_effort: "推理强度（reasoning_effort）",
  web_search_options: "联网搜索（web_search_options）",
  verbosity: "回答详略（verbosity）",
};

export const supportedParameterOptions: Option<SupportedParameter>[] =
  OPENROUTER_SUPPORTED_PARAMETER_FILTERS.map((parameter) => ({
    value: parameter,
    label: supportedParameterLabels[parameter] ?? parameter,
  }));

export const regionOptions: Option<OpenRouterRegion>[] = [
  { value: "eu", label: "欧盟" },
  { value: "us", label: "美国" },
];

export const artificialAnalysisMetricOptions: Option<OpenRouterArtificialAnalysisMetric>[] = [
  { value: "intelligence_index", label: "智能指数" },
  { value: "coding_index", label: "编程指数" },
  { value: "agentic_index", label: "智能体指数" },
];

export const designArenaMetricOptions: Option<OpenRouterDesignArenaMetric>[] = [
  { value: "code_categories", label: "编程综合" },
  { value: "ui_component", label: "界面组件" },
  { value: "game_development", label: "游戏开发" },
  { value: "data_visualization", label: "数据可视化" },
  { value: "3d", label: "3D" },
  { value: "image", label: "图片" },
  { value: "video", label: "视频" },
  { value: "svg", label: "SVG" },
];

export const providerOptions: Option<string>[] = Array.from(
  new Set(models.flatMap((model) => model.openrouter_market.providers)),
)
  .filter(Boolean)
  .map((provider) => ({
    value: provider,
    label: providerLabels[provider] ?? provider,
  }))
  .sort((left, right) => compareFacetOptions(left, right, providerPriority));

export const modelAuthorOptions: Option<string>[] = Array.from(
  new Set(models.map((model) => model.openrouter_market.author)),
)
  .filter(Boolean)
  .map((author) => ({
    value: author,
    label: modelAuthorLabels[author] ?? author,
  }))
  .sort((left, right) => compareFacetOptions(left, right, modelAuthorPriority));
