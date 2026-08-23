import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  LoaderCircle,
  LockKeyhole,
  Plus,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";

type ChatCapability = "chat_text" | "chat_tools" | "chat_file_output";
type ChatControlMode =
  | "legacy"
  | "newapi_preferred"
  | "newapi_required_default";

interface ConnectionSummary {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  scopes: string[];
}

interface RouteSummary {
  capability: ChatCapability;
  connection_ids: string[];
}

interface QualificationSummary {
  capability: ChatCapability;
  connection_id: string;
  connection_name: string;
  provider_kind: string;
  model_id: string;
  valid: boolean;
  reason_code: string;
}

interface PolicyResponse {
  contract_version: string;
  feature_enabled: boolean;
  data_plane_integrated: boolean;
  configured_mode: ChatControlMode;
  effective_mode: ChatControlMode;
  auto_enabled: boolean;
  revision: number;
  policy_fingerprint: string;
  stable_model_ids: string[];
  routes: RouteSummary[];
  qualifications: QualificationSummary[];
}

interface GateResponse {
  feature_enabled: boolean;
  configured_mode: ChatControlMode;
  ready: boolean;
  required_activation_available: boolean;
  required_active: boolean;
  epoch_status?: string | null;
  hard_failure_code?: string | null;
  minimum_request_count: number;
  minimum_observed_days: number;
  minimum_success_rate: number;
  request_count: number;
  success_count: number;
  hard_failure_count: number;
  observed_days: number;
  success_rate?: number | null;
  model_progress: Array<{
    model_id: string;
    success_count: number;
    minimum_success_count: number;
    ready: boolean;
  }>;
  required_drills: string[];
  approval_recorded: boolean;
  acceptance_evidence_complete: boolean;
  acceptance_evidence: Array<{
    evidence_kind: string;
    passed: boolean;
    observed_at: string;
  }>;
  blocking_reason_codes: string[];
}

const DRILL_LABELS: Record<string, string> = {
  auth_failure: "401 / 403 认证失败",
  http_429: "429 限流",
  http_5xx: "5xx 上游故障",
  connect_timeout: "连接超时",
  read_timeout: "读取超时",
  empty_stream: "空流",
  invalid_sse: "非法 SSE",
  stream_interrupted: "流中断",
  service_restart: "服务重启",
  credential_invalid: "凭据失效",
  data_plane_offline: "数据面离线",
  preferred_fallback: "preferred 派发前回退",
};

const CAPABILITIES: Array<{
  value: ChatCapability;
  label: string;
  description: string;
}> = [
  {
    value: "chat_text",
    label: "普通文本",
    description: "首个目标必须是 newAPI；备用只允许在派发前选择。",
  },
  {
    value: "chat_tools",
    label: "工具调用",
    description: "独立认证，不继承普通文本资格。",
  },
  {
    value: "chat_file_output",
    label: "受控文件输出",
    description: "仅对应现有 allowlisted 文件输出合同。",
  },
];

const EMPTY_ROUTES: Record<ChatCapability, string[]> = {
  chat_text: [],
  chat_tools: [],
  chat_file_output: [],
};

async function readError(response: Response) {
  if (response.status === 401) return "管理会话已失效，请重新配对。";
  if (response.status === 409) {
    try {
      const payload = await response.json();
      if (typeof payload?.detail?.message === "string") return payload.detail.message;
    } catch {
      return "策略资格或 revision 已变化，请刷新后重试。";
    }
  }
  try {
    const payload = await response.json();
    if (typeof payload?.detail?.message === "string") return payload.detail.message;
  } catch {
    // Keep the stable fallback.
  }
  return "Provider Chat 控制策略操作未完成。";
}

function routesFromPolicy(policy: PolicyResponse) {
  const result: Record<ChatCapability, string[]> = {
    chat_text: [],
    chat_tools: [],
    chat_file_output: [],
  };
  for (const route of policy.routes) result[route.capability] = route.connection_ids;
  return result;
}

