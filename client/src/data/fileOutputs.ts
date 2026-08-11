import type { FileHandling, FilePurpose } from "./fileCapabilities";

export const FILE_OUTPUT_CAPABILITIES_VERSION =
  "modelmirror-file-output-capabilities-v1";
export const FILE_OUTPUT_REGISTRY_VERSION = "modelmirror-file-formats-v5";

export type FileOutputStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancel_requested"
  | "cancelled"
  | "interrupted"
  | "deleting"
  | "deleted"
  | "expired";
export type FileOutputPreviewKind =
  | "text"
  | "document"
  | "image"
  | "audio"
  | "video"
  | "none";
export type FileOutputAction =
  | "preview"
  | "download"
  | "reuse"
  | "save_rag"
  | "delete";

export interface FileOutputFormatCapability {
  format_id: string;
  media_types: string[];
  preview_kind: FileOutputPreviewKind;
  actions: FileOutputAction[];
  generation_kind: "text" | "document" | "workbook" | "presentation" | "captured";
  interaction_status: "ready" | "planned" | "disabled";
  status_reason: string | null;
}

export interface FileOutputCapabilities {
  version: typeof FILE_OUTPUT_CAPABILITIES_VERSION;
  registry_version: typeof FILE_OUTPUT_REGISTRY_VERSION;
  requested_purpose: FilePurpose;
  requested_model_id: string | null;
  model_specific: boolean;
  interaction_status: "ready" | "planned" | "disabled";
  status_reason: string | null;
  limits: {
    max_files_per_turn: number;
    max_bytes_per_file: number;
    max_total_bytes_per_turn: number;
    max_spec_bytes: number;
    max_spec_chars: number;
    hard_ttl_seconds: number;
  };
  formats: FileOutputFormatCapability[];
}

export interface FileOutput {
  output_id: string;
  asset_id: string | null;
  purpose: FilePurpose;
  scope_id: string;
  producer_kind: string;
  display_name: string;
  format: string;
  media_type: string;
  byte_size: number;
  preview_kind: FileOutputPreviewKind;
  status: FileOutputStatus;
  expires_at: string | null;
  warnings: string[];
  error_code: string | null;
  source_run_id: string | null;
  source_message_id: string | null;
  source_node_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface FileOutputPreview {
  output_id: string;
  preview_kind: "text" | "document" | "none";
  text: string | null;
  document: Record<string, unknown> | null;
  truncated: boolean;
  warnings: string[];
}

export interface FileOutputReuseConfirmation {
  output_id: string;
  asset_id: string;
  handling: FileHandling;
  target_id: string;
  confirmation_revision: number;
  output_confirmation_revision: number;
  expires_at: string;
  confirmed_at: string;
}

export class FileOutputApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "FileOutputApiError";
    this.status = status;
    this.code = code;
  }
}

const purposes = new Set(["chat", "rag", "datax", "agent", "workflow"]);
const statuses = new Set<FileOutputStatus>([
  "queued",
  "running",
  "completed",
  "failed",
  "cancel_requested",
  "cancelled",
  "interrupted",
  "deleting",
  "deleted",
  "expired",
]);
const previewKinds = new Set<FileOutputPreviewKind>([
  "text",
  "document",
  "image",
  "audio",
  "video",
  "none",
]);
const actions = new Set<FileOutputAction>([
  "preview",
  "download",
  "reuse",
  "save_rag",
  "delete",
]);

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function string(value: unknown, max = 1_000) {
  return typeof value === "string" && value.length <= max ? value : null;
}

function nullableString(value: unknown, max = 1_000) {
  return value === null ? null : string(value, max);
}

function integer(value: unknown, minimum = 0) {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum
    ? value
    : null;
}

function stringArray(value: unknown, maxItems = 50, maxLength = 500) {
  if (!Array.isArray(value) || value.length > maxItems) return null;
  const parsed = value.map((item) => string(item, maxLength));
  return parsed.every((item): item is string => item !== null) ? parsed : null;
}

function parsePurpose(value: unknown): FilePurpose | null {
  return typeof value === "string" && purposes.has(value)
    ? (value as FilePurpose)
    : null;
}

