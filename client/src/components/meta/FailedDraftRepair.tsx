import { useEffect, useState } from "react";
import ModelDraftRepair from "./ModelDraftRepair";
import { normalizeRecipeRepairContract, recipeRepairOperations, type RecipeRepairContract } from "./recipeRepair";
import { Check, Plus, RefreshCw, SearchCheck, Trash2 } from "lucide-react";
import { authoringOperationSummary, normalizeAuthoringDiagnostics, normalizeGraphPatchPreview, type GraphPatchEnvelopeV1, type GraphPatchOperationV1, type GraphPatchPreview } from "./metaAuthoring";

type JsonObject = Record<string, unknown>;
interface RepairNode { ref: string; kind: string; title: string; config: JsonObject; inputs?: JsonObject[] | null; outputs?: JsonObject[] }
interface DiagnosticRun { stage?: string; issues?: JsonObject[]; phase_results?: JsonObject[]; control_proof_checks?: JsonObject[]; recipe_repair_progress?: JsonObject }
export interface RecoveryState {
  mode: "recovery";
  proposal_id: string;
  proposal_revision: number;
  can_author: boolean;
  graph_checksum: string;
  candidate_checksum: string;
  diagnostics: ReturnType<typeof normalizeAuthoringDiagnostics>;
  warnings: string[];
  recovery: {
    status: string; reason?: string; artifact_checksum: string; selected_attempt_id: number;
    source_format?: "graph_intent_v3" | "recipe_v1" | "recipe_resource_draft_v1" | "recipe_input_draft_v1";
    recipe?: JsonObject & { nodes: RepairNode[]; control_flow: unknown[] };
    semantic_repair?: RecipeRepairContract;
    intent: JsonObject & { nodes: RepairNode[] };
    node_contracts: Record<string, { config_fields?: string[]; input_ports?: string[]; output_ports?: string[] }>;
    current_diagnostics: DiagnosticRun;
    attempts: Array<{ attempt_id: number; diagnostic_subject: string; diagnostics: DiagnosticRun }>;
  };
}

const record = (value: unknown): value is JsonObject => typeof value === "object" && value !== null && !Array.isArray(value);
const checksum = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);

export function normalizeRecoveryState(value: unknown): RecoveryState | null {
  if (!record(value) || value.mode !== "recovery" || typeof value.proposal_id !== "string" || !record(value.recovery)) return null;
  const raw = value.recovery;
  const intent = record(raw.intent) ? raw.intent : {};
  const isResourceDraft = raw.source_format === "recipe_resource_draft_v1";
  const isInputDraft = raw.source_format === "recipe_input_draft_v1";
  const isRecipe = raw.source_format === "recipe_v1" || isResourceDraft || isInputDraft;
  const source = isRecipe && record(raw.recipe) ? raw.recipe : intent;
  const nodes = Array.isArray(source.nodes) ? source.nodes.filter((node): node is RepairNode => record(node) && typeof node.ref === "string" && typeof node.kind === "string" && typeof node.title === "string" && record(node.config)) : [];
  const knownFormat = raw.source_format === undefined || raw.source_format === "graph_intent_v3" || isRecipe;
  const protocolReadable = source.generation_protocol_version === 1 || (isInputDraft && source.generation_protocol_version === null);
  const complete = knownFormat && (!isResourceDraft || (Array.isArray(source.resources) && source.resources.length <= 40)) && (!isRecipe || (protocolReadable && Array.isArray(source.control_flow) && source.control_flow.length > 0 && source.control_flow.length <= 24)) && raw.executable === false && nodes.length > 0 && nodes.length === (source.nodes as unknown[])?.length && nodes.length <= (isRecipe ? 24 : 64) && checksum(raw.artifact_checksum) && checksum(value.graph_checksum) && checksum(value.candidate_checksum) && Number.isInteger(value.proposal_revision) && Number(value.proposal_revision) > 0;
  return {
    mode: "recovery", proposal_id: value.proposal_id, proposal_revision: Number(value.proposal_revision) || 0,
    can_author: complete && value.can_author === true && value.can_approve === false,
    graph_checksum: String(value.graph_checksum || ""), candidate_checksum: String(value.candidate_checksum || ""),
    diagnostics: normalizeAuthoringDiagnostics(value.diagnostics),
    warnings: Array.isArray(value.warnings) ? value.warnings.filter((item): item is string => typeof item === "string") : [],
    recovery: {
      status: complete ? String(raw.status) : "unavailable", reason: typeof raw.reason === "string" ? raw.reason : complete ? undefined : "未保留完整且校验一致的失败原图，不能从占位图恢复。",
      artifact_checksum: String(raw.artifact_checksum || ""), selected_attempt_id: Number(raw.selected_attempt_id) || 0,
      source_format: isInputDraft ? "recipe_input_draft_v1" : isResourceDraft ? "recipe_resource_draft_v1" : isRecipe ? "recipe_v1" : "graph_intent_v3",
      recipe: isRecipe && complete ? { ...source, nodes, control_flow: source.control_flow as unknown[] } : undefined,
      semantic_repair: raw.source_format === "recipe_v1" && complete ? normalizeRecipeRepairContract(raw.semantic_repair) : undefined,
      intent: { ...intent, nodes: isRecipe ? [] : nodes }, node_contracts: record(raw.node_contracts) ? raw.node_contracts as RecoveryState["recovery"]["node_contracts"] : {},
      current_diagnostics: record(raw.current_diagnostics) ? raw.current_diagnostics : {},
      attempts: Array.isArray(raw.attempts) ? raw.attempts.filter(record).slice(0, 2).map(item => ({ attempt_id: Number(item.attempt_id), diagnostic_subject: String(item.diagnostic_subject), diagnostics: record(item.diagnostics) ? item.diagnostics : {} })) : [],
    },
  };
}

