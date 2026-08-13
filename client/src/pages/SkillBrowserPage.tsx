import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import AuthoringProposalPanel from "../components/authoring/AuthoringProposalPanel";
import SkillDraftPanel from "../components/authoring/SkillDraftPanel";
import {
  type SkillProject,
  type SkillProjectKind,
  type SkillInstallSource,
  skillProjects,
} from "../data/skillProjects";
import {
  loadSkillSetMemberIndex,
  type SkillSetMemberSource,
} from "../data/skillSetMembers";
import {
  loadSkillNeedCandidates,
} from "../data/skillNeedCandidates";
import {
  findSkillsForNeed,
  type SkillNeedMatch,
  type SkillNeedTarget,
} from "../data/skillNeedMatcher";
import type { SkillInstallStatus } from "../data/skillCatalogPolicy";
import type { BuiltinSkill } from "../types/agentWorkspace";
import { useSkillCreatorStatus } from "../hooks/useSkillCreatorStatus";
import {
  saveSkillRerankFeedback,
  searchSkills,
  type SkillRankingReceipt,
  type SkillRankingResult,
  type SkillRerankStatus,
} from "../utils/skillRerankApi";
import SkillTrustPanel, {
  SkillTrustBadge,
  SkillTrustSummaryLine,
} from "../components/skill-trust/SkillTrustPanel";
import {
  effectiveTrustInstallPolicy,
  loadSkillTrustReceipt,
  loadSkillTrustSummaryIndex,
  memberTrustCandidateId,
  projectTrustCandidateId,
  readSkillTrustApiError,
  trustSummaryForCandidate,
  trustSummaryForSource,
  type InstalledSkillTrustFields,
  type SkillTrustReceipt,
  type SkillTrustReceiptSummary,
  type SkillTrustGateMode,
  type SkillTrustSummaryIndex,
} from "../data/skillTrustIndex";

interface InstalledSkill extends InstalledSkillTrustFields {
  skill_id: string;
  name: string;
  description: string;
  repo_url: string;
  sub_path: string;
  installed_at: number;
  source_ref?: string | null;
  source_kind: string;
  source_id?: string | null;
  source_revision?: number | null;
  content_digest?: string | null;
}

interface InstalledSkillsResponse {
  skills: InstalledSkill[];
}

interface SkillSetBatchProgress {
  projectId: string;
  completed: number;
  total: number;
  currentMemberName: string;
}

type SkillTab =
  | "builtin"
  | "market"
  | "installed"
  | "imports"
  | "drafts"
  | "proposals";
type SkillKindFilter = "all" | SkillProjectKind;
type SkillAvailabilityFilter = "all" | SkillInstallStatus;
type NeedSearchStatus = "idle" | "loading" | "ready" | "error";
type FeedbackStatus = "" | "saving" | "relevant" | "not_relevant" | "error";
type TrustIndexStatus = "loading" | "ready" | "error";
type PendingTrustAction =
  | {
      kind: "inspect";
      title: string;
      receiptId: string;
    }
  | {
      kind: "install";
      title: string;
      receiptId: string;
      installId: string;
      label: string;
      source: SkillInstallSource | SkillSetMemberSource;
      typeLabel: string;
    }
  | {
      kind: "acknowledge";
      title: string;
      receiptId: string;
      skill: InstalledSkill;
    };
const MARKET_PAGE_SIZE = 48;
const SKILLSET_MEMBER_PAGE_SIZE = 50;
const NEED_EXAMPLES = [
  "分析 Excel 销售数据",
  "为 React 网页编写自动化测试",
  "审计 Postgres 数据库安全",
] as const;

function needCandidateId(target: SkillNeedTarget) {
  return `catalog:${target.targetType}:${target.id}`;
}

const SkillLocalImportSummaryPanel = lazy(
  () => import("../components/skill-import/SkillLocalImportSummaryPanel"),
);
const SkillLifecyclePanel = lazy(
  () => import("../components/SkillLifecyclePanel"),
);

const INSTALL_STATUS_DETAILS: Record<
  SkillInstallStatus,
  { label: string; action: string; className: string }
> = {
  ready: {
    label: "可一键安装",
    action: "",
    className: "border-emerald-300/30 bg-emerald-300/10 text-emerald-100",
  },
  manual: {
    label: "有安装说明",
    action: "查看安装说明",
    className: "border-sky-300/30 bg-sky-300/10 text-sky-100",
  },
  pending: {
    label: "待核验来源",
    action: "查看并核验",
    className: "border-amber-300/30 bg-amber-300/10 text-amber-100",
  },
  reference: {
    label: "仅资料参考",
    action: "查看资料",
    className: "border-white/15 bg-white/[0.055] text-slate-300",
  },
};

function formatStars(stars: number) {
  return stars >= 1000 ? `${(stars / 1000).toFixed(1)}k` : `${stars}`;
}

function formatProjectKind(project: SkillProject) {
  if (project.kind === "skillset") {
    if (project.skillSet?.mode === "members") {
      return `SkillSet · ${project.skillSet.memberCount} 个成员`;
    }
    if (project.skillSet?.mode === "package") {
      return `SkillSet · ${project.skillSet.skillDocumentCount} 个技能`;
    }
    return "SkillSet";
  }
  return "Skill";
}

function installStatusDetailsFor(project: SkillProject) {
  if (project.installStatus === "ready" && project.installMode === "members") {
    return {
      label: "成员可安装",
      action: "查看成员",
      className: "border-accent-300/30 bg-accent-300/10 text-accent-100",
    };
  }
  return INSTALL_STATUS_DETAILS[project.installStatus];
}

