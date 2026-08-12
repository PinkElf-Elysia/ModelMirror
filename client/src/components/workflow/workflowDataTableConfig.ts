import {
  type AgentTableFieldType,
  type JsonValue,
} from "../../types/agentTables";

export type DataTableValueBinding =
  | { source: "literal"; value: JsonValue }
  | { source: "variable"; variable: string };

export type DataTableFilterOperator =
  | "eq"
  | "ne"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "in"
  | "contains"
  | "is_null";

export interface DataTableFilterCondition {
  field: string;
  operator: DataTableFilterOperator;
  value?: DataTableValueBinding;
}

export interface DataTableFilterGroup {
  logic: "and" | "or";
  items: Array<DataTableFilterGroup | DataTableFilterCondition>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function defaultLiteralForType(type: AgentTableFieldType): JsonValue {
  if (type === "integer" || type === "number") return 0;
  if (type === "boolean") return false;
  if (type === "json") return {};
  return "";
}

export function createLiteralBinding(
  type: AgentTableFieldType,
): DataTableValueBinding {
  return { source: "literal", value: defaultLiteralForType(type) };
}

export function normalizeValueBinding(
  value: unknown,
  type: AgentTableFieldType = "string",
): DataTableValueBinding {
  if (isRecord(value) && value.source === "variable") {
    return {
      source: "variable",
      variable: typeof value.variable === "string" ? value.variable : "",
    };
  }
  if (isRecord(value) && value.source === "literal" && "value" in value) {
    return { source: "literal", value: value.value as JsonValue };
  }
  return createLiteralBinding(type);
}

function normalizeCondition(value: Record<string, unknown>): DataTableFilterCondition {
  const operator = String(value.operator || "eq") as DataTableFilterOperator;
  const condition: DataTableFilterCondition = {
    field: typeof value.field === "string" ? value.field : "",
    operator,
  };
  if (operator !== "is_null") {
    condition.value = normalizeValueBinding(value.value);
  }
  return condition;
}

export function normalizeFilterGroup(value: unknown): DataTableFilterGroup {
  if (!isRecord(value) || !Array.isArray(value.items)) {
    return { logic: "and", items: [] };
  }
  return {
    logic: value.logic === "or" ? "or" : "and",
    items: value.items
      .filter(isRecord)
      .map((item) =>
        Array.isArray(item.items)
          ? normalizeFilterGroup(item)
          : normalizeCondition(item),
      ),
  };
}

export function compactFilterGroup(
  group: DataTableFilterGroup,
): Record<string, unknown> {
  if (group.items.length === 0) return {};
  return {
    logic: group.logic,
    items: group.items.map((item) =>
      "items" in item ? compactFilterGroup(item) : item,
    ),
  };
}

export function formatLiteral(value: JsonValue): string {
  if (typeof value === "string") return value;
  if (value === null) return "null";
  return JSON.stringify(value, null, 2);
}

export function parseLiteral(
  raw: string,
  type: AgentTableFieldType,
): { ok: true; value: JsonValue } | { ok: false; error: string } {
  if (type === "string" || type === "datetime") {
    return { ok: true, value: raw };
  }
  if (type === "integer") {
    const parsed = Number(raw);
    return Number.isInteger(parsed)
      ? { ok: true, value: parsed }
      : { ok: false, error: "请输入整数。" };
  }
  if (type === "number") {
    const parsed = Number(raw);
    return Number.isFinite(parsed)
      ? { ok: true, value: parsed }
      : { ok: false, error: "请输入有效数字。" };
  }
  if (type === "boolean") {
    if (raw === "true" || raw === "false") {
      return { ok: true, value: raw === "true" };
    }
    return { ok: false, error: "布尔值必须为 true 或 false。" };
  }
  try {
    return { ok: true, value: JSON.parse(raw) as JsonValue };
  } catch {
    return { ok: false, error: "请输入合法 JSON。" };
  }
}
