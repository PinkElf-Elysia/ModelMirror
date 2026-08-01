import { useEffect, useMemo, useState } from "react";
import PageContainer from "../components/PageContainer";
import AuthoringProposalPanel from "../components/authoring/AuthoringProposalPanel";
import SkillDraftPanel from "../components/authoring/SkillDraftPanel";
import { type SkillProject, skillProjects } from "../data/skillProjects";

interface InstalledSkill {
  skill_id: string;
  name: string;
  description: string;
  repo_url: string;
  sub_path: string;
  installed_at: number;
}

interface InstalledSkillsResponse {
  skills: InstalledSkill[];
}

type SkillTab = "market" | "installed" | "drafts" | "proposals";

function formatStars(stars: number) {
  return `${(stars / 1000).toFixed(1)}k`;
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
      skill.sub_path === project.installSource?.subPath,
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

  return (
    <article className="group relative overflow-hidden rounded-lg border border-white/10 bg-ink-950/70 p-5 shadow-prism transition duration-200 hover:-translate-y-1 hover:border-hire-300/35 hover:bg-white/[0.065]">
      <div className="absolute inset-x-5 top-0 h-px bg-gradient-to-r from-transparent via-hire-300/80 to-transparent opacity-0 transition group-hover:opacity-100" />
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold text-hire-200">{project.category}</p>
          <h3 className="mt-2 text-xl font-semibold text-white">{project.name}</h3>
          <p className="mt-1 text-xs text-slate-500">{project.repoName}</p>
        </div>
        <span className="rounded-full border border-brand-300/25 bg-brand-300/10 px-3 py-1 text-xs font-semibold text-brand-100">
          {formatStars(project.stars)}
        </span>
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
        <p className="text-xs font-semibold text-slate-400">安装来源</p>
        <code className="mt-2 block break-all rounded-md bg-ink-950/80 p-2 text-xs leading-5 text-hire-100">
          {project.installSource
            ? `${project.installSource.repoUrl} / ${project.installSource.subPath}`
            : "参考资料，无一键安装源"}
        </code>
      </div>

      <div className="mt-5 flex items-center justify-between gap-3">
        <a
          className="text-xs font-semibold text-slate-400 underline decoration-white/20 underline-offset-4 transition hover:text-white"
          href={project.repoUrl}
          rel="noreferrer"
          target="_blank"
        >
          查看仓库
        </a>
        <button
          className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 shadow-[0_0_22px_rgba(251,146,60,0.22)] transition hover:bg-hire-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500 disabled:shadow-none"
          disabled={!canInstall || installed || isInstalling}
          onClick={() => onInstall(project)}
          type="button"
        >
          {isInstalling ? "安装中..." : installed ? "已安装" : canInstall ? "⚡ 安装" : "仅作参考"}
        </button>
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
  const [installedSkills, setInstalledSkills] = useState<InstalledSkill[]>([]);
  const [isLoadingInstalled, setIsLoadingInstalled] = useState(false);
  const [installingId, setInstallingId] = useState("");
  const [uninstallingId, setUninstallingId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const totalStars = useMemo(
    () => skillProjects.reduce((sum, project) => sum + project.stars, 0),
    [],
  );
  const installableProjects = skillProjects.filter((project) => project.installSource);

  useEffect(() => {
    document.title = "模镜 - Skill 技能货架";
    void loadInstalledSkills();
  }, []);

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
        }),
      });
      if (!response.ok) throw new Error(await readApiError(response));
      const installed = (await response.json()) as InstalledSkill;
      setInstalledSkills((current) => [
        installed,
        ...current.filter((skill) => skill.skill_id !== installed.skill_id),
      ]);
      setNotice(`${project.name} 已安装，可在面试间选择使用。`);
    } catch (installError) {
      setError(
        installError instanceof Error ? installError.message : "技能安装失败",
      );
    } finally {
      setInstallingId("");
    }
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
            Skill 是 AI 打工人的岗位手册。安装后可在面试间激活，让模型按技能说明完成任务。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">可安装技能</p>
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
            <p className="text-xs text-slate-400">GitHub 热度</p>
            <p className="mt-1 text-sm font-semibold text-brand-100">
              {formatStars(totalStars)} stars
            </p>
          </div>
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
              从技能市场领取岗位手册，安装到本地后即可在面试间激活。模型会把 Skill 当成系统级工作规范来执行。
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
                  {installedSkills.length}
                </p>
                <p className="mt-1 truncate text-slate-400">已入职</p>
              </div>
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-brand-100">
                  {formatStars(totalStars)}
                </p>
                <p className="mt-1 truncate text-slate-400">热度</p>
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

        {activeTab === "market" ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {skillProjects.map((project) => (
              <MarketSkillCard
                installed={isProjectInstalled(project, installedSkills)}
                installingId={installingId}
                key={project.id}
                onInstall={(item) => void installSkill(item)}
                project={project}
              />
            ))}
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

