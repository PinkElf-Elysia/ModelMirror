import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import WorkflowEditor from "../components/workflow/WorkflowEditor";
import XpertAppDeploymentPanel from "../components/xpert/XpertAppDeploymentPanel";
import AuthoringProposalPanel from "../components/authoring/AuthoringProposalPanel";
import XpertFeatureSettings from "../components/xpert/XpertFeatureSettings";
import {
  type XpertDefinition,
  type XpertFeatureConfig,
  type PromptProfileBinding,
  type XpertValidationResult,
} from "../types/xpert";
import { type WorkflowDefinition } from "../types/workflow";
import {
  getXpert,
  publishXpert,
  toWorkflowDefinition,
  toXpertDraftWorkflow,
  updateXpert,
  validateXpert,
} from "../utils/xpertApi";

function splitTags(value: string) {
  return value.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
}

function formatDate(value: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

interface PromptProfileOption {
  id: string;
  name: string;
  aliases: string[];
  status: string;
  published_version: number | null;
}

export default function XpertStudioPage() {
  const { xpertId = "" } = useParams();
  const [xpert, setXpert] = useState<XpertDefinition | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [starters, setStarters] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(4);
  const [recursionLimit, setRecursionLimit] = useState(1000);
  const [features, setFeatures] = useState<XpertFeatureConfig | null>(null);
  const [promptProfiles, setPromptProfiles] = useState<PromptProfileOption[]>([]);
  const [promptBindings, setPromptBindings] = useState<PromptProfileBinding[]>([]);
  const [releaseNotes, setReleaseNotes] = useState("");
  const [validation, setValidation] = useState<XpertValidationResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"metadata" | "validate" | "publish" | "archive" | "">("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getXpert(xpertId)
      .then((data) => {
        if (cancelled) return;
        setXpert(data);
        setName(data.name);
        setDescription(data.description);
        setTags(data.tags.join(", "));
        setStarters(data.starters.join("\n"));
        setMaxConcurrency(data.draft.agent_config?.max_concurrency ?? 4);
        setRecursionLimit(data.draft.agent_config?.recursion_limit ?? 1000);
        setFeatures(data.draft.features);
        setPromptBindings(data.draft.prompt_profiles ?? []);
        document.title = `模镜 - ${data.name} Studio`;
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Xpert 加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [xpertId]);

  useEffect(() => {
    void fetch("/api/prompt-profiles?status=published&limit=200")
      .then(async (response) => {
        const payload = await response.json() as { items?: PromptProfileOption[] };
        if (!response.ok) throw new Error("Prompt Profile 加载失败");
        setPromptProfiles(payload.items ?? []);
      })
      .catch(() => setPromptProfiles([]));
  }, []);

  async function saveMetadata() {
    if (!xpert) return;
    setBusy("metadata");
    setError("");
    try {
      const nextStarters = starters.split("\n").map((item) => item.trim()).filter(Boolean);
      const updated = await updateXpert(xpert.id, {
        name: name.trim(),
        description: description.trim(),
        tags: splitTags(tags),
        starters: nextStarters,
        draft: {
          ...xpert.draft,
          agent_config: {
            max_concurrency: maxConcurrency,
            recursion_limit: recursionLimit,
          },
          features: features
            ? {
                ...features,
                opening: {
                  ...features.opening,
                  questions: nextStarters,
                },
              }
            : xpert.draft.features,
          prompt_profiles: promptBindings,
        },
      });
      setXpert(updated);
      setNotice("基础信息已保存");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "基础信息保存失败");
    } finally {
      setBusy("");
    }
  }

  async function saveWorkflow(definition: WorkflowDefinition) {
    if (!xpert) return;
    const updated = await updateXpert(xpert.id, {
      draft: {
        ...xpert.draft,
        workflow: toXpertDraftWorkflow(definition),
      },
    });
    setXpert(updated);
    setValidation(null);
  }

  async function runValidation() {
    if (!xpert) return;
    setBusy("validate");
    setError("");
    try {
      const result = await validateXpert(xpert.id);
      setValidation(result);
      setNotice(result.valid ? "发布预检通过" : "发布预检发现问题");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "发布预检失败");
    } finally {
      setBusy("");
    }
  }

  async function publish() {
    if (!xpert) return;
    setBusy("publish");
    setError("");
    try {
      const result = await validateXpert(xpert.id);
      setValidation(result);
      if (!result.valid) {
        setNotice("请先修复预检问题");
        return;
      }
      const version = await publishXpert(xpert.id, releaseNotes);
      const refreshed = await getXpert(xpert.id);
      setXpert(refreshed);
      setReleaseNotes("");
      setNotice(`v${version.version} 已发布`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "发布失败");
    } finally {
      setBusy("");
    }
  }

  async function toggleArchive() {
    if (!xpert) return;
    setBusy("archive");
    setError("");
    try {
      const nextStatus = xpert.status === "archived"
        ? xpert.published_version ? "published" : "draft"
        : "archived";
      const updated = await updateXpert(xpert.id, { status: nextStatus });
      setXpert(updated);
      setNotice(nextStatus === "archived" ? "Xpert 已归档" : "Xpert 已恢复");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "状态更新失败");
    } finally {
      setBusy("");
    }
  }

  if (loading) {
    return (
      <PageContainer activeResource="agents" maxWidthClassName="max-w-[1840px]">
        <div className="h-[70vh] animate-pulse rounded-lg border border-white/10 bg-white/[0.04]" />
      </PageContainer>
    );
  }

  if (!xpert) {
    return (
      <PageContainer activeResource="agents">
        <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 p-5 text-sm text-rose-100">
          {error || "Xpert 不存在或已不可用。"}
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1840px]">
      <header className="mb-5 rounded-lg border border-white/10 bg-ink-950/72 p-4">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <Link className="font-semibold text-hire-100 hover:text-hire-50" to="/agents/studio">我的 Xpert</Link>
              <span className="text-slate-600">/</span>
              <span className="text-slate-400">{xpert.slug}</span>
              <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-slate-400">revision {xpert.draft_revision}</span>
              <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-2 py-0.5 font-semibold text-hire-100">
                {xpert.published_version ? `已发布 v${xpert.published_version}` : "未发布"}
              </span>
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(220px,0.7fr)_minmax(320px,1.3fr)]">
              <input className="h-11 rounded-lg border border-white/10 bg-white/[0.055] px-3 text-lg font-semibold text-white outline-none focus:border-hire-300/60" maxLength={120} onChange={(event) => setName(event.target.value)} value={name} />
              <input className="h-11 rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm text-slate-200 outline-none focus:border-hire-300/60" maxLength={2000} onChange={(event) => setDescription(event.target.value)} placeholder="Xpert 说明" value={description} />
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <input className="h-10 rounded-lg border border-white/10 bg-white/[0.04] px-3 text-xs text-slate-300 outline-none focus:border-hire-300/60" onChange={(event) => setTags(event.target.value)} placeholder="标签，以逗号分隔" value={tags} />
              <textarea className="min-h-10 resize-y rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-slate-300 outline-none focus:border-hire-300/60" onChange={(event) => setStarters(event.target.value)} placeholder="开场问题，每行一个" rows={1} value={starters} />
            </div>
            <div className="mt-3 grid gap-3 rounded-lg border border-white/10 bg-white/[0.025] p-3 lg:grid-cols-2">
              <label className="text-xs font-semibold text-slate-300">
                最大并发请求
                <input
                  className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white outline-none focus:border-hire-300/60"
                  max={100}
                  min={1}
                  onChange={(event) => setMaxConcurrency(Number(event.target.value))}
                  type="number"
                  value={maxConcurrency}
                />
                <span className="mt-1 block font-normal leading-5 text-slate-500">
                  作用于整个 Xpert 执行树中的模型、工具与子 Xpert。
                </span>
              </label>
              <label className="text-xs font-semibold text-slate-300">
                递归次数限制
                <input
                  className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white outline-none focus:border-hire-300/60"
                  max={10000}
                  min={100}
                  onChange={(event) => setRecursionLimit(Number(event.target.value))}
                  type="number"
                  value={recursionLimit}
                />
                <span className="mt-1 block font-normal leading-5 text-slate-500">
                  统计控制节点、模型决策、工具调用和外部专家调用。
                </span>
              </label>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <button className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-2 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35" disabled={Boolean(busy)} onClick={() => void saveMetadata()} type="button">
              {busy === "metadata" ? "保存中..." : "保存信息"}
            </button>
            <button className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-2 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35" disabled={Boolean(busy)} onClick={() => void runValidation()} type="button">
              {busy === "validate" ? "预检中..." : "发布预检"}
            </button>
            {xpert.published_version ? (
              <Link className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs font-semibold text-emerald-100 transition hover:bg-emerald-300/20" to={`/agents/xpert/${xpert.id}/chat`}>
                打开聊天
              </Link>
            ) : null}
            <button className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-slate-400 transition hover:border-rose-300/30 hover:text-rose-100" disabled={Boolean(busy)} onClick={() => void toggleArchive()} type="button">
              {xpert.status === "archived" ? "恢复" : "归档"}
            </button>
          </div>
        </div>
        {notice || error ? (
          <p className={`mt-3 rounded-lg border px-3 py-2 text-xs ${error ? "border-rose-300/25 bg-rose-300/10 text-rose-100" : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"}`}>
            {error || notice}
          </p>
        ) : null}
      </header>

      {features ? (
        <XpertFeatureSettings
          onChange={(next) => {
            setFeatures(next);
            setStarters(next.opening.questions.join("\n"));
          }}
          value={features}
        />
      ) : null}

      <section className="mb-5 rounded-lg border border-white/10 bg-white/[0.035] p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-white">Prompt Command</h2>
            <p className="mt-1 text-xs leading-5 text-slate-400">绑定已发布 Profile；发布 Xpert 时会将 latest 固定为具体版本。</p>
          </div>
          <Link className="text-xs font-semibold text-cyan-200 hover:text-cyan-100" to="/prompts">管理 Prompt Profile</Link>
        </div>
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          {promptProfiles.map((profile) => {
            const binding = promptBindings.find((item) => item.profile_id === profile.id);
            return (
              <div className={`rounded-md border p-3 ${binding?.enabled ? "border-cyan-300/35 bg-cyan-300/[0.08]" : "border-white/10 bg-slate-950/45"}`} key={profile.id}>
                <div className="flex items-start gap-3">
                  <input
                    checked={Boolean(binding?.enabled)}
                    className="mt-1"
                    onChange={(event) => {
                      setPromptBindings((current) => {
                        const rest = current.filter((item) => item.profile_id !== profile.id);
                        if (!event.target.checked) return rest;
                        return [...rest, {
                          profile_id: profile.id,
                          version_policy: "latest",
                          pinned_version: null,
                          enabled: true,
                        }];
                      });
                    }}
                    type="checkbox"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-xs font-semibold text-white">{profile.name}</p>
                      <span className="text-[10px] text-slate-500">v{profile.published_version ?? "-"}</span>
                    </div>
                    <p className="mt-1 truncate text-[11px] text-cyan-100">{profile.aliases.map((alias) => `/${alias}`).join(" · ")}</p>
                    {binding ? (
                      <div className="mt-2 flex gap-2">
                        <select
                          className="h-8 rounded-md border border-white/10 bg-slate-950 px-2 text-[11px] text-white"
                          onChange={(event) => setPromptBindings((current) => current.map((item) => item.profile_id === profile.id ? {
                            ...item,
                            version_policy: event.target.value as "latest" | "pinned",
                            pinned_version: event.target.value === "pinned" ? profile.published_version : null,
                          } : item))}
                          value={binding.version_policy}
                        >
                          <option value="latest">发布时固定 latest</option>
                          <option value="pinned">固定指定版本</option>
                        </select>
                        {binding.version_policy === "pinned" ? (
                          <input
                            className="h-8 w-24 rounded-md border border-white/10 bg-slate-950 px-2 text-[11px] text-white"
                            min={1}
                            onChange={(event) => setPromptBindings((current) => current.map((item) => item.profile_id === profile.id ? { ...item, pinned_version: Number(event.target.value) || null } : item))}
                            type="number"
                            value={binding.pinned_version ?? ""}
                          />
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
          {!promptProfiles.length ? <p className="rounded-md border border-dashed border-white/10 p-4 text-center text-xs text-slate-500">尚无已发布 Prompt Profile</p> : null}
        </div>
      </section>

      <section className="mb-5 grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-white">发布控制</h2>
              <p className="mt-1 text-xs text-slate-400">发布保存过的草稿 revision；版本快照发布后不可变。</p>
            </div>
            <button className="rounded-full bg-hire-300 px-4 py-2 text-xs font-semibold text-ink-950 transition hover:bg-hire-200 disabled:opacity-60" disabled={Boolean(busy) || xpert.status === "archived"} onClick={() => void publish()} type="button">
              {busy === "publish" ? "发布中..." : `发布 v${(xpert.published_version ?? 0) + 1}`}
            </button>
          </div>
          <textarea className="mt-3 min-h-20 w-full resize-y rounded-lg border border-white/10 bg-ink-950/65 px-3 py-2 text-xs leading-5 text-slate-200 outline-none focus:border-hire-300/60" maxLength={2000} onChange={(event) => setReleaseNotes(event.target.value)} placeholder="本次发布说明（可选）" value={releaseNotes} />
          {validation ? (
            <div className={`mt-3 rounded-lg border p-3 ${validation.valid ? "border-emerald-300/25 bg-emerald-300/10" : "border-rose-300/25 bg-rose-300/10"}`}>
              <p className={`text-xs font-semibold ${validation.valid ? "text-emerald-100" : "text-rose-100"}`}>
                {validation.valid ? `预检通过 · ${validation.node_count} 节点` : `预检未通过 · ${validation.issues.length} 项`}
              </p>
              {!validation.valid ? (
                <ul className="mt-2 max-h-36 space-y-1 overflow-y-auto text-xs text-rose-100/90">
                  {validation.issues.map((issue, index) => (
                    <li key={`${issue.code}-${issue.node_id ?? index}`}>{issue.node_id ? `${issue.node_id}: ` : ""}{issue.message}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </div>

        <aside className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-white">版本记录</h2>
            <span className="text-xs text-slate-500">{xpert.versions.length} 个版本</span>
          </div>
          {xpert.versions.length > 0 ? (
            <div className="mt-3 max-h-44 space-y-2 overflow-y-auto">
              {xpert.versions.map((version) => (
                <div className="rounded-lg border border-white/10 bg-ink-950/55 px-3 py-2" key={version.version}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-white">v{version.version}</span>
                    <span className="text-[10px] text-slate-500">revision {version.draft_revision}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-slate-400">{version.release_notes || "无发布说明"}</p>
                  <p className="mt-1 text-[10px] text-slate-600">{formatDate(version.published_at)} · {version.checksum.slice(0, 10)}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-3 rounded-lg border border-dashed border-white/10 px-3 py-4 text-center text-xs text-slate-500">尚未发布版本</p>
          )}
        </aside>
      </section>

      <AuthoringProposalPanel
        kindPrefix="xpert"
        onApplied={() => {
          void getXpert(xpert.id).then(setXpert);
        }}
        targetId={xpert.id}
        title="关联 Xpert 提案"
      />

      <XpertAppDeploymentPanel xpert={xpert} />

      <WorkflowEditor
        initialDefinition={toWorkflowDefinition(xpert)}
        key={`${xpert.id}-${xpert.draft_revision}`}
        onSave={saveWorkflow}
        saveLabel="保存 Xpert 草稿"
        workflowId={xpert.draft.workflow.id}
      />
    </PageContainer>
  );
}
