export const filePurposes = [
  "chat",
  "rag",
  "datax",
  "agent",
  "workflow",
] as const;

export const FILE_CAPABILITIES_VERSION =
  "modelmirror-file-capabilities-v2";
export const FILE_FORMAT_REGISTRY_VERSION = "modelmirror-file-formats-v5";

export type FilePurpose = (typeof filePurposes)[number];

export const fileInputKinds = [
  "document",
  "image",
  "audio",
  "video",
  "data_source",
  "image_reference",
  "audio_generation_image",
  "video_generation_frame",
  "video_generation_reference",
  "visual_analysis",
] as const;

export type FileInputKind = (typeof fileInputKinds)[number];

type FileFamily = "document" | "image" | "audio" | "video" | "dataset";
type SizeMeasure = "binary" | "encoded_payload";
type FileTransport = "multipart" | "data_url";
type FileRetention = "request" | "temporary" | "persistent";
type FileSupportLevel = "native" | "converted" | "specialized" | "unsupported";
type FileInteractionStatus = "ready" | "planned" | "disabled";
export type FileHandling = "native" | "extract";
export type FileAnalysisMode = "vision" | "provider_ocr";

export interface FileAnalysisOption {
  mode: FileAnalysisMode;
  format_ids: string[];
  provider: string;
  paid: boolean;
  max_pages: number;
  max_prompt_chars: number;
  requires_explicit_target: boolean;
  interaction_status: FileInteractionStatus;
  status_reason: string | null;
}

export interface FileHandlingOption {
  handling: FileHandling;
  format_ids: string[];
  support_level: FileSupportLevel;
  interaction_status: FileInteractionStatus;
  status_reason: string | null;
}

export interface FileFormatCapability {
  format_id: string;
  family: FileFamily;
  extensions: string[];
  media_types: string[];
  interaction_status: FileInteractionStatus;
  status_reason: string | null;
}

export interface FileInputCapability {
  purpose: FilePurpose;
  input_kind: FileInputKind;
  families: FileFamily[];
  max_bytes_per_file: number;
  max_files_per_request: number | null;
  max_total_bytes_per_request: number | null;
  size_measure: SizeMeasure;
  transport: FileTransport;
  retention: FileRetention;
  support_level: FileSupportLevel;
  interaction_status: FileInteractionStatus;
  parser_id: string | null;
  ui_entrypoint: string | null;
  status_reason: string | null;
  handling_options: FileHandlingOption[];
  analysis_options: FileAnalysisOption[];
  formats: FileFormatCapability[];
}

export interface FileCapabilitiesResponse {
  version: string;
  registry_version: string;
  requested_purpose: FilePurpose | null;
  requested_model_id: string | null;
  model_specific: boolean;
  capabilities: FileInputCapability[];
}

export interface FileAssetResponse {
  asset_id: string;
  purpose: FilePurpose;
  scope_id: string;
  display_name: string;
  format: string;
  media_type: string;
  byte_size: number;
  status:
    | "validating"
    | "processing"
    | "ready"
    | "failed"
    | "expired"
    | "deleting"
    | "deleted";
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ParsedSection {
  text: string;
  page: number | null;
  slide?: number | null;
  line_range: string | null;
  sheet?: string | null;
  row_range?: string | null;
  time_range?: string | null;
  heading_path?: string[] | null;
}

export interface ParsedDocumentPreview {
  asset_id: string;
  artifact_id: string;
  artifact_expires_at: string;
  format: string;
  title: string | null;
  sections: ParsedSection[];
  warnings: string[];
  extracted_chars: number;
  truncated: boolean;
}

export interface ChatFileConfirmation {
  asset_id: string;
  handling: FileHandling;
  confirmation_revision: number;
  confirmed_at: string;
  analysis_artifact_id?: string | null;
}

export interface FileAnalysisTarget {
  target_id: string;
  mode: FileAnalysisMode;
  connection_id: string;
  connection_name: string;
  model_id: string;
  model_name: string;
  provider: string;
  paid: boolean;
  cost_disclosure: string;
}

export interface FileAnalysisPreflight {
  asset_id: string;
  mode: FileAnalysisMode;
  target: FileAnalysisTarget;
  format: string;
  page_count: number;
  selected_pages: number[];
  prompt_sha256: string;
  config_digest: string;
  paid_confirmation_required: boolean;
  cost_disclosure: string;
  privacy_disclosure: string;
}

export interface FileAnalysisSection {
  kind: "ocr_text" | "visual_summary" | "visual_table" | "visual_chart";
  text: string;
  page: number;
}

export interface FileAnalysisArtifact {
  version: "modelmirror-file-analysis-artifact-v1";
  asset_id: string;
  source_filename: string;
  source_sha256: string;
  format: string;
  mode: FileAnalysisMode;
  target_id: string;
  connection_name: string;
  model_id: string;
  selected_pages: number[];
  sections: FileAnalysisSection[];
  warnings: string[];
  processed_pages: number;
  failed_pages: number[];
  extracted_chars: number;
  truncated: boolean;
}

export type FileAnalysisStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancel_requested"
  | "cancelled"
  | "interrupted";

export interface FileAnalysisJob {
  analysis_id: string;
  asset_id: string;
  scope_id: string;
  mode: FileAnalysisMode;
  target_id: string;
  selected_pages: number[];
  page_count: number;
  processed_pages: number;
  status: FileAnalysisStatus;
  result_artifact_id: string | null;
  result: FileAnalysisArtifact | null;
  actual_cost_usd: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export class FileAssetApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "FileAssetApiError";
    this.status = status;
    this.code = code;
  }
}

