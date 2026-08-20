import { useCallback, useEffect, useMemo, useState } from "react";
import { BadgeCheck, CircleAlert, LoaderCircle, RefreshCw } from "lucide-react";

type CertificationStatus =
  | "not_run"
  | "running"
  | "passed"
  | "failed"
  | "uncertain"
  | "stale";

interface CertificationSummary {
  certification_id?: string | null;
  connection_id: string;
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

const STATUS_LABELS: Record<CertificationStatus, string> = {
  not_run: "尚未认证",
  running: "认证进行中",
  passed: "核心文本 Chat 已通过",
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
  csrfToken,
}: {
  connectionId: string;
  connectionEnabled: boolean;
  csrfToken: string;
}) {
  const [featureEnabled, setFeatureEnabled] = useState(true);
  const [contractVersion, setContractVersion] = useState("");
  const [summary, setSummary] = useState<CertificationSummary | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [running, setRunning] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState("");

  const loadStatus = useCallback(async () => {
    try {
      const response = await fetch("/api/router/certifications/chat");
      if (!response.ok) throw new Error(await readError(response));
      const payload = (await response.json()) as CertificationListResponse;
      setFeatureEnabled(payload.enabled);
      setContractVersion(payload.contract_version);
      setSummary(
        payload.certifications.find(
          (certification) => certification.connection_id === connectionId,
        ) ?? null,
      );
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "无法读取认证状态。");
    }
  }, [connectionId]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

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
            acknowledge_billed_call: true,
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as CertificationSummary;
      setSummary(result);
      setMessage(STATUS_LABELS[result.status]);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Chat 认证未完成。");
      await loadStatus();
    } finally {
      setRunning(false);
    }
  }, [connectionId, csrfToken, loadStatus, selectedModel]);

  const blockedMessage = useMemo(() => {
    const reason = summary?.blocked_reason;
    return reason ? BLOCKED_LABELS[reason] ?? reason : "";
  }, [summary]);
  const canRun = Boolean(
    featureEnabled && connectionEnabled && selectedModel && summary?.can_run !== false,
  );

  return (
    <div className="mt-3 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.04] p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs font-semibold text-cyan-100">newAPI 文本 Chat 认证</p>
          <p className="mt-1 text-[11px] leading-5 text-slate-400">
            只验证核心文本 Chat 契约，不代表默认数据面已就绪。
          </p>
        </div>
        <span className="rounded-full bg-white/[0.06] px-2 py-1 text-[10px] text-slate-300">
          {summary ? STATUS_LABELS[summary.status] : "读取状态中"}
        </span>
      </div>

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
          运行 Chat 认证
        </button>
      </div>

      {blockedMessage ? <p className="mt-2 text-xs text-amber-200">{blockedMessage}</p> : null}
      {message ? <p className="mt-2 text-xs text-slate-300" role="status">{message}</p> : null}
      {summary?.error_code ? (
        <p className="mt-2 text-xs text-rose-200">错误码：{summary.error_code}</p>
      ) : null}
      {contractVersion ? (
        <p className="mt-2 font-mono text-[10px] text-slate-500">{contractVersion}</p>
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
                  <li>发送固定合成文本 “Reply with OK.”，不会发送用户聊天内容。</li>
                  <li>最多一次真实 Chat 请求，不自动重试。</li>
                  <li>temperature=0，max_tokens=16。</li>
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