export default function ProviderChatControlSettings({
  csrfToken,
}: {
  csrfToken: string;
}) {
  const [policy, setPolicy] = useState<PolicyResponse | null>(null);
  const [gate, setGate] = useState<GateResponse | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [mode, setMode] = useState<ChatControlMode>("legacy");
  const [autoEnabled, setAutoEnabled] = useState(false);
  const [stableModelsText, setStableModelsText] = useState("");
  const [routes, setRoutes] = useState<Record<ChatCapability, string[]>>(
    EMPTY_ROUTES,
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activating, setActivating] = useState(false);
  const [showActivation, setShowActivation] = useState(false);
  const [noOpenP0P1, setNoOpenP0P1] = useState(false);
  const [acknowledgeFailClosed, setAcknowledgeFailClosed] = useState(false);
  const [verifiedDrills, setVerifiedDrills] = useState<Record<string, boolean>>({});
  const [quotaDecrementVerified, setQuotaDecrementVerified] = useState(false);
  const [usageLogVerified, setUsageLogVerified] = useState(false);
  const [restartPersistenceVerified, setRestartPersistenceVerified] = useState(false);
  const [correlationReference, setCorrelationReference] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [policyResponse, gateResponse, connectionResponse] = await Promise.all([
        fetch("/api/router/chat-control/policy"),
        fetch("/api/router/chat-control/gate"),
        fetch("/api/router/connections"),
      ]);
      for (const response of [policyResponse, gateResponse, connectionResponse]) {
        if (!response.ok) throw new Error(await readError(response));
      }
      const nextPolicy = (await policyResponse.json()) as PolicyResponse;
      setPolicy(nextPolicy);
      setGate((await gateResponse.json()) as GateResponse);
      setConnections((await connectionResponse.json()) as ConnectionSummary[]);
      setMode(nextPolicy.configured_mode);
      setAutoEnabled(nextPolicy.auto_enabled);
      setStableModelsText(nextPolicy.stable_model_ids.join("\n"));
      setRoutes(routesFromPolicy(nextPolicy));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Chat 控制策略。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const eligibleConnections = useMemo(
    () =>
      connections.filter(
        (connection) =>
          connection.enabled && (connection.scopes ?? []).includes("chat"),
      ),
    [connections],
  );

  const activationComplete = useMemo(
    () =>
      Boolean(gate?.required_activation_available) &&
      noOpenP0P1 &&
      acknowledgeFailClosed &&
      Boolean(gate?.required_drills.every((drill) => verifiedDrills[drill])) &&
      quotaDecrementVerified &&
      usageLogVerified &&
      restartPersistenceVerified &&
      correlationReference.trim().length >= 8,
    [
      acknowledgeFailClosed,
      correlationReference,
      gate,
      noOpenP0P1,
      quotaDecrementVerified,
      restartPersistenceVerified,
      usageLogVerified,
      verifiedDrills,
    ],
  );

  const setRouteAt = (
    capability: ChatCapability,
    position: number,
    connectionId: string,
  ) => {
    setRoutes((current) => ({
      ...current,
      [capability]: current[capability].map((value, index) =>
        index === position ? connectionId : value,
      ),
    }));
  };

  const moveRoute = (
    capability: ChatCapability,
    position: number,
    direction: -1 | 1,
  ) => {
    setRoutes((current) => {
      const values = [...current[capability]];
      const target = position + direction;
      if (target < 0 || target >= values.length) return current;
      const sourceValue = values[position];
      const targetValue = values[target];
      if (sourceValue === undefined || targetValue === undefined) return current;
      values[position] = targetValue;
      values[target] = sourceValue;
      return { ...current, [capability]: values };
    });
  };

  const addRoute = (capability: ChatCapability) => {
    setRoutes((current) => {
      const candidate = eligibleConnections.find(
        (connection) => !current[capability].includes(connection.id),
      );
      if (!candidate) return current;
      return {
        ...current,
        [capability]: [...current[capability], candidate.id],
      };
    });
  };

  const removeRoute = (capability: ChatCapability, position: number) => {
    setRoutes((current) => ({
      ...current,
      [capability]: current[capability].filter((_, index) => index !== position),
    }));
  };

  const save = useCallback(async () => {
    if (!policy) return;
    setSaving(true);
    setError("");
    setMessage("");
    const stableModelIds = Array.from(
      new Set(
        stableModelsText
          .split(/\r?\n/)
          .map((value) => value.trim())
          .filter(Boolean),
      ),
    );
    try {
      const response = await fetch("/api/router/chat-control/policy", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-ModelMirror-CSRF": csrfToken,
        },
        body: JSON.stringify({
          expected_revision: policy.revision,
          mode,
          auto_enabled: autoEnabled,
          stable_model_ids: stableModelIds,
          routes: CAPABILITIES.map((capability) => ({
            capability: capability.value,
            connection_ids: routes[capability.value],
          })),
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      setMessage(
        "Chat 控制策略已原子保存；newapi_preferred 仅在部署总开关开启时按逐能力资格接管白名单请求。",
      );
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存策略失败。");
    } finally {
      setSaving(false);
    }
  }, [autoEnabled, csrfToken, load, mode, policy, routes, stableModelsText]);

  const activateRequired = useCallback(async () => {
    if (!policy || !gate?.required_activation_available || !activationComplete) return;
    setActivating(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(
        "/api/router/chat-control/gate/activate-required",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            expected_revision: policy.revision,
            no_open_p0_p1: noOpenP0P1,
            acknowledge_fail_closed: acknowledgeFailClosed,
            drills: Object.fromEntries(
              gate.required_drills.map((drill) => [drill, Boolean(verifiedDrills[drill])]),
            ),
            newapi_correlation_reference: correlationReference.trim(),
            quota_decrement_verified: quotaDecrementVerified,
            usage_log_verified: usageLogVerified,
            restart_persistence_verified: restartPersistenceVerified,
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      setCorrelationReference("");
      setShowActivation(false);
      setMessage(
        "newapi_required_default 已经人工激活；普通文本硬失败将保持 required 并失败关闭，不会自动回退。",
      );
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "required 激活失败。");
    } finally {
      setActivating(false);
    }
  }, [
    acknowledgeFailClosed,
    activationComplete,
    correlationReference,
    csrfToken,
    gate,
    load,
    noOpenP0P1,
    policy,
    quotaDecrementVerified,
    restartPersistenceVerified,
    usageLogVerified,
    verifiedDrills,
  ]);

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-cyan-300/15 bg-ink-950/82 shadow-prism">
      <div className="border-b border-white/10 bg-cyan-300/[0.04] px-5 py-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-cyan-100">Managed Chat 控制策略</p>
            <h2 className="mt-2 text-xl font-semibold text-white">R5E 资格证据与 required 门禁</h2>
            <p className="mt-2 max-w-[78ch] text-sm leading-6 text-slate-300">
              汇总当前资格纪元内的真实普通文本样本、逐模型成功数和故障演练证据。达到自动门槛后仍需人工完成 Go/No-Go，
              才能将普通文本从 newapi_preferred 切换为失败关闭的 newapi_required_default；Auto、工具、文件和多模态不会计入该门禁。
            </p>
          </div>
          <button
            className="inline-flex items-center gap-2 rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200"
            onClick={() => void load()}
            type="button"
          >
            <RefreshCw className="h-3.5 w-3.5" />刷新
          </button>
        </div>
      </div>

      {loading || !policy ? (
        <div className="flex items-center gap-2 p-5 text-sm text-slate-300">
          <LoaderCircle className="h-4 w-4 animate-spin" />正在读取策略…
        </div>
      ) : (
        <div className="space-y-5 p-5">
          {!policy.feature_enabled ? (
            <p className="rounded-lg border border-amber-300/20 bg-amber-300/[0.07] px-3 py-2 text-xs leading-5 text-amber-100">
              部署总开关 MODEL_CONTROL_CHAT_ENABLED 当前关闭；已保存配置不会接管 Chat。
            </p>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm font-semibold text-white">
              租户策略模式
              <select
                className="mt-2 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 text-sm text-white"
                onChange={(event) => setMode(event.target.value as ChatControlMode)}
                value={mode}
              >
                <option value="legacy">legacy（保持现有静态路径）</option>
                <option value="newapi_preferred">newapi_preferred（受部署总开关控制）</option>
                <option disabled value="newapi_required_default">
                  newapi_required_default（仅通过 Go/No-Go 激活）
                </option>
              </select>
            </label>
            <label className="flex items-center gap-3 rounded-lg border border-white/10 bg-white/[0.035] p-3 text-sm text-slate-200">
              <input
                checked={autoEnabled}
                className="accent-cyan-300"
                onChange={(event) => setAutoEnabled(event.target.checked)}
                type="checkbox"
              />
              启用 Auto 独立证据管道（不改变选路）
            </label>
          </div>

          <label className="block text-sm font-semibold text-white">
            稳定模型允许列表
            <textarea
              className="mt-2 min-h-28 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 font-mono text-xs text-white"
              onChange={(event) => setStableModelsText(event.target.value)}
              placeholder="每行一个精确模型 ID"
              value={stableModelsText}
            />
          </label>

          <div className="grid gap-4 xl:grid-cols-3">
            {CAPABILITIES.map((capability) => (
              <div className="rounded-lg border border-white/10 bg-white/[0.025] p-4" key={capability.value}>
                <p className="text-sm font-semibold text-white">{capability.label}</p>
                <p className="mt-1 text-xs leading-5 text-slate-400">{capability.description}</p>
                <div className="mt-3 space-y-2">
                  {routes[capability.value].map((connectionId, index) => (
                    <div className="flex items-center gap-1.5" key={`${capability.value}-${index}`}>
                      <span className="w-5 text-center text-xs text-slate-500">{index + 1}</span>
                      <select
                        aria-label={`${capability.label}目标 ${index + 1}`}
                        className="min-w-0 flex-1 rounded-lg border border-white/15 bg-slate-950 px-2 py-1.5 text-xs text-white"
                        onChange={(event) => setRouteAt(capability.value, index, event.target.value)}
                        value={connectionId}
                      >
                        {eligibleConnections.map((connection) => (
                          <option
                            disabled={
                              connection.id !== connectionId &&
                              routes[capability.value].includes(connection.id)
                            }
                            key={connection.id}
                            value={connection.id}
                          >
                            {connection.name} · {connection.kind}
                          </option>
                        ))}
                      </select>
                      <button aria-label="上移" disabled={index === 0} onClick={() => moveRoute(capability.value, index, -1)} type="button"><ArrowUp className="h-3.5 w-3.5" /></button>
                      <button aria-label="下移" disabled={index === routes[capability.value].length - 1} onClick={() => moveRoute(capability.value, index, 1)} type="button"><ArrowDown className="h-3.5 w-3.5" /></button>
                      <button aria-label="删除目标" onClick={() => removeRoute(capability.value, index)} type="button"><Trash2 className="h-3.5 w-3.5" /></button>
                    </div>
                  ))}
                  <button
                    className="inline-flex items-center gap-1.5 rounded-full border border-white/15 px-3 py-1.5 text-xs text-slate-200 disabled:opacity-45"
                    disabled={routes[capability.value].length >= eligibleConnections.length}
                    onClick={() => addRoute(capability.value)}
                    type="button"
                  >
                    <Plus className="h-3.5 w-3.5" />添加目标
                  </button>
                </div>
              </div>
            ))}
          </div>

          {policy.qualifications.length ? (
            <div className="rounded-lg border border-white/10 bg-black/10 p-4">
              <p className="text-xs font-semibold text-slate-300">当前资格快照</p>
              <ul className="mt-2 space-y-1 text-xs text-slate-400">
                {policy.qualifications.map((item) => (
                  <li key={`${item.capability}-${item.connection_id}-${item.model_id}`}>
                    {item.valid ? "有效" : "已失效"} · {item.capability} · {item.connection_name} · {item.model_id}
                    {!item.valid ? ` · ${item.reason_code}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {gate ? (
            <div
              className={`rounded-lg border p-4 text-xs ${
                gate.required_active
                  ? "border-emerald-300/20 bg-emerald-300/[0.05]"
                  : gate.epoch_status === "degraded"
                    ? "border-rose-300/20 bg-rose-300/[0.05]"
                    : "border-amber-300/15 bg-amber-300/[0.04]"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="flex items-center gap-2 font-semibold text-white">
                    {gate.required_active ? (
                      <CheckCircle2 className="h-4 w-4 text-emerald-300" />
                    ) : gate.epoch_status === "degraded" ? (
                      <AlertTriangle className="h-4 w-4 text-rose-300" />
                    ) : (
                      <LockKeyhole className="h-4 w-4 text-amber-200" />
                    )}
                    required 门禁：
                    {gate.required_active
                      ? "已人工激活"
                      : gate.epoch_status === "degraded"
                        ? "硬失败降级"
                        : gate.required_activation_available
                          ? "自动门槛已通过，等待人工 Go/No-Go"
                          : "正在收集资格证据"}
                  </p>
                  <p className="mt-2 text-slate-300">
                    {gate.request_count}/{gate.minimum_request_count} 次 · {Math.round(gate.observed_days * 10) / 10}/
                    {gate.minimum_observed_days} 天 · 成功 {gate.success_count} 次 · 成功率 {gate.success_rate == null ? "暂无样本" : `${Math.round(gate.success_rate * 1000) / 10}%`}
                    （门槛 {Math.round(gate.minimum_success_rate * 100)}%）
                  </p>
                  <p className="mt-1 text-slate-400">
                    硬失败 {gate.hard_failure_count} 次 · 资格纪元 {gate.epoch_status ?? "未建立"}
                    {gate.hard_failure_code ? ` · ${gate.hard_failure_code}` : ""}
                  </p>
                </div>
                {gate.required_activation_available && !gate.required_active ? (
                  <button
                    className="rounded-full border border-amber-200/30 px-3 py-1.5 font-semibold text-amber-100"
                    onClick={() => setShowActivation((current) => !current)}
                    type="button"
                  >
                    {showActivation ? "收起 Go/No-Go" : "开始 Go/No-Go"}
                  </button>
                ) : null}
              </div>

              {gate.model_progress.length ? (
                <div className="mt-3 grid gap-2 md:grid-cols-2">
                  {gate.model_progress.map((item) => (
                    <p className="rounded border border-white/10 px-2.5 py-2 text-slate-300" key={item.model_id}>
                      {item.ready ? "已达标" : "待补样本"} · {item.model_id} · {item.success_count}/{item.minimum_success_count}
                    </p>
                  ))}
                </div>
              ) : null}

              {gate.acceptance_evidence.length ? (
                <p className="mt-3 text-slate-300">
                  newAPI 有界验收：{gate.acceptance_evidence.map((item) => `${item.passed ? "通过" : "失败"} · ${item.evidence_kind}`).join("；")}
                </p>
              ) : null}

              {gate.blocking_reason_codes.length ? (
                <p className="mt-3 break-all text-slate-500">
                  阻塞：{gate.blocking_reason_codes.join(" · ")}
                </p>
              ) : null}

              {gate.epoch_status === "degraded" ? (
                <p className="mt-3 rounded border border-rose-300/15 px-3 py-2 leading-5 text-rose-100">
                  required 保持失败关闭，不会自动退回 preferred 或 legacy。管理员需先将策略显式退回 preferred，重新认证并建立新的资格纪元。
                </p>
              ) : null}

              {showActivation && gate.required_activation_available && !gate.required_active ? (
                <div className="mt-4 border-t border-white/10 pt-4">
                  <p className="font-semibold text-white">最终人工确认</p>
                  <p className="mt-1 max-w-[90ch] leading-5 text-amber-50/80">
                    激活后，普通文本必须经首选 newAPI；派发前不合格或派发后失败都不会调用第二个 Provider。此操作不启用 Auto，也不改变工具、文件或多模态路径。
                  </p>
                  <div className="mt-3 grid gap-2 md:grid-cols-2">
                    <label className="flex items-start gap-2 text-slate-200">
                      <input checked={noOpenP0P1} className="mt-0.5 accent-cyan-300" onChange={(event) => setNoOpenP0P1(event.target.checked)} type="checkbox" />
                      当前没有未解决 P0/P1
                    </label>
                    <label className="flex items-start gap-2 text-slate-200">
                      <input checked={acknowledgeFailClosed} className="mt-0.5 accent-cyan-300" onChange={(event) => setAcknowledgeFailClosed(event.target.checked)} type="checkbox" />
                      确认 required 的失败关闭语义
                    </label>
                    {gate.required_drills.map((drill) => (
                      <label className="flex items-start gap-2 text-slate-300" key={drill}>
                        <input
                          checked={Boolean(verifiedDrills[drill])}
                          className="mt-0.5 accent-cyan-300"
                          onChange={(event) => setVerifiedDrills((current) => ({ ...current, [drill]: event.target.checked }))}
                          type="checkbox"
                        />
                        故障演练：{DRILL_LABELS[drill] ?? drill}
                      </label>
                    ))}
                    <label className="flex items-start gap-2 text-slate-300">
                      <input checked={quotaDecrementVerified} className="mt-0.5 accent-cyan-300" onChange={(event) => setQuotaDecrementVerified(event.target.checked)} type="checkbox" />
                      newAPI 额度扣减差额已核对
                    </label>
                    <label className="flex items-start gap-2 text-slate-300">
                      <input checked={usageLogVerified} className="mt-0.5 accent-cyan-300" onChange={(event) => setUsageLogVerified(event.target.checked)} type="checkbox" />
                      newAPI Token 用量日志已关联
                    </label>
                    <label className="flex items-start gap-2 text-slate-300">
                      <input checked={restartPersistenceVerified} className="mt-0.5 accent-cyan-300" onChange={(event) => setRestartPersistenceVerified(event.target.checked)} type="checkbox" />
                      newAPI 重启后持久化已验证
                    </label>
                  </div>
                  <label className="mt-3 block max-w-2xl font-semibold text-slate-200">
                    newAPI 验收关联引用
                    <input
                      autoComplete="off"
                      className="mt-1.5 w-full rounded-lg border border-white/15 bg-slate-950 px-3 py-2 font-mono text-xs text-white"
                      onChange={(event) => setCorrelationReference(event.target.value)}
                      placeholder="输入可回查的验收批次或日志引用（不会保存原文）"
                      type="password"
                      value={correlationReference}
                    />
                  </label>
                  <button
                    className="mt-4 inline-flex items-center gap-2 rounded-full bg-rose-200 px-4 py-2 font-semibold text-rose-950 disabled:opacity-40"
                    disabled={!activationComplete || activating}
                    onClick={() => void activateRequired()}
                    type="button"
                  >
                    {activating ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <LockKeyhole className="h-4 w-4" />}
                    激活 newAPI 强制默认
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}

          <button
            className="inline-flex items-center gap-2 rounded-full bg-cyan-200 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-45"
            disabled={saving || mode === "newapi_required_default"}
            onClick={() => void save()}
            type="button"
          >
            {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            原子保存策略
          </button>
        </div>
      )}

      {error || message ? (
        <p
          className={`border-t px-5 py-3 text-sm ${error ? "border-rose-300/20 text-rose-100" : "border-emerald-300/20 text-emerald-100"}`}
          role="status"
        >
          {error || message}
        </p>
      ) : null}
    </section>
  );
}
