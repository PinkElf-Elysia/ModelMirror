import { useState } from "react";
import { Link } from "react-router-dom";
import { ExternalLink, ShieldAlert } from "lucide-react";
import type { WorkflowNode, WorkflowNodeData } from "../../types/workflow";

export interface WorkflowWriteGrant {
  table_id: string;
  operations: Array<"insert" | "update" | "delete">;
  writable_fields: string[];
  max_affected_rows: number;
}

export interface WorkflowWriteEdit {
  resource_id: string;
  config: Record<string, unknown>;
  inputs: Array<{ port: string; source_ref: string; source_port: string }>;
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function writeEditFromNode(data: WorkflowNodeData): WorkflowWriteEdit {
  const pending = object(data.plannerWriteIntentV2);
  const operation = data.kind.replace("data_table_", "");
  const config = Object.keys(object(pending.config)).length ? object(pending.config) : Object.keys(object(data.plannerAdapterConfigV1)).length ? object(data.plannerAdapterConfigV1) : {
    ...(operation !== "delete" ? { value_source: "literal", values: {} } : {}),
    ...(operation !== "insert" ? { filter: { ref: "selected", field: "record_id", operator: "is_null" }, max_affected_rows: 1 } : {}),
  };
  const inputs = Array.isArray(pending.inputs) ? pending.inputs : Array.isArray(data.plannerInputsV3) ? data.plannerInputsV3 : [];
  return {
    resource_id: typeof pending.resource_id === "string" ? pending.resource_id : String(data.tableId ?? ""),
    config,
    inputs: inputs.map((item) => {
      const input = object(item);
      return { port: String(input.port ?? ""), source_ref: String(input.source_ref ?? ""), source_port: String(input.source_port ?? "") };
    }),
  };
}

const controlClass = "modelmirror-form-control w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300";

function JsonObjectField({ label, value, onChange }: { label: string; value: Record<string, unknown>; onChange: (value: unknown) => void }) {
  const serialized = JSON.stringify(value, null, 2);
  const [draft, setDraft] = useState(serialized);
  const [error, setError] = useState("");
  return <label className="block space-y-1.5 text-xs text-slate-300">
    <span>{label}</span>
    <textarea aria-label={label} aria-invalid={Boolean(error)} className={`${controlClass} min-h-32 font-mono`} value={draft} onChange={(event) => {
      const text = event.target.value;
      setDraft(text);
      try {
        const parsed: unknown = JSON.parse(text);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || new TextEncoder().encode(text).length > 256 * 1024) throw new Error();
        setError(""); onChange(parsed as Record<string, unknown>);
      } catch {
        setError("请输入不超过 256 KiB 的合法 JSON 对象；当前编辑不可预览应用。");
        onChange(text);
      }
    }} />
    {error && <span role="alert" className="block text-rose-200">{error}</span>}
  </label>;
}

