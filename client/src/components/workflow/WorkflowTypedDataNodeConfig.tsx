import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import {
  type AgentTableDetail,
  type AgentTableDefinition,
  type AgentTableField,
  type AgentTableFieldType,
  type JsonValue,
  requestAgentTableJson,
} from "../../types/agentTables";
import { type WorkflowNodeData } from "../../types/workflow";
import {
  compactFilterGroup,
  createLiteralBinding,
  formatLiteral,
  normalizeFilterGroup,
  normalizeValueBinding,
  parseLiteral,
  type DataTableFilterCondition,
  type DataTableFilterGroup,
  type DataTableFilterOperator,
  type DataTableValueBinding,
} from "./workflowDataTableConfig";

const inputClass =
  "w-full rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";

const dataTableKinds = new Set([
  "data_table_query",
  "data_table_insert",
  "data_table_update",
  "data_table_delete",
]);

const systemFields: AgentTableField[] = [
  {
    field_id: "system-record-id",
    name: "record_id",
    label: "记录 ID",
    description: "系统生成的稳定记录标识。",
    data_type: "string",
    required: true,
    has_default: true,
    default_value: "",
  },
  {
    field_id: "system-created-at",
    name: "created_at",
    label: "创建时间",
    description: "记录创建时间。",
    data_type: "number",
    required: true,
    has_default: true,
    default_value: "",
  },
  {
    field_id: "system-updated-at",
    name: "updated_at",
    label: "更新时间",
    description: "记录最近更新时间。",
    data_type: "number",
    required: true,
    has_default: true,
    default_value: "",
  },
  {
    field_id: "system-revision",
    name: "revision",
    label: "记录 Revision",
    description: "用于并发保护的记录版本。",
    data_type: "integer",
    required: true,
    has_default: true,
    default_value: 1,
  },
];

const filterOperatorLabels: Record<DataTableFilterOperator, string> = {
  eq: "等于",
  ne: "不等于",
  gt: "大于",
  gte: "大于等于",
  lt: "小于",
  lte: "小于等于",
  in: "属于列表",
  contains: "包含",
  is_null: "为空",
};

