import { type Model } from "../data/models";

const providerDisplayNameMap: Record<string, string> = {
  "~anthropic": "Anthropic",
  "~google": "Google",
  "~moonshotai": "Moonshot AI",
  "~openai": "OpenAI",
  ai21: "AI21",
  aionlabs: "AionLabs",
  alibaba: "阿里巴巴",
  allenai: "AllenAI",
  amazon: "Amazon",
  anthropic: "Anthropic",
  "anthracite-org": "Anthracite",
  "arcee ai": "Arcee AI",
  baai: "BAAI",
  baidu: "百度",
  "black forest labs": "Black Forest Labs",
  bytedance: "字节跳动",
  "bytedance seed": "字节 Seed",
  cohere: "Cohere",
  "deep cogito": "Deep Cogito",
  deepseek: "深度求索",
  essentialai: "EssentialAI",
  google: "Google",
  gryphe: "Gryphe",
  ibm: "IBM",
  inception: "Inception",
  inclusionai: "InclusionAI",
  inflection: "Inflection",
  intfloat: "Intfloat",
  kling: "可灵",
  kwaipilot: "Kwaipilot",
  liquidai: "LiquidAI",
  mancer: "Mancer",
  meta: "Meta",
  microsoft: "Microsoft",
  minimax: "MiniMax",
  mistral: "Mistral",
  "mistral ai": "Mistral AI",
  "moonshot ai": "月之暗面",
  morph: "Morph",
  "nex agi": "Nex AGI",
  nous: "Nous Research",
  "nous research": "Nous Research",
  nvidia: "NVIDIA",
  openai: "OpenAI",
  perceptron: "Perceptron",
  perplexity: "Perplexity",
  poolside: "Poolside",
  "prime intellect": "Prime Intellect",
  qwen: "通义千问",
  recraft: "Recraft",
  rekaai: "Reka AI",
  relace: "Relace",
  sao10k: "Sao10K",
  "sentence transformers": "Sentence Transformers",
  sesame: "Sesame",
  sourceful: "Sourceful",
  stepfun: "阶跃星辰",
  switchpoint: "Switchpoint",
  tencent: "腾讯",
  thedrummer: "TheDrummer",
  thenlper: "Thenlper",
  undi95: "Undi95",
  upstage: "Upstage",
  venice: "Venice",
  writer: "Writer",
  xai: "xAI",
  xiaomi: "小米",
  "z.ai": "Z.ai",
  zyphra: "Zyphra",
  其他: "其他",
  模镜: "模镜",
};

const capabilityPlainText: Record<string, string> = {
  text: "文字处理",
  image: "看懂图片和文字",
  code: "写代码、查 bug",
  tool: "调用工具办事",
  audio: "语音对话",
  video: "理解视频内容",
  reasoning: "复杂推理",
};

const jobCapabilityPlainText: Record<string, string> = {
  text_chat: "文字对话",
  coding: "编程开发",
  reasoning: "推理分析",
  tool_use: "工具调用",
  document_understanding: "文档理解",
  image_understanding: "图片识别",
  image_generation: "图片生成与编辑",
  audio_understanding: "音频理解",
  transcription: "语音转写",
  speech_synthesis: "语音合成",
  music_generation: "音乐生成",
  realtime_voice: "实时语音",
  video_understanding: "视频理解",
  video_generation: "视频生成",
  embedding: "资料向量化",
  rerank: "检索重排",
  translation: "翻译",
  safety: "安全审核",
  world_generation: "3D 世界生成",
};

const categoryPlainText: Record<string, string> = {
  analysis: "资料分析",
  audio: "音频处理",
  chat: "日常聊天",
  coding: "编程开发",
  embeddings: "资料检索",
  image_generation: "图片生成",
  long_context: "长文档阅读",
  low_cost: "批量低成本任务",
  math: "数学推导",
  multimodal: "图文混合任务",
  reasoning: "复杂问题拆解",
  rerank: "搜索结果排序",
  roleplay: "角色扮演",
  safety: "内容安全审核",
  speech: "语音合成",
  transcription: "语音转文字",
  translation: "翻译润色",
  video: "视频理解",
  vision: "看图分析",
};

