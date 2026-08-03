import anbeimeCatalogJson from "./anbeimeSkillCatalog.generated.json";
import voltagentCatalogJson from "./voltagentSkillCatalog.generated.json";
import {
  auditedInstallSource,
  classifySkill,
  describeSkillInChinese,
  hasAuditedInstallMismatch,
  inferInstallStatus,
  manualInstallCommand,
  type SkillInstallStatus,
} from "./skillCatalogPolicy";

export interface SkillInstallSource {
  repoUrl: string;
  subPath: string;
  verifiedCommit?: string;
}

export type SkillProjectKind = "skill" | "skillset";

export interface SkillProject {
  id: string;
  name: string;
  repoName: string;
  repoUrl: string;
  category: string;
  kind: SkillProjectKind;
  description: string;
  readmeSummary: string;
  stars: number;
  language: string;
  updatedAt: string;
  installCommand: string;
  installNote: string;
  installStatus: SkillInstallStatus;
  installSource?: SkillInstallSource;
  sourceDescription?: string;
  tags: string[];
  includedSkills?: string[];
  sourceCommit?: string;
  catalogName?: string;
  catalogUrl?: string;
  publisher?: string;
  sourceGroup?: string;
}

interface GeneratedSkillIndex {
  source: {
    repoName: string;
    repoUrl: string;
    commit: string;
    updatedAt: string;
    stars: number;
  };
  projects: Array<{
    id: string;
    name: string;
    kind: SkillProjectKind;
    category: string;
    publisher: string;
    sourceGroup: string;
    description: string;
    sourceUrl: string;
    installSource: SkillInstallSource | null;
    tags: string[];
    includedSkills: string[];
  }>;
}

interface GeneratedSkillCatalog {
  source: {
    repoName: string;
    repoUrl: string;
    commit: string;
    updatedAt: string;
    stars: number;
  };
  projects: Array<{
    id: string;
    name: string;
    kind: SkillProjectKind;
    category: string;
    description: string;
    subPath: string;
    language: string;
    tags: string[];
    includedSkills: string[];
  }>;
}

