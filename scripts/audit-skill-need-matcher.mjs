import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { findSkillsForNeed } from "../client/src/data/skillNeedMatcher.ts";
import {
  auditedInstallSource,
  classifySkill,
  describeSkillInChinese,
} from "../client/src/data/skillCatalogPolicy.ts";
import { SKILL_SOURCE_VERIFICATION } from "../client/src/data/skillSourceVerification.generated.ts";

const fixtures = [
  {
    id: "pdf",
    name: "PDF 文档处理",
    category: "内容与办公",
    kind: "skill",
    description: "提取、整理和填写 PDF 文档及合同表单。",
    sourceDescription: "Extract text and fill PDF forms",
    tags: ["PDF", "文档"],
    installStatus: "ready",
  },
  {
    id: "spreadsheet",
    name: "XLSX 表格分析",
    category: "数据与研究",
    kind: "skill",
    description: "分析 Excel 销售数据并生成工作簿。",
    sourceDescription: "Analyze spreadsheet metrics",
    tags: ["Excel", "XLSX", "数据分析"],
    installStatus: "ready",
  },
  {
    id: "web-test",
    name: "Playwright 网页自动化测试",
    category: "开发与测试",
    kind: "skill",
    description: "为 React 网页编写端到端测试并检查交互。",
    sourceDescription: "Test local web applications using Playwright",
    tags: ["Playwright", "E2E", "React"],
    installStatus: "ready",
  },
  {
    id: "frontend",
    name: "React 前端开发",
    category: "开发与测试",
    kind: "skill",
    description: "设计并实现 React 与 Tailwind 网页界面。",
    sourceDescription: "Build React interfaces",
    tags: ["React", "UI"],
    installStatus: "ready",
  },
  {
    id: "database-security",
    name: "Postgres 数据库安全审计",
    category: "安全与运维",
    kind: "skill",
    description: "审计 Postgres 权限、SQL 查询与数据库漏洞。",
    sourceDescription: "Audit PostgreSQL security and access controls",
    tags: ["Postgres", "安全", "审计"],
    installStatus: "pending",
  },
  {
    id: "generic-security-ready",
    name: "代码安全检查",
    category: "安全与运维",
    kind: "skill",
    description: "检查应用代码中的常见安全漏洞。",
    sourceDescription: "Secure code review",
    tags: ["安全"],
    installStatus: "ready",
  },
  {
    id: "marketing",
    name: "SEO 营销计划",
    category: "营销与增长",
    kind: "skillset",
    description: "规划 SEO 内容、推广活动与增长指标。",
    sourceDescription: "Marketing campaign planning",
    tags: ["SEO", "营销"],
    includedSkills: ["内容规划", "增长分析"],
    installStatus: "reference",
  },
];

function expectTop(query, expectedId) {
  const matches = findSkillsForNeed(query, fixtures);
  assert.ok(matches.length > 0, `应为“${query}”返回匹配结果`);
  assert.equal(matches[0].project.id, expectedId, `“${query}”首项不正确`);
  assert.ok(matches[0].reasons.length > 0, "匹配结果必须解释原因");
  assert.ok(
    matches[0].reasons.every(
      (reason) =>
        reason.label &&
        ["direct", "expanded"].includes(reason.origin) &&
        reason.matchedTerms.length > 0,
    ),
    "匹配原因必须包含字段和命中词",
  );
}

expectTop("帮我提取合同 PDF 并填写表单", "pdf");
expectTop("分析 Excel 销售数据，生成工作簿", "spreadsheet");
expectTop("为 React 网页编写自动化测试", "web-test");
expectTop("audit postgres database security", "database-security");
expectTop("制定 SEO 营销增长计划", "marketing");

assert.deepEqual(
  findSkillsForNeed("量子引力弦理论实验", fixtures),
  [],
  "无可靠匹配时必须返回空结果",
);

const tied = findSkillsForNeed("安全检查", [
  { ...fixtures[5], id: "pending", installStatus: "pending" },
  { ...fixtures[5], id: "ready", installStatus: "ready" },
]);
assert.equal(tied[0].project.id, "ready", "相关度相近时可安装项应优先");
assert.ok(
  tied.some((match) => match.project.installStatus === "pending"),
  "高度相关的待核验项不应被隐藏",
);

const firstRun = findSkillsForNeed("数据库安全审计", fixtures);
const secondRun = findSkillsForNeed("数据库安全审计", fixtures);
assert.deepEqual(
  firstRun.map((match) => match.project.id),
  secondRun.map((match) => match.project.id),
  "相同输入必须产生稳定排序",
);
assert.ok(firstRun.length <= 6, "默认最多返回 6 项");

