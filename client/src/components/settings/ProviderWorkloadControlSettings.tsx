import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import {
  BadgeCheck,
  CircleAlert,
  LoaderCircle,
  RefreshCw,
  Route,
  ShieldCheck,
} from "lucide-react";

type EntryId =
  | "agent_shadow"
  | "meta_agent"
  | "workflow_interactive_llm"
  | "workflow_deployment_llm"
  | "workflow_interactive_agent"
  | "workflow_deployment_agent"
  | "xpert"
  | "xpert_app"
  | "expert_team_planner"
  | "expert_team_dag"
  | "fusion"
  | "route_agent"
  | "team_chat"
  | "rag_query_generate"
  | "rag_processor_generate"
  | "rag_embedding"
  | "rag_rerank"
  | "skill_rerank"
  | "openrouter_batch"
  | "chat_image"
  | "chat_document_native"
  | "rag_vision"
  | "workflow_interactive_vision"
  | "workflow_deployment_vision"
  | "xpert_vision"
  | "image_generation"
  | "multimodal_transcription"
  | "multimodal_speech"
  | "xpert_transcription"
  | "xpert_speech"
  | "chat_audio_input"
  | "chat_audio_output"
  | "audio_generation"
  | "multimodal_video_analysis"
  | "chat_video"
  | "video_generation"
  | "realtime_voice";
type ExecutionShape =
  | "chat_text"
  | "chat_tools"
  | "chat_text_unary"
  | "chat_json_object"
  | "fusion_native"
  | "embedding_vectors"
  | "rerank_documents"
  | "openrouter_batch_chat"
  | "openrouter_batch_embeddings"
  | "chat_image_stream"
  | "chat_document_stream"
  | "vision_json_unary"
  | "image_generation"
  | "audio_transcription"
  | "audio_speech"
  | "chat_audio_input"
  | "chat_audio_output"
  | "audio_generation_stream"
  | "video_analysis_unary"
  | "chat_video_stream"
  | "video_generation_async"
  | "realtime_voice_session";
type AdapterContract =
  | "openrouter_chat_multimodal_v1"
  | "openai_compatible_chat_multimodal_v1"
  | "openrouter_chat_native_pdf_v1"
  | "openrouter_images_v1"
  | "openai_compatible_images_generations_v1"
  | "openrouter_audio_transcription_json_v1"
  | "openai_compatible_audio_transcription_multipart_v1"
  | "openrouter_audio_speech_v1"
  | "openai_compatible_audio_speech_v1"
  | "openrouter_chat_audio_v1"
  | "openrouter_audio_generation_stream_v1"
  | "openrouter_chat_video_v1"
  | "openrouter_video_jobs_v1"
  | "openai_realtime_sdp_v1";
type PolicyStatus = "legacy" | "managed_required" | "degraded_required";
type LocalFallbackMode = "none" | "extractive" | "lexical";
type RerankAccessMode = "dedicated" | "llm_json";

interface ConnectionSummary {
  id: string;
  name: string;
  kind: string;
  scopes?: string[];
  enabled: boolean;
}

interface CertificationSummary {
  certification_id?: string | null;
  connection_id: string;
  connection_name: string;
  provider_kind: string;
  execution_shape: ExecutionShape;
  status: string;
  can_run: boolean;
  blocked_reason?: string | null;
  error_code?: string | null;
  requested_model?: string | null;
  actual_model?: string | null;
  candidate_model_ids: string[];
  judge_model_id?: string | null;
  e2e_ms?: number | null;
  total_tokens?: number | null;
  completed_at?: string | null;
  rerank_access_mode?: RerankAccessMode | null;
  vector_dimension?: number | null;
  batch_job_id?: string | null;
  batch_status?: string | null;
  adapter_contract?: AdapterContract | null;
  protocol_version?: string | null;
  certified_input_formats?: string[];
  certified_voice?: string | null;
  certified_response_format?: "mp3" | "wav" | null;
  certified_output_format?: "mp3" | null;
  supports_image_prompt?: boolean | null;
  provider_dispatch_state?: string | null;
  retry_allowed?: boolean | null;
  refresh_available?: boolean;
}

interface BindingSummary {
  execution_shape: ExecutionShape;
  model_id: string;
  connection_id: string;
  connection_name: string;
  provider_kind: string;
  certification_id: string;
  rerank_access_mode?: RerankAccessMode | null;
  adapter_contract?: AdapterContract | null;
  protocol_version?: string | null;
  valid: boolean;
  reason_code: string;
}

interface PolicySummary {
  entry_id: EntryId;
  feature_enabled: boolean;
  data_plane_integrated: boolean;
  configured_status: PolicyStatus;
  effective_status: PolicyStatus;
  revision: number;
  policy_fingerprint: string;
  local_fallback_mode: LocalFallbackMode;
  bindings: BindingSummary[];
  approval_valid: boolean;
  blocking_reason_codes: string[];
}

interface ReceiptCall {
  call_id: string;
  execution_shape: ExecutionShape;
  model_id: string;
  actual_model?: string | null;
  connection_id?: string | null;
  call_sequence: number;
  dispatched: boolean;
  status: string;
  result_class?: string | null;
  error_code?: string | null;
  ttft_ms?: number | null;
  e2e_ms?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  adapter_contract?: AdapterContract | null;
  protocol_version?: string | null;
  provider_dispatch_state?: string | null;
}

interface ReceiptRun {
  run_id: string;
  entry_id: EntryId;
  status: string;
  parent_run_reference?: string | null;
  result_class?: string | null;
  reason_codes?: string[];
  created_at: string;
  batch_job_id?: string | null;
  batch_status?: string | null;
  batch_request_count?: number | null;
  batch_completed_count?: number | null;
  batch_failed_count?: number | null;
  billing_authoritative?: false | null;
  calls: ReceiptCall[];
}

interface EditableBinding {
  execution_shape: ExecutionShape;
  model_id: string;
  connection_id: string;
  rerank_access_mode?: RerankAccessMode | null;
  adapter_contract?: AdapterContract | null;
}

