import anbeimeCatalogJson from "./anbeimeSkillCatalog.generated.json";
import voltagentCatalogJson from "./voltagentSkillCatalog.generated.json";
import {
  auditedInstallSource,
  classifySkill,
  describeSkillInChinese,
  type SkillInstallStatus,
} from "./skillCatalogPolicy";
import {
  SKILL_SOURCE_VERIFICATION,
  type SkillInstallMode,
  type SkillSetMode,
  type SkillSourceVerificationEvidence,
} from "./skillSourceVerification.generated";

export interface SkillInstallSource {
  repoUrl: string;
  subPath: string;
  verifiedCommit: string;
}

export type SkillProjectKind = "skill" | "skillset";

export interface SkillSetSummary {
  id: string;
  mode: SkillSetMode;
  repoUrl: string;
  verifiedCommit: string;
  scopeSubPath: string;
  skillDocumentCount: number;
  memberCount: number;
  nestedSkillCount: number;
  duplicateMemberCount: number;
}

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
  installMode: SkillInstallMode;
  installSource?: SkillInstallSource;
  skillSet?: SkillSetSummary;
  sourceDescription?: string;
  tags: string[];
  includedSkills?: string[];
  sourceCommit?: string;
  catalogName?: string;
  catalogUrl?: string;
  publisher?: string;
  sourceGroup?: string;
  verification: SkillSourceVerificationEvidence;
}

interface CatalogSkillInstallSource {
  repoUrl: string;
  subPath: string;
  verifiedCommit?: string;
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
    installSource: CatalogSkillInstallSource | null;
    tags: string[];
    includedSkills: string[];
  }>;
}

const skillVerificationById = SKILL_SOURCE_VERIFICATION as Record<
  string,
  SkillSourceVerificationEvidence
>;

function verificationFor(projectId: string) {
  return (
    skillVerificationById[projectId] ?? {
      status: "pending",
      sourceUrl: "",
      reasonCode: "no-install-source",
      reason: "该目录记录尚未进入统一来源核验。",
    }
  );
}

function installStatusFor(projectId: string): SkillInstallStatus {
  const verification = verificationFor(projectId);
  if (verification.status === "verified") return "ready";
  return verification.status;
}

function installSourceFor(projectId: string): SkillInstallSource | undefined {
  const verification = verificationFor(projectId);
  if (
    verification.status !== "verified" ||
    verification.installMode !== "direct" ||
    !verification.repoUrl ||
    verification.subPath === undefined ||
    !verification.verifiedCommit
  ) {
    return undefined;
  }
  return {
    repoUrl: verification.repoUrl,
    subPath: verification.subPath,
    verifiedCommit: verification.verifiedCommit,
  };
}

function projectKindFor(
  projectId: string,
  declaredKind: SkillProjectKind,
): SkillProjectKind {
  return verificationFor(projectId).verifiedKind ?? declaredKind;
}

function skillSetFor(projectId: string): SkillSetSummary | undefined {
  const verification = verificationFor(projectId);
  if (
    verification.status !== "verified" ||
    verification.verifiedKind !== "skillset" ||
    !verification.skillsetMode ||
    !verification.repoUrl ||
    !verification.verifiedCommit ||
    verification.scopeSubPath === undefined ||
    verification.skillDocumentCount === undefined ||
    verification.topMemberCount === undefined ||
    verification.nestedSkillCount === undefined
  ) {
    return undefined;
  }
  return {
    id: verification.skillSetId ?? projectId,
    mode: verification.skillsetMode,
    repoUrl: verification.repoUrl,
    verifiedCommit: verification.verifiedCommit,
    scopeSubPath: verification.scopeSubPath,
    skillDocumentCount: verification.skillDocumentCount,
    memberCount: verification.topMemberCount,
    nestedSkillCount: verification.nestedSkillCount,
    duplicateMemberCount: verification.duplicateMemberCount ?? 0,
  };
}

function tagsForKind(
  tags: string[],
  kind: SkillProjectKind,
  installMode: SkillInstallMode,
) {
  const normalized = tags.filter(
    (tag) => tag !== "技能包" && tag !== "成员可安装",
  );
  if (kind === "skillset") normalized.push("技能包");
  if (installMode === "members") normalized.push("成员可安装");
  return [...new Set(normalized)];
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
    kind: projectKindFor("anthropic-pdf-skill", "skill"),
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
    installStatus: installStatusFor("anthropic-pdf-skill"),
    installMode: verificationFor("anthropic-pdf-skill").installMode,
    installSource: installSourceFor("anthropic-pdf-skill"),
    skillSet: skillSetFor("anthropic-pdf-skill"),
    tags: ["官方示例", "PDF", "文档摘要"],
    verification: verificationFor("anthropic-pdf-skill"),
  },
  {
    id: "anthropic-xlsx-skill",
    name: "XLSX 表格处理技能",
    repoName: "anthropics/skills",
    repoUrl: "https://github.com/anthropics/skills",
    category: "内容与办公",
    kind: projectKindFor("anthropic-xlsx-skill", "skill"),
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
    installStatus: installStatusFor("anthropic-xlsx-skill"),
    installMode: verificationFor("anthropic-xlsx-skill").installMode,
    installSource: installSourceFor("anthropic-xlsx-skill"),
    skillSet: skillSetFor("anthropic-xlsx-skill"),
    tags: ["官方示例", "Excel", "数据分析"],
    verification: verificationFor("anthropic-xlsx-skill"),
  },
  {
    id: "mattpocock-tdd-skill",
    name: "TypeScript TDD 技能",
    repoName: "mattpocock/skills",
    repoUrl: "https://github.com/mattpocock/skills",
    category: "开发与测试",
    kind: projectKindFor("mattpocock-tdd-skill", "skill"),
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
    installStatus: installStatusFor("mattpocock-tdd-skill"),
    installMode: verificationFor("mattpocock-tdd-skill").installMode,
    installSource: installSourceFor("mattpocock-tdd-skill"),
    skillSet: skillSetFor("mattpocock-tdd-skill"),
    tags: ["TypeScript", "TDD", "工程质量"],
    verification: verificationFor("mattpocock-tdd-skill"),
  },
  {
    id: "agent-skills-standard",
    name: "Agent Skills 开放标准",
    repoName: "agentskills/agentskills",
    repoUrl: "https://github.com/agentskills/agentskills",
    category: "AI 与智能体",
    kind: projectKindFor("agent-skills-standard", "skill"),
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
    installStatus: installStatusFor("agent-skills-standard"),
    installMode: verificationFor("agent-skills-standard").installMode,
    skillSet: skillSetFor("agent-skills-standard"),
    tags: ["开放标准", "模板", "规范"],
    verification: verificationFor("agent-skills-standard"),
  },
];

