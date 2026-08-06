import {
  classifySkill,
  describeSkillInChinese,
} from "./skillCatalogPolicy";
import {
  type SkillNeedMemberTarget,
  type SkillNeedProjectTarget,
  type SkillNeedTarget,
} from "./skillNeedMatcher";
import { loadSkillNeedMemberMetadataIndex } from "./skillNeedMembers";
import {
  type SkillInstallSource,
  type SkillProject,
  skillProjects,
} from "./skillProjects";
import {
  loadSkillSetMemberIndex,
  type SkillSetMemberIndex,
} from "./skillSetMembers";

function installSourceKey(source: SkillInstallSource) {
  return `${source.repoUrl.toLowerCase()}#${source.subPath}#${source.verifiedCommit.toLowerCase()}`;
}

function pathTerms(subPath: string) {
  return [subPath, ...subPath.split("/").flatMap((part) => part.split(/[-_.]+/))];
}

function scopeDepth(scopeSubPath: string) {
  return scopeSubPath.split("/").filter(Boolean).length;
}

function isDeprecatedSkill(description = "") {
  return /\bdeprecated\b|\barchived\b|已弃用|停止维护|不再维护/i.test(description);
}

function searchableSourceDescription(description = "") {
  const exclusion = description.search(
    /\bnot for\b|\bdo not use\b|\bdon't use\b|不适用|不要用于|请勿用于/i,
  );
  return (exclusion < 0 ? description : description.slice(0, exclusion)).trim();
}

function memberDescriptionInChinese(input: {
  name: string;
  description: string;
  category: ReturnType<typeof classifySkill>;
  tags: string[];
}) {
  const chineseCount = (input.description.match(/[\u3400-\u9fff]/g) ?? []).length;
  const latinCount = (input.description.match(/[a-z]/gi) ?? []).length;
  const chineseIsPrimary =
    chineseCount >= 10 && chineseCount / Math.max(1, chineseCount + latinCount) >= 0.35;
  if (chineseIsPrimary) return describeSkillInChinese(input, input.category);

  const searchable = `${input.name} ${input.description} ${input.tags.join(" ")}`;
  const capabilities = [
    [
      /schema|json-ld|structured data|meta description|open graph|twitter card/i,
      "生成搜索展示元数据和结构化标记",
    ],
    [
      /serp|\bseo\b|keyword|search ranking|rank tracker/i,
      "跟踪搜索排名并分析 SEO 表现",
    ],
    [
      /share of voice|brand mentions|sentiment-weighted|competitor mentions/i,
      "跟踪品牌与竞品声量并分析趋势",
    ],
    [
      /playwright|cypress|selenium|browser testing|\be2e\b/i,
      "编写网页自动化测试并检查交互结果",
    ],
    [
      /postgres(?:ql)?|mysql|sqlite|mongodb|redis|database|\bsql\b/i,
      "设计数据库、查询和数据访问流程",
    ],
    [
      /security|vulnerab|threat|pentest|compliance|permission|access control/i,
      "检查安全风险、权限和合规问题",
    ],
    [
      /xlsx|spreadsheet|excel|workbook|\bcsv\b/i,
      "分析电子表格并整理可复用结果",
    ],
    [
      /\bpdf\b|document|documentation|markdown|knowledge/i,
      "处理文档、知识资料和办公内容",
    ],
    [
      /figma|design system|user interface|\bui\b|frontend design/i,
      "设计界面、组件和视觉规范",
    ],
    [
      /analytics|analysis|metric|statistics|forecast|dataset|reporting/i,
      "分析数据、指标和趋势并整理结论",
    ],
    [
      /research|paper|evidence|literature|citation/i,
      "搜集资料、比较证据并形成研究结论",
    ],
    [
      /marketing|campaign|growth|competitor|influencer|social media|sales/i,
      "分析营销活动、竞品和增长机会",
    ],
    [
      /image|illustrat|creative|canvas|poster/i,
      "制作和优化图像与视觉内容",
    ],
    [
      /video|animation|remotion/i,
      "制作、编辑和检查视频或动画",
    ],
    [
      /audio|voice|music|speech|tts|asr/i,
      "处理语音、音频或音乐内容",
    ],
    [
      /deploy|devops|docker|kubernetes|terraform|infrastructure|observability/i,
      "配置部署、容器和运行环境",
    ],
    [
      /automation|workflow|integration|scraping|crawler|webhook/i,
      "连接工具并自动处理重复任务",
    ],
    [
      /agent|\bllm\b|prompt|\brag\b|\bmcp\b/i,
      "设计智能体、提示词和知识检索流程",
    ],
    [
      /code|coding|developer|typescript|javascript|python|\bapi\b|testing/i,
      "完成开发、调试和代码质量检查",
    ],
  ] as const;
  const matched = capabilities
    .filter(([pattern]) => pattern.test(searchable))
    .map(([, description]) => description)
    .filter((description, index, values) => values.indexOf(description) === index)
    .slice(0, 2);
  const categoryFallbacks: Record<string, string> = {
    "AI 与智能体": "搭建和改进 AI 智能体工作流",
    "开发与测试": "完成开发、调试和质量检查任务",
    "数据与研究": "整理数据、开展研究并输出结论",
    "自动化与集成": "连接工具并自动处理重复工作",
    "设计与多媒体": "制作和优化视觉或多媒体内容",
    "内容与办公": "处理文档、内容和日常办公资料",
    "营销与增长": "规划营销活动并改进增长效果",
    "产品与协作": "梳理产品需求并推进团队协作",
    "安全与运维": "检查风险并维护系统稳定运行",
    "商业与专业服务": "处理商业分析和专业服务任务",
  };
  const topic = input.name.replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
  const primary = matched[0] ?? categoryFallbacks[input.category] ?? "完成对应的专业工作流";
  return `围绕 ${topic}，帮助你${primary}${matched[1] ? `，并可${matched[1]}` : ""}。`;
}

