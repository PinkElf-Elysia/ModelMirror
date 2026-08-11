import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArrowLeft,
  CheckCircle2,
  Database,
  Plus,
  RefreshCw,
  Save,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import {
  AgentTableDefinition,
  AgentTableDetail,
  AgentTableField,
  AgentTableFieldType,
  AgentTableRecord,
  JsonValue,
  requestAgentTableJson,
} from "../types/agentTables";


type DetailTab = "schema" | "records" | "versions";
type FieldDraft = AgentTableField & { _key: string; default_text: string };
type RecordDraft = Record<string, string | boolean>;

const fieldTypes: Array<{ id: AgentTableFieldType; label: string }> = [
  { id: "string", label: "文本" },
  { id: "integer", label: "整数" },
  { id: "number", label: "数值" },
  { id: "boolean", label: "布尔" },
  { id: "datetime", label: "日期时间" },
  { id: "json", label: "JSON" },
];

function draftKey() {
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function toFieldDraft(field: AgentTableField): FieldDraft {
  return {
    ...field,
    _key: field.field_id || draftKey(),
    default_text: field.has_default
      ? typeof field.default_value === "string"
        ? field.default_value
        : JSON.stringify(field.default_value)
      : "",
  };
}

function blankField(): FieldDraft {
  return {
    _key: draftKey(),
    field_id: "",
    name: "",
    label: "",
    description: "",
    data_type: "string",
    required: false,
    has_default: false,
    default_value: null,
    default_text: "",
  };
}

function parseTypedText(field: FieldDraft, text: string): JsonValue {
  if (field.data_type === "string" || field.data_type === "datetime") return text;
  if (field.data_type === "integer") {
    const value = Number(text);
    if (!Number.isInteger(value)) throw new Error(`${field.name || "字段"} 的默认值必须是整数`);
    return value;
  }
  if (field.data_type === "number") {
    const value = Number(text);
    if (!Number.isFinite(value)) throw new Error(`${field.name || "字段"} 的默认值必须是有限数值`);
    return value;
  }
  if (field.data_type === "boolean") return text === "true";
  try {
    return JSON.parse(text) as JsonValue;
  } catch {
    throw new Error(`${field.name || "JSON 字段"} 的默认值不是合法 JSON`);
  }
}

function serializeFields(fields: FieldDraft[]): AgentTableField[] {
  return fields.map(({ _key: _ignored, default_text, ...field }) => ({
    ...field,
    default_value: field.has_default ? parseTypedText(field as FieldDraft, default_text) : null,
  }));
}

function operationId(prefix: string) {
  return `${prefix}:${typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : Date.now()}`;
}

function recordDraft(fields: FieldDraft[], record?: AgentTableRecord): RecordDraft {
  return Object.fromEntries(
    fields.map((field) => {
      const value = record?.data[field.name];
      if (field.data_type === "boolean") return [field.name, value === true];
      if (value === undefined || value === null) return [field.name, ""];
      if (field.data_type === "json") return [field.name, JSON.stringify(value, null, 2)];
      return [field.name, String(value)];
    }),
  );
}

function parseRecord(fields: FieldDraft[], draft: RecordDraft): Record<string, JsonValue> {
  const result: Record<string, JsonValue> = {};
  for (const field of fields) {
    const raw = draft[field.name];
    if (field.data_type === "boolean") {
      result[field.name] = raw === true;
      continue;
    }
    const text = String(raw ?? "");
    if (!text && !field.required) continue;
    if (!text && field.required) throw new Error(`${field.label || field.name} 不能为空`);
    result[field.name] = parseTypedText(field, text);
  }
  return result;
}

function formatValue(value: JsonValue | undefined): string {
  if (value === undefined || value === null) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function DataTableDetailPage() {
  const { tableId = "" } = useParams();
  const [detail, setDetail] = useState<AgentTableDetail | null>(null);
  const [fields, setFields] = useState<FieldDraft[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [records, setRecords] = useState<AgentTableRecord[]>([]);
  const [selectedRecord, setSelectedRecord] = useState<AgentTableRecord | null>(null);
  const [recordForm, setRecordForm] = useState<RecordDraft>({});
  const [tab, setTab] = useState<DetailTab>("schema");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [validationIssues, setValidationIssues] = useState<string[]>([]);

  const loadRecords = useCallback(async () => {
    if (!tableId) return;
    try {
      const payload = await requestAgentTableJson<{ items: AgentTableRecord[] }>(
        `/api/data-tables/${tableId}/records?limit=200`,
      );
      setRecords(payload.items ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "记录加载失败");
    }
  }, [tableId]);

  const load = useCallback(async () => {
    if (!tableId) return;
    setLoading(true);
    try {
      const payload = await requestAgentTableJson<AgentTableDetail>(
        `/api/data-tables/${tableId}`,
      );
      setDetail(payload);
      setFields(payload.table.fields.map(toFieldDraft));
      setName(payload.table.name);
      setDescription(payload.table.description);
      setRecordForm(recordDraft(payload.table.fields.map(toFieldDraft)));
      setError("");
      if (payload.table.active_schema_version) await loadRecords();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "数据表加载失败");
    } finally {
      setLoading(false);
    }
  }, [loadRecords, tableId]);

  useEffect(() => {
    void load();
  }, [load]);

  const table = detail?.table;
  const activeSchema = useMemo(
    () =>
      detail?.schema_versions.find(
        (schema) => schema.version === table?.active_schema_version,
      ),
    [detail?.schema_versions, table?.active_schema_version],
  );
  const activeFields = useMemo(
    () => (activeSchema?.fields ?? []).map(toFieldDraft),
    [activeSchema],
  );
  const activeFieldIds = useMemo(
    () => new Set((activeSchema?.fields ?? []).map((field) => field.field_id)),
    [activeSchema],
  );

  function updateField(key: string, patch: Partial<FieldDraft>) {
    setFields((current) =>
      current.map((field) => (field._key === key ? { ...field, ...patch } : field)),
    );
  }

  async function saveDraft(): Promise<AgentTableDefinition> {
    if (!table) throw new Error("数据表尚未加载");
    const updated = await requestAgentTableJson<AgentTableDefinition>(
      `/api/data-tables/${table.table_id}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: table.draft_revision,
          name: name.trim(),
          description: description.trim(),
          fields: serializeFields(fields),
        }),
      },
    );
    setDetail((current) => (current ? { ...current, table: updated } : current));
    setFields(updated.fields.map(toFieldDraft));
    setRecordForm(recordDraft(updated.fields.map(toFieldDraft)));
    return updated;
  }

  async function handleSave() {
    setBusy(true);
    try {
      await saveDraft();
      setNotice("草稿已保存");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "草稿保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleValidate() {
    setBusy(true);
    try {
      const updated = await saveDraft();
      const result = await requestAgentTableJson<{
        valid: boolean;
        issues: Array<{ message: string }>;
      }>(`/api/data-tables/${updated.table_id}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision: updated.draft_revision }),
      });
      setValidationIssues(result.issues.map((issue) => issue.message));
      setNotice(result.valid ? "Schema 校验通过" : "Schema 仍有待修复项");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Schema 校验失败");
    } finally {
      setBusy(false);
    }
  }

  async function handlePublish() {
    setBusy(true);
    try {
      const updated = await saveDraft();
      await requestAgentTableJson(
        `/api/data-tables/${updated.table_id}/publish`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: updated.draft_revision }),
        },
      );
      setValidationIssues([]);
      setNotice("Schema 已发布为不可变版本");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Schema 发布失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleArchive() {
    if (!table || !window.confirm("归档后数据表将变为只读，确定继续吗？")) return;
    setBusy(true);
    try {
      await requestAgentTableJson(`/api/data-tables/${table.table_id}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision: table.draft_revision }),
      });
      await load();
      setNotice("数据表已归档");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "归档失败");
    } finally {
      setBusy(false);
    }
  }

  async function saveRecord(event: FormEvent) {
    event.preventDefault();
    if (!table) return;
    setBusy(true);
    try {
      const data = parseRecord(activeFields, recordForm);
      if (selectedRecord) {
        await requestAgentTableJson(
          `/api/data-tables/${table.table_id}/records/${selectedRecord.record_id}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              revision: selectedRecord.revision,
              data,
              operation_id: operationId("ui-update"),
            }),
          },
        );
      } else {
        await requestAgentTableJson(`/api/data-tables/${table.table_id}/records`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data, operation_id: operationId("ui-create") }),
        });
      }
      setSelectedRecord(null);
      setRecordForm(recordDraft(activeFields));
      await loadRecords();
      setNotice(selectedRecord ? "记录已更新" : "记录已创建");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "记录保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function deleteRecord(record: AgentTableRecord) {
    if (!table || !window.confirm("确定删除这条记录吗？")) return;
    setBusy(true);
    try {
      const op = encodeURIComponent(operationId("ui-delete"));
      await requestAgentTableJson(
        `/api/data-tables/${table.table_id}/records/${record.record_id}?revision=${record.revision}&operation_id=${op}`,
        { method: "DELETE" },
      );
      if (selectedRecord?.record_id === record.record_id) {
        setSelectedRecord(null);
        setRecordForm(recordDraft(activeFields));
      }
      await loadRecords();
      setNotice("记录已删除");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "记录删除失败");
    } finally {
      setBusy(false);
    }
  }

  function selectRecord(record: AgentTableRecord) {
    setSelectedRecord(record);
    setRecordForm(recordDraft(activeFields, record));
  }

  if (loading && !detail) {
    return (
      <PageContainer>
        <div className="py-24 text-center text-sm text-slate-500">正在加载数据表...</div>
      </PageContainer>
    );
  }

  if (!table) {
    return (
      <PageContainer>
        <div className="py-24 text-center">
          <p className="text-sm text-rose-200">{error || "数据表不存在"}</p>
          <Link className="mt-4 inline-block text-sm text-emerald-200" to="/data-tables">返回数据表列表</Link>
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <div className="mx-auto w-full max-w-[1500px]">
        <header className="border-b border-white/10 pb-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="min-w-0">
              <Link className="inline-flex items-center gap-1 text-xs font-semibold text-slate-400 hover:text-white" to="/data-tables">
                <ArrowLeft aria-hidden="true" size={14} />
                数据表
              </Link>
              <div className="mt-3 flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-emerald-300/15 text-emerald-200">
                  <Database aria-hidden="true" size={21} />
                </div>
                <div className="min-w-0">
                  <h1 className="truncate text-2xl font-semibold text-white">{table.name}</h1>
                  <p className="mt-1 text-xs text-slate-500">
                    {table.status === "archived" ? "已归档，只读" : table.active_schema_version ? `Schema v${table.active_schema_version}` : "尚未发布 Schema"}
                    {` · ${detail.record_count} 条记录 · 草稿 revision ${table.draft_revision}`}
                  </p>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button className="inline-flex h-10 w-10 items-center justify-center rounded-md border border-white/10 text-slate-200 hover:bg-white/5" onClick={() => void load()} title="刷新" type="button">
                <RefreshCw aria-hidden="true" size={17} />
              </button>
              {table.status !== "archived" ? (
                <button className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5" disabled={busy} onClick={() => void handleArchive()} type="button">
                  <Archive aria-hidden="true" size={16} />
                  归档
                </button>
              ) : null}
            </div>
          </div>
        </header>

        {error ? <div className="mt-4 rounded-md border border-rose-400/20 bg-rose-400/10 px-3 py-2 text-sm text-rose-100" role="alert">{error}</div> : null}
        {notice ? <div className="mt-4 flex items-center gap-2 rounded-md border border-emerald-400/20 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100"><CheckCircle2 aria-hidden="true" size={16} />{notice}</div> : null}

        <div className="mt-6 flex gap-1 border-b border-white/10">
          {([
            ["schema", "Schema"],
            ["records", `记录 (${records.length})`],
            ["versions", `版本 (${detail.schema_versions.length})`],
          ] as Array<[DetailTab, string]>).map(([id, label]) => (
            <button className={`border-b-2 px-4 py-2 text-sm font-semibold ${tab === id ? "border-emerald-300 text-white" : "border-transparent text-slate-500 hover:text-slate-200"}`} key={id} onClick={() => setTab(id)} type="button">{label}</button>
          ))}
        </div>

        {tab === "schema" ? (
          <section className="py-6">
            <div className="grid gap-4 lg:grid-cols-2">
              <label className="text-xs font-semibold text-slate-300">名称
                <input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-emerald-300 disabled:opacity-60" disabled={table.status === "archived"} maxLength={160} onChange={(event) => setName(event.target.value)} value={name} />
              </label>
              <label className="text-xs font-semibold text-slate-300">说明
                <input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-emerald-300 disabled:opacity-60" disabled={table.status === "archived"} maxLength={2000} onChange={(event) => setDescription(event.target.value)} value={description} />
              </label>
            </div>

            <div className="mt-7 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-white">字段定义</h2>
                <p className="mt-1 text-xs text-slate-500">字段名使用 ASCII；已发布字段只允许修改标签和说明。</p>
              </div>
              {table.status !== "archived" ? (
                <button className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-white/5" onClick={() => setFields((current) => [...current, blankField()])} type="button">
                  <Plus aria-hidden="true" size={15} />新增字段
                </button>
              ) : null}
            </div>

            <div className="mt-3 overflow-x-auto border-y border-white/10">
              <div className="min-w-[1080px]">
                <div className="grid grid-cols-[170px_180px_150px_90px_110px_minmax(180px,1fr)_42px] gap-3 border-b border-white/10 px-3 py-2 text-[11px] font-semibold uppercase text-slate-500">
                  <span>字段名</span><span>标签</span><span>类型</span><span>必填</span><span>默认值</span><span>说明</span><span />
                </div>
                {fields.map((field) => {
                  const published = activeFieldIds.has(field.field_id);
                  return (
                    <div className="grid grid-cols-[170px_180px_150px_90px_110px_minmax(180px,1fr)_42px] gap-3 border-b border-white/5 px-3 py-3 last:border-b-0" key={field._key}>
                      <input aria-label="字段名" className="rounded-md border border-white/10 bg-ink-950 px-2 py-2 text-xs text-white disabled:opacity-55" disabled={table.status === "archived" || published} onChange={(event) => updateField(field._key, { name: event.target.value })} placeholder="field_name" value={field.name} />
                      <input aria-label="字段标签" className="rounded-md border border-white/10 bg-ink-950 px-2 py-2 text-xs text-white disabled:opacity-55" disabled={table.status === "archived"} onChange={(event) => updateField(field._key, { label: event.target.value })} placeholder="中文标签" value={field.label} />
                      <select aria-label="字段类型" className="rounded-md border border-white/10 bg-ink-950 px-2 py-2 text-xs text-white disabled:opacity-55" disabled={table.status === "archived" || published} onChange={(event) => updateField(field._key, { data_type: event.target.value as AgentTableFieldType, default_text: "" })} value={field.data_type}>
                        {fieldTypes.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
                      </select>
                      <label className="flex items-center gap-2 text-xs text-slate-300"><input checked={field.required} disabled={table.status === "archived" || published} onChange={(event) => updateField(field._key, { required: event.target.checked })} type="checkbox" />是</label>
                      <label className="flex items-center gap-2 text-xs text-slate-300"><input checked={field.has_default} disabled={table.status === "archived" || published} onChange={(event) => updateField(field._key, { has_default: event.target.checked })} type="checkbox" />启用</label>
                      <div className="grid grid-cols-2 gap-2">
                        <input aria-label="字段默认值" className="rounded-md border border-white/10 bg-ink-950 px-2 py-2 text-xs text-white disabled:opacity-45" disabled={table.status === "archived" || published || !field.has_default} onChange={(event) => updateField(field._key, { default_text: event.target.value })} placeholder={field.has_default ? "默认值" : "无默认值"} value={field.default_text} />
                        <input aria-label="字段说明" className="rounded-md border border-white/10 bg-ink-950 px-2 py-2 text-xs text-white disabled:opacity-55" disabled={table.status === "archived"} onChange={(event) => updateField(field._key, { description: event.target.value })} placeholder="字段说明" value={field.description} />
                      </div>
                      <button className="flex h-9 w-9 items-center justify-center rounded-md text-slate-500 hover:bg-rose-400/10 hover:text-rose-200 disabled:opacity-30" disabled={table.status === "archived" || published} onClick={() => setFields((current) => current.filter((item) => item._key !== field._key))} title="删除字段" type="button"><Trash2 aria-hidden="true" size={16} /></button>
                    </div>
                  );
                })}
                {!fields.length ? <div className="px-3 py-10 text-center text-sm text-slate-500">至少新增一个字段后才能发布 Schema。</div> : null}
              </div>
            </div>

            {validationIssues.length ? <div className="mt-4 rounded-md border border-amber-300/20 bg-amber-300/10 px-3 py-3 text-xs text-amber-100">{validationIssues.map((issue) => <p key={issue}>{issue}</p>)}</div> : null}
            {table.status !== "archived" ? (
              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <button className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5 disabled:opacity-50" disabled={busy} onClick={() => void handleSave()} type="button"><Save aria-hidden="true" size={16} />保存草稿</button>
                <button className="inline-flex items-center gap-2 rounded-md border border-emerald-300/30 px-3 py-2 text-sm font-semibold text-emerald-100 hover:bg-emerald-300/10 disabled:opacity-50" disabled={busy} onClick={() => void handleValidate()} type="button"><CheckCircle2 aria-hidden="true" size={16} />校验</button>
                <button className="inline-flex items-center gap-2 rounded-md bg-emerald-300 px-3 py-2 text-sm font-semibold text-ink-950 hover:bg-emerald-200 disabled:opacity-50" disabled={busy || !fields.length} onClick={() => void handlePublish()} type="button"><Send aria-hidden="true" size={16} />发布 Schema</button>
              </div>
            ) : null}
          </section>
        ) : null}

        {tab === "records" ? (
          <section className="py-6">
            {!table.active_schema_version ? <div className="border-y border-white/10 py-14 text-center text-sm text-slate-500">发布首个 Schema 后才能管理记录。</div> : (
              <div className="grid gap-7 xl:grid-cols-[360px_minmax(0,1fr)]">
                <form className="self-start border-t border-white/10 pt-4" onSubmit={saveRecord}>
                  <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold text-white">{selectedRecord ? "编辑记录" : "新增记录"}</h2>{selectedRecord ? <button className="text-slate-500 hover:text-white" onClick={() => { setSelectedRecord(null); setRecordForm(recordDraft(activeFields)); }} title="取消编辑" type="button"><X aria-hidden="true" size={17} /></button> : null}</div>
                  <div className="mt-4 space-y-3">
                    {activeFields.map((field) => (
                      <label className="block text-xs font-semibold text-slate-300" key={field.field_id || field._key}>{field.label || field.name}{field.required ? " *" : ""}
                        {field.data_type === "boolean" ? (
                          <input checked={recordForm[field.name] === true} className="ml-3" disabled={table.status === "archived"} onChange={(event) => setRecordForm((current) => ({ ...current, [field.name]: event.target.checked }))} type="checkbox" />
                        ) : field.data_type === "json" ? (
                          <textarea className="mt-1 min-h-24 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 font-mono text-xs text-white outline-none focus:border-emerald-300 disabled:opacity-50" disabled={table.status === "archived"} onChange={(event) => setRecordForm((current) => ({ ...current, [field.name]: event.target.value }))} placeholder="{}" value={String(recordForm[field.name] ?? "")} />
                        ) : (
                          <input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white outline-none focus:border-emerald-300 disabled:opacity-50" disabled={table.status === "archived"} onChange={(event) => setRecordForm((current) => ({ ...current, [field.name]: event.target.value }))} required={field.required} type={field.data_type === "datetime" ? "datetime-local" : field.data_type === "integer" || field.data_type === "number" ? "number" : "text"} value={String(recordForm[field.name] ?? "")} />
                        )}
                      </label>
                    ))}
                  </div>
                  {table.status !== "archived" ? <button className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-md bg-emerald-300 px-3 py-2 text-sm font-semibold text-ink-950 hover:bg-emerald-200 disabled:opacity-50" disabled={busy} type="submit"><Save aria-hidden="true" size={16} />{selectedRecord ? "保存修改" : "新增记录"}</button> : null}
                </form>

                <div className="min-w-0 overflow-x-auto border-y border-white/10">
                  <table className="w-full min-w-[720px] table-fixed text-left text-xs">
                    <thead className="border-b border-white/10 text-slate-500"><tr>{activeFields.slice(0, 5).map((field) => <th className="px-3 py-3 font-semibold" key={field.field_id}>{field.label || field.name}</th>)}<th className="w-28 px-3 py-3 font-semibold">revision</th><th className="w-24 px-3 py-3" /></tr></thead>
                    <tbody className="divide-y divide-white/5">{records.map((record) => <tr className="hover:bg-white/[0.025]" key={record.record_id}>{activeFields.slice(0, 5).map((field) => <td className="truncate px-3 py-3 text-slate-300" key={field.field_id} title={formatValue(record.data[field.name])}>{formatValue(record.data[field.name])}</td>)}<td className="px-3 py-3 text-slate-500">r{record.revision}</td><td className="px-3 py-2"><div className="flex justify-end gap-1"><button className="rounded-md px-2 py-1 text-emerald-200 hover:bg-emerald-300/10" onClick={() => selectRecord(record)} type="button">编辑</button>{table.status !== "archived" ? <button className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-rose-400/10 hover:text-rose-200" onClick={() => void deleteRecord(record)} title="删除记录" type="button"><Trash2 aria-hidden="true" size={15} /></button> : null}</div></td></tr>)}</tbody>
                  </table>
                  {!records.length ? <div className="py-14 text-center text-sm text-slate-500">当前还没有记录。</div> : null}
                </div>
              </div>
            )}
          </section>
        ) : null}

        {tab === "versions" ? (
          <section className="py-6">
            <div className="divide-y divide-white/10 border-y border-white/10">
              {detail.schema_versions.map((version) => (
                <div className="grid gap-3 py-4 sm:grid-cols-[100px_minmax(0,1fr)_220px] sm:items-center" key={version.version}>
                  <div className="text-sm font-semibold text-white">Schema v{version.version}</div>
                  <div className="text-xs text-slate-400">{version.fields.length} 个字段 · draft revision {version.draft_revision}<div className="mt-1 font-mono text-[10px] text-slate-600">{version.checksum.slice(0, 20)}</div></div>
                  <time className="text-xs text-slate-500 sm:text-right">{new Date(version.published_at * 1000).toLocaleString("zh-CN")}</time>
                </div>
              ))}
              {!detail.schema_versions.length ? <div className="py-14 text-center text-sm text-slate-500">尚未发布 Schema 版本。</div> : null}
            </div>
          </section>
        ) : null}
      </div>
    </PageContainer>
  );
}