export function parseFileOutput(value: unknown): FileOutput | null {
  const item = record(value);
  if (!item) return null;
  const purpose = parsePurpose(item.purpose);
  const status = statuses.has(item.status as FileOutputStatus)
    ? (item.status as FileOutputStatus)
    : null;
  const previewKind = previewKinds.has(item.preview_kind as FileOutputPreviewKind)
    ? (item.preview_kind as FileOutputPreviewKind)
    : null;
  const warnings = stringArray(item.warnings, 20, 500);
  const outputId = string(item.output_id, 80);
  const scopeId = string(item.scope_id, 256);
  const producerKind = string(item.producer_kind, 64);
  const displayName = string(item.display_name, 255);
  const format = string(item.format, 64);
  const mediaType = string(item.media_type, 160);
  const byteSize = integer(item.byte_size);
  const createdAt = string(item.created_at, 64);
  const updatedAt = string(item.updated_at, 64);
  const assetId = nullableString(item.asset_id, 80);
  const expiresAt = nullableString(item.expires_at, 64);
  const errorCode = nullableString(item.error_code, 160);
  const sourceRunId = nullableString(item.source_run_id, 256);
  const sourceMessageId = nullableString(item.source_message_id, 256);
  const sourceNodeId = nullableString(item.source_node_id, 256);
  if (
    !purpose ||
    !status ||
    !previewKind ||
    !warnings ||
    !outputId ||
    !scopeId ||
    !producerKind ||
    !displayName ||
    !format ||
    !mediaType ||
    byteSize === null ||
    !createdAt ||
    !updatedAt ||
    assetId === null && item.asset_id !== null ||
    expiresAt === null && item.expires_at !== null ||
    errorCode === null && item.error_code !== null ||
    sourceRunId === null && item.source_run_id !== null ||
    sourceMessageId === null && item.source_message_id !== null ||
    sourceNodeId === null && item.source_node_id !== null
  ) return null;
  return {
    output_id: outputId,
    asset_id: assetId,
    purpose,
    scope_id: scopeId,
    producer_kind: producerKind,
    display_name: displayName,
    format,
    media_type: mediaType,
    byte_size: byteSize,
    preview_kind: previewKind,
    status,
    expires_at: expiresAt,
    warnings,
    error_code: errorCode,
    source_run_id: sourceRunId,
    source_message_id: sourceMessageId,
    source_node_id: sourceNodeId,
    created_at: createdAt,
    updated_at: updatedAt,
  };
}

export function parseFileOutputCapabilities(
  value: unknown,
): FileOutputCapabilities | null {
  const payload = record(value);
  if (
    !payload ||
    payload.version !== FILE_OUTPUT_CAPABILITIES_VERSION ||
    payload.registry_version !== FILE_OUTPUT_REGISTRY_VERSION
  ) return null;
  const purpose = parsePurpose(payload.requested_purpose);
  const status = payload.interaction_status;
  const modelId = nullableString(payload.requested_model_id, 256);
  const statusReason = nullableString(payload.status_reason, 500);
  const limits = record(payload.limits);
  if (
    !purpose ||
    (status !== "ready" && status !== "planned" && status !== "disabled") ||
    typeof payload.model_specific !== "boolean" ||
    (modelId === null && payload.requested_model_id !== null) ||
    (statusReason === null && payload.status_reason !== null) ||
    !limits ||
    !Array.isArray(payload.formats)
  ) return null;
  const parsedFormats = payload.formats.map((candidate) => {
    const item = record(candidate);
    if (!item) return null;
    const formatId = string(item.format_id, 64);
    const mediaTypes = stringArray(item.media_types, 20, 160);
    const rawActions = item.actions;
    const actionCount = Array.isArray(rawActions) ? rawActions.length : null;
    const itemActions = Array.isArray(rawActions)
      ? rawActions.filter((action): action is FileOutputAction =>
          typeof action === "string" && actions.has(action as FileOutputAction),
        )
      : null;
    const previewKind = previewKinds.has(item.preview_kind as FileOutputPreviewKind)
      ? (item.preview_kind as FileOutputPreviewKind)
      : null;
    const generationKind = item.generation_kind;
    const interactionStatus = item.interaction_status;
    const reason = nullableString(item.status_reason, 500);
    if (
      !formatId ||
      !mediaTypes ||
      !itemActions ||
      actionCount === null ||
      itemActions.length !== actionCount ||
      !previewKind ||
      !["text", "document", "workbook", "presentation", "captured"].includes(
        String(generationKind),
      ) ||
      !["ready", "planned", "disabled"].includes(String(interactionStatus)) ||
      (reason === null && item.status_reason !== null)
    ) return null;
    return {
      format_id: formatId,
      media_types: mediaTypes,
      preview_kind: previewKind,
      actions: itemActions,
      generation_kind: generationKind as FileOutputFormatCapability["generation_kind"],
      interaction_status: interactionStatus as FileOutputFormatCapability["interaction_status"],
      status_reason: reason,
    };
  });
  if (parsedFormats.some((item) => item === null)) return null;
  const numericLimits = {
    max_files_per_turn: integer(limits.max_files_per_turn, 1),
    max_bytes_per_file: integer(limits.max_bytes_per_file, 1),
    max_total_bytes_per_turn: integer(limits.max_total_bytes_per_turn, 1),
    max_spec_bytes: integer(limits.max_spec_bytes, 1),
    max_spec_chars: integer(limits.max_spec_chars, 1),
    hard_ttl_seconds: integer(limits.hard_ttl_seconds, 1),
  };
  if (Object.values(numericLimits).some((item) => item === null)) return null;
  return {
    version: FILE_OUTPUT_CAPABILITIES_VERSION,
    registry_version: FILE_OUTPUT_REGISTRY_VERSION,
    requested_purpose: purpose,
    requested_model_id: modelId,
    model_specific: payload.model_specific,
    interaction_status: status,
    status_reason: statusReason,
    limits: numericLimits as FileOutputCapabilities["limits"],
    formats: parsedFormats as FileOutputFormatCapability[],
  };
}