const curatedSkillProjects: SkillProject[] = [
  {
    id: "anthropic-pdf-skill",
    name: "PDF 文档处理技能",
    repoName: "anthropics/skills",
    repoUrl: "https://github.com/anthropics/skills",
    category: "内容与办公",
    kind: "skill",
    description:
      "让模型按标准流程处理 PDF：抽取内容、整理结构、摘要重点，适合合同、论文、报告和说明书。",
    readmeSummary:
      "Anthropic 官方 Skills 示例库中的 PDF 技能，目录包含 SKILL.md 与相关脚本资源，可作为文档解析类技能的基础模板。",
    stars: 147754,
    language: "Markdown / Python",
    updatedAt: "2026-06-08",
    installCommand:
      "git clone --depth 1 --filter=blob:none --sparse https://github.com/anthropics/skills\ncd skills\ngit sparse-checkout set skills/pdf",
    installNote: "模镜会通过后端 Skill 管理器执行 sparse checkout，只安装 skills/pdf 子目录。",
    installStatus: "ready",
    installSource: {
      repoUrl: "https://github.com/anthropics/skills",
      subPath: "skills/pdf",
    },
    tags: ["官方示例", "PDF", "文档摘要"],
  },
  {
    id: "anthropic-xlsx-skill",
    name: "XLSX 表格处理技能",
    repoName: "anthropics/skills",
    repoUrl: "https://github.com/anthropics/skills",
    category: "内容与办公",
    kind: "skill",
    description:
      "让模型理解电子表格任务：读取工作簿、解释数据、辅助分析和生成表格处理建议。",
    readmeSummary:
      "Anthropic 官方 Skills 示例库中的 XLSX 技能，面向 Excel 和电子表格工作流，可作为财务、运营、数据分析任务的技能模板。",
    stars: 147754,
    language: "Markdown / Python",
    updatedAt: "2026-06-08",
    installCommand:
      "git clone --depth 1 --filter=blob:none --sparse https://github.com/anthropics/skills\ncd skills\ngit sparse-checkout set skills/xlsx",
    installNote: "模镜会通过后端 Skill 管理器执行 sparse checkout，只安装 skills/xlsx 子目录。",
    installStatus: "ready",
    installSource: {
      repoUrl: "https://github.com/anthropics/skills",
      subPath: "skills/xlsx",
    },
    tags: ["官方示例", "Excel", "数据分析"],
  },
  {
    id: "mattpocock-tdd-skill",
    name: "TypeScript TDD 技能",
    repoName: "mattpocock/skills",
    repoUrl: "https://github.com/mattpocock/skills",
    category: "开发与测试",
    kind: "skill",
    description:
      "把 AI 训练成更稳的 TypeScript 工程搭档，强调测试先行、逐步实现和代码质量反馈。",
    readmeSummary:
      "Matt Pocock 的 Skills 仓库面向工程实践，engineering/tdd 技能聚焦测试驱动开发，适合让模型按红绿重构节奏协助写代码。",
    stars: 2100,
    language: "Markdown / TypeScript",
    updatedAt: "2026-06-08",
    installCommand:
      "git clone --depth 1 --filter=blob:none --sparse https://github.com/mattpocock/skills\ncd skills\ngit sparse-checkout set skills/engineering/tdd",
    installNote:
      "模镜会通过后端 Skill 管理器执行 sparse checkout，只安装 skills/engineering/tdd 子目录。",
    installStatus: "ready",
    installSource: {
      repoUrl: "https://github.com/mattpocock/skills",
      subPath: "skills/engineering/tdd",
    },
    tags: ["TypeScript", "TDD", "工程质量"],
  },
  {
    id: "agent-skills-standard",
    name: "Agent Skills 开放标准",
    repoName: "agentskills/agentskills",
    repoUrl: "https://github.com/agentskills/agentskills",
    category: "AI 与智能体",
    kind: "skill",
    description:
      "定义 Skill 文件夹结构、SKILL.md 元数据和渐进加载方式，适合团队统一扩展包格式。",
    readmeSummary:
      "Agent Skills 是轻量开放格式，核心是包含 SKILL.md 的文件夹，也可携带脚本、参考资料、模板和资源。",
    stars: 20110,
    language: "Markdown",
    updatedAt: "2026-06-08",
    installCommand:
      "git clone https://github.com/agentskills/agentskills.git\n# 参考 template 目录创建内部 Skill",
    installNote:
      "这是规范与模板仓库，不是单个可安装 Skill；适合团队参考并创建自己的技能包。",
    installStatus: "reference",
    tags: ["开放标准", "模板", "规范"],
  },
];

const anbeimeCatalog = anbeimeCatalogJson as GeneratedSkillCatalog;

const anbeimeSkillProjects: SkillProject[] = anbeimeCatalog.projects.map((project) => {
  const policyInput = {
    name: project.name,
    description: project.description,
    category: project.category,
    tags: project.tags,
  };
  const category = classifySkill(policyInput);
  return {
    id: project.id,
    name: project.name,
    repoName: anbeimeCatalog.source.repoName,
    repoUrl: anbeimeCatalog.source.repoUrl,
    category,
    kind: project.kind,
    description: describeSkillInChinese(policyInput, category),
    sourceDescription: project.description,
    readmeSummary:
      project.kind === "skillset"
        ? `该 SkillSet 包含 ${project.includedSkills.length} 个子技能，安装父目录时会一并保留相关 SKILL.md、脚本和参考资料。`
        : "目录数据由本地 skill-main 与远端 main 提交核对后生成。安装前请检查第三方说明和运行依赖。",
    stars: anbeimeCatalog.source.stars,
    language: project.language,
    updatedAt: anbeimeCatalog.source.updatedAt,
    installCommand: `git clone --depth 1 --filter=blob:none --sparse ${anbeimeCatalog.source.repoUrl}\ncd skill\ngit sparse-checkout set ${project.subPath}`,
    installNote:
      "模镜只复制该目录，不会在安装时执行其中脚本。使用社区 Skill 前请先检查依赖、外部服务与凭据要求。",
    installStatus: "ready",
    installSource: {
      repoUrl: anbeimeCatalog.source.repoUrl,
      subPath: project.subPath,
    },
    tags: project.tags,
    includedSkills: project.includedSkills,
    sourceCommit: anbeimeCatalog.source.commit,
    catalogName: anbeimeCatalog.source.repoName,
    catalogUrl: anbeimeCatalog.source.repoUrl,
  };
});

