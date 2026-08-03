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
import {
  REJECTED_OFFICIAL_SKILL_INSTALL_SOURCES,
  VERIFIED_OFFICIAL_SKILL_INSTALL_SOURCES,
} from "../client/src/data/officialSkillInstallSources.generated.ts";

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
if (auditedBatch !== 425) {
  throw new Error(`核验安装源应为 425 项，实际为 ${auditedBatch} 项`);
}
const mismatchBatch = voltagent.projects.filter((project) =>
  hasAuditedInstallMismatch(project.sourceUrl),
).length;
if (mismatchBatch !== 153) {
  throw new Error(`来源失配记录应为 153 项，实际为 ${mismatchBatch} 项`);
}

const generatedVerifiedEntries = Object.entries(
  VERIFIED_OFFICIAL_SKILL_INSTALL_SOURCES,
);
const generatedRejectedEntries = Object.entries(
  REJECTED_OFFICIAL_SKILL_INSTALL_SOURCES,
);
if (generatedVerifiedEntries.length !== 240) {
  throw new Error(`新增核验映射应为 240 项，实际为 ${generatedVerifiedEntries.length} 项`);
}
if (generatedRejectedEntries.length !== 118) {
  throw new Error(`新增拒绝映射应为 118 项，实际为 ${generatedRejectedEntries.length} 项`);
}
const catalogSourceUrls = new Set(voltagent.projects.map((project) => project.sourceUrl));
for (const [sourceUrl, source] of generatedVerifiedEntries) {
  if (!catalogSourceUrls.has(sourceUrl)) {
    throw new Error(`核验映射不属于当前目录：${sourceUrl}`);
  }
  if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(source.repoUrl)) {
    throw new Error(`核验仓库地址不合法：${source.repoUrl}`);
  }
  if (!/^[a-f0-9]{40}$/.test(source.verifiedCommit)) {
    throw new Error(`核验提交不合法：${sourceUrl} -> ${source.verifiedCommit}`);
  }
}
for (const [sourceUrl, rejection] of generatedRejectedEntries) {
  if (!catalogSourceUrls.has(sourceUrl) || !rejection.reason) {
    throw new Error(`拒绝映射不完整：${sourceUrl}`);
  }
  if (sourceUrl in VERIFIED_OFFICIAL_SKILL_INSTALL_SOURCES) {
    throw new Error(`来源同时出现在通过和拒绝映射：${sourceUrl}`);
  }
}
const repairedPathCount = generatedVerifiedEntries.filter(([, source]) =>
  source.pathResolution.includes("修正"),
).length;
if (repairedPathCount !== 30) {
  throw new Error(`唯一同名目录修正应为 30 项，实际为 ${repairedPathCount} 项`);
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

const expectedBaselineAuditedByRepository = {
  "https://github.com/anthropics/skills": 17,
  "https://github.com/openai/skills": 36,
  "https://github.com/microsoft/skills": 132,
};
if (
  !Object.entries(expectedBaselineAuditedByRepository).every(
    ([repoUrl, count]) => auditedByRepository[repoUrl] === count,
  )
) {
  throw new Error(`基础核验仓库批次数量变化：${JSON.stringify(auditedByRepository)}`);
}
const expectedBaselineMismatchByPublisher = { getsentry: 28, openai: 6, microsoft: 1 };
if (
  !Object.entries(expectedBaselineMismatchByPublisher).every(
    ([publisher, count]) => mismatchByPublisher[publisher] === count,
  )
) {
  throw new Error(`来源失配批次数量变化：${JSON.stringify(mismatchByPublisher)}`);
}

console.log(`体验审计通过：${records.length} 条来源记录`);
console.table(categoryCounts);
console.table(installStatusCounts);
console.log(`新增核验安装源：${auditedBatch} 项`);
console.log(`已阻止失配安装源：${mismatchBatch} 项`);
console.log(
  `本轮核验：${generatedVerifiedEntries.length} 项通过（${repairedPathCount} 项安全修正路径），${generatedRejectedEntries.length} 项未通过`,
);
console.log(
  `本轮通过来源覆盖 ${new Set(generatedVerifiedEntries.map(([, source]) => source.repoUrl)).size} 个 GitHub 仓库`,
);
