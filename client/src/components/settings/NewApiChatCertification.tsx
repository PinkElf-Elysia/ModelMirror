import { useCallback, useEffect, useMemo, useState } from "react";
import { BadgeCheck, CircleAlert, LoaderCircle, RefreshCw } from "lucide-react";

type CertificationStatus =
  | "not_run"
  | "running"
  | "passed"
  | "failed"
  | "uncertain"
  | "stale";
type ChatCapability = "chat_text" | "chat_tools" | "chat_file_output";

interface CertificationSummary {
  certification_id?: string | null;
  connection_id: string;
  capability: ChatCapability;
  status: CertificationStatus;
  can_run: boolean;
  blocked_reason?: string | null;
  warning_codes: string[];
  error_code?: string | null;
  requested_model?: string | null;
  actual_model?: string | null;
  ttft_ms?: number | null;
  e2e_ms?: number | null;
  completed_at?: string | null;
}

interface CertificationListResponse {
  enabled: boolean;
  contract_version: string;
  certifications: CertificationSummary[];
}

interface ModelsRefreshResponse {
  ok: boolean;
  model_ids: string[];
  model_count: number;
  checked_at: string;
  truncated: boolean;
  message: string;
}

interface CanaryModelStatus {
  model_id: string;
  certification_status: CertificationStatus;
  available: boolean;
  reason_code: string;
  paused: boolean;
  pause_reason?: string | null;
  baseline_overlap: boolean;
  completed_at?: string | null;
  certification_expires_at?: string | null;
}

interface CanaryConnectionStatus {
  connection_id: string;
  connection_name: string;
  eligible_connection: boolean;
  reason_code: string;
  models: CanaryModelStatus[];
}

interface CanaryRunSummary {
  run_id: string;
  connection_id: string;
  model_id: string;
  status: string;
  dispatched: boolean;
  result_class?: string | null;
  error_code?: string | null;
  ttft_ms?: number | null;
  e2e_ms?: number | null;
  total_tokens?: number | null;
  baseline_overlap: boolean;
  current_evidence: boolean;
  stale_reason?: string | null;
  created_at: string;
}

interface CanaryAggregate {
  connection_id: string;
  model_id: string;
  certification_id: string;
  total_runs: number;
  dispatched_runs: number;
  succeeded_runs: number;
  hard_failure_runs: number;
  transient_failure_runs: number;
  request_failure_runs: number;
  cancelled_runs: number;
  uncertain_runs: number;
  preflight_fallback_runs: number;
  success_rate?: number | null;
  average_ttft_ms?: number | null;
  average_e2e_ms?: number | null;
  total_tokens: number;
  baseline_overlap: boolean;
  last_completed_at?: string | null;
}

interface CanaryAdminResponse {
  contract_version: string;
  feature_enabled: boolean;
  policy_enabled: boolean;
  selected_connection_id?: string | null;
  consent_revision: string;
  certification_max_age_seconds?: number | null;
  connections: CanaryConnectionStatus[];
  runs: CanaryRunSummary[];
  aggregates: CanaryAggregate[];
}

const STATUS_LABELS: Record<CertificationStatus, string> = {
  not_run: "尚未认证",
  running: "认证进行中",
  passed: "当前能力已通过",
  failed: "认证失败",
  uncertain: "结果不确定，未自动重放",
  stale: "连接配置已变化，结果已过期",
};

const BLOCKED_LABELS: Record<string, string> = {
  provider_chat_certification_disabled: "部署已关闭 Chat 认证操作。",
  connection_disabled: "请先恢复该连接。",
  connection_chat_scope_required: "该连接未启用 Chat scope。",
  provider_model_catalog_not_checked: "请先刷新可认证模型。",
  provider_chat_certification_already_running: "该连接已有认证正在运行。",
};

const CERTIFICATION_ERROR_LABELS: Record<string, string> = {
  provider_chat_visible_text_budget_exhausted:
    "模型的可见文本预算已耗尽，未产生正文。",
};