const inputClass = "w-full min-w-0 rounded-md border border-white/20 bg-slate-900 px-3 py-2 text-sm text-slate-100 focus:border-cyan-300 focus:outline-none disabled:opacity-50";
const buttonClass = "inline-flex items-center justify-center gap-2 rounded-md border border-white/20 px-3 py-2 text-sm text-slate-100 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40";
const phaseLabels: Record<string, string> = { intent_parse: "结构解析", recipe_lowering: "生成描述展开", authorization: "授权与节点契约", resolve: "资源与类型解析", compile: "编译", publish_preflight: "发布预检", patch_apply: "修复操作", patch_parse: "修复格式", control_flow: "控制流", type_ports: "类型与端口", node_config: "节点配置" };
const phaseStates: Record<string, string> = { passed: "通过", failed: "失败", blocked: "未执行" };
const proofLabels: Record<string, string> = { structure: "控制结构", predicate_domains: "条件输入与真值", outcome_coverage: "分支可达性", terminal_coverage: "终点覆盖", data_availability: "路径数据可用性" };
const schemaLabels: Record<string, string> = { array: "输入引用数组", protocol_v1: "生成协议 V1", null: "null", missing: "缺失", invalid: "无效值" };

function Diagnostics({ value, onLocate }: { value: DiagnosticRun; onLocate: (ref: string) => void }) {
  const phases = Array.isArray(value.phase_results) ? value.phase_results : [];
  const issues = Array.isArray(value.issues) ? value.issues : [];
  const proofs = Array.isArray(value.control_proof_checks) ? value.control_proof_checks.filter(item => record(item) && Object.hasOwn(proofLabels, String(item.id))) : [];
  return <div className="space-y-2 text-xs text-slate-200">
    {value.recipe_repair_progress?.assessment === "not_rechecked" ? <p className="text-amber-100">修复未到达原失败检查，旧问题尚未复核，不能判定已消除。</p> : null}
    <div className="flex flex-wrap gap-x-4 gap-y-1">{phases.map((phase, index) => <span key={index}>{phaseLabels[String(phase.id)] || String(phase.id)}：{phaseStates[String(phase.status)] || String(phase.status)}</span>)}</div>
    {proofs.length > 0 ? <ul aria-label="控制流证明状态" className="space-y-1">{proofs.map((proof, index) => <li key={index} className="break-words">
      {proofLabels[String(proof.id)]}：{proof.status === "blocked" ? "被阻断" : phaseStates[String(proof.status)] || "不可验证"}
      {proof.status === "blocked" ? `（${proofLabels[String(proof.blocked_by)] || "前置检查"}尚未通过）` : ""}
    </li>)}</ul> : null}
    {issues.map((issue, index) => <div className="flex flex-wrap items-center gap-2 border-b border-white/10 py-1.5" key={index}>
      <span>{phaseLabels[String(issue.category)] || String(issue.category || "校验")} · <code>{String(issue.code || "CONTRACT_CHECK_FAILED")}</code></span>
      {typeof issue.node_ref === "string" ? <button type="button" className="text-cyan-200 underline" onClick={() => onLocate(issue.node_ref as string)}>定位 {issue.node_ref}</button> : null}
      {Array.isArray(issue.location) && issue.location.length ? <code className="break-all text-slate-300">{issue.location.join(".")}</code> : null}
      {record(issue.schema_detail) && Object.hasOwn(schemaLabels, String(issue.schema_detail.expected)) && Object.hasOwn(schemaLabels, String(issue.schema_detail.actual)) ? <span className="text-slate-200">要求：{schemaLabels[String(issue.schema_detail.expected)]}；实际：{schemaLabels[String(issue.schema_detail.actual)]}</span> : null}
      {record(issue.recipe_detail) && Array.isArray(issue.recipe_detail.first_location) ? <span className="break-all text-slate-300">首次出现：{issue.recipe_detail.first_location.join(".")}</span> : null}
    </div>)}
  </div>;
}

