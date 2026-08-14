import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CheckCircle2,
  ExternalLink,
  FileText,
  Layers3,
  PackageCheck,
  Search,
  ShieldAlert,
  UsersRound,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import ModelWorkbenchSidebar from "../components/ModelWorkbenchSidebar";
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
type SkillSetMemberStatusFilter = "all" | "allow" | "confirm" | "installed";
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

function builtinSkillSetProject(skillCount: number): SkillProject {
  return {
    id: "modelmirror-agent-builtin-skillset",
    name: "Agent 内置技能集",
    repoName: "ModelMirror 官方",
    repoUrl: "",
    category: "平台内置",
    kind: "skillset",
    description: "平台内置的通用技能集合，仅用于查看运行前能力与审计状态。",
    readmeSummary: "",
    stars: 0,
    language: "",
    updatedAt: "",
    installCommand: "",
    installNote: "只读集合，不支持从技能市场安装，也不会在此修改运行时配置。",
    installStatus: "reference",
    installMode: "none",
    sourceDescription: "ModelMirror Agent Runtime",
    tags: ["只读", "仅审计", `${skillCount || 16} 个技能`],
    verification: {
      status: "reference",
      sourceUrl: "",
      declaredKind: "skillset",
      verifiedKind: "skillset",
      installMode: "none",
      reasonCode: "no-install-source",
      reason: "平台内置只读集合，不提供市场安装入口。",
    },
  };
}

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

