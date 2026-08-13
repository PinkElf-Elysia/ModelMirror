interface TalentProfileSource {
  provider: string;
  context_length: number;
  job_capabilities: string[];
  categories: string[];
}

export const recruitmentTheme = {
  eventTitle: "AI 牛马招聘会",
  eventSubtitle: "找到你的专属 AI 打工人",
  eventPitch: "海量模型，现场面试，当场录用。按技能、薪资、经验值快速筛出靠谱候选人。",
  listSearchPlaceholder: "搜索候选人、用人单位、技能或求职宣言",
  noResultTitle: "招聘会现场暂时没有符合要求的候选人",
  noResultBody: "试试放宽岗位要求，或清空筛选后重新逛展。",
  filterPanelTitle: "招聘岗位分类",
  filterPanelDescription: "先按可接收的输入筛选，再按可完成的任务筛选",
  promptPanelTitle: "面试题库",
  promptPanelSubtitle: "挑一道题，现场考考候选人",
  superPromptTitle: "魔鬼面试官模式",
  superPromptDescription: "自动把问题包装成更严格的面试题",
  chatPlaceholder: "向你的候选人提问...",
  interviewWaiting: "正在等待候选人入场...",
};

export const recruitmentFilterTitles = {
  provider: "用人单位/猎头公司",
  inputModalities: "工作技能（可接收输入）",
  jobCapabilities: "岗位能力（可完成任务）",
  context: "工作年限/经验值",
  pricing: "期望薪资",
  series: "毕业院校/系列",
  parameters: "工具熟练度",
  distillable: "可带徒弟",
  zdr: "保密意识",
  routing: "本地驻场",
  authors: "候选人作者",
  inactive: "到期候选人",
};

export const recruitmentCapabilityLabels: Record<string, string> = {
  text: "文案岗",
  image: "视觉岗",
  code: "工程岗",
  tool: "工具岗",
  audio: "音频岗",
  video: "视频岗",
  reasoning: "策略岗",
};

export const recruitmentJobCapabilityLabels: Record<string, string> = {
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
  safety: "安全审核",
  world_generation: "3D 世界生成",
};

export function getRecruitmentCapability(capability: string) {
  return recruitmentCapabilityLabels[capability] ?? capability;
}

export function getRecruitmentJobCapability(capability: string) {
  return recruitmentJobCapabilityLabels[capability] ?? capability;
}

export function buildPersonaDescription(model: TalentProfileSource) {
  const skills = model.job_capabilities
    .slice(0, 3)
    .map(getRecruitmentJobCapability)
    .join("、");
  const scenes = model.categories.slice(0, 3).join("、") || "通用 AI 任务";
  const contextInK = Math.max(1, Math.round(model.context_length / 1000));

  return `我来自 ${model.provider}，主攻${skills || "通用岗位"}，适合${scenes}。我可以处理约 ${contextInK}K token 的上下文，期待接下你的下一份 AI 工作。`;
}
