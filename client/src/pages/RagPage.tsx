import { type DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import XlsxDestinationChooser, {
  isXlsxFile,
} from "../components/XlsxDestinationChooser";
import {
  extensionsForPurpose,
  fetchFileCapabilities,
  type FileCapabilitiesResponse,
} from "../data/fileCapabilities";
import {
  DEFAULT_EMBEDDING_MODEL_ID,
  embeddingModelOptions,
  rerankModelOptions,
} from "../data/modelOptions";

interface KnowledgeBase {
  id: string;
  name: string;
  document_count: number;
  created_at: number;
  updated_at: number;
  deletion_status?: "active" | "deleting" | "cleanup_pending" | "failed";
  deletion_error_code?: string | null;
  origin?: string;
  catalog_ref?: Record<string, unknown>;
  corpus_locked?: boolean;
  provisioning_status?: string;
}

interface KnowledgeBaseListResponse {
  knowledge_bases: KnowledgeBase[];
}

interface RagDocument {
  id: string;
  kb_id: string;
  filename: string;
  size: number;
  chunk_count: number;
  content_type?: string;
  ingestion_status?: string;
  visual_candidate?: boolean;
  warnings: string[];
  created_at: number;
}

interface DocumentListResponse {
  documents: RagDocument[];
}

interface PendingDocumentDeletion {
  document_id: string;
  filename: string;
  status: "deleting" | "cleanup_pending" | "failed";
  error_code: string | null;
  requested_at: number;
}

interface PendingDocumentDeletionListResponse {
  tenant_id: "local";
  knowledge_base_id: string;
  deletions: PendingDocumentDeletion[];
}

interface PipelineAsset {
  file_asset_id: string;
  document_id: string;
  knowledge_base_id: string;
  filename: string;
  size: number;
  extension: string;
  mime_type?: string | null;
  created_at: number;
}

interface PipelineAssetListResponse {
  assets: PipelineAsset[];
  asset_count: number;
}

interface PipelineArtifact {
  artifact_id: string;
  file_asset_id: string;
  document_id: string;
  knowledge_base_id: string;
  title: string;
  chunk_count: number;
  created_at: number;
}

interface PipelineArtifactListResponse {
  artifacts: PipelineArtifact[];
  artifact_count: number;
}

interface PipelineDraftStage {
  id: string;
  kind: string;
  title: string;
  status: string;
  item_count: number;
  summary: string;
  config?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

interface PipelineDraftResponse {
  kb_id: string;
  draft_id: string;
  version: number;
  updated_at: number;
  editable: boolean;
  index_schema_version: number;
  embedding_profile: Record<string, unknown>;
  retrieval_profile: Record<string, unknown>;
  stages: PipelineDraftStage[];
  stage_count: number;
}

interface PipelinePreflightStage {
  id: string;
  kind: string;
  title: string;
  status: string;
  severity: string;
  summary: string;
  metadata?: Record<string, unknown>;
}

interface PipelinePreflightResponse {
  kb_id: string;
  draft_id: string;
  ready: boolean;
  warnings: string[];
  stage_checks: PipelinePreflightStage[];
  document_count: number;
  artifact_count: number;
  chunk_count: number;
}

interface PipelineDraftEdits {
  processorMode: "general" | "qa" | "summary";
  processorModelId: string;
  processorFailurePolicy: "continue_on_error" | "strict";
  extractTitle: boolean;
  preserveTables: boolean;
  preserveCodeBlocks: boolean;
  removeRepeatedHeadersFooters: boolean;
  maxGeneratedItems: string;
  strategy: "recursive_character" | "parent_child";
  chunkSize: string;
  chunkOverlap: string;
  separators: string;
  parentChunkSize: string;
  parentChunkOverlap: string;
  parentSeparators: string;
  childChunkSize: string;
  childChunkOverlap: string;
  childSeparators: string;
  embeddingProvider: "hash" | "openai_compatible";
  embeddingModel: string;
  retrievalMode: "vector" | "fulltext" | "hybrid";
  vectorWeight: string;
  fulltextWeight: string;
  topK: string;
  scoreThreshold: string;
  candidateMultiplier: string;
  rerankEnabled: boolean;
  rerankProvider: "none" | "auto" | "api" | "llm";
  rerankModel: string;
  rerankTopN: string;
}

interface ProcessorCapabilities {
  version: string;
  modes: string[];
  failure_policies: string[];
  supported_extensions: string[];
  block_types: string[];
  llm_configured: boolean;
  model_label: string;
  generation_targets: string[];
  limits: { max_generated_items: number; preview_items: number; preview_text_characters: number };
}

interface ProcessorPreview {
  kb_id: string;
  document_id: string;
  filename: string;
  title: string;
  config: Record<string, unknown>;
  character_count: number;
  block_count: number;
  block_counts: Record<string, number>;
  generated_count: number;
  warnings: string[];
  blocks: Array<{ block_id: string; kind: string; text: string; page_number?: number | null; truncated?: boolean; metadata?: Record<string, unknown> }>;
  generated_items: Array<{ item_id: string; item_type: string; index_text: string; context_text: string; truncated?: boolean }>;
}

interface RetrievalCapabilities {
  version: string;
  index_schema_version: number;
  modes: string[];
  vector: { available: boolean; backend: string };
  fulltext: { available: boolean; backend: string };
  embedding: { provider: string; model: string; dimension: number; degraded: boolean };
  rerank: {
    api_configured: boolean;
    llm_configured: boolean;
    api_model: string;
    llm_model: string;
  };
}

interface PipelineJobStage {
  id: string;
  title: string;
  status: string;
  progress: number;
  item_count: number | null;
  error: string | null;
}

interface PipelineJob {
  job_id: string;
  kb_id: string;
  draft_version: number;
  status: string;
  stages: PipelineJobStage[];
  source_count: number;
  candidate_version_id: string;
  candidate_version: number;
  attempt: number;
  error: string | null;
  warnings: string[];
  document_results: Array<{
    source_id: string;
    filename: string;
    status: string;
    attempt: number;
    block_count: number;
    generated_count: number;
    chunk_count: number;
    error: string | null;
    duration_ms: number | null;
  }>;
  created_at: number;
  updated_at: number;
}

interface PipelineJobListResponse {
  jobs: PipelineJob[];
  job_count: number;
}

interface PipelineVersion {
  version_id: string;
  kb_id: string;
  version: number;
  status: string;
  active: boolean;
  draft_version: number;
  document_count: number;
  chunk_count: number;
  block_count?: number;
  qa_count?: number;
  summary_count?: number;
  source_summary: Array<{
    source_id: string;
    source_kind: string;
    filename: string;
  }>;
  document_results: Array<{
    source_id: string;
    status: string;
    chunk_count: number;
    vision_status?: string;
  }>;
  processor_profile?: Record<string, unknown>;
  warnings?: string[];
  job_id: string;
  index_schema_version?: number;
  embedding_profile?: Record<string, unknown>;
  retrieval_profile?: Record<string, unknown>;
  vector_index_ready?: boolean;
  lexical_index_ready?: boolean;
  created_at: number;
  activated_at: number | null;
}

interface PipelineVersionListResponse {
  versions: PipelineVersion[];
  version_count: number;
  active_version_id: string | null;
}

interface PipelineVersionQueryResponse {
  version_id: string;
  version: number;
  answer: string;
  warnings: string[];
  retrieval: Record<string, unknown>;
  sources: Array<{
    chunk_id: string;
    document_name: string;
    text: string;
    score: number;
    matched_text?: string | null;
    vector_score?: number | null;
    fulltext_score?: number | null;
    fused_score?: number | null;
    rerank_score?: number | null;
    parent_chunk_id?: string | null;
    parent_lifted?: boolean;
    chunk_type?: string;
    page_number?: number | null;
    slide?: number | null;
    sheet?: string | null;
    row_range?: string | null;
    heading_path?: string[] | null;
  }>;
}

export function formatRagSourceLocation(metadata?: Record<string, unknown>) {
  const locations: string[] = [];
  const sheet = typeof metadata?.sheet === "string" ? metadata.sheet.trim() : "";
  const rowRange =
    typeof metadata?.row_range === "string" ? metadata.row_range.trim() : "";
  const slide =
    typeof metadata?.slide === "number" &&
    Number.isInteger(metadata.slide) &&
    metadata.slide > 0
      ? metadata.slide
      : null;
  const page =
    typeof metadata?.page_number === "number" &&
    Number.isInteger(metadata.page_number) &&
    metadata.page_number > 0
      ? metadata.page_number
      : null;
  const headingPath = Array.isArray(metadata?.heading_path)
    ? metadata.heading_path
        .filter((item): item is string => typeof item === "string")
        .map((item) => item.trim())
        .filter(Boolean)
    : [];

  if (sheet) {
    locations.push(
      rowRange ? `工作表「${sheet}」· ${rowRange}` : `工作表「${sheet}」`,
    );
  } else if (rowRange) {
    locations.push(`范围 ${rowRange}`);
  }
  if (slide) locations.push(`第 ${slide} 张幻灯片`);
  if (page) locations.push(`第 ${page} 页`);
  if (headingPath.length > 0) {
    locations.push(`章节：${headingPath.join(" / ")}`);
  }
  return locations.join(" · ");
}

export function formatRagSheetLocation(metadata?: Record<string, unknown>) {
  return formatRagSourceLocation(metadata);
}

export function formatPipelineSourceLocation(source: {
  page_number?: number | null;
  slide?: number | null;
  sheet?: string | null;
  row_range?: string | null;
  heading_path?: string[] | null;
}) {
  return formatRagSourceLocation({
    page_number: source.page_number,
    slide: source.slide,
    sheet: source.sheet,
    row_range: source.row_range,
    heading_path: source.heading_path,
  });
}

export function isRagFileSelectionDisabled({
  capabilityReady,
  isUploading,
  hasPendingXlsx,
}: {
  capabilityReady: boolean;
  isUploading: boolean;
  hasPendingXlsx: boolean;
}) {
  return !capabilityReady || isUploading || hasPendingXlsx;
}

interface RagDocumentWarningSummary {
  items: string[];
  hiddenCount: number;
}

type RagUploadStatus =
  | "queued"
  | "uploading"
  | "succeeded"
  | "failed"
  | "cancel_requested"
  | "cancelled";

interface RagUploadQueueItem {
  id: string;
  knowledgeBaseId: string;
  file: File;
  status: RagUploadStatus;
  error: string;
  chunkCount: number | null;
}

export function ragUploadStatusLabel(status: RagUploadStatus) {
  if (status === "queued") return "等待上传";
  if (status === "uploading") return "正在入库";
  if (status === "succeeded") return "已完成";
  if (status === "failed") return "失败";
  if (status === "cancel_requested") return "已请求取消，请刷新确认";
  return "已取消";
}

const EMPTY_DOCUMENT_WARNING_SUMMARY: RagDocumentWarningSummary = {
  items: [],
  hiddenCount: 0,
};
const MAX_VISIBLE_DOCUMENT_WARNINGS = 3;
const MAX_VISIBLE_DOCUMENT_WARNING_CHARACTERS = 180;

function isOfficeDocumentPayload(document: {
  filename?: unknown;
  content_type?: unknown;
}) {
  const filename =
    typeof document.filename === "string" ? document.filename.trim().toLowerCase() : "";
  const contentType =
    typeof document.content_type === "string"
      ? document.content_type.trim().toLowerCase()
      : "";
  return (
    filename.endsWith(".docx") ||
    filename.endsWith(".pptx") ||
    contentType ===
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document" ||
    contentType ===
      "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  );
}

function localizeOfficeWarning(
  warning: string,
  document: { filename?: unknown; content_type?: unknown },
) {
  if (/no vision model was called/i.test(warning) && /images?/i.test(warning)) {
    const filename =
      typeof document.filename === "string" ? document.filename.trim().toLowerCase() : "";
    const contentType =
      typeof document.content_type === "string"
        ? document.content_type.trim().toLowerCase()
        : "";
    const isPresentation =
      filename.endsWith(".pptx") ||
      contentType ===
        "application/vnd.openxmlformats-officedocument.presentationml.presentation";
    return isPresentation
      ? "幻灯片内图片仅以占位符保留，本次未调用视觉模型。"
      : "文档内图片仅以占位符保留，本次未调用视觉模型。";
  }
  if (/tracked revisions? (?:were )?detected/i.test(warning)) {
    return "检测到修订记录，插入、删除或移动的修订内容可能未完整提取。";
  }
  return warning;
}

export function getRagOfficeWarningSummary(document: {
  filename?: unknown;
  content_type?: unknown;
  warnings?: unknown;
}): RagDocumentWarningSummary {
  if (!isOfficeDocumentPayload(document) || !Array.isArray(document.warnings)) {
    return EMPTY_DOCUMENT_WARNING_SUMMARY;
  }

  const warnings = document.warnings
    .filter((warning): warning is string => typeof warning === "string")
    .map((warning) => warning.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .map((warning) => localizeOfficeWarning(warning, document))
    .map((warning) =>
      warning.length > MAX_VISIBLE_DOCUMENT_WARNING_CHARACTERS
        ? `${warning.slice(0, MAX_VISIBLE_DOCUMENT_WARNING_CHARACTERS - 1).trimEnd()}…`
        : warning,
    )
    .filter((warning, index, values) => values.indexOf(warning) === index);

  return {
    items: warnings.slice(0, MAX_VISIBLE_DOCUMENT_WARNINGS),
    hiddenCount: Math.max(0, warnings.length - MAX_VISIBLE_DOCUMENT_WARNINGS),
  };
}

function separatorLabel(value: unknown) {
  if (value === "\n\n") return "\\n\\n";
  if (value === "\n") return "\\n";
  if (value === " ") return "<space>";
  if (value === "") return "<empty>";
  return String(value ?? "");
}

function separatorsToText(value: unknown) {
  return Array.isArray(value) ? value.map(separatorLabel).join("\n") : "\\n\\n\n\\n\n。\n！\n？\n<space>\n<empty>";
}

function textToSeparators(value: string) {
  return value
    .split(/\r?\n/)
    .map((item) => {
      const trimmed = item.trim();
      if (trimmed === "\\n\\n") return "\n\n";
      if (trimmed === "\\n") return "\n";
      if (trimmed === "<space>") return " ";
      if (trimmed === "<empty>") return "";
      return trimmed;
    })
    .filter((item, index, items) => items.indexOf(item) === index);
}

function numericProfileValue(profile: Record<string, unknown>, key: string, fallback: number) {
  const value = Number(profile[key]);
  return Number.isFinite(value) ? value : fallback;
}

function embeddingProfileSection(
  profile: Record<string, unknown> | undefined,
  key: "requested" | "effective",
) {
  const value = profile?.[key];
  return isUnknownRecord(value) ? value : {};
}

function draftEditsFromResponse(draft: PipelineDraftResponse): PipelineDraftEdits {
  const processor = draft.stages.find((stage) => stage.kind === "processor")?.config ?? {};
  const chunker = draft.stages.find((stage) => stage.kind === "chunker")?.config ?? {};
  const retrieval = draft.retrieval_profile ?? {};
  const requestedEmbedding = embeddingProfileSection(
    draft.embedding_profile,
    "requested",
  );
  const strategy = chunker.strategy === "parent_child" ? "parent_child" : "recursive_character";
  const retrievalMode = ["vector", "fulltext", "hybrid"].includes(String(retrieval.mode))
    ? String(retrieval.mode) as PipelineDraftEdits["retrievalMode"]
    : "hybrid";
  const provider = ["none", "auto", "api", "llm"].includes(String(retrieval.rerank_provider))
    ? String(retrieval.rerank_provider) as PipelineDraftEdits["rerankProvider"]
    : "auto";
  return {
    processorMode: ["qa", "summary"].includes(String(processor.mode))
      ? String(processor.mode) as PipelineDraftEdits["processorMode"]
      : "general",
    processorModelId: String(processor.model_id ?? ""),
    processorFailurePolicy: processor.failure_policy === "strict" ? "strict" : "continue_on_error",
    extractTitle: processor.extract_title !== false,
    preserveTables: processor.preserve_tables !== false,
    preserveCodeBlocks: processor.preserve_code_blocks !== false,
    removeRepeatedHeadersFooters: processor.remove_repeated_headers_footers !== false,
    maxGeneratedItems: String(processor.max_generated_items ?? 20),
    strategy,
    chunkSize: String(chunker.chunk_size ?? 500),
    chunkOverlap: String(chunker.chunk_overlap ?? 50),
    separators: separatorsToText(chunker.separators),
    parentChunkSize: String(chunker.parent_chunk_size ?? 1500),
    parentChunkOverlap: String(chunker.parent_chunk_overlap ?? 100),
    parentSeparators: separatorsToText(chunker.parent_separators),
    childChunkSize: String(chunker.child_chunk_size ?? 400),
    childChunkOverlap: String(chunker.child_chunk_overlap ?? 50),
    childSeparators: separatorsToText(chunker.child_separators),
    embeddingProvider:
      requestedEmbedding.provider === "openai_compatible"
        ? "openai_compatible"
        : "hash",
    embeddingModel: String(
      requestedEmbedding.model
        ?? draft.embedding_profile?.model
        ?? DEFAULT_EMBEDDING_MODEL_ID,
    ),
    retrievalMode,
    vectorWeight: String(numericProfileValue(retrieval, "vector_weight", 0.7)),
    fulltextWeight: String(numericProfileValue(retrieval, "fulltext_weight", 0.3)),
    topK: String(numericProfileValue(retrieval, "top_k", 5)),
    scoreThreshold: String(numericProfileValue(retrieval, "score_threshold", 0)),
    candidateMultiplier: String(numericProfileValue(retrieval, "candidate_multiplier", 4)),
    rerankEnabled: Boolean(retrieval.rerank_enabled),
    rerankProvider: provider,
    rerankModel: String(retrieval.rerank_model ?? ""),
    rerankTopN: String(numericProfileValue(retrieval, "rerank_top_n", 5)),
  };
}

function processorConfigFromEdits(edits: PipelineDraftEdits) {
  return {
    parser: "structured_local_parser",
    mode: edits.processorMode,
    model_id: edits.processorModelId.trim(),
    failure_policy: edits.processorFailurePolicy,
    extract_title: edits.extractTitle,
    preserve_tables: edits.preserveTables,
    preserve_code_blocks: edits.preserveCodeBlocks,
    remove_repeated_headers_footers: edits.removeRepeatedHeadersFooters,
    max_generated_items: Number(edits.maxGeneratedItems),
  };
}

function formatDate(timestamp: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function stageStatusLabel(status: string) {
  if (status === "ready") return "可观测";
  if (status === "empty") return "暂无数据";
  if (status === "planned") return "待接入";
  return status || "未知";
}

function stageStatusClass(status: string) {
  if (status === "ready") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "planned") return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  return "border-white/10 bg-white/[0.06] text-slate-300";
}

function preflightSeverityClass(severity: string) {
  if (severity === "warning") return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  if (severity === "error") return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
}

function pipelineJobStatusLabel(status: string) {
  if (status === "queued") return "等待执行";
  if (status === "running") return "执行中";
  if (status === "succeeded") return "候选就绪";
  if (status === "failed") return "执行失败";
  if (status === "cancelled") return "已取消";
  return status;
}

function pipelineJobStatusClass(status: string) {
  if (status === "succeeded") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "running" || status === "queued") return "border-sky-300/25 bg-sky-300/10 text-sky-100";
  if (status === "failed") return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  return "border-white/10 bg-white/[0.06] text-slate-300";
}

function formatConfigValue(value: unknown) {
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value == null) return "未设置";
  return String(value);
}

function isUnknownRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function readError(response: Response) {
  const fallback = `请求失败：${response.status}`;
  try {
    const payload: unknown = await response.json();
    if (!isUnknownRecord(payload)) return fallback;

    const detail = payload.detail;
    if (
      isUnknownRecord(detail) &&
      typeof detail.message === "string"
    ) {
      return detail.message;
    }
    if (typeof detail === "string") return detail;
    if (typeof payload.error === "string") return payload.error;
    if (typeof payload.message === "string") return payload.message;
    return fallback;
  } catch {
    return fallback;
  }
}

function validateFile(
  file: File,
  supportedExtensions: string[],
  maxFileBytes: number,
) {
  if (!supportedExtensions.length || maxFileBytes <= 0) {
    return "文件能力清单暂不可用，请刷新页面后重试。";
  }
  const lowerName = file.name.toLowerCase();
  const isSupported = supportedExtensions.some((extension) =>
    lowerName.endsWith(extension),
  );
  if (!isSupported) {
    return `仅支持 ${supportedExtensions.join(" / ")} 文件。`;
  }
  if (file.size > maxFileBytes) {
    return `文件过大，请上传 ${(maxFileBytes / 1024 / 1024).toLocaleString("zh-CN")} MB 以内的文档。`;
  }
  return "";
}

export default function RagPage() {
  const [searchParams] = useSearchParams();
  const requestedKbId = searchParams.get("kb_id") ?? "";
  const requestedJobId = searchParams.get("job_id") ?? "";
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedKbId, setSelectedKbId] = useState("");
  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [isLoadingKbs, setIsLoadingKbs] = useState(false);
  const [isLoadingDocs, setIsLoadingDocs] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadQueue, setUploadQueue] = useState<RagUploadQueueItem[]>([]);
  const [pendingDeletionRetries, setPendingDeletionRetries] = useState<
    PendingDocumentDeletion[]
  >([]);
  const [isDragging, setIsDragging] = useState(false);
  const [fileCapabilities, setFileCapabilities] =
    useState<FileCapabilitiesResponse | null>(null);
  const [fileCapabilitiesLoaded, setFileCapabilitiesLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uploadWarningSummary, setUploadWarningSummary] =
    useState<RagDocumentWarningSummary>(EMPTY_DOCUMENT_WARNING_SUMMARY);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newKbName, setNewKbName] = useState("");
  const [isPipelineOpen, setIsPipelineOpen] = useState(Boolean(requestedKbId || requestedJobId));
  const [isLoadingPipeline, setIsLoadingPipeline] = useState(false);
  const [pipelineError, setPipelineError] = useState("");
  const [pipelineAssets, setPipelineAssets] = useState<PipelineAsset[]>([]);
  const [pipelineArtifacts, setPipelineArtifacts] = useState<PipelineArtifact[]>([]);
  const [pipelineDraft, setPipelineDraft] = useState<PipelineDraftResponse | null>(null);
  const [pipelineDraftEdits, setPipelineDraftEdits] = useState<PipelineDraftEdits>({
    processorMode: "general",
    processorModelId: "",
    processorFailurePolicy: "continue_on_error",
    extractTitle: true,
    preserveTables: true,
    preserveCodeBlocks: true,
    removeRepeatedHeadersFooters: true,
    maxGeneratedItems: "20",
    strategy: "recursive_character",
    chunkSize: "",
    chunkOverlap: "",
    separators: "",
    parentChunkSize: "",
    parentChunkOverlap: "",
    parentSeparators: "",
    childChunkSize: "",
    childChunkOverlap: "",
    childSeparators: "",
    embeddingProvider: "openai_compatible",
    embeddingModel: DEFAULT_EMBEDDING_MODEL_ID,
    retrievalMode: "hybrid",
    vectorWeight: "0.7",
    fulltextWeight: "0.3",
    topK: "5",
    scoreThreshold: "0",
    candidateMultiplier: "4",
    rerankEnabled: false,
    rerankProvider: "auto",
    rerankModel: "",
    rerankTopN: "5",
  });
  const [retrievalCapabilities, setRetrievalCapabilities] = useState<RetrievalCapabilities | null>(null);
  const [retrievalCapabilitiesError, setRetrievalCapabilitiesError] = useState("");
  const [processorCapabilities, setProcessorCapabilities] = useState<ProcessorCapabilities | null>(null);
  const [processorCapabilitiesError, setProcessorCapabilitiesError] = useState("");
  const [processorPreviewDocumentId, setProcessorPreviewDocumentId] = useState("");
  const [processorPreview, setProcessorPreview] = useState<ProcessorPreview | null>(null);
  const [isPreviewingProcessor, setIsPreviewingProcessor] = useState(false);
  const [pipelinePreflight, setPipelinePreflight] = useState<PipelinePreflightResponse | null>(null);
  const [isSavingPipelineDraft, setIsSavingPipelineDraft] = useState(false);
  const [isPreflightingPipeline, setIsPreflightingPipeline] = useState(false);
  const [pipelineDraftNotice, setPipelineDraftNotice] = useState("");
  const [pipelineJobs, setPipelineJobs] = useState<PipelineJob[]>([]);
  const [pipelineVersions, setPipelineVersions] = useState<PipelineVersion[]>([]);
  const [activePipelineVersionId, setActivePipelineVersionId] = useState<string | null>(null);
  const [selectedPipelineJobId, setSelectedPipelineJobId] = useState(requestedJobId);
  const [isExecutingPipeline, setIsExecutingPipeline] = useState(false);
  const [pipelinePreviewQuestion, setPipelinePreviewQuestion] = useState("");
  const [pipelinePreview, setPipelinePreview] = useState<PipelineVersionQueryResponse | null>(null);
  const [previewingVersionId, setPreviewingVersionId] = useState("");
  const [activatingVersionId, setActivatingVersionId] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadButtonRef = useRef<HTMLButtonElement>(null);
  const createKnowledgeBaseButtonRef = useRef<HTMLButtonElement>(null);
  const knowledgeBaseCleanupButtonRefs = useRef(
    new Map<string, HTMLButtonElement>(),
  );
  const uploadControllersRef = useRef(new Map<string, AbortController>());
  const cancelledUploadIdsRef = useRef(new Set<string>());
  const uploadSequenceRef = useRef(0);
  const uploadGenerationRef = useRef(0);
  const pendingDeletionGenerationRef = useRef(0);
  const uploadQueueRunningRef = useRef(false);
  const [pendingXlsxFile, setPendingXlsxFile] = useState<File | null>(null);

  const ragExtensions = useMemo(
    () =>
      fileCapabilities
        ? extensionsForPurpose(fileCapabilities, "rag")
        : [],
    [fileCapabilities],
  );
  const ragMaxFileBytes = useMemo(() => {
    const limits = (fileCapabilities?.capabilities ?? [])
      .filter(
        (capability) =>
          capability.purpose === "rag" &&
          capability.interaction_status === "ready",
      )
      .map((capability) => capability.max_bytes_per_file);
    return limits.length ? Math.min(...limits) : 0;
  }, [fileCapabilities]);
  const ragUploadAvailable =
    fileCapabilitiesLoaded && ragExtensions.length > 0 && ragMaxFileBytes > 0;
  const ragCapabilityDisabled = isRagFileSelectionDisabled({
    capabilityReady: ragUploadAvailable,
    isUploading,
    hasPendingXlsx: Boolean(pendingXlsxFile),
  });
  const ragAccept = ragExtensions.join(",");
  const ragFormatHint = ragUploadAvailable
    ? `支持 ${ragExtensions.join(" / ")}，单文件不超过 ${(ragMaxFileBytes / 1024 / 1024).toLocaleString("zh-CN")} MB。图片和扫描 PDF 需在知识流水线中完成视觉理解后再激活索引。`
    : fileCapabilitiesLoaded
      ? "文件能力清单暂不可用，已暂停选择与拖放。请刷新页面后重试。"
      : "正在读取文件能力清单…";

  const selectedKnowledgeBase = useMemo(
    () =>
      knowledgeBases.find(
        (item) =>
          item.id === selectedKbId &&
          (item.deletion_status ?? "active") === "active",
      ) ?? null,
    [knowledgeBases, selectedKbId],
  );
  const requestedEmbeddingProfile = embeddingProfileSection(
    pipelineDraft?.embedding_profile,
    "requested",
  );
  const effectiveEmbeddingProfile = embeddingProfileSection(
    pipelineDraft?.embedding_profile,
    "effective",
  );
  const ragFileSelectionDisabled = ragCapabilityDisabled || Boolean(selectedKnowledgeBase?.corpus_locked);

  const activePipelineVersion = useMemo(
    () =>
      pipelineVersions.find(
        (version) =>
          version.version_id === activePipelineVersionId || version.active,
      ) ?? null,
    [activePipelineVersionId, pipelineVersions],
  );

  const activeDocumentChunkCounts = useMemo(
    () =>
      new Map(
        (activePipelineVersion?.document_results ?? [])
          .filter(
            (result) =>
              result.status === "completed" && result.chunk_count > 0,
          )
          .map(
            (result): [string, number] => [
              result.source_id,
              result.chunk_count,
            ],
          ),
      ),
    [activePipelineVersion],
  );

  const selectedPipelineJob = useMemo(
    () => pipelineJobs.find((job) => job.job_id === selectedPipelineJobId) ?? pipelineJobs[0] ?? null,
    [pipelineJobs, selectedPipelineJobId],
  );

  const hasActivePipelineJob = useMemo(
    () => pipelineJobs.some((job) => job.status === "queued" || job.status === "running"),
    [pipelineJobs],
  );

  useEffect(() => {
    document.title = "模镜 - 知识库管理";
    void loadKnowledgeBases();
    void loadRetrievalCapabilities();
    void loadProcessorCapabilities();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void fetchFileCapabilities(controller.signal).then((payload) => {
      if (!controller.signal.aborted) {
        setFileCapabilities(payload);
        setFileCapabilitiesLoaded(true);
      }
    });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    uploadGenerationRef.current += 1;
    pendingDeletionGenerationRef.current += 1;
    uploadControllersRef.current.forEach((controller) => controller.abort());
    uploadControllersRef.current.clear();
    cancelledUploadIdsRef.current.clear();
    setUploadQueue([]);
    setPendingDeletionRetries([]);
    setPendingXlsxFile(null);
  }, [selectedKbId]);

  useEffect(
    () => () => {
      uploadControllersRef.current.forEach((controller) => controller.abort());
      uploadControllersRef.current.clear();
    },
    [],
  );

  useEffect(() => {
    if (!selectedKbId) {
      setDocuments([]);
      setPipelineAssets([]);
      setPipelineArtifacts([]);
      setPipelineDraft(null);
      setProcessorPreview(null);
      setProcessorPreviewDocumentId("");
      setPipelinePreflight(null);
      setPipelineJobs([]);
      setPipelineVersions([]);
      setActivePipelineVersionId(null);
      setSelectedPipelineJobId("");
      setPipelinePreview(null);
      setPipelineDraftNotice("");
      setPipelineError("");
      return;
    }
    void loadDocuments(selectedKbId);
    void loadPendingDocumentDeletions(selectedKbId);
    if (isPipelineOpen) void loadPipeline(selectedKbId);
  }, [isPipelineOpen, selectedKbId]);

  useEffect(() => {
    if (!isPipelineOpen || !selectedKbId || !hasActivePipelineJob) return;
    const timer = window.setInterval(() => {
      void loadPipelineRuntime(selectedKbId);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasActivePipelineJob, isPipelineOpen, selectedKbId]);

  async function loadKnowledgeBases(nextSelectedId?: string) {
    setIsLoadingKbs(true);
    setError("");
    try {
      const response = await fetch("/api/rag/knowledge_bases");
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as KnowledgeBaseListResponse;
      setKnowledgeBases(data.knowledge_bases);
      const activeKnowledgeBases = data.knowledge_bases.filter(
        (item) => (item.deletion_status ?? "active") === "active",
      );
      const preferredId =
        nextSelectedId || requestedKbId || selectedKbId || activeKnowledgeBases[0]?.id || "";
      if (
        preferredId &&
        activeKnowledgeBases.some((item) => item.id === preferredId)
      ) {
        setSelectedKbId(preferredId);
      } else {
        setSelectedKbId(activeKnowledgeBases[0]?.id ?? "");
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "知识库列表加载失败。");
    } finally {
      setIsLoadingKbs(false);
    }
  }

  async function loadRetrievalCapabilities() {
    setRetrievalCapabilitiesError("");
    try {
      const response = await fetch("/api/rag/retrieval-capabilities");
      if (!response.ok) throw new Error(await readError(response));
      setRetrievalCapabilities((await response.json()) as RetrievalCapabilities);
    } catch (loadError) {
      setRetrievalCapabilities(null);
      setRetrievalCapabilitiesError(
        loadError instanceof Error ? loadError.message : "检索能力状态暂不可用。",
      );
    }
  }

  async function loadProcessorCapabilities() {
    setProcessorCapabilitiesError("");
    try {
      const response = await fetch("/api/rag/processor-capabilities");
      if (!response.ok) throw new Error(await readError(response));
      setProcessorCapabilities((await response.json()) as ProcessorCapabilities);
    } catch (loadError) {
      setProcessorCapabilities(null);
      setProcessorCapabilitiesError(
        loadError instanceof Error ? loadError.message : "处理器能力状态暂不可用。",
      );
    }
  }

  async function loadDocuments(kbId: string) {
    setIsLoadingDocs(true);
    setError("");
    try {
      const response = await fetch(`/api/rag/knowledge_bases/${kbId}/documents`);
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as DocumentListResponse;
      setDocuments(data.documents);
      setProcessorPreviewDocumentId((current) => (
        current && data.documents.some((item) => item.id === current)
          ? current
          : data.documents[0]?.id ?? ""
      ));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "文档列表加载失败。");
    } finally {
      setIsLoadingDocs(false);
    }
  }

  async function loadPendingDocumentDeletions(kbId: string) {
    const generation = pendingDeletionGenerationRef.current;
    try {
      const response = await fetch(
        `/api/rag/knowledge_bases/${kbId}/pending-deletions`,
      );
      if (!response.ok) throw new Error(await readError(response));
      const data = (await response.json()) as PendingDocumentDeletionListResponse;
      if (data.tenant_id !== "local" || data.knowledge_base_id !== kbId) {
        throw new Error("待清理文档列表的作用域不匹配，请刷新后重试。");
      }
      if (generation !== pendingDeletionGenerationRef.current) return;
      setPendingDeletionRetries(data.deletions);
    } catch (loadError) {
      if (generation !== pendingDeletionGenerationRef.current) return;
      setPendingDeletionRetries([]);
      setError(
        loadError instanceof Error ? loadError.message : "待清理文档列表加载失败。",
      );
    }
  }

  async function loadPipeline(kbId: string) {
    setIsLoadingPipeline(true);
    setPipelineError("");
    try {
      const query = new URLSearchParams({ kb_id: kbId }).toString();
      const [assetsResponse, artifactsResponse, draftResponse, jobsResponse, versionsResponse] = await Promise.all([
        fetch(`/api/rag/pipeline/assets?${query}`),
        fetch(`/api/rag/pipeline/artifacts?${query}`),
        fetch(`/api/rag/pipeline/draft?${query}`),
        fetch(`/api/rag/pipeline/jobs?${query}&limit=20`),
        fetch(`/api/rag/pipeline/versions?${query}`),
      ]);
      if (!assetsResponse.ok) throw new Error(await readError(assetsResponse));
      if (!artifactsResponse.ok) throw new Error(await readError(artifactsResponse));
      if (!draftResponse.ok) throw new Error(await readError(draftResponse));
      if (!jobsResponse.ok) throw new Error(await readError(jobsResponse));
      if (!versionsResponse.ok) throw new Error(await readError(versionsResponse));
      const assetsData = (await assetsResponse.json()) as PipelineAssetListResponse;
      const artifactsData = (await artifactsResponse.json()) as PipelineArtifactListResponse;
      const draftData = (await draftResponse.json()) as PipelineDraftResponse;
      const jobsData = (await jobsResponse.json()) as PipelineJobListResponse;
      const versionsData = (await versionsResponse.json()) as PipelineVersionListResponse;
      setPipelineAssets(assetsData.assets);
      setPipelineArtifacts(artifactsData.artifacts);
      setPipelineDraft(draftData);
      setPipelineJobs(jobsData.jobs);
      setPipelineVersions(versionsData.versions);
      setActivePipelineVersionId(versionsData.active_version_id);
      setSelectedPipelineJobId((current) => {
        if (current && jobsData.jobs.some((job) => job.job_id === current)) return current;
        if (requestedJobId && jobsData.jobs.some((job) => job.job_id === requestedJobId)) return requestedJobId;
        return jobsData.jobs[0]?.job_id ?? "";
      });
      setPipelineDraftEdits(draftEditsFromResponse(draftData));
      setPipelinePreflight(null);
      setProcessorPreview(null);
      setPipelineDraftNotice("");
    } catch (loadError) {
      setPipelineError(
        loadError instanceof Error ? loadError.message : "知识流水线加载失败。",
      );
    } finally {
      setIsLoadingPipeline(false);
    }
  }

  async function loadPipelineRuntime(kbId: string) {
    try {
      const query = new URLSearchParams({ kb_id: kbId }).toString();
      const [jobsResponse, versionsResponse] = await Promise.all([
        fetch(`/api/rag/pipeline/jobs?${query}&limit=20`),
        fetch(`/api/rag/pipeline/versions?${query}`),
      ]);
      if (!jobsResponse.ok) throw new Error(await readError(jobsResponse));
      if (!versionsResponse.ok) throw new Error(await readError(versionsResponse));
      const jobsData = (await jobsResponse.json()) as PipelineJobListResponse;
      const versionsData = (await versionsResponse.json()) as PipelineVersionListResponse;
      setPipelineJobs(jobsData.jobs);
      setPipelineVersions(versionsData.versions);
      setActivePipelineVersionId(versionsData.active_version_id);
      setSelectedPipelineJobId((current) =>
        current && jobsData.jobs.some((job) => job.job_id === current)
          ? current
          : jobsData.jobs[0]?.job_id ?? "",
      );
    } catch (loadError) {
      setPipelineError(
        loadError instanceof Error ? loadError.message : "知识流水线运行状态加载失败。",
      );
    }
  }

  async function savePipelineDraft() {
    if (!selectedKbId || isSavingPipelineDraft) return;
    setIsSavingPipelineDraft(true);
    setPipelineError("");
    setPipelineDraftNotice("");
    try {
      const chunkSize = Number(pipelineDraftEdits.chunkSize);
      const chunkOverlap = Number(pipelineDraftEdits.chunkOverlap);
      const parentChunkSize = Number(pipelineDraftEdits.parentChunkSize);
      const parentChunkOverlap = Number(pipelineDraftEdits.parentChunkOverlap);
      const childChunkSize = Number(pipelineDraftEdits.childChunkSize);
      const childChunkOverlap = Number(pipelineDraftEdits.childChunkOverlap);
      const response = await fetch(`/api/rag/pipeline/draft/${selectedKbId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stages: {
            stage_processor: {
              config: processorConfigFromEdits(pipelineDraftEdits),
            },
            stage_chunker: {
              config: {
                strategy: pipelineDraftEdits.strategy,
                chunk_size: chunkSize,
                chunk_overlap: chunkOverlap,
                separators: textToSeparators(pipelineDraftEdits.separators),
                parent_chunk_size: parentChunkSize,
                parent_chunk_overlap: parentChunkOverlap,
                parent_separators: textToSeparators(pipelineDraftEdits.parentSeparators),
                child_chunk_size: childChunkSize,
                child_chunk_overlap: childChunkOverlap,
                child_separators: textToSeparators(pipelineDraftEdits.childSeparators),
              },
            },
          },
          embedding_profile: {
            provider: pipelineDraftEdits.embeddingProvider,
            model: pipelineDraftEdits.embeddingModel.trim(),
          },
          retrieval_profile: {
            mode: pipelineDraftEdits.retrievalMode,
            vector_weight: Number(pipelineDraftEdits.vectorWeight),
            fulltext_weight: Number(pipelineDraftEdits.fulltextWeight),
            top_k: Number(pipelineDraftEdits.topK),
            score_threshold: Number(pipelineDraftEdits.scoreThreshold),
            candidate_multiplier: Number(pipelineDraftEdits.candidateMultiplier),
            rerank_enabled: pipelineDraftEdits.rerankEnabled,
            rerank_provider: pipelineDraftEdits.rerankEnabled
              ? pipelineDraftEdits.rerankProvider
              : "none",
            rerank_model: pipelineDraftEdits.rerankModel.trim(),
            rerank_top_n: Number(pipelineDraftEdits.rerankTopN),
          },
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const draftData = (await response.json()) as PipelineDraftResponse;
      setPipelineDraft(draftData);
      setPipelineDraftEdits(draftEditsFromResponse(draftData));
      setPipelinePreflight(null);
      setPipelineDraftNotice("高级 RAG 草稿已保存；执行候选版本并手动激活前，不影响现有检索与聊天 RAG。");
    } catch (saveError) {
      setPipelineError(saveError instanceof Error ? saveError.message : "保存流水线草稿失败。");
    } finally {
      setIsSavingPipelineDraft(false);
    }
  }

  async function previewProcessor() {
    if (!selectedKbId || !processorPreviewDocumentId || isPreviewingProcessor) return;
    setIsPreviewingProcessor(true);
    setPipelineError("");
    setProcessorPreview(null);
    try {
      const response = await fetch(
        `/api/rag/pipeline/draft/${selectedKbId}/processor-preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            document_id: processorPreviewDocumentId,
            processor: processorConfigFromEdits(pipelineDraftEdits),
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      setProcessorPreview((await response.json()) as ProcessorPreview);
    } catch (previewError) {
      setPipelineError(
        previewError instanceof Error ? previewError.message : "文档处理预览失败。",
      );
    } finally {
      setIsPreviewingProcessor(false);
    }
  }

  async function runPipelinePreflight() {
    if (!selectedKbId || isPreflightingPipeline) return;
    setIsPreflightingPipeline(true);
    setPipelineError("");
    try {
      const response = await fetch(`/api/rag/pipeline/draft/${selectedKbId}/preflight`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await readError(response));
      const preflightData = (await response.json()) as PipelinePreflightResponse;
      setPipelinePreflight(preflightData);
      setPipelineDraftNotice(
        preflightData.ready
          ? "预检通过：当前草稿配置与已入库文档元数据一致。"
          : "预检完成：请查看 warnings 与 stage 检查结果。",
      );
    } catch (preflightError) {
      setPipelineError(
        preflightError instanceof Error ? preflightError.message : "运行流水线预检失败。",
      );
    } finally {
      setIsPreflightingPipeline(false);
    }
  }

  async function executePipelineDraft() {
    if (!selectedKbId || !pipelineDraft || isExecutingPipeline) return;
    setIsExecutingPipeline(true);
    setPipelineError("");
    setPipelineDraftNotice("");
    try {
      const response = await fetch(`/api/rag/pipeline/draft/${selectedKbId}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          draft_version: pipelineDraft.version,
          source_document_ids: null,
          xpert_file_refs: [],
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const created = (await response.json()) as PipelineJob;
      setSelectedPipelineJobId(created.job_id);
      setPipelineJobs((current) => [created, ...current]);
      setPipelineDraftNotice("执行任务已创建。候选索引完成后需人工预览并激活，当前检索不会自动切换。");
      await loadPipelineRuntime(selectedKbId);
    } catch (executeError) {
      setPipelineError(
        executeError instanceof Error ? executeError.message : "知识流水线执行失败。",
      );
    } finally {
      setIsExecutingPipeline(false);
    }
  }

  async function cancelPipelineJob(jobId: string) {
    setPipelineError("");
    try {
      const response = await fetch(`/api/rag/pipeline/jobs/${jobId}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(await readError(response));
      await loadPipelineRuntime(selectedKbId);
    } catch (cancelError) {
      setPipelineError(cancelError instanceof Error ? cancelError.message : "取消任务失败。");
    }
  }

  async function retryPipelineJob(jobId: string) {
    setPipelineError("");
    try {
      const response = await fetch(`/api/rag/pipeline/jobs/${jobId}/retry`, { method: "POST" });
      if (!response.ok) throw new Error(await readError(response));
      setSelectedPipelineJobId(jobId);
      await loadPipelineRuntime(selectedKbId);
    } catch (retryError) {
      setPipelineError(retryError instanceof Error ? retryError.message : "重试任务失败。");
    }
  }

  async function previewPipelineVersion(versionId: string) {
    const question = pipelinePreviewQuestion.trim();
    if (!question || previewingVersionId) return;
    setPreviewingVersionId(versionId);
    setPipelineError("");
    try {
      const response = await fetch(`/api/rag/pipeline/versions/${versionId}/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          retrieval: {
            mode: pipelineDraftEdits.retrievalMode,
            vector_weight: Number(pipelineDraftEdits.vectorWeight),
            fulltext_weight: Number(pipelineDraftEdits.fulltextWeight),
            top_k: Number(pipelineDraftEdits.topK),
            score_threshold: Number(pipelineDraftEdits.scoreThreshold),
            candidate_multiplier: Number(pipelineDraftEdits.candidateMultiplier),
            rerank_enabled: pipelineDraftEdits.rerankEnabled,
            rerank_provider: pipelineDraftEdits.rerankEnabled
              ? pipelineDraftEdits.rerankProvider
              : "none",
            rerank_model: pipelineDraftEdits.rerankModel.trim(),
            rerank_top_n: Number(pipelineDraftEdits.rerankTopN),
          },
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      setPipelinePreview((await response.json()) as PipelineVersionQueryResponse);
    } catch (previewError) {
      setPipelineError(previewError instanceof Error ? previewError.message : "候选版本预览失败。");
    } finally {
      setPreviewingVersionId("");
    }
  }

  async function activatePipelineVersion(versionId: string) {
    if (activatingVersionId) return;
    setActivatingVersionId(versionId);
    setPipelineError("");
    try {
      const response = await fetch(`/api/rag/pipeline/versions/${versionId}/activate`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await readError(response));
      await loadPipelineRuntime(selectedKbId);
      const activated = (await response.json()) as PipelineVersion;
      setPipelineDraftNotice(`知识索引 v${activated.version} 已激活。RAG、聊天和知识引用节点将使用该版本。`);
    } catch (activateError) {
      setPipelineError(activateError instanceof Error ? activateError.message : "激活版本失败。");
    } finally {
      setActivatingVersionId("");
    }
  }

  async function createKnowledgeBase() {
    const name = newKbName.trim();
    if (!name || isCreating) return;

    setIsCreating(true);
    setError("");
    setNotice("");
    setUploadWarningSummary(EMPTY_DOCUMENT_WARNING_SUMMARY);
    try {
      const response = await fetch("/api/rag/knowledge_bases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const created = (await response.json()) as KnowledgeBase;
      setShowCreateModal(false);
      setNewKbName("");
      setNotice(`知识库「${created.name}」已创建。`);
      await loadKnowledgeBases(created.id);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "创建知识库失败。");
    } finally {
      setIsCreating(false);
    }
  }

  async function deleteKnowledgeBase(kb: KnowledgeBase) {
    const cleanupPending = (kb.deletion_status ?? "active") !== "active";
    const confirmation = cleanupPending
      ? `继续重试彻底清理知识库「${kb.name}」吗？该资料库已经隔离，清理完成前无法恢复使用。`
      : `彻底删除知识库「${kb.name}」及其中全部文档和文件资产吗？此操作不可撤销。仍被其他模块引用的共享资产会保留。`;
    if (!window.confirm(confirmation)) return;

    setError("");
    setNotice("");
    setUploadWarningSummary(EMPTY_DOCUMENT_WARNING_SUMMARY);
    try {
      const response = await fetch(`/api/rag/knowledge_bases/${kb.id}`, {
        method: "DELETE",
      });
      if (response.status === 409) {
        await loadKnowledgeBases();
        setNotice(
          `知识库「${kb.name}」已隔离，仍有文件或派生物等待清理。请稍后重试彻底清理。`,
        );
        window.requestAnimationFrame(() =>
          knowledgeBaseCleanupButtonRefs.current.get(kb.id)?.focus(),
        );
        return;
      }
      if (!response.ok) throw new Error(await readError(response));
      setNotice(`知识库「${kb.name}」已删除。`);
      if (selectedKbId === kb.id) setSelectedKbId("");
      await loadKnowledgeBases();
      window.requestAnimationFrame(() => createKnowledgeBaseButtonRef.current?.focus());
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除知识库失败。");
    }
  }

  async function uploadFileRequest(
    file: File,
    knowledgeBaseId: string,
    signal?: AbortSignal,
  ) {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(
      `/api/rag/knowledge_bases/${knowledgeBaseId}/documents`,
      {
        method: "POST",
        body: form,
        signal,
      },
    );
    if (!response.ok) throw new Error(await readError(response));
    return (await response.json()) as RagDocument;
  }

  async function refreshAfterUploads(knowledgeBaseId: string) {
    await Promise.all([
      loadDocuments(knowledgeBaseId),
      loadPendingDocumentDeletions(knowledgeBaseId),
      loadKnowledgeBases(knowledgeBaseId),
    ]);
    if (isPipelineOpen) await loadPipeline(knowledgeBaseId);
  }

  async function uploadFile(file: File) {
    if (!selectedKbId || isUploading) return false;
    const validationError = validateFile(file, ragExtensions, ragMaxFileBytes);
    if (validationError) {
      setError(validationError);
      return false;
    }

    setIsUploading(true);
    setError("");
    setNotice("");
    setUploadWarningSummary(EMPTY_DOCUMENT_WARNING_SUMMARY);
    try {
      const uploaded = await uploadFileRequest(file, selectedKbId);
      setNotice(`文档「${uploaded.filename}」已入库，切成 ${uploaded.chunk_count} 个片段。`);
      setUploadWarningSummary(getRagOfficeWarningSummary(uploaded));
      await refreshAfterUploads(selectedKbId);
      return true;
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "上传文档失败。");
      return false;
    } finally {
      setIsUploading(false);
    }
  }

  async function runUploadQueue(items: RagUploadQueueItem[]) {
    if (!items.length || isUploading || uploadQueueRunningRef.current) return;
    uploadQueueRunningRef.current = true;
    const uploadGeneration = uploadGenerationRef.current;
    const knowledgeBaseId = items[0].knowledgeBaseId;
    setIsUploading(true);
    setError("");
    setNotice("");
    setUploadWarningSummary(EMPTY_DOCUMENT_WARNING_SUMMARY);
    let succeeded = 0;
    let failed = 0;
    let cancelRequested = 0;
    const warnings: string[] = [];
    try {
      for (const item of items) {
        if (
          uploadGeneration !== uploadGenerationRef.current ||
          cancelledUploadIdsRef.current.has(item.id)
        ) {
          setUploadQueue((current) =>
            current.map((candidate) =>
              candidate.id === item.id
                ? { ...candidate, status: "cancelled", error: "" }
                : candidate,
            ),
          );
          continue;
        }
        const controller = new AbortController();
        uploadControllersRef.current.set(item.id, controller);
        setUploadQueue((current) =>
          current.map((candidate) =>
            candidate.id === item.id
              ? { ...candidate, status: "uploading", error: "" }
              : candidate,
          ),
        );
        try {
          const uploaded = await uploadFileRequest(
            item.file,
            item.knowledgeBaseId,
            controller.signal,
          );
          succeeded += 1;
          warnings.push(...getRagOfficeWarningSummary(uploaded).items);
          setUploadQueue((current) =>
            current.map((candidate) =>
              candidate.id === item.id
                ? {
                    ...candidate,
                    status: "succeeded",
                    error: "",
                    chunkCount: uploaded.chunk_count,
                  }
                : candidate,
            ),
          );
        } catch (uploadError) {
          if (controller.signal.aborted) {
            cancelRequested += 1;
            setUploadQueue((current) =>
              current.map((candidate) =>
                candidate.id === item.id
                  ? {
                      ...candidate,
                      status: "cancel_requested",
                      error: "浏览器已请求取消，但服务端可能已接收文件；请刷新文档清单确认最终结果。",
                    }
                  : candidate,
              ),
            );
          } else {
            failed += 1;
            const message =
              uploadError instanceof Error ? uploadError.message : "上传文档失败。";
            setUploadQueue((current) =>
              current.map((candidate) =>
                candidate.id === item.id
                  ? { ...candidate, status: "failed", error: message }
                  : candidate,
              ),
            );
          }
        } finally {
          uploadControllersRef.current.delete(item.id);
        }
      }
      if (succeeded > 0 || cancelRequested > 0) {
        await refreshAfterUploads(knowledgeBaseId);
      }
      setNotice(
        cancelRequested > 0
          ? `已请求取消 ${cancelRequested} 个上传并自动刷新文档清单；服务端可能仍在处理，请稍后再次刷新确认。`
          : succeeded > 0
            ? `批量入库完成：${succeeded} 个成功${failed ? `，${failed} 个失败` : ""}。`
          : "",
      );
      if (warnings.length > 0) {
        const uniqueWarnings = [...new Set(warnings)];
        setUploadWarningSummary({
          items: uniqueWarnings.slice(0, 3),
          hiddenCount: Math.max(0, uniqueWarnings.length - 3),
        });
      }
    } finally {
      uploadQueueRunningRef.current = false;
      setIsUploading(false);
    }
  }

  function enqueueUploadFiles(files: File[]) {
    if (!selectedKbId || selectedKnowledgeBase?.corpus_locked || isUploading || files.length === 0) return;
    setUploadWarningSummary(EMPTY_DOCUMENT_WARNING_SUMMARY);
    setError("");
    if (files.length === 1 && isXlsxFile(files[0])) {
      const validationError = validateFile(files[0], ragExtensions, ragMaxFileBytes);
      if (validationError) {
        setError(validationError);
      } else {
        setPendingXlsxFile(files[0]);
      }
      return;
    }

    const created = files.map((file) => {
      const validationError = validateFile(file, ragExtensions, ragMaxFileBytes);
      const requiresXlsxChoice = isXlsxFile(file);
      uploadSequenceRef.current += 1;
      return {
        id: `rag-upload-${Date.now()}-${uploadSequenceRef.current}`,
        knowledgeBaseId: selectedKbId,
        file,
        status:
          validationError || requiresXlsxChoice
            ? ("failed" as const)
            : ("queued" as const),
        error:
          validationError ||
          (requiresXlsxChoice
            ? "XLSX 需要逐个确认“加入资料库”用途，请单独选择该文件。"
            : ""),
        chunkCount: null,
      };
    });
    setUploadQueue((current) => [...current, ...created]);
    const runnable = created.filter((item) => item.status === "queued");
    void runUploadQueue(runnable);
  }

  function cancelQueuedUpload(item: RagUploadQueueItem) {
    cancelledUploadIdsRef.current.add(item.id);
    uploadControllersRef.current.get(item.id)?.abort();
    setUploadQueue((current) =>
      current.map((candidate) =>
        candidate.id === item.id
          ? item.status === "uploading"
            ? {
                ...candidate,
                status: "cancel_requested",
                error: "浏览器已请求取消，但服务端可能已接收文件；请刷新文档清单确认最终结果。",
              }
            : { ...candidate, status: "cancelled", error: "文件尚未发送。" }
          : candidate,
      ),
    );
  }

  function retryQueuedUpload(item: RagUploadQueueItem) {
    if (isUploading || item.knowledgeBaseId !== selectedKbId) return;
    cancelledUploadIdsRef.current.delete(item.id);
    const queued = { ...item, status: "queued" as const, error: "", chunkCount: null };
    setUploadQueue((current) =>
      current.map((candidate) => (candidate.id === item.id ? queued : candidate)),
    );
    void runUploadQueue([queued]);
  }

  function clearPendingXlsxFile(returnFocus = false) {
    setPendingXlsxFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (returnFocus) {
      window.requestAnimationFrame(() => uploadButtonRef.current?.focus());
    }
  }

  async function deleteDocument(document: RagDocument) {
    if (!window.confirm(`确认删除文档「${document.filename}」吗？`)) return;

    setError("");
    setNotice("");
    setUploadWarningSummary(EMPTY_DOCUMENT_WARNING_SUMMARY);
    try {
      const response = await fetch(`/api/rag/documents/${document.id}`, {
        method: "DELETE",
      });
      if (response.status === 409) {
        const message = await readError(response);
        setPendingDeletionRetries((current) => [
          ...current.filter((item) => item.document_id !== document.id),
          {
            document_id: document.id,
            filename: document.filename,
            status: "cleanup_pending",
            error_code: "rag_document_cleanup_pending",
            requested_at: Date.now() / 1000,
          },
        ]);
        setNotice(
          `文档「${document.filename}」已从检索隔离，但派生物清理尚未完成。${message}`,
        );
        await refreshAfterUploads(selectedKbId);
        return;
      }
      if (!response.ok) throw new Error(await readError(response));
      setPendingDeletionRetries((current) =>
        current.filter((item) => item.document_id !== document.id),
      );
      setNotice(`文档「${document.filename}」已删除。`);
      await refreshAfterUploads(selectedKbId);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除文档失败。");
    }
  }

  async function retryDocumentCleanup(document: PendingDocumentDeletion) {
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/rag/documents/${document.document_id}`, {
        method: "DELETE",
      });
      if (response.status === 409) {
        setNotice(
          `文档「${document.filename}」仍保持检索隔离，清理尚未完成，请稍后再次重试。`,
        );
        await refreshAfterUploads(selectedKbId);
        return;
      }
      if (!response.ok) throw new Error(await readError(response));
      setPendingDeletionRetries((current) =>
        current.filter((item) => item.document_id !== document.document_id),
      );
      setNotice(`文档「${document.filename}」的原件、索引和派生物已清理完成。`);
      await refreshAfterUploads(selectedKbId);
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "重试清理失败。");
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    if (pendingXlsxFile) {
      setError("请先选择当前 XLSX 的用途，或取消后再选择其他文件。");
      return;
    }
    if (!ragUploadAvailable) {
      setError("文件能力清单暂不可用，暂时无法拖放文件。请刷新页面后重试。");
      return;
    }
    enqueueUploadFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <PageContainer
      activeResource="prompts"
      maxWidthClassName="max-w-[1760px]"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">资料库服务台</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            本地 RAG 已接管资料库：上传文档后自动解析、切块、向量化，面试间可以直接引用。
          </p>
          <div className="mt-4 rounded-lg border border-hire-300/20 bg-hire-300/10 p-3 text-xs leading-5 text-hire-50">
            {ragFormatHint}
          </div>
        </div>
      }
    >
      <header className="mb-6 overflow-hidden rounded-lg border border-hire-300/20 bg-[linear-gradient(135deg,rgba(67,20,7,0.82),rgba(6,9,22,0.95)_52%,rgba(8,51,68,0.5))] p-6 shadow-prism">
        <p className="text-sm font-semibold text-hire-100">RAG 资料库</p>
        <div className="mt-3 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-3xl font-semibold text-white sm:text-4xl">
              知识库管理
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
              像整理候选人档案一样管理你的资料：创建资料库、上传文档、让模型回答时带着引用说话。
            </p>
          </div>
          <button
            className="rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 shadow-[0_0_24px_rgba(251,191,36,0.25)] transition hover:bg-hire-200 active:scale-[0.98]"
            onClick={() => setShowCreateModal(true)}
            ref={createKnowledgeBaseButtonRef}
            type="button"
          >
            新建知识库
          </button>
        </div>
      </header>

      {(error || notice) ? (
        <div
          aria-live={error ? "assertive" : "polite"}
          className={`mb-5 rounded-lg border px-4 py-3 text-sm ${
            error
              ? "border-rose-300/25 bg-rose-400/10 text-rose-100"
              : "border-emerald-300/25 bg-emerald-400/10 text-emerald-100"
          }`}
          role={error ? "alert" : "status"}
        >
          {error || notice}
        </div>
      ) : null}

      {!error && uploadWarningSummary.items.length > 0 ? (
        <div
          aria-live="polite"
          className="-mt-3 mb-5 rounded-lg bg-amber-300/10 px-4 py-3 text-xs leading-5 text-amber-100"
          role="status"
        >
          <p className="font-semibold">Office 解析提示</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {uploadWarningSummary.items.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          {uploadWarningSummary.hiddenCount > 0 ? (
            <p className="mt-1 text-amber-200/80">
              另有 {uploadWarningSummary.hiddenCount} 项解析提示未展开。
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="surface-panel rounded-lg p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white">资料库列表</h2>
              <p className="mt-1 text-xs text-slate-400">
                {
                  knowledgeBases.filter(
                    (item) => (item.deletion_status ?? "active") === "active",
                  ).length
                } 个资料库可用
                {knowledgeBases.some(
                  (item) => (item.deletion_status ?? "active") !== "active",
                )
                  ? "，另有清理任务"
                  : ""}
              </p>
            </div>
            <button
              className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
              onClick={() => void loadKnowledgeBases()}
              type="button"
            >
              刷新
            </button>
          </div>

          <div className="mt-4 space-y-3">
            {isLoadingKbs ? (
              <div className="rounded-lg border border-white/10 bg-white/[0.04] p-4 text-sm text-slate-400">
                正在整理资料库名册...
              </div>
            ) : knowledgeBases.length === 0 ? (
              <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] p-5 text-sm leading-6 text-slate-400">
                资料库展台还空着。先新建一个知识库，再上传你的文档。
              </div>
            ) : (
              knowledgeBases.map((kb) => {
                const cleanupPending =
                  (kb.deletion_status ?? "active") !== "active";
                return (
                  <article
                    className={`rounded-lg border p-4 transition ${
                      cleanupPending
                        ? "border-amber-300/30 bg-amber-300/10"
                        : kb.id === selectedKbId
                          ? "border-hire-300/50 bg-hire-300/10 shadow-[0_0_28px_rgba(251,191,36,0.16)]"
                          : "border-white/10 bg-white/[0.045] hover:border-hire-300/25 hover:bg-white/[0.07]"
                    }`}
                    key={kb.id}
                  >
                    {cleanupPending ? (
                      <div aria-label={`${kb.name} 已隔离，等待彻底清理`}>
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <h3 className="font-semibold text-white">{kb.name}</h3>
                            <p className="mt-1 text-xs leading-5 text-amber-100">
                              已从检索与上传入口隔离，仍有文件或派生物等待清理。
                            </p>
                          </div>
                          <span className="rounded-full bg-amber-200/15 px-2 py-1 text-[11px] font-semibold text-amber-100">
                            清理待完成
                          </span>
                        </div>
                      </div>
                    ) : (
                      <button
                        className="block min-h-11 w-full text-left"
                        onClick={() => setSelectedKbId(kb.id)}
                        type="button"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <h3 className="font-semibold text-white">{kb.name}</h3>
                            <p className="mt-1 text-xs text-slate-400">
                              {kb.document_count} 份文档 · 更新于 {formatDate(kb.updated_at)}
                            </p>
                          </div>
                          <span className="rounded-full border border-white/10 bg-white/[0.06] px-2 py-1 text-[11px] font-semibold text-slate-300">
                            打开
                          </span>
                        </div>
                      </button>
                    )}
                    <button
                      className={`mt-3 min-h-11 rounded-full px-3 text-xs font-semibold transition ${
                        cleanupPending
                          ? "border border-amber-200/30 text-amber-50 hover:bg-amber-200/10"
                          : "text-rose-200 hover:bg-rose-200/10 hover:text-rose-100"
                      }`}
                      onClick={() => void deleteKnowledgeBase(kb)}
                      ref={(element) => {
                        if (element) {
                          knowledgeBaseCleanupButtonRefs.current.set(kb.id, element);
                        } else {
                          knowledgeBaseCleanupButtonRefs.current.delete(kb.id);
                        }
                      }}
                      type="button"
                    >
                      {cleanupPending ? "重试彻底清理" : "彻底删除资料库"}
                    </button>
                  </article>
                );
              })
            )}
          </div>
        </section>

        <section className="surface-panel min-h-[560px] rounded-lg p-4 sm:p-5">
          {selectedKnowledgeBase ? (
            <>
              <div className="flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <p className="text-sm font-semibold text-hire-100">当前资料库</p>
                  <h2 className="mt-2 text-2xl font-semibold text-white">
                    {selectedKnowledgeBase.name}
                  </h2>
                  <p className="mt-2 text-sm text-slate-400">
                    {selectedKnowledgeBase.document_count} 份文档，创建于{" "}
                    {formatDate(selectedKnowledgeBase.created_at)}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selectedKnowledgeBase.corpus_locked ? (
                    <span className="cursor-not-allowed rounded-lg border border-white/10 bg-white/[0.035] px-4 py-2 text-sm font-semibold text-slate-500" title="标准 Benchmark 语料不可写入">知识审批已锁定</span>
                  ) : (
                    <Link
                      className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-2 text-sm font-semibold text-amber-100 transition hover:bg-amber-300/15"
                      to={`/rag/${encodeURIComponent(selectedKnowledgeBase.id)}/inbox`}
                    >
                      知识审批 Inbox
                    </Link>
                  )}
                  <Link
                    className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-4 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/15"
                    to={`/rag/${encodeURIComponent(selectedKnowledgeBase.id)}/pipeline`}
                  >
                    打开流水线画布
                  </Link>
                  <Link
                    className="rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-4 py-2 text-sm font-semibold text-emerald-100 transition hover:bg-emerald-300/15"
                    to={`/rag/${encodeURIComponent(selectedKnowledgeBase.id)}/evaluation`}
                  >
                    检索质量评估
                  </Link>
                  <button
                    className="rounded-lg border border-hire-300/30 bg-hire-300/10 px-4 py-2 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={ragFileSelectionDisabled}
                    onClick={() => fileInputRef.current?.click()}
                    ref={uploadButtonRef}
                    type="button"
                  >
                    上传文档
                  </button>
                </div>
              </div>

              {selectedKnowledgeBase.corpus_locked ? (
                <div className="mt-5 rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm text-amber-100">
                  <strong>RAG 引擎标准基准语料已锁定。</strong>
                  <span className="ml-2 text-amber-100/80">该语料只用于检索一致性与回归验证，不代表业务知识库质量。不能上传、删除文档或审批写入；仍可调整流水线、构建候选版本、评测、激活和回滚。</span>
                </div>
              ) : null}

              <input
                accept={ragAccept}
                className="hidden"
                disabled={ragFileSelectionDisabled}
                multiple
                onChange={(event) => {
                  enqueueUploadFiles(Array.from(event.target.files ?? []));
                  event.target.value = "";
                }}
                ref={fileInputRef}
                type="file"
              />

              {pendingXlsxFile ? (
                <XlsxDestinationChooser
                  className="mt-5"
                  currentDestination="rag"
                  disabled={isUploading}
                  fileName={pendingXlsxFile.name}
                  onCancel={() => clearPendingXlsxFile(true)}
                  onNavigate={() => clearPendingXlsxFile(false)}
                  onUseCurrent={() => {
                    const file = pendingXlsxFile;
                    void uploadFile(file).then((uploaded) => {
                      if (uploaded) clearPendingXlsxFile(false);
                    });
                  }}
                />
              ) : null}

              <div
                aria-disabled={ragFileSelectionDisabled}
                className={`mt-5 rounded-lg border border-dashed p-6 text-center transition ${
                  isDragging
                    ? "border-hire-300/70 bg-hire-300/10"
                    : "border-white/15 bg-white/[0.035] hover:border-hire-300/35"
                }`}
                onDragLeave={() => setIsDragging(false)}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (!ragFileSelectionDisabled) setIsDragging(true);
                }}
                onDrop={handleDrop}
              >
                <p className="text-sm font-semibold text-white">
                  {selectedKnowledgeBase.corpus_locked ? "此 Benchmark 知识库的标准语料不可变更" : isUploading ? "正在按队列入库..." : "拖拽一批文档到这里，或点击上传"}
                </p>
                <p className="mt-2 text-xs text-slate-400">
                  {selectedKnowledgeBase.corpus_locked ? "请在流水线中调整处理、分块和检索配置，并用固定评测版本比较候选。" : ragFormatHint}
                </p>
                <button
                  className="mt-4 rounded-full bg-white/[0.08] px-4 py-2 text-sm font-semibold text-slate-100 transition hover:bg-white/[0.12] disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={ragFileSelectionDisabled}
                  onClick={() => fileInputRef.current?.click()}
                  type="button"
                >
                  {isUploading
                    ? "处理中"
                      : selectedKnowledgeBase.corpus_locked
                        ? "语料已锁定"
                        : ragUploadAvailable
                      ? "选择文件（可多选）"
                      : "暂不可用"}
                </button>
              </div>

              {uploadQueue.length > 0 ? (
                <section
                  aria-label="上传队列"
                  className="mt-4 overflow-hidden rounded-lg border border-white/10 bg-white/[0.025]"
                >
                  <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
                    <div>
                      <h3 className="text-sm font-semibold text-white">批量上传队列</h3>
                      <p className="mt-0.5 text-[11px] text-slate-400">
                        文件逐个入库；浏览器取消不保证服务端停止，系统会刷新文档清单确认结果。
                      </p>
                    </div>
                    <button
                      className="text-xs font-semibold text-slate-400 transition hover:text-white disabled:opacity-40"
                      disabled={isUploading}
                      onClick={() =>
                        setUploadQueue((current) =>
                          current.filter(
                            (item) =>
                              item.status === "queued" || item.status === "uploading",
                          ),
                        )
                      }
                      type="button"
                    >
                      清除已结束
                    </button>
                  </div>
                  <div className="divide-y divide-white/10">
                    {uploadQueue.map((item) => {
                      const canRetry =
                        (item.status === "failed" || item.status === "cancelled") &&
                        !isXlsxFile(item.file) &&
                        !validateFile(item.file, ragExtensions, ragMaxFileBytes);
                      return (
                        <article
                          className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                          key={item.id}
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-slate-100">
                              {item.file.name}
                            </p>
                            <p className="mt-0.5 text-[11px] text-slate-400">
                              {formatFileSize(item.file.size)} · {ragUploadStatusLabel(item.status)}
                              {item.chunkCount != null ? ` · ${item.chunkCount} 个片段` : ""}
                            </p>
                            {item.error ? (
                              <p className="mt-1 text-xs text-rose-200" role="alert">
                                {item.error}
                              </p>
                            ) : null}
                          </div>
                          <div className="flex shrink-0 items-center gap-2">
                            {item.status === "queued" || item.status === "uploading" ? (
                              <button
                                className="rounded-full border border-slate-300/20 px-3 py-1 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.08]"
                                onClick={() => cancelQueuedUpload(item)}
                                type="button"
                              >
                                取消此文件
                              </button>
                            ) : null}
                            {canRetry ? (
                              <button
                                className="rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20 disabled:opacity-40"
                                disabled={isUploading}
                                onClick={() => retryQueuedUpload(item)}
                                type="button"
                              >
                                重试
                              </button>
                            ) : null}
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </section>
              ) : null}

              <section className="mt-5 overflow-hidden rounded-lg border border-white/10 bg-white/[0.035]">
                <button
                  className="flex w-full flex-col gap-3 px-4 py-3 text-left transition hover:bg-white/[0.04] sm:flex-row sm:items-center sm:justify-between"
                  onClick={() => setIsPipelineOpen((value) => !value)}
                  type="button"
                >
                  <span>
                    <span className="block text-sm font-semibold text-white">
                      知识流水线 Beta
                    </span>
                    <span className="mt-1 block text-xs leading-5 text-slate-400">
                      FileAsset → Artifact → Chunk → 版本化索引 → CitationAnchor
                    </span>
                  </span>
                  <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1 text-xs font-semibold text-hire-100">
                    {isPipelineOpen ? "收起" : "展开"}
                  </span>
                </button>

                {isPipelineOpen ? (
                  <div className="border-t border-white/10 px-4 py-4">
                    {isLoadingPipeline ? (
                      <p className="text-sm text-slate-400">正在读取知识流水线元数据...</p>
                    ) : pipelineError ? (
                      <p className="text-sm text-rose-100">{pipelineError}</p>
                    ) : (
                      <>
                        <div className="rounded-lg border border-hire-300/20 bg-hire-300/10 p-3 text-xs leading-5 text-hire-50">
                          <span className="font-semibold">使用须知：</span>
                          保存草稿不会改变检索。执行后会生成隔离的候选索引，预览并手动激活后，RAG、聊天和知识引用才会切换；激活旧版本即可回滚。
                        </div>

                        <div className="mt-3 flex flex-col gap-3 rounded-lg border border-white/10 bg-ink-950/35 p-3 sm:flex-row sm:items-center sm:justify-between">
                          <div>
                            <p className="text-xs font-semibold text-slate-300">
                              草稿版本 v{pipelineDraft?.version ?? 1}
                            </p>
                            <p className="mt-1 text-xs text-slate-500">
                              {pipelineDraft?.updated_at
                                ? `更新于 ${formatDate(pipelineDraft.updated_at)}`
                                : "尚未保存草稿"}
                            </p>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <button
                              className="rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={isSavingPipelineDraft || !pipelineDraft}
                              onClick={() => void savePipelineDraft()}
                              type="button"
                            >
                              {isSavingPipelineDraft ? "保存中..." : "保存草稿"}
                            </button>
                            <button
                              className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={isPreflightingPipeline || !pipelineDraft}
                              onClick={() => void runPipelinePreflight()}
                              type="button"
                            >
                              {isPreflightingPipeline ? "预检中..." : "运行预检"}
                            </button>
                            <button
                              className="inline-flex items-center gap-1.5 rounded-lg bg-hire-300 px-3 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={isExecutingPipeline || !pipelineDraft || documents.length === 0}
                              onClick={() => void executePipelineDraft()}
                              title={documents.length === 0 ? "请先上传至少一份文档" : "构建候选知识索引"}
                              type="button"
                            >
                              {isExecutingPipeline ? "创建任务中..." : "执行草稿"}
                            </button>
                          </div>
                        </div>

                        {pipelineDraftNotice ? (
                          <div className="mt-3 rounded-lg border border-emerald-300/20 bg-emerald-300/10 p-3 text-xs leading-5 text-emerald-100">
                            {pipelineDraftNotice}
                          </div>
                        ) : null}

                        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                          {(pipelineDraft?.stages ?? []).map((stage, index) => (
                            <div
                              className="rounded-lg border border-white/10 bg-ink-950/35 p-3"
                              key={stage.id}
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div>
                                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                                    Stage {index + 1}
                                  </p>
                                  <p className="mt-1 text-sm font-semibold text-white">
                                    {stage.title}
                                  </p>
                                </div>
                                <span
                                  className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${stageStatusClass(
                                    stage.status,
                                  )}`}
                                >
                                  {stageStatusLabel(stage.status)}
                                </span>
                              </div>
                              <p className="mt-3 text-2xl font-semibold text-white">
                                {stage.item_count}
                              </p>
                              {stage.kind === "chunker" ? (
                                <p className="mt-1 text-[10px] font-medium text-slate-500">
                                  个预处理结构块
                                </p>
                              ) : null}
                              <p className="mt-2 min-h-10 text-xs leading-5 text-slate-400">
                                {stage.kind === "chunker"
                                  ? "执行后会按当前配置生成候选索引片段；此处不是当前激活索引数量。"
                                  : stage.summary}
                              </p>
                              {stage.config ? (
                                <dl className="mt-3 space-y-1 border-t border-white/10 pt-3 text-[11px] text-slate-400">
                                  {Object.entries(stage.config).map(([key, value]) => (
                                    <div
                                      className="flex items-start justify-between gap-2"
                                      key={key}
                                    >
                                      <dt className="shrink-0 text-slate-500">{key}</dt>
                                      <dd className="break-all text-right text-slate-300">
                                        {formatConfigValue(value)}
                                      </dd>
                                    </div>
                                  ))}
                                </dl>
                              ) : null}
                            </div>
                          ))}
                        </div>

                        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
                          <div className="rounded-lg border border-white/10 bg-white/[0.025] p-4">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <p className="text-sm font-semibold text-white">草稿配置</p>
                                <p className="mt-1 text-xs leading-5 text-slate-400">
                                  执行时固定处理器、分块、Embedding 与检索配置。
                                </p>
                              </div>
                              <span className="rounded-full border border-white/10 bg-white/[0.06] px-2 py-0.5 text-[11px] font-semibold text-slate-300">
                                editable
                              </span>
                            </div>
                            <div className="mt-4 border-t border-white/10 pt-4">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div>
                                  <p className="text-xs font-semibold text-white">文档处理与生成式索引</p>
                                  <p className="mt-1 text-[11px] leading-5 text-slate-500">
                                    General 保留结构正文；QA 索引问题并返回答案原文；Summary 索引摘要并提升来源块。
                                  </p>
                                </div>
                                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                                  pipelineDraftEdits.processorMode === "general" || processorCapabilities?.llm_configured
                                    ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                    : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                                }`}>
                                  {pipelineDraftEdits.processorMode === "general"
                                    ? "本地结构解析"
                                    : processorCapabilities?.llm_configured ? "模型已就绪" : "模型未配置"}
                                </span>
                              </div>
                              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                                <label className="block">
                                  <span className="text-xs font-medium text-slate-300">处理模式</span>
                                  <select
                                    className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                    onChange={(event) => setPipelineDraftEdits((current) => ({
                                      ...current,
                                      processorMode: event.target.value as PipelineDraftEdits["processorMode"],
                                    }))}
                                    value={pipelineDraftEdits.processorMode}
                                  >
                                    <option value="general">General 结构正文</option>
                                    <option value="qa">QA 问答索引</option>
                                    <option value="summary">Summary 摘要索引</option>
                                  </select>
                                </label>
                                <label className="block">
                                  <span className="text-xs font-medium text-slate-300">失败策略</span>
                                  <select
                                    className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                    onChange={(event) => setPipelineDraftEdits((current) => ({
                                      ...current,
                                      processorFailurePolicy: event.target.value as PipelineDraftEdits["processorFailurePolicy"],
                                    }))}
                                    value={pipelineDraftEdits.processorFailurePolicy}
                                  >
                                    <option value="continue_on_error">部分成功可生成候选</option>
                                    <option value="strict">任一失败阻断候选</option>
                                  </select>
                                </label>
                                {pipelineDraftEdits.processorMode !== "general" ? (
                                  <label className="block">
                                    <span className="text-xs font-medium text-slate-300">生成模型</span>
                                    <input
                                      className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                      onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, processorModelId: event.target.value }))}
                                      placeholder={processorCapabilities?.model_label || "OpenRouter / newAPI 模型 ID"}
                                      value={pipelineDraftEdits.processorModelId}
                                    />
                                  </label>
                                ) : null}
                                <label className="block">
                                  <span className="text-xs font-medium text-slate-300">每文档最多生成项</span>
                                  <input
                                    className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                    max={50}
                                    min={1}
                                    onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, maxGeneratedItems: event.target.value }))}
                                    type="number"
                                    value={pipelineDraftEdits.maxGeneratedItems}
                                  />
                                </label>
                              </div>
                              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                                {[
                                  ["extractTitle", "提取标题"],
                                  ["preserveTables", "保留表格结构"],
                                  ["preserveCodeBlocks", "保留代码块"],
                                  ["removeRepeatedHeadersFooters", "移除 PDF 重复页眉页脚"],
                                ].map(([field, label]) => (
                                  <label className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-white/[0.025] px-3 py-2" key={field}>
                                    <span className="text-[11px] text-slate-300">{label}</span>
                                    <input
                                      checked={Boolean(pipelineDraftEdits[field as keyof PipelineDraftEdits])}
                                      className="h-4 w-4 accent-hire-300"
                                      onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, [field]: event.target.checked }))}
                                      type="checkbox"
                                    />
                                  </label>
                                ))}
                              </div>
                              <div className="mt-3 text-[11px] leading-5 text-slate-500">
                                {processorCapabilities ? (
                                  <p>
                                    结构块：{processorCapabilities.block_types.join(" / ")} · 生成目标：{processorCapabilities.generation_targets.join(" / ") || "未配置"}
                                  </p>
                                ) : (
                                  <p>{processorCapabilitiesError || "正在读取处理器能力摘要..."}</p>
                                )}
                              </div>
                              <div className="mt-3 flex flex-col gap-2 border-t border-white/10 pt-3 sm:flex-row sm:items-end">
                                <label className="min-w-0 flex-1">
                                  <span className="text-xs font-medium text-slate-300">处理预览文档</span>
                                  <select
                                    className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                    onChange={(event) => setProcessorPreviewDocumentId(event.target.value)}
                                    value={processorPreviewDocumentId}
                                  >
                                    {documents.map((document) => <option key={document.id} value={document.id}>{document.filename}</option>)}
                                  </select>
                                </label>
                                <button
                                  className="shrink-0 rounded-lg border border-white/10 bg-white/[0.06] px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-50"
                                  disabled={!processorPreviewDocumentId || isPreviewingProcessor}
                                  onClick={() => void previewProcessor()}
                                  type="button"
                                >
                                  {isPreviewingProcessor ? "生成预览中..." : "预览处理结果"}
                                </button>
                              </div>
                              {processorPreview ? (
                                <div className="mt-3 overflow-hidden rounded-lg border border-sky-300/20 bg-sky-300/[0.05]">
                                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-3 py-2">
                                    <div>
                                      <p className="text-xs font-semibold text-white">{processorPreview.title}</p>
                                      <p className="mt-0.5 text-[10px] text-slate-400">
                                        {processorPreview.block_count} blocks · {processorPreview.generated_count} generated · {processorPreview.character_count} chars
                                      </p>
                                    </div>
                                    <span className="text-[10px] text-sky-100">只读预览，不写入索引</span>
                                  </div>
                                  <div className="max-h-64 space-y-2 overflow-y-auto p-3">
                                    {(processorPreview.generated_items.length > 0
                                      ? processorPreview.generated_items.map((item) => ({ id: item.item_id, kind: item.item_type, text: item.index_text, detail: item.context_text }))
                                      : processorPreview.blocks.map((block) => { const location = formatRagSourceLocation({ ...block.metadata, page_number: block.page_number }); return { id: block.block_id, kind: location ? `${block.kind} · ${location}` : block.kind, text: block.text, detail: "" }; })
                                    ).map((item) => (
                                      <div className="border-b border-white/10 pb-2 last:border-0 last:pb-0" key={item.id}>
                                        <span className="rounded border border-white/10 px-1.5 py-0.5 text-[10px] text-slate-400">{item.kind}</span>
                                        <p className="mt-1 text-[11px] leading-5 text-slate-200">{item.text}</p>
                                        {item.detail ? <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-slate-500">{item.detail}</p> : null}
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              ) : null}
                            </div>
                            <div className="mt-4 border-t border-white/10 pt-4">
                              <div className="flex items-center justify-between gap-3">
                                <p className="text-xs font-semibold text-white">分块与分段</p>
                                <span className="text-[10px] uppercase text-slate-500">schema v{pipelineDraft?.index_schema_version ?? 2}</span>
                              </div>
                              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                                <label className="block">
                                  <span className="text-xs font-medium text-slate-300">分块策略</span>
                                  <select
                                    className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                    onChange={(event) => setPipelineDraftEdits((current) => ({
                                      ...current,
                                      strategy: event.target.value as PipelineDraftEdits["strategy"],
                                    }))}
                                    value={pipelineDraftEdits.strategy}
                                  >
                                    <option value="recursive_character">递归字符分块</option>
                                    <option value="parent_child">父子分段</option>
                                  </select>
                                </label>
                                <label className="block">
                                  <span className="text-xs font-medium text-slate-300">Embedding 模型</span>
                                  <select
                                    className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                    onChange={(event) => setPipelineDraftEdits((current) => ({
                                      ...current,
                                      embeddingProvider: "openai_compatible",
                                      embeddingModel: event.target.value,
                                    }))}
                                    value={pipelineDraftEdits.embeddingModel}
                                  >
                                    {!embeddingModelOptions.some(
                                      (option) =>
                                        option.id ===
                                        pipelineDraftEdits.embeddingModel,
                                    ) && pipelineDraftEdits.embeddingModel ? (
                                      <option value={pipelineDraftEdits.embeddingModel}>
                                        {pipelineDraftEdits.embeddingModel}（当前配置）
                                      </option>
                                    ) : null}
                                    {embeddingModelOptions.map((option) => (
                                      <option key={option.id} value={option.id}>
                                        {option.name} · {option.provider}
                                      </option>
                                    ))}
                                  </select>
                                </label>
                              </div>
                              {pipelineDraft ? (
                                <p
                                  className={`mt-2 text-[11px] leading-5 ${
                                    effectiveEmbeddingProfile.ready === false
                                      ? "text-amber-200"
                                      : "text-slate-500"
                                  }`}
                                >
                                  请求：{String(requestedEmbeddingProfile.provider || "未设置")} / {String(requestedEmbeddingProfile.model || "未设置")}
                                  {" · "}
                                  生效：{String(effectiveEmbeddingProfile.provider || "未设置")} / {String(effectiveEmbeddingProfile.model || "未设置")}
                                  {effectiveEmbeddingProfile.ready === false
                                    ? "（不可用，预检与执行将被阻止）"
                                    : effectiveEmbeddingProfile.degraded
                                      ? "（显式降级模式）"
                                      : ""}
                                </p>
                              ) : null}

                              {pipelineDraftEdits.strategy === "recursive_character" ? (
                                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                                  <label className="block">
                                    <span className="text-xs font-medium text-slate-300">分块大小</span>
                                    <input
                                      className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                      max={4000}
                                      min={100}
                                      onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, chunkSize: event.target.value }))}
                                      type="number"
                                      value={pipelineDraftEdits.chunkSize}
                                    />
                                  </label>
                                  <label className="block">
                                    <span className="text-xs font-medium text-slate-300">重叠字符</span>
                                    <input
                                      className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                      min={0}
                                      onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, chunkOverlap: event.target.value }))}
                                      type="number"
                                      value={pipelineDraftEdits.chunkOverlap}
                                    />
                                  </label>
                                  <label className="block sm:col-span-2">
                                    <span className="text-xs font-medium text-slate-300">分段标识符（每行一个）</span>
                                    <textarea
                                      className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs leading-5 text-white outline-none focus:border-hire-300/50"
                                      onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, separators: event.target.value }))}
                                      value={pipelineDraftEdits.separators}
                                    />
                                  </label>
                                </div>
                              ) : (
                                <div className="mt-3 space-y-3">
                                  <div className="grid gap-3 sm:grid-cols-2">
                                    <label className="block">
                                      <span className="text-xs font-medium text-slate-300">父段大小 / 重叠</span>
                                      <div className="mt-2 grid grid-cols-2 gap-2">
                                        <input className="w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={100} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, parentChunkSize: event.target.value }))} type="number" value={pipelineDraftEdits.parentChunkSize} />
                                        <input className="w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={0} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, parentChunkOverlap: event.target.value }))} type="number" value={pipelineDraftEdits.parentChunkOverlap} />
                                      </div>
                                    </label>
                                    <label className="block">
                                      <span className="text-xs font-medium text-slate-300">子段大小 / 重叠</span>
                                      <div className="mt-2 grid grid-cols-2 gap-2">
                                        <input className="w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={100} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, childChunkSize: event.target.value }))} type="number" value={pipelineDraftEdits.childChunkSize} />
                                        <input className="w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={0} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, childChunkOverlap: event.target.value }))} type="number" value={pipelineDraftEdits.childChunkOverlap} />
                                      </div>
                                    </label>
                                  </div>
                                  <div className="grid gap-3 sm:grid-cols-2">
                                    <label className="block">
                                      <span className="text-xs font-medium text-slate-300">父段标识符</span>
                                      <textarea className="mt-2 min-h-24 w-full resize-y rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs leading-5 text-white outline-none focus:border-hire-300/50" onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, parentSeparators: event.target.value }))} value={pipelineDraftEdits.parentSeparators} />
                                    </label>
                                    <label className="block">
                                      <span className="text-xs font-medium text-slate-300">子段标识符</span>
                                      <textarea className="mt-2 min-h-24 w-full resize-y rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 font-mono text-xs leading-5 text-white outline-none focus:border-hire-300/50" onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, childSeparators: event.target.value }))} value={pipelineDraftEdits.childSeparators} />
                                    </label>
                                  </div>
                                  <p className="text-[11px] leading-5 text-slate-500">子段参与索引；命中后提升并返回父段上下文，同时保留子段作为引用锚点。</p>
                                </div>
                              )}
                            </div>

                            <div className="mt-4 border-t border-white/10 pt-4">
                              <p className="text-xs font-semibold text-white">检索与 Rerank</p>
                              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                                <label className="block">
                                  <span className="text-xs font-medium text-slate-300">检索模式</span>
                                  <select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, retrievalMode: event.target.value as PipelineDraftEdits["retrievalMode"] }))} value={pipelineDraftEdits.retrievalMode}>
                                    <option value="hybrid">混合检索</option>
                                    <option value="vector">向量检索</option>
                                    <option value="fulltext">全文检索</option>
                                  </select>
                                </label>
                                <div className="grid grid-cols-2 gap-2">
                                  <label className="block"><span className="text-xs font-medium text-slate-300">Top-K</span><input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={1} max={50} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, topK: event.target.value }))} type="number" value={pipelineDraftEdits.topK} /></label>
                                  <label className="block"><span className="text-xs font-medium text-slate-300">Score 阈值</span><input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={0} max={1} step={0.05} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, scoreThreshold: event.target.value }))} type="number" value={pipelineDraftEdits.scoreThreshold} /></label>
                                </div>
                              </div>
                              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                                <label className="block"><span className="text-xs font-medium text-slate-300">向量权重</span><input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" disabled={pipelineDraftEdits.retrievalMode !== "hybrid"} min={0} max={1} step={0.1} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, vectorWeight: event.target.value }))} type="number" value={pipelineDraftEdits.vectorWeight} /></label>
                                <label className="block"><span className="text-xs font-medium text-slate-300">全文权重</span><input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" disabled={pipelineDraftEdits.retrievalMode !== "hybrid"} min={0} max={1} step={0.1} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, fulltextWeight: event.target.value }))} type="number" value={pipelineDraftEdits.fulltextWeight} /></label>
                                <label className="block"><span className="text-xs font-medium text-slate-300">候选倍数</span><input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={1} max={10} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, candidateMultiplier: event.target.value }))} type="number" value={pipelineDraftEdits.candidateMultiplier} /></label>
                              </div>
                              <div className="mt-3 flex items-center justify-between gap-3 border-y border-white/10 py-3">
                                <div><p className="text-xs font-medium text-slate-200">启用 Rerank</p><p className="mt-1 text-[11px] text-slate-500">失败时保留混合融合排序，并返回 warning。</p></div>
                                <input checked={pipelineDraftEdits.rerankEnabled} className="h-4 w-4 accent-hire-300" onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, rerankEnabled: event.target.checked }))} type="checkbox" />
                              </div>
                              {pipelineDraftEdits.rerankEnabled ? (
                                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                                  <label className="block"><span className="text-xs font-medium text-slate-300">Provider</span><select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, rerankProvider: event.target.value as PipelineDraftEdits["rerankProvider"] }))} value={pipelineDraftEdits.rerankProvider}><option value="auto">自动</option><option value="api">专用 API</option><option value="llm">LLM JSON</option></select></label>
                                  <label className="block">
                                    <span className="text-xs font-medium text-slate-300">Rerank 模型</span>
                                    <select
                                      className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50"
                                      onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, rerankModel: event.target.value }))}
                                      value={pipelineDraftEdits.rerankModel}
                                    >
                                      <option value="">使用服务端默认模型</option>
                                      {!rerankModelOptions.some((option) => option.id === pipelineDraftEdits.rerankModel) && pipelineDraftEdits.rerankModel ? (
                                        <option value={pipelineDraftEdits.rerankModel}>
                                          {pipelineDraftEdits.rerankModel}（当前配置）
                                        </option>
                                      ) : null}
                                      {rerankModelOptions.map((option) => (
                                        <option key={option.id} value={option.id}>
                                          {option.name}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <label className="block"><span className="text-xs font-medium text-slate-300">Rerank Top-N</span><input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" min={1} max={50} onChange={(event) => setPipelineDraftEdits((current) => ({ ...current, rerankTopN: event.target.value }))} type="number" value={pipelineDraftEdits.rerankTopN} /></label>
                                </div>
                              ) : null}
                              <div className="mt-3 text-[11px] leading-5 text-slate-500">
                                {retrievalCapabilities ? (
                                  <p>向量：{retrievalCapabilities.vector.backend} · 全文：{retrievalCapabilities.fulltext.backend} · Embedding：{retrievalCapabilities.embedding.provider}{retrievalCapabilities.embedding.degraded ? "（降级模式）" : ""} · Rerank API/LLM：{retrievalCapabilities.rerank.api_configured ? "ready" : "off"}/{retrievalCapabilities.rerank.llm_configured ? "ready" : "off"}</p>
                                ) : (
                                  <p>{retrievalCapabilitiesError || "正在读取检索能力摘要..."}</p>
                                )}
                              </div>
                            </div>
                            <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-2">
                              <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                                数据源锁定为 uploaded_files
                              </div>
                              <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                                处理器：{pipelineDraftEdits.processorMode} · {pipelineDraftEdits.processorFailurePolicy === "strict" ? "严格阻断" : "逐文档容错"}
                              </div>
                              <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3 sm:col-span-2">
                                {pipelineDraft?.stages.some(
                                  (stage) =>
                                    stage.kind === "image_understanding" &&
                                    stage.config?.enabled === true,
                                )
                                  ? `图像理解已启用 · ${
                                      pipelineDraft.stages.find(
                                        (stage) =>
                                          stage.kind === "image_understanding",
                                      )?.config?.vision_model_id ?? "已配置模型"
                                    }`
                                  : "图像理解未启用；图片和扫描 PDF 需启用后再执行流水线。"}
                              </div>
                            </div>
                          </div>

                          <div className="rounded-lg border border-white/10 bg-white/[0.025] p-4">
                            <div className="flex items-center justify-between gap-3">
                              <p className="text-sm font-semibold text-white">预检结果</p>
                              {pipelinePreflight ? (
                                <span
                                  className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
                                    pipelinePreflight.ready
                                      ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                      : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                                  }`}
                                >
                                  {pipelinePreflight.ready ? "ready" : "warnings"}
                                </span>
                              ) : null}
                            </div>
                            {!pipelinePreflight ? (
                              <p className="mt-3 text-xs leading-5 text-slate-400">
                                点击“运行预检”检查当前草稿与已入库文档、Artifact、Chunk 的一致性。
                              </p>
                            ) : (
                              <div className="mt-3 space-y-3">
                                {pipelinePreflight.warnings.length > 0 ? (
                                  <div className="rounded-lg border border-amber-300/20 bg-amber-300/10 p-3 text-xs leading-5 text-amber-100">
                                    {pipelinePreflight.warnings.map((warning) => (
                                      <p key={warning}>{warning}</p>
                                    ))}
                                  </div>
                                ) : (
                                  <div className="rounded-lg border border-emerald-300/20 bg-emerald-300/10 p-3 text-xs text-emerald-100">
                                    当前草稿通过预检。
                                  </div>
                                )}
                                <div className="space-y-2">
                                  {pipelinePreflight.stage_checks.map((check) => (
                                    <div
                                      className="rounded-lg border border-white/10 bg-ink-950/35 p-3"
                                      key={check.id}
                                    >
                                      <div className="flex items-center justify-between gap-3">
                                        <p className="text-xs font-semibold text-white">
                                          {check.title}
                                        </p>
                                        <span
                                          className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${preflightSeverityClass(
                                            check.severity,
                                          )}`}
                                        >
                                          {check.severity}
                                        </span>
                                      </div>
                                      <p className="mt-2 text-xs leading-5 text-slate-400">
                                        {check.summary}
                                      </p>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>

                        <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
                          <section className="rounded-lg border border-white/10 bg-white/[0.025] p-4">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <h3 className="text-sm font-semibold text-white">执行任务</h3>
                                <p className="mt-1 text-xs leading-5 text-slate-400">
                                  五阶段构建候选索引。失败或取消不会影响当前激活版本。
                                </p>
                              </div>
                              <button
                                className="rounded-md border border-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-300 transition hover:bg-white/[0.07] hover:text-white"
                                onClick={() => void loadPipelineRuntime(selectedKbId)}
                                type="button"
                              >
                                刷新
                              </button>
                            </div>

                            {pipelineJobs.length > 0 ? (
                              <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
                                {pipelineJobs.slice(0, 8).map((job) => (
                                  <button
                                    className={`shrink-0 rounded-md border px-2.5 py-1.5 text-left text-[11px] transition ${
                                      selectedPipelineJob?.job_id === job.job_id
                                        ? "border-hire-300/45 bg-hire-300/10 text-hire-100"
                                        : "border-white/10 bg-white/[0.035] text-slate-400 hover:text-slate-200"
                                    }`}
                                    key={job.job_id}
                                    onClick={() => setSelectedPipelineJobId(job.job_id)}
                                    type="button"
                                  >
                                    v{job.candidate_version} · {pipelineJobStatusLabel(job.status)}
                                  </button>
                                ))}
                              </div>
                            ) : null}

                            {selectedPipelineJob ? (
                              <div className="mt-3">
                                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 pb-3">
                                  <div>
                                    <p className="font-mono text-[11px] text-slate-300">{selectedPipelineJob.job_id}</p>
                                    <p className="mt-1 text-[11px] text-slate-500">
                                      {selectedPipelineJob.source_count} 个来源 · attempt {selectedPipelineJob.attempt}
                                    </p>
                                  </div>
                                  <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${pipelineJobStatusClass(selectedPipelineJob.status)}`}>
                                    {pipelineJobStatusLabel(selectedPipelineJob.status)}
                                  </span>
                                </div>

                                <div className="mt-3 space-y-2">
                                  {selectedPipelineJob.stages.map((stage) => (
                                    <div className="grid grid-cols-[86px_minmax(0,1fr)_auto] items-center gap-2" key={stage.id}>
                                      <span className="text-[11px] font-medium text-slate-300">{stage.title}</span>
                                      <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.07]">
                                        <div
                                          className={`h-full rounded-full transition-[width] duration-200 ${
                                            stage.status === "failed" ? "bg-rose-300" : "bg-hire-300"
                                          }`}
                                          style={{ width: `${Math.max(0, Math.min(stage.progress, 100))}%` }}
                                        />
                                      </div>
                                      <span className="w-14 text-right text-[10px] text-slate-500">{stage.status}</span>
                                    </div>
                                  ))}
                                </div>

                                {selectedPipelineJob.error ? (
                                  <p className="mt-3 rounded-md border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100">
                                    {selectedPipelineJob.error}
                                  </p>
                                ) : null}

                                {selectedPipelineJob.warnings?.length > 0 ? (
                                  <div className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-[11px] leading-5 text-amber-100">
                                    {selectedPipelineJob.warnings.map((warning) => <p key={warning}>{warning}</p>)}
                                  </div>
                                ) : null}

                                {selectedPipelineJob.document_results?.length > 0 ? (
                                  <div className="mt-3 divide-y divide-white/10 overflow-hidden rounded-md border border-white/10">
                                    {selectedPipelineJob.document_results.map((result) => (
                                      <div className="bg-ink-950/25 px-3 py-2" key={result.source_id}>
                                        <div className="flex flex-wrap items-center justify-between gap-2">
                                          <span className="min-w-0 truncate text-[11px] font-medium text-slate-200">{result.filename}</span>
                                          <span className={`rounded border px-1.5 py-0.5 text-[10px] ${
                                            result.status === "completed"
                                              ? "border-emerald-300/20 text-emerald-100"
                                              : result.status === "failed"
                                                ? "border-rose-300/20 text-rose-100"
                                                : "border-white/10 text-slate-400"
                                          }`}>{result.status}</span>
                                        </div>
                                        <p className="mt-1 text-[10px] text-slate-500">
                                          attempt {result.attempt} · {result.block_count} blocks · {result.generated_count} generated · {result.chunk_count} chunks
                                          {result.duration_ms != null ? ` · ${Math.round(result.duration_ms)} ms` : ""}
                                        </p>
                                        {result.error ? <p className="mt-1 text-[10px] leading-4 text-rose-200">{result.error}</p> : null}
                                      </div>
                                    ))}
                                  </div>
                                ) : null}

                                <div className="mt-3 flex justify-end gap-2">
                                  {selectedPipelineJob.status === "queued" || selectedPipelineJob.status === "running" ? (
                                    <button
                                      className="inline-flex items-center gap-1.5 rounded-md border border-rose-300/20 bg-rose-300/10 px-3 py-1.5 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/15"
                                      onClick={() => void cancelPipelineJob(selectedPipelineJob.job_id)}
                                      type="button"
                                    >
                                      取消任务
                                    </button>
                                  ) : null}
                                  {selectedPipelineJob.status === "failed" || selectedPipelineJob.status === "cancelled" ? (
                                    <button
                                      className="inline-flex items-center gap-1.5 rounded-md border border-white/10 bg-white/[0.06] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.1]"
                                      onClick={() => void retryPipelineJob(selectedPipelineJob.job_id)}
                                      type="button"
                                    >
                                      重试任务
                                    </button>
                                  ) : null}
                                </div>
                              </div>
                            ) : (
                              <p className="mt-3 rounded-lg border border-dashed border-white/10 p-4 text-center text-xs leading-5 text-slate-500">
                                保存并预检草稿后，点击“执行草稿”构建第一个候选版本。
                              </p>
                            )}
                          </section>

                          <section className="rounded-lg border border-white/10 bg-white/[0.025] p-4">
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <h3 className="text-sm font-semibold text-white">索引版本</h3>
                                <p className="mt-1 text-xs leading-5 text-slate-400">
                                  候选版本先预览，再人工激活。激活旧版本即完成回滚。
                                </p>
                              </div>
                              <span className="rounded-full border border-white/10 bg-white/[0.05] px-2 py-0.5 text-[11px] text-slate-300">
                                {pipelineVersions.length} versions
                              </span>
                            </div>

                            <div className="mt-3 flex gap-2">
                              <input
                                className="min-w-0 flex-1 rounded-lg border border-white/10 bg-ink-950/45 px-3 py-2 text-xs text-white outline-none placeholder:text-slate-500 focus:border-hire-300/50"
                                onChange={(event) => setPipelinePreviewQuestion(event.target.value)}
                                placeholder="输入问题预览候选版本"
                                value={pipelinePreviewQuestion}
                              />
                            </div>

                            <div className="mt-3 divide-y divide-white/10 overflow-hidden rounded-lg border border-white/10">
                              {pipelineVersions.length > 0 ? pipelineVersions.map((versionItem) => (
                                <div className="bg-ink-950/30 p-3" key={versionItem.version_id}>
                                  <div className="flex items-start justify-between gap-3">
                                    <div>
                                      <div className="flex items-center gap-2">
                                        <p className="text-sm font-semibold text-white">v{versionItem.version}</p>
                                        {versionItem.active ? (
                                          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-100">
                                            当前激活
                                          </span>
                                        ) : (
                                          <span className="rounded-full border border-sky-300/20 bg-sky-300/10 px-2 py-0.5 text-[10px] text-sky-100">候选</span>
                                        )}
                                      </div>
                                      <p className="mt-1 text-[10px] text-slate-500">
                                        {versionItem.document_count} 文档 · {versionItem.chunk_count} chunks · draft v{versionItem.draft_version}
                                      </p>
                                      <p className="mt-1 text-[10px] text-slate-500">
                                        processor {String(versionItem.processor_profile?.mode ?? "general")} · {versionItem.block_count ?? 0} blocks · QA {versionItem.qa_count ?? 0} · Summary {versionItem.summary_count ?? 0}
                                      </p>
                                      <p className="mt-1 text-[10px] text-slate-500">
                                        index schema v{versionItem.index_schema_version ?? 1} · vector {versionItem.vector_index_ready === false ? "incomplete" : "ready"} · fulltext {versionItem.lexical_index_ready ? "ready" : "legacy/off"}
                                      </p>
                                    </div>
                                    <div className="flex gap-1.5">
                                      <button
                                        className="rounded-md border border-white/10 px-2 py-1.5 text-[11px] font-semibold text-slate-300 transition hover:bg-white/[0.08] hover:text-white disabled:opacity-40"
                                        disabled={!pipelinePreviewQuestion.trim() || Boolean(previewingVersionId)}
                                        onClick={() => void previewPipelineVersion(versionItem.version_id)}
                                        type="button"
                                      >
                                        预览
                                      </button>
                                      {!versionItem.active ? (
                                        <button
                                          className="rounded-md bg-hire-300 px-2.5 py-1.5 text-[11px] font-semibold text-ink-950 transition hover:bg-hire-200 disabled:opacity-50"
                                          disabled={Boolean(activatingVersionId)}
                                          onClick={() => void activatePipelineVersion(versionItem.version_id)}
                                          type="button"
                                        >
                                          {activePipelineVersionId ? "回滚/切换" : "激活"}
                                        </button>
                                      ) : null}
                                    </div>
                                  </div>
                                </div>
                              )) : (
                                <p className="p-4 text-center text-xs leading-5 text-slate-500">
                                  尚无候选版本。执行草稿后在此预览和激活。
                                </p>
                              )}
                            </div>

                            {pipelinePreview ? (
                              <div className="mt-3 rounded-lg border border-sky-300/20 bg-sky-300/[0.07] p-3">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <p className="text-[11px] font-semibold text-sky-100">v{pipelinePreview.version} 预览结果</p>
                                  <span className="text-[10px] text-slate-400">
                                    {String(pipelinePreview.retrieval.mode ?? pipelineDraftEdits.retrievalMode)} · Top-{String(pipelinePreview.retrieval.top_k ?? pipelineDraftEdits.topK)}
                                  </span>
                                </div>
                                <p className="mt-2 line-clamp-5 text-xs leading-5 text-slate-200">{pipelinePreview.answer}</p>
                                {pipelinePreview.warnings.length > 0 ? (
                                  <div className="mt-2 rounded-md border border-amber-300/20 bg-amber-300/10 px-2.5 py-2 text-[11px] leading-5 text-amber-100">
                                    {pipelinePreview.warnings.map((warning) => <p key={warning}>{warning}</p>)}
                                  </div>
                                ) : null}
                                <div className="mt-3 space-y-2">
                                  {pipelinePreview.sources.map((source, index) => { const location = formatPipelineSourceLocation(source); return (
                                    <div className="border-t border-white/10 pt-2" key={`${source.chunk_id}-${index}`}>
                                      <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                                        <span className="font-semibold text-slate-200">{source.document_name}</span>
                                        {location ? <span className="rounded border border-sky-300/20 px-1.5 py-0.5 text-sky-100">{location}</span> : null}
                                        {source.vector_score != null ? <span className="rounded border border-white/10 px-1.5 py-0.5 text-slate-400">vector {source.vector_score.toFixed(3)}</span> : null}
                                        {source.fulltext_score != null ? <span className="rounded border border-white/10 px-1.5 py-0.5 text-slate-400">fts {source.fulltext_score.toFixed(3)}</span> : null}
                                        {source.fused_score != null ? <span className="rounded border border-sky-300/20 px-1.5 py-0.5 text-sky-100">fused {source.fused_score.toFixed(3)}</span> : null}
                                        {source.rerank_score != null ? <span className="rounded border border-emerald-300/20 px-1.5 py-0.5 text-emerald-100">rerank {source.rerank_score.toFixed(3)}</span> : null}
                                        {source.parent_lifted ? <span className="rounded border border-violet-300/20 px-1.5 py-0.5 text-violet-100">父段提升</span> : null}
                                      </div>
                                      <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-400">{source.matched_text || source.text}</p>
                                    </div>
                                  ); })}
                                </div>
                              </div>
                            ) : null}
                          </section>
                        </div>

                        <div className="mt-3 grid gap-3 sm:grid-cols-3">
                          <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              资料文件
                            </p>
                            <p className="mt-2 text-lg font-semibold text-white">
                              {pipelineAssets.length}
                            </p>
                          </div>
                          <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              文档产物
                            </p>
                            <p className="mt-2 text-lg font-semibold text-white">
                              {pipelineArtifacts.length}
                            </p>
                          </div>
                          <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3">
                            <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              当前索引片段
                            </p>
                            <p className="mt-2 text-lg font-semibold text-white">
                              {activePipelineVersion?.chunk_count ?? "尚未激活"}
                            </p>
                            {activePipelineVersion ? (
                              <p className="mt-1 text-[10px] text-slate-500">
                                激活版本 v{activePipelineVersion.version}
                              </p>
                            ) : null}
                          </div>
                        </div>

                        <div className="mt-4">
                          <div className="flex items-center justify-between gap-3">
                            <p className="text-xs font-semibold text-slate-300">
                              最近文档产物
                            </p>
                            <button
                              className="text-xs font-semibold text-hire-100 transition hover:text-hire-50"
                              onClick={(event) => {
                                event.stopPropagation();
                                void loadPipeline(selectedKbId);
                              }}
                              type="button"
                            >
                              刷新
                            </button>
                          </div>
                          {pipelineArtifacts.length === 0 ? (
                            <p className="mt-3 rounded-lg border border-dashed border-white/10 p-3 text-sm text-slate-400">
                              当前资料库还没有可观察的文档产物。
                            </p>
                          ) : (
                            <div className="mt-3 divide-y divide-white/10 overflow-hidden rounded-lg border border-white/10">
                              {pipelineArtifacts.slice(0, 5).map((artifact) => (
                                <div
                                  className="grid gap-2 bg-ink-950/30 p-3 text-sm sm:grid-cols-[minmax(0,1fr)_auto]"
                                  key={artifact.artifact_id}
                                >
                                  <div className="min-w-0">
                                    <p className="truncate font-semibold text-white">
                                      {artifact.title}
                                    </p>
                                    <p className="mt-1 text-xs text-slate-400">
                                      {artifact.artifact_id}
                                    </p>
                                  </div>
                                  <p className="text-xs font-semibold text-hire-100">
                                    {artifact.chunk_count} 个预处理结构块
                                  </p>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                ) : null}
              </section>

              <div className="mt-6">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold text-white">文档清单</h3>
                  <button
                    className="text-xs font-semibold text-slate-300 transition hover:text-white"
                    onClick={() =>
                      void Promise.all([
                        loadDocuments(selectedKbId),
                        loadPendingDocumentDeletions(selectedKbId),
                      ])
                    }
                    type="button"
                  >
                    刷新文档
                  </button>
                </div>

                {pendingDeletionRetries.length > 0 ? (
                  <section
                    aria-label="待重试的文档清理"
                    className="mt-3 rounded-lg border border-amber-300/25 bg-amber-300/10 p-3"
                  >
                    <p className="text-xs font-semibold text-amber-100">
                      以下文档已从所有检索入口隔离，但本地派生物仍需重试清理：
                    </p>
                    <div className="mt-2 space-y-2">
                      {pendingDeletionRetries.map((document) => (
                        <div
                          className="flex flex-wrap items-center justify-between gap-2"
                          key={document.document_id}
                        >
                          <span className="text-xs text-amber-50">{document.filename}</span>
                          <button
                            className="rounded-full border border-amber-200/30 px-3 py-1 text-xs font-semibold text-amber-50 transition hover:bg-amber-200/10"
                            onClick={() => void retryDocumentCleanup(document)}
                            type="button"
                          >
                            重试彻底清理
                          </button>
                        </div>
                      ))}
                    </div>
                  </section>
                ) : null}

                <div className="mt-3 overflow-hidden rounded-lg border border-white/10">
                  {isLoadingDocs ? (
                    <div className="bg-white/[0.035] p-5 text-sm text-slate-400">
                      正在翻资料夹...
                    </div>
                  ) : documents.length === 0 ? (
                    <div className="bg-white/[0.035] p-6 text-sm leading-6 text-slate-400">
                      还没有文档。上传第一份资料后，面试间就能引用它回答问题。
                    </div>
                  ) : (
                    <div className="divide-y divide-white/10">
                      {documents.map((document) => {
                        const warningSummary = getRagOfficeWarningSummary(document);
                        return (
                          <article
                            className="grid gap-3 bg-white/[0.035] p-4 transition hover:bg-white/[0.055] md:grid-cols-[minmax(0,1fr)_auto]"
                            key={document.id}
                          >
                            <div>
                              <div className="flex flex-wrap items-center gap-2">
                                <h4 className="font-semibold text-white">{document.filename}</h4>
                                {activeDocumentChunkCounts.has(document.id) ? (
                                  <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-100">
                                    已加入当前索引
                                  </span>
                                ) : document.ingestion_status === "pipeline_required" ? (
                                  <span className="rounded-full border border-violet-300/25 bg-violet-300/10 px-2 py-0.5 text-[10px] font-semibold text-violet-100">
                                    等待流水线处理
                                  </span>
                                ) : null}
                              </div>
                              <p className="mt-1 text-xs text-slate-400">
                                {activeDocumentChunkCounts.has(document.id)
                                  ? `当前索引 ${activeDocumentChunkCounts.get(document.id)} 个片段`
                                  : `${document.chunk_count} 个片段`}{" "}
                                · {formatFileSize(document.size)} ·{" "}
                                {formatDate(document.created_at)}
                              </p>
                              {warningSummary.items.length > 0 ? (
                                <div
                                  className="mt-2 max-w-3xl rounded-md bg-amber-300/10 px-3 py-2 text-[11px] leading-5 text-amber-100"
                                  role="note"
                                >
                                  <p className="font-semibold">Office 解析提示</p>
                                  <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
                                    {warningSummary.items.map((warning) => (
                                      <li key={warning}>{warning}</li>
                                    ))}
                                  </ul>
                                  {warningSummary.hiddenCount > 0 ? (
                                    <p className="mt-0.5 text-amber-200/80">
                                      另有 {warningSummary.hiddenCount} 项提示未展开。
                                    </p>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>
                            <button
                              className="w-fit rounded-full border border-rose-300/20 bg-rose-300/10 px-3 py-1.5 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/20"
                              disabled={selectedKnowledgeBase.corpus_locked}
                              onClick={() => void deleteDocument(document)}
                              title={selectedKnowledgeBase.corpus_locked ? "标准 Benchmark 语料不可删除" : undefined}
                              type="button"
                            >
                              {selectedKnowledgeBase.corpus_locked ? "已锁定" : "删除"}
                            </button>
                          </article>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex min-h-[480px] flex-col items-center justify-center text-center">
              <div className="rounded-lg border border-hire-300/20 bg-hire-300/10 px-4 py-3 text-3xl">
                📚
              </div>
              <h2 className="mt-5 text-2xl font-semibold text-white">
                先搭一个资料库展台
              </h2>
              <p className="mt-3 max-w-md text-sm leading-6 text-slate-400">
                创建知识库后，你可以上传文档并在面试间选择它，让模型基于资料回答。
              </p>
              <button
                className="mt-6 rounded-full bg-hire-300 px-5 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
                onClick={() => setShowCreateModal(true)}
                type="button"
              >
                新建知识库
              </button>
            </div>
          )}
        </section>
      </div>

      {showCreateModal ? (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-ink-950/80 p-4 backdrop-blur">
          <div className="w-full max-w-md rounded-lg border border-white/10 bg-surface-900 p-5 shadow-prism">
            <h2 className="text-xl font-semibold text-white">新建知识库</h2>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              给这个资料库起个好记的名字，比如“产品手册”或“客户问答”。
            </p>
            <input
              autoFocus
              className="mt-5 w-full rounded-lg border border-white/10 bg-white/[0.06] px-4 py-3 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-hire-300/50 focus:ring-4 focus:ring-hire-300/10"
              onChange={(event) => setNewKbName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void createKnowledgeBase();
              }}
              placeholder="输入知识库名称"
              value={newKbName}
            />
            <div className="mt-5 flex justify-end gap-3">
              <button
                className="rounded-full border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06]"
                onClick={() => {
                  setShowCreateModal(false);
                  setNewKbName("");
                }}
                type="button"
              >
                取消
              </button>
              <button
                className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={!newKbName.trim() || isCreating}
                onClick={() => void createKnowledgeBase()}
                type="button"
              >
                {isCreating ? "创建中" : "创建"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </PageContainer>
  );
}