export interface FileSurfaceSummary {
  registryAvailable: boolean;
  chatDocumentDeclared: boolean;
  chatDocumentFormats: string[];
  ragFormats: string[];
  dataxFormats: string[];
  agentFormats: string[];
  workflowFormats: string[];
}

const EMPTY_FILE_SURFACE_SUMMARY: FileSurfaceSummary = {
  registryAvailable: false,
  chatDocumentDeclared: false,
  chatDocumentFormats: [],
  ragFormats: [],
  dataxFormats: [],
  agentFormats: [],
  workflowFormats: [],
};

const filePurposeSet = new Set<string>(filePurposes);
const fileInputKindSet = new Set<string>(fileInputKinds);
const fileFamilySet = new Set<string>([
  "document",
  "image",
  "audio",
  "video",
  "dataset",
]);
const sizeMeasureSet = new Set<string>(["binary", "encoded_payload"]);
const fileTransportSet = new Set<string>(["multipart", "data_url"]);
const fileRetentionSet = new Set<string>([
  "request",
  "temporary",
  "persistent",
]);
const fileSupportLevelSet = new Set<string>([
  "native",
  "converted",
  "specialized",
  "unsupported",
]);
const fileInteractionStatusSet = new Set<string>([
  "ready",
  "planned",
  "disabled",
]);
const fileHandlingSet = new Set<string>(["native", "extract"]);
const fileAnalysisModeSet = new Set<string>(["vision", "provider_ocr"]);
const fileAssetStatusSet = new Set<string>([
  "validating",
  "processing",
  "ready",
  "failed",
  "expired",
  "deleting",
  "deleted",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isEnumValue<T extends string>(
  value: unknown,
  allowed: Set<string>,
): value is T {
  return typeof value === "string" && allowed.has(value);
}

function isOptionalPositiveNumber(value: unknown) {
  return (
    value === null ||
    (typeof value === "number" && Number.isFinite(value) && value > 0)
  );
}

function parseFormat(value: unknown): FileFormatCapability | null {
  if (
    !isRecord(value) ||
    typeof value.format_id !== "string" ||
    !isEnumValue<FileFamily>(value.family, fileFamilySet) ||
    !isStringArray(value.extensions) ||
    !isStringArray(value.media_types) ||
    !isEnumValue<FileInteractionStatus>(
      value.interaction_status,
      fileInteractionStatusSet,
    ) ||
    !(value.status_reason === null || typeof value.status_reason === "string")
  ) {
    return null;
  }
  if (value.interaction_status !== "ready" && !value.status_reason) return null;
  return {
    format_id: value.format_id,
    family: value.family,
    extensions: [...value.extensions],
    media_types: [...value.media_types],
    interaction_status: value.interaction_status,
    status_reason: value.status_reason,
  };
}

function parseHandlingOption(value: unknown): FileHandlingOption | null {
  if (
    !isRecord(value) ||
    !isEnumValue<FileHandling>(value.handling, fileHandlingSet) ||
    !isStringArray(value.format_ids) ||
    value.format_ids.length === 0 ||
    !isEnumValue<FileSupportLevel>(value.support_level, fileSupportLevelSet) ||
    !isEnumValue<FileInteractionStatus>(
      value.interaction_status,
      fileInteractionStatusSet,
    ) ||
    !(value.status_reason === null || typeof value.status_reason === "string")
  ) {
    return null;
  }
  if (value.interaction_status !== "ready" && !value.status_reason) return null;
  return {
    handling: value.handling,
    format_ids: [...value.format_ids],
    support_level: value.support_level,
    interaction_status: value.interaction_status,
    status_reason: value.status_reason,
  };
}

function parseAnalysisOption(value: unknown): FileAnalysisOption | null {
  if (
    !isRecord(value) ||
    !isEnumValue<FileAnalysisMode>(value.mode, fileAnalysisModeSet) ||
    !isStringArray(value.format_ids) ||
    value.format_ids.length === 0 ||
    typeof value.provider !== "string" ||
    typeof value.paid !== "boolean" ||
    !Number.isInteger(value.max_pages) ||
    Number(value.max_pages) < 1 ||
    Number(value.max_pages) > 20 ||
    !Number.isInteger(value.max_prompt_chars) ||
    Number(value.max_prompt_chars) < 1 ||
    Number(value.max_prompt_chars) > 2_000 ||
    typeof value.requires_explicit_target !== "boolean" ||
    !isEnumValue<FileInteractionStatus>(
      value.interaction_status,
      fileInteractionStatusSet,
    ) ||
    !(value.status_reason === null || typeof value.status_reason === "string")
  ) {
    return null;
  }
  if (value.interaction_status !== "ready" && !value.status_reason) return null;
  return {
    mode: value.mode,
    format_ids: [...value.format_ids],
    provider: value.provider,
    paid: value.paid,
    max_pages: Number(value.max_pages),
    max_prompt_chars: Number(value.max_prompt_chars),
    requires_explicit_target: value.requires_explicit_target,
    interaction_status: value.interaction_status,
    status_reason: value.status_reason,
  };
}

function parseCapability(value: unknown): FileInputCapability | null {
  if (
    !isRecord(value) ||
    !isEnumValue<FilePurpose>(value.purpose, filePurposeSet) ||
    !isEnumValue<FileInputKind>(value.input_kind, fileInputKindSet) ||
    !isStringArray(value.families) ||
    !value.families.every((family) => fileFamilySet.has(family)) ||
    typeof value.max_bytes_per_file !== "number" ||
    !Number.isFinite(value.max_bytes_per_file) ||
    value.max_bytes_per_file <= 0 ||
    !isOptionalPositiveNumber(value.max_files_per_request) ||
    !isOptionalPositiveNumber(value.max_total_bytes_per_request) ||
    !isEnumValue<SizeMeasure>(value.size_measure, sizeMeasureSet) ||
    !isEnumValue<FileTransport>(value.transport, fileTransportSet) ||
    !isEnumValue<FileRetention>(value.retention, fileRetentionSet) ||
    !isEnumValue<FileSupportLevel>(value.support_level, fileSupportLevelSet) ||
    !isEnumValue<FileInteractionStatus>(
      value.interaction_status,
      fileInteractionStatusSet,
    ) ||
    !(value.parser_id === null || typeof value.parser_id === "string") ||
    !(value.ui_entrypoint === null || typeof value.ui_entrypoint === "string") ||
    !(value.status_reason === null || typeof value.status_reason === "string") ||
    !Array.isArray(value.handling_options) ||
    !Array.isArray(value.analysis_options) ||
    !Array.isArray(value.formats)
  ) {
    return null;
  }
  if (
    (value.interaction_status === "ready" &&
      (!value.parser_id || !value.ui_entrypoint)) ||
    (value.interaction_status !== "ready" && !value.status_reason)
  ) {
    return null;
  }
  const formats = value.formats.map(parseFormat);
  const handlingOptions = value.handling_options.map(parseHandlingOption);
  const analysisOptions = value.analysis_options.map(parseAnalysisOption);
  if (
    formats.some((format) => format === null) ||
    handlingOptions.some((option) => option === null) ||
    analysisOptions.some((option) => option === null)
  ) {
    return null;
  }
  return {
    purpose: value.purpose,
    input_kind: value.input_kind,
    families: [...value.families] as FileFamily[],
    max_bytes_per_file: value.max_bytes_per_file,
    max_files_per_request: value.max_files_per_request as number | null,
    max_total_bytes_per_request:
      value.max_total_bytes_per_request as number | null,
    size_measure: value.size_measure,
    transport: value.transport,
    retention: value.retention,
    support_level: value.support_level,
    interaction_status: value.interaction_status,
    parser_id: value.parser_id,
    ui_entrypoint: value.ui_entrypoint,
    status_reason: value.status_reason,
    handling_options: handlingOptions as FileHandlingOption[],
    analysis_options: analysisOptions as FileAnalysisOption[],
    formats: formats as FileFormatCapability[],
  };
}

export function parseFileCapabilities(
  value: unknown,
): FileCapabilitiesResponse | null {
  if (
    !isRecord(value) ||
    value.version !== FILE_CAPABILITIES_VERSION ||
    value.registry_version !== FILE_FORMAT_REGISTRY_VERSION ||
    !(
      value.requested_purpose === null ||
      isEnumValue<FilePurpose>(value.requested_purpose, filePurposeSet)
    ) ||
    !(value.requested_model_id === null || typeof value.requested_model_id === "string") ||
    typeof value.model_specific !== "boolean" ||
    !Array.isArray(value.capabilities)
  ) {
    return null;
  }
  const capabilities = value.capabilities.map(parseCapability);
  if (capabilities.some((capability) => capability === null)) return null;
  return {
    version: value.version,
    registry_version: value.registry_version,
    requested_purpose: value.requested_purpose,
    requested_model_id: value.requested_model_id,
    model_specific: value.model_specific,
    capabilities: capabilities as FileInputCapability[],
  };
}

export async function fetchFileCapabilities(
  signal?: AbortSignal,
  filters?: { purpose?: FilePurpose; modelId?: string },
): Promise<FileCapabilitiesResponse | null> {
  try {
    const params = new URLSearchParams();
    if (filters?.purpose) params.set("purpose", filters.purpose);
    if (filters?.modelId) params.set("model_id", filters.modelId);
    const query = params.toString();
    const response = await fetch(
      `/api/files/capabilities${query ? `?${query}` : ""}`,
      { signal },
    );
    if (!response.ok) return null;
    return parseFileCapabilities(await response.json());
  } catch {
    return null;
  }
}

function parseFileAsset(value: unknown): FileAssetResponse | null {
  if (
    !isRecord(value) ||
    typeof value.asset_id !== "string" ||
    !isEnumValue<FilePurpose>(value.purpose, filePurposeSet) ||
    typeof value.scope_id !== "string" ||
    typeof value.display_name !== "string" ||
    typeof value.format !== "string" ||
    typeof value.media_type !== "string" ||
    typeof value.byte_size !== "number" ||
    !Number.isFinite(value.byte_size) ||
    value.byte_size < 0 ||
    !isEnumValue<FileAssetResponse["status"]>(
      value.status,
      fileAssetStatusSet,
    ) ||
    !(value.expires_at === null || typeof value.expires_at === "string") ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    return null;
  }
  return value as unknown as FileAssetResponse;
}

export function parseDocumentPreview(value: unknown): ParsedDocumentPreview | null {
  if (
    !isRecord(value) ||
    typeof value.asset_id !== "string" ||
    typeof value.artifact_id !== "string" ||
    typeof value.artifact_expires_at !== "string" ||
    typeof value.format !== "string" ||
    !(value.title === null || typeof value.title === "string") ||
    !Array.isArray(value.sections) ||
    !isStringArray(value.warnings) ||
    typeof value.extracted_chars !== "number" ||
    !Number.isFinite(value.extracted_chars) ||
    value.extracted_chars < 0 ||
    typeof value.truncated !== "boolean"
  ) {
    return null;
  }
  const sections: ParsedSection[] = [];
  for (const section of value.sections) {
    const slide = section && isRecord(section) ? section.slide : undefined;
    const sheet = section && isRecord(section) ? section.sheet : undefined;
    const rowRange = section && isRecord(section) ? section.row_range : undefined;
    const timeRange = section && isRecord(section) ? section.time_range : undefined;
    const headingPath =
      section && isRecord(section) ? section.heading_path : undefined;
    if (
      !isRecord(section) ||
      typeof section.text !== "string" ||
      section.text.length === 0 ||
      !(
        section.page === null ||
        (typeof section.page === "number" &&
          Number.isInteger(section.page) &&
          section.page > 0)
      ) ||
      !(
        slide === undefined ||
        slide === null ||
        (typeof slide === "number" &&
          Number.isInteger(slide) &&
          slide > 0)
      ) ||
      !(
        section.line_range === null ||
        typeof section.line_range === "string"
      ) ||
      !(sheet === undefined || sheet === null || typeof sheet === "string") ||
      !(rowRange === undefined || rowRange === null || typeof rowRange === "string") ||
      !(
        timeRange === undefined ||
        timeRange === null ||
        typeof timeRange === "string"
      ) ||
      !(
        headingPath === undefined ||
        headingPath === null ||
        isStringArray(headingPath)
      )
    ) {
      return null;
    }
    sections.push({
      text: section.text,
      page: section.page,
      slide: typeof slide === "number" ? slide : null,
      line_range: section.line_range,
      sheet: typeof sheet === "string" ? sheet : null,
      row_range: typeof rowRange === "string" ? rowRange : null,
      time_range: typeof timeRange === "string" ? timeRange : null,
      heading_path: Array.isArray(headingPath) ? [...headingPath] : [],
    });
  }
  return {
    asset_id: value.asset_id,
    artifact_id: value.artifact_id,
    artifact_expires_at: value.artifact_expires_at,
    format: value.format,
    title: value.title,
    sections,
    warnings: [...value.warnings],
    extracted_chars: value.extracted_chars,
    truncated: value.truncated,
  };
}

async function apiError(response: Response): Promise<FileAssetApiError> {
  let message = `文件处理失败（${response.status}）`;
  let code = `http_${response.status}`;
  try {
    const payload = (await response.json()) as unknown;
    if (isRecord(payload)) {
      const detail = isRecord(payload.detail) ? payload.detail : payload;
      if (typeof detail.code === "string") code = detail.code;
      if (typeof detail.message === "string") message = detail.message;
      else if (typeof payload.error === "string") message = payload.error;
      else if (typeof payload.detail === "string") message = payload.detail;
    }
  } catch {
    // Keep the stable status-based fallback and never expose an upstream body.
  }
  return new FileAssetApiError(message, response.status, code);
}

export async function uploadChatFile(
  file: File,
  scopeId: string,
  signal?: AbortSignal,
): Promise<FileAssetResponse> {
  const form = new FormData();
  form.append("purpose", "chat");
  form.append("scope_id", scopeId);
  form.append("file", file);
  const response = await fetch("/api/files", {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw await apiError(response);
  const asset = parseFileAsset(await response.json());
  if (!asset) {
    throw new FileAssetApiError(
      "文件服务返回了无法识别的数据，请刷新后重试。",
      502,
      "invalid_file_asset_response",
    );
  }
  return asset;
}

export async function uploadChatAnalysisFile(
  file: File,
  scopeId: string,
  signal?: AbortSignal,
): Promise<FileAssetResponse> {
  const form = new FormData();
  form.append("purpose", "chat");
  form.append("scope_id", scopeId);
  form.append("input_kind", "visual_analysis");
  form.append("file", file);
  const response = await fetch("/api/files", {
    method: "POST",
    body: form,
    signal,
  });
  if (!response.ok) throw await apiError(response);
  const asset = parseFileAsset(await response.json());
  if (!asset) {
    throw new FileAssetApiError(
      "视觉/OCR 文件服务返回了无法识别的数据，请重试。",
      502,
      "invalid_file_asset_response",
    );
  }
  return asset;
}

function isPositiveIntegerArray(value: unknown): value is number[] {
  return (
    Array.isArray(value) &&
    value.every((item) => Number.isInteger(item) && Number(item) > 0)
  );
}

function parseAnalysisTarget(value: unknown): FileAnalysisTarget | null {
  if (
    !isRecord(value) ||
    typeof value.target_id !== "string" ||
    !isEnumValue<FileAnalysisMode>(value.mode, fileAnalysisModeSet) ||
    typeof value.connection_id !== "string" ||
    typeof value.connection_name !== "string" ||
    typeof value.model_id !== "string" ||
    typeof value.model_name !== "string" ||
    typeof value.provider !== "string" ||
    typeof value.paid !== "boolean" ||
    typeof value.cost_disclosure !== "string"
  ) {
    return null;
  }
  return value as unknown as FileAnalysisTarget;
}

function parseAnalysisArtifact(value: unknown): FileAnalysisArtifact | null {
  if (
    !isRecord(value) ||
    value.version !== "modelmirror-file-analysis-artifact-v1" ||
    typeof value.asset_id !== "string" ||
    typeof value.source_filename !== "string" ||
    typeof value.source_sha256 !== "string" ||
    typeof value.format !== "string" ||
    !isEnumValue<FileAnalysisMode>(value.mode, fileAnalysisModeSet) ||
    typeof value.target_id !== "string" ||
    typeof value.connection_name !== "string" ||
    typeof value.model_id !== "string" ||
    !isPositiveIntegerArray(value.selected_pages) ||
    !Array.isArray(value.sections) ||
    !isStringArray(value.warnings) ||
    !Number.isInteger(value.processed_pages) ||
    !isPositiveIntegerArray(value.failed_pages) ||
    !Number.isInteger(value.extracted_chars) ||
    typeof value.truncated !== "boolean"
  ) {
    return null;
  }
  const sections: FileAnalysisSection[] = [];
  for (const section of value.sections) {
    if (
      !isRecord(section) ||
      ![
        "ocr_text",
        "visual_summary",
        "visual_table",
        "visual_chart",
      ].includes(String(section.kind)) ||
      typeof section.text !== "string" ||
      !Number.isInteger(section.page) ||
      Number(section.page) < 1
    ) {
      return null;
    }
    sections.push(section as unknown as FileAnalysisSection);
  }
  return { ...(value as unknown as FileAnalysisArtifact), sections };
}

function parseAnalysisJob(value: unknown): FileAnalysisJob | null {
  if (
    !isRecord(value) ||
    typeof value.analysis_id !== "string" ||
    typeof value.asset_id !== "string" ||
    typeof value.scope_id !== "string" ||
    !isEnumValue<FileAnalysisMode>(value.mode, fileAnalysisModeSet) ||
    typeof value.target_id !== "string" ||
    !isPositiveIntegerArray(value.selected_pages) ||
    !Number.isInteger(value.page_count) ||
    !Number.isInteger(value.processed_pages) ||
    ![
      "queued",
      "running",
      "completed",
      "failed",
      "cancel_requested",
      "cancelled",
      "interrupted",
    ].includes(String(value.status)) ||
    !(
      value.result_artifact_id === null ||
      typeof value.result_artifact_id === "string"
    ) ||
    !(value.actual_cost_usd === null || typeof value.actual_cost_usd === "string") ||
    !(value.error_code === null || typeof value.error_code === "string") ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string" ||
    !(value.completed_at === null || typeof value.completed_at === "string")
  ) {
    return null;
  }
  const result = value.result === null ? null : parseAnalysisArtifact(value.result);
  if (value.result !== null && result === null) return null;
  return { ...(value as unknown as FileAnalysisJob), result };
}

export async function fetchFileAnalysisTargets(
  signal?: AbortSignal,
): Promise<FileAnalysisTarget[]> {
  const response = await fetch("/api/files/analysis-targets", { signal });
  if (!response.ok) throw await apiError(response);
  const payload = (await response.json()) as unknown;
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new FileAssetApiError(
      "视觉/OCR 目标清单无效，请刷新后重试。",
      502,
      "invalid_analysis_targets_response",
    );
  }
  const items = payload.items.map(parseAnalysisTarget);
  if (items.some((item) => item === null)) {
    throw new FileAssetApiError(
      "视觉/OCR 目标清单无效，请刷新后重试。",
      502,
      "invalid_analysis_targets_response",
    );
  }
  return items as FileAnalysisTarget[];
}

interface AnalysisRequestInput {
  scope_id: string;
  mode: FileAnalysisMode;
  target_id: string;
  selected_pages: number[];
  prompt: string;
}

async function analysisJsonRequest(
  path: string,
  body: object,
  signal?: AbortSignal,
) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<unknown>;
}

export async function preflightChatFileAnalysis(
  assetId: string,
  input: AnalysisRequestInput,
  signal?: AbortSignal,
): Promise<FileAnalysisPreflight> {
  const payload = await analysisJsonRequest(
    `/api/files/${encodeURIComponent(assetId)}/analysis-preflight`,
    {
      scope_id: input.scope_id,
      mode: input.mode,
      target_id: input.target_id,
      selected_pages: input.selected_pages,
      prompt: input.prompt,
    },
    signal,
  );
  if (
    !isRecord(payload) ||
    payload.asset_id !== assetId ||
    !isEnumValue<FileAnalysisMode>(payload.mode, fileAnalysisModeSet) ||
    !parseAnalysisTarget(payload.target) ||
    typeof payload.format !== "string" ||
    !Number.isInteger(payload.page_count) ||
    !isPositiveIntegerArray(payload.selected_pages) ||
    typeof payload.prompt_sha256 !== "string" ||
    typeof payload.config_digest !== "string" ||
    typeof payload.paid_confirmation_required !== "boolean" ||
    typeof payload.cost_disclosure !== "string" ||
    typeof payload.privacy_disclosure !== "string"
  ) {
    throw new FileAssetApiError(
      "视觉/OCR 预检响应无效，请重试。",
      502,
      "invalid_analysis_preflight_response",
    );
  }
  return payload as unknown as FileAnalysisPreflight;
}

export async function confirmChatFileAnalysis(
  assetId: string,
  input: AnalysisRequestInput & { paid_acknowledged: boolean },
  signal?: AbortSignal,
) {
  const payload = await analysisJsonRequest(
    `/api/files/${encodeURIComponent(assetId)}/analysis-confirm`,
    input,
    signal,
  );
  if (
    !isRecord(payload) ||
    payload.asset_id !== assetId ||
    !Number.isInteger(payload.confirmation_revision) ||
    Number(payload.confirmation_revision) < 1 ||
    typeof payload.config_digest !== "string" ||
    typeof payload.prompt_sha256 !== "string"
  ) {
    throw new FileAssetApiError(
      "视觉/OCR 确认响应无效，请重试。",
      502,
      "invalid_analysis_confirmation_response",
    );
  }
  return payload as unknown as {
    confirmation_revision: number;
    config_digest: string;
    prompt_sha256: string;
  };
}

export async function createChatFileAnalysis(
  assetId: string,
  input: AnalysisRequestInput & {
    paid_acknowledged: boolean;
    confirmation_revision: number;
  },
  signal?: AbortSignal,
): Promise<FileAnalysisJob> {
  const payload = await analysisJsonRequest(
    `/api/files/${encodeURIComponent(assetId)}/analyses`,
    input,
    signal,
  );
  const job = parseAnalysisJob(payload);
  if (!job) {
    throw new FileAssetApiError(
      "视觉/OCR 任务响应无效，请刷新后重试。",
      502,
      "invalid_analysis_job_response",
    );
  }
  return job;
}

export async function fetchChatFileAnalysis(
  assetId: string,
  analysisId: string,
  scopeId: string,
  signal?: AbortSignal,
): Promise<FileAnalysisJob> {
  const params = new URLSearchParams({ scope_id: scopeId });
  const response = await fetch(
    `/api/files/${encodeURIComponent(assetId)}/analyses/${encodeURIComponent(analysisId)}?${params}`,
    { signal },
  );
  if (!response.ok) throw await apiError(response);
  const job = parseAnalysisJob(await response.json());
  if (!job) throw new FileAssetApiError("视觉/OCR 任务响应无效。", 502, "invalid_analysis_job_response");
  return job;
}

export async function listChatFileAnalyses(
  scopeId: string,
  signal?: AbortSignal,
): Promise<FileAnalysisJob[]> {
  const params = new URLSearchParams({ purpose: "chat", scope_id: scopeId });
  const response = await fetch(`/api/files/analyses?${params}`, { signal });
  if (!response.ok) throw await apiError(response);
  const payload = (await response.json()) as unknown;
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new FileAssetApiError("视觉/OCR 任务列表无效。", 502, "invalid_analysis_job_list");
  }
  const items = payload.items.map(parseAnalysisJob);
  if (items.some((item) => item === null)) {
    throw new FileAssetApiError("视觉/OCR 任务列表无效。", 502, "invalid_analysis_job_list");
  }
  return items as FileAnalysisJob[];
}

export async function cancelChatFileAnalysis(
  assetId: string,
  analysisId: string,
  scopeId: string,
): Promise<FileAnalysisJob> {
  const params = new URLSearchParams({ scope_id: scopeId });
  const response = await fetch(
    `/api/files/${encodeURIComponent(assetId)}/analyses/${encodeURIComponent(analysisId)}?${params}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw await apiError(response);
  const job = parseAnalysisJob(await response.json());
  if (!job) throw new FileAssetApiError("取消响应无效。", 502, "invalid_analysis_job_response");
  return job;
}

async function requestPreview(
  assetId: string,
  scopeId: string,
  method: "GET" | "POST",
  signal?: AbortSignal,
): Promise<ParsedDocumentPreview> {
  const params = new URLSearchParams({ purpose: "chat", scope_id: scopeId });
  const response = await fetch(
    `/api/files/${encodeURIComponent(assetId)}/${
      method === "POST" ? "parse" : "preview"
    }?${params}`,
    { method, signal },
  );
  if (!response.ok) throw await apiError(response);
  const preview = parseDocumentPreview(await response.json());
  if (!preview) {
    throw new FileAssetApiError(
      "文件预览返回了无法识别的数据，请重新上传。",
      502,
      "invalid_file_preview_response",
    );
  }
  return preview;
}

export function parseChatFile(
  assetId: string,
  scopeId: string,
  signal?: AbortSignal,
) {
  return requestPreview(assetId, scopeId, "POST", signal);
}

export function fetchChatFilePreview(
  assetId: string,
  scopeId: string,
  signal?: AbortSignal,
) {
  return requestPreview(assetId, scopeId, "GET", signal);
}

export async function deleteChatFile(assetId: string, scopeId: string) {
  const params = new URLSearchParams({ purpose: "chat", scope_id: scopeId });
  const response = await fetch(
    `/api/files/${encodeURIComponent(assetId)}?${params}`,
    { method: "DELETE" },
  );
  if (!response.ok && response.status !== 202) throw await apiError(response);
}

export async function confirmChatFile(
  assetId: string,
  scopeId: string,
  handling: FileHandling,
  signal?: AbortSignal,
  analysis?: { artifactId: string; prompt: string },
): Promise<ChatFileConfirmation> {
  const params = new URLSearchParams({ purpose: "chat", scope_id: scopeId });
  const response = await fetch(
    `/api/files/${encodeURIComponent(assetId)}/confirm?${params}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        handling,
        analysis_artifact_id: analysis?.artifactId,
        analysis_prompt: analysis?.prompt,
      }),
      signal,
    },
  );
  if (!response.ok) throw await apiError(response);
  const payload = (await response.json()) as Partial<ChatFileConfirmation>;
  if (
    payload.asset_id !== assetId ||
    payload.handling !== handling ||
    !Number.isInteger(payload.confirmation_revision) ||
    Number(payload.confirmation_revision) < 1 ||
    typeof payload.confirmed_at !== "string"
  ) {
    throw new FileAssetApiError(
      "文件确认响应无效，请重试。",
      502,
      "file_confirmation_invalid",
    );
  }
  if (
    analysis &&
    payload.analysis_artifact_id !== analysis.artifactId
  ) {
    throw new FileAssetApiError(
      "识别结果确认响应与当前任务不一致，请重新确认。",
      409,
      "analysis_confirmation_mismatch",
    );
  }
  return payload as ChatFileConfirmation;
}

