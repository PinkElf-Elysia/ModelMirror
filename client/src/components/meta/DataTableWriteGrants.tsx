import { Database, ShieldCheck } from "lucide-react";
import {
  isSafeDataTableBusinessField,
  normalizeDataTableWriteGrants,
  type DataTableWriteGrant,
  type DataTableWriteOperation,
} from "./metaAuthoring";

export interface DataTableWriteCatalogField {
  name: string;
  label: string;
  data_type: string;
}

export interface DataTableWriteCatalogItem {
  table_id: string;
  name: string;
  schema_version: number | null;
  fields: DataTableWriteCatalogField[];
}

interface DataTableWriteGrantsProps {
  catalog: DataTableWriteCatalogItem[];
  disabled?: boolean;
  grants: DataTableWriteGrant[];
  onChange: (grants: DataTableWriteGrant[]) => void;
}

const OPERATION_OPTIONS: Array<{
  value: DataTableWriteOperation;
  label: string;
}> = [
  { value: "insert", label: "新增 insert" },
  { value: "update", label: "更新 update" },
  { value: "delete", label: "删除 delete" },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeText(value: unknown, maximum: number) {
  return (
    typeof value === "string" &&
    value.trim().length > 0 &&
    value.length <= maximum &&
    !/[\u0000-\u001f\u007f-\u009f]/.test(value)
  );
}

export function normalizeDataTableWriteCatalog(
  value: unknown,
): DataTableWriteCatalogItem[] {
  if (!Array.isArray(value)) return [];
  const seenTables = new Set<string>();
  const catalog: DataTableWriteCatalogItem[] = [];
  for (const item of value.slice(0, 50)) {
    if (!isRecord(item)) continue;
    const rawTableId = item.table_id ?? item.id;
    if (!safeText(rawTableId, 200)) continue;
    const tableId = rawTableId as string;
    if (seenTables.has(tableId)) continue;
    seenTables.add(tableId);
    const seenFields = new Set<string>();
    const fields = Array.isArray(item.fields)
      ? item.fields.flatMap((field) => {
          if (!isRecord(field) || !isSafeDataTableBusinessField(field.name)) {
            return [];
          }
          const name = field.name as string;
          if (seenFields.has(name)) return [];
          seenFields.add(name);
          return [{
            name,
            label: safeText(field.label, 120) ? String(field.label) : name,
            data_type: safeText(field.data_type ?? field.type, 40)
              ? String(field.data_type ?? field.type)
              : "unknown",
          }];
        })
      : [];
    const rawSchemaVersion = item.active_schema_version ?? item.schema_version;
    const schemaVersion =
      typeof rawSchemaVersion === "number" &&
      Number.isInteger(rawSchemaVersion) &&
      rawSchemaVersion > 0
        ? rawSchemaVersion
        : null;
    catalog.push({
      table_id: tableId,
      name: safeText(item.name ?? item.title, 200)
        ? String(item.name ?? item.title)
        : tableId,
      schema_version: schemaVersion,
      fields,
    });
  }
  return catalog;
}

export function dataTableWriteGrantError(
  grant: DataTableWriteGrant,
  catalog: DataTableWriteCatalogItem[],
): string | null {
  const table = catalog.find((item) => item.table_id === grant.table_id);
  if (!table) return `写授权表 ${grant.table_id} 不在当前安全目录中。`;
  if (table.schema_version === null) {
    return `写授权表 ${grant.table_id} 没有可固定的已发布 Schema。`;
  }
  if (grant.operations.length === 0) {
    return `请为 ${grant.table_id} 选择至少一个写操作。`;
  }
  if (grant.operations.length !== new Set(grant.operations).size) {
    return `写授权表 ${grant.table_id} 包含重复操作。`;
  }
  if (
    !Number.isInteger(grant.max_affected_rows) ||
    grant.max_affected_rows < 1 ||
    grant.max_affected_rows > 100
  ) {
    return `${grant.table_id} 的单次影响行数必须为 1 到 100。`;
  }
  if (grant.writable_fields.length !== new Set(grant.writable_fields).size) {
    return `写授权表 ${grant.table_id} 包含重复字段。`;
  }
  if (
    grant.writable_fields.some(
      (field) => !isSafeDataTableBusinessField(field),
    )
  ) {
    return `写授权表 ${grant.table_id} 包含无效业务字段名。`;
  }
  const knownFields = new Set(table.fields.map((field) => field.name));
  if (grant.writable_fields.some((field) => !knownFields.has(field))) {
    return `写授权表 ${grant.table_id} 包含目录外字段。`;
  }
  const needsFields = grant.operations.some(
    (operation) => operation === "insert" || operation === "update",
  );
  if (needsFields && grant.writable_fields.length === 0) {
    return `新增或更新 ${grant.table_id} 前必须选择至少一个可写字段。`;
  }
  return null;
}

export function validateDataTableWriteGrants(
  grants: DataTableWriteGrant[],
  catalog: DataTableWriteCatalogItem[],
): string | null {
  if (grants.length > 20) {
    return "Agent Table 写授权最多允许 20 张表。";
  }
  if (grants.length !== new Set(grants.map((grant) => grant.table_id)).size) {
    return "Agent Table 写授权包含重复表。";
  }
  for (const grant of grants) {
    const error = dataTableWriteGrantError(grant, catalog);
    if (error) return error;
  }
  return null;
}

export default function DataTableWriteGrants({
  catalog,
  disabled = false,
  grants,
  onChange,
}: DataTableWriteGrantsProps) {
  const selectedByTable = new Map(
    grants.map((grant) => [grant.table_id, grant]),
  );
  const limitReached = grants.length >= 20;

  function commit(next: DataTableWriteGrant[]) {
    onChange(normalizeDataTableWriteGrants(next));
  }

  function toggleTable(table: DataTableWriteCatalogItem) {
    const selected = selectedByTable.has(table.table_id);
    commit(
      selected
        ? grants.filter((grant) => grant.table_id !== table.table_id)
        : [
            ...grants,
            {
              table_id: table.table_id,
              operations: [],
              writable_fields: [],
              max_affected_rows: 1,
            },
          ],
    );
  }

  function replaceGrant(
    tableId: string,
    update: (grant: DataTableWriteGrant) => DataTableWriteGrant,
  ) {
    commit(
      grants.map((grant) =>
        grant.table_id === tableId ? update(grant) : grant,
      ),
    );
  }

  function toggleOperation(
    grant: DataTableWriteGrant,
    operation: DataTableWriteOperation,
  ) {
    replaceGrant(grant.table_id, (current) => {
      const operations = current.operations.includes(operation)
        ? current.operations.filter((item) => item !== operation)
        : [...current.operations, operation];
      const needsFields = operations.some(
        (item) => item === "insert" || item === "update",
      );
      return {
        ...current,
        operations,
        writable_fields: needsFields ? current.writable_fields : [],
      };
    });
  }

  function toggleField(grant: DataTableWriteGrant, field: string) {
    replaceGrant(grant.table_id, (current) => ({
      ...current,
      writable_fields: current.writable_fields.includes(field)
        ? current.writable_fields.filter((item) => item !== field)
        : [...current.writable_fields, field],
    }));
  }

  return (
    <section
      aria-labelledby="data-table-write-grants-title"
      className="border-t border-white/10 pt-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <ShieldCheck
            aria-hidden="true"
            className="mt-0.5 h-4 w-4 shrink-0 text-amber-200"
          />
          <div>
            <h3
              className="text-xs font-semibold text-slate-200"
              id="data-table-write-grants-title"
            >
              Agent Table 写入授权
            </h3>
            <p className="mt-1 text-[10px] leading-4 text-slate-400">
              写入与查询授权相互独立。选择写表不会加入查询范围。
            </p>
          </div>
        </div>
        <span className="shrink-0 text-[10px] text-slate-400">
          已选 {grants.length}/20 张表
        </span>
      </div>

      {limitReached ? (
        <p className="mt-2 text-[10px] leading-4 text-slate-400">
          已达到 20 张写授权表上限。
        </p>
      ) : null}

      <div className="mt-3 space-y-2">
        {catalog.map((table) => {
          const grant = selectedByTable.get(table.table_id);
          const unavailable = table.schema_version === null;
          const selectionUnavailable = unavailable || (!grant && limitReached);
          const grantError = grant
            ? dataTableWriteGrantError(grant, catalog)
            : null;
          const fieldsEnabled = Boolean(
            grant?.operations.some(
              (operation) => operation === "insert" || operation === "update",
            ),
          );
          return (
            <div
              className="rounded-md border border-white/10 bg-white/[0.025] p-2.5"
              key={table.table_id}
            >
              <label
                className={
                  selectionUnavailable || disabled
                    ? "flex cursor-not-allowed items-start gap-2 opacity-55"
                    : "flex cursor-pointer items-start gap-2"
                }
              >
                <input
                  aria-label={`写授权表：${table.name}`}
                  checked={Boolean(grant)}
                  className="mt-0.5 accent-amber-300"
                  disabled={disabled || selectionUnavailable}
                  onChange={() => toggleTable(table)}
                  type="checkbox"
                />
                <Database
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400"
                />
                <span className="min-w-0">
                  <span className="block break-words text-xs font-medium text-slate-200">
                    {table.name}
                  </span>
                  <span className="mt-0.5 block break-all font-mono text-[10px] text-slate-400">
                    {table.table_id}
                    {table.schema_version === null
                      ? " · 无已发布 Schema"
                      : ` · Schema v${table.schema_version}`}
                  </span>
                </span>
              </label>

              {grant ? (
                <div className="mt-3 border-t border-white/10 pt-3">
                  <fieldset disabled={disabled}>
                    <legend className="text-[11px] font-semibold text-slate-300">
                      允许操作
                    </legend>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
                      {OPERATION_OPTIONS.map((operation) => (
                        <label
                          className="flex cursor-pointer items-center gap-1.5 text-[11px] text-slate-300"
                          key={operation.value}
                        >
                          <input
                            aria-label={`${table.table_id} 操作：${operation.label}`}
                            checked={grant.operations.includes(operation.value)}
                            className="accent-amber-300"
                            onChange={() =>
                              toggleOperation(grant, operation.value)
                            }
                            type="checkbox"
                          />
                          {operation.label}
                        </label>
                      ))}
                    </div>
                  </fieldset>

                  <fieldset className="mt-3" disabled={disabled || !fieldsEnabled}>
                    <legend className="text-[11px] font-semibold text-slate-300">
                      可写字段
                    </legend>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {table.fields.map((field) => (
                        <label
                          className="flex cursor-pointer items-center gap-1.5 rounded border border-white/10 px-2 py-1 text-[10px] text-slate-300 disabled:cursor-not-allowed"
                          key={field.name}
                          title={`${field.label} · ${field.data_type}`}
                        >
                          <input
                            aria-label={`${table.table_id} 字段：${field.name}，类型：${field.data_type}`}
                            checked={grant.writable_fields.includes(field.name)}
                            className="accent-amber-300"
                            onChange={() => toggleField(grant, field.name)}
                            type="checkbox"
                          />
                          <span className="font-mono">{field.name}</span>
                          <span className="font-mono text-slate-400">{field.data_type}</span>
                        </label>
                      ))}
                      {table.fields.length === 0 ? (
                        <span className="text-[10px] text-slate-400">
                          当前安全目录没有字段摘要。
                        </span>
                      ) : null}
                    </div>
                    {!fieldsEnabled ? (
                      <p className="mt-1.5 text-[10px] leading-4 text-slate-400">
                        仅删除授权无需选择字段。
                      </p>
                    ) : null}
                  </fieldset>

                  <label className="mt-3 block max-w-[220px]">
                    <span className="text-[11px] font-semibold text-slate-300">
                      单次影响行数上限
                    </span>
                    <input
                      aria-label={`${table.table_id} 单次影响行数上限`}
                      className="modelmirror-form-control mt-1.5 h-9 w-full rounded-md border border-white/10 bg-slate-950 px-2.5 text-xs text-white outline-none focus:border-amber-300/50"
                      disabled={disabled}
                      max={100}
                      min={1}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        replaceGrant(grant.table_id, (current) => ({
                          ...current,
                          max_affected_rows:
                            Number.isInteger(value) && value >= 1 && value <= 100
                              ? value
                              : 1,
                        }));
                      }}
                      type="number"
                      value={grant.max_affected_rows}
                    />
                  </label>

                  {grantError ? (
                    <p className="mt-2 text-[10px] leading-4 text-rose-200" role="alert">
                      {grantError}
                    </p>
                  ) : (
                    <p className="mt-2 text-[10px] leading-4 text-emerald-200">
                      该表写授权已完整，仍需单独勾选对应写节点。
                    </p>
                  )}
                </div>
              ) : null}
            </div>
          );
        })}
        {catalog.length === 0 ? (
          <p className="text-[11px] leading-5 text-slate-400">
            当前没有带安全字段摘要的 Agent Table。
          </p>
        ) : null}
      </div>
    </section>
  );
}