function projectTarget(project: SkillProject): SkillNeedProjectTarget {
  return {
    targetType: "project",
    project,
    id: project.id,
    name: project.name,
    category: project.category,
    kind: project.kind,
    description: project.description,
    sourceDescription: project.sourceDescription,
    searchDescription: searchableSourceDescription(project.sourceDescription),
    tags: project.tags,
    includedSkills: project.includedSkills,
    installStatus: project.installStatus,
    publisher: project.publisher,
    sourceGroup: project.sourceGroup,
    pathTerms: project.installSource
      ? pathTerms(project.installSource.subPath)
      : [],
    deprecated: isDeprecatedSkill(project.sourceDescription),
  };
}

export function buildSkillNeedCandidates({
  memberIndex,
  memberMetadata,
  projects,
}: {
  memberIndex: SkillSetMemberIndex;
  memberMetadata: Awaited<ReturnType<typeof loadSkillNeedMemberMetadataIndex>>;
  projects: readonly SkillProject[];
}): SkillNeedTarget[] {
  if (memberIndex.version !== 2 || !memberIndex.fingerprint) {
    throw new Error("SkillSet 成员注册表版本不受支持，请重新生成目录数据。");
  }
  if (memberMetadata.memberIndexFingerprint !== memberIndex.fingerprint) {
    throw new Error("成员搜索索引与安装注册表不一致，请重新生成目录数据。");
  }
  const memberIds = Object.keys(memberIndex.members).sort();
  if (
    memberIds.length !== Object.keys(memberMetadata.members).length ||
    memberIds.some((memberId) => !memberMetadata.members[memberId])
  ) {
    throw new Error("成员搜索索引覆盖不完整，本次不会返回部分结果。");
  }

  const projectBySkillSetId = new Map<string, SkillProject>();
  for (const project of projects) {
    if (project.skillSet) projectBySkillSetId.set(project.skillSet.id, project);
  }
  const parentsByMemberId = new Map<
    string,
    Array<{ project: SkillProject; scopeSubPath: string }>
  >();
  for (const group of Object.values(memberIndex.skillSets)) {
    const project = projectBySkillSetId.get(group.id);
    if (!project) {
      throw new Error(`成员搜索索引缺少所属 SkillSet：${group.id}`);
    }
    for (const memberId of group.memberIds) {
      const parents = parentsByMemberId.get(memberId) ?? [];
      parents.push({ project, scopeSubPath: group.scopeSubPath });
      parentsByMemberId.set(memberId, parents);
    }
  }

  const targets: SkillNeedTarget[] = projects.map(projectTarget);
  const directSources = new Set(
    projects.flatMap((project) =>
      project.installSource ? [installSourceKey(project.installSource)] : [],
    ),
  );
  for (const memberId of memberIds) {
    const member = memberIndex.members[memberId];
    const source: SkillInstallSource = {
      repoUrl: member.repoUrl,
      subPath: member.subPath,
      verifiedCommit: member.verifiedCommit,
    };
    if (directSources.has(installSourceKey(source))) continue;
    const metadata = memberMetadata.members[memberId];
    const parents = (parentsByMemberId.get(memberId) ?? []).sort(
      (left, right) =>
        scopeDepth(right.scopeSubPath) - scopeDepth(left.scopeSubPath) ||
        left.project.name.localeCompare(right.project.name, "zh-CN") ||
        left.project.id.localeCompare(right.project.id, "en"),
    );
    if (parents.length === 0) {
      throw new Error(`成员不属于任何 SkillSet：${memberId}`);
    }
    const searchDescription = searchableSourceDescription(
      metadata.sourceDescription,
    );
    const policyInput = {
      name: metadata.displayName,
      description: searchDescription,
      category: parents[0].project.category,
      tags: metadata.tags,
    };
    const category = classifySkill(policyInput);
    const target: SkillNeedMemberTarget = {
      targetType: "member",
      member,
      installSource: source,
      primarySkillSet: parents[0].project,
      parentSkillSets: parents.map((parent) => parent.project),
      id: member.id,
      name: metadata.displayName,
      category,
      kind: "skill",
      description: memberDescriptionInChinese({ ...policyInput, category }),
      sourceDescription: metadata.sourceDescription,
      searchDescription,
      tags: metadata.tags,
      installStatus: "ready",
      pathTerms: pathTerms(member.subPath),
      parentNames: parents.map((parent) => parent.project.name),
      deprecated: isDeprecatedSkill(metadata.sourceDescription),
    };
    targets.push(target);
  }
  return targets;
}

let candidatesPromise: Promise<SkillNeedTarget[]> | undefined;

export function loadSkillNeedCandidates() {
  candidatesPromise ??= Promise.all([
    loadSkillSetMemberIndex(),
    loadSkillNeedMemberMetadataIndex(),
  ]).then(([memberIndex, memberMetadata]) =>
    buildSkillNeedCandidates({
      memberIndex,
      memberMetadata,
      projects: skillProjects,
    }),
  );
  return candidatesPromise;
}
