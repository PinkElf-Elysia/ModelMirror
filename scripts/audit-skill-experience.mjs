import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  SKILL_CATEGORIES,
  auditedInstallSource,
  classifySkill,
  describeSkillInChinese,
  hasAuditedInstallMismatch,
  hasReadableChinese,
  inferInstallStatus,
} from "../client/src/data/skillCatalogPolicy.ts";

function readJson(path) {
  return JSON.parse(readFileSync(resolve(path), "utf8"));
}

const anbeime = readJson("client/src/data/anbeimeSkillCatalog.generated.json");
const voltagent = readJson("client/src/data/voltagentSkillCatalog.generated.json");
const sourceRecords = [
  {
    name: "PDF 文档处理技能",
    description: "让模型按标准流程处理 PDF 文档。",
    category: "内容与办公",
    installStatus: "ready",
    sourceKey: "https://github.com/anthropics/skills#skills/pdf",
  },
  {
    name: "XLSX 表格处理技能",
    description: "让模型理解电子表格任务并辅助分析。",
    category: "内容与办公",
    installStatus: "ready",
    sourceKey: "https://github.com/anthropics/skills#skills/xlsx",
  },
  {
    name: "TypeScript TDD 技能",
    description: "通过测试先行改进 TypeScript 工程质量。",
    category: "开发与测试",
    installStatus: "ready",
    sourceKey: "https://github.com/mattpocock/skills#skills/engineering/tdd",
  },
  {
    name: "Agent Skills 开放标准",
    description: "定义 Skill 文件夹结构和渐进加载方式。",
    category: "AI 与智能体",
    installStatus: "reference",
  },
  ...anbeime.projects.map((project) => {
    const input = {
      name: project.name,
      description: project.description,
      category: project.category,
      tags: project.tags,
    };
    const category = classifySkill(input);
    return {
      name: project.name,
      description: describeSkillInChinese(input, category),
      category,
      installStatus: "ready",
      sourceKey: `${anbeime.source.repoUrl.toLowerCase()}#${project.subPath}`,
    };
  }),
  ...voltagent.projects.map((project) => {
    const input = {
      name: project.name,
      description: project.description,
      category: project.category,
      sourceGroup: project.sourceGroup,
      tags: project.tags,
    };
    const category = classifySkill(input);
    const installSource = project.installSource ?? auditedInstallSource(project.sourceUrl);
    return {
      name: project.name,
      description: describeSkillInChinese(input, category),
      category,
      installStatus: inferInstallStatus(project.sourceUrl, Boolean(installSource)),
      sourceKey: installSource
        ? `${installSource.repoUrl.toLowerCase()}#${installSource.subPath}`
        : undefined,
    };
  }),
];

const seenSourceKeys = new Set();
const records = sourceRecords.filter((record) => {
  if (!record.sourceKey) return true;
  if (seenSourceKeys.has(record.sourceKey)) return false;
  seenSourceKeys.add(record.sourceKey);
  return true;
});

const categoryCounts = Object.fromEntries(SKILL_CATEGORIES.map((category) => [category, 0]));
const installStatusCounts = { ready: 0, manual: 0, pending: 0, reference: 0 };
for (const record of records) {
  if (!(record.category in categoryCounts)) {
    throw new Error(`未归类：${record.name} -> ${record.category}`);
  }
  if (!(record.installStatus in installStatusCounts)) {
    throw new Error(`安装状态缺失：${record.name}`);
  }
  if (!hasReadableChinese(record.description)) {
    throw new Error(`主说明不是清晰中文：${record.name} -> ${record.description}`);
  }
  categoryCounts[record.category] += 1;
  installStatusCounts[record.installStatus] += 1;
}

const sparseCategories = Object.entries(categoryCounts).filter(([, count]) => count < 3);
if (sparseCategories.length > 0) {
  throw new Error(`仍有碎片分类：${JSON.stringify(sparseCategories)}`);
}

const auditedBatch = voltagent.projects.filter(
  (project) => !project.installSource && auditedInstallSource(project.sourceUrl),
).length;
if (auditedBatch !== 185) {
  throw new Error(`三批核验安装源应为 185 项，实际为 ${auditedBatch} 项`);
}
const mismatchBatch = voltagent.projects.filter((project) =>
  hasAuditedInstallMismatch(project.sourceUrl),
).length;
if (mismatchBatch !== 35) {
  throw new Error(`来源失配记录应为 35 项，实际为 ${mismatchBatch} 项`);
}

const auditedByRepository = {};
const mismatchByPublisher = {};
for (const project of voltagent.projects) {
  const installSource = !project.installSource
    ? auditedInstallSource(project.sourceUrl)
    : undefined;
  if (installSource) {
    if (
      installSource.subPath.split("/").some(
        (part) => !part || part === "." || part === ".." || !/^[A-Za-z0-9_.-]+$/.test(part),
      )
    ) {
      throw new Error(`安装子目录不安全：${project.name} -> ${installSource.subPath}`);
    }
    auditedByRepository[installSource.repoUrl] =
      (auditedByRepository[installSource.repoUrl] ?? 0) + 1;
  }
  if (hasAuditedInstallMismatch(project.sourceUrl)) {
    mismatchByPublisher[project.publisher] =
      (mismatchByPublisher[project.publisher] ?? 0) + 1;
  }
}

const expectedAuditedByRepository = {
  "https://github.com/anthropics/skills": 17,
  "https://github.com/openai/skills": 36,
  "https://github.com/microsoft/skills": 132,
};
const countsMatch = (actual, expected) =>
  Object.keys(actual).length === Object.keys(expected).length &&
  Object.entries(expected).every(([key, count]) => actual[key] === count);
if (!countsMatch(auditedByRepository, expectedAuditedByRepository)) {
  throw new Error(`核验仓库批次数量变化：${JSON.stringify(auditedByRepository)}`);
}
const expectedMismatchByPublisher = { getsentry: 28, openai: 6, microsoft: 1 };
if (!countsMatch(mismatchByPublisher, expectedMismatchByPublisher)) {
  throw new Error(`来源失配批次数量变化：${JSON.stringify(mismatchByPublisher)}`);
}

console.log(`体验审计通过：${records.length} 条来源记录`);
console.table(categoryCounts);
console.table(installStatusCounts);
console.log(`新增核验安装源：${auditedBatch} 项`);
console.log(`已阻止失配安装源：${mismatchBatch} 项`);
console.table(auditedByRepository);
console.table(mismatchByPublisher);