const anbeimeCatalog = anbeimeCatalogJson as GeneratedSkillCatalog;

const anbeimeSkillProjects: SkillProject[] = anbeimeCatalog.projects.map((project) => {
  const verification = verificationFor(project.id);
  const resolvedKind = projectKindFor(project.id, project.kind);
  const policyInput = {
    name: project.name,
    description: project.description,
    category: project.category,
    tags: tagsForKind(project.tags, resolvedKind, verification.installMode),
  };
  const category = classifySkill(policyInput);
  return {
    id: project.id,
    name: project.name,
    repoName: anbeimeCatalog.source.repoName,
    repoUrl: anbeimeCatalog.source.repoUrl,
    category,
    kind: resolvedKind,
    description: describeSkillInChinese(policyInput, category),
    sourceDescription: project.description,
    readmeSummary:
      resolvedKind === "skillset"
        ? `该 SkillSet 包含 ${verification.skillDocumentCount ?? project.includedSkills.length + 1} 个 Skill 文档，安装父目录时会一并保留相关脚本和参考资料。`
        : "目录数据由本地 skill-main 与远端 main 提交核对后生成。安装前请检查第三方说明和运行依赖。",
    stars: anbeimeCatalog.source.stars,
    language: project.language,
    updatedAt: anbeimeCatalog.source.updatedAt,
    installCommand: `git clone --depth 1 --filter=blob:none --sparse ${anbeimeCatalog.source.repoUrl}\ncd skill\ngit sparse-checkout set ${project.subPath}`,
    installNote:
      "模镜只复制该目录，不会在安装时执行其中脚本。使用社区 Skill 前请先检查依赖、外部服务与凭据要求。",
    installStatus: installStatusFor(project.id),
    installMode: verification.installMode,
    installSource: installSourceFor(project.id),
    skillSet: skillSetFor(project.id),
    tags: project.tags,
    includedSkills: project.includedSkills,
    sourceCommit: anbeimeCatalog.source.commit,
    catalogName: anbeimeCatalog.source.repoName,
    catalogUrl: anbeimeCatalog.source.repoUrl,
    verification,
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
  .map((project) => {
    const verification = verificationFor(project.id);
    return {
      ...project,
      verification,
      catalogInstallSource:
        project.installSource ?? auditedInstallSource(project.sourceUrl),
      resolvedInstallSource: installSourceFor(project.id),
    };
  })
  .filter((project) => {
    const source = project.resolvedInstallSource ?? project.catalogInstallSource;
    if (!source) return true;
    const key = `${source.repoUrl.toLowerCase()}#${source.subPath}`;
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
    const installStatus = installStatusFor(project.id);
    const resolvedKind = projectKindFor(project.id, project.kind);
    const resolvedSkillSet = skillSetFor(project.id);
    return {
      id: project.id,
      name: project.name,
      repoName: project.name,
      repoUrl: project.sourceUrl,
      category,
      kind: resolvedKind,
      description: describeSkillInChinese(policyInput, category),
      sourceDescription: project.description,
      readmeSummary: `收录分组：${project.sourceGroup}。该条目来自 VoltAgent 维护的外部 Skill 索引。`,
      stars: voltagentCatalog.source.stars,
      language: "Markdown",
      updatedAt: voltagentCatalog.source.updatedAt,
      installCommand: project.resolvedInstallSource
        ? `git clone --depth 1 --filter=blob:none --sparse ${project.resolvedInstallSource.repoUrl}\ncd skill\ngit sparse-checkout set ${project.resolvedInstallSource.subPath}`
        : "",
      installNote: project.resolvedInstallSource
        ? "该安装源已核对固定 Git 提交中的 SKILL.md，可由模镜按提交执行安装。"
        : resolvedSkillSet?.mode === "members"
          ? `已核验 ${resolvedSkillSet.memberCount} 个独立成员，可在 SkillSet 详情中逐项安装。`
        : project.verification.reason ??
          "该来源尚未形成可由当前安装器验证的 Skill 安装目录。",
      installStatus,
      installMode: project.verification.installMode,
      installSource: project.resolvedInstallSource,
      skillSet: resolvedSkillSet,
      tags: tagsForKind(
        project.tags,
        resolvedKind,
        project.verification.installMode,
      ),
      includedSkills: project.includedSkills,
      sourceCommit: project.resolvedInstallSource?.verifiedCommit,
      catalogName: voltagentCatalog.source.repoName,
      catalogUrl: voltagentCatalog.source.repoUrl,
      publisher: project.publisher,
      sourceGroup: project.sourceGroup,
      verification: project.verification,
    };
  });

export const skillProjects: SkillProject[] = [
  ...primarySkillProjects,
  ...voltagentSkillProjects,
];