async function apiError(response: Response): Promise<FileOutputApiError> {
  let message = `请求失败：${response.status}`;
  let code = "file_output_request_failed";
  try {
    const payload = record(await response.json());
    const detail = record(payload?.detail);
    const detailMessage = string(detail?.message, 1_000);
    const detailCode = string(detail?.code, 160);
    const topMessage = string(payload?.message, 1_000);
    if (detailMessage) message = detailMessage;
    else if (topMessage) message = topMessage;
    if (detailCode) code = detailCode;
  } catch {
    // Keep the stable generic message; never surface an untrusted response body.
  }
  return new FileOutputApiError(message, response.status, code);
}

function scopedUrl(path: string, purpose: FilePurpose, scopeId: string) {
  const params = new URLSearchParams({ purpose, scope_id: scopeId });
  return `/api/files${path}?${params.toString()}`;
}

export async function fetchFileOutputCapabilities(
  purpose: FilePurpose,
  modelId?: string,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ purpose });
  if (modelId) params.set("model_id", modelId);
  const response = await fetch(`/api/files/output-capabilities?${params}`, { signal });
  if (!response.ok) throw await apiError(response);
  const parsed = parseFileOutputCapabilities(await response.json());
  if (!parsed) throw new FileOutputApiError("输出能力响应无效，已安全禁用。", 502, "file_output_capability_invalid");
  return parsed;
}

export async function fetchFileOutputs(
  purpose: FilePurpose,
  scopeId: string,
  signal?: AbortSignal,
) {
  const response = await fetch(scopedUrl("/outputs", purpose, scopeId), { signal });
  if (!response.ok) throw await apiError(response);
  const payload = record(await response.json());
  if (!payload || !Array.isArray(payload.items)) throw new FileOutputApiError("输出列表响应无效。", 502, "file_output_list_invalid");
  const items = payload.items.map(parseFileOutput);
  if (items.some((item) => item === null)) throw new FileOutputApiError("输出列表响应无效。", 502, "file_output_list_invalid");
  return items as FileOutput[];
}

export interface FileOutputKnowledgeBase {
  id: string;
  name: string;
  deletion_status?: string;
}

export async function fetchFileOutputKnowledgeBases(signal?: AbortSignal) {
  const response = await fetch("/api/rag/knowledge_bases", { signal });
  if (!response.ok) throw await apiError(response);
  const payload = record(await response.json());
  if (!payload || !Array.isArray(payload.knowledge_bases)) {
    throw new FileOutputApiError(
      "资料库列表响应无效。",
      502,
      "file_output_rag_list_invalid",
    );
  }
  const items = payload.knowledge_bases.flatMap((value) => {
    const item = record(value);
    const id = string(item?.id, 256);
    const name = string(item?.name, 160);
    const deletionStatus = nullableString(item?.deletion_status, 80);
    if (!id || !name || (deletionStatus === null && item?.deletion_status != null)) return [];
    return [{ id, name, deletion_status: deletionStatus ?? undefined }];
  });
  if (items.length !== payload.knowledge_bases.length) {
    throw new FileOutputApiError(
      "资料库列表响应无效。",
      502,
      "file_output_rag_list_invalid",
    );
  }
  return items.filter((item) => !item.deletion_status || item.deletion_status === "active");
}

