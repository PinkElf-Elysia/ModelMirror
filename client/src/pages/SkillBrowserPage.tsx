import { useEffect, useMemo, useState } from "react";
import PageContainer from "../components/PageContainer";
import AuthoringProposalPanel from "../components/authoring/AuthoringProposalPanel";
import SkillDraftPanel from "../components/authoring/SkillDraftPanel";
import {
  type SkillProject,
  type SkillProjectKind,
  skillProjects,
} from "../data/skillProjects";
import type { SkillInstallStatus } from "../data/skillCatalogPolicy";

interface InstalledSkill {
  skill_id: string;
  name: string;
  description: string;
  repo_url: string;
  sub_path: string;
  installed_at: number;
  source_ref?: string | null;
}

interface InstalledSkillsResponse {
  skills: InstalledSkill[];
}

type SkillTab = "market" | "installed" | "drafts" | "proposals";
type SkillKindFilter = "all" | SkillProjectKind;
type SkillAvailabilityFilter = "all" | SkillInstallStatus;
const MARKET_PAGE_SIZE = 48;

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
    const includedCount = project.includedSkills?.length ?? 0;
    return includedCount > 0 ? `SkillSet · ${includedCount} 项` : "SkillSet";
  }
  return "Skill";
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
  try {
    const data = (await response.json()) as { detail?: string; error?: string };
    return data.detail ?? data.error ?? `请求失败：${response.status}`;
  } catch {
    return `请求失败：${response.status}`;
  }
}

function isProjectInstalled(project: SkillProject, installedSkills: InstalledSkill[]) {
  if (!project.installSource) return false;
  return installedSkills.some(
    (skill) =>
      skill.repo_url === project.installSource?.repoUrl &&
      skill.sub_path === project.installSource?.subPath &&
      (!project.installSource.verifiedCommit ||
        skill.source_ref === project.installSource.verifiedCommit),
  );
}

function MarketSkillCard({
  installingId,
  installed,
  onInstall,
  project,
}: {
  installingId: string;
  installed: boolean;
  onInstall: (project: SkillProject) => void;
  project: SkillProject;
}) {
  const canInstall = Boolean(project.installSource);
  const isInstalling = installingId === project.id;
  const installLabel = project.kind === "skillset" ? "安装技能包" : "安装技能";
  const installStatus = INSTALL_STATUS_DETAILS[project.installStatus];
  const hasIncludedSkills =
    project.kind === "skillset" && (project.includedSkills?.length ?? 0) > 0;

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
          {hasIncludedSkills
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
          {project.installSource ? (
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
            disabled={installed || isInstalling}
            onClick={() => onInstall(project)}
            type="button"
          >
            {isInstalling ? "安装中..." : installed ? "已安装" : installLabel}
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

function InstalledSkillCard({
  onUninstall,
  skill,
  uninstallingId,
}: {
  onUninstall: (skill: InstalledSkill) => void;
  skill: InstalledSkill;
  uninstallingId: string;
}) {
  const isUninstalling = uninstallingId === skill.skill_id;

  return (
    <article className="rounded-lg border border-white/10 bg-white/[0.055] p-5 shadow-prism">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold text-emerald-100">已入职技能</p>
          <h3 className="mt-2 text-xl font-semibold text-white">{skill.name}</h3>
          <p className="mt-1 text-xs text-slate-500">
            {skill.repo_url} / {skill.sub_path || "."}
          </p>
        </div>
        <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1 text-xs font-semibold text-emerald-100">
          {formatInstallTime(skill.installed_at)}
        </span>
      </div>
      <p className="mt-4 text-sm leading-6 text-slate-300">{skill.description}</p>
      <div className="mt-5 flex justify-end">
        <button
          className="rounded-full border border-rose-300/30 bg-rose-300/10 px-4 py-2 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/20 disabled:cursor-not-allowed disabled:opacity-50"
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
  const requestedTab = new URLSearchParams(window.location.search).get("tab");
  const [activeTab, setActiveTab] = useState<SkillTab>(
    requestedTab === "drafts" || requestedTab === "proposals"
      ? requestedTab
      : "market",
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [selectedKind, setSelectedKind] = useState<SkillKindFilter>("all");
  const [selectedAvailability, setSelectedAvailability] =
    useState<SkillAvailabilityFilter>("all");
  const [visibleProjectCount, setVisibleProjectCount] = useState(MARKET_PAGE_SIZE);
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
  const [isLoadingInstalled, setIsLoadingInstalled] = useState(false);
  const [installingId, setInstallingId] = useState("");
  const [uninstallingId, setUninstallingId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
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
  const hasActiveFilters =
    searchQuery.trim().length > 0 ||
    selectedCategory !== "all" ||
    selectedKind !== "all" ||
    selectedAvailability !== "all";

  useEffect(() => {
    document.title = "模镜 - Skill 技能货架";
    void loadInstalledSkills();
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

  async function installSkill(project: SkillProject) {
    if (!project.installSource || installingId) return;

    setInstallingId(project.id);
    setError("");
    setNotice("");

    try {
      const response = await fetch("/api/skills/install", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_url: project.installSource.repoUrl,
          sub_path: project.installSource.subPath,
          ref: project.installSource.verifiedCommit,
        }),
      });
      if (!response.ok) throw new Error(await readApiError(response));
      const installed = (await response.json()) as InstalledSkill;
      setInstalledSkills((current) => [
        installed,
        ...current.filter((skill) => skill.skill_id !== installed.skill_id),
      ]);
      setNotice(
        `${project.name} ${project.kind === "skillset" ? "技能包" : "技能"}已安装，可在面试间选择使用。`,
      );
    } catch (installError) {
      setError(
        installError instanceof Error ? installError.message : "技能安装失败",
      );
    } finally {
      setInstallingId("");
    }
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

  return (
    <PageContainer
      activeResource="skills"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">技能培训服务台</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            Skill 是单项岗位手册，SkillSet 是带多个子技能的组合包。安装后可在面试间激活。
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
              按任务、分类和资源类型查找岗位手册。目录同时收录独立 Skill 与可一次安装的 SkillSet。
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
          <div className="inline-flex w-fit rounded-full border border-white/10 bg-white/[0.055] p-1">
            {[
              { id: "market", label: "技能市场" },
              { id: "installed", label: "已安装" },
              { id: "drafts", label: "工作区草稿" },
              { id: "proposals", label: "待审提案" },
            ].map((tab) => (
              <button
                className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
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
          <button
            className="w-fit rounded-full border border-white/10 px-4 py-2 text-sm font-semibold text-slate-300 transition hover:border-hire-300/30 hover:bg-hire-300/10 hover:text-hire-100 disabled:opacity-50"
            disabled={isLoadingInstalled}
            onClick={() => void loadInstalledSkills()}
            type="button"
          >
            {isLoadingInstalled ? "刷新中..." : "刷新已安装"}
          </button>
        </div>

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
                  <option value="ready">可一键安装（{installStatusCounts.ready}）</option>
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

        {activeTab === "market" && filteredProjects.length > 0 ? (
          <div>
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {visibleProjects.map((project) => (
                <MarketSkillCard
                  installed={isProjectInstalled(project, installedSkills)}
                  installingId={installingId}
                  key={project.id}
                  onInstall={(item) => void installSkill(item)}
                  project={project}
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
        ) : activeTab === "installed" && installedSkills.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {installedSkills.map((skill) => (
              <InstalledSkillCard
                key={skill.skill_id}
                onUninstall={(item) => void uninstallSkill(item)}
                skill={skill}
                uninstallingId={uninstallingId}
              />
            ))}
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