function chatFileScopeStorageKey(modelId: string) {
  return `modelmirror-chat-file-scope:${modelId}`;
}

export function createChatFileScopeId() {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `chat-${random}`.replace(/[^A-Za-z0-9._:-]/g, "").slice(0, 128);
}

export function activateChatFileScope(modelId: string, scopeId: string) {
  const key = chatFileScopeStorageKey(modelId);
  const previousScopeId = window.sessionStorage.getItem(key);
  window.sessionStorage.setItem(key, scopeId);
  return previousScopeId;
}

export function rotateChatFileScope(modelId: string) {
  const scopeId = createChatFileScopeId();
  const previousScopeId = activateChatFileScope(modelId, scopeId);
  return { scopeId, previousScopeId };
}

export function forgetChatFileScope(modelId: string, expectedScopeId: string) {
  const key = chatFileScopeStorageKey(modelId);
  if (window.sessionStorage.getItem(key) === expectedScopeId) {
    window.sessionStorage.removeItem(key);
  }
}

export async function purgeChatFileScope(scopeId: string): Promise<boolean> {
  if (!scopeId) return true;
  try {
    const params = new URLSearchParams({ purpose: "chat" });
    const response = await fetch(
      `/api/files/scopes/${encodeURIComponent(scopeId)}?${params}`,
      { method: "DELETE" },
    );
    return response.ok || response.status === 202;
  } catch {
    return false;
  }
}