const runTypePlainText: Record<string, string> = {
  agent_handoff: "智能体交接",
  agent_task: "智能体任务",
  chat: "聊天",
  goal: "长期目标",
  workflow: "工作流",
  workflow_agent: "工作流智能体",
  xpert_automation: "智能体自动化",
  xpert_evaluation: "智能体评测",
  xpert_evolution: "智能体优化",
};

const runStatusPlainText: Record<string, string> = {
  cancelled: "已取消",
  completed: "已完成",
  failed: "失败",
  paused: "已暂停",
  pending: "待处理",
  running: "运行中",
  waiting_approval: "等待审批",
};

function titleCaseProvider(value: string) {
  return value
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function normalizeProviderKey(value: string) {
  return value.trim().replace(/^~/, "~").toLowerCase();
}

export function getProviderDisplayName(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "未知单位";

  const direct = providerDisplayNameMap[trimmed];
  if (direct) return direct;

  const normalized = providerDisplayNameMap[normalizeProviderKey(trimmed)];
  if (normalized) return normalized;

  return titleCaseProvider(trimmed.replace(/^~/, ""));
}

export function deriveProviderFromModel(model: Model) {
  if (model.model_author && model.model_author !== "模镜") {
    return getProviderDisplayName(model.model_author);
  }

  if (model.provider && model.provider !== "其他") {
    return getProviderDisplayName(model.provider);
  }

  const idProvider = model.id.split("/")[0] ?? "";
  return getProviderDisplayName(idProvider);
}

export function providerFilterMatches(model: Model, selectedProvider: string) {
  if (selectedProvider === "all") return true;

  const candidates = [
    model.provider,
    model.model_author,
    deriveProviderFromModel(model),
    getProviderDisplayName(model.provider),
    getProviderDisplayName(model.model_author),
  ];

  return candidates.some(
    (candidate) =>
      candidate === selectedProvider ||
      getProviderDisplayName(candidate) === selectedProvider,
  );
}

function formatContextWorkload(contextLength: number) {
  if (contextLength >= 1_000_000) {
    return `${Math.round(contextLength / 1_000_000)}M token`;
  }

  return `${Math.max(1, Math.round(contextLength / 1000))}K token`;
}

export function getFriendlyCapabilityLabel(value: string) {
  return capabilityPlainText[value] ?? value;
}

export function getFriendlyJobCapabilityLabel(value: string) {
  return jobCapabilityPlainText[value] ?? value;
}

export function getFriendlyCategoryLabel(value: string) {
  return categoryPlainText[value] ?? value;
}

export function replaceLegacyAgentTerms(value: string) {
  return value
    .replace(/\bXpert Studio\b/g, "Agent Studio")
    .replace(/\bXpert Automation\b/g, "智能体自动化")
    .replace(/\bXpert\b/g, "智能体");
}

export function getFriendlyRunTypeLabel(value: string) {
  return runTypePlainText[value] ?? replaceLegacyAgentTerms(value);
}

export function getFriendlyRunStatusLabel(value: string) {
  return runStatusPlainText[value] ?? value;
}

export function buildFriendlyTalentIntro(model: Model) {
  const provider = deriveProviderFromModel(model);
  const skills = model.job_capabilities
    .slice(0, 3)
    .map(getFriendlyJobCapabilityLabel)
    .join("、");
  const scenes = model.categories
    .slice(0, 3)
    .map(getFriendlyCategoryLabel)
    .join("、");
  const workload = formatContextWorkload(model.context_length);

  return `我来自 ${provider}，擅长${skills || "通用 AI 工作"}。适合接下${scenes || "日常问答和资料处理"}这类活儿，一次能记住约 ${workload} 的上下文。`;
}
