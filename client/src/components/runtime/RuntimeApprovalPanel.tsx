import { useCallback, useEffect, useMemo, useState } from "react";

export interface RuntimeApproval {
  approval_id: string;
  request_type: "tool_call" | "final_output" | "manual_input" | "execution_gate" | "browser_domain";
  task_id: string;
  run_id: string;
  node_id: string;
  node_title: string;
  status: "pending" | "decided" | "expired" | "cancelled";
  revision: number;
  scope_type: string;
  scope_id: string;
  tool_name: string | null;
  arguments: Record<string, unknown>;
  description: string;
  content_preview: string;
  allowed_decisions: string[];
  expires_at: number;
  created_at: number;
  metadata: Record<string, unknown>;
}

interface RuntimeApprovalPanelProps {
  taskId?: string;
  runId?: string;
  scopeType?: string;
  scopeId?: string;
  title?: string;
  compact?: boolean;
  pollIntervalMs?: number;
  requestTypes?: RuntimeApproval["request_type"][];
  onResolved?: (approval: RuntimeApproval) => void | Promise<void>;
}

interface SkillInstallApproval {
  candidate_id: string;
  name: string;
  repo_url: string;
  sub_path: string;
  current_sha?: string | null;
  target_sha: string;
  install_action: "install" | "upgrade";
  authorization_scope: "global_install_current_run_only";
  trust: {
    riskLevel?: string | null;
    trustStatus?: string;
    installPolicy?: string;
    compatibilityStatus?: string;
    routerEligible?: boolean;
    reasonCodes?: string[];
    missingCapabilities?: string[];
  };
}

function skillInstallApproval(approval: RuntimeApproval): SkillInstallApproval | null {
  const value = approval.metadata.skill_approval;
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.candidate_id !== "string" ||
    typeof candidate.name !== "string" ||
    typeof candidate.repo_url !== "string" ||
    typeof candidate.sub_path !== "string" ||
    typeof candidate.target_sha !== "string" ||
    candidate.authorization_scope !== "global_install_current_run_only"
  ) {
    return null;
  }
  return {
    candidate_id: candidate.candidate_id,
    name: candidate.name,
    repo_url: candidate.repo_url,
    sub_path: candidate.sub_path,
    current_sha: typeof candidate.current_sha === "string" ? candidate.current_sha : null,
    target_sha: candidate.target_sha,
    install_action: candidate.install_action === "upgrade" ? "upgrade" : "install",
    authorization_scope: candidate.authorization_scope,
    trust:
      candidate.trust && typeof candidate.trust === "object" && !Array.isArray(candidate.trust)
        ? (candidate.trust as SkillInstallApproval["trust"])
        : {},
  };
}

function approvalError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  }
  return fallback;
}

