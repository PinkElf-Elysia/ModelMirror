export type JsonValue =
  | null
  | string
  | number
  | boolean
  | JsonValue[]
  | { [key: string]: JsonValue };

export type AgentTableStatus = "draft" | "published" | "archived";
export type AgentTableFieldType =
  | "string"
  | "integer"
  | "number"
  | "boolean"
  | "datetime"
  | "json";

export interface AgentTableField {
  field_id: string;
  name: string;
  label: string;
  description: string;
  data_type: AgentTableFieldType;
  required: boolean;
  has_default: boolean;
  default_value: JsonValue;
}

export interface AgentTableDefinition {
  table_id: string;
  name: string;
  description: string;
  status: AgentTableStatus;
  draft_revision: number;
  active_schema_version: number | null;
  fields: AgentTableField[];
  created_at: number;
  updated_at: number;
}

export interface AgentTableSchemaVersion {
  table_id: string;
  version: number;
  draft_revision: number;
  fields: AgentTableField[];
  checksum: string;
  published_at: number;
}

export interface AgentTableRecord {
  record_id: string;
  table_id: string;
  schema_version: number;
  data: Record<string, JsonValue>;
  revision: number;
  created_at: number;
  updated_at: number;
}

export interface AgentTableDetail {
  table: AgentTableDefinition;
  schema_versions: AgentTableSchemaVersion[];
  record_count: number;
}

export async function requestAgentTableJson<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, init);
  const payload = (await response.json().catch(() => ({}))) as {
    detail?: string;
  };
  if (!response.ok) {
    throw new Error(payload.detail || `请求失败：${response.status}`);
  }
  return payload as T;
}