function availableFilterOperators(
  type: AgentTableFieldType | undefined,
): DataTableFilterOperator[] {
  if (type === "boolean") return ["eq", "ne", "in", "is_null"];
  if (type && type !== "string" && type !== "datetime") {
    return ["eq", "ne", "gt", "gte", "lt", "lte", "in", "is_null"];
  }
  return ["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "is_null"];
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-white/10 pt-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-300">
        {title}
      </h3>
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

function LabeledField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-300">{label}</span>
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

function BindingEditor({
  binding,
  dataType,
  onChange,
}: {
  binding: unknown;
  dataType: AgentTableFieldType;
  onChange: (value: DataTableValueBinding) => void;
}) {
  const normalized = normalizeValueBinding(binding, dataType);
  const [literalDraft, setLiteralDraft] = useState(
    normalized.source === "literal" ? formatLiteral(normalized.value) : "",
  );
  const [literalError, setLiteralError] = useState("");

  useEffect(() => {
    if (normalized.source === "literal") {
      setLiteralDraft(formatLiteral(normalized.value));
      setLiteralError("");
    }
  }, [binding, dataType]);

  function updateLiteral(raw: string) {
    setLiteralDraft(raw);
    const parsed = parseLiteral(raw, dataType);
    if (!parsed.ok) {
      setLiteralError(parsed.error);
      return;
    }
    setLiteralError("");
    onChange({ source: "literal", value: parsed.value });
  }

  return (
    <div className="grid gap-2 sm:grid-cols-[110px_minmax(0,1fr)]">
      <select
        aria-label="值来源"
        className={inputClass}
        onChange={(event) =>
          onChange(
            event.target.value === "variable"
              ? { source: "variable", variable: "" }
              : createLiteralBinding(dataType),
          )
        }
        value={normalized.source}
      >
        <option className="bg-slate-950" value="literal">
          固定值
        </option>
        <option className="bg-slate-950" value="variable">
          工作流变量
        </option>
      </select>
      {normalized.source === "variable" ? (
        <input
          aria-label="变量名"
          className={inputClass}
          onChange={(event) =>
            onChange({ source: "variable", variable: event.target.value })
          }
          placeholder="例如 current_user_id"
          value={normalized.variable}
        />
      ) : dataType === "boolean" ? (
        <select
          aria-label="布尔固定值"
          className={inputClass}
          onChange={(event) => updateLiteral(event.target.value)}
          value={String(normalized.value)}
        >
          <option className="bg-slate-950" value="false">
            false
          </option>
          <option className="bg-slate-950" value="true">
            true
          </option>
        </select>
      ) : (
        <div>
          {dataType === "json" ? (
            <textarea
              aria-label="JSON 固定值"
              className={`${inputClass} min-h-24 resize-y font-mono text-xs`}
              onChange={(event) => updateLiteral(event.target.value)}
              value={literalDraft}
            />
          ) : (
            <input
              aria-label="固定值"
              className={inputClass}
              onChange={(event) => updateLiteral(event.target.value)}
              type={dataType === "number" || dataType === "integer" ? "number" : "text"}
              value={literalDraft}
            />
          )}
          {literalError ? (
            <p className="mt-1 text-xs text-rose-200">{literalError}</p>
          ) : null}
        </div>
      )}
    </div>
  );
}

function FilterGroupEditor({
  group,
  fields,
  depth = 0,
  onChange,
}: {
  group: DataTableFilterGroup;
  fields: AgentTableField[];
  depth?: number;
  onChange: (value: DataTableFilterGroup) => void;
}) {
  const fieldByName = useMemo(
    () => new Map(fields.map((field) => [field.name, field])),
    [fields],
  );

  function newCondition(): DataTableFilterCondition {
    const field = fields[0];
    return {
      field: field?.name ?? "",
      operator: "eq",
      value: createLiteralBinding(field?.data_type ?? "string"),
    };
  }

  function updateItem(
    index: number,
    value: DataTableFilterCondition | DataTableFilterGroup,
  ) {
    onChange({
      ...group,
      items: group.items.map((item, itemIndex) =>
        itemIndex === index ? value : item,
      ),
    });
  }

  function removeItem(index: number) {
    const remaining = group.items.filter((_, itemIndex) => itemIndex !== index);
    onChange({
      ...group,
      items: depth > 0 && remaining.length === 0 ? [newCondition()] : remaining,
    });
  }

  return (
    <div className={depth > 0 ? "border-l border-brand-300/25 pl-3" : ""}>
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-400">匹配</span>
        <select
          aria-label="条件组合"
          className="rounded-md border border-white/10 bg-white/[0.055] px-2 py-1.5 text-xs text-white"
          onChange={(event) =>
            onChange({ ...group, logic: event.target.value === "or" ? "or" : "and" })
          }
          value={group.logic}
        >
          <option className="bg-slate-950" value="and">全部条件</option>
          <option className="bg-slate-950" value="or">任一条件</option>
        </select>
      </div>
      <div className="mt-2 space-y-3">
        {group.items.map((item, index) =>
          "items" in item ? (
            <div className="relative" key={`group-${index}`}>
              <FilterGroupEditor
                depth={depth + 1}
                fields={fields}
                group={item}
                onChange={(value) => updateItem(index, value)}
              />
              <button
                aria-label="删除条件组"
                className="absolute right-0 top-0 text-slate-500 hover:text-rose-200"
                onClick={() => removeItem(index)}
                type="button"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ) : (
            <div className="space-y-2 border-l border-white/10 pl-3" key={`condition-${index}`}>
              <div className="grid grid-cols-[minmax(0,1fr)_120px_28px] gap-2">
                <select
                  aria-label="条件字段"
                  className={inputClass}
                  onChange={(event) => {
                    const nextField = fieldByName.get(event.target.value);
                    const nextOperator = availableFilterOperators(
                      nextField?.data_type,
                    ).includes(item.operator)
                      ? item.operator
                      : "eq";
                    updateItem(index, {
                      ...item,
                      field: event.target.value,
                      operator: nextOperator,
                      value:
                        nextOperator === "is_null"
                          ? undefined
                          : createLiteralBinding(
                              nextOperator === "in"
                                ? "json"
                                : nextField?.data_type ?? "string",
                            ),
                    });
                  }}
                  value={item.field}
                >
                  <option className="bg-slate-950" value="">选择字段</option>
                  {fields.map((field) => (
                    <option className="bg-slate-950" key={field.name} value={field.name}>
                      {field.label || field.name}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="条件操作符"
                  className={inputClass}
                  onChange={(event) => {
                    const operator = event.target.value as DataTableFilterOperator;
                    const field = fieldByName.get(item.field);
                    updateItem(index, {
                      ...item,
                      operator,
                      value:
                        operator === "is_null"
                          ? undefined
                          : normalizeValueBinding(
                              item.value,
                              operator === "in" ? "json" : field?.data_type,
                            ),
                    });
                  }}
                  value={item.operator}
                >
                  {availableFilterOperators(fieldByName.get(item.field)?.data_type).map((value) => (
                    <option className="bg-slate-950" key={value} value={value}>
                      {filterOperatorLabels[value]}
                    </option>
                  ))}
                </select>
                <button
                  aria-label="删除条件"
                  className="inline-flex items-center justify-center text-slate-500 hover:text-rose-200"
                  onClick={() => removeItem(index)}
                  type="button"
                >
                  <Trash2 size={15} />
                </button>
              </div>
              {item.operator !== "is_null" ? (
                <BindingEditor
                  binding={item.value}
                  dataType={
                    item.operator === "in"
                      ? "json"
                      : fieldByName.get(item.field)?.data_type ?? "string"
                  }
                  onChange={(value) => updateItem(index, { ...item, value })}
                />
              ) : null}
            </div>
          ),
        )}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          className="inline-flex items-center gap-1 rounded-md border border-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-300 hover:bg-white/5"
          onClick={() => onChange({ ...group, items: [...group.items, newCondition()] })}
          type="button"
        >
          <Plus size={13} /> 条件
        </button>
        {depth < 3 ? (
          <button
            className="inline-flex items-center gap-1 rounded-md border border-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-300 hover:bg-white/5"
            onClick={() =>
              onChange({
                ...group,
                items: [...group.items, { logic: "and", items: [newCondition()] }],
              })
            }
            type="button"
          >
            <Plus size={13} /> 条件组
          </button>
        ) : null}
      </div>
    </div>
  );
}

function WriteBindingsEditor({
  fields,
  bindings,
  onChange,
}: {
  fields: AgentTableField[];
  bindings: Record<string, unknown>;
  onChange: (bindings: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      {fields.map((field) => {
        const enabled = Object.prototype.hasOwnProperty.call(bindings, field.name);
        return (
          <div className="border-l border-white/10 pl-3" key={field.field_id}>
            <label className="flex items-start gap-2 text-xs text-slate-200">
              <input
                checked={enabled}
                className="mt-0.5 accent-emerald-300"
                onChange={(event) => {
                  const next = { ...bindings };
                  if (event.target.checked) {
                    next[field.name] = field.has_default
                      ? { source: "literal", value: field.default_value }
                      : createLiteralBinding(field.data_type);
                  } else {
                    delete next[field.name];
                  }
                  onChange(next);
                }}
                type="checkbox"
              />
              <span>
                <strong>{field.label || field.name}</strong>
                <span className="ml-1 font-mono text-[11px] text-slate-500">{field.name}</span>
                {field.required && !field.has_default ? (
                  <span className="ml-1 text-amber-200">必填</span>
                ) : null}
              </span>
            </label>
            {enabled ? (
              <div className="mt-2">
                <BindingEditor
                  binding={bindings[field.name]}
                  dataType={field.data_type}
                  onChange={(value) => onChange({ ...bindings, [field.name]: value })}
                />
              </div>
            ) : null}
          </div>
        );
      })}
      {fields.length === 0 ? (
        <p className="text-xs leading-5 text-slate-500">当前 Schema 没有业务字段。</p>
      ) : null}
    </div>
  );
}

export default function WorkflowTypedDataNodeConfig({
  data,
  onChange,
}: {
  data: WorkflowNodeData;
  onChange: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const isDataTable = dataTableKinds.has(data.kind);
  const [tables, setTables] = useState<AgentTableDefinition[]>([]);
  const [detail, setDetail] = useState<AgentTableDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadTables = useCallback(async () => {
    if (!isDataTable) return;
    setLoading(true);
    try {
      const payload = await requestAgentTableJson<{ items: AgentTableDefinition[] }>(
        "/api/data-tables?status=published&limit=500",
      );
      setTables(payload.items ?? []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "数据表加载失败。" );
    } finally {
      setLoading(false);
    }
  }, [isDataTable]);

  useEffect(() => {
    void loadTables();
  }, [loadTables]);

  useEffect(() => {
    if (!isDataTable || !data.tableId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    requestAgentTableJson<AgentTableDetail>(`/api/data-tables/${data.tableId}`)
      .then((payload) => {
        if (!cancelled) {
          setDetail(payload);
          setError("");
        }
      })
      .catch((reason) => {
        if (!cancelled) {
          setDetail(null);
          setError(reason instanceof Error ? reason.message : "Schema 加载失败。" );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [data.tableId, isDataTable]);

  const selectedSchema = useMemo(() => {
    if (!detail) return null;
    const requestedVersion =
      data.versionPolicy === "pinned"
        ? Number(data.pinnedSchemaVersion || 0)
        : detail.table.active_schema_version;
    return (
      detail.schema_versions.find((version) => version.version === requestedVersion) ??
      null
    );
  }, [data.pinnedSchemaVersion, data.versionPolicy, detail]);

  if (data.kind === "json_serialize") {
    return (
      <Section title="JSON 转换">
        <LabeledField label="输入变量">
          <input className={inputClass} onChange={(event) => onChange({ inputVariable: event.target.value })} value={data.inputVariable ?? ""} />
        </LabeledField>
        <LabeledField label="格式">
          <select className={inputClass} onChange={(event) => onChange({ format: event.target.value as "compact" | "pretty" })} value={data.format ?? "compact"}>
            <option className="bg-slate-950" value="compact">紧凑 JSON</option>
            <option className="bg-slate-950" value="pretty">两空格格式化</option>
          </select>
        </LabeledField>
        <LabeledField label="输出变量">
          <input className={inputClass} onChange={(event) => onChange({ outputVariable: event.target.value })} value={data.outputVariable ?? ""} />
        </LabeledField>
      </Section>
    );
  }

  if (data.kind === "json_deserialize") {
    return (
      <Section title="JSON 转换">
        <LabeledField label="JSON 字符串变量">
          <input className={inputClass} onChange={(event) => onChange({ inputVariable: event.target.value })} value={data.inputVariable ?? ""} />
        </LabeledField>
        <LabeledField label="类型化输出变量">
          <input className={inputClass} onChange={(event) => onChange({ outputVariable: event.target.value })} value={data.outputVariable ?? ""} />
        </LabeledField>
      </Section>
    );
  }

  if (data.kind === "annotation") {
    return (
      <Section title="画布说明">
        <textarea
          className={`${inputClass} min-h-40 resize-y leading-6`}
          maxLength={20000}
          onChange={(event) => onChange({ content: event.target.value })}
          placeholder="记录设计意图、约束或交接说明。注释不会参与运行。"
          value={data.content ?? ""}
        />
        <p className="text-xs leading-5 text-slate-500">仅随工作流草稿和发布快照保存，不产生变量或运行事件。</p>
      </Section>
    );
  }

  if (!isDataTable) return null;

  const businessFields = selectedSchema?.fields ?? [];
  const readableFields = [...businessFields, ...systemFields];
  const filter = normalizeFilterGroup(data.filter);
  const valueBindings =
    data.valueBindings && typeof data.valueBindings === "object"
      ? data.valueBindings
      : {};
  const selectedFields = Array.isArray(data.selectFields) ? data.selectFields : [];
  const sort = Array.isArray(data.sort) ? data.sort : [];

  return (
    <div className="space-y-4">
      <Section title="Agent Table">
        <div className="flex items-end gap-2">
          <LabeledField label="已发布数据表">
            <select
              className={inputClass}
              disabled={loading}
              onChange={(event) =>
                onChange({
                  tableId: event.target.value,
                  versionPolicy: "latest",
                  pinnedSchemaVersion: "",
                  selectFields: [],
                  filter: {},
                  sort: [],
                  valueBindings: {},
                })
              }
              value={data.tableId ?? ""}
            >
              <option className="bg-slate-950" value="">选择数据表</option>
              {tables.map((table) => (
                <option className="bg-slate-950" key={table.table_id} value={table.table_id}>
                  {table.name} · Schema v{table.active_schema_version}
                </option>
              ))}
            </select>
          </LabeledField>
          <button
            aria-label="刷新数据表"
            className="mb-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-white/10 text-slate-300 hover:bg-white/5"
            onClick={() => void loadTables()}
            type="button"
          >
            <RefreshCw className={loading ? "animate-spin" : ""} size={15} />
          </button>
        </div>
        <div className="flex flex-wrap gap-3 text-xs font-semibold">
          <Link className="inline-flex items-center gap-1 text-emerald-200 hover:text-emerald-100" target="_blank" to="/data-tables">
            管理或新建数据表 <ExternalLink size={12} />
          </Link>
          {data.tableId ? (
            <Link className="inline-flex items-center gap-1 text-cyan-200 hover:text-cyan-100" target="_blank" to={`/data-tables/${data.tableId}`}>
              打开当前数据表 <ExternalLink size={12} />
            </Link>
          ) : null}
        </div>
        {error ? <p className="text-xs leading-5 text-rose-200">{error}</p> : null}
        {!loading && tables.length === 0 ? (
          <p className="text-xs leading-5 text-amber-100">尚无已发布数据表。请先创建字段并发布 Schema。</p>
        ) : null}
      </Section>

      {detail ? (
        <Section title="Schema 绑定">
          <LabeledField label="版本策略">
            <select
              className={inputClass}
              onChange={(event) => {
                const versionPolicy = event.target.value;
                onChange({
                  versionPolicy,
                  pinnedSchemaVersion:
                    versionPolicy === "pinned"
                      ? detail.table.active_schema_version ?? ""
                      : "",
                });
              }}
              value={data.versionPolicy ?? "latest"}
            >
              <option className="bg-slate-950" value="latest">运行时使用当前发布 Schema</option>
              <option className="bg-slate-950" value="pinned">固定 Schema 版本</option>
            </select>
          </LabeledField>
          {data.versionPolicy === "pinned" ? (
            <LabeledField label="固定版本">
              <select
                className={inputClass}
                onChange={(event) => onChange({ pinnedSchemaVersion: Number(event.target.value) })}
                value={data.pinnedSchemaVersion ?? ""}
              >
                <option className="bg-slate-950" value="">选择版本</option>
                {detail.schema_versions.map((version) => (
                  <option className="bg-slate-950" key={version.version} value={version.version}>
                    Schema v{version.version} · {version.fields.length} 个字段
                  </option>
                ))}
              </select>
            </LabeledField>
          ) : null}
          <div className="text-xs leading-5 text-slate-400">
            {selectedSchema
              ? `当前配置：Schema v${selectedSchema.version}，${selectedSchema.fields.length} 个业务字段。Xpert 发布时会固定具体版本。`
              : "所选版本不可用，请重新选择 Schema。"}
          </div>
        </Section>
      ) : null}

      {selectedSchema && data.kind === "data_table_query" ? (
        <>
          <Section title="返回字段">
            <p className="text-xs leading-5 text-slate-500">不勾选时返回全部业务字段和系统字段。</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {businessFields.map((field) => (
                <label className="flex items-center gap-2 text-xs text-slate-200" key={field.field_id}>
                  <input
                    checked={selectedFields.includes(field.name)}
                    className="accent-emerald-300"
                    onChange={(event) =>
                      onChange({
                        selectFields: event.target.checked
                          ? [...selectedFields, field.name]
                          : selectedFields.filter((name) => name !== field.name),
                      })
                    }
                    type="checkbox"
                  />
                  <span>{field.label || field.name}</span>
                </label>
              ))}
            </div>
          </Section>
          <Section title="查询条件">
            <FilterGroupEditor
              fields={readableFields}
              group={filter}
              onChange={(value) => onChange({ filter: compactFilterGroup(value) })}
            />
          </Section>
          <Section title="排序与结果">
            <div className="space-y-2">
              {sort.map((item, index) => (
                <div className="grid grid-cols-[minmax(0,1fr)_100px_28px] gap-2" key={`${item.field}-${index}`}>
                  <select
                    className={inputClass}
                    onChange={(event) => onChange({ sort: sort.map((entry, entryIndex) => entryIndex === index ? { ...entry, field: event.target.value } : entry) })}
                    value={item.field}
                  >
                    <option className="bg-slate-950" value="">选择字段</option>
                    {readableFields.map((field) => <option className="bg-slate-950" key={field.name} value={field.name}>{field.label || field.name}</option>)}
                  </select>
                  <select className={inputClass} onChange={(event) => onChange({ sort: sort.map((entry, entryIndex) => entryIndex === index ? { ...entry, direction: event.target.value as "asc" | "desc" } : entry) })} value={item.direction}>
                    <option className="bg-slate-950" value="asc">升序</option>
                    <option className="bg-slate-950" value="desc">降序</option>
                  </select>
                  <button aria-label="删除排序" className="inline-flex items-center justify-center text-slate-500 hover:text-rose-200" onClick={() => onChange({ sort: sort.filter((_, entryIndex) => entryIndex !== index) })} type="button"><Trash2 size={15} /></button>
                </div>
              ))}
              {sort.length < 5 ? (
                <button className="inline-flex items-center gap-1 rounded-md border border-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-300 hover:bg-white/5" onClick={() => onChange({ sort: [...sort, { field: readableFields[0]?.name ?? "", direction: "asc" }] })} type="button"><Plus size={13} /> 排序</button>
              ) : null}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <LabeledField label="最多返回">
                <input className={inputClass} max={200} min={1} onChange={(event) => onChange({ limit: Number(event.target.value) })} type="number" value={data.limit ?? 20} />
              </LabeledField>
              <LabeledField label="返回模式">
                <select className={inputClass} onChange={(event) => onChange({ returnMode: event.target.value as "list" | "first" })} value={data.returnMode ?? "list"}>
                  <option className="bg-slate-950" value="list">记录列表</option>
                  <option className="bg-slate-950" value="first">首条或 null</option>
                </select>
              </LabeledField>
            </div>
          </Section>
        </>
      ) : null}

      {selectedSchema && (data.kind === "data_table_insert" || data.kind === "data_table_update") ? (
        <Section title={data.kind === "data_table_insert" ? "新增字段绑定" : "更新字段绑定"}>
          <p className="text-xs leading-5 text-slate-500">固定值会按 Schema 类型保存；变量在运行时保留真实 JSON 类型。</p>
          <WriteBindingsEditor fields={businessFields} bindings={valueBindings} onChange={(value) => onChange({ valueBindings: value })} />
        </Section>
      ) : null}

      {selectedSchema && (data.kind === "data_table_update" || data.kind === "data_table_delete") ? (
        <Section title="必填安全条件">
          <p className="text-xs leading-5 text-amber-100">更新和删除不允许全表操作，且单次最多影响 100 行。</p>
          <FilterGroupEditor fields={readableFields} group={filter} onChange={(value) => onChange({ filter: compactFilterGroup(value) })} />
        </Section>
      ) : null}

      <Section title="输出">
        <LabeledField label="输出变量">
          <input className={inputClass} onChange={(event) => onChange({ outputVariable: event.target.value })} value={data.outputVariable ?? ""} />
        </LabeledField>
        <p className="text-xs leading-5 text-slate-500">
          {data.kind === "data_table_query"
            ? "输出为 array<object>，首条模式为 object|null。"
            : data.kind === "data_table_insert"
              ? "输出新记录及 record_id、revision 和系统时间字段。"
              : "输出 matched 与 affected 计数。"}
        </p>
      </Section>
    </div>
  );
}
