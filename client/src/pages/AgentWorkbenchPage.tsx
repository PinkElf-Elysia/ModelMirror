import { useEffect, useState } from "react";
import { ArrowRight, Bot, Braces, Plus, ShieldCheck } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import type { AgentSummary } from "../types/agentWorkspace";
import {
  createWorkspaceAgent,
  listWorkspaceAgents,
  readAgentWorkspaceStatus,
} from "../utils/agentWorkspaceApi";

function WorkbenchSkeleton() {
  return (
    <div aria-label="正在加载 Agent" className="grid gap-4 lg:grid-cols-2">
      {[0, 1].map((item) => (
        <div
          className="h-44 animate-pulse rounded-lg border border-white/10 bg-white/[0.04]"
          key={item}
        />
      ))}
    </div>
  );
}

export default function AgentWorkbenchPage() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const status = await readAgentWorkspaceStatus();
      setEnabled(status.enabled);
      setAgents(status.enabled ? await listWorkspaceAgents() : []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent 工作区加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    document.title = "Agent 工作区 - 模镜";
    void load();
  }, []);

  async function createAgent() {
    const suffix = Date.now().toString(36).slice(-5);
    setCreating(true);
    setError("");
    try {
      const created = await createWorkspaceAgent({
        agent_id: `agent-${suffix}`,
        name: "新 Agent",
        description: "从 General Agent 默认配置创建。",
      });
      navigate(`/agents/workbench/agents/${created.agent_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent 创建失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <PageContainer activeResource="agents" hideSidebar maxWidthClassName="max-w-[1360px]">
      <header className="mb-6 border-b border-white/10 pb-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <Link
              className="text-sm text-slate-400 transition hover:text-white"
              to="/agents"
            >
              ← 返回智能体市场
            </Link>
            <p className="mt-5 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-200">
              Native Agent State
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white">
              Agent 工作区
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              管理独立的 Agent State、提示词、运行参数、九个内置工具配置与 Skill 快照。
              本轮仅开放配置面，任务执行将在下一轮接入。
            </p>
          </div>
          <button
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-md bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!enabled || creating}
            onClick={() => void createAgent()}
            type="button"
          >
            <Plus aria-hidden size={16} />
            {creating ? "创建中…" : "新建 Agent"}
          </button>
        </div>
      </header>

      {!enabled ? (
        <section className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-6">
          <h2 className="text-lg font-semibold text-amber-50">Agent 工作区已关闭</h2>
          <p className="mt-2 text-sm leading-6 text-amber-100/75">
            设置 AGENT_WORKSPACE_ENABLED=1 后，导航入口与独立 API 才会开放；现有聊天、工作流和智能体市场不受影响。
          </p>
        </section>
      ) : null}

      {error ? (
        <div
          aria-live="polite"
          className="mb-5 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50"
          role="alert"
        >
          {error}
          <button
            className="ml-3 font-semibold underline underline-offset-4"
            onClick={() => void load()}
            type="button"
          >
            重试
          </button>
        </div>
      ) : null}

      {enabled ? (
        <section>
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-white">Agent State</h2>
              <p className="mt-1 text-sm text-slate-400">
                General Agent 不可删除；所有 Skill 安装后都是独立快照。
              </p>
            </div>
            <span className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-300">
              {agents.length} 个 Agent
            </span>
          </div>

          {loading ? <WorkbenchSkeleton /> : null}
          {!loading && agents.length ? (
            <div className="grid gap-4 lg:grid-cols-2">
              {agents.map((agent) => (
                <Link
                  className="group rounded-lg border border-white/10 bg-slate-950/55 p-5 transition hover:border-cyan-300/35 hover:bg-white/[0.055]"
                  key={agent.agent_id}
                  to={`/agents/workbench/agents/${agent.agent_id}`}
                >
                  <div className="flex items-start justify-between gap-4">
                    <span className="flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-300/20 bg-cyan-300/10 text-cyan-100">
                      <Bot aria-hidden size={20} />
                    </span>
                    <span className="rounded-full border border-white/10 px-2.5 py-1 text-[11px] text-slate-400">
                      {agent.builtin ? "内置" : "自定义"} · v{agent.version}
                    </span>
                  </div>
                  <h3 className="mt-4 text-lg font-semibold text-white">{agent.name}</h3>
                  <p className="mt-1 font-mono text-xs text-cyan-100">{agent.agent_id}</p>
                  <p className="mt-3 min-h-10 text-sm leading-5 text-slate-400">
                    {agent.description || "尚未填写 Agent 描述。"}
                  </p>
                  <div className="mt-5 flex items-center justify-between border-t border-white/10 pt-4 text-xs text-slate-400">
                    <span>{agent.skill_count} 个 Skill 快照</span>
                    <span className="inline-flex items-center gap-1 font-semibold text-cyan-100">
                      打开配置 <ArrowRight aria-hidden size={14} />
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          ) : null}
          {!loading && !agents.length ? (
            <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.03] px-6 py-12 text-center">
              <Bot className="mx-auto text-slate-500" size={28} />
              <p className="mt-3 text-sm font-semibold text-white">尚无 Agent State</p>
              <p className="mt-1 text-xs text-slate-500">重新加载会幂等创建 General Agent。</p>
            </div>
          ) : null}
        </section>
      ) : null}

      <section className="mt-7 grid gap-4 md:grid-cols-3">
        {[
          {
            icon: Braces,
            title: "文件可读",
            text: "YAML、AGENTS.md 与 Skill 快照可直接审计和备份。",
          },
          {
            icon: ShieldCheck,
            title: "严格隔离",
            text: "独立 API 与命名卷，不改造现有 /api/chat 或工作流路径。",
          },
          {
            icon: Bot,
            title: "执行面预留",
            text: "Session、审批、九工具执行与一句话生成 Agent 将在第二轮实现。",
          },
        ].map(({ icon: Icon, text, title }) => (
          <article className="rounded-lg border border-white/10 bg-white/[0.035] p-4" key={title}>
            <Icon aria-hidden className="text-cyan-200" size={18} />
            <h2 className="mt-3 text-sm font-semibold text-white">{title}</h2>
            <p className="mt-1 text-xs leading-5 text-slate-400">{text}</p>
          </article>
        ))}
      </section>
    </PageContainer>
  );
}
