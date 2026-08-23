import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BadgeCheck,
  CircleAlert,
  LoaderCircle,
  RefreshCw,
  Route,
  ShieldCheck,
} from "lucide-react";

type EntryId =
  | "agent_shadow"
  | "meta_agent"
  | "workflow_interactive_llm"
  | "workflow_deployment_llm"
  | "workflow_interactive_agent"
  | "workflow_deployment_agent"
  | "xpert"
  | "xpert_app"
  | "expert_team_planner"
  | "expert_team_dag"
  | "fusion"
  | "route_agent"
  | "team_chat";
type ExecutionShape =
  | "chat_text"
  | "chat_tools"
  | "chat_text_unary"
  | "chat_json_object"
  | "fusion_native";
type PolicyStatus = "legacy" | "managed_required" | "degraded_required";

interface ConnectionSummary {
  id: string;
  name: string;
  kind: string;
  scopes?: string[];
  enabled: boolean;
}

interface CertificationSummary {
  certification_id?: string | null;
  connection_id: string;
  connection_name: string;
  provider_kind: string;
  execution_shape: ExecutionShape;
  status: string;
  can_run: boolean;
  blocked_reason?: string | null;
  error_code?: string | null;
  requested_model?: string | null;
  actual_model?: string | null;
  candidate_model_ids: string[];
  judge_model_id?: string | null;
  e2e_ms?: number | null;
  total_tokens?: number | null;
  completed_at?: string | null;
}

interface BindingSummary {
  execution_shape: ExecutionShape;
  model_id: string;
  connection_id: string;
  connection_name: string;
  provider_kind: string;
  certification_id: string;
  valid: boolean;
  reason_code: string;
}

interface PolicySummary {
  entry_id: EntryId;
  feature_enabled: boolean;
  data_plane_integrated: boolean;
  configured_status: PolicyStatus;
  effective_status: PolicyStatus;
  revision: number;
  policy_fingerprint: string;
  bindings: BindingSummary[];
  approval_valid: boolean;
  blocking_reason_codes: string[];
}

interface ReceiptCall {
  call_id: string;
  execution_shape: ExecutionShape;
  model_id: string;
  call_sequence: number;
  dispatched: boolean;
  status: string;
  total_tokens?: number | null;
}

interface ReceiptRun {
  run_id: string;
  entry_id: EntryId;
  status: string;
  created_at: string;
  calls: ReceiptCall[];
}

interface EditableBinding {
  execution_shape: ExecutionShape;
  model_id: string;
  connection_id: string;
}

const ENTRY_LABELS: Record<EntryId, string> = {
  agent_shadow: "Engine Shadow",
  meta_agent: "Meta Agent",
  workflow_interactive_llm: "Workflow 交互 LLM",
  workflow_deployment_llm: "Workflow 部署 LLM",
  workflow_interactive_agent: "Workflow 交互 Agent",
  workflow_deployment_agent: "Workflow 部署 Agent",
  xpert: "Published Xpert",
  xpert_app: "Xpert App",
  expert_team_planner: "Expert Team Planner",
  expert_team_dag: "Expert Team DAG",
  fusion: "Fusion",
  route_agent: "Route Agent",
  team_chat: "Team Chat",
};

const SHAPE_LABELS: Record<ExecutionShape, string> = {
  chat_text: "流式文本（复用 R5）",
  chat_tools: "流式工具调用（复用 R5）",
  chat_text_unary: "非流式文本",
  chat_json_object: "JSON Object",
  fusion_native: "OpenRouter 原生 Fusion",
};

const NEW_CERTIFICATION_SHAPES: ExecutionShape[] = [
  "chat_text_unary",
  "chat_json_object",
  "fusion_native",
];