const CAPABILITY_OPTIONS: Array<{
  value: ChatCapability;
  label: string;
  description: string;
}> = [
  {
    value: "chat_text",
    label: "普通文本",
    description: "验证非空文本流和安全终止。",
  },
  {
    value: "chat_tools",
    label: "工具调用",
    description: "只验证固定无副作用工具调用，不执行外部工具。",
  },
  {
    value: "chat_file_output",
    label: "受控文件输出",
    description: "只验证 allowlisted 文件工具合同，不创建或保存文件。",
  },
];

async function readError(response: Response) {
  if (response.status === 401) return "管理会话已失效，请重新配对。";
  if (response.status === 429) return "操作过于频繁，请稍后重试。";
  if (response.status === 503) return "Provider Chat 认证尚未配置或已关闭。";
  try {
    const payload = await response.json();
    if (typeof payload?.detail?.message === "string") return payload.detail.message;
  } catch {
    // Keep the stable fallback below.
  }
  return "操作未完成，请检查连接状态后重试。";
}

export default function NewApiChatCertification({
  connectionId,
  connectionEnabled,
  connectionKind,
  csrfToken,
}: {
  connectionId: string;
  connectionEnabled: boolean;
  connectionKind: string;
  csrfToken: string;
}) {
  const [featureEnabled, setFeatureEnabled] = useState(true);
  const [contractVersion, setContractVersion] = useState("");
  const [summary, setSummary] = useState<CertificationSummary | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [capability, setCapability] = useState<ChatCapability>("chat_text");
  const [refreshing, setRefreshing] = useState(false);
  const [running, setRunning] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState("");
  const [canary, setCanary] = useState<CanaryAdminResponse | null>(null);
  const [updatingCanary, setUpdatingCanary] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/router/certifications/chat");
      if (!response.ok) throw new Error(await readError(response));
      const payload = (await response.json()) as CertificationListResponse;
      setFeatureEnabled(payload.enabled);
      setContractVersion(payload.contract_version);
      setSummary(
        payload.certifications.find(
          (certification) =>
            certification.connection_id === connectionId &&
            certification.capability === capability,
        ) ?? null,
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "无法读取认证状态。");
    }
  }, [capability, connectionId]);

  const loadCanary = useCallback(async () => {
    if (connectionKind !== "newapi") {
      setCanary(null);
      return;
    }
    try {
      const response = await fetch("/api/router/canaries/chat?limit=50");
      if (!response.ok) throw new Error(await readError(response));
      setCanary((await response.json()) as CanaryAdminResponse);
    } catch (reason) {
      setMessage(
        reason instanceof Error ? reason.message : "无法读取 Chat 试运行状态。",
      );
    }
  }, [connectionKind]);

  useEffect(() => {
    void loadStatus();
    void loadCanary();
  }, [loadCanary, loadStatus]);

  const refreshModels = useCallback(async () => {
    setRefreshing(true);
    setMessage("");
    try {
      const response = await fetch(
        `/api/router/connections/${encodeURIComponent(connectionId)}/models/refresh`,
        { method: "POST", headers: { "X-ModelMirror-CSRF": csrfToken } },
      );
      if (!response.ok) throw new Error(await readError(response));
      const payload = (await response.json()) as ModelsRefreshResponse;
      if (!payload.ok) throw new Error(payload.message);
      setModels(payload.model_ids);
      setSelectedModel((current) =>
        payload.model_ids.includes(current) ? current : payload.model_ids[0] ?? "",
      );
      setMessage(
        `已刷新 ${payload.model_count} 个模型${payload.truncated ? "，列表仅显示前 500 个" : ""}。`,
      );
      await loadStatus();
    } catch (reason) {
      setModels([]);
      setSelectedModel("");
      setMessage(reason instanceof Error ? reason.message : "模型目录刷新失败。");
    } finally {
      setRefreshing(false);
    }
  }, [connectionId, csrfToken, loadStatus]);

  const runCertification = useCallback(async () => {
    if (!selectedModel) return;
    setRunning(true);
    setConfirming(false);
    setMessage("");
    try {
      const idempotencyKey =
        typeof crypto.randomUUID === "function"
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random()}`;
      const response = await fetch(
        `/api/router/connections/${encodeURIComponent(connectionId)}/certifications/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            model_id: selectedModel,
            capability,
            acknowledge_billed_call: true,
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as CertificationSummary;
      setSummary(result);
      setMessage(STATUS_LABELS[result.status]);
      await loadCanary();
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Chat 认证未完成。");
      await loadStatus();
    } finally {
      setRunning(false);
    }
  }, [capability, connectionId, csrfToken, loadCanary, loadStatus, selectedModel]);

  const updateCanary = useCallback(
    async (enabled: boolean) => {
      setUpdatingCanary(true);
      setMessage("");
      try {
        const response = await fetch("/api/router/canaries/chat", {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({ connection_id: connectionId, enabled }),
        });
        if (!response.ok) throw new Error(await readError(response));
        const payload = (await response.json()) as CanaryAdminResponse;
        setCanary(payload);
        setMessage(
          enabled
            ? "已设为唯一的 newAPI Chat 试运行连接。"
            : "已关闭 newAPI Chat 试运行策略。",
        );
      } catch (reason) {
        setMessage(reason instanceof Error ? reason.message : "试运行策略未更新。");
      } finally {
        setUpdatingCanary(false);
      }
    },
    [connectionId, csrfToken],
  );

  const blockedMessage = useMemo(() => {
    const reason = summary?.blocked_reason;
    return reason ? BLOCKED_LABELS[reason] ?? reason : "";
  }, [summary]);
  const canRun = Boolean(
    featureEnabled && connectionEnabled && selectedModel && summary?.can_run !== false,
  );
  const canaryConnection = canary?.connections.find(
    (connection) => connection.connection_id === connectionId,
  );
  const canarySelected = canary?.selected_connection_id === connectionId;
  const canaryEnabled = Boolean(canarySelected && canary?.policy_enabled);
  const canaryAggregates =
    canary?.aggregates?.filter(
      (aggregate) => aggregate.connection_id === connectionId,
    ) ?? [];

  return (
    <div className="mt-3 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.04] p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-cyan-100">Provider Chat 能力认证</p>
          <p className="mt-1 text-[11px] leading-5 text-slate-400">
            各能力独立验证；通过不代表默认数据面已就绪。
          </p>
        </div>
        <span className="rounded-full bg-white/[0.06] px-2 py-1 text-[10px] text-slate-300">
          {summary ? STATUS_LABELS[summary.status] : "读取状态中"}
        </span>
      </div>

      <label className="mt-3 block text-[11px] text-slate-400">
        认证能力
        <select
          aria-label="认证能力"
          className="mt-1 w-full rounded-lg border border-white/15 bg-slate-950 px-2 py-1.5 text-xs text-white"
          disabled={running}
          onChange={(event) => {
            setCapability(event.target.value as ChatCapability);
            setMessage("");
          }}
          value={capability}
        >
          {CAPABILITY_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <span className="mt-1 block leading-5">
          {CAPABILITY_OPTIONS.find((option) => option.value === capability)?.description}
        </span>
      </label>

      <div className="mt-3 flex flex-col gap-2 sm:flex-row">
        <button
          className="inline-flex items-center justify-center gap-1.5 rounded-full border border-white/15 px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-white/[0.07] disabled:opacity-45"
          disabled={!connectionEnabled || refreshing || running}
          onClick={() => void refreshModels()}
          type="button"
        >
          {refreshing ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          刷新可认证模型
        </button>
        <select
          aria-label="认证模型"
          className="min-w-0 flex-1 rounded-lg border border-white/15 bg-slate-950 px-2 py-1.5 text-xs text-white disabled:opacity-45"
          disabled={models.length === 0 || running}
          onChange={(event) => setSelectedModel(event.target.value)}
          value={selectedModel}
        >
          {models.length === 0 ? <option value="">请先刷新模型</option> : null}
          {models.map((model) => (
            <option key={model} value={model}>
              {model}
            </option>
          ))}
        </select>
        <button
          className="inline-flex items-center justify-center gap-1.5 rounded-full bg-cyan-200 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-cyan-100 disabled:opacity-45"
          disabled={!canRun || running}
          onClick={() => setConfirming(true)}
          type="button"
        >
          {running ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <BadgeCheck className="h-3.5 w-3.5" />}
          运行能力认证
        </button>
      </div>

      {blockedMessage ? <p className="mt-2 text-xs text-amber-200">{blockedMessage}</p> : null}
      {message ? <p className="mt-2 text-xs text-slate-300" role="status">{message}</p> : null}
      {summary?.error_code ? (
        <p className="mt-2 text-xs text-rose-200">
          {CERTIFICATION_ERROR_LABELS[summary.error_code]
            ? `${CERTIFICATION_ERROR_LABELS[summary.error_code]} 错误码：${summary.error_code}`
            : `错误码：${summary.error_code}`}
        </p>
      ) : null}
      {contractVersion ? (
        <p className="mt-2 font-mono text-[10px] text-slate-500">{contractVersion}</p>
      ) : null}

      {connectionKind === "newapi" ? (
      <div className="mt-3 border-t border-white/10 pt-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-xs font-semibold text-cyan-100">手动会话 Canary</p>
            <p className="mt-1 text-[11px] leading-5 text-slate-400">
              试运行证据，不代表默认数据面已就绪；不提供比例灰度或默认切换。
            </p>
          </div>
          <button
            className="inline-flex min-h-9 items-center rounded-full border border-white/15 px-3 text-xs font-semibold text-slate-100 transition hover:bg-white/[0.07] disabled:opacity-45"
            disabled={
              updatingCanary ||
              (!canaryEnabled &&
                (!canary?.feature_enabled ||
                  !canaryConnection?.eligible_connection ||
                  !canaryConnection.models.some(
                    (model) => model.certification_status === "passed",
                  )))
            }
            onClick={() => void updateCanary(!canaryEnabled)}
            type="button"
          >
            {updatingCanary ? (
              <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            {canaryEnabled ? "关闭试运行" : "设为唯一试运行连接"}
          </button>
        </div>
        {canary && !canary.feature_enabled ? (
          <p className="mt-2 text-xs text-amber-200">部署开关当前关闭。</p>
        ) : null}
        {canarySelected ? (
          <p className="mt-2 text-xs text-slate-300">
            当前策略：{canaryEnabled ? "已启用" : "已选择但未启用"}
          </p>
        ) : null}
        {canaryConnection?.models.length ? (
          <div className="mt-2 space-y-2">
            {canaryConnection.models.map((model) => (
              <div
                className="rounded-md border border-white/10 bg-black/10 px-2.5 py-2 text-[11px] text-slate-300"
                key={`${model.model_id}-${model.completed_at ?? "none"}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="break-all font-mono text-slate-200">{model.model_id}</span>
                  <span>{model.paused ? "已自动暂停" : model.certification_status}</span>
                </div>
                <p className="mt-1 text-slate-500">
                  {model.pause_reason ?? model.reason_code}
                  {model.baseline_overlap ? " · baseline_overlap" : ""}
                </p>
                {model.certification_expires_at ? (
                  <p className="mt-1 text-slate-500">
                    认证有效至 {new Date(model.certification_expires_at).toLocaleString()}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-xs text-slate-500">完成逐模型真实认证后才可启用。</p>
        )}
        {canaryAggregates.length ? (
          <div className="mt-3">
            <p className="text-[11px] font-semibold text-slate-300">
              当前认证证据窗口
            </p>
            <p className="mt-1 text-[10px] leading-4 text-slate-500">
              仅汇总当前连接配置与最新有效认证下的近期记录，不代表默认数据面资格。
            </p>
            <div className="mt-2 space-y-2">
              {canaryAggregates.map((aggregate) => (
                <div
                  className="rounded-md border border-white/10 bg-black/10 px-2.5 py-2 text-[11px] text-slate-400"
                  key={`${aggregate.connection_id}-${aggregate.model_id}-${aggregate.certification_id}`}
                >
                  <p className="break-all font-mono text-slate-200">
                    {aggregate.model_id}
                  </p>
                  <p className="mt-1">
                    {aggregate.total_runs} 次 · 成功率 {aggregate.success_rate == null
                      ? "—"
                      : `${Math.round(aggregate.success_rate * 100)}%`}
                    {` · 硬失败 ${aggregate.hard_failure_runs}`}
                    {` · 瞬时失败 ${aggregate.transient_failure_runs}`}
                    {` · 不确定 ${aggregate.uncertain_runs}`}
                  </p>
                  <p className="mt-1 text-slate-500">
                    TTFT {aggregate.average_ttft_ms == null
                      ? "—"
                      : `${Math.round(aggregate.average_ttft_ms)}ms`}
                    {` · E2E ${aggregate.average_e2e_ms == null
                      ? "—"
                      : `${Math.round(aggregate.average_e2e_ms)}ms`}`}
                    {` · ${aggregate.total_tokens} tokens`}
                    {aggregate.baseline_overlap ? " · baseline_overlap" : ""}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {canary?.runs.some((run) => run.connection_id === connectionId) ? (
          <div className="mt-3">
            <p className="text-[11px] font-semibold text-slate-400">近期脱敏证据</p>
            <ul className="mt-1 space-y-1 text-[11px] text-slate-500">
              {canary.runs
                .filter((run) => run.connection_id === connectionId)
                .slice(0, 5)
                .map((run) => (
                  <li key={run.run_id}>
                    {run.current_evidence
                      ? "当前窗口"
                      : `历史证据 · ${run.stale_reason ?? "认证窗口已变化"}`}
                    {` · ${run.model_id} · ${run.status}`}
                    {run.error_code ? ` · ${run.error_code}` : ""}
                    {run.e2e_ms != null ? ` · ${Math.round(run.e2e_ms)}ms` : ""}
                    {run.total_tokens != null ? ` · ${run.total_tokens} tokens` : ""}
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
        {canary?.contract_version ? (
          <p className="mt-2 font-mono text-[10px] text-slate-500">
            {canary.contract_version}
          </p>
        ) : null}
      </div>
      ) : null}

      {confirming ? (
        <div
          aria-labelledby={`chat-certification-title-${connectionId}`}
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
        >
          <div className="w-full max-w-lg rounded-xl border border-white/15 bg-slate-950 p-5 shadow-2xl">
            <div className="flex items-start gap-3">
              <CircleAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-200" />
              <div>
                <h3 className="font-semibold text-white" id={`chat-certification-title-${connectionId}`}>
                  确认一次真实模型调用
                </h3>
                <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-6 text-slate-300">
                  <li>
                    {capability === "chat_text"
                      ? "发送固定合成文本 Reply with OK."
                      : capability === "chat_tools"
                        ? "请求固定无副作用认证工具，但不会执行任何外部工具。"
                        : "请求固定的受控文件工具合同，但不会创建或保存文件。"}
                    不会发送用户聊天内容。
                  </li>
                  <li>最多一次真实 Chat 请求，不自动重试。</li>
                  <li>
                    temperature=0，max_tokens=64。
                  </li>
                  <li>本次调用可能产生少量额度费用。</li>
                </ul>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-3">
              <button
                className="rounded-full border border-white/15 px-4 py-2 text-sm text-slate-200"
                onClick={() => setConfirming(false)}
                type="button"
              >
                取消
              </button>
              <button
                className="rounded-full bg-amber-200 px-4 py-2 text-sm font-semibold text-slate-950"
                onClick={() => void runCertification()}
                type="button"
              >
                确认并运行一次
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