export default function FailedDraftRepair({ state, onApplied }: { state: RecoveryState; onApplied: () => Promise<void> }) {
  return state.recovery.source_format === "recipe_v1" || state.recovery.source_format === "recipe_resource_draft_v1" || state.recovery.source_format === "recipe_input_draft_v1"
    ? <RecipeDraftRepair key={`${state.proposal_id}:${state.proposal_revision}:${state.recovery.source_format}`} state={state} onApplied={onApplied} />
    : <IntentDraftRepair state={state} onApplied={onApplied} />;
}

function RecipeDraftRepair({ state, onApplied }: { state: RecoveryState; onApplied: () => Promise<void> }) {
  const recipe = state.recovery.recipe;
  const isResourceDraft = state.recovery.source_format === "recipe_resource_draft_v1";
  const isInputDraft = state.recovery.source_format === "recipe_input_draft_v1";
  const semantic = state.recovery.semantic_repair;
  const fieldLabel = semantic ? "语义修复操作 JSON" : isInputDraft ? "节点输入引用 JSON" : isResourceDraft ? "Agent 资源绑定 JSON" : "控制结构 JSON";
  const original = JSON.stringify((semantic ? [] : isInputDraft ? recipe?.nodes.map(node => ({ node_ref: node.ref, inputs: node.inputs })) : isResourceDraft ? recipe?.resources : recipe?.control_flow) || [], null, 2);
  const [flowText, setFlowText] = useState(original);
  const [confirmProtocol, setConfirmProtocol] = useState(false);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState("");
  const [located, setLocated] = useState("");
  const [preview, setPreview] = useState<{ result: GraphPatchPreview; body: JsonObject; diagnosis: DiagnosticRun } | null>(null);
  const locked = !state.can_author || busy || conflict || !recipe;
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (flowText !== original || confirmProtocol) event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [flowText, original, confirmProtocol]);
  async function post(path: string, body: JsonObject) {
    const response = await fetch(`/api/meta-agent/authoring/proposals/${state.proposal_id}/patch/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const value: unknown = await response.json();
    if (response.status === 409) setConflict(true);
    if (!response.ok || !record(value)) {
      const detail = record(value) && record(value.detail) ? value.detail : {};
      throw new Error(typeof detail.message === "string" ? detail.message : "生成描述修复失败，候选未修改。");
    }
    return value;
  }
  async function validate() {
    setPreview(null); setError("");
    try {
      let flow: unknown;
      try { flow = JSON.parse(flowText); } catch { throw new Error(`${fieldLabel} 格式无效。`); }
      let operations: JsonObject[];
      if (semantic) {
        const parsed = recipeRepairOperations(flow, semantic);
        if (!parsed) throw new Error("修复操作不符合服务端字段契约，或超过 16 项、64 KiB 限制。");
        operations = parsed;
      } else if (isInputDraft) {
        if (!Array.isArray(flow) || flow.length > 24) throw new Error("节点输入引用必须为最多 24 项的数组。");
        const seen = new Set<string>();
        operations = confirmProtocol ? [{ op: "confirm_recipe_protocol" }] : [];
        for (const row of flow) {
          if (!record(row) || Object.keys(row).some(key => key !== "node_ref" && key !== "inputs") || typeof row.node_ref !== "string" || !(row.inputs === null || (Array.isArray(row.inputs) && row.inputs.length <= 50))) throw new Error("每项必须仅包含 node_ref 和 inputs 数组或 null。");
          const node = recipe?.nodes.find(item => item.ref === row.node_ref);
          if (!node || seen.has(row.node_ref)) throw new Error("输入修复包含重复或不存在的节点引用。");
          seen.add(row.node_ref);
          if (JSON.stringify(row.inputs) !== JSON.stringify(node.inputs)) operations.push({ op: "replace_recipe_inputs", node_ref: row.node_ref, inputs: row.inputs });
        }
        if (!operations.length) throw new Error("没有协议确认或输入引用变更。");
      } else {
        if (!Array.isArray(flow) || flow.length < (isResourceDraft ? 0 : 1) || flow.length > (isResourceDraft ? 40 : 24)) throw new Error(isResourceDraft ? "资源绑定必须为 0–40 项的数组。" : "控制结构必须为 1–24 项的数组。");
        operations = [isResourceDraft ? { op: "replace_recipe_resources", resources: flow } : { op: "replace_recipe_control_flow", control_flow: flow }];
      }
      const body = { mode: "recovery", artifact_checksum: state.recovery.artifact_checksum, patch: {
        protocol_version: semantic ? semantic.protocol_version : isInputDraft ? "recipe_inputs_v1" : isResourceDraft ? "recipe_resource_bindings_v1" : "recipe_control_flow_v1", proposal_revision: state.proposal_revision,
        expected_graph_checksum: state.graph_checksum, expected_candidate_checksum: state.candidate_checksum,
        operations,
      } };
      setBusy(true);
      const value = await post("preview", body);
      const result = normalizeGraphPatchPreview(value);
      if (!result) throw new Error("预览响应不完整，不允许应用。");
      setPreview({ result, body, diagnosis: record(value.recovery_diagnostics) ? value.recovery_diagnostics : {} });
    } catch (exc) { setError(exc instanceof Error ? exc.message : "校验失败。"); }
    finally { setBusy(false); }
  }
  async function apply() {
    if (!preview?.result.can_apply || locked) return;
    setBusy(true); setError("");
    let applied = false;
    try {
      await post("apply", { ...preview.body, preview_checksum: preview.result.preview_checksum });
      applied = true; setConflict(true); setPreview(null);
      await onApplied();
    } catch (exc) { setError(applied ? "修复已写回，重新加载失败；请重新加载候选，不要重复应用。" : exc instanceof Error ? exc.message : "应用失败。"); }
    finally { setBusy(false); }
  }
  return <section aria-label="失败生成描述修复" className="min-w-0 space-y-4 p-4 text-slate-100">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 pb-3"><h3 className="text-base font-semibold">失败生成描述修复</h3><span className="text-xs text-amber-100">尝试 {state.recovery.selected_attempt_id} · 尚未生成有效图 · 不可执行</span></div>
    <p className="text-sm text-amber-100">{semantic ? "受限语义修复已开放。任务、资源、模型身份与授权锁定；未通过全部校验不可应用。" : isInputDraft ? "协议或输入引用未通过校验。配置、任务、资源、控制结构与授权已锁定。" : isResourceDraft ? "资源种类校验未通过。仅可修正 Agent 资源绑定；节点自有资源、任务、控制结构与授权保持原值。" : "本次仅可修改控制结构。节点配置、任务、资源与授权保持原值；仍有其他错误时不可应用。"}</p>
    {state.recovery.reason ? <p role="status" className="text-sm text-amber-100">{state.recovery.reason}</p> : null}
    {state.warnings.map(warning => <p key={warning} className="text-xs text-amber-100">{warning}</p>)}
    <Diagnostics value={state.recovery.current_diagnostics} onLocate={setLocated} />
    {state.diagnostics.map((item, index) => <p className="break-words text-xs text-rose-100" key={index}>{item.message}</p>)}
    <details><summary className="cursor-pointer text-sm">原始生成与修复诊断</summary><div className="mt-2 space-y-3">{state.recovery.attempts.map(attempt => <div key={attempt.attempt_id}><p className="mb-2 text-xs">尝试 {attempt.attempt_id} · 生成描述</p><Diagnostics value={attempt.diagnostics} onLocate={setLocated} /></div>)}</div></details>
    {recipe ? <>
      {semantic ? <details><summary className="cursor-pointer text-sm">操作字段契约</summary><dl className="mt-2 space-y-2 break-words text-xs">{Object.entries(semantic.edit_contract.operations).map(([op, fields]) => <div key={op}><dt><code>{op}</code></dt><dd>必填：{fields.required_fields.join("、")}；可选：{fields.optional_fields.join("、") || "无"}</dd></div>)}</dl><pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(semantic.edit_contract, null, 2)}</pre><details><summary className="cursor-pointer text-xs">完整配置 Schema</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(semantic.operations_schema, null, 2)}</pre></details></details> : null}
      {isInputDraft && recipe.generation_protocol_version === null ? <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={locked} checked={confirmProtocol} onChange={event => { setConfirmProtocol(event.target.checked); setPreview(null); setError(""); }} />确认采用生成协议 V1</label> : null}
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <label className="block min-w-0 text-sm">{fieldLabel}<textarea aria-label={fieldLabel} spellCheck={false} disabled={locked} className={`${inputClass} mt-2 h-96 font-mono`} value={flowText} onChange={event => { setFlowText(event.target.value); setPreview(null); setError(""); }} /></label>
        <div className="min-w-0 space-y-2"><h4 className="text-sm font-semibold">原始节点 · 只读</h4><ul className="divide-y divide-white/10 text-xs">{recipe.nodes.map(node => <li key={node.ref} className={`break-all py-2 ${located === node.ref ? "bg-cyan-950 text-cyan-100" : ""}`}><span className="font-medium">{node.title}</span> · <code>{node.ref}</code> · {node.kind}</li>)}</ul><details><summary className="cursor-pointer text-xs">原始配置与输入</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({ nodes: recipe.nodes, final_output: recipe.final_output, ...(isResourceDraft || semantic ? { control_flow: recipe.control_flow } : {}) }, null, 2)}</pre></details></div>
      </div>
      <div className="flex flex-wrap gap-2"><button className={buttonClass} type="button" disabled={locked || (flowText === original && !confirmProtocol)} onClick={() => void validate()}><SearchCheck size={16} />{busy ? "处理中" : "校验并预览修复"}</button><button className={buttonClass} type="button" disabled={locked || !preview?.result.can_apply} onClick={() => void apply()}><Check size={16} />确认应用修复</button></div>
      {preview ? <div className="space-y-2 border-t border-white/10 pt-3" aria-live="polite"><h4 className="text-sm font-semibold">{preview.result.can_apply ? "修复预览通过，尚未写回" : "修复未通过，候选未修改"}</h4><Diagnostics value={preview.diagnosis} onLocate={setLocated} />{preview.result.diagnostics.map((item, index) => <p key={index} className="break-words text-xs text-rose-100">{item.message}</p>)}<pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify(preview.body.patch, null, 2)}</pre></div> : null}
    </> : null}
    {recipe && semantic ? <ModelDraftRepair state={state} disabled={locked} hasLocalChanges={flowText !== original}
      onLoad={operations => { setFlowText(JSON.stringify(operations, null, 2)); setPreview(null); setError(""); }} onBusyChange={setBusy} onConflict={() => setConflict(true)} /> : null}
    {error ? <p role="alert" className="text-sm text-rose-100">{error}</p> : null}
    {conflict ? <p className="text-xs text-amber-100">当前版本已锁定，请重新加载候选。</p> : null}
  </section>;
}

function IntentDraftRepair({ state, onApplied }: { state: RecoveryState; onApplied: () => Promise<void> }) {
  const { nodes } = state.recovery.intent;
  const [selected, setSelected] = useState(nodes[0]?.ref || "");
  const node = nodes.find(item => item.ref === selected);
  const [title, setTitle] = useState(node?.title || "");
  const [config, setConfig] = useState(JSON.stringify(node?.config || {}, null, 2));
  const [operationsText, setOperationsText] = useState("[]");
  const [operation, setOperation] = useState("connect_data");
  const [source, setSource] = useState(nodes[0]?.ref || "input");
  const [target, setTarget] = useState(nodes[1]?.ref || nodes[0]?.ref || "");
  const [sourcePort, setSourcePort] = useState("result");
  const [targetPort, setTargetPort] = useState("records");
  const [outcome, setOutcome] = useState("success");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [preview, setPreview] = useState<{ result: GraphPatchPreview; patch: GraphPatchEnvelopeV1; diagnosis: DiagnosticRun } | null>(null);
  useEffect(() => { setTitle(node?.title || ""); setConfig(JSON.stringify(node?.config || {}, null, 2)); }, [node]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (operationsText !== "[]") event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [operationsText]);
  const locked = !state.can_author || busy || conflict;
  function changeOperations(text: string) { setOperationsText(text); setPreview(null); setError(""); }
  function readOperations(): GraphPatchOperationV1[] {
    let parsed: unknown;
    try { parsed = JSON.parse(operationsText); } catch { throw new Error("修复操作 JSON 格式无效，请检查引号、逗号和括号。"); }
    if (!Array.isArray(parsed) || parsed.length > 64 || !parsed.every(item => record(item) && typeof item.op === "string")) throw new Error("修复操作必须为最多 64 项的 JSON 数组。");
    return parsed as GraphPatchOperationV1[];
  }
  function append(change: GraphPatchOperationV1) {
    try { changeOperations(JSON.stringify([...readOperations(), change], null, 2)); } catch (exc) { setError(exc instanceof Error ? exc.message : "修复操作格式无效。"); }
  }
  function addNodeChange() {
    let value: unknown;
    try { value = JSON.parse(config); } catch { setError("节点配置 JSON 格式无效，请检查引号、逗号和括号。"); return; }
    if (!record(value)) { setError("节点配置必须为 JSON 对象。"); return; }
    append({ op: "update_node", ref: selected, title, config: value });
  }
  async function post(path: string, body: unknown): Promise<JsonObject> {
    const response = await fetch(`/api/meta-agent/authoring/proposals/${state.proposal_id}/patch/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const value: unknown = await response.json();
    if (response.status === 409) setConflict(true);
    if (!response.ok || !record(value)) {
      const detail = record(value) && record(value.detail) ? value.detail : {};
      throw new Error(typeof detail.message === "string" ? detail.message : "修复请求失败，未修改候选。");
    }
    return value;
  }
  async function validate() {
    setPreview(null); setError("");
    try {
      const operations = readOperations();
      if (!operations.length) throw new Error("尚未添加修复操作。");
      const patch: GraphPatchEnvelopeV1 = { protocol_version: 1, proposal_revision: state.proposal_revision, expected_graph_checksum: state.graph_checksum, expected_candidate_checksum: state.candidate_checksum, operations };
      setBusy(true);
      const value = await post("preview", { mode: "recovery", artifact_checksum: state.recovery.artifact_checksum, patch });
      const result = normalizeGraphPatchPreview(value);
      if (!result) throw new Error("预览响应不完整，不允许应用。");
      setPreview({ result, patch, diagnosis: record(value.recovery_diagnostics) ? value.recovery_diagnostics : {} });
    } catch (exc) { setError(exc instanceof Error ? exc.message : "校验失败。"); }
    finally { setBusy(false); }
  }
  async function apply() {
    if (!preview?.result.can_apply || conflict) return;
    setBusy(true); setError("");
    let applied = false;
    try {
      await post("apply", { mode: "recovery", artifact_checksum: state.recovery.artifact_checksum, patch: preview.patch, preview_checksum: preview.result.preview_checksum });
      applied = true;
      setPreview(null); setOperationsText("[]"); setConflict(true);
      await onApplied();
    } catch (exc) { setError(applied ? "修复已写回，但重新加载失败。请重新加载候选，不要重复应用。" : exc instanceof Error ? exc.message : "应用失败。"); }
    finally { setBusy(false); }
  }
  let operations: GraphPatchOperationV1[] = [];
  try { operations = readOperations(); } catch { /* The local draft may be invalid while being edited. */ }
  const sourceNode = nodes.find(item => item.ref === source);
  const targetNode = nodes.find(item => item.ref === target);
  const sourcePorts = source === "input" ? ["user_input", "conversation_history"] : [...new Set([...(sourceNode?.outputs || []).map(item => String(item.port)), ...(state.recovery.node_contracts[sourceNode?.kind || ""]?.output_ports || [])])];
  const targetPorts = [...new Set([...(targetNode?.inputs || []).map(item => String(item.port)), ...(state.recovery.node_contracts[targetNode?.kind || ""]?.input_ports || [])])];
  return <section aria-label="失败候选人工修复" className="min-w-0 space-y-4 p-4 text-slate-100">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 pb-3">
      <h3 className="text-base font-semibold">失败候选人工修复</h3>
      <span className="text-xs text-amber-100">原图尝试 {state.recovery.selected_attempt_id || "未保留"} · 未应用 · 不可执行</span>
    </div>
    {state.recovery.reason ? <p role="status" className="text-sm text-amber-100">{state.recovery.reason}</p> : null}
    {state.warnings.map(warning => <p key={warning} className="text-xs text-amber-100">{warning}</p>)}
    <details><summary className="cursor-pointer text-sm">原始生成与修复诊断</summary><div className="mt-2 space-y-4">{state.recovery.attempts.map(attempt => <div key={attempt.attempt_id}><p className="mb-2 text-xs text-slate-300">尝试 {attempt.attempt_id} · {attempt.diagnostic_subject === "repair_input" ? "修复输入图，未产生新图" : attempt.diagnostic_subject === "intent_result" ? "本次产出图" : "未解析产物"}</p><Diagnostics value={attempt.diagnostics} onLocate={setSelected} /></div>)}</div></details>
    <div className="border-b border-white/10 pb-3"><h4 className="mb-2 text-sm font-medium">当前原图复核</h4><Diagnostics value={state.recovery.current_diagnostics} onLocate={setSelected} />{state.diagnostics.map((item, index) => <p className="mt-2 break-words text-xs text-rose-100" key={index}>{item.message}</p>)}</div>
    {nodes.length ? <>
      <div className="grid min-w-0 gap-4 lg:grid-cols-2">
        <fieldset disabled={locked} className="min-w-0 space-y-3">
          <legend className="mb-2 text-sm font-semibold">节点配置</legend>
          <label className="block text-xs">节点<select className={`${inputClass} mt-1`} value={selected} onChange={event => setSelected(event.target.value)}>{nodes.map(item => <option key={item.ref} value={item.ref}>{item.title} · {item.ref}</option>)}</select></label>
          <label className="block text-xs">节点标题<input className={`${inputClass} mt-1`} value={title} onChange={event => { setTitle(event.target.value); setPreview(null); }} /></label>
          <label className="block text-xs">语义配置 JSON<textarea aria-label="节点语义配置 JSON" className={`${inputClass} mt-1 h-40 font-mono`} value={config} spellCheck={false} onChange={event => { setConfig(event.target.value); setPreview(null); }} /></label>
          <button type="button" className={buttonClass} onClick={addNodeChange}><Plus size={15} />加入节点修改</button>
        </fieldset>
        <fieldset disabled={locked} className="min-w-0 space-y-3">
          <legend className="mb-2 text-sm font-semibold">连接修复</legend>
          <label className="block text-xs">连接操作<select className={`${inputClass} mt-1`} value={operation} onChange={event => setOperation(event.target.value)}>{Object.entries({ connect_data: "添加数据连接", disconnect_data: "移除数据连接", connect_control: "添加控制连接", disconnect_control: "移除控制连接" }).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <div className="grid grid-cols-2 gap-2"><label className="min-w-0 text-xs">来源节点<select className={`${inputClass} mt-1`} value={source} onChange={event => setSource(event.target.value)}><option value="input">工作流输入</option>{nodes.map(item => <option key={item.ref} value={item.ref}>{item.ref}</option>)}</select></label><label className="min-w-0 text-xs">目标节点<select className={`${inputClass} mt-1`} value={target} onChange={event => setTarget(event.target.value)}>{nodes.map(item => <option key={item.ref} value={item.ref}>{item.ref}</option>)}</select></label></div>
          {operation.endsWith("data") ? <div className="grid grid-cols-2 gap-2"><label className="min-w-0 text-xs">来源端口<input list="recovery-source-ports" className={`${inputClass} mt-1`} value={sourcePort} onChange={event => setSourcePort(event.target.value)} /><datalist id="recovery-source-ports">{sourcePorts.map(port => <option key={port} value={port} />)}</datalist></label><label className="min-w-0 text-xs">目标端口<input list="recovery-target-ports" className={`${inputClass} mt-1`} value={targetPort} onChange={event => setTargetPort(event.target.value)} /><datalist id="recovery-target-ports">{targetPorts.map(port => <option key={port} value={port} />)}</datalist></label></div> : <label className="block text-xs">语义结果<select className={`${inputClass} mt-1`} value={outcome} onChange={event => setOutcome(event.target.value)}>{["success", "error", "matched", "unmatched", ...Array.from({ length: 8 }, (_, i) => `case_${i + 1}`), "default"].map(item => <option key={item}>{item}</option>)}</select></label>}
          <button type="button" className={buttonClass} onClick={() => append({ op: operation, source_ref: source, target_ref: target, ...(operation.endsWith("data") ? { source_port: sourcePort, target_port: targetPort } : { outcome_ref: outcome }) })}><Plus size={15} />加入连接修改</button>
          <details><summary className="cursor-pointer text-xs">原图数据与控制连接</summary><pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({ data: nodes.flatMap(item => (item.inputs || []).map(binding => ({ target_ref: item.ref, ...binding }))), control: state.recovery.intent.control_edges, final_output: state.recovery.intent.final_output }, null, 2)}</pre></details>
        </fieldset>
      </div>
      <div className="border-t border-white/10 pt-3"><div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-semibold">待应用改动 · {operations.length}/64</h4><button title="清空本地修复操作" aria-label="清空本地修复操作" type="button" className={buttonClass} disabled={locked} onClick={() => changeOperations("[]")}><Trash2 size={15} /></button></div>
        <ol className="my-2 space-y-1 break-words text-xs text-slate-200">{operations.map((item, index) => <li key={index}>{index + 1}. {authoringOperationSummary(item)}</li>)}</ol>
        <details><summary className="cursor-pointer text-xs">高级：编辑完整修复操作</summary><textarea aria-label="修复操作 JSON" disabled={locked} className={`${inputClass} mt-2 h-48 font-mono`} value={operationsText} onChange={event => changeOperations(event.target.value)} spellCheck={false} /></details>
        <p className="mt-2 text-xs text-amber-100">未应用的修改仅在当前编辑区，刷新不会保存。人工编辑与预览不调用模型、不执行工作流。</p>
      </div>
      <div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} disabled={locked} onClick={() => void validate()}><SearchCheck size={16} />{busy ? "处理中" : "校验并预览修复"}</button><button type="button" className={buttonClass} disabled={locked || !preview?.result.can_apply} onClick={() => void apply()}><Check size={16} />确认应用修复</button></div>
      {preview ? <div className="space-y-2 border-t border-white/10 pt-3" aria-live="polite"><h4 className="text-sm font-semibold">{preview.result.can_apply ? "修复预览通过，尚未写回" : "修复未通过，候选未修改"}</h4><Diagnostics value={preview.diagnosis} onLocate={setSelected} />{preview.result.diagnostics.map((item, index) => <p key={index} className="break-words text-xs text-rose-100">{item.message}</p>)}<pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all text-xs text-slate-200">{JSON.stringify(preview.patch.operations, null, 2)}</pre></div> : null}
    </> : null}
    {nodes.length ? <ModelDraftRepair state={state} disabled={locked} hasLocalChanges={operationsText.trim() !== "[]"}
      onLoad={operations => changeOperations(JSON.stringify(operations, null, 2))} onBusyChange={setBusy} onConflict={() => setConflict(true)} /> : null}
    {error ? <p role="alert" className="break-words text-sm text-rose-100">{error}</p> : null}
    {conflict ? <p className="flex items-center gap-2 text-xs text-amber-100"><RefreshCw size={14} />当前版本已锁定，请使用“重新加载候选”。</p> : null}
  </section>;
}