const ENTRY_LABELS: Record<EntryId, string> = {
  agent_shadow: "Engine Shadow",
  meta_agent: "Meta Agent",
  workflow_interactive_llm: "Workflow 交互 LLM",
  workflow_deployment_llm: "Workflow 部署 LLM",
  workflow_interactive_agent: "Workflow 交互 Agent",
  workflow_deployment_agent: "Workflow 部署 Agent",
  xpert: "Published Xpert",
  xpert_app: "Xpert App",
  expert_team_planner: "Expert Team Planner",
  expert_team_dag: "Expert Team DAG",
  fusion: "Fusion",
  route_agent: "Route Agent",
  team_chat: "Team Chat",
  rag_query_generate: "RAG 问答生成",
  rag_processor_generate: "RAG AI Processor",
  rag_embedding: "RAG Embedding",
  rag_rerank: "RAG Rerank",
  skill_rerank: "Skill Rerank",
  openrouter_batch: "OpenRouter Batch",
  chat_image: "Chat 图片理解",
  chat_document_native: "Chat 原生 PDF",
  rag_vision: "RAG Vision",
  workflow_interactive_vision: "Workflow 交互 Vision",
  workflow_deployment_vision: "Workflow 部署 Vision",
  xpert_vision: "Xpert Vision",
  image_generation: "图片生成",
  multimodal_transcription: "独立 STT",
  multimodal_speech: "独立 TTS",
  xpert_transcription: "Xpert STT",
  xpert_speech: "Xpert TTS",
  chat_audio_input: "Chat Audio Input",
  chat_audio_output: "Chat Audio Output",
  audio_generation: "音频生成",
  multimodal_video_analysis: "独立视频分析",
  chat_video: "Chat 视频",
  video_generation: "视频生成",
  realtime_voice: "Realtime Voice",
};

const SHAPE_LABELS: Record<ExecutionShape, string> = {
  chat_text: "流式文本（复用 R5）",
  chat_tools: "流式工具调用（复用 R5）",
  chat_text_unary: "非流式文本",
  chat_json_object: "JSON Object",
  fusion_native: "OpenRouter 原生 Fusion",
  embedding_vectors: "Embedding 向量",
  rerank_documents: "Rerank 文档",
  openrouter_batch_chat: "OpenRouter Chat Batch",
  openrouter_batch_embeddings: "OpenRouter Embedding Batch",
  chat_image_stream: "图片 Chat 流",
  chat_document_stream: "原生 PDF Chat 流",
  vision_json_unary: "Vision 严格 JSON",
  image_generation: "图片生成",
  audio_transcription: "音频转录",
  audio_speech: "语音合成",
  chat_audio_input: "Chat 音频输入",
  chat_audio_output: "Chat 音频输出",
  audio_generation_stream: "音频生成流",
  video_analysis_unary: "视频分析",
  chat_video_stream: "视频 Chat 流",
  video_generation_async: "异步视频生成",
  realtime_voice_session: "Realtime SDP 会话",
};

const MULTIMODAL_SHAPES = new Set<ExecutionShape>([
  "chat_image_stream", "chat_document_stream", "vision_json_unary",
  "image_generation", "audio_transcription", "audio_speech",
  "chat_audio_input", "chat_audio_output", "audio_generation_stream",
  "video_analysis_unary", "chat_video_stream", "video_generation_async",
  "realtime_voice_session",
]);

const ACTIVE_MULTIMODAL_CERTIFICATION_SHAPES = new Set<ExecutionShape>([
  "chat_image_stream",
  "chat_document_stream",
  "vision_json_unary",
  "image_generation",
  "audio_transcription",
  "audio_speech",
  "chat_audio_input",
  "chat_audio_output",
  "audio_generation_stream",
]);

const REFRESHABLE_MULTIMODAL_CERTIFICATION_SHAPES = new Set<ExecutionShape>([
  "audio_transcription",
  "audio_speech",
]);

const ADAPTER_OPTIONS: Record<AdapterContract, {
  label: string;
  shapes: ExecutionShape[];
  kinds: string[];
  scopes: string[];
}> = {
  openrouter_chat_multimodal_v1: { label: "OpenRouter Chat Multimodal", shapes: ["chat_image_stream", "vision_json_unary"], kinds: ["openrouter"], scopes: ["chat", "image"] },
  openai_compatible_chat_multimodal_v1: { label: "OpenAI-compatible Chat Multimodal", shapes: ["chat_image_stream", "vision_json_unary"], kinds: ["newapi", "openai_compatible", "openai"], scopes: ["chat", "image"] },
  openrouter_chat_native_pdf_v1: { label: "OpenRouter Native PDF", shapes: ["chat_document_stream"], kinds: ["openrouter"], scopes: ["chat", "document"] },
  openrouter_images_v1: { label: "OpenRouter Images", shapes: ["image_generation"], kinds: ["openrouter"], scopes: ["image"] },
  openai_compatible_images_generations_v1: { label: "OpenAI-compatible Images", shapes: ["image_generation"], kinds: ["newapi", "openai_compatible", "openai"], scopes: ["image"] },
  openrouter_audio_transcription_json_v1: { label: "OpenRouter STT JSON", shapes: ["audio_transcription"], kinds: ["openrouter"], scopes: ["audio"] },
  openai_compatible_audio_transcription_multipart_v1: { label: "OpenAI-compatible STT Multipart", shapes: ["audio_transcription"], kinds: ["newapi", "openai_compatible", "openai"], scopes: ["audio"] },
  openrouter_audio_speech_v1: { label: "OpenRouter TTS", shapes: ["audio_speech"], kinds: ["openrouter"], scopes: ["audio"] },
  openai_compatible_audio_speech_v1: { label: "OpenAI-compatible TTS", shapes: ["audio_speech"], kinds: ["newapi", "openai_compatible", "openai"], scopes: ["audio"] },
  openrouter_chat_audio_v1: { label: "OpenRouter Chat Audio", shapes: ["chat_audio_input", "chat_audio_output"], kinds: ["openrouter"], scopes: ["chat", "audio"] },
  openrouter_audio_generation_stream_v1: { label: "OpenRouter Audio Generation", shapes: ["audio_generation_stream"], kinds: ["openrouter"], scopes: ["audio"] },
  openrouter_chat_video_v1: { label: "OpenRouter Chat Video", shapes: ["video_analysis_unary", "chat_video_stream"], kinds: ["openrouter"], scopes: ["chat", "video"] },
  openrouter_video_jobs_v1: { label: "OpenRouter Video Jobs", shapes: ["video_generation_async"], kinds: ["openrouter"], scopes: ["video"] },
  openai_realtime_sdp_v1: { label: "OpenAI Realtime SDP", shapes: ["realtime_voice_session"], kinds: ["openai"], scopes: ["realtime"] },
};

const adaptersForShape = (shape: ExecutionShape) =>
  (Object.entries(ADAPTER_OPTIONS) as [AdapterContract, (typeof ADAPTER_OPTIONS)[AdapterContract]][])
    .filter(([, spec]) => spec.shapes.includes(shape));

const connectionSupports = (
  connection: ConnectionSummary,
  shape: ExecutionShape,
  adapter?: AdapterContract | null,
) => {
  if (shape.startsWith("openrouter_batch_")) return connection.kind === "openrouter" && (connection.scopes ?? []).includes("batch");
  if (!MULTIMODAL_SHAPES.has(shape)) return (connection.scopes ?? []).includes(requiredScope(shape));
  if (!adapter) return false;
  const spec = ADAPTER_OPTIONS[adapter];
  return spec.shapes.includes(shape)
    && spec.kinds.includes(connection.kind)
    && spec.scopes.every((scope) => (connection.scopes ?? []).includes(scope));
};