function formatInstallTime(value: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

async function readApiError(response: Response) {
  return readSkillTrustApiError(response);
}

function isSourceInstalled(
  source: SkillInstallSource | SkillSetMemberSource,
  installedSkills: InstalledSkill[],
) {
  return installedSkills.some(
    (skill) =>
      skill.repo_url === source.repoUrl &&
      skill.sub_path === source.subPath &&
      skill.source_ref === source.verifiedCommit,
  );
}

function isProjectInstalled(project: SkillProject, installedSkills: InstalledSkill[]) {
  return project.installSource
    ? isSourceInstalled(project.installSource, installedSkills)
    : false;
}

function fixedSourceUrl(source: SkillInstallSource | SkillSetMemberSource) {
  const path = source.subPath
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  return `${source.repoUrl.replace(/\.git$/i, "")}/tree/${source.verifiedCommit}${path ? `/${path}` : ""}`;
}

function repositoryName(repoUrl: string) {
  return repoUrl.replace(/\.git$/i, "").split("/").filter(Boolean).slice(-2).join("/");
}

function MarketSkillCard({
  gateMode,
  installingId,
  installed,
  onInstall,
  onInspectTrust,
  onOpenSkillSet,
  project,
  trustSummary,
}: {
  gateMode: SkillTrustGateMode;
  installingId: string;
  installed: boolean;
  onInstall: (project: SkillProject) => void;
  onInspectTrust: (title: string, receiptId: string) => void;
  onOpenSkillSet: (project: SkillProject) => void;
  project: SkillProject;
  trustSummary: SkillTrustReceiptSummary | null;
}) {
  const canInstall = project.installMode === "direct" && Boolean(project.installSource);
  const canBrowseMembers =
    project.installMode === "members" && project.skillSet?.mode === "members";
  const isInstalling = installingId === project.id;
  const installLabel = project.kind === "skillset" ? "安装技能包" : "安装技能";
  const installStatus = installStatusDetailsFor(project);
  const effectivePolicy = effectiveTrustInstallPolicy(
    gateMode,
    trustSummary,
  );
  const hasIncludedSkills =
    project.skillSet?.mode === "package" && (project.includedSkills?.length ?? 0) > 0;

  return (
    <article className="group relative overflow-hidden rounded-lg border border-white/10 bg-ink-950/70 p-5 transition duration-200 hover:border-hire-300/35 hover:bg-white/[0.065]">
      <div className="absolute inset-x-5 top-0 h-px bg-gradient-to-r from-transparent via-hire-300/80 to-transparent opacity-0 transition group-hover:opacity-100" />
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold text-hire-200">{project.category}</p>
          <h3 className="mt-2 text-xl font-semibold text-white">{project.name}</h3>
          <p className="mt-1 text-xs text-slate-400">
            {project.repoName}
            {project.catalogName && project.catalogName !== project.repoName
              ? ` · ${project.catalogName}`
              : ""}
            {` · ★ ${formatStars(project.stars)}`}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <span
            className={`rounded-full border px-3 py-1 text-xs font-semibold ${
              project.kind === "skillset"
                ? "border-accent-300/30 bg-accent-300/10 text-accent-100"
                : "border-brand-300/25 bg-brand-300/10 text-brand-100"
            }`}
          >
            {formatProjectKind(project)}
          </span>
          <span
            className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${installStatus.className}`}
          >
            {installStatus.label}
          </span>
        </div>
      </div>

      <p className="mt-4 min-h-20 text-sm leading-6 text-slate-300">
        {project.description}
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        {project.tags.map((tag) => (
          <span
            className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs text-slate-300"
            key={tag}
          >
            {tag}
          </span>
        ))}
      </div>

      <div className="mt-5 rounded-lg border border-white/10 bg-white/[0.045] p-3">
        <p className="text-xs font-semibold text-slate-300">
          {project.skillSet?.mode === "members"
            ? "成员集合来源"
            : hasIncludedSkills
              ? "技能包内容"
              : project.kind === "skillset"
                ? "技能包来源"
                : "安装来源"}
        </p>
        {hasIncludedSkills ? (
          <p className="mt-2 text-xs leading-5 text-slate-400">
            {project.includedSkills?.join("、")}
          </p>
        ) : null}
        <div className="mt-2 rounded-md bg-ink-950/80 p-2 text-xs leading-5 text-slate-300">
          {project.skillSet?.mode === "members" ? (
            <div>
              <code className="break-all text-hire-100">
                {project.skillSet.repoUrl} / {project.skillSet.scopeSubPath || "."}
              </code>
              <p className="mt-1 text-[11px] text-emerald-200/80">
                已核验固定提交 {project.skillSet.verifiedCommit.slice(0, 12)}
              </p>
              <p className="mt-1 text-[11px] text-slate-400">
                {project.skillSet.memberCount} 个顶层成员
                {project.skillSet.nestedSkillCount > 0
                  ? ` · ${project.skillSet.nestedSkillCount} 个嵌套技能`
                  : ""}
              </p>
            </div>
          ) : project.installSource ? (
            <div>
              <code className="break-all text-hire-100">
                {project.installSource.repoUrl} / {project.installSource.subPath}
              </code>
              {project.installSource.verifiedCommit ? (
                <p className="mt-1 text-[11px] text-emerald-200/80">
                  已核验固定提交 {project.installSource.verifiedCommit.slice(0, 12)}
                </p>
              ) : null}
            </div>
          ) : project.installStatus === "manual" && project.installCommand ? (
            <code className="break-all text-sky-100">{project.installCommand}</code>
          ) : (
            project.installNote
          )}
        </div>
      </div>

      {canInstall ? (
        <SkillTrustSummaryLine
          gateMode={gateMode}
          onInspect={() =>
            trustSummary && onInspectTrust(project.name, trustSummary.receiptId)
          }
          summary={trustSummary}
        />
      ) : null}

      <div className="mt-5 flex items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <a
            className="text-xs font-semibold text-slate-400 underline decoration-white/20 underline-offset-4 transition hover:text-white"
            href={project.repoUrl}
            rel="noreferrer"
            target="_blank"
          >
            查看来源
          </a>
          {project.catalogUrl && project.catalogUrl !== project.repoUrl ? (
            <a
              className="text-xs font-semibold text-slate-500 underline decoration-white/15 underline-offset-4 transition hover:text-white"
              href={project.catalogUrl}
              rel="noreferrer"
              target="_blank"
            >
              查看索引
            </a>
          ) : null}
        </div>
        {canInstall ? (
          <button
            className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 shadow-[0_0_22px_rgba(251,146,60,0.22)] transition hover:bg-hire-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500 disabled:shadow-none"
            disabled={
              installed ||
              Boolean(installingId) ||
              effectivePolicy === "block"
            }
            onClick={() => onInstall(project)}
            type="button"
          >
            {isInstalling
              ? "安装中..."
              : installed
                ? "已安装"
                : effectivePolicy === "block"
                  ? "信任策略已阻断"
                  : installLabel}
          </button>
        ) : canBrowseMembers ? (
          <button
            className="rounded-full border border-accent-300/30 bg-accent-300/10 px-4 py-2 text-sm font-semibold text-accent-100 transition hover:bg-accent-300/20 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-200"
            onClick={() => onOpenSkillSet(project)}
            type="button"
          >
            查看成员
          </button>
        ) : (
          <a
            className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-brand-300/35 hover:bg-brand-300/10 hover:text-white"
            href={project.repoUrl}
            rel="noreferrer"
            target="_blank"
          >
            {installStatus.action}
          </a>
        )}
      </div>
    </article>
  );
}

function NeedMatchCard({
  gateMode,
  installed,
  installingId,
  match,
  onInstallMember,
  onInstallProject,
  onInspectTrust,
  onLocate,
  onOpenSkillSet,
  onFeedback,
  rankingResult,
  feedbackStatus,
  trustSummary,
}: {
  gateMode: SkillTrustGateMode;
  installed: boolean;
  installingId: string;
  match: SkillNeedMatch<SkillNeedTarget>;
  onInstallMember: (member: SkillSetMemberSource) => void;
  onInstallProject: (project: SkillProject) => void;
  onInspectTrust: (title: string, receiptId: string) => void;
  onLocate: (project: SkillProject) => void;
  onOpenSkillSet: (project: SkillProject, memberId?: string) => void;
  onFeedback?: (judgment: "relevant" | "not_relevant") => void;
  rankingResult?: SkillRankingResult;
  feedbackStatus?: FeedbackStatus;
  trustSummary: SkillTrustReceiptSummary | null;
}) {
  const { project: target, reasons } = match;
  const isMember = target.targetType === "member";
  const catalogProject = isMember ? target.primarySkillSet : target.project;
  const installStatus = isMember
    ? INSTALL_STATUS_DETAILS.ready
    : installStatusDetailsFor(catalogProject);
  const canInstall = isMember
    ? true
    : catalogProject.installStatus === "ready" &&
      catalogProject.installMode === "direct" &&
      Boolean(catalogProject.installSource);
  const canBrowseMembers =
    !isMember &&
    catalogProject.installStatus === "ready" &&
    catalogProject.installMode === "members" &&
    catalogProject.skillSet?.mode === "members";
  const isInstalling = installingId === target.id;
  const sourceUrl = isMember
    ? fixedSourceUrl(target.installSource)
    : catalogProject.repoUrl;
  const sourceName = isMember
    ? repositoryName(target.member.repoUrl)
    : catalogProject.repoName;
  const effectivePolicy = effectiveTrustInstallPolicy(
    gateMode,
    trustSummary,
  );

  return (
    <article className="rounded-lg border border-brand-300/20 bg-ink-950/65 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xs font-semibold">
            <span className="text-brand-100">{target.category}</span>
            <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-slate-300">
              {isMember ? "SkillSet 成员" : formatProjectKind(catalogProject)}
            </span>
          </div>
          <h3 className="mt-1 truncate text-base font-semibold text-white">
            {target.name}
          </h3>
          <p className="mt-1 truncate text-xs text-slate-500">
            {sourceName}
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${installStatus.className}`}
        >
          {installStatus.label}
        </span>
      </div>

      <p className="mt-3 line-clamp-3 text-sm leading-6 text-slate-300">
        {target.description}
      </p>
      {isMember ? (
        <div className="mt-3 space-y-1 text-xs leading-5 text-slate-400">
          <p>
            所属集合：
            <span className="font-semibold text-slate-200">
              {target.primarySkillSet.name}
            </span>
            {target.parentSkillSets.length > 1
              ? `，另见 ${target.parentSkillSets.length - 1} 个重叠集合`
              : ""}
          </p>
          <p className="line-clamp-2" lang="en">
            来源说明：{target.sourceDescription}
          </p>
        </div>
      ) : null}
      <div className="mt-3 space-y-2">
        {reasons.slice(0, 3).map((reason) => (
          <div
            className="flex items-start gap-2 text-xs leading-5"
            key={`${reason.type}-${reason.matchedTerms.join("-")}`}
          >
            <span className="shrink-0 rounded-full bg-brand-300/10 px-2 py-0.5 font-semibold text-brand-100">
              {reason.label}
            </span>
            <span className="text-slate-400">
              {reason.matchedTerms.join("、")}
            </span>
          </div>
        ))}
      </div>

      {rankingResult?.semanticRank ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 font-semibold text-cyan-100">
            语义第 {rankingResult.semanticRank} 名
          </span>
          <span className="text-slate-400">
            词典第 {rankingResult.lexicalRank ?? "?"} 名
            {rankingResult.rankDelta
              ? `，${rankingResult.rankDelta > 0 ? "上升" : "下降"} ${Math.abs(rankingResult.rankDelta)} 位`
              : "，名次未变"}
          </span>
        </div>
      ) : null}

      {!canInstall && !canBrowseMembers ? (
        <p className="mt-3 text-xs leading-5 text-amber-100/80">
          {catalogProject.installNote}
        </p>
      ) : canBrowseMembers ? (
        <p className="mt-3 text-xs leading-5 text-accent-100/85">
          该集合不整包安装，请打开成员列表选择需要的 Skill。
        </p>
      ) : null}

      {canInstall ? (
        <SkillTrustSummaryLine
          gateMode={gateMode}
          onInspect={() =>
            trustSummary && onInspectTrust(target.name, trustSummary.receiptId)
          }
          summary={trustSummary}
        />
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-brand-300/35 hover:bg-brand-300/10 hover:text-white"
          onClick={() =>
            isMember
              ? onOpenSkillSet(target.primarySkillSet, target.member.id)
              : onLocate(catalogProject)
          }
          type="button"
        >
          {isMember ? "查看所属集合" : "在货架中查看"}
        </button>
        <a
          className="px-2 py-1.5 text-xs font-semibold text-slate-400 underline decoration-white/20 underline-offset-4 transition hover:text-white"
          href={sourceUrl}
          rel="noreferrer"
          target="_blank"
        >
          查看来源
        </a>
        {canInstall ? (
          <button
            className="ml-auto rounded-full bg-hire-300 px-3 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
            disabled={
              installed ||
              Boolean(installingId) ||
              effectivePolicy === "block"
            }
            onClick={() =>
              isMember
                ? onInstallMember(target.member)
                : onInstallProject(catalogProject)
            }
            type="button"
          >
            {isInstalling
              ? "安装中..."
              : installed
                ? "已安装"
                : effectivePolicy === "block"
                  ? "已阻断"
                  : "一键安装"}
          </button>
        ) : canBrowseMembers ? (
          <button
            className="ml-auto rounded-full bg-accent-200 px-3 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-100"
            onClick={() => onOpenSkillSet(catalogProject)}
            type="button"
          >
            查看成员
          </button>
        ) : null}
      </div>
      {onFeedback ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/10 pt-3">
          <span className="mr-1 text-xs text-slate-400">这项推荐：</span>
          <button
            aria-pressed={feedbackStatus === "relevant"}
            className="min-h-9 rounded-full border border-white/15 px-3 text-xs font-semibold text-slate-200 transition hover:border-emerald-300/35 hover:bg-emerald-300/10 disabled:opacity-60"
            disabled={feedbackStatus === "saving" || feedbackStatus === "relevant"}
            onClick={() => onFeedback("relevant")}
            type="button"
          >
            {feedbackStatus === "relevant" ? "已记为相关" : "相关"}
          </button>
          <button
            aria-pressed={feedbackStatus === "not_relevant"}
            className="min-h-9 rounded-full border border-white/15 px-3 text-xs font-semibold text-slate-200 transition hover:border-rose-300/35 hover:bg-rose-300/10 disabled:opacity-60"
            disabled={
              feedbackStatus === "saving" || feedbackStatus === "not_relevant"
            }
            onClick={() => onFeedback("not_relevant")}
            type="button"
          >
            {feedbackStatus === "not_relevant" ? "已记为不相关" : "不相关"}
          </button>
          {feedbackStatus === "saving" ? (
            <span className="text-xs text-slate-400" role="status">正在保存…</span>
          ) : feedbackStatus === "error" ? (
            <span className="text-xs text-rose-200">保存失败，请刷新结果后重试。</span>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function SkillSetMemberPanel({
  batchProgress,
  focusedMemberId,
  installedSkills,
  installingId,
  onClose,
  onInstallAll,
  onInstallMember,
  onInspectTrust,
  project,
  trustIndex,
}: {
  batchProgress: SkillSetBatchProgress | null;
  focusedMemberId?: string;
  installedSkills: InstalledSkill[];
  installingId: string;
  onClose: () => void;
  onInstallAll: (members: SkillSetMemberSource[]) => void;
  onInstallMember: (member: SkillSetMemberSource) => void;
  onInspectTrust: (title: string, receiptId: string) => void;
  project: SkillProject;
  trustIndex: SkillTrustSummaryIndex | null;
}) {
  const panelRef = useRef<HTMLElement>(null);
  const [members, setMembers] = useState<SkillSetMemberSource[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [memberQuery, setMemberQuery] = useState("");
  const [memberPage, setMemberPage] = useState(1);

  useEffect(() => {
    let isCurrent = true;
    setMembers(null);
    setLoadError("");
    setMemberQuery("");
    setMemberPage(1);

    void loadSkillSetMemberIndex()
      .then((index) => {
        if (!isCurrent) return;
        const group = index.skillSets[project.id];
        if (!group) {
          throw new Error("该 SkillSet 的成员索引不存在，请重新运行目录核验。");
        }
        const resolvedMembers = group.memberIds
          .map((memberId) => index.members[memberId])
          .filter((member): member is SkillSetMemberSource => Boolean(member));
        if (resolvedMembers.length !== group.memberIds.length) {
          throw new Error("成员索引不完整，本次不会提供安装操作。");
        }
        setMembers(resolvedMembers);
        if (focusedMemberId) {
          const focusedMember = resolvedMembers.find(
            (member) => member.id === focusedMemberId,
          );
          if (focusedMember) setMemberQuery(focusedMember.name);
        }
      })
      .catch((memberError) => {
        if (!isCurrent) return;
        setLoadError(
          memberError instanceof Error ? memberError.message : "成员索引加载失败",
        );
      });

    window.requestAnimationFrame(() => {
      panelRef.current?.focus({ preventScroll: true });
      panelRef.current?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
    });

    return () => {
      isCurrent = false;
    };
  }, [focusedMemberId, project.id]);

  const filteredMembers = useMemo(() => {
    const query = memberQuery.trim().toLocaleLowerCase("zh-CN");
    if (!members || !query) return members ?? [];
    return members.filter((member) =>
      `${member.name} ${member.subPath}`
        .toLocaleLowerCase("zh-CN")
        .includes(query),
    );
  }, [memberQuery, members]);
  const pageCount = Math.max(
    1,
    Math.ceil(filteredMembers.length / SKILLSET_MEMBER_PAGE_SIZE),
  );
  const visibleMembers = filteredMembers.slice(
    (memberPage - 1) * SKILLSET_MEMBER_PAGE_SIZE,
    memberPage * SKILLSET_MEMBER_PAGE_SIZE,
  );
  const summary = project.skillSet;
  const remainingMemberCount = useMemo(
    () =>
      members?.filter((member) => !isSourceInstalled(member, installedSkills))
        .length ?? 0,
    [installedSkills, members],
  );
  const memberTrustCounts = useMemo(() => {
    const counts = { allow: 0, confirm: 0, blocked: 0 };
    for (const member of members ?? []) {
      if (isSourceInstalled(member, installedSkills)) continue;
      const summary =
        trustSummaryForCandidate(
          trustIndex,
          memberTrustCandidateId(member.id),
        ) ?? trustSummaryForSource(trustIndex, member);
      const policy = effectiveTrustInstallPolicy(
        trustIndex?.gateMode ?? "enforce",
        summary,
      );
      if (policy === "allow" || policy === "confirm") counts[policy] += 1;
      else counts.blocked += 1;
    }
    return counts;
  }, [installedSkills, members, trustIndex]);
  const activeBatchProgress =
    batchProgress?.projectId === project.id ? batchProgress : null;
  const isBatchInstalling = Boolean(activeBatchProgress);

  useEffect(() => {
    setMemberPage(1);
  }, [memberQuery]);

  return (
    <section
      aria-labelledby="skillset-member-title"
      className="mb-6 scroll-mt-4 rounded-lg border border-accent-300/25 bg-surface-900/80 p-5 shadow-prism focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-200"
      id="skillset-member-panel"
      ref={panelRef}
      tabIndex={-1}
    >
      <div className="flex flex-col gap-4 border-b border-white/10 pb-5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-accent-100">SkillSet 成员</p>
          <h2 className="mt-1 text-2xl font-semibold text-white" id="skillset-member-title">
            {project.name}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            该集合没有可整体安装的父级 SKILL.md。低风险成员可批量安装，需确认成员必须逐项核对；不会下载集合中的其他目录。
          </p>
        </div>
        <button
          className="shrink-0 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-white/30 hover:bg-white/[0.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
          disabled={isBatchInstalling}
          onClick={onClose}
          type="button"
        >
          收起成员
        </button>
      </div>

      <div className="grid gap-3 border-b border-white/10 py-4 text-xs sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <p className="text-slate-500">固定提交</p>
          <p className="mt-1 font-mono text-emerald-100">
            {summary?.verifiedCommit.slice(0, 12) ?? "—"}
          </p>
        </div>
        <div>
          <p className="text-slate-500">核验范围</p>
          <p className="mt-1 break-all font-mono text-slate-200">
            {summary?.scopeSubPath || "."}
          </p>
        </div>
        <div>
          <p className="text-slate-500">可安装成员</p>
          <p className="mt-1 font-semibold text-white">
            {summary?.memberCount ?? 0} 个
          </p>
        </div>
        <div>
          <p className="text-slate-500">包含结构</p>
          <p className="mt-1 font-semibold text-white">
            {summary?.skillDocumentCount ?? 0} 个 Skill 文档
            {summary?.nestedSkillCount
              ? ` · ${summary.nestedSkillCount} 个嵌套`
              : ""}
          </p>
        </div>
      </div>

      {members ? (
        <div className="mt-5 flex flex-col gap-3 rounded-lg border border-hire-300/20 bg-hire-300/[0.07] p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-white">安装整个成员集合</p>
            <p aria-live="polite" className="mt-1 text-xs leading-5 text-slate-300">
              {activeBatchProgress
                ? `正在安装 ${activeBatchProgress.completed} / ${activeBatchProgress.total}：${activeBatchProgress.currentMemberName}`
                : remainingMemberCount > 0
                  ? `${memberTrustCounts.allow} 个低风险成员可直接批量安装；${memberTrustCounts.confirm} 个需逐项确认，${memberTrustCounts.blocked} 个被阻断或缺少凭据。`
                  : "该集合的全部成员均已安装。"}
            </p>
          </div>
          <button
            className="shrink-0 rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
            disabled={
              memberTrustCounts.allow === 0 ||
              Boolean(installingId) ||
              isBatchInstalling
            }
            onClick={() => onInstallAll(members)}
            type="button"
          >
            {activeBatchProgress
              ? `安装中 ${activeBatchProgress.completed}/${activeBatchProgress.total}`
              : remainingMemberCount === 0
                ? "全部成员已安装"
                : memberTrustCounts.allow === 0
                  ? "无可直接批量安装成员"
                  : `批量安装低风险成员（${memberTrustCounts.allow}）`}
          </button>
        </div>
      ) : null}

      <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <label className="block w-full max-w-xl" htmlFor="skillset-member-search">
          <span className="text-xs font-semibold text-slate-300">搜索成员</span>
          <input
            className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 py-2.5 text-sm text-white placeholder:text-slate-500 transition focus:border-accent-300/50 focus:outline-none focus:ring-2 focus:ring-accent-300/15"
            id="skillset-member-search"
            onChange={(event) => setMemberQuery(event.target.value)}
            placeholder="输入成员名称或仓库路径"
            type="search"
            value={memberQuery}
          />
        </label>
        <a
          className="w-fit text-xs font-semibold text-slate-400 underline decoration-white/20 underline-offset-4 transition hover:text-white"
          href={project.repoUrl}
          rel="noreferrer"
          target="_blank"
        >
          查看来源仓库
        </a>
      </div>

      {loadError ? (
        <div className="mt-5 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50">
          {loadError}
        </div>
      ) : members === null ? (
        <div aria-live="polite" className="mt-5 space-y-2">
          {[0, 1, 2].map((item) => (
            <div
              className="h-16 animate-pulse rounded-md bg-white/[0.055] motion-reduce:animate-none"
              key={item}
            />
          ))}
          <span className="sr-only">正在加载成员索引</span>
        </div>
      ) : filteredMembers.length === 0 ? (
        <div className="mt-5 rounded-lg border border-dashed border-white/15 px-5 py-8 text-center">
          <p className="text-sm font-semibold text-white">没有匹配的集合成员</p>
          <p className="mt-2 text-xs text-slate-400">请尝试名称片段或目录路径。</p>
        </div>
      ) : (
        <>
          <div
            aria-live="polite"
            className="mt-5 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400"
          >
            <span>
              找到 <strong className="text-white">{filteredMembers.length}</strong> 个成员
            </span>
            <span>
              第 {memberPage} / {pageCount} 页 · 每页最多 {SKILLSET_MEMBER_PAGE_SIZE} 项
            </span>
          </div>
          <ul className="mt-3 divide-y divide-white/10 border-y border-white/10">
            {visibleMembers.map((member) => {
              const installed = isSourceInstalled(member, installedSkills);
              const isInstalling = installingId === member.id;
              const trustSummary =
                trustSummaryForCandidate(
                  trustIndex,
                  memberTrustCandidateId(member.id),
                ) ?? trustSummaryForSource(trustIndex, member);
              const effectivePolicy = effectiveTrustInstallPolicy(
                trustIndex?.gateMode ?? "enforce",
                trustSummary,
              );
              return (
                <li
                  className={`flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between ${
                    member.id === focusedMemberId
                      ? "rounded-lg bg-brand-300/10 px-3 ring-1 ring-brand-300/25"
                      : ""
                  }`}
                  key={member.id}
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-semibold text-white">{member.name}</p>
                      {member.nestedSkillCount > 0 ? (
                        <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300">
                          含 {member.nestedSkillCount} 个嵌套技能
                        </span>
                      ) : null}
                    </div>
                    <p className="mt-1 break-all font-mono text-xs leading-5 text-slate-500">
                      {member.subPath}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <SkillTrustBadge summary={trustSummary} />
                      {trustSummary ? (
                        <button
                          className="min-h-8 text-xs font-semibold text-slate-400 underline decoration-white/20 underline-offset-4 transition hover:text-white"
                          onClick={() => onInspectTrust(member.name, trustSummary.receiptId)}
                          type="button"
                        >
                          查看凭据
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <button
                    className="shrink-0 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                    disabled={
                      installed ||
                      Boolean(installingId) ||
                      isBatchInstalling ||
                      effectivePolicy === "block"
                    }
                    onClick={() => onInstallMember(member)}
                    type="button"
                  >
                    {isInstalling
                      ? "安装中..."
                      : installed
                        ? "已安装"
                        : effectivePolicy === "block"
                          ? "已阻断"
                          : "安装成员"}
                  </button>
                </li>
              );
            })}
          </ul>
          {pageCount > 1 ? (
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40"
                disabled={memberPage === 1}
                onClick={() => setMemberPage((current) => Math.max(1, current - 1))}
                type="button"
              >
                上一页
              </button>
              <button
                className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-40"
                disabled={memberPage === pageCount}
                onClick={() =>
                  setMemberPage((current) => Math.min(pageCount, current + 1))
                }
                type="button"
              >
                下一页
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function InstalledSkillCard({
  onAcknowledge,
  onInspectTrust,
  onManageVersions,
  onRevokeAcknowledgement,
  onUninstall,
  skill,
  trustSummary,
  uninstallingId,
}: {
  onAcknowledge: (skill: InstalledSkill) => void;
  onInspectTrust: (title: string, receiptId: string) => void;
  onManageVersions: (skill: InstalledSkill) => void;
  onRevokeAcknowledgement: (skill: InstalledSkill) => void;
  onUninstall: (skill: InstalledSkill) => void;
  skill: InstalledSkill;
  trustSummary: SkillTrustReceiptSummary | null;
  uninstallingId: string;
}) {
  const isUninstalling = uninstallingId === skill.skill_id;
  const isLocalImport = skill.source_kind === "local_import";
  const localRiskLabel =
    skill.trust_risk_level === "low"
      ? "低风险"
      : skill.trust_risk_level === "medium"
        ? "中风险"
        : skill.trust_risk_level === "high"
          ? "高风险"
          : skill.trust_risk_level === "critical"
            ? "严重风险"
            : "风险未知";

  return (
    <article className="rounded-lg border border-white/10 bg-white/[0.055] p-5 shadow-prism">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold text-emerald-100">已入职技能</p>
          <h3 className="mt-2 text-xl font-semibold text-white">{skill.name}</h3>
          <p className="mt-1 break-all text-xs text-slate-500">
            {isLocalImport
              ? `本地导入 ${skill.source_id || skill.skill_id}`
              : `${skill.repo_url} / ${skill.sub_path || "."}`}
          </p>
        </div>
        <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1 text-xs font-semibold text-emerald-100">
          {formatInstallTime(skill.installed_at)}
        </span>
      </div>
      <p className="mt-4 text-sm leading-6 text-slate-300">{skill.description}</p>
      <div className="mt-4 rounded-lg border border-white/10 bg-ink-950/55 p-3">
        <div className="flex flex-wrap items-center gap-2">
          {skill.source_kind === "git" ? (
            <SkillTrustBadge summary={trustSummary} />
          ) : isLocalImport ? (
            <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-2.5 py-1 text-xs font-semibold text-amber-100">
              {localRiskLabel} · {skill.trust_install_policy === "allow" ? "已验证" : "已确认"}
            </span>
          ) : (
            <span className="rounded-full border border-sky-300/25 bg-sky-300/10 px-2.5 py-1 text-xs font-semibold text-sky-100">
              本地来源合同
            </span>
          )}
          <span className={`text-xs font-semibold ${skill.trust_activation_allowed ? "text-emerald-100" : "text-amber-100"}`}>
            {skill.trust_activation_status === "ready"
              ? "可激活"
              : skill.trust_activation_status === "ack_required"
                ? "等待本机确认"
                : skill.trust_activation_status === "not_applicable"
                  ? "本地来源"
                  : "已禁止激活"}
          </span>
          {!skill.trust_router_eligible && ["git", "local_import"].includes(skill.source_kind) ? (
            <span className="text-xs text-slate-500">不纳入 Router</span>
          ) : null}
        </div>
        {skill.source_ref ? (
          <p className="mt-2 break-all font-mono text-[11px] text-slate-500">
            SHA {skill.source_ref}
          </p>
        ) : null}
        {isLocalImport && skill.content_digest ? (
          <p className="mt-2 break-all font-mono text-[11px] text-slate-500">
            package {skill.content_digest}
          </p>
        ) : null}
        {skill.trust_state === "unverified_legacy" ? (
          <p className="mt-2 text-xs leading-5 text-rose-100">
            此 Git Skill 未匹配当前固定凭据，可继续查看或卸载，但不能用于聊天、工作流或 Router。
          </p>
        ) : null}
        {skill.trust_reason_codes.length ? (
          <p className="mt-2 break-words text-[11px] leading-5 text-slate-500">
            原因：{skill.trust_reason_codes.join("、")}
          </p>
        ) : null}
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        {["git", "local_import", "workspace_draft"].includes(skill.source_kind) ? (
          <button
            className="min-h-10 rounded-full border border-hire-300/30 px-4 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/10"
            onClick={() => onManageVersions(skill)}
            type="button"
          >
            版本与恢复
          </button>
        ) : null}
        {isLocalImport && skill.source_id ? (
          <Link
            className="inline-flex min-h-10 items-center rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06]"
            to={`/skills/import/${encodeURIComponent(skill.source_id)}`}
          >
            查看导入
          </Link>
        ) : null}
        {skill.trust_receipt_id ? (
          <button
            className="min-h-10 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06]"
            onClick={() => onInspectTrust(skill.name, skill.trust_receipt_id!)}
            type="button"
          >
            查看凭据
          </button>
        ) : null}
        {skill.trust_activation_status === "ack_required" ? (
          <button
            className="min-h-10 rounded-full bg-amber-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-amber-200"
            onClick={() => onAcknowledge(skill)}
            type="button"
          >
            确认此版本
          </button>
        ) : null}
        {skill.trust_acknowledgement_required && skill.trust_acknowledgement_satisfied ? (
          <button
            className="min-h-10 rounded-full border border-amber-300/30 bg-amber-300/10 px-4 text-sm font-semibold text-amber-100 transition hover:bg-amber-300/20"
            onClick={() => onRevokeAcknowledgement(skill)}
            type="button"
          >
            撤销激活授权
          </button>
        ) : null}
        <button
          className="min-h-10 rounded-full border border-rose-300/30 bg-rose-300/10 px-4 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/20 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={isUninstalling}
          onClick={() => onUninstall(skill)}
          type="button"
        >
          {isUninstalling ? "卸载中..." : "卸载"}
        </button>
      </div>
    </article>
  );
}

export default function SkillBrowserPage() {
  const { status: creatorStatus } = useSkillCreatorStatus();
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  const [activeTab, setActiveTab] = useState<SkillTab>(
    requestedTab === "builtin" ||
      requestedTab === "installed" ||
      requestedTab === "imports" ||
      requestedTab === "drafts" ||
      requestedTab === "proposals"
      ? requestedTab
      : "market",
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [needQuery, setNeedQuery] = useState("");
  const [submittedNeed, setSubmittedNeed] = useState("");
  const [needMatches, setNeedMatches] = useState<
    SkillNeedMatch<SkillNeedTarget>[]
  >([]);
  const [needSearchStatus, setNeedSearchStatus] =
    useState<NeedSearchStatus>("idle");
  const [needSearchError, setNeedSearchError] = useState("");
  const [semanticRerankEnabled, setSemanticRerankEnabled] = useState(false);
  const [semanticConsentOpen, setSemanticConsentOpen] = useState(false);
  const [rankingStatus, setRankingStatus] = useState<SkillRerankStatus | "">("");
  const [rankingWarnings, setRankingWarnings] = useState<string[]>([]);
  const [rankingReceipt, setRankingReceipt] = useState<SkillRankingReceipt | null>(
    null,
  );
  const [rankingResults, setRankingResults] = useState<SkillRankingResult[]>([]);
  const [governanceRevision, setGovernanceRevision] = useState(1);
  const [feedbackByCandidate, setFeedbackByCandidate] = useState<
    Record<string, FeedbackStatus>
  >({});
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [selectedKind, setSelectedKind] = useState<SkillKindFilter>("all");
  const [selectedAvailability, setSelectedAvailability] =
    useState<SkillAvailabilityFilter>("all");
  const [visibleProjectCount, setVisibleProjectCount] = useState(MARKET_PAGE_SIZE);
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
  const [builtinSkills, setBuiltinSkills] = useState<BuiltinSkill[]>([]);
  const [isLoadingInstalled, setIsLoadingInstalled] = useState(false);
  const [isLoadingBuiltin, setIsLoadingBuiltin] = useState(false);
  const [trustIndex, setTrustIndex] = useState<SkillTrustSummaryIndex | null>(null);
  const [trustIndexStatus, setTrustIndexStatus] = useState<TrustIndexStatus>("loading");
  const [pendingTrustAction, setPendingTrustAction] = useState<PendingTrustAction | null>(null);
  const [selectedTrustReceipt, setSelectedTrustReceipt] = useState<SkillTrustReceipt | null>(null);
  const [trustActionBusy, setTrustActionBusy] = useState(false);
  const [installingId, setInstallingId] = useState("");
  const [uninstallingId, setUninstallingId] = useState("");
  const [lifecycleFocusSkillId, setLifecycleFocusSkillId] = useState("");
  const [selectedSkillSetId, setSelectedSkillSetId] = useState("");
  const [focusedSkillSetMemberId, setFocusedSkillSetMemberId] = useState("");
  const [skillSetBatchProgress, setSkillSetBatchProgress] =
    useState<SkillSetBatchProgress | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const needSearchRequestId = useRef(0);
  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    skillProjects.forEach((project) => {
      counts.set(project.category, (counts.get(project.category) ?? 0) + 1);
    });
    return [...counts.entries()].sort(([left], [right]) =>
      left.localeCompare(right, "zh-CN"),
    );
  }, []);
  const totalStars = useMemo(() => {
    const countedRepositories = new Set<string>();
    return skillProjects.reduce((sum, project) => {
      const metricsSource = project.catalogUrl ?? project.repoUrl;
      if (countedRepositories.has(metricsSource)) return sum;
      countedRepositories.add(metricsSource);
      return sum + project.stars;
    }, 0);
  }, []);
  const catalogCount = useMemo(
    () =>
      new Set(skillProjects.map((project) => project.catalogUrl ?? project.repoUrl)).size,
    [],
  );
  const skillsetCount = useMemo(
    () => skillProjects.filter((project) => project.kind === "skillset").length,
    [],
  );
  const installableProjects = useMemo(
    () => skillProjects.filter((project) => project.installStatus === "ready"),
    [],
  );
  const installStatusCounts = useMemo(() => {
    const counts: Record<SkillInstallStatus, number> = {
      ready: 0,
      manual: 0,
      pending: 0,
      reference: 0,
    };
    skillProjects.forEach((project) => {
      counts[project.installStatus] += 1;
    });
    return counts;
  }, []);
  const filteredProjects = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLocaleLowerCase("zh-CN");
    return skillProjects.filter((project) => {
      if (selectedCategory !== "all" && project.category !== selectedCategory) return false;
      if (selectedKind !== "all" && project.kind !== selectedKind) return false;
      if (
        selectedAvailability !== "all" &&
        project.installStatus !== selectedAvailability
      ) {
        return false;
      }
      if (!normalizedQuery) return true;

      const searchableText = [
        project.name,
        project.description,
        project.sourceDescription ?? "",
        project.category,
        project.repoName,
        project.catalogName ?? "",
        project.publisher ?? "",
        project.sourceGroup ?? "",
        ...project.tags,
        ...(project.includedSkills ?? []),
      ]
        .join(" ")
        .toLocaleLowerCase("zh-CN");
      return searchableText.includes(normalizedQuery);
    });
  }, [searchQuery, selectedAvailability, selectedCategory, selectedKind]);
  const visibleProjects = filteredProjects.slice(0, visibleProjectCount);
  const selectedSkillSetProject = useMemo(
    () =>
      skillProjects.find(
        (project) =>
          project.id === selectedSkillSetId &&
          project.installMode === "members" &&
          project.skillSet?.mode === "members",
      ),
    [selectedSkillSetId],
  );
  const hasActiveFilters =
    searchQuery.trim().length > 0 ||
    selectedCategory !== "all" ||
    selectedKind !== "all" ||
    selectedAvailability !== "all";

  useEffect(() => {
    document.title = "模镜 - Skill 技能货架";
    void refreshSkillResources();
  }, []);

  useEffect(() => {
    setVisibleProjectCount(MARKET_PAGE_SIZE);
  }, [searchQuery, selectedAvailability, selectedCategory, selectedKind]);

  async function loadInstalledSkills() {
    setIsLoadingInstalled(true);
    try {
      const response = await fetch("/api/skills/installed");
      if (!response.ok) throw new Error(await readApiError(response));
      const data = (await response.json()) as InstalledSkillsResponse;
      setInstalledSkills(data.skills);
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "已安装技能加载失败",
      );
    } finally {
      setIsLoadingInstalled(false);
    }
  }

  async function loadBuiltinSkills() {
    setIsLoadingBuiltin(true);
    try {
      const response = await fetch("/api/skills/library");
      if (!response.ok) throw new Error(await readApiError(response));
      const data = (await response.json()) as { skills: BuiltinSkill[] };
      setBuiltinSkills(data.skills);
    } catch (loadError) {
      setError(
        loadError instanceof Error ? loadError.message : "内置 Skill 加载失败",
      );
    } finally {
      setIsLoadingBuiltin(false);
    }
  }

  async function loadTrustIndex() {
    setTrustIndexStatus("loading");
    try {
      const index = await loadSkillTrustSummaryIndex(true);
      setTrustIndex(index);
      setTrustIndexStatus("ready");
    } catch (loadError) {
      setTrustIndex(null);
      setTrustIndexStatus("error");
      setError(
        loadError instanceof Error
          ? `Skill 信任索引加载失败：${loadError.message}`
          : "Skill 信任索引加载失败",
      );
    }
  }

  async function openTrustAction(action: PendingTrustAction) {
    setError("");
    setSelectedTrustReceipt(null);
    setPendingTrustAction(action);
    try {
      const receipt = await loadSkillTrustReceipt(action.receiptId);
      setSelectedTrustReceipt(receipt);
      window.requestAnimationFrame(() =>
        document.getElementById("skill-trust-panel")?.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "auto"
            : "smooth",
          block: "start",
        }),
      );
    } catch (loadError) {
      setPendingTrustAction(null);
      setError(
        loadError instanceof Error ? loadError.message : "Skill 信任凭据加载失败",
      );
    }
  }

  async function installFromSource({
    announceSuccess = true,
    installId,
    label,
    source,
    trustConfirmation,
    typeLabel,
  }: {
    announceSuccess?: boolean;
    installId: string;
    label: string;
    source: SkillInstallSource | SkillSetMemberSource;
    trustConfirmation?: SkillTrustReceiptSummary;
    typeLabel: string;
  }) {
    if (installingId) return false;

    setInstallingId(installId);
    setError("");
    setNotice("");

    try {
      const response = await fetch("/api/skills/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_url: source.repoUrl,
          sub_path: source.subPath,
          ref: source.verifiedCommit,
          ...(trustConfirmation
            ? {
                expected_trust_fingerprint: trustConfirmation.trustFingerprint,
                confirmed: true,
              }
            : {}),
        }),
      });
      if (!response.ok) throw new Error(await readApiError(response));
      const installed = (await response.json()) as InstalledSkill;
      setInstalledSkills((current) => [
        installed,
        ...current.filter((skill) => skill.skill_id !== installed.skill_id),
      ]);
      if (announceSuccess) {
        setNotice(`「${label}」${typeLabel}已安装，可在面试间选择使用。`);
      }
      return true;
    } catch (installError) {
      const message =
        installError instanceof Error ? installError.message : "技能安装失败";
      setError(`「${label}」安装失败：${message}`);
      return false;
    } finally {
      setInstallingId("");
    }
  }

  async function installSkill(project: SkillProject) {
    if (!project.installSource || project.installMode !== "direct") return;
    await requestTrustedInstall({
      candidateId: projectTrustCandidateId(project.id),
      installId: project.id,
      label: project.name,
      source: project.installSource,
      typeLabel: project.kind === "skillset" ? "技能包" : "技能",
    });
  }

  async function installSkillSetMember(member: SkillSetMemberSource) {
    await requestTrustedInstall({
      candidateId: memberTrustCandidateId(member.id),
      installId: member.id,
      label: member.name,
      source: member,
      typeLabel: "成员",
    });
  }

  async function requestTrustedInstall({
    candidateId,
    installId,
    label,
    source,
    typeLabel,
  }: {
    candidateId: string;
    installId: string;
    label: string;
    source: SkillInstallSource | SkillSetMemberSource;
    typeLabel: string;
  }) {
    const summary =
      trustSummaryForCandidate(trustIndex, candidateId) ??
      trustSummaryForSource(trustIndex, source);
    const policy = effectiveTrustInstallPolicy(
      trustIndex?.gateMode ?? "enforce",
      summary,
    );
    if (!summary && policy === "block") {
      setError("该来源没有匹配的信任凭据，本次不会安装。");
      return false;
    }
    if (policy === "block" && summary) {
      setError("该来源已被信任策略阻断；可查看凭据了解原因，但不能安装。");
      await openTrustAction({
        kind: "inspect",
        title: label,
        receiptId: summary.receiptId,
      });
      return false;
    }
    if (policy === "confirm" && summary) {
      await openTrustAction({
        kind: "install",
        title: label,
        receiptId: summary.receiptId,
        installId,
        label,
        source,
        typeLabel,
      });
      return false;
    }
    return installFromSource({ installId, label, source, typeLabel });
  }

  async function installAllSkillSetMembers(
    project: SkillProject,
    members: SkillSetMemberSource[],
  ) {
    if (installingId || skillSetBatchProgress) return;

    const pendingMembers = members.filter(
      (member) => !isSourceInstalled(member, installedSkills),
    );
    const skippedCount = members.length - pendingMembers.length;
    if (pendingMembers.length === 0) {
      setError("");
      setNotice(`「${project.name}」的全部成员均已安装。`);
      return;
    }

    const automaticallyInstallable = pendingMembers.filter(
      (member) => {
        const summary = trustSummaryForCandidate(
          trustIndex,
          memberTrustCandidateId(member.id),
        ) ?? trustSummaryForSource(trustIndex, member);
        return effectiveTrustInstallPolicy(
          trustIndex?.gateMode ?? "enforce",
          summary,
        ) === "allow";
      },
    );
    const confirmationCount = pendingMembers.filter(
      (member) => {
        const summary = trustSummaryForCandidate(
          trustIndex,
          memberTrustCandidateId(member.id),
        ) ?? trustSummaryForSource(trustIndex, member);
        return effectiveTrustInstallPolicy(
          trustIndex?.gateMode ?? "enforce",
          summary,
        ) === "confirm";
      },
    ).length;
    const blockedCount = pendingMembers.length - automaticallyInstallable.length - confirmationCount;
    if (automaticallyInstallable.length === 0) {
      setNotice(
        `该集合没有可直接批量安装的低风险成员；${confirmationCount} 个需逐项确认，${blockedCount} 个被阻断或缺少凭据。`,
      );
      return;
    }

    setError("");
    setNotice("");
    let completed = 0;

    for (const member of automaticallyInstallable) {
      setSkillSetBatchProgress({
        projectId: project.id,
        completed,
        total: automaticallyInstallable.length,
        currentMemberName: member.name,
      });
      const installed = await installFromSource({
        announceSuccess: false,
        installId: member.id,
        label: member.name,
        source: member,
        typeLabel: "成员",
      });
      if (!installed) {
        setNotice(
          `「${project.name}」已安装 ${completed} / ${automaticallyInstallable.length} 个可直接安装成员；失败后已停止，可再次继续。`,
        );
        setSkillSetBatchProgress(null);
        return;
      }
      completed += 1;
      setSkillSetBatchProgress({
        projectId: project.id,
        completed,
        total: automaticallyInstallable.length,
        currentMemberName: member.name,
      });
    }

    setSkillSetBatchProgress(null);
    setNotice(
      `「${project.name}」已按顺序安装 ${completed} 个低风险成员${
        skippedCount > 0 ? `，并跳过 ${skippedCount} 个已安装成员` : ""
      }${confirmationCount ? `；另有 ${confirmationCount} 个需逐项确认` : ""}${blockedCount ? `，${blockedCount} 个不可安装` : ""}。`,
    );
  }

  async function confirmPendingTrustAction() {
    if (!pendingTrustAction || !selectedTrustReceipt) return;
    setTrustActionBusy(true);
    setError("");
    try {
      if (pendingTrustAction.kind === "install") {
        const installed = await installFromSource({
          installId: pendingTrustAction.installId,
          label: pendingTrustAction.label,
          source: pendingTrustAction.source,
          trustConfirmation: selectedTrustReceipt,
          typeLabel: pendingTrustAction.typeLabel,
        });
        if (!installed) return;
      } else if (pendingTrustAction.kind === "acknowledge") {
        const response = await fetch(
          `/api/skills/${encodeURIComponent(pendingTrustAction.skill.skill_id)}/trust-acknowledgement`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              expected_trust_fingerprint: selectedTrustReceipt.trustFingerprint,
              confirmed: true,
            }),
          },
        );
        if (!response.ok) throw new Error(await readApiError(response));
        const updated = (await response.json()) as InstalledSkill;
        setInstalledSkills((current) =>
          current.map((skill) =>
            skill.skill_id === updated.skill_id ? updated : skill,
          ),
        );
        setNotice(`「${updated.name}」已授权当前固定版本在本机激活。`);
      }
      setPendingTrustAction(null);
      setSelectedTrustReceipt(null);
    } catch (actionError) {
      setError(
        actionError instanceof Error ? actionError.message : "Skill 信任确认失败",
      );
    } finally {
      setTrustActionBusy(false);
    }
  }

  async function revokeTrustAcknowledgement(skill: InstalledSkill) {
    if (!window.confirm(`撤销「${skill.name}」当前版本的激活授权吗？`)) return;
    setError("");
    try {
      const response = await fetch(
        `/api/skills/${encodeURIComponent(skill.skill_id)}/trust-acknowledgement`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await readApiError(response));
      const updated = (await response.json()) as InstalledSkill;
      setInstalledSkills((current) =>
        current.map((item) => (item.skill_id === updated.skill_id ? updated : item)),
      );
      setNotice(`「${updated.name}」的激活授权已撤销；仍可查看或卸载。`);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "撤销授权失败");
    }
  }

  async function submitNeedSearch(value: string) {
    const normalized = value.trim().slice(0, 500);
    setNeedQuery(normalized);
    setSubmittedNeed(normalized);
    const requestId = needSearchRequestId.current + 1;
    needSearchRequestId.current = requestId;
    if (!normalized) {
      setNeedMatches([]);
      setNeedSearchStatus("idle");
      setNeedSearchError("");
      setRankingStatus("");
      setRankingWarnings([]);
      setRankingReceipt(null);
      setRankingResults([]);
      setFeedbackByCandidate({});
      return;
    }
    setNeedMatches([]);
    setNeedSearchStatus("loading");
    setNeedSearchError("");
    setFeedbackByCandidate({});
    try {
      const candidates = await loadSkillNeedCandidates();
      if (needSearchRequestId.current !== requestId) return;
      if (!semanticRerankEnabled) {
        setNeedMatches(findSkillsForNeed(normalized, candidates));
        setRankingStatus("lexical");
        setRankingWarnings([]);
        setRankingReceipt(null);
        setRankingResults([]);
      } else {
        const outcome = await searchSkills(normalized, true);
        if (needSearchRequestId.current !== requestId) return;
        const byId = new Map(
          candidates.map((candidate) => [needCandidateId(candidate), candidate]),
        );
        const matches = outcome.finalResults.flatMap((result) => {
          const target = byId.get(result.candidateId);
          if (!target) return [];
          return [
            {
              project: target,
              score: result.score,
              reasons: result.reasons,
            } as SkillNeedMatch<SkillNeedTarget>,
          ];
        });
        setNeedMatches(matches);
        setRankingStatus(outcome.status);
        setRankingWarnings(outcome.warnings);
        setRankingReceipt(outcome.receipt);
        setRankingResults(outcome.finalResults);
        setGovernanceRevision(outcome.governanceRevision);
      }
      setNeedSearchStatus("ready");
    } catch (searchError) {
      if (needSearchRequestId.current !== requestId) return;
      setNeedSearchStatus("error");
      setNeedSearchError(
        searchError instanceof Error
          ? searchError.message
          : "完整 Skill 索引加载失败",
      );
    }
  }

  async function submitRerankFeedback(
    target: SkillNeedTarget,
    judgment: "relevant" | "not_relevant",
  ) {
    const candidateId = needCandidateId(target);
    const result = rankingResults.find((item) => item.candidateId === candidateId);
    if (!rankingReceipt || !result || !submittedNeed) return;
    setFeedbackByCandidate((current) => ({ ...current, [candidateId]: "saving" }));
    try {
      const response = await saveSkillRerankFeedback({
        expectedRevision: governanceRevision,
        query: submittedNeed,
        candidateId,
        candidateFingerprint: result.candidateFingerprint,
        judgment,
        receipt: rankingReceipt,
      });
      setGovernanceRevision(response.governanceRevision);
      setFeedbackByCandidate((current) => ({
        ...current,
        [candidateId]: judgment,
      }));
    } catch {
      setFeedbackByCandidate((current) => ({ ...current, [candidateId]: "error" }));
    }
  }

  async function refreshSkillResources() {
    setError("");
    await Promise.all([loadInstalledSkills(), loadBuiltinSkills(), loadTrustIndex()]);
  }

  function locateRecommendedSkill(project: SkillProject) {
    setSearchQuery(project.name);
    setSelectedCategory("all");
    setSelectedKind("all");
    setSelectedAvailability("all");
    setVisibleProjectCount(MARKET_PAGE_SIZE);
    window.requestAnimationFrame(() =>
      window.requestAnimationFrame(() =>
        document
          .getElementById("skill-market-results")
          ?.scrollIntoView({ behavior: "smooth", block: "start" }),
      ),
    );
  }

  function openSkillSet(project: SkillProject, memberId = "") {
    if (project.installMode !== "members" || project.skillSet?.mode !== "members") {
      return;
    }
    setActiveTab("market");
    setSelectedSkillSetId(project.id);
    setFocusedSkillSetMemberId(memberId);
  }

  function resetMarketFilters() {
    setSearchQuery("");
    setSelectedCategory("all");
    setSelectedKind("all");
    setSelectedAvailability("all");
    setVisibleProjectCount(MARKET_PAGE_SIZE);
  }

  async function uninstallSkill(skill: InstalledSkill) {
    if (!window.confirm(`确定卸载「${skill.name}」吗？`)) return;

    setUninstallingId(skill.skill_id);
    setError("");
    setNotice("");

    try {
      const response = await fetch(`/api/skills/${encodeURIComponent(skill.skill_id)}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await readApiError(response));
      setInstalledSkills((current) =>
        current.filter((item) => item.skill_id !== skill.skill_id),
      );
      setNotice(`${skill.name} 已卸载。`);
    } catch (uninstallError) {
      setError(
        uninstallError instanceof Error ? uninstallError.message : "技能卸载失败",
      );
    } finally {
      setUninstallingId("");
    }
  }

  function manageSkillVersions(skill: InstalledSkill) {
    setLifecycleFocusSkillId(skill.skill_id);
    window.setTimeout(() => {
      document.getElementById("skill-lifecycle-panel")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }, 0);
  }

  return (
    <PageContainer
      activeResource="skills"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">技能培训服务台</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            Skill 是单项岗位手册；SkillSet 可能是可整体安装的父包，也可能是需要逐项选择的成员集合。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">可安装资源</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">
              {installableProjects.length} 个
            </p>
          </div>
          <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">已安装</p>
            <p className="mt-1 text-sm font-semibold text-emerald-100">
              {installedSkills.length} 个
            </p>
          </div>
          <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">目录来源</p>
            <p className="mt-1 text-sm font-semibold text-brand-100">
              {catalogCount} 个 · ★ {formatStars(totalStars)}
            </p>
          </div>
          <p className="mt-4 text-xs leading-5 text-slate-500">
            社区 Skill 安装时只复制目录，不自动执行脚本。激活前请检查依赖、外部服务和凭据要求。
          </p>
          <p className={`mt-2 text-xs font-semibold ${trustIndexStatus === "ready" ? trustIndex?.gateMode === "enforce" ? "text-emerald-200" : "text-amber-100" : trustIndexStatus === "error" ? "text-rose-200" : "text-slate-400"}`}>
            {trustIndexStatus === "ready"
              ? trustIndex?.gateMode === "enforce"
                ? "信任目录已核对 · 强制门禁"
                : trustIndex?.gateMode === "audit"
                  ? "信任门处于审计模式 · 不阻断"
                  : "信任门已关闭 · 按旧行为运行"
              : trustIndexStatus === "error"
                ? "信任目录不可用 · 安装已关闭"
                : "正在核对信任目录…"}
          </p>
        </div>
      }
    >
      <header className="relative overflow-hidden border-y border-hire-300/20 py-8 sm:py-10 lg:py-12">
        <div className="absolute inset-x-6 top-0 h-16 rounded-b-[50%] border-x border-b border-hire-300/30 bg-[linear-gradient(180deg,rgba(251,146,60,0.18),transparent)]" />
        <div className="absolute left-0 top-0 h-px w-full animate-pulse-line bg-[linear-gradient(90deg,transparent,rgba(251,146,60,0.82),rgba(253,186,116,0.72),transparent)]" />
        <div className="relative grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
          <div>
            <p className="text-sm font-semibold text-hire-200">
              技能培训教室开放报名
            </p>
            <h1 className="mt-3 max-w-4xl text-4xl font-semibold tracking-normal text-white sm:text-6xl">
              Skill 技能货架
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
              按任务、分类和资源类型查找岗位手册。独立 Skill 与父级技能包可直接安装，成员集合可展开后逐项选择。
            </p>
          </div>

          <div className="surface-card rounded-lg p-4">
            <div className="flex items-center justify-between border-b border-white/10 pb-4">
              <span className="text-sm text-slate-400">货架状态</span>
              <span className="text-2xl font-semibold text-white">
                {skillProjects.length}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-hire-100">
                  {installableProjects.length}
                </p>
                <p className="mt-1 truncate text-slate-400">可安装</p>
              </div>
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-emerald-100">
                  {skillsetCount}
                </p>
                <p className="mt-1 truncate text-slate-400">SkillSet</p>
              </div>
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-brand-100">
                  {categories.length}
                </p>
                <p className="mt-1 truncate text-slate-400">分类</p>
              </div>
            </div>
          </div>
        </div>
      </header>

      <section className="mt-8">
        <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex max-w-full overflow-x-auto rounded-full border border-white/10 bg-white/[0.055] p-1">
            {[
              { id: "builtin", label: `Agent 内置（${builtinSkills.length || 16}）` },
              { id: "market", label: "技能市场" },
              { id: "installed", label: "已安装" },
              { id: "imports", label: "本地导入" },
              { id: "drafts", label: "工作区草稿" },
              { id: "proposals", label: "待审提案" },
            ].map((tab) => (
              <button
                className={`shrink-0 rounded-full px-4 py-2 text-sm font-semibold transition ${
                  activeTab === tab.id
                    ? "bg-hire-300 text-ink-950 shadow-[0_0_18px_rgba(251,146,60,0.24)]"
                    : "text-slate-300 hover:bg-white/[0.06] hover:text-white"
                }`}
                key={tab.id}
                onClick={() => setActiveTab(tab.id as SkillTab)}
                type="button"
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              className="inline-flex min-h-10 w-fit items-center rounded-full border border-hire-300/30 px-4 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/10"
              to="/skills/import"
            >
              导入本地 Skill
            </Link>
            <Link
              className="inline-flex min-h-10 w-fit items-center rounded-full border border-cyan-300/25 px-4 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/10"
              to="/skills/rerank"
            >
              重排治理
            </Link>
            {creatorStatus?.enabled ? (
              <Link
                className="w-fit rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:ring-2 focus-visible:ring-hire-100"
                to="/skills/create"
              >
                创建 Skill
              </Link>
            ) : null}
            <button
              className="w-fit rounded-full border border-white/10 px-4 py-2 text-sm font-semibold text-slate-300 transition hover:border-hire-300/30 hover:bg-hire-300/10 hover:text-hire-100 disabled:opacity-50"
              disabled={isLoadingInstalled || isLoadingBuiltin || trustIndexStatus === "loading"}
              onClick={() => void refreshSkillResources()}
              type="button"
            >
              {isLoadingInstalled || isLoadingBuiltin || trustIndexStatus === "loading" ? "刷新中..." : "刷新资源"}
            </button>
          </div>
        </div>

        {activeTab === "market" ? (
          <section className="mb-5">
            <div className="grid gap-5 rounded-lg border border-brand-300/20 bg-surface-900/75 p-5 lg:grid-cols-[minmax(0,1fr)_260px] lg:items-start">
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void submitNeedSearch(needQuery);
                }}
              >
                <label className="block" htmlFor="skill-need-query">
                  <span className="text-sm font-semibold text-white">
                    描述你要完成的事
                  </span>
                  <span className="mt-1 block text-xs leading-5 text-slate-400">
                    本地分析任务、工具和目标，推荐结果不会自动安装，也不会查询外部市场。
                  </span>
                </label>
                <textarea
                  className="mt-3 min-h-24 w-full resize-y rounded-lg border border-white/10 bg-ink-950/80 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 transition focus:border-brand-300/50 focus:outline-none"
                  id="skill-need-query"
                  maxLength={500}
                  onChange={(event) => setNeedQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                      event.preventDefault();
                      void submitNeedSearch(needQuery);
                    }
                  }}
                  placeholder="例如：为 React 网页编写 Playwright 自动化测试，并能检查交互和错误"
                  value={needQuery}
                />
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    className="rounded-full bg-brand-200 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                    disabled={!needQuery.trim() || needSearchStatus === "loading"}
                    type="submit"
                  >
                    {needSearchStatus === "loading"
                      ? "正在检索完整目录..."
                      : "寻找合适的 Skill"}
                  </button>
                  {submittedNeed ? (
                    <button
                      className="rounded-full border border-white/10 px-4 py-2 text-sm font-semibold text-slate-300 transition hover:bg-white/[0.06] hover:text-white"
                      onClick={() => {
                        setNeedQuery("");
                        setSubmittedNeed("");
                        needSearchRequestId.current += 1;
                        setNeedMatches([]);
                        setNeedSearchStatus("idle");
                        setNeedSearchError("");
                        setRankingStatus("");
                        setRankingWarnings([]);
                        setRankingReceipt(null);
                        setRankingResults([]);
                        setFeedbackByCandidate({});
                      }}
                      type="button"
                    >
                      清除推荐
                    </button>
                  ) : null}
                  <span className="text-xs text-slate-500">
                    Ctrl / ⌘ + Enter 快速查找
                  </span>
                </div>
              </form>

              <div className="rounded-lg border border-white/10 bg-ink-950/55 p-4">
                <p className="text-xs font-semibold text-slate-300">试试这些需求</p>
                <div className="mt-3 flex flex-col gap-2">
                  {NEED_EXAMPLES.map((example) => (
                    <button
                      className="rounded-md border border-white/10 bg-white/[0.045] px-3 py-2 text-left text-xs leading-5 text-slate-300 transition hover:border-brand-300/30 hover:bg-brand-300/10 hover:text-white"
                      key={example}
                      onClick={() => void submitNeedSearch(example)}
                      type="button"
                    >
                      {example}
                    </button>
                  ))}
                </div>
                <div className="mt-4 border-t border-white/10 pt-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-xs font-semibold text-white">语义重排</p>
                      <p className="mt-1 text-xs leading-5 text-slate-400">
                        {semanticRerankEnabled ? "仅对本页后续查询启用" : "默认关闭，刷新后仍关闭"}
                      </p>
                    </div>
                    <button
                      aria-pressed={semanticRerankEnabled}
                      className={`min-h-10 rounded-full border px-3 text-xs font-semibold transition ${
                        semanticRerankEnabled
                          ? "border-cyan-300/35 bg-cyan-300/15 text-cyan-50"
                          : "border-white/15 text-slate-300 hover:bg-white/[0.06]"
                      }`}
                      onClick={() => {
                        if (semanticRerankEnabled) {
                          setSemanticRerankEnabled(false);
                          setSemanticConsentOpen(false);
                          setSubmittedNeed("");
                          setNeedMatches([]);
                          setNeedSearchStatus("idle");
                          setRankingStatus("");
                          setRankingReceipt(null);
                          setRankingResults([]);
                          setFeedbackByCandidate({});
                        } else {
                          setSemanticConsentOpen(true);
                        }
                      }}
                      type="button"
                    >
                      {semanticRerankEnabled ? "已开启" : "开启"}
                    </button>
                  </div>
                  {semanticConsentOpen && !semanticRerankEnabled ? (
                    <div className="mt-3 rounded-md bg-cyan-300/10 p-3 text-xs leading-5 text-cyan-50">
                      <p>
                        开启后会向当前配置的重排服务发送需求文本，以及最多 24 个公共目录候选的名称、标签和能力摘要。
                        本地导入、Creator、插件、Skill 正文、信任详情和安装记录不会外发。
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <button
                          className="min-h-10 rounded-full bg-cyan-200 px-3 font-semibold text-ink-950 transition hover:bg-white"
                          onClick={() => {
                            setSemanticRerankEnabled(true);
                            setSemanticConsentOpen(false);
                          }}
                          type="button"
                        >
                          开启语义重排
                        </button>
                        <button
                          className="min-h-10 rounded-full border border-white/15 px-3 font-semibold text-slate-200 transition hover:bg-white/[0.06]"
                          onClick={() => setSemanticConsentOpen(false)}
                          type="button"
                        >
                          保持词典排序
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>

            {submittedNeed ? (
              <div className="mt-5">
                <div
                  aria-live="polite"
                  className="mb-4 flex flex-wrap items-center justify-between gap-2"
                >
                  <p className="text-sm font-semibold text-white">
                    {needSearchStatus === "loading"
                      ? "正在加载顶层 Skill 与已核验成员"
                      : needSearchStatus === "error"
                        ? "完整目录加载失败"
                        : needMatches.length > 0
                          ? `找到 ${needMatches.length} 个较相关的 Skill`
                          : "当前目录没有可靠匹配"}
                  </p>
                  <p className="text-xs text-slate-500">
                    {rankingStatus === "semantic"
                      ? "语义重排，仍保留词典命中理由"
                      : rankingStatus === "lexical_fallback"
                        ? "语义服务已降级，当前使用词典排序"
                        : "词典排序，覆盖顶层目录与 SkillSet 成员"}
                  </p>
                </div>
                {rankingStatus === "lexical_fallback" ? (
                  <p className="mb-4 rounded-md bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
                    重排服务未返回可用结果，已安全回退。{rankingWarnings[0] ? ` 原因：${rankingWarnings[0]}` : ""}
                  </p>
                ) : null}
                {needSearchStatus === "loading" ? (
                  <div
                    aria-label="正在加载 Skill 推荐"
                    className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3"
                    role="status"
                  >
                    {[0, 1, 2].map((item) => (
                      <div
                        className="h-64 animate-pulse rounded-lg border border-white/10 bg-white/[0.045] motion-reduce:animate-none"
                        key={item}
                      />
                    ))}
                  </div>
                ) : needSearchStatus === "error" ? (
                  <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-5 py-5">
                    <p className="text-sm font-semibold text-rose-50">
                      未返回不完整的推荐结果
                    </p>
                    <p className="mt-2 text-xs leading-5 text-rose-100/80">
                      {needSearchError}
                    </p>
                    <button
                      className="mt-4 rounded-full border border-rose-200/30 px-4 py-2 text-xs font-semibold text-rose-50 transition hover:bg-rose-100/10"
                      onClick={() => void submitNeedSearch(submittedNeed)}
                      type="button"
                    >
                      重新加载完整目录
                    </button>
                  </div>
                ) : needMatches.length > 0 ? (
                  <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                    {needMatches.map((match) => (
                      <NeedMatchCard
                        gateMode={trustIndex?.gateMode ?? "enforce"}
                        installed={
                          match.project.targetType === "member"
                            ? isSourceInstalled(
                                match.project.installSource,
                                installedSkills,
                              )
                            : isProjectInstalled(
                                match.project.project,
                                installedSkills,
                              )
                        }
                        installingId={installingId}
                        key={match.project.id}
                        match={match}
                        onInstallMember={(member) =>
                          void installSkillSetMember(member)
                        }
                        onInstallProject={(project) => void installSkill(project)}
                        onInspectTrust={(title, receiptId) =>
                          void openTrustAction({ kind: "inspect", title, receiptId })
                        }
                        onLocate={locateRecommendedSkill}
                        onOpenSkillSet={openSkillSet}
                        onFeedback={
                          semanticRerankEnabled &&
                          rankingStatus === "semantic" &&
                          rankingReceipt
                            ? (judgment) =>
                                void submitRerankFeedback(match.project, judgment)
                            : undefined
                        }
                        rankingResult={rankingResults.find(
                          (item) => item.candidateId === needCandidateId(match.project),
                        )}
                        feedbackStatus={
                          feedbackByCandidate[needCandidateId(match.project)] ?? ""
                        }
                        trustSummary={trustSummaryForCandidate(
                          trustIndex,
                          match.project.targetType === "member"
                            ? memberTrustCandidateId(match.project.member.id)
                            : projectTrustCandidateId(match.project.project.id),
                        )}
                      />
                    ))}
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed border-white/15 bg-ink-950/45 px-5 py-7 text-center">
                    <p className="text-sm font-semibold text-white">
                      现有顶层目录与已核验成员暂未覆盖这项需求。
                    </p>
                    <p className="mt-2 text-xs leading-5 text-slate-400">
                      请补充具体工具、输入或输出；本轮不会伪造 Skill，也不会转向未批准的外部市场。
                    </p>
                  </div>
                )}
              </div>
            ) : null}
          </section>
        ) : null}

        {activeTab === "market" ? (
          <div className="mb-5 rounded-lg border border-white/10 bg-white/[0.04] p-4">
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_190px_170px_auto] lg:items-end">
              <label className="block">
                <span className="text-xs font-semibold text-slate-300">搜索技能</span>
                <input
                  className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 py-2.5 text-sm text-white placeholder:text-slate-500 transition focus:border-brand-300/50 focus:outline-none"
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="名称、能力、标签或仓库"
                  type="search"
                  value={searchQuery}
                />
              </label>

              <label className="block">
                <span className="text-xs font-semibold text-slate-300">分类</span>
                <select
                  className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 py-2.5 text-sm text-white transition focus:border-brand-300/50 focus:outline-none"
                  onChange={(event) => setSelectedCategory(event.target.value)}
                  value={selectedCategory}
                >
                  <option value="all">全部分类</option>
                  {categories.map(([category, count]) => (
                    <option key={category} value={category}>
                      {category}（{count}）
                    </option>
                  ))}
                </select>
              </label>

              <label className="block">
                <span className="text-xs font-semibold text-slate-300">安装状态</span>
                <select
                  className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 py-2.5 text-sm text-white transition focus:border-brand-300/50 focus:outline-none"
                  onChange={(event) =>
                    setSelectedAvailability(event.target.value as SkillAvailabilityFilter)
                  }
                  value={selectedAvailability}
                >
                  <option value="all">全部资源</option>
                  <option value="ready">固定来源可用（{installStatusCounts.ready}）</option>
                  {installStatusCounts.manual > 0 ? (
                    <option value="manual">
                      有安装说明（{installStatusCounts.manual}）
                    </option>
                  ) : null}
                  <option value="pending">待核验来源（{installStatusCounts.pending}）</option>
                  <option value="reference">仅资料参考（{installStatusCounts.reference}）</option>
                </select>
              </label>

              <fieldset>
                <legend className="text-xs font-semibold text-slate-300">资源类型</legend>
                <div className="mt-2 inline-flex rounded-lg border border-white/10 bg-ink-950/80 p-1">
                  {[
                    { id: "all", label: "全部" },
                    { id: "skill", label: "Skill" },
                    { id: "skillset", label: "SkillSet" },
                  ].map((kind) => (
                    <button
                      aria-pressed={selectedKind === kind.id}
                      className={`rounded-md px-3 py-1.5 text-sm font-semibold transition ${
                        selectedKind === kind.id
                          ? "bg-brand-300/20 text-brand-100"
                          : "text-slate-400 hover:bg-white/[0.06] hover:text-white"
                      }`}
                      key={kind.id}
                      onClick={() => setSelectedKind(kind.id as SkillKindFilter)}
                      type="button"
                    >
                      {kind.label}
                    </button>
                  ))}
                </div>
              </fieldset>
            </div>

            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-3">
              <p aria-live="polite" className="text-sm text-slate-400">
                显示 <span className="font-semibold text-white">{filteredProjects.length}</span> / {skillProjects.length} 项
              </p>
              {hasActiveFilters ? (
                <button
                  className="text-sm font-semibold text-brand-100 underline decoration-brand-300/30 underline-offset-4 transition hover:text-white"
                  onClick={resetMarketFilters}
                  type="button"
                >
                  清除筛选
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        {notice ? (
          <div className="mb-4 rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-4 py-3 text-sm text-emerald-50">
            {notice}
          </div>
        ) : null}
        {error ? (
          <div className="mb-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50">
            {error}
          </div>
        ) : null}

        {pendingTrustAction && selectedTrustReceipt ? (
          <SkillTrustPanel
            action={pendingTrustAction.kind}
            busy={trustActionBusy}
            onCancel={() => {
              setPendingTrustAction(null);
              setSelectedTrustReceipt(null);
            }}
            onConfirm={() => void confirmPendingTrustAction()}
            receipt={selectedTrustReceipt}
            title={pendingTrustAction.title}
          />
        ) : pendingTrustAction ? (
          <div aria-live="polite" className="mb-6 rounded-lg border border-white/10 bg-white/[0.04] px-4 py-3 text-sm text-slate-300">
            正在加载固定版本的信任凭据…
          </div>
        ) : null}

        {activeTab === "market" && selectedSkillSetProject ? (
          <SkillSetMemberPanel
            batchProgress={skillSetBatchProgress}
            focusedMemberId={focusedSkillSetMemberId}
            installedSkills={installedSkills}
            installingId={installingId}
            onClose={() => {
              setSelectedSkillSetId("");
              setFocusedSkillSetMemberId("");
            }}
            onInstallAll={(members) =>
              void installAllSkillSetMembers(selectedSkillSetProject, members)
            }
            onInstallMember={(member) => void installSkillSetMember(member)}
            onInspectTrust={(title, receiptId) =>
              void openTrustAction({ kind: "inspect", title, receiptId })
            }
            project={selectedSkillSetProject}
            trustIndex={trustIndex}
          />
        ) : null}

        {activeTab === "builtin" ? (
          builtinSkills.length > 0 ? (
            <div>
              <div className="mb-4 rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-4 py-3 text-sm leading-6 text-cyan-50">
                General Agent 默认 Skillset 固定包含以下 16 项并保存内容摘要。只有标记为“可运行”且环境满足的 Skill 会注入运行上下文；外部 Skill 不在此集合中。
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                {builtinSkills.map((skill) => {
                  const status =
                    skill.status === "ready"
                      ? { label: "可运行", className: "border-emerald-300/25 bg-emerald-300/10 text-emerald-100" }
                      : skill.status === "conditional"
                        ? { label: "环境探测", className: "border-sky-300/25 bg-sky-300/10 text-sky-100" }
                        : skill.status === "dependency_missing"
                          ? { label: "依赖缺失", className: "border-amber-300/25 bg-amber-300/10 text-amber-100" }
                          : { label: "仅供查看", className: "border-white/15 bg-white/[0.05] text-slate-300" };
                  return (
                    <article className="rounded-lg border border-white/10 bg-slate-950/55 p-5" key={skill.skill_id}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="truncate font-mono text-sm font-semibold text-white">{skill.skill_id}</h3>
                          <p className="mt-2 text-sm leading-6 text-slate-300">{skill.description}</p>
                        </div>
                        <span className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${status.className}`}>
                          {status.label}
                        </span>
                      </div>
                      <p className="mt-3 text-xs leading-5 text-slate-500">{skill.availability_reason}</p>
                      <div className="mt-4 flex flex-wrap gap-2 border-t border-white/10 pt-3 text-[11px] text-slate-500">
                        <span>{skill.source_license}</span>
                        <span>摘要 {skill.digest.slice(0, 12)}</span>
                        {skill.adapted ? <span>已原生适配</span> : null}
                        <span className="ml-auto">{skill.inject_runtime ? "运行时可注入" : "运行时不注入"}</span>
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.04] px-6 py-12 text-center text-sm text-slate-400">
              {isLoadingBuiltin ? "正在加载 16 个内置 Skill…" : "内置 Skill 清单不可用。"}
            </div>
          )
        ) : activeTab === "market" && filteredProjects.length > 0 ? (
          <div id="skill-market-results">
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {visibleProjects.map((project) => (
                <MarketSkillCard
                  gateMode={trustIndex?.gateMode ?? "enforce"}
                  installed={isProjectInstalled(project, installedSkills)}
                  installingId={installingId}
                  key={project.id}
                  onInstall={(item) => void installSkill(item)}
                  onInspectTrust={(title, receiptId) =>
                    void openTrustAction({ kind: "inspect", title, receiptId })
                  }
                  onOpenSkillSet={openSkillSet}
                  project={project}
                  trustSummary={trustSummaryForCandidate(
                    trustIndex,
                    projectTrustCandidateId(project.id),
                  )}
                />
              ))}
            </div>
            {visibleProjects.length < filteredProjects.length ? (
              <div className="mt-6 flex justify-center">
                <button
                  className="rounded-full border border-white/15 px-5 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-brand-300/35 hover:bg-brand-300/10 hover:text-white"
                  onClick={() =>
                    setVisibleProjectCount((current) => current + MARKET_PAGE_SIZE)
                  }
                  type="button"
                >
                  加载更多（剩余 {filteredProjects.length - visibleProjects.length} 项）
                </button>
              </div>
            ) : null}
          </div>
        ) : activeTab === "market" ? (
          <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.04] px-6 py-12 text-center">
            <p className="text-lg font-semibold text-white">没有匹配的技能资源。</p>
            <p className="mt-2 text-sm text-slate-400">
              换一个关键词、分类或资源类型，或者清除当前筛选。
            </p>
            <button
              className="mt-5 rounded-full border border-brand-300/30 px-5 py-2 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/10"
              onClick={resetMarketFilters}
              type="button"
            >
              清除筛选
            </button>
          </div>
        ) : activeTab === "imports" ? (
          <Suspense
            fallback={
              <div aria-label="正在加载本地导入" className="space-y-3">
                <div className="h-20 animate-pulse rounded-lg bg-white/[0.05] motion-reduce:animate-none" />
                <div className="h-24 animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
              </div>
            }
          >
            <SkillLocalImportSummaryPanel />
          </Suspense>
        ) : activeTab === "installed" ? (
          <div className="space-y-5">
            <Suspense
              fallback={
                <div className="h-36 animate-pulse rounded-lg bg-white/[0.05]" />
              }
            >
              <SkillLifecyclePanel
                focusSkillId={lifecycleFocusSkillId}
                onChanged={() => void loadInstalledSkills()}
              />
            </Suspense>
            {installedSkills.length > 0 ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {installedSkills.map((skill) => (
                  <InstalledSkillCard
                    key={skill.skill_id}
                    onAcknowledge={(item) => {
                      if (!item.trust_receipt_id) {
                        setError("该 Skill 没有可确认的信任凭据。");
                        return;
                      }
                      void openTrustAction({
                        kind: "acknowledge",
                        title: item.name,
                        receiptId: item.trust_receipt_id,
                        skill: item,
                      });
                    }}
                    onInspectTrust={(title, receiptId) =>
                      void openTrustAction({ kind: "inspect", title, receiptId })
                    }
                    onManageVersions={manageSkillVersions}
                    onRevokeAcknowledgement={(item) =>
                      void revokeTrustAcknowledgement(item)
                    }
                    onUninstall={(item) => void uninstallSkill(item)}
                    skill={skill}
                    trustSummary={
                      trustIndex?.receipts.find(
                        (receipt) => receipt.receiptId === skill.trust_receipt_id,
                      ) ?? null
                    }
                    uninstallingId={uninstallingId}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.04] px-6 py-10 text-center">
                <p className="text-base font-semibold text-white">当前没有安装中的 Skill</p>
                <p className="mt-2 text-sm text-slate-400">上方仍会保留可恢复的历史版本。也可以返回市场安装新 Skill。</p>
                <button className="mt-5 min-h-11 rounded-full bg-hire-300 px-5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200" onClick={() => setActiveTab("market")} type="button">前往 Skill 市场</button>
              </div>
            )}
          </div>
        ) : activeTab === "drafts" ? (
          <SkillDraftPanel onInstalled={() => void loadInstalledSkills()} />
        ) : activeTab === "proposals" ? (
          <AuthoringProposalPanel
            kindPrefix="skill"
            title="Skill 自编写提案"
          />
        ) : (
          <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.04] px-6 py-12 text-center">
            <p className="text-lg font-semibold text-white">
              还没有安装技能，去技能市场看看吧。
            </p>
            <p className="mt-2 text-sm text-slate-400">
              先安装一个 PDF、XLSX 或 TypeScript 技能，再到面试间选择使用。
            </p>
            <button
              className="mt-5 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
              onClick={() => setActiveTab("market")}
              type="button"
            >
              去技能市场
            </button>
          </div>
        )}
      </section>
    </PageContainer>
  );
}
