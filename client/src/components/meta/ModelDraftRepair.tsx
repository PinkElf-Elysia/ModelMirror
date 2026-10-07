import { useState } from "react";
import { FileSearch, RefreshCw, Send, WandSparkles, FilePenLine } from "lucide-react";
import { models } from "../../data/models";
import { normalizeGraphPatchEnvelope, type GraphPatchOperationV1 } from "./metaAuthoring";
import type { RecoveryState } from "./FailedDraftRepair";
import { normalizeRecipeRepairPatch } from "./recipeRepair";

type Json = Record<string, unknown>;
const record = (value: unknown): value is Json => value !== null && typeof value === "object" && !Array.isArray(value);
const digest = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const inputClass = "w-full min-w-0 rounded-md border border-white/20 bg-slate-900 px-3 py-2 text-sm text-slate-100 focus:border-cyan-300 focus:outline-none disabled:opacity-50";
const buttonClass = "inline-flex items-center justify-center gap-2 rounded-md border border-white/20 px-3 py-2 text-sm text-slate-100 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40";
const statuses: Record<string, string> = { dispatching: "派发中，勿重复调用", suggested: "建议已通过预检，尚未应用", invalid: "建议未通过校验", failed: "调用未完成", uncertain: "结果不确定，禁止自动重发", stale: "修复依据已过期" };
const reasons: Record<string, string> = {
  repair_response_model_mismatch: "返回模型与本次确认不一致，已拒绝建议；该调用可能已计费。",
  repair_call_failed: "传输或响应契约未通过，系统没有重试。",
  repair_patch_invalid: "返回内容不符合修改契约或安全保留要求，未生成可载入建议。",
  repair_patch_unrepresentable: "修改超出单次 Patch 的 64 项操作上限，请分批人工修复；不会自动拆分或追加调用。",
  repair_validation_failed: "建议已解析，但尚未通过原有工作流门禁。",
  repair_preview_passed: "本地预检通过，仍须人工载入、检查并重新预览。",
  repair_basis_changed: "调用期间提案、资源或授权已变化，旧建议不能应用。",
  repair_interrupted: "服务重启前派发尚未确定完成，不会自动重发。",
  repair_cancelled_unknown: "请求已中断，Provider 是否完成尚不可验证。",
  repair_result_unknown: "结果尚不可验证，请核对调用回执；系统没有重试。",
};

interface Confirmation {
  request: Json;
  authorization_checksum: string;
  authorization_token: string;
  expires_at: number;
  uncertain_previous: number;
  outbound: Json;
  model_id: string;
  route: Json;
  max_output_tokens: number;
  outbound_bytes: number;
  repair_protocol?: "generation_recipe_v1" | "graph_patch_v1" | "recipe_edits_v1";
}

function confirmation(value: Json, request: Json): Confirmation | null {
  if (value.can_dispatch !== true || value.max_calls !== 1 || !digest(value.authorization_checksum) ||
      typeof value.authorization_token !== "string" || !/^[a-f0-9]{32}\.[0-9]{10}\.[a-f0-9]{64}$/.test(value.authorization_token) ||
      !record(value.outbound) || !record(value.route) || value.model_id !== request.model_id || value.max_output_tokens !== request.max_output_tokens ||
      typeof value.expires_at !== "number" || value.expires_at * 1000 <= Date.now()) return null;
  return { request, authorization_checksum: String(value.authorization_checksum), authorization_token: value.authorization_token,
    expires_at: value.expires_at, uncertain_previous: Number(value.uncertain_previous) || 0, outbound: value.outbound,
    model_id: String(value.model_id), route: value.route, max_output_tokens: Number(value.max_output_tokens), outbound_bytes: Number(value.outbound_bytes) || 0,
    repair_protocol: value.repair_protocol === "generation_recipe_v1" || value.repair_protocol === "graph_patch_v1" || value.repair_protocol === "recipe_edits_v1" ? value.repair_protocol : undefined };
}