function formatDeadline(timestamp: number) {
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "未设置";
  return new Date(timestamp * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function requestTypeLabel(type: RuntimeApproval["request_type"]) {
  if (type === "tool_call") return "工具调用审批";
  if (type === "final_output") return "最终输出确认";
  if (type === "execution_gate") return "执行批准";
  if (type === "browser_domain") return "浏览器域名授权";
  return "人工输入";
}

function decisionLabel(decision: string) {
  const labels: Record<string, string> = {
    approve: "批准",
    edit: "编辑参数并批准",
    reject: "拒绝",
    replace: "使用人工内容",
    revise: "要求模型修订",
  };
  return labels[decision] ?? decision;
}

export default function RuntimeApprovalPanel({
  taskId,
  runId,
  scopeType,
  scopeId,
  title = "运行审批",
  compact = false,
  pollIntervalMs = 4000,
  requestTypes,
  onResolved,
}: RuntimeApprovalPanelProps) {
  const [items, setItems] = useState<RuntimeApproval[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [editingId, setEditingId] = useState("");
  const [editText, setEditText] = useState("");
  const [messageDrafts, setMessageDrafts] = useState<Record<string, string>>({});
  const [replacementDrafts, setReplacementDrafts] = useState<Record<string, string>>({});
  const requestTypeKey = requestTypes === undefined
    ? null
    : [...new Set(requestTypes)].sort().join("\u0000");

  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: compact ? "20" : "100" });
    if (taskId) params.set("task_id", taskId);
    if (runId) params.set("run_id", runId);
    if (scopeType) params.set("scope_type", scopeType);
    if (scopeId) params.set("scope_id", scopeId);
    return params.toString();
  }, [compact, runId, scopeId, scopeType, taskId]);

  const load = useCallback(async (quiet = false) => {
    if (!taskId && !runId && !scopeType && !scopeId) {
      setItems([]);
      return;
    }
    if (!quiet) setLoading(true);
    try {
      const responses = await Promise.all(["pending", "expired"].map(async (status) => {
        const response = await fetch(`/api/runtime/approvals?status=${status}&${query}`);
        const payload = (await response.json().catch(() => null)) as
          | { items?: RuntimeApproval[]; detail?: string }
          | null;
        if (!response.ok) throw new Error(approvalError(payload, "审批列表加载失败"));
        return payload?.items ?? [];
      }));
      const allowedRequestTypes = requestTypeKey === null
        ? null
        : new Set(
            requestTypeKey
              ? requestTypeKey.split("\u0000") as RuntimeApproval["request_type"][]
              : [],
          );
      const currentById = new Map<string, RuntimeApproval>();
      responses.flat().forEach((item) => {
        const existing = currentById.get(item.approval_id);
        if (!existing || item.revision >= existing.revision) {
          currentById.set(item.approval_id, item);
        }
      });
      setItems(
        [...currentById.values()].filter(
          (item) => !allowedRequestTypes || allowedRequestTypes.has(item.request_type),
        ),
      );
      setError("");
    } catch (caught) {
      if (!quiet) {
        setError(caught instanceof Error ? caught.message : "审批列表加载失败");
      }
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [query, requestTypeKey, runId, scopeId, scopeType, taskId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), pollIntervalMs);
    return () => window.clearInterval(timer);
  }, [load, pollIntervalMs]);

  async function decide(
    approval: RuntimeApproval,
    decision: string,
    extra: Record<string, unknown> = {},
  ) {
    setBusyId(approval.approval_id);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/approvals/${approval.approval_id}/decide`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: approval.revision,
            decision,
            operator: "local-operator",
            ...extra,
          }),
        },
      );
      const payload = (await response.json().catch(() => null)) as
        | RuntimeApproval
        | { detail?: string }
        | null;
      if (!response.ok) {
        throw new Error(approvalError(payload, "审批提交失败"));
      }
      setEditingId("");
      await load(true);
      if (payload && "approval_id" in payload) await onResolved?.(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "审批提交失败");
      await load(true);
    } finally {
      setBusyId("");
    }
  }

  async function submitEditedArguments(approval: RuntimeApproval) {
    try {
      const parsed = JSON.parse(editText) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("工具参数必须是 JSON 对象");
      }
      await decide(approval, "edit", {
        edited_arguments: parsed as Record<string, unknown>,
        message: messageDrafts[approval.approval_id] || undefined,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "工具参数 JSON 无效");
    }
  }

  async function reopen(approval: RuntimeApproval) {
    setBusyId(approval.approval_id);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/approvals/${approval.approval_id}/reopen`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: approval.revision,
            timeout_seconds: 3600,
            operator: "local-operator",
          }),
        },
      );
      const payload = (await response.json().catch(() => null)) as RuntimeApproval | { detail?: string } | null;
      if (!response.ok) throw new Error(approvalError(payload, "重新打开审批失败"));
      await load(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "重新打开审批失败");
      await load(true);
    } finally {
      setBusyId("");
    }
  }

  if (!error && items.length === 0) return null;

  return (
    <section className="rounded-lg border border-amber-300/25 bg-amber-300/[0.065] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-amber-300/15 text-amber-100">
            <span aria-hidden="true" className="text-sm font-bold">✓</span>
          </span>
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-white">{title}</h3>
            <p className="mt-0.5 text-xs leading-5 text-slate-400">
              执行已暂停。审批后将从原动作继续，不会重跑已完成节点。
            </p>
          </div>
        </div>
        <button
          aria-label="刷新审批"
          className="rounded-md p-1.5 text-slate-400 transition hover:bg-white/10 hover:text-white"
          disabled={loading}
          onClick={() => void load()}
          title="刷新审批"
          type="button"
        >
          <span aria-hidden="true" className={loading ? "inline-block animate-spin" : "inline-block"}>↻</span>
        </button>
      </div>

      {error ? (
        <div className="mt-3 flex items-start gap-2 rounded-md border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100">
          <span aria-hidden="true" className="mt-0.5 shrink-0 font-bold">!</span>
          <span>{error}</span>
        </div>
      ) : null}

      <div className="mt-3 space-y-3">
        {items.map((approval) => {
          const busy = busyId === approval.approval_id;
          const message = messageDrafts[approval.approval_id] ?? "";
          const replacement = replacementDrafts[approval.approval_id] ?? "";
          const skillApproval = skillInstallApproval(approval);
          return (
            <article
              className="rounded-lg border border-white/10 bg-ink-950/55 p-3"
              key={approval.approval_id}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-md bg-amber-300/12 px-2 py-1 text-[10px] font-semibold text-amber-100">
                      {requestTypeLabel(approval.request_type)}
                    </span>
                    {approval.tool_name ? (
                      <code className="break-all text-[11px] text-cyan-100">{approval.tool_name}</code>
                    ) : null}
                  </div>
                  <p className="mt-2 text-xs font-semibold text-slate-200">{approval.node_title}</p>
                  {approval.description ? (
                    <p className="mt-1 text-xs leading-5 text-slate-400">{approval.description}</p>
                  ) : null}
                </div>
                <span className="inline-flex items-center gap-1 text-[10px] text-slate-500">
                  <span aria-hidden="true">◷</span>
                  {formatDeadline(approval.expires_at)}
                </span>
              </div>

              {skillApproval ? (
                <div className="mt-3 rounded-md border border-cyan-300/20 bg-cyan-300/[0.07] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-cyan-50">
                      {skillApproval.install_action === "upgrade" ? "升级 Skill" : "安装 Skill"}：{skillApproval.name}
                    </p>
                    <span className="rounded-full border border-amber-200/25 bg-amber-200/10 px-2 py-0.5 text-[10px] font-semibold text-amber-100">
                      全局安装 · 仅本轮授权
                    </span>
                  </div>
                  <dl className="mt-2 grid gap-2 text-[11px] sm:grid-cols-2">
                    <div className="min-w-0">
                      <dt className="text-slate-500">来源仓库</dt>
                      <dd className="mt-0.5 break-all text-slate-200">{skillApproval.repo_url}</dd>
                    </div>
                    <div className="min-w-0">
                      <dt className="text-slate-500">Skill 目录</dt>
                      <dd className="mt-0.5 break-all text-slate-200">{skillApproval.sub_path || "."}</dd>
                    </div>
                    <div className="min-w-0">
                      <dt className="text-slate-500">当前 SHA</dt>
                      <dd className="mt-0.5 break-all font-mono text-slate-300">{skillApproval.current_sha || "未安装"}</dd>
                    </div>
                    <div className="min-w-0">
                      <dt className="text-slate-500">目标 SHA（已核验）</dt>
                      <dd className="mt-0.5 break-all font-mono text-emerald-200">{skillApproval.target_sha}</dd>
                    </div>
                  </dl>
                  <div className="mt-3 border-t border-cyan-200/10 pt-3 text-[11px] leading-5">
                    <div className="flex flex-wrap gap-x-4 gap-y-1">
                      <span className="text-slate-400">风险：<strong className="text-amber-100">{skillApproval.trust.riskLevel || "未知"}</strong></span>
                      <span className="text-slate-400">兼容性：<strong className="text-slate-200">{skillApproval.trust.compatibilityStatus || "未知"}</strong></span>
                      <span className="text-slate-400">Router：<strong className="text-slate-200">{skillApproval.trust.routerEligible === false ? "不纳入自动发现" : "当前候选可用"}</strong></span>
                    </div>
                    {skillApproval.trust.reasonCodes?.length ? (
                      <p className="mt-2 break-words text-slate-400">发现：{skillApproval.trust.reasonCodes.join("、")}</p>
                    ) : null}
                    {skillApproval.trust.missingCapabilities?.length ? (
                      <p className="mt-2 break-words text-rose-100">当前运行缺少：{skillApproval.trust.missingCapabilities.join("、")}</p>
                    ) : null}
                    <p className="mt-2 text-amber-100/85">批准只授权当前 Agent 运行；来源参数和固定 SHA 不可编辑。</p>
                  </div>
                </div>
              ) : approval.request_type === "tool_call" || approval.request_type === "browser_domain" ? (
                <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-black/25 p-2.5 text-[11px] leading-5 text-slate-300">
                  {JSON.stringify(approval.arguments, null, 2)}
                </pre>
              ) : approval.content_preview ? (
                <p className="mt-3 max-h-48 overflow-y-auto whitespace-pre-wrap rounded-md bg-black/25 p-2.5 text-xs leading-5 text-slate-300">
                  {approval.content_preview}
                </p>
              ) : null}

              {approval.request_type === "browser_domain" ? (
                <p className="mt-2 text-[11px] leading-5 text-amber-100/80">
                  授权仅对当前私有浏览器会话生效，可在浏览器会话面板随时撤销。
                </p>
              ) : null}

              {editingId === approval.approval_id ? (
                <div className="mt-3 space-y-2">
                  <label className="block text-[11px] font-semibold text-slate-400">
                    工具参数 JSON
                    <textarea
                      className="mt-1 min-h-32 w-full resize-y rounded-md border border-white/10 bg-ink-950 px-2.5 py-2 font-mono text-xs leading-5 text-white outline-none focus:border-amber-300/45"
                      onChange={(event) => setEditText(event.target.value)}
                      value={editText}
                    />
                  </label>
                  <div className="flex justify-end gap-2">
                    <button
                      className="h-8 rounded-md border border-white/10 px-2.5 text-xs font-semibold text-slate-300"
                      onClick={() => setEditingId("")}
                      type="button"
                    >
                      取消编辑
                    </button>
                    <button
                      className="h-8 rounded-md bg-amber-300 px-2.5 text-xs font-semibold text-ink-950 disabled:opacity-50"
                      disabled={busy}
                      onClick={() => void submitEditedArguments(approval)}
                      type="button"
                    >
                      提交参数
                    </button>
                  </div>
                </div>
              ) : null}

              {approval.allowed_decisions.some((decision) =>
                ["reject", "revise", "edit"].includes(decision),
              ) ? (
                <textarea
                  className="mt-3 min-h-16 w-full resize-y rounded-md border border-white/10 bg-white/[0.035] px-2.5 py-2 text-xs leading-5 text-white outline-none placeholder:text-slate-600 focus:border-amber-300/45"
                  onChange={(event) =>
                    setMessageDrafts((current) => ({
                      ...current,
                      [approval.approval_id]: event.target.value,
                    }))
                  }
                  placeholder={approval.request_type === "final_output" ? "修订反馈（要求模型修订时必填）" : "审批说明或拒绝原因"}
                  value={message}
                />
              ) : null}

              {approval.allowed_decisions.includes("replace") ? (
                <textarea
                  className="mt-2 min-h-20 w-full resize-y rounded-md border border-white/10 bg-white/[0.035] px-2.5 py-2 text-xs leading-5 text-white outline-none placeholder:text-slate-600 focus:border-cyan-300/45"
                  onChange={(event) =>
                    setReplacementDrafts((current) => ({
                      ...current,
                      [approval.approval_id]: event.target.value,
                    }))
                  }
                  placeholder={approval.request_type === "manual_input" ? "输入人工内容" : "人工替换后的最终输出"}
                  value={replacement}
                />
              ) : null}

              <div className="mt-3 flex flex-wrap gap-2">
                {approval.status === "expired" ? (
                  <button
                    className="inline-flex h-8 items-center rounded-md bg-amber-200 px-3 text-xs font-semibold text-ink-950 disabled:opacity-45"
                    disabled={busy}
                    onClick={() => void reopen(approval)}
                    type="button"
                  >
                    {busy ? "重新打开中…" : "重新打开 1 小时"}
                  </button>
                ) : approval.allowed_decisions.map((decision) => {
                  if (decision === "edit") {
                    return (
                      <button
                        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-cyan-300/25 bg-cyan-300/10 px-2.5 text-xs font-semibold text-cyan-100"
                        disabled={busy}
                        key={decision}
                        onClick={() => {
                          setEditingId(approval.approval_id);
                          setEditText(JSON.stringify(approval.arguments, null, 2));
                        }}
                        type="button"
                      >
                        <span aria-hidden="true">✎</span>
                        编辑参数
                      </button>
                    );
                  }
                  const destructive = decision === "reject";
                  const disabled =
                    busy ||
                    (decision === "replace" && !replacement.trim()) ||
                    (decision === "revise" && !message.trim());
                  return (
                    <button
                      className={`inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-45 ${
                        destructive
                          ? "border border-rose-300/25 bg-rose-300/10 text-rose-100"
                          : "bg-emerald-300 text-ink-950"
                      }`}
                      disabled={disabled}
                      key={decision}
                      onClick={() =>
                        void decide(approval, decision, {
                          message: message || undefined,
                          replacement_text: replacement || undefined,
                        })
                      }
                      type="button"
                    >
                      <span aria-hidden="true">{destructive ? "×" : "✓"}</span>
                      {decisionLabel(decision)}
                    </button>
                  );
                })}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