export async function saveFileOutputToRag(
  output: FileOutput,
  knowledgeBaseId: string,
) {
  const response = await fetch(
    `/api/rag/knowledge_bases/${encodeURIComponent(knowledgeBaseId)}/documents/from-file-output`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        output_id: output.output_id,
        purpose: output.purpose,
        scope_id: output.scope_id,
      }),
    },
  );
  if (!response.ok) throw await apiError(response);
  const payload = record(await response.json());
  const documentId = string(payload?.id, 256);
  if (!payload || !documentId) {
    throw new FileOutputApiError(
      "资料库派生响应无效。",
      502,
      "file_output_rag_response_invalid",
    );
  }
  return { documentId };
}

export async function fetchFileOutputPreview(
  outputId: string,
  purpose: FilePurpose,
  scopeId: string,
  signal?: AbortSignal,
): Promise<FileOutputPreview> {
  const response = await fetch(scopedUrl(`/outputs/${encodeURIComponent(outputId)}/preview`, purpose, scopeId), { signal });
  if (!response.ok) throw await apiError(response);
  const payload = record(await response.json());
  const previewKind = payload?.preview_kind;
  const warnings = stringArray(payload?.warnings, 20, 500);
  const output = payload ? string(payload.output_id, 80) : null;
  const text = payload ? nullableString(payload.text, 500_000) : null;
  const document = payload?.document === null ? null : record(payload?.document);
  if (!payload || !output || !warnings || !["text", "document", "none"].includes(String(previewKind)) || typeof payload.truncated !== "boolean" || (text === null && payload.text !== null) || (document === null && payload.document !== null)) {
    throw new FileOutputApiError("输出预览响应无效。", 502, "file_output_preview_invalid");
  }
  return { output_id: output, preview_kind: previewKind as FileOutputPreview["preview_kind"], text, document, truncated: payload.truncated, warnings };
}

export function fileOutputPreviewUrl(outputId: string, purpose: FilePurpose, scopeId: string) {
  return scopedUrl(`/outputs/${encodeURIComponent(outputId)}/preview`, purpose, scopeId);
}

export function fileOutputDownloadUrl(outputId: string, purpose: FilePurpose, scopeId: string) {
  return scopedUrl(`/outputs/${encodeURIComponent(outputId)}/download`, purpose, scopeId);
}

export async function retryFileOutput(outputId: string, purpose: FilePurpose, scopeId: string) {
  const response = await fetch(scopedUrl(`/outputs/${encodeURIComponent(outputId)}/retry`, purpose, scopeId), { method: "POST" });
  if (!response.ok) throw await apiError(response);
  const parsed = parseFileOutput(await response.json());
  if (!parsed) throw new FileOutputApiError("输出重试响应无效。", 502, "file_output_response_invalid");
  return parsed;
}

export async function confirmFileOutputReuse(
  outputId: string,
  scopeId: string,
  targetId: string,
  handling: FileHandling = "extract",
  purpose: Extract<FilePurpose, "chat" | "agent" | "workflow"> = "chat",
) {
  const response = await fetch(scopedUrl(`/outputs/${encodeURIComponent(outputId)}/confirm-reuse`, purpose, scopeId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ handling, target_id: targetId, gateway: "default" }),
  });
  if (!response.ok) throw await apiError(response);
  const payload = record(await response.json());
  const confirmation = payload && {
    output_id: string(payload.output_id, 80),
    asset_id: string(payload.asset_id, 80),
    handling: payload.handling,
    target_id: string(payload.target_id, 256),
    confirmation_revision: integer(payload.confirmation_revision, 1),
    output_confirmation_revision: integer(payload.output_confirmation_revision, 1),
    expires_at: string(payload.expires_at, 64),
    confirmed_at: string(payload.confirmed_at, 64),
  };
  if (!confirmation || !confirmation.output_id || !confirmation.asset_id || !["native", "extract"].includes(String(confirmation.handling)) || !confirmation.target_id || confirmation.confirmation_revision === null || confirmation.output_confirmation_revision === null || !confirmation.expires_at || !confirmation.confirmed_at) {
    throw new FileOutputApiError("输出复用确认响应无效。", 502, "file_output_reuse_invalid");
  }
  return confirmation as FileOutputReuseConfirmation;
}

export async function deleteFileOutput(outputId: string, purpose: FilePurpose, scopeId: string) {
  const response = await fetch(scopedUrl(`/outputs/${encodeURIComponent(outputId)}`, purpose, scopeId), { method: "DELETE" });
  if (response.status === 204) return { cleanupPending: false };
  if (response.status === 202) return { cleanupPending: true };
  throw await apiError(response);
}