export default function WorkflowControlledWriteConfig({ data, nodes, grants, onChange }: {
  data: WorkflowNodeData; nodes: WorkflowNode[]; grants: WorkflowWriteGrant[];
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const edit = writeEditFromNode(data);
  const operation = data.kind.replace("data_table_", "") as "insert" | "update" | "delete";
  const available = grants.filter((grant) => grant.operations.includes(operation));
  const grant = available.find((item) => item.table_id === edit.resource_id);
  const update = (next: WorkflowWriteEdit) => onChange({ plannerWriteIntentV2: next });
  const updateConfig = (config: Record<string, unknown>) => update({ ...edit, config: { ...edit.config, ...config } });
  const sourceOptions = (port: string) => nodes.filter((node) => {
    if (!node.data.plannerRef) return false;
    return port === "records"
      ? ["data_table_query", "data_table_insert"].includes(node.data.kind) && node.data.tableId === edit.resource_id && node.data.plannerRef !== data.plannerRef
      : node.data.kind === "json_deserialize" && node.data.contractVersion === 2;
  });
  const sourceField = (port: "records" | "values", label: string) => <label className="block space-y-1.5 text-xs text-slate-300">
    <span>{label}</span>
    <select aria-label={label} className={controlClass} value={edit.inputs.find((item) => item.port === port)?.source_ref ?? ""} onChange={(event) => update({ ...edit, inputs: [...edit.inputs.filter((item) => item.port !== port), ...(event.target.value ? [{ port, source_ref: event.target.value, source_port: port === "records" ? "result" : "value" }] : [])] })}>
      <option value="">选择已编译的上游来源</option>
      {sourceOptions(port).map((node) => <option key={node.id} value={String(node.data.plannerRef)}>{node.data.title} · {String(node.data.plannerRef)}</option>)}
    </select>
  </label>;
  return <section className="space-y-4 border-t border-white/10 pt-4">
    <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-200"><ShieldAlert size={16} />受控写入 V2</h3>
    <label className="block space-y-1.5 text-xs text-slate-300"><span>操作授权表</span>
      <select aria-label="操作授权表" className={controlClass} value={edit.resource_id} onChange={(event) => update({ ...edit, resource_id: event.target.value, inputs: edit.inputs.filter((item) => item.port !== "records") })}>
        <option value="">选择已显式授权的数据表</option>
        {available.map((item) => <option key={item.table_id} value={item.table_id}>{item.table_id}</option>)}
      </select>
    </label>
    {edit.resource_id && <Link className="inline-flex items-center gap-1 text-xs text-cyan-200" target="_blank" to={`/data-tables/${encodeURIComponent(edit.resource_id)}`}>查看数据表 <ExternalLink size={12} /></Link>}
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-slate-300">
      <dt>固定 Schema</dt><dd>{data.pinnedSchemaVersion ? `v${data.pinnedSchemaVersion}` : "预览时由服务端固定"}</dd>
      <dt>允许字段</dt><dd className="break-words font-mono">{grant?.writable_fields.join("、") || (operation === "delete" ? "不写入字段值" : "未授权")}</dd>
      <dt>事务边界</dt><dd>单节点原子提交；后续失败不撤销先前写入</dd>
      <dt>异常策略</dt><dd>失败停止，无自动重试</dd>
    </dl>
    {operation !== "insert" && sourceField("records", "可信记录来源")}
    {operation !== "delete" && <>
      <label className="block space-y-1.5 text-xs text-slate-300"><span>业务值来源</span><select aria-label="业务值来源" className={controlClass} value={String(edit.config.value_source ?? "literal")} onChange={(event) => {
        const source = event.target.value;
        update({ ...edit, config: { ...edit.config, value_source: source, values: source === "literal" ? {} : null }, inputs: edit.inputs.filter((item) => item.port !== "values") });
      }}><option value="literal">固定对象</option><option value="input">已校验对象输入</option></select></label>
      {edit.config.value_source === "input" ? sourceField("values", "业务值校验来源") : <JsonObjectField key={`${edit.resource_id}:${operation}`} label="固定业务对象" value={object(edit.config.values)} onChange={(values) => updateConfig({ values })} />}
    </>}
    {operation !== "insert" && <>
      <JsonObjectField key={`filter:${edit.resource_id}`} label="缩小记录范围的条件" value={object(edit.config.filter)} onChange={(filter) => updateConfig({ filter })} />
      <label className="block space-y-1.5 text-xs text-slate-300"><span>最多影响行数</span><input aria-label="最多影响行数" className={controlClass} type="number" min={1} max={grant?.max_affected_rows ?? 1} value={Number(edit.config.max_affected_rows ?? 1)} onChange={(event) => updateConfig({ max_affected_rows: Number(event.target.value) })} /></label>
    </>}
    {!grant && <p role="alert" className="text-xs text-rose-200">该节点缺少当前 Proposal 的逐表操作授权，不能预览应用。</p>}
    <p className="text-xs leading-5 text-slate-400">记录身份和 revision 来自实际 Query/Insert 回执；预览与批准不会执行业务写入。</p>
  </section>;
}