const NEW_CERTIFICATION_SHAPES: ExecutionShape[] = [
  "chat_text_unary",
  "chat_json_object",
  "fusion_native",
  "embedding_vectors",
  "rerank_documents",
  "openrouter_batch_chat",
  "openrouter_batch_embeddings",
  "chat_image_stream",
  "chat_document_stream",
  "vision_json_unary",
  "image_generation",
  "audio_transcription",
  "audio_speech",
  "chat_audio_input",
  "chat_audio_output",
  "audio_generation_stream",
  "video_analysis_unary",
  "chat_video_stream",
  "video_generation_async",
  "realtime_voice_session",
];

export const ENTRY_SHAPES: Record<EntryId, ExecutionShape[]> = {
  agent_shadow: ["chat_tools"],
  meta_agent: ["chat_json_object"],
  workflow_interactive_llm: ["chat_text", "chat_text_unary", "chat_json_object"],
  workflow_deployment_llm: ["chat_text", "chat_text_unary", "chat_json_object"],
  workflow_interactive_agent: ["chat_text", "chat_tools", "chat_json_object"],
  workflow_deployment_agent: ["chat_text", "chat_tools", "chat_json_object"],
  xpert: ["chat_text", "chat_tools", "chat_json_object"],
  xpert_app: ["chat_text", "chat_tools", "chat_json_object"],
  expert_team_planner: ["chat_text_unary"],
  expert_team_dag: ["chat_text_unary", "chat_json_object"],
  fusion: ["chat_text", "fusion_native"],
  route_agent: ["chat_text"],
  team_chat: ["chat_text"],
  rag_query_generate: ["chat_text_unary"],
  rag_processor_generate: ["chat_json_object"],
  rag_embedding: ["embedding_vectors"],
  rag_rerank: ["rerank_documents"],
  skill_rerank: ["rerank_documents"],
  openrouter_batch: ["openrouter_batch_chat", "openrouter_batch_embeddings"],
  chat_image: ["chat_image_stream"],
  chat_document_native: ["chat_document_stream"],
  rag_vision: ["vision_json_unary"],
  workflow_interactive_vision: ["vision_json_unary"],
  workflow_deployment_vision: ["vision_json_unary"],
  xpert_vision: ["vision_json_unary"],
  image_generation: ["image_generation"],
  multimodal_transcription: ["audio_transcription"],
  multimodal_speech: ["audio_speech"],
  xpert_transcription: ["audio_transcription"],
  xpert_speech: ["audio_speech"],
  chat_audio_input: ["chat_audio_input"],
  chat_audio_output: ["chat_audio_output"],
  audio_generation: ["audio_generation_stream"],
  multimodal_video_analysis: ["video_analysis_unary"],
  chat_video: ["chat_video_stream"],
  video_generation: ["video_generation_async"],
  realtime_voice: ["realtime_voice_session"],
};

const FALLBACK_OPTIONS: Record<EntryId, LocalFallbackMode[]> = Object.fromEntries(
  (Object.keys(ENTRY_LABELS) as EntryId[]).map((entry) => [entry, ["none"]]),
) as Record<EntryId, LocalFallbackMode[]>;
FALLBACK_OPTIONS.rag_query_generate = ["none", "extractive"];
FALLBACK_OPTIONS.rag_rerank = ["none", "lexical"];
FALLBACK_OPTIONS.skill_rerank = ["none", "lexical"];

const FALLBACK_LABELS: Record<LocalFallbackMode, string> = {
  none: "不使用本地降级",
  extractive: "本地抽取式答案（非模型）",
  lexical: "保留词法顺序（非模型）",
};

function requiredScope(shape: ExecutionShape) {
  if (shape === "embedding_vectors") return "embedding";
  if (shape === "rerank_documents") return "rerank";
  if (shape.startsWith("openrouter_batch_")) return "batch";
  if (shape === "chat_document_stream") return "document";
  if (["chat_image_stream", "vision_json_unary", "image_generation"].includes(shape)) return "image";
  if (["audio_transcription", "audio_speech", "chat_audio_input", "chat_audio_output", "audio_generation_stream"].includes(shape)) return "audio";
  if (["video_analysis_unary", "chat_video_stream", "video_generation_async"].includes(shape)) return "video";
  if (shape === "realtime_voice_session") return "realtime";
  return "chat";
}

const REASON_LABELS: Record<string, string> = {
  provider_workload_bindings_required: "尚未配置精确模型 Binding",
  provider_workload_data_plane_not_integrated: "该入口的数据面尚未在当前子轮次接入",
  provider_workload_feature_disabled: "部署 Feature Flag 当前关闭",
  provider_workload_approval_missing: "人工 fail-closed 批准已失效或尚未记录",
  provider_workload_certification_required: "缺少对应执行形态的真实 Provider 资格",
  provider_workload_certification_not_passed: "最新执行形态资格未通过",
  provider_workload_catalog_refresh_truncated: "最新目录已截断",
  provider_workload_newer_certification_requires_policy_update: "存在更新资格，需要重新保存 Binding",
  provider_workload_connection_missing: "Binding 指向的 Managed 连接已不存在",
  provider_workload_credential_unavailable: "Provider 凭据当前无法解密",
  provider_workload_certification_expired: "执行形态资格已过期，需要重新认证",
  provider_connection_not_online: "Managed 连接当前不在线",
  provider_multimodal_adapter_required: "多模态 Binding 必须显式选择 Adapter",
  provider_multimodal_adapter_shape_mismatch: "Adapter 与执行形态不匹配",
  provider_multimodal_adapter_provider_mismatch: "Provider 类型不支持该 Adapter",
  provider_multimodal_protocol_stale: "多模态协议版本已变化，需要重新认证",
  qualified: "资格有效",
};

async function readError(response: Response) {
  if (response.status === 401) return "管理会话已失效，请重新配对。";
  if (response.status === 403) return "CSRF 校验失败，请刷新页面后重试。";
  try {
    const payload = await response.json();
    if (typeof payload?.detail?.message === "string") return payload.detail.message;
    if (typeof payload?.detail === "string") return payload.detail;
  } catch {
    // Use the stable fallback without exposing an upstream body.
  }
  return "Workload 控制面操作未完成。";
}

function statusLabel(status: PolicyStatus) {
  if (status === "managed_required") return "Managed 必经";
  if (status === "degraded_required") return "Managed 降级阻断";
  return "Legacy";
}