export function MarketSkillCard({
  gateMode,
  installingId,
  installed,
  onInstall,
  onInspectTrust,
  onOpenSkillSet,
  project,
  readOnly,
  trustSummary,
}: {
  gateMode: SkillTrustGateMode;
  installingId: string;
  installed: boolean;
  onInstall: (project: SkillProject) => void;
  onInspectTrust: (title: string, receiptId: string) => void;
  onOpenSkillSet: (project: SkillProject) => void;
  project: SkillProject;
  readOnly?: { notice: string; statusLabel: string };
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
  const isSkillSet = project.kind === "skillset";
  const visibleTags = project.tags.slice(0, 3);
  const hiddenTagCount = Math.max(0, project.tags.length - visibleTags.length);
  const visibleIncludedSkills = project.includedSkills?.slice(0, 3) ?? [];
  const hiddenIncludedSkillCount = Math.max(
    0,
    (project.includedSkills?.length ?? 0) - visibleIncludedSkills.length,
  );
  const ProjectIcon = isSkillSet ? Layers3 : FileText;
  const cardTone = isSkillSet
    ? {
        article: "border-accent-300/30 hover:border-accent-200/50",
        header: "bg-accent-300/[0.075]",
        icon: "border-accent-200/45 bg-accent-300/10 text-accent-100",
        kind: "border-accent-300/35 bg-accent-300/10 text-accent-100",
        source: "border-accent-300/15 bg-accent-300/[0.045]",
      }
    : {
        article: "border-brand-300/30 hover:border-brand-200/50",
        header: "bg-brand-300/[0.075]",
        icon: "border-brand-200/45 bg-brand-300/10 text-brand-100",
        kind: "border-brand-300/35 bg-brand-300/10 text-brand-100",
        source: "border-brand-300/15 bg-brand-300/[0.045]",
      };

  return (
    <article
      className={`group relative isolate flex h-full flex-col overflow-hidden rounded-xl border bg-ink-950/90 shadow-[0_16px_38px_rgba(2,8,23,0.22)] transition duration-200 hover:-translate-y-0.5 hover:bg-ink-950 ${cardTone.article}`}
      data-skill-kind={project.kind}
    >
      <div
        className={`flex min-h-[9.5rem] items-start justify-between gap-4 border-b border-white/10 p-5 ${cardTone.header}`}
        data-testid="skill-card-header"
      >
        <div className="flex min-w-0 items-start gap-4">
          <span
            aria-label={isSkillSet ? "SkillSet 技能包" : "单项 Skill"}
            className={`grid h-16 w-16 shrink-0 place-items-center rounded-xl border shadow-[inset_0_1px_0_rgba(255,255,255,0.08)] ${cardTone.icon}`}
          >
            <ProjectIcon aria-hidden="true" size={30} strokeWidth={1.65} />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold tracking-wide text-hire-200">
              {project.category}
            </p>
            <h3 className="mt-1.5 text-[1.375rem] font-semibold leading-tight text-white text-balance">
              {project.name}
            </h3>
            <p className="mt-2 truncate text-sm text-slate-400">
            {project.repoName}
            {project.catalogName && project.catalogName !== project.repoName
              ? ` · ${project.catalogName}`
              : ""}
              {project.stars > 0 ? ` · ★ ${formatStars(project.stars)}` : ""}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <span
            className={`rounded-full border px-3.5 py-1.5 text-xs font-semibold ${cardTone.kind}`}
          >
            {formatProjectKind(project)}
          </span>
          <span
            className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${installStatus.className}`}
          >
            {readOnly?.statusLabel ?? installStatus.label}
          </span>
        </div>
      </div>

      <div className="flex flex-1 flex-col p-5">
        <p className="line-clamp-2 min-h-12 text-sm leading-6 text-slate-200">
          {project.description}
        </p>

        <div className="mt-4 flex min-h-7 flex-wrap gap-1.5" data-testid="skill-card-tags">
          {visibleTags.map((tag) => (
            <span
              className="rounded-full border border-white/10 bg-white/[0.06] px-2.5 py-1 text-xs text-slate-200"
              key={tag}
            >
              {tag}
            </span>
          ))}
          {hiddenTagCount > 0 ? (
            <span className="rounded-full border border-white/10 px-2.5 py-1 text-xs text-slate-400">
              +{hiddenTagCount}
            </span>
          ) : null}
        </div>

        {!readOnly ? (
          <div
            className={`mt-5 border-t border-white/10 pt-4 ${hasIncludedSkills ? "pb-1" : ""}`}
            data-testid="skill-card-source"
          >
            <p className="text-sm font-semibold text-slate-200">
              {project.skillSet?.mode === "members"
                ? "成员集合来源"
                : hasIncludedSkills
                  ? "技能包内容"
                  : project.kind === "skillset"
                    ? "技能包来源"
                    : "安装来源"}
            </p>
            {hasIncludedSkills ? (
              <div className="mt-2.5 flex flex-wrap gap-1.5" data-testid="skillset-members">
                {visibleIncludedSkills.map((skill) => (
                  <span
                    className="rounded-lg border border-accent-300/20 bg-accent-300/[0.08] px-2.5 py-1 text-xs text-accent-100"
                    key={skill}
                  >
                    {skill}
                  </span>
                ))}
                {hiddenIncludedSkillCount > 0 ? (
                  <span className="rounded-lg border border-white/10 px-2.5 py-1 text-xs text-slate-400">
                    +{hiddenIncludedSkillCount}
                  </span>
                ) : null}
              </div>
            ) : null}
            <div className={`mt-2.5 rounded-lg border px-3 py-2.5 text-xs leading-5 text-slate-300 ${cardTone.source}`}>
              {project.skillSet?.mode === "members" ? (
                <div>
                  <code className="break-all text-sm text-hire-100">
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
                  <code className="break-all text-sm text-hire-100">
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
        ) : null}

        {canInstall ? (
          <SkillTrustSummaryLine
            gateMode={gateMode}
            onInspect={() =>
              trustSummary && onInspectTrust(project.name, trustSummary.receiptId)
            }
            summary={trustSummary}
          />
        ) : null}

        <div
          className="mt-auto flex flex-col gap-3 border-t border-white/10 pt-4 sm:flex-row sm:items-center sm:justify-between"
          data-testid="skill-card-actions"
        >
          {readOnly ? (
            <p className="text-sm leading-6 text-slate-400">{readOnly.notice}</p>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <a
                className="inline-flex min-h-10 items-center gap-2 text-sm font-semibold text-slate-300 underline decoration-white/20 underline-offset-4 transition hover:text-white"
                href={project.repoUrl}
                rel="noreferrer"
                target="_blank"
              >
                <ExternalLink aria-hidden="true" size={15} />
                查看来源
              </a>
              {project.catalogUrl && project.catalogUrl !== project.repoUrl ? (
                <a
                  className="inline-flex min-h-10 items-center text-xs font-semibold text-slate-500 underline decoration-white/15 underline-offset-4 transition hover:text-white"
                  href={project.catalogUrl}
                  rel="noreferrer"
                  target="_blank"
                >
                  查看索引
                </a>
              ) : null}
            </div>
          )}
          {readOnly ? (
            <span className="inline-flex min-h-11 items-center rounded-lg border border-white/15 bg-white/[0.04] px-5 py-2.5 text-sm font-semibold text-slate-300">
              不可安装
            </span>
          ) : canInstall ? (
            <button
              className="min-h-11 rounded-lg bg-hire-300 px-5 py-2.5 text-sm font-bold text-ink-950 shadow-[0_8px_20px_rgba(251,146,60,0.14)] transition hover:bg-hire-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500 disabled:shadow-none"
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
              className="min-h-11 rounded-lg border border-accent-300/30 bg-accent-300/10 px-5 py-2.5 text-sm font-bold text-accent-100 transition hover:bg-accent-300/20 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-200"
              onClick={() => onOpenSkillSet(project)}
              type="button"
            >
              查看成员
            </button>
          ) : (
            <a
              className="inline-flex min-h-11 items-center rounded-lg border border-white/15 px-5 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-brand-300/35 hover:bg-brand-300/10 hover:text-white"
              href={project.repoUrl}
              rel="noreferrer"
              target="_blank"
            >
              {installStatus.action}
            </a>
          )}
        </div>
      </div>
    </article>
  );
}

export function BuiltinSkillSetAuditCard({
  project,
  skills,
}: {
  project: SkillProject;
  skills: BuiltinSkill[];
}) {
  const visibleSkills = skills.slice(0, 6);

  return (
    <article
      className="group flex min-h-48 flex-col rounded-xl border border-cyan-300/20 bg-[linear-gradient(135deg,rgba(34,211,238,0.08),rgba(15,23,42,0.92)_44%)] p-5 transition duration-200 hover:border-cyan-200/35 hover:bg-surface-900"
      data-testid="builtin-skillset-audit-card"
    >
      <div className="flex items-start gap-4">
        <span className="grid h-12 w-12 shrink-0 place-items-center rounded-lg border border-cyan-200/30 bg-cyan-300/10 text-cyan-100">
          <Layers3 aria-hidden="true" size={24} strokeWidth={1.7} />
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 text-xs font-semibold text-cyan-100">
              仅审计
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.045] px-2.5 py-1 text-xs font-semibold text-slate-300">
              可查看
            </span>
          </div>
          <h3 className="mt-3 text-lg font-semibold leading-tight text-white">
            {project.name}
          </h3>
        </div>
      </div>
      <p className="mt-4 line-clamp-2 max-w-[62ch] text-sm leading-6 text-slate-300">
        {project.description}
      </p>
      <div className="mt-4 border-t border-cyan-100/10 pt-4">
        <p className="text-xs font-semibold text-cyan-100">内置技能</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {visibleSkills.length > 0 ? (
            visibleSkills.map((skill) => (
              <span
                className="rounded-md border border-white/10 bg-ink-950/35 px-2.5 py-1 text-xs text-slate-200"
                key={skill.skill_id}
                title={skill.description}
              >
                {skill.name}
              </span>
            ))
          ) : (
            <span className="text-xs leading-5 text-slate-400">
              正在读取运行时公开的技能名称与用途。
            </span>
          )}
          {skills.length > visibleSkills.length ? (
            <span className="rounded-md border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-xs text-cyan-100">
              +{skills.length - visibleSkills.length}
            </span>
          ) : null}
        </div>
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

export function SkillSetMemberPanel({
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
  const onCloseRef = useRef(onClose);
  const batchInstallingRef = useRef(false);
  const [members, setMembers] = useState<SkillSetMemberSource[] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [memberQuery, setMemberQuery] = useState("");
  const [memberStatusFilter, setMemberStatusFilter] =
    useState<SkillSetMemberStatusFilter>("all");
  const [memberPage, setMemberPage] = useState(1);

  useEffect(() => {
    let isCurrent = true;
    setMembers(null);
    setLoadError("");
    setMemberQuery("");
    setMemberStatusFilter("all");
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

    return () => {
      isCurrent = false;
    };
  }, [focusedMemberId, project.id]);

  const memberStates = useMemo(() => {
    const states = new Map<
      string,
      {
        installed: boolean;
        policy: "allow" | "confirm" | "block";
        status: Exclude<SkillSetMemberStatusFilter, "all"> | "blocked";
        trustSummary: SkillTrustReceiptSummary | null;
      }
    >();
    for (const member of members ?? []) {
      const installed = isSourceInstalled(member, installedSkills);
      const trustSummary =
        trustSummaryForCandidate(trustIndex, memberTrustCandidateId(member.id)) ??
        trustSummaryForSource(trustIndex, member);
      const policy = effectiveTrustInstallPolicy(
        trustIndex?.gateMode ?? "enforce",
        trustSummary,
      );
      states.set(member.id, {
        installed,
        policy,
        status: installed
          ? "installed"
          : policy === "allow" || policy === "confirm"
            ? policy
            : "blocked",
        trustSummary,
      });
    }
    return states;
  }, [installedSkills, members, trustIndex]);

  const filteredMembers = useMemo(() => {
    const query = memberQuery.trim().toLocaleLowerCase("zh-CN");
    return (members ?? []).filter((member) => {
      const matchesQuery =
        !query ||
        `${member.name} ${member.subPath}`
          .toLocaleLowerCase("zh-CN")
          .includes(query);
      const matchesStatus =
        memberStatusFilter === "all" ||
        memberStates.get(member.id)?.status === memberStatusFilter;
      return matchesQuery && matchesStatus;
    });
  }, [memberQuery, memberStates, memberStatusFilter, members]);
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
    const counts = { allow: 0, confirm: 0, blocked: 0, installed: 0 };
    for (const state of memberStates.values()) counts[state.status] += 1;
    return counts;
  }, [memberStates]);
  const activeBatchProgress =
    batchProgress?.projectId === project.id ? batchProgress : null;
  const isBatchInstalling = Boolean(activeBatchProgress);
  onCloseRef.current = onClose;
  batchInstallingRef.current = isBatchInstalling;

  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.requestAnimationFrame(() => panelRef.current?.focus());

    function handleKeyDown(event: KeyboardEvent) {
      if (document.getElementById("skill-trust-panel")) return;
      if (event.key === "Escape" && !batchInstallingRef.current) {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = Array.from(
        panelRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, [project.id]);

  useEffect(() => {
    setMemberPage(1);
  }, [memberQuery, memberStatusFilter]);

  const content = (
    <div
      className="fixed inset-0 z-[90] flex items-end justify-center bg-ink-950/80 px-0 pt-8 backdrop-blur-[2px] sm:items-center sm:p-5"
      onMouseDown={(event) => {
        if (
          event.target === event.currentTarget &&
          !batchInstallingRef.current
        ) {
          onCloseRef.current();
        }
      }}
    >
    <section
      aria-labelledby="skillset-member-title"
      aria-modal="true"
      className="max-h-[calc(100dvh-1rem)] w-full max-w-6xl overflow-y-auto rounded-t-2xl border border-accent-300/20 bg-surface-900 shadow-[0_24px_80px_rgba(0,0,0,0.55)] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-200 sm:max-h-[min(820px,calc(100dvh-2.5rem))] sm:rounded-xl"
      id="skillset-member-panel"
      ref={panelRef}
      role="dialog"
      tabIndex={-1}
    >
      <div className="sticky top-0 z-10 flex flex-col gap-4 border-b border-white/10 bg-surface-900 px-4 py-5 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold text-accent-100">
            <UsersRound aria-hidden="true" size={16} />
            SkillSet 成员
          </div>
          <h2 className="mt-2 text-xl font-semibold text-white sm:text-2xl" id="skillset-member-title">
            {project.name}
          </h2>
          <p className="mt-1.5 max-w-3xl text-sm leading-6 text-slate-400">
            逐项选择集合内的独立 Skill；需确认的成员会在安装前显示固定版本与权限。
          </p>
        </div>
        <button
          className="min-h-10 shrink-0 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:border-white/30 hover:bg-white/[0.06] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
          disabled={isBatchInstalling}
          onClick={onClose}
          type="button"
        >
          收起成员
        </button>
      </div>

      <div className="grid grid-cols-2 border-b border-white/10 bg-white/[0.025] sm:grid-cols-4">
        {[
          ["成员", summary?.memberCount ?? 0, UsersRound],
          ["可直接安装", memberTrustCounts.allow, CheckCircle2],
          ["需确认", memberTrustCounts.confirm, ShieldAlert],
          ["已安装", memberTrustCounts.installed, PackageCheck],
        ].map(([label, value, Icon]) => {
          const MetricIcon = Icon as typeof UsersRound;
          return (
            <div className="flex min-h-20 items-center gap-3 border-b border-white/10 px-4 py-3 last:border-b-0 even:border-l sm:border-b-0 sm:border-l sm:first:border-l-0" key={label as string}>
              <MetricIcon aria-hidden="true" className="hidden shrink-0 text-slate-500 lg:block" size={17} />
              <div>
                <p className="text-xs text-slate-500">{label as string}</p>
                <p className="mt-1 text-lg font-semibold text-white">{value as number}</p>
              </div>
            </div>
          );
        })}
      </div>

      {members ? (
        <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <div>
            <p className="text-sm font-semibold text-white">批量安装可直接使用的成员</p>
            <p aria-live="polite" className="mt-1 text-xs leading-5 text-slate-300">
              {activeBatchProgress
                ? `正在安装 ${activeBatchProgress.completed} / ${activeBatchProgress.total}：${activeBatchProgress.currentMemberName}`
                : remainingMemberCount > 0
                  ? `${memberTrustCounts.allow} 个低风险成员可直接批量安装；${memberTrustCounts.confirm} 个需逐项确认，${memberTrustCounts.blocked} 个被阻断或缺少凭据。`
                  : "该集合的全部成员均已安装。"}
            </p>
          </div>
          <button
            className="min-h-10 shrink-0 rounded-full border border-hire-300/35 bg-hire-300/10 px-4 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.03] disabled:text-slate-500"
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

      <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-4 sm:px-6">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <label className="relative block min-w-0 flex-1" htmlFor="skillset-member-search">
            <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={17} />
          <input
              className="min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 py-2.5 pl-10 pr-3 text-sm text-white placeholder:text-slate-500 transition focus:border-accent-300/50 focus:outline-none focus:ring-2 focus:ring-accent-300/15"
            id="skillset-member-search"
            onChange={(event) => setMemberQuery(event.target.value)}
            placeholder="输入成员名称或仓库路径"
            type="search"
            value={memberQuery}
          />
        </label>
          <div aria-label="按安装状态筛选" className="flex flex-wrap gap-2">
            {([
              ["all", "全部", members?.length ?? 0],
              ["allow", "可直接安装", memberTrustCounts.allow],
              ["confirm", "需确认", memberTrustCounts.confirm],
              ["installed", "已安装", memberTrustCounts.installed],
            ] as const).map(([value, label, count]) => (
              <button
                aria-pressed={memberStatusFilter === value}
                className={`min-h-10 rounded-full border px-3 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-200 ${memberStatusFilter === value ? "border-accent-300/40 bg-accent-300/15 text-accent-100" : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-white/20 hover:text-white"}`}
                key={value}
                onClick={() => setMemberStatusFilter(value)}
                type="button"
              >
                {label} {count}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
          <span>固定版本 {summary?.verifiedCommit.slice(0, 12) ?? "—"}</span>
          <a
            className="font-semibold text-slate-400 underline decoration-white/20 underline-offset-4 transition hover:text-white"
            href={project.repoUrl}
            rel="noreferrer"
            target="_blank"
          >
            查看来源仓库
          </a>
        </div>
      </div>

      {loadError ? (
        <div className="m-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50 sm:m-6">
          {loadError}
        </div>
      ) : members === null ? (
        <div aria-live="polite" className="space-y-2 p-4 sm:p-6">
          {[0, 1, 2].map((item) => (
            <div
              className="h-16 animate-pulse rounded-md bg-white/[0.055] motion-reduce:animate-none"
              key={item}
            />
          ))}
          <span className="sr-only">正在加载成员索引</span>
        </div>
      ) : filteredMembers.length === 0 ? (
        <div className="m-4 rounded-lg border border-dashed border-white/15 px-5 py-8 text-center sm:m-6">
          <p className="text-sm font-semibold text-white">没有匹配的集合成员</p>
          <p className="mt-2 text-xs text-slate-400">请尝试名称片段或目录路径。</p>
        </div>
      ) : (
        <>
          <div
            aria-live="polite"
            className="flex flex-wrap items-center justify-between gap-2 px-4 pt-4 text-xs text-slate-400 sm:px-6"
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
              const memberState = memberStates.get(member.id);
              const installed = memberState?.installed ?? false;
              const isInstalling = installingId === member.id;
              const trustSummary = memberState?.trustSummary ?? null;
              const effectivePolicy = memberState?.policy ?? "block";
              return (
                <li
                  className={`grid gap-3 px-4 py-4 sm:px-6 md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center ${
                    member.id === focusedMemberId
                      ? "bg-brand-300/10 ring-1 ring-inset ring-brand-300/25"
                      : "transition hover:bg-white/[0.025]"
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
                  </div>
                  <div className="flex flex-wrap items-center gap-2 md:justify-end">
                    <SkillTrustBadge summary={trustSummary} />
                    {trustSummary ? (
                      <button
                        className="min-h-9 rounded-full border border-white/10 px-3 text-xs font-semibold text-slate-300 transition hover:border-white/25 hover:bg-white/[0.05] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
                        onClick={() => onInspectTrust(member.name, trustSummary.receiptId)}
                        type="button"
                      >
                        查看凭据
                      </button>
                    ) : null}
                  </div>
                  <button
                    className="min-h-10 shrink-0 rounded-full bg-hire-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
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
            <div className="flex items-center justify-end gap-2 px-4 py-4 sm:px-6">
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
    </div>
  );

  return createPortal(content, document.body);
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
  const [needResultsOpen, setNeedResultsOpen] = useState(false);
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
  const needResultsCloseButtonRef = useRef<HTMLButtonElement>(null);
  const needResultsReturnFocusRef = useRef<HTMLElement | null>(null);
  const needSearchButtonRef = useRef<HTMLButtonElement>(null);
  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    skillProjects.forEach((project) => {
      counts.set(project.category, (counts.get(project.category) ?? 0) + 1);
    });
    return [...counts.entries()].sort(([left], [right]) =>
      left.localeCompare(right, "zh-CN"),
    );
  }, []);
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
  const builtinProject = useMemo(
    () => builtinSkillSetProject(builtinSkills.length),
    [builtinSkills.length],
  );
  const showBuiltinSkillSet = useMemo(() => {
    if (selectedCategory !== "all") return false;
    if (selectedKind === "skill") return false;
    if (
      selectedAvailability !== "all" &&
      selectedAvailability !== builtinProject.installStatus
    ) {
      return false;
    }

    const normalizedQuery = searchQuery.trim().toLocaleLowerCase("zh-CN");
    if (!normalizedQuery) return true;
    return [
      builtinProject.name,
      builtinProject.description,
      builtinProject.category,
      builtinProject.repoName,
      ...builtinProject.tags,
    ]
      .join(" ")
      .toLocaleLowerCase("zh-CN")
      .includes(normalizedQuery);
  }, [
    builtinProject,
    searchQuery,
    selectedAvailability,
    selectedCategory,
    selectedKind,
  ]);
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

  function closeNeedResults() {
    setNeedResultsOpen(false);
    needResultsReturnFocusRef.current?.focus();
  }

  useEffect(() => {
    document.title = "模镜 - Skill 技能货架";
    void refreshSkillResources();
  }, []);

  useEffect(() => {
    setVisibleProjectCount(MARKET_PAGE_SIZE);
  }, [searchQuery, selectedAvailability, selectedCategory, selectedKind]);

  useEffect(() => {
    if (!needResultsOpen) return;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusFrame = window.requestAnimationFrame(() =>
      needResultsCloseButtonRef.current?.focus(),
    );
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeNeedResults();
      }
    };
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [needResultsOpen]);

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
      closeNeedResults();
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
    setNeedResultsOpen(true);
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
    closeNeedResults();
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
      maxWidthClassName="max-w-[1500px]"
      mobileSidebar={<ModelWorkbenchSidebar compact />}
      showSystemCapabilityBar={false}
      sidebar={<ModelWorkbenchSidebar />}
      sidebarGridClassName="xl:grid-cols-[230px_minmax(0,1fr)] xl:gap-x-[54px]"
    >
      <header className="border-b border-white/10 pb-6 pt-2 sm:pt-4">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <h1 className="text-3xl font-semibold tracking-[-0.025em] text-white sm:text-4xl">
              Skill 技能货架
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300 sm:text-base">
              查找、安装并管理可复用的 AI 技能与 SkillSet。
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4 lg:shrink-0">
            {[
              [skillProjects.length, "个 Skill", "text-hire-100"],
              [installableProjects.length, "可安装", "text-emerald-100"],
              [categories.length, "个分类", "text-brand-100"],
              [skillsetCount, "个 SkillSet", "text-cyan-100"],
            ].map(([value, label, tone]) => (
              <div className="min-w-[84px]" key={label}>
                <dd className={`text-xl font-semibold tabular-nums ${tone}`}>{value}</dd>
                <dt className="mt-0.5 text-xs text-slate-400">{label}</dt>
              </div>
            ))}
          </dl>
        </div>
      </header>

      <section className="mt-6">
        <div className="mb-4 grid gap-3 rounded-xl border border-white/10 bg-white/[0.035] p-2.5 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
          <div
            aria-label="技能工作区"
            className="flex min-w-0 flex-wrap gap-1"
            role="group"
          >
            {[
              { id: "market", label: "技能市场" },
              { id: "installed", label: "已安装" },
              { id: "imports", label: "本地导入" },
              { id: "drafts", label: "工作区草稿" },
              { id: "proposals", label: "待审提案" },
            ].map((tab) => (
              <button
                aria-pressed={activeTab === tab.id}
                className={`min-h-10 rounded-lg px-3.5 py-2 text-sm font-semibold transition ${
                  activeTab === tab.id
                    ? "bg-hire-300 text-ink-950"
                    : "text-slate-300 hover:bg-white/[0.07] hover:text-white"
                }`}
                key={tab.id}
                onClick={() => setActiveTab(tab.id as SkillTab)}
                type="button"
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5 xl:justify-end xl:border-l xl:border-white/10 xl:pl-3">
            <Link
              className="inline-flex min-h-10 w-fit items-center rounded-lg border border-white/10 px-3 text-sm font-semibold text-slate-200 transition hover:border-hire-300/30 hover:bg-hire-300/10 hover:text-hire-100"
              to="/skills/import"
            >
              导入 Skill
            </Link>
            <Link
              className="inline-flex min-h-10 w-fit items-center rounded-lg border border-white/10 px-3 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/25 hover:bg-cyan-300/10 hover:text-cyan-100"
              to="/skills/rerank"
            >
              重排治理
            </Link>
            {creatorStatus?.enabled ? (
              <Link
                className="inline-flex min-h-10 w-fit items-center rounded-lg bg-hire-300 px-3 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:ring-2 focus-visible:ring-hire-100"
                to="/skills/create"
              >
                创建 Skill
              </Link>
            ) : null}
            <button
              className="min-h-10 w-fit rounded-lg border border-white/10 px-3 text-sm font-semibold text-slate-300 transition hover:border-hire-300/30 hover:bg-hire-300/10 hover:text-hire-100 disabled:opacity-50"
              disabled={isLoadingInstalled || isLoadingBuiltin || trustIndexStatus === "loading"}
              onClick={() => void refreshSkillResources()}
              type="button"
            >
              {isLoadingInstalled || isLoadingBuiltin || trustIndexStatus === "loading" ? "刷新中..." : "刷新"}
            </button>
          </div>
        </div>

        {activeTab === "market" ? (
          <section className="mb-4 overflow-hidden rounded-xl border border-brand-300/25 bg-surface-900/80">
            <div className="flex flex-col gap-4 border-b border-white/10 bg-brand-300/[0.055] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-brand-200/30 bg-brand-300/10 text-brand-100">
                  <Search aria-hidden="true" size={19} />
                </span>
                <div>
                  <h2 className="text-base font-semibold text-white">按任务寻找 Skill</h2>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    本地匹配目录，不会自动安装；结果在独立浮层中查看。
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-3 rounded-lg border border-white/10 bg-ink-950/35 px-3 py-2">
                <div className="text-right">
                  <p className="text-xs font-semibold text-slate-100">语义重排</p>
                  <p className="text-[11px] text-slate-500">
                    {semanticRerankEnabled ? "已开启" : "默认关闭"}
                  </p>
                </div>
                <button
                  aria-checked={semanticRerankEnabled}
                  aria-label="语义重排"
                  className={`relative h-6 w-11 shrink-0 rounded-full border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200 ${
                    semanticRerankEnabled
                      ? "border-cyan-200/50 bg-cyan-300/70"
                      : "border-white/20 bg-white/10 hover:bg-white/15"
                  }`}
                  onClick={() => {
                    if (semanticRerankEnabled) {
                      setSemanticRerankEnabled(false);
                      setSemanticConsentOpen(false);
                      closeNeedResults();
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
                  role="switch"
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={`absolute left-0 top-0.5 h-[18px] w-[18px] rounded-full bg-white transition-transform ${
                      semanticRerankEnabled ? "translate-x-[22px]" : "translate-x-0.5"
                    }`}
                  />
                </button>
              </div>
            </div>

            <form
              className="p-4"
              onSubmit={(event) => {
                event.preventDefault();
                needResultsReturnFocusRef.current = needSearchButtonRef.current;
                void submitNeedSearch(needQuery);
              }}
            >
              <label className="sr-only" htmlFor="skill-need-query">
                描述你要完成的事
              </label>
              <textarea
                className="min-h-16 w-full resize-y rounded-lg border border-white/10 bg-ink-950/85 px-4 py-3 text-sm leading-6 text-white placeholder:text-slate-400 transition focus:border-brand-300/50 focus:outline-none"
                id="skill-need-query"
                maxLength={500}
                onChange={(event) => setNeedQuery(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                    event.preventDefault();
                    needResultsReturnFocusRef.current = event.currentTarget;
                    void submitNeedSearch(needQuery);
                  }
                }}
                placeholder="描述任务、输入和期望结果，例如：为 React 网页编写 Playwright 自动化测试"
                value={needQuery}
              />

              <div className="mt-3 flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="text-xs font-semibold text-slate-500">试一试</span>
                  {NEED_EXAMPLES.map((example) => (
                    <button
                      className="max-w-full truncate rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 text-xs text-slate-300 transition hover:border-brand-300/30 hover:bg-brand-300/10 hover:text-white"
                      key={example}
                      onClick={(event) => {
                        needResultsReturnFocusRef.current = event.currentTarget;
                        void submitNeedSearch(example);
                      }}
                      title={example}
                      type="button"
                    >
                      {example}
                    </button>
                  ))}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  {submittedNeed ? (
                    <>
                      <button
                        className="min-h-10 rounded-lg border border-white/10 px-3 text-xs font-semibold text-slate-300 transition hover:bg-white/[0.06] hover:text-white"
                        onClick={() => setNeedResultsOpen(true)}
                        type="button"
                      >
                        查看上次结果
                      </button>
                      <button
                        className="min-h-10 rounded-lg border border-white/10 px-3 text-xs font-semibold text-slate-400 transition hover:bg-white/[0.06] hover:text-white"
                        onClick={() => {
                          setNeedQuery("");
                          setSubmittedNeed("");
                          closeNeedResults();
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
                        清除
                      </button>
                    </>
                  ) : null}
                  <span className="hidden text-xs text-slate-500 sm:inline">
                    Ctrl / ⌘ + Enter
                  </span>
                  <button
                    className="min-h-10 rounded-lg bg-brand-200 px-4 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                    disabled={!needQuery.trim() || needSearchStatus === "loading"}
                    ref={needSearchButtonRef}
                    type="submit"
                  >
                    {needSearchStatus === "loading" ? "正在查找..." : "寻找合适的 Skill"}
                  </button>
                </div>
              </div>
            </form>

            {semanticConsentOpen && !semanticRerankEnabled ? (
              <div className="border-t border-cyan-300/15 bg-cyan-300/[0.075] px-5 py-4 text-xs leading-5 text-cyan-50">
                <p className="max-w-4xl">
                  开启后会向当前配置的重排服务发送需求文本，以及最多 24 个公共目录候选的名称、标签和能力摘要。
                  本地导入、Creator、插件、Skill 正文、信任详情和安装记录不会外发。
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    className="min-h-10 rounded-lg bg-cyan-200 px-3 font-semibold text-ink-950 transition hover:bg-white"
                    onClick={() => {
                      setSemanticRerankEnabled(true);
                      setSemanticConsentOpen(false);
                    }}
                    type="button"
                  >
                    确认启用语义重排
                  </button>
                  <button
                    className="min-h-10 rounded-lg border border-white/15 px-3 font-semibold text-slate-200 transition hover:bg-white/[0.06]"
                    onClick={() => setSemanticConsentOpen(false)}
                    type="button"
                  >
                    保持本地排序
                  </button>
                </div>
              </div>
            ) : null}

            {needResultsOpen && submittedNeed
              ? createPortal(
                  <div
                    className="fixed inset-0 z-[95] flex items-end justify-center bg-ink-950/80 pt-8 backdrop-blur-[2px] sm:items-center sm:p-5"
                    onMouseDown={(event) => {
                      if (event.target === event.currentTarget) {
                        closeNeedResults();
                      }
                    }}
                  >
                    <section
                      aria-labelledby="skill-need-results-title"
                      aria-modal="true"
                      className="flex max-h-[min(88vh,880px)] w-full max-w-6xl flex-col overflow-hidden rounded-t-xl border border-brand-300/25 bg-surface-900 shadow-[0_30px_90px_rgba(2,8,23,0.7)] sm:rounded-xl"
                      role="dialog"
                    >
                      <header className="flex items-start justify-between gap-4 border-b border-white/10 bg-brand-300/[0.055] px-5 py-4 sm:px-6">
                        <div>
                          <h2
                            className="text-lg font-semibold text-white"
                            id="skill-need-results-title"
                          >
                            Skill 推荐结果
                          </h2>
                          <p className="mt-1 line-clamp-1 text-xs text-slate-400">
                            {submittedNeed}
                          </p>
                        </div>
                        <button
                          aria-label="关闭推荐结果"
                          className="grid min-h-11 min-w-11 shrink-0 place-items-center rounded-lg border border-white/10 text-slate-300 transition hover:bg-white/[0.08] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-200"
                          onClick={closeNeedResults}
                          ref={needResultsCloseButtonRef}
                          type="button"
                        >
                          <X aria-hidden="true" size={20} />
                        </button>
                      </header>
                      <div className="overflow-y-auto p-5 sm:p-6">
                        <div>
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
                      </div>
                    </section>
                  </div>,
                  document.body,
                )
              : null}
          </section>
        ) : null}

        {activeTab === "market" ? (
          <section className="mb-4 rounded-xl border border-white/10 bg-white/[0.035] p-3">
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[minmax(280px,1fr)_180px_180px_auto_auto] xl:items-center">
              <label className="block">
                <span className="sr-only">搜索技能</span>
                <input
                  className="min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white placeholder:text-slate-400 transition focus:border-brand-300/50 focus:outline-none"
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder="名称、能力、标签或仓库"
                  type="search"
                  value={searchQuery}
                />
              </label>

              <label className="block">
                <span className="sr-only">分类</span>
                <select
                  aria-label="分类"
                  className="min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white transition focus:border-brand-300/50 focus:outline-none"
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
                <span className="sr-only">安装状态</span>
                <select
                  aria-label="安装状态"
                  className="min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white transition focus:border-brand-300/50 focus:outline-none"
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

              <fieldset className="min-w-0">
                <legend className="sr-only">资源类型</legend>
                <div className="inline-flex min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 p-1 xl:w-auto">
                  {[
                    { id: "all", label: "全部" },
                    { id: "skill", label: "Skill" },
                    { id: "skillset", label: "SkillSet" },
                  ].map((kind) => (
                    <button
                      aria-pressed={selectedKind === kind.id}
                      className={`flex-1 rounded-md px-3 py-1.5 text-sm font-semibold transition xl:flex-none ${
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
              {hasActiveFilters ? (
                <button
                  className="min-h-11 rounded-lg border border-white/10 px-3 text-xs font-semibold text-brand-100 transition hover:bg-brand-300/10 hover:text-white"
                  onClick={resetMarketFilters}
                  type="button"
                >
                  清除筛选
                </button>
              ) : (
                <span aria-hidden="true" className="hidden xl:block" />
              )}
            </div>
          </section>
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
          <div className="fixed inset-0 z-[100] grid place-items-center bg-ink-950/75 p-5 backdrop-blur-[2px]">
            <div aria-live="polite" className="w-full max-w-sm rounded-xl border border-white/10 bg-surface-900 px-5 py-4 text-center text-sm text-slate-300 shadow-2xl">
              正在加载固定版本的信任凭据…
            </div>
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

        {activeTab === "market" &&
        (filteredProjects.length > 0 || showBuiltinSkillSet) ? (
          <div id="skill-market-results">
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {visibleProjects.slice(0, 5).map((project) => (
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
              {showBuiltinSkillSet ? (
                <BuiltinSkillSetAuditCard
                  key={builtinProject.id}
                  project={builtinProject}
                  skills={builtinSkills}
                />
              ) : null}
              {visibleProjects.slice(5).map((project) => (
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