const primarySkillProjects: SkillProject[] = [
  ...curatedSkillProjects,
  ...anbeimeSkillProjects,
];

const installedSourceKeys = new Set(
  primarySkillProjects.flatMap((project) =>
    project.installSource
      ? [`${project.installSource.repoUrl.toLowerCase()}#${project.installSource.subPath}`]
      : [],
  ),
);
const voltagentCatalog = voltagentCatalogJson as GeneratedSkillIndex;
const voltagentSkillProjects: SkillProject[] = voltagentCatalog.projects
  .map((project) => ({
    ...project,
    resolvedInstallSource:
      (project.installSource ??
        auditedInstallSource(project.sourceUrl)) as SkillInstallSource | undefined,
  }))
  .filter((project) => {
    if (!project.resolvedInstallSource) return true;
    const key = `${project.resolvedInstallSource.repoUrl.toLowerCase()}#${project.resolvedInstallSource.subPath}`;
    if (installedSourceKeys.has(key)) return false;
    installedSourceKeys.add(key);
    return true;
  })
  .map((project) => {
    const policyInput = {
      name: project.name,
      description: project.description,
      category: project.category,
      sourceGroup: project.sourceGroup,
      tags: project.tags,
    };
    const category = classifySkill(policyInput);
    const installStatus = inferInstallStatus(
      project.sourceUrl,
      Boolean(project.resolvedInstallSource),
    );
    const manualCommand = manualInstallCommand(project.sourceUrl);
    const wasAudited = !project.installSource && Boolean(project.resolvedInstallSource);
    return {
      id: project.id,
      name: project.name,
      repoName: project.name,
      repoUrl: project.sourceUrl,
      category,
      kind: project.kind,
      description: describeSkillInChinese(policyInput, category),
      sourceDescription: project.description,
      readmeSummary: `收录分组：${project.sourceGroup}。该条目来自 VoltAgent 维护的外部 Skill 索引。`,
      stars: voltagentCatalog.source.stars,
      language: "Markdown",
      updatedAt: voltagentCatalog.source.updatedAt,
      installCommand: project.resolvedInstallSource
        ? `git clone --depth 1 --filter=blob:none --sparse ${project.resolvedInstallSource.repoUrl}\ncd skill\ngit sparse-checkout set ${project.resolvedInstallSource.subPath}`
        : manualCommand,
      installNote: project.resolvedInstallSource
        ? wasAudited
          ? "该安装源已在批次审计中核对 GitHub 仓库的 SKILL.md 路径，可由模镜执行 sparse checkout 安装。"
          : "索引提供了明确的 GitHub Skill 子目录，模镜可执行 sparse checkout 安装。"
        : installStatus === "manual"
          ? "来源页提供外部 CLI 安装命令；当前需离开模镜手动执行，后续批次将继续核验其 GitHub 子目录。"
          : installStatus === "pending"
            ? hasAuditedInstallMismatch(project.sourceUrl)
              ? "来源页给出的 Skill 名称或路径与当前 GitHub 仓库树不一致，已暂停安装并等待上游修正或再次核验。"
              : "已定位 GitHub 仓库，但尚未核对具体 SKILL.md 子目录，因此暂不开放一键安装。"
            : "这是资料或产品页面，尚未发现可由当前后端验证的 Skill 安装目录。",
      installStatus,
      installSource: project.resolvedInstallSource,
      tags: project.tags,
      includedSkills: project.includedSkills,
      sourceCommit:
        project.resolvedInstallSource?.verifiedCommit ?? voltagentCatalog.source.commit,
      catalogName: voltagentCatalog.source.repoName,
      catalogUrl: voltagentCatalog.source.repoUrl,
      publisher: project.publisher,
      sourceGroup: project.sourceGroup,
    };
  });

export const skillProjects: SkillProject[] = [
  ...primarySkillProjects,
  ...voltagentSkillProjects,
];

