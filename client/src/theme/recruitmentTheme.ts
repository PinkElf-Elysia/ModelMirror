interface TalentProfileSource {
  id: string;
  provider: string;
  description: string;
  context_length: number;
  price_cny: {
    input: number;
    output: number;
  };
  pricing_status?: "fixed" | "free" | "dynamic";
  capabilities: string[];
  categories: string[];
  tags: string[];
}

export const recruitmentTheme = {
  eventTitle: "AI 牛马招聘会",
  eventSubtitle: "找到你的专属 AI 打工人",
  eventPitch: "海量模型，现场面试，当场录用。按技能、薪资、经验值快速筛出靠谱候选人。",
  listSearchPlaceholder: "搜索候选人、用人单位、技能或求职宣言",
  noResultTitle: "招聘会现场暂时没有符合要求的候选人",
  noResultBody: "试试放宽岗位要求，或清空筛选后重新逛展。",
  filterPanelTitle: "招聘岗位分类",
  filterPanelDescription: "按岗位技能、薪资预期和候选人背景筛选",
  promptPanelTitle: "面试题库",
  promptPanelSubtitle: "挑一道题，现场考考候选人",
  superPromptTitle: "魔鬼面试官模式",
  superPromptDescription: "自动把问题包装成更严格的面试题",
  chatPlaceholder: "向你的候选人提问...",
  interviewWaiting: "正在等待候选人入场...",
};

export const recruitmentFilterTitles = {
  provider: "用人单位/猎头公司",
  inputModalities: "工作技能",
  categories: "岗位类型",
  context: "工作年限/经验值",
  pricing: "期望薪资",
  series: "毕业院校/系列",
  parameters: "工具熟练度",
  distillable: "可带徒弟",
  zdr: "保密意识",
  routing: "本地驻场",
  authors: "候选人作者",
  inactive: "历史候选人",
};

export const recruitmentTagLabels: Record<string, string> = {
  精选: "优秀员工",
  新: "新入场",
  热门: "本月之星",
  多模态: "全能工",
  开源: "开源人才",
  免费: "免费试工",
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

export function getRecruitmentTag(tag: string) {
  return recruitmentTagLabels[tag] ?? tag;
}

export function getRecruitmentCapability(capability: string) {
  return recruitmentCapabilityLabels[capability] ?? capability;
}

function hashText(value: string) {
  return value.split("").reduce((total, char) => total + char.charCodeAt(0), 0);
}

export function getTalentStats(model: TalentProfileSource) {
  const seed = hashText(model.id);
  const popularity = 72 + (seed % 28);
  const hiredCount = 120 + (seed % 880);
  const isBudgetFriendly =
    model.pricing_status !== "dynamic" &&
    (model.pricing_status === "free" || model.price_cny.input <= 1);
  const urgent = isBudgetFriendly || model.tags.some((tag) => ["热门", "精选", "免费"].includes(tag));

  return {
    popularity,
    hiredCount,
    urgent,
  };
}

export function buildPersonaDescription(model: TalentProfileSource) {
  const skills = model.capabilities
    .slice(0, 3)
    .map(getRecruitmentCapability)
    .join("、");
  const scenes = model.categories.slice(0, 3).join("、") || "通用 AI 任务";
  const contextInK = Math.max(1, Math.round(model.context_length / 1000));

  return `我来自 ${model.provider}，主攻${skills || "通用岗位"}，适合${scenes}。我可以处理约 ${contextInK}K token 的上下文，期待接下你的下一份 AI 工作。`;
}