const ENTRY_SHAPES: Record<EntryId, ExecutionShape[]> = {
  agent_shadow: ["chat_tools"],
  meta_agent: ["chat_json_object"],
  workflow_interactive_llm: ["chat_text", "chat_text_unary", "chat_json_object"],
  workflow_deployment_llm: ["chat_text", "chat_text_unary", "chat_json_object"],
  workflow_interactive_agent: ["chat_text", "chat_tools", "chat_json_object"],
  workflow_deployment_agent: ["chat_text", "chat_tools", "chat_json_object"],
  xpert: ["chat_text", "chat_tools", "chat_json_object"],
  xpert_app: ["chat_text", "chat_tools", "chat_json_object"],
  expert_team_planner: ["chat_json_object"],
  expert_team_dag: ["chat_text_unary"],
  fusion: ["chat_text", "fusion_native"],
  route_agent: ["chat_text"],
  team_chat: ["chat_text"],
};

const REASON_LABELS: Record<string, string> = {
  provider_workload_bindings_required: "尚未配置精确模型 Binding",
  provider_workload_data_plane_not_integrated: "该入口的数据面尚未在当前子轮次接入",
  provider_workload_feature_disabled: "部署 Feature Flag 当前关闭",
  provider_workload_approval_missing: "人工 fail-closed 批准已失效或尚未记录",
  provider_workload_certification_required: "缺少对应执行形态的真实 Provider 资格",
  provider_workload_certification_not_passed: "最新执行形态资格未通过",
  provider_workload_catalog_refresh_truncated: "最新目录已截断",
  provider_workload_newer_certification_requires_policy_update: "存在更新资格，需要重新保存 Binding",
  provider_workload_connection_missing: "Binding 指向的 Managed 连接已不存在",
  provider_workload_credential_unavailable: "Provider 凭据当前无法解密",
  provider_workload_certification_expired: "执行形态资格已过期，需要重新认证",
  provider_connection_not_online: "Managed 连接当前不在线",
  qualified: "资格有效",
};

async function readError(response: Response) {
  if (response.status === 401) return "管理会话已失效，请重新配对。";
  if (response.status === 403) return "CSRF 校验失败，请刷新页面后重试。";
  try {
    const payload = await response.json();
    if (typeof payload?.detail?.message === "string") return payload.detail.message;
    if (typeof payload?.detail === "string") return payload.detail;
  } catch {
    // Use the stable fallback without exposing an upstream body.
  }
  return "Workload 控制面操作未完成。";
}

function statusLabel(status: PolicyStatus) {
  if (status === "managed_required") return "Managed 必经";
  if (status === "degraded_required") return "Managed 降级阻断";
  return "Legacy";
}

