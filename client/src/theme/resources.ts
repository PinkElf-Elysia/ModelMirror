export type ResourceKey =
  | "models"
  | "agents"
  | "mcps"
  | "skills"
  | "runtime"
  | "prompts";

export interface ResourceNavItem {
  key: ResourceKey;
  title: string;
  shortTitle: string;
  english: string;
  path: string;
  icon: string;
  description: string;
}

export const resourceNavItems: ResourceNavItem[] = [
  {
    key: "models",
    title: "模型招聘会",
    shortTitle: "模型",
    english: "Models",
    path: "/models",
    icon: "模",
    description: "挑选可直接面试的大模型候选人",
  },
  {
    key: "agents",
    title: "AI 人才市场",
    shortTitle: "人才",
    english: "Agents",
    path: "/agents",
    icon: "才",
    description: "招募带完整岗位人设的 AI 专家",
  },
  {
    key: "mcps",
    title: "MCP 工具采购",
    shortTitle: "MCP",
    english: "MCPs",
    path: "/mcps",
    icon: "插",
    description: "采购可接入工作流的工具和服务",
  },
  {
    key: "skills",
    title: "Skill 技能培训",
    shortTitle: "技能",
    english: "Skills",
    path: "/skills",
    icon: "训",
    description: "管理可复用的模型操作技能",
  },
  {
    key: "runtime",
    title: "Runtime 运维",
    shortTitle: "运维",
    english: "Runtime",
    path: "/runtime",
    icon: "运",
    description: "查看 MCP、工具、运行记录和 Skill 状态",
  },
  {
    key: "prompts",
    title: "提示词市场",
    shortTitle: "提示",
    english: "Prompts",
    path: "/prompts",
    icon: "词",
    description: "浏览可直接开问的任务模板",
  },
];

export const resourceComingSoonCopy: Record<
  Exclude<ResourceKey, "models" | "agents" | "runtime">,
  { title: string; description: string; actionHint: string }
> = {
  mcps: {
    title: "MCP 工具采购即将上线",
    description: "工具招领处正在搭建货架，很快就能按协议、能力和适用场景采购 MCP 工具。",
    actionHint: "先去招聘会挑模型，等工具区开张后再来补装备。",
  },
  skills: {
    title: "Skill 技能培训即将上线",
    description: "培训教室正在排课，未来会集中展示可复用的 Skill、工作流和操作手册。",
    actionHint: "先逛 AI 人才市场，挑一位专家进面试间试试。",
  },
  prompts: {
    title: "提示词市场即将上线",
    description: "题库摊位正在整理招牌，后续会把提示词按岗位、场景和难度统一上架。",
    actionHint: "当前可以在面试间右侧题库里先试用现有提示词。",
  },
};
