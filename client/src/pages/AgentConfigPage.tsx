import { useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, CircleAlert, RotateCcw, Save } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import type {
  AgentPayload,
  AgentToolConfig,
  BuiltinSkill,
  SkillCapabilityStatus,
} from "../types/agentWorkspace";
import {
  listBuiltinSkills,
  readWorkspaceAgent,
  resetWorkspaceAgent,
  saveWorkspaceAgent,
} from "../utils/agentWorkspaceApi";

type ConfigTab = "overview" | "prompt" | "runtime" | "tools" | "skills";

const tabs: Array<{ id: ConfigTab; label: string }> = [
  { id: "overview", label: "概览" },
  { id: "prompt", label: "Prompt" },
  { id: "runtime", label: "运行参数" },
  { id: "tools", label: "工具" },
  { id: "skills", label: "技能" },
];

const statusStyle: Record<SkillCapabilityStatus, { label: string; className: string }> = {
  ready: {
    label: "可运行",
    className: "border-emerald-300/25 bg-emerald-300/10 text-emerald-100",
  },
  conditional: {
    label: "环境探测",
    className: "border-sky-300/25 bg-sky-300/10 text-sky-100",
  },
  dependency_missing: {
    label: "依赖缺失",
    className: "border-amber-300/25 bg-amber-300/10 text-amber-100",
  },
  reference_only: {
    label: "仅供查看",
    className: "border-white/15 bg-white/[0.05] text-slate-300",
  },
};

const inputClass =
  "mt-2 w-full rounded-md border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-600 focus:border-cyan-300/50 focus:ring-2 focus:ring-cyan-300/10";

function ConfigSkeleton() {
  return (
    <div aria-label="正在加载 Agent 配置" className="space-y-4">
      <div className="h-12 animate-pulse rounded-lg bg-white/[0.05]" />
      <div className="h-72 animate-pulse rounded-lg bg-white/[0.04]" />
    </div>
  );
}

function numberValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export default function AgentConfigPage() {
  const { agentId = "" } = useParams();
  const [agent, setAgent] = useState<AgentPayload | null>(null);
  const [baseline, setBaseline] = useState("");
  const [library, setLibrary] = useState<BuiltinSkill[]>([]);
  const [activeTab, setActiveTab] = useState<ConfigTab>("overview");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const dirty = useMemo(
    () => Boolean(agent && baseline && JSON.stringify(agent) !== baseline),
    [agent, baseline],
  );
  const libraryById = useMemo(
    () => new Map(library.map((skill) => [skill.skill_id, skill])),
    [library],
  );

  async function load() {
    setLoading(true);
    setError("");
    try {
      const [nextAgent, nextLibrary] = await Promise.all([
        readWorkspaceAgent(agentId),
        listBuiltinSkills(),
      ]);
      setAgent(nextAgent);
      setBaseline(JSON.stringify(nextAgent));
      setLibrary(nextLibrary);
      document.title = `${nextAgent.config.name} - Agent 工作区`;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent 配置加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [agentId]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function updateConfig(mutator: (draft: AgentPayload) => AgentPayload) {
    setAgent((current) => (current ? mutator(current) : current));
    setNotice("");
  }

  async function save() {
    if (!agent) return;
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const saved = await saveWorkspaceAgent(agent);
      setAgent(saved);
      setBaseline(JSON.stringify(saved));
      setNotice("Agent State 已保存。Skill 快照未被改写。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setBusy("");
    }
  }

  async function reset() {
    if (!agent) return;
    if (!window.confirm("恢复默认运行配置？AGENTS.md 与 Skill 快照会保留。")) return;
    setBusy("reset");
    setError("");
    setNotice("");
    try {
      const restored = await resetWorkspaceAgent(agent);
      setAgent(restored);
      setBaseline(JSON.stringify(restored));
      setNotice("运行参数与系统提示词已恢复默认；用户行为层和 Skill 快照已保留。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "恢复失败");
    } finally {
      setBusy("");
    }
  }

  function updateTool(index: number, patch: Partial<AgentToolConfig>) {
    updateConfig((current) => ({
      ...current,
      config: {
        ...current.config,
        tools: {
          builtin: current.config.tools.builtin.map((tool, toolIndex) =>
            toolIndex === index ? { ...tool, ...patch } : tool,
          ),
        },
      },
    }));
  }

  return (
    <PageContainer activeResource="agents" hideSidebar maxWidthClassName="max-w-[1320px]">
      <header className="mb-5 border-b border-white/10 pb-5">
        <Link
          className="inline-flex items-center gap-1 text-sm text-slate-400 transition hover:text-white"
          to="/agents/workbench"
        >
          <ChevronLeft aria-hidden size={16} /> 返回 Agent 工作区
        </Link>
        <div className="mt-5 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-white">
              {agent?.config.name ?? "Agent 配置"}
            </h1>
            <p className="mt-1 font-mono text-xs text-cyan-100">{agentId}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {dirty ? (
              <span className="inline-flex items-center gap-1 text-xs text-amber-100">
                <CircleAlert aria-hidden size={14} /> 有未保存修改
              </span>
            ) : null}
            <button
              className="inline-flex min-h-10 items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-50"
              disabled={!agent || Boolean(busy)}
              onClick={() => void reset()}
              type="button"
            >
              <RotateCcw aria-hidden size={15} /> 恢复默认配置
            </button>
            <button
              className="inline-flex min-h-10 items-center gap-2 rounded-md bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!agent || !dirty || Boolean(busy)}
              onClick={() => void save()}
              type="button"
            >
              <Save aria-hidden size={15} /> {busy === "save" ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      </header>

      {error || notice ? (
        <div
          aria-live="polite"
          className={`mb-4 rounded-lg border px-4 py-3 text-sm ${
            error
              ? "border-rose-300/25 bg-rose-300/10 text-rose-50"
              : "border-emerald-300/25 bg-emerald-300/10 text-emerald-50"
          }`}
          role={error ? "alert" : "status"}
        >
          {error || notice}
          {error && !agent ? (
            <button className="ml-3 font-semibold underline" onClick={() => void load()} type="button">
              重试
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="mb-5 overflow-x-auto border-b border-white/10">
        <div aria-label="Agent 配置页签" className="flex min-w-max gap-1" role="tablist">
          {tabs.map((tab) => (
            <button
              aria-selected={activeTab === tab.id}
              className={`border-b-2 px-4 py-3 text-sm font-semibold transition ${
                activeTab === tab.id
                  ? "border-cyan-300 text-white"
                  : "border-transparent text-slate-400 hover:text-white"
              }`}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              role="tab"
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? <ConfigSkeleton /> : null}
      {!loading && !agent && !error ? (
        <div className="rounded-lg border border-dashed border-white/15 p-10 text-center text-sm text-slate-400">
          未找到 Agent State。
        </div>
      ) : null}

      {!loading && agent ? (
        <section className="rounded-lg border border-white/10 bg-slate-950/55 p-4 sm:p-5">
          {activeTab === "overview" ? (
            <div className="space-y-5">
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-xs font-semibold text-slate-300">
                  名称
                  <input
                    className={inputClass}
                    maxLength={120}
                    onChange={(event) =>
                      updateConfig((current) => ({
                        ...current,
                        config: { ...current.config, name: event.target.value },
                      }))
                    }
                    value={agent.config.name}
                  />
                </label>
                <label className="text-xs font-semibold text-slate-300">
                  Agent ID
                  <input className={`${inputClass} opacity-70`} disabled value={agent.agent_id} />
                </label>
              </div>
              <label className="block text-xs font-semibold text-slate-300">
                描述
                <textarea
                  className={`${inputClass} min-h-24 resize-y`}
                  maxLength={1000}
                  onChange={(event) =>
                    updateConfig((current) => ({
                      ...current,
                      config: { ...current.config, description: event.target.value },
                    }))
                  }
                  value={agent.config.description}
                />
              </label>
              <dl className="grid gap-3 border-t border-white/10 pt-5 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  ["State 路径", agent.state_path],
                  ["Agent State 版本", `v${agent.config.version}`],
                  ["Skillset", agent.config.skillset_id],
                  ["Skill 快照", `${agent.skills.length} 项`],
                ].map(([label, value]) => (
                  <div className="rounded-md border border-white/10 bg-white/[0.035] p-3" key={label}>
                    <dt className="text-[11px] text-slate-500">{label}</dt>
                    <dd className="mt-1 break-all text-xs font-semibold text-slate-200">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}

          {activeTab === "prompt" ? (
            <div className="space-y-5">
              <label className="block text-xs font-semibold text-slate-300">
                AGENTS.md（用户行为层）
                <textarea
                  className={`${inputClass} min-h-52 resize-y font-mono text-[13px] leading-6`}
                  onChange={(event) =>
                    updateConfig((current) => ({ ...current, agents_md: event.target.value }))
                  }
                  placeholder="默认留空；在这里写入角色、工作流程、成功标准和停止规则。"
                  value={agent.agents_md}
                />
              </label>
              <label className="block text-xs font-semibold text-slate-300">
                system_prompt 模板（稳定系统层）
                <textarea
                  className={`${inputClass} min-h-[28rem] resize-y font-mono text-[13px] leading-6`}
                  onChange={(event) =>
                    updateConfig((current) => ({
                      ...current,
                      config: { ...current.config, system_prompt: event.target.value },
                    }))
                  }
                  value={agent.config.system_prompt}
                />
              </label>
              <p className="rounded-md border border-white/10 bg-white/[0.03] p-3 text-xs leading-5 text-slate-400">
                支持 AGENTS_MD、SKILL_METADATA、SESSION_ID、CWD、AGENT_ID、MODEL_ID、平台和日期等占位符；未知占位符会由后端拒绝。
              </p>
            </div>
          ) : null}

          {activeTab === "runtime" ? (
            <div className="space-y-6">
              <div className="grid gap-4 md:grid-cols-2">
                <label className="text-xs font-semibold text-slate-300">
                  max_turns（-1 不限制）
                  <input
                    className={inputClass}
                    onChange={(event) =>
                      updateConfig((current) => ({
                        ...current,
                        config: {
                          ...current.config,
                          max_turns: numberValue(event.target.value, current.config.max_turns),
                        },
                      }))
                    }
                    type="number"
                    value={agent.config.max_turns}
                  />
                </label>
                <label className="text-xs font-semibold text-slate-300">
                  model.max_tokens
                  <input
                    className={inputClass}
                    min={1}
                    onChange={(event) =>
                      updateConfig((current) => ({
                        ...current,
                        config: {
                          ...current.config,
                          model: {
                            ...current.config.model,
                            max_tokens: numberValue(event.target.value, current.config.model.max_tokens),
                          },
                        },
                      }))
                    }
                    type="number"
                    value={agent.config.model.max_tokens}
                  />
                </label>
                <label className="text-xs font-semibold text-slate-300">
                  model.thinking_level
                  <select
                    className={inputClass}
                    onChange={(event) =>
                      updateConfig((current) => ({
                        ...current,
                        config: {
                          ...current.config,
                          model: {
                            ...current.config.model,
                            thinking_level: event.target.value as AgentPayload["config"]["model"]["thinking_level"],
                          },
                        },
                      }))
                    }
                    value={agent.config.model.thinking_level}
                  >
                    {['low', 'medium', 'high', 'xhigh'].map((level) => <option key={level} value={level}>{level}</option>)}
                  </select>
                </label>
                <label className="text-xs font-semibold text-slate-300">
                  model.timeoutMs
                  <input
                    className={inputClass}
                    min={1000}
                    onChange={(event) =>
                      updateConfig((current) => ({
                        ...current,
                        config: {
                          ...current.config,
                          model: {
                            ...current.config.model,
                            timeoutMs: numberValue(event.target.value, current.config.model.timeoutMs),
                          },
                        },
                      }))
                    }
                    type="number"
                    value={agent.config.model.timeoutMs}
                  />
                </label>
              </div>

              <div className="border-t border-white/10 pt-5">
                <h2 className="text-sm font-semibold text-white">上下文压缩</h2>
                <p className="mt-1 text-xs text-slate-400">本轮只保存参数；压缩执行将在运行时接入。</p>
                <div className="mt-4 grid gap-4 md:grid-cols-3">
                  {([
                    ["max_context_length", "max_context_length"],
                    ["max_session_turns", "max_session_turns"],
                  ] as const).map(([label, key]) => (
                    <label className="text-xs font-semibold text-slate-300" key={key}>
                      {label}
                      <input
                        className={inputClass}
                        onChange={(event) =>
                          updateConfig((current) => ({
                            ...current,
                            config: {
                              ...current.config,
                              compaction: {
                                ...current.config.compaction,
                                [key]: numberValue(event.target.value, current.config.compaction[key]),
                              },
                            },
                          }))
                        }
                        type="number"
                        value={agent.config.compaction[key]}
                      />
                    </label>
                  ))}
                  <label className="text-xs font-semibold text-slate-300">
                    mode
                    <input className={`${inputClass} opacity-70`} disabled value="summarize" />
                  </label>
                </div>
                <label className="mt-4 block text-xs font-semibold text-slate-300">
                  摘要提示词
                  <textarea
                    className={`${inputClass} min-h-44 resize-y font-mono text-[13px] leading-6`}
                    onChange={(event) =>
                      updateConfig((current) => ({
                        ...current,
                        config: {
                          ...current.config,
                          compaction: { ...current.config.compaction, prompt: event.target.value },
                        },
                      }))
                    }
                    value={agent.config.compaction.prompt}
                  />
                </label>
              </div>
            </div>
          ) : null}

          {activeTab === "tools" ? (
            <div>
              <div className="mb-4">
                <h2 className="text-sm font-semibold text-white">九个内置工具 Schema</h2>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  本轮只配置 permission、timeoutMs、maxOutputLength 与调用描述；不会执行任何工具。
                </p>
              </div>
              <div className="overflow-x-auto rounded-lg border border-white/10">
                <table className="min-w-[860px] w-full text-left text-xs">
                  <thead className="bg-white/[0.04] text-slate-400">
                    <tr>
                      <th className="px-3 py-3 font-semibold">名称</th>
                      <th className="px-3 py-3 font-semibold">权限</th>
                      <th className="px-3 py-3 font-semibold">超时 ms</th>
                      <th className="px-3 py-3 font-semibold">最大输出</th>
                      <th className="px-3 py-3 font-semibold">调用描述</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/10">
                    {agent.config.tools.builtin.map((tool, index) => (
                      <tr key={tool.name}>
                        <td className="px-3 py-3 font-mono font-semibold text-cyan-100">{tool.name}</td>
                        <td className="px-3 py-3">
                          <select
                            aria-label={`${tool.name} 权限`}
                            className="rounded-md border border-white/10 bg-slate-950 px-2 py-1.5 text-white"
                            onChange={(event) => updateTool(index, { permission: event.target.value as "r" | "rw" })}
                            value={tool.permission}
                          >
                            <option value="r">r</option><option value="rw">rw</option>
                          </select>
                        </td>
                        <td className="px-3 py-3">
                          <input aria-label={`${tool.name} 超时`} className="w-28 rounded-md border border-white/10 bg-black/20 px-2 py-1.5 text-white" min={1000} onChange={(event) => updateTool(index, { timeoutMs: numberValue(event.target.value, tool.timeoutMs) })} type="number" value={tool.timeoutMs} />
                        </td>
                        <td className="px-3 py-3">
                          <input aria-label={`${tool.name} 最大输出`} className="w-28 rounded-md border border-white/10 bg-black/20 px-2 py-1.5 text-white" min={1024} onChange={(event) => updateTool(index, { maxOutputLength: numberValue(event.target.value, tool.maxOutputLength) })} type="number" value={tool.maxOutputLength} />
                        </td>
                        <td className="px-3 py-3">
                          <input aria-label={`${tool.name} 调用描述`} checked={tool.call_description} onChange={(event) => updateTool(index, { call_description: event.target.checked })} type="checkbox" />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {activeTab === "skills" ? (
            <div>
              <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <h2 className="text-sm font-semibold text-white">内置 Skill 快照</h2>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    默认 Skillset 固定为 16 项；只有当前可运行项会在下一轮注入模型上下文。
                  </p>
                </div>
                <span className="w-fit rounded-full border border-white/10 px-3 py-1 text-xs text-slate-300">
                  {agent.skills.length} / 16
                </span>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                {agent.skills.map((skill) => {
                  const capability = libraryById.get(skill.skill_id);
                  const status = statusStyle[skill.status];
                  return (
                    <article className="rounded-lg border border-white/10 bg-white/[0.03] p-4" key={skill.skill_id}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="truncate font-mono text-sm font-semibold text-white">{skill.skill_id}</h3>
                          <p className="mt-1 text-xs leading-5 text-slate-400">{skill.description}</p>
                        </div>
                        <span className={`shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${status.className}`}>
                          {status.label}
                        </span>
                      </div>
                      <p className="mt-3 text-xs leading-5 text-slate-500">{capability?.availability_reason || skill.reason}</p>
                      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/10 pt-3 text-[11px] text-slate-500">
                        <span>{skill.source_license}</span>
                        <span>摘要 {skill.digest.slice(0, 10)}</span>
                        {skill.adapted ? <span>已原生适配</span> : null}
                        {capability?.inject_runtime ? (
                          <span className="ml-auto inline-flex items-center gap-1 text-emerald-200"><Check aria-hidden size={12} /> 可注入</span>
                        ) : (
                          <span className="ml-auto">不注入</span>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}
    </PageContainer>
  );
}