export function extensionsForPurpose(
  registry: FileCapabilitiesResponse,
  purpose: FilePurpose,
  inputKind?: FileInputKind,
) {
  return Array.from(
    new Set(
      registry.capabilities
        .filter(
          (item) =>
            item.purpose === purpose &&
            item.interaction_status === "ready" &&
            (inputKind === undefined || item.input_kind === inputKind),
        )
        .flatMap((item) => item.formats)
        .filter((format) => format.interaction_status === "ready")
        .flatMap((format) => format.extensions)
        .map((extension) => extension.trim().toLowerCase())
        .filter(Boolean),
    ),
  ).sort((left, right) => left.localeCompare(right));
}

export function deriveFileSurfaceSummary(
  registry: FileCapabilitiesResponse | null,
): FileSurfaceSummary {
  if (!registry) return EMPTY_FILE_SURFACE_SUMMARY;
  return {
    registryAvailable: true,
    chatDocumentDeclared: registry.capabilities.some(
      (item) => item.purpose === "chat" && item.input_kind === "document",
    ),
    chatDocumentFormats: extensionsForPurpose(registry, "chat", "document"),
    ragFormats: extensionsForPurpose(registry, "rag"),
    dataxFormats: extensionsForPurpose(registry, "datax"),
    agentFormats: extensionsForPurpose(registry, "agent"),
    workflowFormats: extensionsForPurpose(registry, "workflow"),
  };
}