export default function ModelDraftRepair({ state, disabled, hasLocalChanges, onLoad, onBusyChange, onConflict }: {
  state: RecoveryState; disabled: boolean; hasLocalChanges: boolean;
  onLoad: (operations: GraphPatchOperationV1[]) => void; onBusyChange: (busy: boolean) => void; onConflict: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [model, setModel] = useState("");
  const [focus, setFocus] = useState("");
  const [limit, setLimit] = useState(12000);
  const [prepared, setPrepared] = useState<Confirmation | null>(null);
  const [consent, setConsent] = useState(false);
  const [uncertainConsent, setUncertainConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipts, setReceipts] = useState<Json[]>([]);
  const [result, setResult] = useState<Json | null>(null);
  const locked = disabled || busy || !state.can_author;
  const base = `/api/meta-agent/authoring/proposals/${encodeURIComponent(state.proposal_id)}/repair`;
  const reset = () => { setPrepared(null); setConsent(false); setUncertainConsent(false); setError(""); };
  function markBusy(value: boolean) { setBusy(value); onBusyChange(value); }
  async function api(path: string, body?: Json) {
    const response = await fetch(base + path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
    const value: unknown = await response.json();
    if (response.status === 409) { reset(); onConflict(); }
    if (!response.ok || !record(value)) {
      const detail = record(value) && record(value.detail) ? value.detail : {};
      throw new Error(typeof detail.message === "string" ? detail.message : "请求未完成，请检查回执和当前版本。");
    }
    return value;
  }
  async function history() {
    setError(""); markBusy(true);
    try { const data = await api("/receipts"); setReceipts(Array.isArray(data.attempts) ? data.attempts.filter(record) : []); }
    catch (exc) { setError(exc instanceof Error ? exc.message : "读取回执失败。"); }
    finally { markBusy(false); }
  }
  async function preflight() {
    reset(); markBusy(true);
    const request = { proposal_revision: state.proposal_revision, artifact_checksum: state.recovery.artifact_checksum,
      expected_graph_checksum: state.graph_checksum, expected_candidate_checksum: state.candidate_checksum,
      model_id: model, focus, max_output_tokens: limit };
    try {
      const data = confirmation(await api("/preflight", request), request);
      if (!data) throw new Error("预检响应不完整或已过期，未授权模型调用。");
      setPrepared(data);
    } catch (exc) { setError(exc instanceof Error ? exc.message : "预检失败。"); }
    finally { markBusy(false); }
  }
  async function execute() {
    if (!prepared || !consent || (prepared.uncertain_previous > 0 && !uncertainConsent) || locked) return;
    if (prepared.expires_at * 1000 <= Date.now()) { reset(); setError("确认已过期，请重新预检。"); return; }
    const request_id = `repair_${crypto.randomUUID().replaceAll("-", "")}`;
    const body = { ...prepared.request, request_id, authorization_checksum: prepared.authorization_checksum,
      authorization_token: prepared.authorization_token, acknowledge_external_send: true,
      acknowledge_uncertain_previous: uncertainConsent, max_calls: 1 };
    reset(); setResult(null); markBusy(true);
    try {
      const value = await api("/execute", body);
      setResult(value); setReceipts(previous => [...previous.filter(item => item.request_id !== request_id), value]);
    } catch (exc) {
      setError(`${exc instanceof Error ? exc.message : "连接中断。"} 本次结果可能不确定，请刷新调用回执；不会自动重发。`);
    } finally { markBusy(false); }
  }
  async function viewReceipt(id: string) {
    setError(""); markBusy(true);
    try { setResult(await api(`/receipts/${encodeURIComponent(id)}`)); }
    catch (exc) { setError(exc instanceof Error ? exc.message : "读取建议失败。"); }
    finally { markBusy(false); }
  }
  const patch = result ? state.recovery.source_format === "recipe_v1"
    ? state.recovery.semantic_repair ? normalizeRecipeRepairPatch(result.patch, state.recovery.semantic_repair) : null
    : normalizeGraphPatchEnvelope(result.patch) : null;
  const loadable = patch && result?.stale !== true && ["suggested", "invalid"].includes(String(result?.status)) &&
    patch.proposal_revision === state.proposal_revision && patch.expected_graph_checksum === state.graph_checksum &&
    patch.expected_candidate_checksum === state.candidate_checksum && patch.operations.length > 0;
  const summary = record(result?.preview_summary) ? result.preview_summary : {};
  const receipt = record(result?.receipt) ? result.receipt : {};
  return <section aria-label="定向模型修复" className="min-w-0 border-t border-white/10 pt-3">
    <button type="button" className={buttonClass} disabled={busy} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><WandSparkles size={16} />定向模型修复</button>
    {expanded ? <div className="mt-3 min-w-0 space-y-3">
      <fieldset disabled={locked} className="grid min-w-0 gap-3 md:grid-cols-2">
        <label className="min-w-0 text-xs">修复模型<select className={`${inputClass} mt-1`} style={{ colorScheme: "dark" }} value={model} onChange={event => { setModel(event.target.value); reset(); }}><option value="">请选择模型</option>{models.map(item => <option value={item.id} key={item.id}>{item.name} · {item.id}</option>)}</select></label>
        <label className="min-w-0 text-xs">输出 Token 上限<input className={`${inputClass} mt-1`} type="number" min={512} max={16000} step={512} value={limit} onChange={event => { setLimit(Number(event.target.value)); reset(); }} /></label>
        <label className="min-w-0 text-xs md:col-span-2">补充修复要求<textarea className={`${inputClass} mt-1 h-24`} maxLength={2000} value={focus} onChange={event => { setFocus(event.target.value); reset(); }} /></label>
      </fieldset>
      <div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} disabled={locked || !model || limit < 512 || limit > 16000} onClick={() => void preflight()}><FileSearch size={16} />查看本次外发内容</button><button type="button" className={buttonClass} disabled={busy} onClick={() => void history()}><RefreshCw size={16} />刷新调用回执</button></div>
      {prepared ? <div className="min-w-0 space-y-3 border-y border-white/10 py-3" aria-label="模型调用确认">
        <p className="break-words text-sm">{String(prepared.route.label)} · {prepared.model_id}</p>
        <p className="text-xs text-slate-200">仅 1 次 completion · 输出上限 {prepared.max_output_tokens} Token · 外发 {prepared.outbound_bytes} 字节</p>
        {prepared.repair_protocol ? <p className="text-xs text-slate-200">修复协议：{prepared.repair_protocol === "recipe_edits_v1" ? "受限语义修改，未修改部分保留" : prepared.repair_protocol === "generation_recipe_v1" ? "语义描述，服务端生成修改" : "旧图 Patch"}</p> : null}
        <p className="text-xs text-amber-100">将发送失败图内的 Prompt、任务目标、诊断及授权资源安全元数据。不额外读取数据表记录或附件正文。</p>
        <details><summary className="cursor-pointer text-xs">完整外发正文</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(prepared.outbound, null, 2)}</pre></details>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={consent} disabled={locked} onChange={event => setConsent(event.target.checked)} /><span>我确认以上内容、模型和预算，授权本次付费调用</span></label>
        {prepared.uncertain_previous > 0 ? <label className="flex items-start gap-2 text-sm text-amber-100"><input type="checkbox" checked={uncertainConsent} disabled={locked} onChange={event => setUncertainConsent(event.target.checked)} /><span>已有 {prepared.uncertain_previous} 次结果不确定；我理解新调用可能再次计费</span></label> : null}
        <button type="button" className={buttonClass} disabled={locked || !consent || (prepared.uncertain_previous > 0 && !uncertainConsent)} onClick={() => void execute()}><Send size={16} />授权并执行一次修复</button>
      </div> : null}
      {busy ? <p role="status" className="text-sm text-slate-200">请求处理中，请勿重复提交。</p> : null}
      {receipts.length ? <ul aria-label="修复调用回执" className="space-y-2 text-xs">{receipts.map(item => <li key={String(item.request_id)} className="flex flex-wrap items-center gap-2 border-b border-white/10 py-2"><span>{statuses[String(item.status)] || "状态不可验证"} · {String(item.model_id || "")}</span><button className={buttonClass} disabled={busy} onClick={() => void viewReceipt(String(item.request_id))}>查看建议与回执</button></li>)}</ul> : null}
      {result ? <div className="min-w-0 space-y-2 border-t border-white/10 pt-3" aria-live="polite">
        <h4 className="text-sm font-medium">{result.stale ? "依据已变化，建议不可载入" : statuses[String(result.status)] || "状态不可验证"}</h4>
        <p className="break-all text-xs text-slate-300">{String(result.request_id || "")}</p>
        {reasons[String(result.reason_code)] ? <p className="break-words text-xs text-amber-100">{reasons[String(result.reason_code)]}</p> : null}
        <p className="text-xs text-slate-200">Provider 派发：{receipt.provider_dispatched === true ? "已确认" : receipt.provider_dispatched === false ? "未派发" : "不可验证"} · 收到响应：{receipt.response_received === true ? "是" : "未确认"} · 实际 Token：{receipt.usage_verified === true ? String(receipt.total_tokens) : "不可验证"}</p>
        {Array.isArray(summary.diagnostics) ? summary.diagnostics.filter(record).map((item, index) => <p className="break-words text-xs text-rose-100" key={index}>{String(item.message || item.code || "校验未通过")}</p>) : null}
        {patch ? <details><summary className="cursor-pointer text-xs">建议修改 · {patch.operations.length} 项</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(patch.operations, null, 2)}</pre></details> : null}
        <button className={buttonClass} disabled={locked || !loadable || hasLocalChanges} onClick={() => { if (loadable && patch && !hasLocalChanges) onLoad(patch.operations); }}><FilePenLine size={16} />载入人工编辑区</button>
        {hasLocalChanges ? <p className="text-xs text-amber-100">编辑区已有修改，模型建议不会覆盖。</p> : null}
      </div> : null}
      {error ? <p role="alert" className="break-words text-sm text-rose-100">{error}</p> : null}
    </div> : null}
  </section>;
}