function randomIdempotencyKey() {
  return `workload-cert-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function ProviderWorkloadControlSettings({
  csrfToken,
  view,
}: {
  csrfToken: string;
  view: "certifications" | "routing";
}) {
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [certifications, setCertifications] = useState<CertificationSummary[]>([]);
  const [policies, setPolicies] = useState<PolicySummary[]>([]);
  const [receipts, setReceipts] = useState<ReceiptRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const [connectionId, setConnectionId] = useState("");
  const [certificationShape, setCertificationShape] = useState<ExecutionShape>(
    "chat_text_unary",
  );
  const [certificationModel, setCertificationModel] = useState("");
  const [candidateModels, setCandidateModels] = useState("");
  const [judgeModel, setJudgeModel] = useState("");
  const [confirmCertification, setConfirmCertification] = useState(false);

  const [entryId, setEntryId] = useState<EntryId>("agent_shadow");
  const [editableBindings, setEditableBindings] = useState<EditableBinding[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (view === "certifications") {
        const [connectionResponse, certificationResponse] = await Promise.all([
          fetch("/api/router/connections"),
          fetch("/api/router/certifications/workloads"),
        ]);
        for (const response of [connectionResponse, certificationResponse]) {
          if (!response.ok) throw new Error(await readError(response));
        }
        const nextConnections = (await connectionResponse.json()) as ConnectionSummary[];
        const certificationPayload = (await certificationResponse.json()) as {
          certifications: CertificationSummary[];
        };
        setConnections(nextConnections);
        setCertifications(certificationPayload.certifications);
        setConnectionId((current) =>
          current || nextConnections.find(
            (item) => item.enabled && (item.scopes ?? []).includes("chat"),
          )?.id || "",
        );
      } else {
        const [policyResponse, connectionResponse, receiptResponse] = await Promise.all([
          fetch("/api/router/workload-control/policies"),
          fetch("/api/router/connections"),
          fetch("/api/router/workload-control/receipts?limit=20"),
        ]);
        for (const response of [policyResponse, connectionResponse, receiptResponse]) {
          if (!response.ok) throw new Error(await readError(response));
        }
        const policyPayload = (await policyResponse.json()) as {
          policies: PolicySummary[];
        };
        const receiptPayload = (await receiptResponse.json()) as {
          runs: ReceiptRun[];
        };
        setPolicies(policyPayload.policies);
        setConnections((await connectionResponse.json()) as ConnectionSummary[]);
        setReceipts(receiptPayload.runs);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Workload 控制面。")
    } finally {
      setLoading(false);
    }
  }, [view]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedPolicy = useMemo(
    () => policies.find((policy) => policy.entry_id === entryId) ?? null,
    [entryId, policies],
  );

  useEffect(() => {
    if (!selectedPolicy) return;
    setEditableBindings(
      selectedPolicy.bindings.map((binding) => ({
        execution_shape: binding.execution_shape,
        model_id: binding.model_id,
        connection_id: binding.connection_id,
      })),
    );
  }, [selectedPolicy]);

  const eligibleConnections = useMemo(
    () =>
      connections.filter(
        (connection) =>
          connection.enabled && (connection.scopes ?? []).includes("chat"),
      ),
    [connections],
  );

  const runCertification = useCallback(async () => {
    if (!connectionId || !certificationModel.trim()) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(
        `/api/router/connections/${encodeURIComponent(connectionId)}/certifications/workloads`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": randomIdempotencyKey(),
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            execution_shape: certificationShape,
            model_id: certificationModel.trim(),
            acknowledge_billed_call: true,
            candidate_model_ids:
              certificationShape === "fusion_native"
                ? candidateModels.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
                : [],
            judge_model_id:
              certificationShape === "fusion_native" ? judgeModel.trim() : null,
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as CertificationSummary;
      setMessage(
        result.status === "passed"
          ? "执行形态资格已通过；它不代表任何 Agent 或 Workflow 入口已经启用。"
          : `资格未通过：${result.error_code ?? result.status}`,
      );
      setConfirmCertification(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资格认证失败。")
    } finally {
      setBusy(false);
    }
  }, [
    candidateModels,
    certificationModel,
    certificationShape,
    connectionId,
    csrfToken,
    judgeModel,
    load,
  ]);

  const addBinding = () => {
    const firstConnection = eligibleConnections[0]?.id ?? "";
    const shape = ENTRY_SHAPES[entryId][0];
    if (!firstConnection || !shape) return;
    setEditableBindings((current) => [
      ...current,
      { execution_shape: shape, model_id: "", connection_id: firstConnection },
    ]);
  };

  const savePolicy = useCallback(async () => {
    if (!selectedPolicy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(
        `/api/router/workload-control/policies/${entryId}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            expected_revision: selectedPolicy.revision,
            bindings: editableBindings.map((binding) => ({
              ...binding,
              model_id: binding.model_id.trim(),
            })),
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      setMessage(
        "入口 Binding 已原子保存；R6A 尚未接管真实数据面，因此不会改变现有 Agent/Workflow 调用。",
      );
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存入口策略失败。")
    } finally {
      setBusy(false);
    }
  }, [csrfToken, editableBindings, entryId, load, selectedPolicy]);

  const deactivate = useCallback(async () => {
    if (!selectedPolicy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(
        `/api/router/workload-control/policies/${entryId}/deactivate`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({ expected_revision: selectedPolicy.revision }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      setMessage("该入口已显式恢复 legacy；资格与脱敏 Receipt 仍保留。")
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "停用入口失败。")
    } finally {
      setBusy(false);
    }
  }, [csrfToken, entryId, load, selectedPolicy]);

  if (loading) {
    return (
      <section className="mb-6 rounded-lg border border-white/10 bg-ink-950/82 p-5 text-sm text-slate-300">
        <span className="inline-flex items-center gap-2">
          <LoaderCircle className="h-4 w-4 animate-spin" />正在读取 Workload 控制面…
        </span>
      </section>
    );
  }

  if (view === "certifications") {
    const fusionSelected = certificationShape === "fusion_native";
    const canConfirm = Boolean(
      connectionId &&
      certificationModel.trim() &&
      (!fusionSelected || (candidateModels.trim() && judgeModel.trim())),
    );
    return (
      <section className="mb-6 overflow-hidden rounded-lg border border-violet-300/15 bg-ink-950/82 shadow-prism">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 bg-violet-300/[0.04] px-5 py-5">
          <div>
            <p className="text-sm font-semibold text-violet-100">Agent / Workflow 资格</p>
            <h2 className="mt-2 text-xl font-semibold text-white">R6 非流式、JSON 与原生 Fusion 合同</h2>
            <p className="mt-2 max-w-[78ch] text-sm leading-6 text-slate-300">
              仅发送固定合成输入，自动刷新完整目录，并对精确连接、模型和执行形态留存脱敏资格。流式文本与工具调用继续复用 R5 认证。
            </p>
          </div>
          <button className="inline-flex items-center gap-2 rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200" onClick={() => void load()} type="button">
            <RefreshCw className="h-3.5 w-3.5" />刷新
          </button>
        </div>
        <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="space-y-4 rounded-lg border border-white/10 bg-white/[0.025] p-4">
            <label className="block text-sm text-slate-300">
              Managed 连接
              <select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>
                <option value="">选择连接</option>
                {eligibleConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name} · {connection.kind}</option>)}
              </select>
            </label>
            <label className="block text-sm text-slate-300">
              执行形态
              <select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" value={certificationShape} onChange={(event) => setCertificationShape(event.target.value as ExecutionShape)}>
                {NEW_CERTIFICATION_SHAPES.map((shape) => <option key={shape} value={shape}>{SHAPE_LABELS[shape]}</option>)}
              </select>
            </label>
            <label className="block text-sm text-slate-300">
              精确模型 ID
              <input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setCertificationModel(event.target.value)} placeholder={fusionSelected ? "openrouter/fusion" : "provider/model"} value={certificationModel} />
            </label>
            {fusionSelected ? <>
              <label className="block text-sm text-slate-300">有序候选模型（每行一个）<textarea className="mt-2 min-h-24 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 font-mono text-xs text-white" onChange={(event) => setCandidateModels(event.target.value)} value={candidateModels} /></label>
              <label className="block text-sm text-slate-300">裁判模型 ID<input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setJudgeModel(event.target.value)} value={judgeModel} /></label>
            </> : null}
            <button className="inline-flex items-center gap-2 rounded-full bg-violet-200 px-4 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40" disabled={!canConfirm || busy} onClick={() => setConfirmCertification(true)} type="button">
              <ShieldCheck className="h-4 w-4" />运行资格认证
            </button>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">最近资格</h3>
            <div className="mt-3 space-y-2">
              {certifications.length ? certifications.map((item) => <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3" key={item.certification_id ?? `${item.connection_id}-${item.execution_shape}`}>
                <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-semibold text-white">{item.connection_name} · {SHAPE_LABELS[item.execution_shape]}</p><span className={`rounded-full border px-2.5 py-1 text-xs ${item.status === "passed" ? "border-emerald-300/25 text-emerald-200" : "border-amber-300/25 text-amber-100"}`}>{item.status}</span></div>
                <p className="mt-2 break-all font-mono text-xs text-slate-300">{item.requested_model ?? "尚未运行"}</p>
                <p className="mt-1 text-xs text-slate-400">{item.error_code ?? (item.total_tokens != null ? `${item.total_tokens} tokens` : "不保存合成输入或模型正文")}</p>
              </div>) : <p className="rounded-lg border border-dashed border-white/10 p-4 text-sm text-slate-400">尚无 R6 Workload 资格记录。</p>}
            </div>
          </div>
        </div>
        {confirmCertification ? <div aria-modal="true" className="border-t border-amber-300/20 bg-amber-300/[0.05] p-5" role="dialog">
          <p className="text-sm font-semibold text-amber-100">确认一次真实付费资格调用</p>
          <p className="mt-2 text-sm leading-6 text-slate-300">将发送固定合成文本；不发送用户会话、文件或工具参数。最多一个 Provider POST、零自动重试，可能产生少量费用。</p>
          <div className="mt-3 flex gap-2"><button className="rounded-full bg-amber-200 px-4 py-2 text-sm font-semibold text-ink-950" disabled={busy} onClick={() => void runCertification()} type="button">确认并运行</button><button className="rounded-full border border-white/15 px-4 py-2 text-sm text-slate-200" onClick={() => setConfirmCertification(false)} type="button">取消</button></div>
        </div> : null}
        {error ? <p className="m-5 flex items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-300/10 p-3 text-sm text-rose-100"><CircleAlert className="h-4 w-4" />{error}</p> : null}
        {message ? <p className="m-5 flex items-center gap-2 rounded-lg border border-emerald-300/20 bg-emerald-300/10 p-3 text-sm text-emerald-100"><BadgeCheck className="h-4 w-4" />{message}</p> : null}
      </section>
    );
  }

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-sky-300/15 bg-ink-950/82 shadow-prism">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 bg-sky-300/[0.04] px-5 py-5">
        <div>
          <p className="text-sm font-semibold text-sky-100">Managed Workload 控制策略</p>
          <h2 className="mt-2 text-xl font-semibold text-white">R6A 入口、精确 Binding 与 Receipt 基础</h2>
          <p className="mt-2 max-w-[80ch] text-sm leading-6 text-slate-300">当前子轮次只建设控制面，不接管真实 Agent/Workflow 调用。每个后续入口需完成独立 PR、真实 Smoke 与人工批准后才能激活。</p>
        </div>
        <button className="inline-flex items-center gap-2 rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200" onClick={() => void load()} type="button"><RefreshCw className="h-3.5 w-3.5" />刷新</button>
      </div>
      <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,0.72fr)_minmax(0,1.28fr)]">
        <div>
          <label className="block text-sm text-slate-300">入口<select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" value={entryId} onChange={(event) => setEntryId(event.target.value as EntryId)}>{Object.entries(ENTRY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          {selectedPolicy ? <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.025] p-4">
            <div className="flex items-center justify-between gap-3"><span className="text-sm font-semibold text-white">{statusLabel(selectedPolicy.effective_status)}</span><span className="text-xs text-slate-400">revision {selectedPolicy.revision}</span></div>
            <dl className="mt-3 grid gap-2 text-xs text-slate-300"><div className="flex justify-between"><dt>部署开关</dt><dd>{selectedPolicy.feature_enabled ? "开启" : "关闭"}</dd></div><div className="flex justify-between"><dt>数据面接入</dt><dd>{selectedPolicy.data_plane_integrated ? "已接入" : "R6A 未接入"}</dd></div><div className="flex justify-between"><dt>人工批准</dt><dd>{selectedPolicy.approval_valid ? "有效" : "未生效"}</dd></div></dl>
            <div className="mt-3 space-y-1">{selectedPolicy.blocking_reason_codes.map((reason) => <p className="text-xs text-amber-100" key={reason}>· {REASON_LABELS[reason] ?? reason}</p>)}</div>
          </div> : null}
        </div>
        <div>
          <div className="flex items-center justify-between"><h3 className="flex items-center gap-2 text-sm font-semibold text-white"><Route className="h-4 w-4" />精确模型 Binding</h3><button className="rounded-full border border-white/15 px-3 py-1.5 text-xs text-slate-200 disabled:opacity-40" disabled={!eligibleConnections.length} onClick={addBinding} type="button">添加 Binding</button></div>
          <div className="mt-3 space-y-3">{editableBindings.map((binding, index) => <div className="grid gap-2 rounded-lg border border-white/10 bg-white/[0.025] p-3 md:grid-cols-[0.8fr_1.2fr_1fr_auto]" key={`${index}-${binding.execution_shape}`}>
            <select aria-label={`Binding ${index + 1} 执行形态`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 text-sm text-white" value={binding.execution_shape} onChange={(event) => setEditableBindings((current) => current.map((item, position) => position === index ? { ...item, execution_shape: event.target.value as ExecutionShape } : item))}>{ENTRY_SHAPES[entryId].map((shape) => <option key={shape} value={shape}>{SHAPE_LABELS[shape]}</option>)}</select>
            <input aria-label={`Binding ${index + 1} 模型 ID`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 font-mono text-xs text-white" onChange={(event) => setEditableBindings((current) => current.map((item, position) => position === index ? { ...item, model_id: event.target.value } : item))} placeholder="精确模型 ID" value={binding.model_id} />
            <select aria-label={`Binding ${index + 1} 连接`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 text-sm text-white" value={binding.connection_id} onChange={(event) => setEditableBindings((current) => current.map((item, position) => position === index ? { ...item, connection_id: event.target.value } : item))}>{eligibleConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}</select>
            <button className="rounded-full border border-rose-300/20 px-3 py-2 text-xs text-rose-100" onClick={() => setEditableBindings((current) => current.filter((_, position) => position !== index))} type="button">移除</button>
          </div>)}{!editableBindings.length ? <p className="rounded-lg border border-dashed border-white/10 p-4 text-sm text-slate-400">尚未配置；没有 Binding 时不能激活。</p> : null}</div>
          <div className="mt-4 flex flex-wrap gap-2"><button className="rounded-full bg-sky-200 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={busy || !selectedPolicy || editableBindings.some((item) => !item.model_id.trim() || !item.connection_id)} onClick={() => void savePolicy()} type="button">保存 Binding</button>{selectedPolicy?.configured_status !== "legacy" ? <button className="rounded-full border border-white/15 px-4 py-2 text-sm text-slate-200" disabled={busy} onClick={() => void deactivate()} type="button">显式恢复 Legacy</button> : null}<button className="rounded-full border border-amber-300/20 px-4 py-2 text-sm text-amber-100 opacity-50" disabled type="button">激活 Managed（等待对应数据面子轮次）</button></div>
        </div>
      </div>
      <div className="border-t border-white/10 p-5"><h3 className="text-sm font-semibold text-white">最近脱敏 Receipt</h3><div className="mt-3 space-y-2">{receipts.length ? receipts.map((run) => <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3" key={run.run_id}><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm text-white">{ENTRY_LABELS[run.entry_id]} · {run.status}</p><span className="text-xs text-slate-400">{run.calls.length} 次逻辑调用</span></div><p className="mt-1 text-xs text-slate-400">仅保存模型、序号、状态、指标与用量；不保存 Prompt、消息、模型正文或工具参数。</p></div>) : <p className="rounded-lg border border-dashed border-white/10 p-4 text-sm text-slate-400">R6A 未接管数据面，因此当前没有 Workload Receipt。</p>}</div></div>
      {error ? <p className="m-5 flex items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-300/10 p-3 text-sm text-rose-100"><CircleAlert className="h-4 w-4" />{error}</p> : null}
      {message ? <p className="m-5 flex items-center gap-2 rounded-lg border border-emerald-300/20 bg-emerald-300/10 p-3 text-sm text-emerald-100"><BadgeCheck className="h-4 w-4" />{message}</p> : null}
    </section>
  );
}