const anbeime = JSON.parse(
  readFileSync("client/src/data/anbeimeSkillCatalog.generated.json", "utf8"),
);
const voltagent = JSON.parse(
  readFileSync("client/src/data/voltagentSkillCatalog.generated.json", "utf8"),
);
const statusFor = (id) =>
  SKILL_SOURCE_VERIFICATION[id].status === "verified"
    ? "ready"
    : SKILL_SOURCE_VERIFICATION[id].status;
const actualCatalog = [
  {
    id: "anthropic-pdf-skill",
    name: "PDF 文档处理技能",
    category: "内容与办公",
    kind: "skill",
    description: "让模型按标准流程处理 PDF 文档、合同和表单。",
    sourceDescription: "PDF document processing",
    tags: ["PDF", "文档摘要"],
    installStatus: statusFor("anthropic-pdf-skill"),
  },
  {
    id: "anthropic-xlsx-skill",
    name: "XLSX 表格处理技能",
    category: "内容与办公",
    kind: "skill",
    description: "分析电子表格和 Excel 数据。",
    sourceDescription: "Spreadsheet analysis",
    tags: ["Excel", "数据分析"],
    installStatus: statusFor("anthropic-xlsx-skill"),
  },
  {
    id: "mattpocock-tdd-skill",
    name: "TypeScript TDD 技能",
    category: "开发与测试",
    kind: "skill",
    description: "通过测试先行改进 TypeScript 工程质量。",
    sourceDescription: "TypeScript test driven development",
    tags: ["TypeScript", "TDD"],
    installStatus: statusFor("mattpocock-tdd-skill"),
  },
  {
    id: "agent-skills-standard",
    name: "Agent Skills 开放标准",
    category: "AI 与智能体",
    kind: "skill",
    description: "定义 Skill 文件夹结构和渐进加载方式。",
    sourceDescription: "Agent Skills specification",
    tags: ["开放标准"],
    installStatus: statusFor("agent-skills-standard"),
  },
];
const sourceKeys = new Set([
  "https://github.com/anthropics/skills#skills/pdf",
  "https://github.com/anthropics/skills#skills/xlsx",
  "https://github.com/mattpocock/skills#skills/engineering/tdd",
]);
for (const project of anbeime.projects) {
  sourceKeys.add(`${anbeime.source.repoUrl.toLowerCase()}#${project.subPath}`);
  const input = {
    name: project.name,
    description: project.description,
    category: project.category,
    tags: project.tags,
  };
  const category = classifySkill(input);
  actualCatalog.push({
    id: project.id,
    name: project.name,
    category,
    kind: project.kind,
    description: describeSkillInChinese(input, category),
    sourceDescription: project.description,
    tags: project.tags,
    includedSkills: project.includedSkills,
    installStatus: statusFor(project.id),
  });
}
for (const project of voltagent.projects) {
  const source = project.installSource ?? auditedInstallSource(project.sourceUrl);
  if (source) {
    const key = `${source.repoUrl.toLowerCase()}#${source.subPath}`;
    if (sourceKeys.has(key)) continue;
    sourceKeys.add(key);
  }
  const verification = SKILL_SOURCE_VERIFICATION[project.id];
  assert.ok(verification, `实际目录缺少核验证据：${project.id}`);
  const input = {
    name: project.name,
    description: project.description,
    category: project.category,
    sourceGroup: project.sourceGroup,
    tags: project.tags,
  };
  const category = classifySkill(input);
  actualCatalog.push({
    id: project.id,
    name: project.name,
    category,
    kind: project.kind,
    description: describeSkillInChinese(input, category),
    sourceDescription: project.description,
    tags: project.tags,
    includedSkills: project.includedSkills,
    installStatus: statusFor(project.id),
    publisher: project.publisher,
    sourceGroup: project.sourceGroup,
  });
}
assert.equal(actualCatalog.length, 1242, "实际目录冒烟测试必须覆盖 1,242 项");
for (const query of [
  "提取 PDF 合同",
  "分析 Excel 销售表格",
  "为 React 网页编写自动化测试",
  "审计 Postgres 数据库安全",
  "制定 SEO 营销计划",
]) {
  const matches = findSkillsForNeed(query, actualCatalog);
  assert.ok(matches.length > 0, `实际目录应能匹配：${query}`);
  assert.ok(matches.every((match) => match.reasons.length > 0));
}

console.log(
  "Skill 需求匹配审计通过：合成用例与 1,242 项实际目录的中英文、状态优先、解释和无匹配场景均符合预期",
);
