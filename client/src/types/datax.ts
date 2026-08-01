export type DataXStatus = "active" | "archived" | "pending" | "processing" | "ready" | "failed";

export interface DataXProject {
  project_id: string;
  name: string;
  description: string;
  status: "active" | "archived";
  revision: number;
  created_at: number;
  updated_at: number;
}

export interface DataXColumnProfile {
  name: string;
  data_type: string;
  null_count: number;
  null_rate: number;
  unique_count: number;
  min?: unknown;
  max?: unknown;
}

export interface DataXSource {
  source_id: string;
  project_id: string;
  name: string;
  file_name: string;
  file_type: "csv" | "xlsx" | "parquet";
  byte_size: number;
  row_count: number;
  column_count: number;
  status: DataXStatus;
  profile: { row_count?: number; columns?: DataXColumnProfile[] };
  error: string;
  created_at: number;
  updated_at: number;
}

export interface DataXImportJob {
  job_id: string;
  project_id: string;
  source_id: string;
  status: "pending" | "processing" | "ready" | "failed";
  attempt_count: number;
  error: string;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
}

export interface DataXField {
  field_id: string;
  entity_id: string;
  source_field: string;
  name: string;
  label: string;
  data_type: string;
  role: "dimension" | "time" | "measure" | "hidden";
}

export interface DataXModel {
  model_id: string;
  project_id: string;
  name: string;
  description: string;
  entities: Array<{ entity_id: string; source_id: string; alias: string; label: string }>;
  joins: Array<Record<string, string>>;
  fields: DataXField[];
  revision: number;
  created_at: number;
  updated_at: number;
}

export interface DataXIndicator {
  indicator_id: string;
  project_id: string;
  model_id: string;
  code: string;
  name: string;
  description: string;
  indicator_type: "basic" | "derived";
  aggregation: string | null;
  measure_field: string | null;
  formula: string | null;
  default_dimensions: string[];
  time_field: string | null;
  status: "draft" | "published" | "archived";
  revision: number;
  current_version: number | null;
  tags: string[];
}

export interface DataXProposal {
  proposal_id: string;
  project_id: string;
  model_id: string;
  indicator_id: string | null;
  proposal_type: "create" | "update";
  title: string;
  payload: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "cancelled";
  revision: number;
  source_xpert_id: string | null;
  source_run_id: string | null;
  created_at: number;
  updated_at: number;
  reason: string;
}

export interface DataXProjectDetail extends DataXProject {
  sources: DataXSource[];
  models: DataXModel[];
  indicators: DataXIndicator[];
}

export interface DataXResult {
  artifact_id: string;
  project_id: string;
  model_id: string;
  view: "kpi" | "table" | "line" | "bar";
  columns: string[];
  rows: Array<Record<string, unknown>>;
  row_count: number;
  truncated: boolean;
  warnings: string[];
}