function randomIdempotencyKey() {
  return `workload-cert-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function ProviderWorkloadControlSettings({
  csrfToken,
  view,
}: {
  csrfToken: string;
  view: "certifications" | "routing";
}) {
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [certifications, setCertifications] = useState<CertificationSummary[]>([]);
  const [policies, setPolicies] = useState<PolicySummary[]>([]);
  const [receipts, setReceipts] = useState<ReceiptRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [messageTone, setMessageTone] = useState<"success" | "warning">("success");
  const [error, setError] = useState("");

  const [connectionId, setConnectionId] = useState("");
  const [certificationShape, setCertificationShape] = useState<ExecutionShape>(
    "chat_text_unary",
  );
  const [certificationModel, setCertificationModel] = useState("");
  const [candidateModels, setCandidateModels] = useState("");
  const [judgeModel, setJudgeModel] = useState("");
  const [rerankAccessMode, setRerankAccessMode] = useState<RerankAccessMode>(
    "dedicated",
  );
  const [certificationAdapter, setCertificationAdapter] =
    useState<AdapterContract | null>(null);
  const [confirmCertification, setConfirmCertification] = useState(false);

  const [entryId, setEntryId] = useState<EntryId>("agent_shadow");
  const [editableBindings, setEditableBindings] = useState<EditableBinding[]>([]);
  const [localFallbackMode, setLocalFallbackMode] =
    useState<LocalFallbackMode>("none");
  const [confirmActivation, setConfirmActivation] = useState(false);
  const [confirmNoOpenP0P1, setConfirmNoOpenP0P1] = useState(false);
  const [acknowledgeFailClosed, setAcknowledgeFailClosed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (view === "certifications") {
        const [connectionResponse, certificationResponse] = await Promise.all([
          fetch("/api/router/connections"),
          fetch("/api/router/certifications/workloads"),
        ]);
        for (const response of [connectionResponse, certificationResponse]) {
          if (!response.ok) throw new Error(await readError(response));
        }
        const nextConnections = (await connectionResponse.json()) as ConnectionSummary[];
        const certificationPayload = (await certificationResponse.json()) as {
          certifications: CertificationSummary[];
        };
        setConnections(nextConnections);
        setCertifications(certificationPayload.certifications);
        setConnectionId((current) =>
          current || nextConnections.find(
            (item) => item.enabled && (item.scopes ?? []).includes("chat"),
          )?.id || "",
        );
      } else {
        const [policyResponse, connectionResponse, receiptResponse] = await Promise.all([
          fetch("/api/router/workload-control/policies"),
          fetch("/api/router/connections"),
          fetch("/api/router/workload-control/receipts?limit=20"),
        ]);
        for (const response of [policyResponse, connectionResponse, receiptResponse]) {
          if (!response.ok) throw new Error(await readError(response));
        }
        const policyPayload = (await policyResponse.json()) as {
          policies: PolicySummary[];
        };
        const receiptPayload = (await receiptResponse.json()) as {
          runs: ReceiptRun[];
        };
        setPolicies(policyPayload.policies);
        setConnections((await connectionResponse.json()) as ConnectionSummary[]);
        setReceipts(receiptPayload.runs);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Workload 控制面。")
    } finally {
      setLoading(false);
    }
  }, [view]);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedPolicy = useMemo(
    () => policies.find((policy) => policy.entry_id === entryId) ?? null,
    [entryId, policies],
  );

  useLayoutEffect(() => {
    if (!selectedPolicy) return;
    setConfirmActivation(false);
    setConfirmNoOpenP0P1(false);
    setAcknowledgeFailClosed(false);
    setLocalFallbackMode(selectedPolicy.local_fallback_mode ?? "none");
    setEditableBindings(
      selectedPolicy.bindings.map((binding) => ({
        execution_shape: binding.execution_shape,
        model_id: binding.model_id,
        connection_id: binding.connection_id,
        rerank_access_mode: binding.rerank_access_mode ?? null,
        adapter_contract: binding.adapter_contract ?? null,
      })),
    );
  }, [selectedPolicy]);

  const eligibleConnections = useMemo(
    () => connections.filter((connection) => connection.enabled),
    [connections],
  );
  const certificationEligibleConnections = useMemo(
    () => eligibleConnections.filter((connection) =>
      connectionSupports(connection, certificationShape, certificationAdapter),
    ),
    [certificationAdapter, certificationShape, eligibleConnections],
  );

  useEffect(() => {
    const options = adaptersForShape(certificationShape);
    if (!MULTIMODAL_SHAPES.has(certificationShape)) {
      setCertificationAdapter(null);
      return;
    }
    if (options.some(([contract]) => contract === certificationAdapter)) return;
    setCertificationAdapter(options[0]?.[0] ?? null);
  }, [certificationAdapter, certificationShape]);

  useEffect(() => {
    if (certificationEligibleConnections.some((item) => item.id === connectionId)) {
      return;
    }
    setConnectionId(certificationEligibleConnections[0]?.id ?? "");
  }, [certificationEligibleConnections, connectionId]);

  const runCertification = useCallback(async () => {
    if (!connectionId || !certificationModel.trim()) return;
    setBusy(true);
    setError("");
    setMessage("");
    setMessageTone("success");
    try {
      const response = await fetch(
        `/api/router/connections/${encodeURIComponent(connectionId)}/certifications/workloads`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": randomIdempotencyKey(),
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            execution_shape: certificationShape,
            model_id: certificationModel.trim(),
            acknowledge_billed_call: true,
            candidate_model_ids:
              certificationShape === "fusion_native"
                ? candidateModels.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
                : [],
            judge_model_id:
              certificationShape === "fusion_native" ? judgeModel.trim() : null,
            rerank_access_mode:
              certificationShape === "rerank_documents" ? rerankAccessMode : null,
            adapter_contract: certificationAdapter,
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as CertificationSummary;
      setMessage(
        result.status === "passed"
          ? "执行形态资格已通过；它不代表任何 Agent 或 Workflow 入口已经启用。"
          : `资格未通过：${result.error_code ?? result.status}`,
      );
      setConfirmCertification(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "资格认证失败。")
    } finally {
      setBusy(false);
    }
  }, [
    candidateModels,
    certificationModel,
    certificationShape,
    certificationAdapter,
    connectionId,
    csrfToken,
    judgeModel,
    load,
    rerankAccessMode,
  ]);

  const refreshCertificationEvidence = useCallback(async (certificationId: string) => {
    setBusy(true);
    setError("");
    setMessage("");
    setMessageTone("success");
    try {
      const response = await fetch(
        `/api/router/certifications/workloads/${encodeURIComponent(certificationId)}/refresh`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({ acknowledge_poll_only: true }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const result = (await response.json()) as CertificationSummary;
      await load();
      if (result.status === "passed") {
        setMessageTone("success");
        setMessage("实际模型证据已确认；本次只执行了上游元数据 GET。");
      } else if (result.status === "failed") {
        setError(
          `实际模型证据核验失败：${result.error_code ?? "provider_workload_certification_failed"}`,
        );
      } else {
        setMessageTone("warning");
        setMessage(`实际模型证据仍待确认：${result.error_code ?? result.status}`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "只读刷新模型证据失败。");
    } finally {
      setBusy(false);
    }
  }, [csrfToken, load]);

  const addBinding = () => {
    const shape = ENTRY_SHAPES[entryId][0];
    const adapter = adaptersForShape(shape)[0]?.[0] ?? null;
    const firstConnection = eligibleConnections.find((connection) =>
      connectionSupports(connection, shape, adapter),
    )?.id ?? "";
    if (!firstConnection || !shape) return;
    setEditableBindings((current) => [
      ...current,
      {
        execution_shape: shape,
        model_id: "",
        connection_id: firstConnection,
        rerank_access_mode: shape === "rerank_documents" ? "dedicated" : null,
        adapter_contract: adapter,
      },
    ]);
  };

  const savePolicy = useCallback(async () => {
    if (!selectedPolicy) return;
    setBusy(true);
    setError("");
    setMessage("");
    setMessageTone("success");
    try {
      const response = await fetch(
        `/api/router/workload-control/policies/${entryId}`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            expected_revision: selectedPolicy.revision,
            local_fallback_mode: localFallbackMode,
            bindings: editableBindings.map((binding) => ({
              execution_shape: binding.execution_shape,
              model_id: binding.model_id.trim(),
              connection_id: binding.connection_id,
              ...(binding.execution_shape === "rerank_documents"
                ? { rerank_access_mode: binding.rerank_access_mode ?? "dedicated" }
                : {}),
              ...(binding.adapter_contract
                ? { adapter_contract: binding.adapter_contract }
                : {}),
            })),
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      setMessage(
        "入口 Binding 已原子保存；只有已接入数据面的入口经过人工确认后才能激活。",
      );
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存入口策略失败。")
    } finally {
      setBusy(false);
    }
  }, [csrfToken, editableBindings, entryId, load, localFallbackMode, selectedPolicy]);

  const activate = useCallback(async () => {
    if (!selectedPolicy || !confirmNoOpenP0P1 || !acknowledgeFailClosed) return;
    setBusy(true);
    setError("");
    setMessage("");
    setMessageTone("success");
    try {
      const response = await fetch(
        `/api/router/workload-control/policies/${entryId}/activate`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({
            expected_revision: selectedPolicy.revision,
            no_open_p0_p1: true,
            acknowledge_fail_closed: true,
          }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      setMessage("该入口已激活 Managed 必经；资格漂移后将保持失败关闭。")
      setConfirmActivation(false);
      setConfirmNoOpenP0P1(false);
      setAcknowledgeFailClosed(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "激活入口失败。")
    } finally {
      setBusy(false);
    }
  }, [
    acknowledgeFailClosed,
    confirmNoOpenP0P1,
    csrfToken,
    entryId,
    load,
    selectedPolicy,
  ]);

  const deactivate = useCallback(async () => {
    if (!selectedPolicy) return;
    setBusy(true);
    setError("");
    setMessage("");
    setMessageTone("success");
    try {
      const response = await fetch(
        `/api/router/workload-control/policies/${entryId}/deactivate`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-ModelMirror-CSRF": csrfToken,
          },
          body: JSON.stringify({ expected_revision: selectedPolicy.revision }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      setMessage("该入口已显式恢复 legacy；资格与脱敏 Receipt 仍保留。")
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "停用入口失败。")
    } finally {
      setBusy(false);
    }
  }, [csrfToken, entryId, load, selectedPolicy]);

  if (loading) {
    return (
      <section className="mb-6 rounded-lg border border-white/10 bg-ink-950/82 p-5 text-sm text-slate-300">
        <span className="inline-flex items-center gap-2">
          <LoaderCircle className="h-4 w-4 animate-spin" />正在读取 Workload 控制面…
        </span>
      </section>
    );
  }

  if (view === "certifications") {
    const fusionSelected = certificationShape === "fusion_native";
    const multimodalSelected = MULTIMODAL_SHAPES.has(certificationShape);
    const multimodalFoundationOnly = multimodalSelected
      && !ACTIVE_MULTIMODAL_CERTIFICATION_SHAPES.has(certificationShape);
    const canConfirm = Boolean(
      connectionId &&
      certificationModel.trim() &&
      (!fusionSelected || (candidateModels.trim() && judgeModel.trim())) &&
      (!multimodalSelected || certificationAdapter) &&
      !multimodalFoundationOnly,
    );
    const certificationInputDisclosure = certificationShape === "audio_transcription"
      ? "将上传仓库内固定、无敏感信息的合成 WAV；不会发送真实用户的会话、文件或工具参数。"
      : "将发送仓库内固定、无敏感信息的合成素材（部分形态包含固定合成媒体）；不会发送真实用户的内容、文件或工具参数。";
    return (
      <section className="mb-6 overflow-hidden rounded-lg border border-violet-300/15 bg-ink-950/82 shadow-prism">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 bg-violet-300/[0.04] px-5 py-5">
          <div>
            <p className="text-sm font-semibold text-violet-100">Managed Workload 资格</p>
            <h2 className="mt-2 text-xl font-semibold text-white">R6 / R7 资格与 R8 多模态认证</h2>
            <p className="mt-2 max-w-[78ch] text-sm leading-6 text-slate-300">
              仅发送固定合成输入，自动刷新完整目录，并对精确连接、模型和执行形态留存脱敏资格。Embedding、Rerank 与 Batch 不会因此自动接管数据面。
            </p>
          </div>
          <button className="inline-flex items-center gap-2 rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200" onClick={() => void load()} type="button">
            <RefreshCw className="h-3.5 w-3.5" />刷新
          </button>
        </div>
        <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="space-y-4 rounded-lg border border-white/10 bg-white/[0.025] p-4">
            <label className="block text-sm text-slate-300">
              Managed 连接
              <select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>
                <option value="">选择连接</option>
                {certificationEligibleConnections.map((connection) => <option key={connection.id} value={connection.id}>{connection.name} · {connection.kind}</option>)}
              </select>
            </label>
            <label className="block text-sm text-slate-300">
              执行形态
              <select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" value={certificationShape} onChange={(event) => setCertificationShape(event.target.value as ExecutionShape)}>
                {NEW_CERTIFICATION_SHAPES.map((shape) => <option key={shape} value={shape}>{SHAPE_LABELS[shape]}</option>)}
              </select>
            </label>
            <label className="block text-sm text-slate-300">
              精确模型 ID
              <input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setCertificationModel(event.target.value)} placeholder={fusionSelected ? "openrouter/fusion" : "provider/model"} value={certificationModel} />
            </label>
            {multimodalSelected ? <label className="block text-sm text-slate-300">
              Adapter Contract
              <select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setCertificationAdapter(event.target.value as AdapterContract)} value={certificationAdapter ?? ""}>
                {adaptersForShape(certificationShape).map(([contract, spec]) => <option key={contract} value={contract}>{spec.label}</option>)}
              </select>
            </label> : null}
            {fusionSelected ? <>
              <label className="block text-sm text-slate-300">有序候选模型（每行一个）<textarea className="mt-2 min-h-24 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 font-mono text-xs text-white" onChange={(event) => setCandidateModels(event.target.value)} value={candidateModels} /></label>
              <label className="block text-sm text-slate-300">裁判模型 ID<input className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setJudgeModel(event.target.value)} value={judgeModel} /></label>
            </> : null}
            {certificationShape === "rerank_documents" ? <label className="block text-sm text-slate-300">
              Rerank 访问方式
              <select aria-label="Rerank 访问方式" className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" onChange={(event) => setRerankAccessMode(event.target.value as RerankAccessMode)} value={rerankAccessMode}>
                <option value="dedicated">Dedicated Rerank API</option>
                <option value="llm_json">LLM JSON Adapter</option>
              </select>
            </label> : null}
            {multimodalFoundationOnly ? <p className="rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-3 text-xs leading-5 text-amber-100">该多模态形态目前仅建立 Adapter、Binding 和状态基础，不会发送付费认证；对应数据面批次接入后才开放此按钮。</p> : null}
            <button className="inline-flex items-center gap-2 rounded-full bg-violet-200 px-4 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40" disabled={!canConfirm || busy} onClick={() => setConfirmCertification(true)} type="button">
              <ShieldCheck className="h-4 w-4" />运行资格认证
            </button>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">最近资格</h3>
            <div className="mt-3 space-y-2">
              {certifications.length ? certifications.map((item) => <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3" key={item.certification_id ?? `${item.connection_id}-${item.execution_shape}`}>
                <div className="flex flex-wrap items-center justify-between gap-2"><p className="text-sm font-semibold text-white">{item.connection_name} · {SHAPE_LABELS[item.execution_shape]}</p><span className={`rounded-full border px-2.5 py-1 text-xs ${item.status === "passed" ? "border-emerald-300/25 text-emerald-200" : item.status === "failed" ? "border-rose-300/25 text-rose-100" : "border-amber-300/25 text-amber-100"}`}>{item.status}</span></div>
                <p className="mt-2 break-all font-mono text-xs text-slate-300">{item.requested_model ?? "尚未运行"}</p>
                <p className="mt-1 text-xs text-slate-400">{item.error_code ?? (item.total_tokens != null ? `${item.total_tokens} tokens` : "不保存合成输入或模型正文")}</p>
                {item.rerank_access_mode ? <p className="mt-1 text-xs text-slate-500">访问方式：{item.rerank_access_mode}</p> : null}
                {item.vector_dimension != null ? <p className="mt-1 text-xs text-slate-500">向量维度：{item.vector_dimension}</p> : null}
                {item.batch_status ? <p className="mt-1 text-xs text-slate-500">Batch：{item.batch_status} · {item.batch_job_id}</p> : null}
                {item.certified_input_formats?.length ? <p className="mt-1 text-xs text-slate-500">认证输入格式：{item.certified_input_formats.map((format) => format.toUpperCase()).join("、")}</p> : null}
                {item.certified_voice ? <p className="mt-1 text-xs text-slate-500">认证声线：{item.certified_voice}</p> : null}
                {item.certified_response_format ? <p className="mt-1 text-xs text-slate-500">认证外部输出格式：{item.certified_response_format.toUpperCase()}</p> : null}
                {item.certified_output_format ? <p className="mt-1 text-xs text-slate-500">认证生成格式：{item.certified_output_format.toUpperCase()}</p> : null}
                {item.supports_image_prompt != null ? <p className="mt-1 text-xs text-slate-500">图片提示：{item.supports_image_prompt ? "已认证" : "不支持"}</p> : null}
                {item.adapter_contract ? <p className="mt-1 break-all text-xs text-slate-500">Adapter：{item.adapter_contract} · {item.protocol_version ?? "协议待确认"}</p> : null}
                {item.refresh_available && item.certification_id && REFRESHABLE_MULTIMODAL_CERTIFICATION_SHAPES.has(item.execution_shape) ? <div className="mt-3 rounded-lg border border-sky-300/15 bg-sky-300/[0.04] p-3">
                  <p className="text-xs leading-5 text-sky-100">仅查询已保存 Generation ID 的实际模型证据；不会重新提交音频或产生第二次模型 POST。</p>
                  <button className="mt-2 inline-flex items-center gap-2 rounded-full border border-sky-200/25 px-3 py-1.5 text-xs font-semibold text-sky-100 disabled:cursor-not-allowed disabled:opacity-40" disabled={busy} onClick={() => void refreshCertificationEvidence(item.certification_id!)} type="button"><RefreshCw className="h-3.5 w-3.5" />只读刷新模型证据</button>
                </div> : null}
              </div>) : <p className="rounded-lg border border-dashed border-white/10 p-4 text-sm text-slate-400">尚无 Workload 资格记录。</p>}
            </div>
          </div>
        </div>
        {confirmCertification ? <div aria-modal="true" className="border-t border-amber-300/20 bg-amber-300/[0.05] p-5" role="dialog">
          <p className="text-sm font-semibold text-amber-100">确认一次真实付费资格调用</p>
          <p className="mt-2 text-sm leading-6 text-slate-300">{certificationInputDisclosure}{certificationShape.startsWith("openrouter_batch_") ? "最多提交一个异步 Batch；后续只读轮询不会重放提交" : "最多一个 Provider POST、零自动重试"}，可能产生少量费用。</p>
          <div className="mt-3 flex gap-2"><button className="rounded-full bg-amber-200 px-4 py-2 text-sm font-semibold text-ink-950" disabled={busy} onClick={() => void runCertification()} type="button">确认并运行</button><button className="rounded-full border border-white/15 px-4 py-2 text-sm text-slate-200" onClick={() => setConfirmCertification(false)} type="button">取消</button></div>
        </div> : null}
        {error ? <p className="m-5 flex items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-300/10 p-3 text-sm text-rose-100" role="alert"><CircleAlert className="h-4 w-4" />{error}</p> : null}
        {message ? <p className={`m-5 flex items-center gap-2 rounded-lg border p-3 text-sm ${messageTone === "success" ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100" : "border-amber-300/20 bg-amber-300/10 text-amber-100"}`} data-tone={messageTone} role="status">{messageTone === "success" ? <BadgeCheck className="h-4 w-4" /> : <CircleAlert className="h-4 w-4" />}{message}</p> : null}
      </section>
    );
  }

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-sky-300/15 bg-ink-950/82 shadow-prism">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 bg-sky-300/[0.04] px-5 py-5">
        <div>
          <p className="text-sm font-semibold text-sky-100">Managed Workload 控制策略</p>
          <h2 className="mt-2 text-xl font-semibold text-white">R6 / R7 入口与 R8 多模态控制面基础</h2>
          <p className="mt-2 max-w-[80ch] text-sm leading-6 text-slate-300">仅已完成对应数据面子轮次、资格有效且通过人工 fail-closed 确认的入口可以激活；其他入口继续保持 Legacy。</p>
        </div>
        <button className="inline-flex items-center gap-2 rounded-full border border-white/15 px-3 py-2 text-xs font-semibold text-slate-200" onClick={() => void load()} type="button"><RefreshCw className="h-3.5 w-3.5" />刷新</button>
      </div>
      <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,0.72fr)_minmax(0,1.28fr)]">
        <div>
          <label className="block text-sm text-slate-300">入口<select className="mt-2 w-full rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-white" value={entryId} onChange={(event) => setEntryId(event.target.value as EntryId)}>{Object.entries(ENTRY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
          {selectedPolicy ? <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.025] p-4">
            <div className="flex items-center justify-between gap-3"><span className="text-sm font-semibold text-white">{statusLabel(selectedPolicy.effective_status)}</span><span className="text-xs text-slate-400">revision {selectedPolicy.revision}</span></div>
            <dl className="mt-3 grid gap-2 text-xs text-slate-300"><div className="flex justify-between"><dt>部署开关</dt><dd>{selectedPolicy.feature_enabled ? "开启" : "关闭"}</dd></div><div className="flex justify-between"><dt>数据面接入</dt><dd>{selectedPolicy.data_plane_integrated ? "已接入" : "当前子轮次未接入"}</dd></div><div className="flex justify-between"><dt>人工批准</dt><dd>{selectedPolicy.approval_valid ? "有效" : "未生效"}</dd></div></dl>
            <label className="mt-3 block text-xs text-slate-300">本地降级模式<select aria-label="本地降级模式" className="mt-1.5 w-full rounded-lg border border-white/10 bg-ink-950 px-2 py-2 text-sm text-white" onChange={(event) => setLocalFallbackMode(event.target.value as LocalFallbackMode)} value={localFallbackMode}>{FALLBACK_OPTIONS[entryId].map((mode) => <option key={mode} value={mode}>{FALLBACK_LABELS[mode]}</option>)}</select></label>
            {localFallbackMode !== "none" ? <p className="mt-2 text-xs text-amber-100">本地降级必须在用户结果中标记为非模型结果，不会调用第二个远程 Provider。</p> : null}
            <div className="mt-3 space-y-1">{selectedPolicy.blocking_reason_codes.map((reason) => <p className="text-xs text-amber-100" key={reason}>· {REASON_LABELS[reason] ?? reason}</p>)}</div>
          </div> : null}
        </div>
        <div>
          <div className="flex items-center justify-between"><h3 className="flex items-center gap-2 text-sm font-semibold text-white"><Route className="h-4 w-4" />精确模型 Binding</h3><button className="rounded-full border border-white/15 px-3 py-1.5 text-xs text-slate-200 disabled:opacity-40" disabled={!eligibleConnections.length} onClick={addBinding} type="button">添加 Binding</button></div>
          <div className="mt-3 space-y-3">{editableBindings.map((binding, index) => <div className="grid gap-2 rounded-lg border border-white/10 bg-white/[0.025] p-3 md:grid-cols-[0.8fr_1fr_1.2fr_1fr_auto]" key={`${index}-${binding.execution_shape}`}>
            <select aria-label={`Binding ${index + 1} 执行形态`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 text-sm text-white" value={binding.execution_shape} onChange={(event) => setEditableBindings((current) => current.map((item, position) => {
              if (position !== index) return item;
              const executionShape = event.target.value as ExecutionShape;
              const adapterContract = adaptersForShape(executionShape)[0]?.[0] ?? null;
              const nextConnection = eligibleConnections.find((connection) =>
                connectionSupports(connection, executionShape, adapterContract)
              )?.id ?? "";
              return {
                ...item,
                execution_shape: executionShape,
                connection_id: nextConnection,
                rerank_access_mode: executionShape === "rerank_documents" ? "dedicated" : null,
                adapter_contract: adapterContract,
              };
            }))}>{ENTRY_SHAPES[entryId].map((shape) => <option key={shape} value={shape}>{SHAPE_LABELS[shape]}</option>)}</select>
            {binding.execution_shape === "rerank_documents" ? <select aria-label={`Binding ${index + 1} Rerank 访问方式`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 text-sm text-white" onChange={(event) => setEditableBindings((current) => current.map((item, position) => position === index ? { ...item, rerank_access_mode: event.target.value as RerankAccessMode } : item))} value={binding.rerank_access_mode ?? "dedicated"}><option value="dedicated">Dedicated API</option><option value="llm_json">LLM JSON Adapter</option></select> : MULTIMODAL_SHAPES.has(binding.execution_shape) ? <select aria-label={`Binding ${index + 1} Adapter`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 text-xs text-white" onChange={(event) => setEditableBindings((current) => current.map((item, position) => {
              if (position !== index) return item;
              const adapterContract = event.target.value as AdapterContract;
              const nextConnection = eligibleConnections.find((connection) => connectionSupports(connection, item.execution_shape, adapterContract))?.id ?? "";
              return { ...item, adapter_contract: adapterContract, connection_id: nextConnection };
            }))} value={binding.adapter_contract ?? ""}>{adaptersForShape(binding.execution_shape).map(([contract, spec]) => <option key={contract} value={contract}>{spec.label}</option>)}</select> : <span className="flex items-center rounded-lg border border-white/5 px-2 text-xs text-slate-500">固定契约</span>}
            <input aria-label={`Binding ${index + 1} 模型 ID`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 font-mono text-xs text-white" onChange={(event) => setEditableBindings((current) => current.map((item, position) => position === index ? { ...item, model_id: event.target.value } : item))} placeholder="精确模型 ID" value={binding.model_id} />
            <select aria-label={`Binding ${index + 1} 连接`} className="rounded-lg border border-white/10 bg-ink-950 px-2 py-2 text-sm text-white" value={binding.connection_id} onChange={(event) => setEditableBindings((current) => current.map((item, position) => position === index ? { ...item, connection_id: event.target.value } : item))}>{eligibleConnections.filter((connection) => connectionSupports(connection, binding.execution_shape, binding.adapter_contract)).map((connection) => <option key={connection.id} value={connection.id}>{connection.name}</option>)}</select>
            <button className="rounded-full border border-rose-300/20 px-3 py-2 text-xs text-rose-100" onClick={() => setEditableBindings((current) => current.filter((_, position) => position !== index))} type="button">移除</button>
          </div>)}{!editableBindings.length ? <p className="rounded-lg border border-dashed border-white/10 p-4 text-sm text-slate-400">尚未配置；没有 Binding 时不能激活。</p> : null}</div>
          <div className="mt-4 flex flex-wrap gap-2"><button className="rounded-full bg-sky-200 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-40" disabled={busy || !selectedPolicy || editableBindings.some((item) => !item.model_id.trim() || !item.connection_id || (MULTIMODAL_SHAPES.has(item.execution_shape) && !item.adapter_contract))} onClick={() => void savePolicy()} type="button">保存 Binding</button>{selectedPolicy?.configured_status !== "legacy" ? <button className="rounded-full border border-white/15 px-4 py-2 text-sm text-slate-200" disabled={busy} onClick={() => void deactivate()} type="button">显式恢复 Legacy</button> : null}{selectedPolicy && (selectedPolicy.configured_status === "legacy" || !selectedPolicy.approval_valid) ? <button className="rounded-full border border-amber-300/30 px-4 py-2 text-sm text-amber-100 disabled:cursor-not-allowed disabled:opacity-40" disabled={busy || !selectedPolicy.data_plane_integrated || !selectedPolicy.feature_enabled || selectedPolicy.blocking_reason_codes.length > 0} onClick={() => setConfirmActivation(true)} type="button">{selectedPolicy.configured_status === "legacy" ? "激活 Managed 必经" : "重新批准 Managed 必经"}</button> : null}</div>
        </div>
      </div>
      <div className="border-t border-white/10 p-5">
        <h3 className="text-sm font-semibold text-white">最近脱敏 Receipt</h3>
        <p className="mt-1 text-xs text-slate-400">管理视图显示内部连接引用与逐调用指标；不保存 Prompt、消息、模型正文、凭据或工具参数。</p>
        <div className="mt-3 space-y-2">
          {receipts.length ? receipts.map((run) => (
            <details className="rounded-lg border border-white/10 bg-white/[0.025]" key={run.run_id}>
              <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-2 p-3">
                <span className="text-sm text-white">{ENTRY_LABELS[run.entry_id]} · {run.status}</span>
                <span className="text-xs text-slate-400">{run.calls.length} 次逻辑调用</span>
              </summary>
              <div className="space-y-2 border-t border-white/10 p-3 text-xs text-slate-300">
                <p className="break-all font-mono text-[10px] text-slate-500">运行 {run.run_id}</p>
                {run.parent_run_reference ? <p className="break-all text-slate-400">父运行：{run.parent_run_reference}</p> : null}
                {run.result_class ? <p>结果分类：{run.result_class}</p> : null}
                {run.batch_job_id ? <p className="break-all text-sky-100">Batch：{run.batch_job_id} · {run.batch_status ?? "unknown"} · {(run.batch_completed_count ?? 0) + (run.batch_failed_count ?? 0)} / {run.batch_request_count ?? 0}</p> : null}
                {run.batch_job_id ? <p className="text-slate-500">usage/cost 为 Provider 报告元数据，不构成 ModelMirror 计费依据。</p> : null}
                {run.reason_codes?.length ? <p className="text-amber-100">原因：{run.reason_codes.join("、")}</p> : null}
                {run.calls.map((call) => (
                  <div className="rounded-md bg-slate-950/35 p-2.5" key={call.call_id}>
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      <span className="font-semibold text-slate-100">调用 {call.call_sequence}</span>
                      <span>{SHAPE_LABELS[call.execution_shape]}</span>
                      <span>{call.dispatched ? "已派发" : "发送前阻断"}</span>
                      <span>{call.status}</span>
                    </div>
                    <p className="mt-1 break-all font-mono text-[10px] text-slate-400">请求模型：{call.model_id}</p>
                    {call.actual_model ? <p className="mt-1 break-all font-mono text-[10px] text-slate-400">实际模型：{call.actual_model}</p> : null}
                    {call.connection_id ? <p className="mt-1 break-all font-mono text-[10px] text-slate-500">连接引用：{call.connection_id}</p> : null}
                    {call.adapter_contract ? <p className="mt-1 break-all font-mono text-[10px] text-slate-500">Adapter：{call.adapter_contract} · {call.protocol_version ?? "unknown"} · {call.provider_dispatch_state ?? "unknown"}</p> : null}
                    <div className="mt-1 flex flex-wrap gap-x-3 text-slate-500">
                      {call.ttft_ms != null ? <span>TTFT {Math.round(call.ttft_ms)} ms</span> : null}
                      {call.e2e_ms != null ? <span>E2E {Math.round(call.e2e_ms)} ms</span> : null}
                      {call.total_tokens != null ? <span>{call.total_tokens} tokens</span> : null}
                      {call.result_class ? <span>{call.result_class}</span> : null}
                      {call.error_code ? <span className="text-rose-200">{call.error_code}</span> : null}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )) : <p className="rounded-lg border border-dashed border-white/10 p-4 text-sm text-slate-400">当前入口尚无 Workload Receipt。</p>}
        </div>
      </div>
      {confirmActivation && selectedPolicy ? <div aria-modal="true" className="border-t border-amber-300/20 bg-amber-300/[0.05] p-5" role="dialog">
        <p className="text-sm font-semibold text-amber-100">确认激活 {ENTRY_LABELS[entryId]} Managed 必经</p>
        <p className="mt-2 text-sm leading-6 text-slate-300">激活后，Binding、资格、连接或凭据不合格时将失败关闭，不会自动回退 Legacy 或第二 Provider。</p>
        <label className="mt-3 flex items-start gap-2 text-sm text-slate-200"><input checked={confirmNoOpenP0P1} className="mt-1" onChange={(event) => setConfirmNoOpenP0P1(event.target.checked)} type="checkbox" />确认当前没有未解决的 P0/P1 阻塞项</label>
        <label className="mt-2 flex items-start gap-2 text-sm text-slate-200"><input checked={acknowledgeFailClosed} className="mt-1" onChange={(event) => setAcknowledgeFailClosed(event.target.checked)} type="checkbox" />理解并接受 Managed 不可用时失败关闭</label>
        <div className="mt-4 flex gap-2"><button className="rounded-full bg-amber-200 px-4 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-40" disabled={busy || !confirmNoOpenP0P1 || !acknowledgeFailClosed} onClick={() => void activate()} type="button">确认激活</button><button className="rounded-full border border-white/15 px-4 py-2 text-sm text-slate-200" disabled={busy} onClick={() => setConfirmActivation(false)} type="button">取消</button></div>
      </div> : null}
      {error ? <p className="m-5 flex items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-300/10 p-3 text-sm text-rose-100" role="alert"><CircleAlert className="h-4 w-4" />{error}</p> : null}
      {message ? <p className={`m-5 flex items-center gap-2 rounded-lg border p-3 text-sm ${messageTone === "success" ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-100" : "border-amber-300/20 bg-amber-300/10 text-amber-100"}`} data-tone={messageTone} role="status">{messageTone === "success" ? <BadgeCheck className="h-4 w-4" /> : <CircleAlert className="h-4 w-4" />}{message}</p> : null}
    </section>
  );
}
