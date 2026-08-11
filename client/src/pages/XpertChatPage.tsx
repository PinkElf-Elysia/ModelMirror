import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import AuthoringProposalNotice from "../components/authoring/AuthoringProposalNotice";
import RuntimeApprovalPanel from "../components/runtime/RuntimeApprovalPanel";
import BrowserSessionPanel from "../components/runtime/BrowserSessionPanel";
import ClientToolPanel from "../components/runtime/ClientToolPanel";
import SandboxWorkspacePanel from "../components/runtime/SandboxWorkspacePanel";
import DataXResultCard from "../components/datax/DataXResultCard";
import FileOutputTray from "../components/FileOutputTray";
import FileMemoryPanel from "../components/xpert/FileMemoryPanel";
import SkillCreatorCaptureButton, {
  xpertMessageCaptureSource,
} from "../components/skill-creator/SkillCreatorCaptureButton";
import { useSkillCreatorStatus } from "../hooks/useSkillCreatorStatus";
import {
  fetchFileOutputs,
  type FileOutput,
  type FileOutputReuseConfirmation,
} from "../data/fileOutputs";
import {
  type XpertConversationMessage,
  type XpertConversation,
  type XpertDefinition,
  type XpertFileAsset,
  type XpertMemoryCandidate,
  type XpertMemoryRecord,
  type XpertSummary,
} from "../types/xpert";
import { createGoal } from "../utils/goalApi";
import {
  createXpertConversation,
  createXpertMemory,
  deleteXpertFile,
  getXpertAudioCapabilities,
  getXpert,
  getXpertConversation,
  listXpertConversations,
  listXpertFiles,
  listXpertMemories,
  listXpertMemoryCandidates,
  listXperts,
  synthesizeXpertSpeech,
  transcribeXpertAudio,
  uploadXpertFile,
  type XpertAudioCapabilities,
} from "../utils/xpertApi";

interface XpertRunEvent {
  event: string;
  task_id?: string;
  run_id?: string;
  node_id?: string;
  node_title?: string;
  node_type?: string;
  output?: string;
  final_output?: string;
  message?: string;
  xpert_id?: string;
  xpert_version?: number;
  approval_id?: string;
  request_id?: string;
  approval_status?: string;
  request_type?: "tool_call" | "final_output" | "manual_input";
  tool_name?: string;
  status?: string;
  candidate_id?: string;
  activated_skill_id?: string;
  source_ref?: string;
  result_count?: number;
  sequence?: number;
  suggestions?: string[];
  conversation_title?: string;
}

export function selectedXpertFilesAfterConversationRestore(
  _files: XpertFileAsset[],
): string[] {
  return [];
}

export function selectedXpertFilesAfterRefresh(
  current: string[],
  files: XpertFileAsset[],
): string[] {
  const availableIds = new Set(files.map((file) => file.asset_id));
  return current.filter((assetId) => availableIds.has(assetId));
}

export function consumeSelectedXpertFiles(
  fileUploadEnabled: boolean,
  selectedFileIds: string[],
): { fileAssetIdsForRun: string[]; nextSelectedFileIds: string[] } {
  return {
    fileAssetIdsForRun: fileUploadEnabled ? [...selectedFileIds] : [],
    nextSelectedFileIds: [],
  };
}

export function xpertOutputScopeId(xpertId: string, conversationId: string) {
  return `xpert:${xpertId}:${conversationId}`;
}

export function fileOutputsForRun(outputs: FileOutput[], runId: string | null | undefined) {
  if (!runId) return [];
  return outputs.filter((output) => output.source_run_id === runId);
}

export function unassociatedXpertFileOutputs(
  outputs: FileOutput[],
  messages: XpertConversationMessage[],
) {
  const associatedRunIds = new Set(
    messages
      .filter((message) => message.role === "assistant" && message.source_run_id)
      .map((message) => message.source_run_id as string),
  );
  return outputs.filter(
    (output) => !output.source_run_id || !associatedRunIds.has(output.source_run_id),
  );
}

export function replaceFileOutputSubset(
  current: FileOutput[],
  previousSubset: FileOutput[],
  nextSubset: FileOutput[],
) {
  const previousIds = new Set(previousSubset.map((output) => output.output_id));
  return [
    ...current.filter((output) => !previousIds.has(output.output_id)),
    ...nextSubset,
  ];
}

export function isCurrentXpertConversationRequest(
  requestToken: number,
  currentToken: number,
  requestConversationId: string,
  currentConversationId: string,
): boolean {
  return requestToken === currentToken
    && requestConversationId === currentConversationId;
}

export function xpertFilesAfterPermanentDelete(
  files: XpertFileAsset[],
  assetId: string,
): XpertFileAsset[] {
  return files.filter((file) => file.asset_id !== assetId);
}

export function xpertConversationNavigationLocked(
  contextLoading: boolean,
  running: boolean,
  uploading: boolean,
  deletingFileId: string,
): boolean {
  return contextLoading || running || uploading || Boolean(deletingFileId);
}

export function xpertMessageInputLocked(
  contextLoading: boolean,
  running: boolean,
): boolean {
  return contextLoading || running;
}

interface RuntimeRunSummary {
  run_id: string;
  run_type: string;
  status: string;
  title: string;
  source_id: string | null;
  parent_run_id: string | null;
  metadata: Record<string, unknown>;
  error: string | null;
}

interface RuntimeCheckpoint {
  checkpoint_id: string;
  event_type: string;
  title: string;
  summary: string;
  severity: string;
  created_at: number;
}

interface ToolAuditRecord {
  record_id: string;
  tool_name: string;
  status: string;
  duration_ms: number | null;
  output_length: number | null;
  error: string | null;
}

interface TraceBundle {
  run: RuntimeRunSummary | null;
  childRuns: RuntimeRunSummary[];
  checkpoints: RuntimeCheckpoint[];
  childCheckpoints: Record<string, RuntimeCheckpoint[]>;
  audits: ToolAuditRecord[];
}

interface KnowledgeBaseSummary {
  id: string;
  name: string;
  document_count: number;
}

interface KnowledgeWriteProposalSummary {
  proposal_id: string;
  kb_id: string;
  title: string;
  status: string;
}

interface RuntimeTodoItem {
  todo_id: string;
  scope_type: string;
  scope_id: string;
  title: string;
  details: string;
  status: "pending" | "in_progress" | "completed" | "cancelled" | "archived";
  priority: number;
  order: number;
  revision: number;
}

interface XpertToolMemoryRecord {
  memory_id: string;
  tool_name: string;
  provider: string;
  summary: string;
  parameter_summary: Record<string, unknown>;
  source_run_id: string | null;
  created_at: number;
}

async function responseError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: string; error?: string };
    return payload.detail || payload.error || `请求失败：${response.status}`;
  } catch {
    return `请求失败：${response.status}`;
  }
}

function todoScopeId(xpertId: string, conversationId: string) {
  return `${xpertId}:${conversationId}`;
}

async function listConversationTodos(xpertId: string, conversationId: string) {
  const response = await fetch(
    `/api/runtime/todos?scope_type=conversation&scope_id=${encodeURIComponent(todoScopeId(xpertId, conversationId))}`,
  );
  if (!response.ok) throw new Error(await responseError(response));
  const payload = (await response.json()) as { items?: RuntimeTodoItem[] };
  return payload.items ?? [];
}

function eventSummary(event: XpertRunEvent) {
  if (event.event === "workflow_meta") return "运行已登记";
  if (event.event === "workflow_end") return "最终回答已生成";
  if (event.event === "error") return event.message || "运行失败";
  if (event.event === "skill_runtime_status") {
    if (event.status === "find") return `本地 Skill 检索完成：${event.result_count ?? 0} 项候选`;
    if (event.status === "enable") return `已为本轮激活 ${event.activated_skill_id ?? event.candidate_id ?? "Skill"}`;
    if (event.status === "install") return `已安装并仅授权本轮使用：${event.activated_skill_id ?? "Skill"}`;
    if (event.status === "upgrade") return `已升级并仅授权本轮使用：${event.activated_skill_id ?? "Skill"}`;
    if (event.status === "reject") return `已拒绝候选：${event.candidate_id ?? "Skill"}`;
  }
  return event.output || event.message || event.node_title || event.event;
}

function roleCopy(role: XpertConversationMessage["role"]) {
  return role === "user" ? "你" : "智能体";
}

export default function XpertChatPage() {
  const { xpertId = "" } = useParams();
  const navigate = useNavigate();
  const { status: skillCreatorStatus } = useSkillCreatorStatus();
  const [xpert, setXpert] = useState<XpertDefinition | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [messages, setMessages] = useState<XpertConversationMessage[]>([]);
  const [input, setInput] = useState("");
  const [events, setEvents] = useState<XpertRunEvent[]>([]);
  const [runId, setRunId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [trace, setTrace] = useState<TraceBundle | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showGoalComposer, setShowGoalComposer] = useState(false);
  const [goalTitle, setGoalTitle] = useState("");
  const [goalObjective, setGoalObjective] = useState("");
  const [plannerXpertId, setPlannerXpertId] = useState("");
  const [publishedXperts, setPublishedXperts] = useState<XpertSummary[]>([]);
  const [creatingGoal, setCreatingGoal] = useState(false);
  const [conversations, setConversations] = useState<XpertConversation[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [summaryRevision, setSummaryRevision] = useState(0);
  const [files, setFiles] = useState<XpertFileAsset[]>([]);
  const [fileOutputs, setFileOutputs] = useState<FileOutput[]>([]);
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
  const [memories, setMemories] = useState<XpertMemoryRecord[]>([]);
  const [memoryCandidates, setMemoryCandidates] = useState<XpertMemoryCandidate[]>([]);
  const [toolMemories, setToolMemories] = useState<XpertToolMemoryRecord[]>([]);
  const [todos, setTodos] = useState<RuntimeTodoItem[]>([]);
  const [newTodoTitle, setNewTodoTitle] = useState("");
  const [todoBusy, setTodoBusy] = useState(false);
  const [contextLoading, setContextLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [deletingFileId, setDeletingFileId] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [knowledgeTargetId, setKnowledgeTargetId] = useState("");
  const [knowledgeProposals, setKnowledgeProposals] = useState<KnowledgeWriteProposalSummary[]>([]);
  const [promotingFiles, setPromotingFiles] = useState(false);
  const [audioCapabilities, setAudioCapabilities] = useState<XpertAudioCapabilities | null>(null);
  const [transcribing, setTranscribing] = useState(false);
  const [speakingMessageId, setSpeakingMessageId] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const audioInputRef = useRef<HTMLInputElement | null>(null);
  const conversationIdRef = useRef("");
  const conversationRequestTokenRef = useRef(0);
  const conversationNavigationLocked = xpertConversationNavigationLocked(
    contextLoading,
    running,
    uploading,
    deletingFileId,
  );
  const messageInputLocked = xpertMessageInputLocked(contextLoading, running);

  useEffect(() => {
    let cancelled = false;
    getXpert(xpertId)
      .then((data) => {
        if (cancelled) return;
        setXpert(data);
        setVersion(data.published_version);
        document.title = `模镜 - ${data.name}`;
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "智能体加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [xpertId]);

  useEffect(() => {
    listXperts({ status: "published", limit: 200 })
      .then((payload) => setPublishedXperts(payload.items))
      .catch(() => setPublishedXperts([]));
  }, []);

  useEffect(() => {
    if (!xpert?.id) return;
    void refreshKnowledgeProposals(xpert.id);
  }, [xpert?.id]);

  useEffect(() => {
    fetch("/api/rag/knowledge_bases")
      .then(async (response) => {
        if (!response.ok) throw new Error(await responseError(response));
        return response.json() as Promise<{ knowledge_bases: KnowledgeBaseSummary[] }>;
      })
      .then((payload) => {
        setKnowledgeBases(payload.knowledge_bases);
        setKnowledgeTargetId((current) => current || payload.knowledge_bases[0]?.id || "");
      })
      .catch(() => setKnowledgeBases([]));
  }, []);

  useEffect(() => {
    if (!xpert) return;
    let cancelled = false;
    setContextLoading(true);
    listXpertConversations(xpert.id)
      .then(async (payload) => {
        if (cancelled) return;
        let active = payload.items[0];
        if (!active) active = await createXpertConversation(xpert.id);
        if (cancelled) return;
        setConversations(active ? [active, ...payload.items.filter((item) => item.conversation_id !== active.conversation_id)] : payload.items);
        await selectConversation(active.conversation_id, xpert.id);
      })
      .catch((caught) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "\u4f1a\u8bdd\u52a0\u8f7d\u5931\u8d25");
      })
      .finally(() => {
        if (!cancelled) setContextLoading(false);
      });
    return () => {
      cancelled = true;
      conversationRequestTokenRef.current += 1;
    };
  }, [xpert?.id]);

  const publishedVersions = useMemo(
    () => [...(xpert?.versions ?? [])].sort((a, b) => b.version - a.version),
    [xpert],
  );
  const activeVersion = useMemo(
    () => publishedVersions.find((item) => item.version === version) ?? null,
    [publishedVersions, version],
  );
  const versionFeatures = activeVersion?.features ?? null;
  const openingQuestions = versionFeatures
    ? (versionFeatures.opening.enabled ? versionFeatures.opening.questions : [])
    : (xpert?.starters ?? []);
  const openingMessage = versionFeatures?.opening.enabled
    ? versionFeatures.opening.message
    : "";
  const commandProfiles = activeVersion?.prompt_profiles ?? [];
  const fileUploadEnabled = versionFeatures?.file_upload.enabled ?? true;
  const maxFilesPerRun = versionFeatures?.file_upload.max_files_per_run ?? 5;
  const allowedFileExtensions = (
    versionFeatures?.file_upload.allowed_extensions ?? [".txt", ".md", ".markdown", ".pdf"]
  ).map((item) => item.startsWith(".") ? item.toLowerCase() : `.${item.toLowerCase()}`);
  const fileAccept = allowedFileExtensions.join(",");
  const skillCaptureEnabled = Boolean(
    skillCreatorStatus?.enabled
    && skillCreatorStatus.supported_sources.includes("xpert_chat"),
  );

  useEffect(() => {
    if (!xpert?.id || !version) {
      setAudioCapabilities(null);
      return;
    }
    let cancelled = false;
    setAudioCapabilities(null);
    getXpertAudioCapabilities(xpert.id, version)
      .then((payload) => {
        if (!cancelled) setAudioCapabilities(payload);
      })
      .catch(() => {
        if (!cancelled) setAudioCapabilities(null);
      });
    return () => {
      cancelled = true;
    };
  }, [xpert?.id, version]);

  useEffect(() => {
    setSelectedFileIds((current) => (
      fileUploadEnabled ? current.slice(0, maxFilesPerRun) : []
    ));
  }, [fileUploadEnabled, maxFilesPerRun]);

  async function selectConversation(
    nextConversationId: string,
    selectedXpertId = xpert?.id,
  ) {
    if (!selectedXpertId || !nextConversationId) return;
    const requestToken = conversationRequestTokenRef.current + 1;
    conversationRequestTokenRef.current = requestToken;
    conversationIdRef.current = nextConversationId;
    setConversationId(nextConversationId);
    setMessages([]);
    setInput("");
    setFiles([]);
    setFileOutputs([]);
    setSelectedFileIds([]);
    setContextLoading(true);
    setError("");
    try {
      const [conversation, filePayload, outputItems, memoryPayload, candidatePayload, todoItems, toolMemoryPayload] = await Promise.all([
        getXpertConversation(selectedXpertId, nextConversationId),
        listXpertFiles(selectedXpertId, nextConversationId),
        fetchFileOutputs(
          "agent",
          xpertOutputScopeId(selectedXpertId, nextConversationId),
        ).catch(() => []),
        listXpertMemories(selectedXpertId, nextConversationId),
        listXpertMemoryCandidates(selectedXpertId, nextConversationId),
        listConversationTodos(selectedXpertId, nextConversationId),
        listToolMemories(selectedXpertId, nextConversationId),
      ]);
      if (!isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        nextConversationId,
        conversationIdRef.current,
      )) return;
      setMessages(conversation.messages ?? []);
      setSummaryRevision(conversation.summary_revision ?? 0);
      setFiles(filePayload.items);
      setFileOutputs(outputItems);
      setSelectedFileIds(selectedXpertFilesAfterConversationRestore(filePayload.items));
      setMemories(memoryPayload.items);
      setMemoryCandidates(candidatePayload.items);
      setTodos(todoItems);
      setToolMemories(toolMemoryPayload.items);
    } catch (caught) {
      if (isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        nextConversationId,
        conversationIdRef.current,
      )) {
        setError(caught instanceof Error ? caught.message : "会话加载失败");
      }
    } finally {
      if (isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        nextConversationId,
        conversationIdRef.current,
      )) {
        setContextLoading(false);
      }
    }
  }

  async function refreshContext() {
    const selectedXpertId = xpert?.id;
    const selectedConversationId = conversationIdRef.current || conversationId;
    const requestToken = conversationRequestTokenRef.current;
    if (!selectedXpertId || !selectedConversationId) return;
    const [conversationPayload, filePayload, outputItems, memoryPayload, candidatePayload, todoItems, toolMemoryPayload] = await Promise.all([
      listXpertConversations(selectedXpertId),
      listXpertFiles(selectedXpertId, selectedConversationId),
      fetchFileOutputs(
        "agent",
        xpertOutputScopeId(selectedXpertId, selectedConversationId),
      ).catch(() => []),
      listXpertMemories(selectedXpertId, selectedConversationId),
      listXpertMemoryCandidates(selectedXpertId, selectedConversationId),
      listConversationTodos(selectedXpertId, selectedConversationId),
      listToolMemories(selectedXpertId, selectedConversationId),
    ]);
    if (!isCurrentXpertConversationRequest(
      requestToken,
      conversationRequestTokenRef.current,
      selectedConversationId,
      conversationIdRef.current,
    )) return;
    setConversations(conversationPayload.items);
    setFiles(filePayload.items);
    setFileOutputs(outputItems);
    setSelectedFileIds((current) => selectedXpertFilesAfterRefresh(current, filePayload.items));
    setMemories(memoryPayload.items);
    setMemoryCandidates(candidatePayload.items);
    setTodos(todoItems);
    setToolMemories(toolMemoryPayload.items);
  }

  async function prepareXpertOutputReuse(
    _output: FileOutput,
    confirmation: FileOutputReuseConfirmation,
  ) {
    if (selectedFileIds.length >= maxFilesPerRun) {
      throw new Error(`本轮最多选择 ${maxFilesPerRun} 个文件，请先移除一个附件。`);
    }
    await refreshContext();
    setSelectedFileIds((current) =>
      Array.from(new Set([...current, confirmation.asset_id])).slice(0, maxFilesPerRun),
    );
  }

  async function syncCompletedAssistantMessage(
    fallback: XpertConversationMessage,
  ) {
    setMessages((current) => [...current, fallback]);
    if (
      !xpert
      || !conversationId
      || !fallback.source_task_id
      || !fallback.source_run_id
    ) {
      return;
    }
    try {
      const persisted = await getXpertConversation(xpert.id, conversationId);
      const linked = persisted.messages?.some((message) => (
        message.role === "assistant"
        && message.source_task_id === fallback.source_task_id
        && message.source_run_id === fallback.source_run_id
        && Boolean(message.message_id)
      ));
      if (linked) setMessages(persisted.messages ?? []);
    } catch {
      // Keep the visible fallback. It intentionally has no message ID, so it
      // cannot expose the trusted-source action until a later refresh.
    }
  }

  async function listToolMemories(selectedXpertId: string, selectedConversationId: string) {
    const response = await fetch(
      `/api/xperts/${encodeURIComponent(selectedXpertId)}/conversations/${encodeURIComponent(selectedConversationId)}/tool-memory`,
    );
    if (!response.ok) throw new Error(await responseError(response));
    return response.json() as Promise<{ items: XpertToolMemoryRecord[] }>;
  }

  async function clearToolMemory(memoryId: string) {
    if (!xpert || !conversationId) return;
    const response = await fetch(
      `/api/xperts/${encodeURIComponent(xpert.id)}/conversations/${encodeURIComponent(conversationId)}/tool-memory/${encodeURIComponent(memoryId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      setError(await responseError(response));
      return;
    }
    setToolMemories((current) => current.filter((item) => item.memory_id !== memoryId));
  }

  async function refreshKnowledgeProposals(selectedXpertId = xpert?.id) {
    if (!selectedXpertId) return;
    try {
      const response = await fetch(
        `/api/rag/knowledge-write-proposals?status=pending&source_xpert_id=${encodeURIComponent(selectedXpertId)}&limit=100`,
      );
      if (!response.ok) throw new Error(await responseError(response));
      const payload = (await response.json()) as {
        proposals: KnowledgeWriteProposalSummary[];
      };
      setKnowledgeProposals(payload.proposals ?? []);
    } catch {
      setKnowledgeProposals([]);
    }
  }

  async function startConversation() {
    if (!xpert || running || contextLoading || uploading || deletingFileId) return;
    const created = await createXpertConversation(xpert.id);
    setConversations((current) => [created, ...current]);
    await selectConversation(created.conversation_id);
  }

  async function handleFileUpload(file: File) {
    if (!xpert || !conversationId) return;
    const targetXpertId = xpert.id;
    const targetConversationId = conversationId;
    const requestToken = conversationRequestTokenRef.current;
    if (!fileUploadEnabled) {
      setError("当前发布版本未启用会话文件。");
      return;
    }
    if (selectedFileIds.length >= maxFilesPerRun) {
      setError(`当前发布版本每次最多选择 ${maxFilesPerRun} 个文件。`);
      return;
    }
    const extension = file.name.includes(".")
      ? `.${file.name.split(".").pop()?.toLowerCase()}`
      : "";
    if (!allowedFileExtensions.includes(extension)) {
      setError(`当前发布版本仅允许：${allowedFileExtensions.join(", ")}`);
      return;
    }
    setUploading(true);
    setError("");
    try {
      const uploaded = await uploadXpertFile(
        targetXpertId,
        targetConversationId,
        file,
      );
      if (!isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        targetConversationId,
        conversationIdRef.current,
      )) return;
      await refreshContext();
      if (!isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        targetConversationId,
        conversationIdRef.current,
      )) return;
      setSelectedFileIds((current) => (
        [...current, uploaded.asset_id].slice(-maxFilesPerRun)
      ));
      setShowContext(true);
      setShowTrace(false);
    } catch (caught) {
      if (isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        targetConversationId,
        conversationIdRef.current,
      )) {
        setError(caught instanceof Error ? caught.message : "\u6587\u4ef6\u4e0a\u4f20\u5931\u8d25");
      }
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function permanentlyDeleteFile(file: XpertFileAsset) {
    if (!xpert || !conversationId || deletingFileId) return;
    const targetXpertId = xpert.id;
    const targetConversationId = conversationId;
    const requestToken = conversationRequestTokenRef.current;
    const confirmed = window.confirm(
      `彻底删除“${file.filename}”？原件和提取文本将从当前会话永久移除，无法撤销。`,
    );
    if (!confirmed) return;
    setDeletingFileId(file.asset_id);
    setError("");
    try {
      await deleteXpertFile(targetXpertId, targetConversationId, file.asset_id);
      if (!isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        targetConversationId,
        conversationIdRef.current,
      )) return;
      setFiles((current) => xpertFilesAfterPermanentDelete(current, file.asset_id));
      setSelectedFileIds((current) => current.filter((item) => item !== file.asset_id));
      void refreshContext().catch(() => undefined);
    } catch (caught) {
      if (isCurrentXpertConversationRequest(
        requestToken,
        conversationRequestTokenRef.current,
        targetConversationId,
        conversationIdRef.current,
      )) {
        setError(caught instanceof Error ? caught.message : "文件删除失败");
      }
    } finally {
      setDeletingFileId("");
    }
  }

  async function handleAudioTranscription(file: File) {
    if (!xpert || !version || !audioCapabilities?.speech_to_text.enabled) return;
    setTranscribing(true);
    setError("");
    try {
      const payload = await transcribeXpertAudio(xpert.id, version, file);
      setInput((current) => [current.trim(), payload.text.trim()].filter(Boolean).join("\n"));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "语音转写失败");
    } finally {
      setTranscribing(false);
      if (audioInputRef.current) audioInputRef.current.value = "";
    }
  }

  async function speakMessage(message: XpertConversationMessage, index: number) {
    if (!xpert || !version || !audioCapabilities?.text_to_speech.enabled) return;
    const messageId = message.message_id || `assistant-${index}`;
    setSpeakingMessageId(messageId);
    setError("");
    try {
      const blob = await synthesizeXpertSpeech(xpert.id, version, message.content);
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.addEventListener("ended", () => {
        URL.revokeObjectURL(url);
        setSpeakingMessageId("");
      }, { once: true });
      audio.addEventListener("error", () => {
        URL.revokeObjectURL(url);
        setSpeakingMessageId("");
        setError("语音播放失败");
      }, { once: true });
      await audio.play();
    } catch (caught) {
      setSpeakingMessageId("");
      setError(caught instanceof Error ? caught.message : "语音合成失败");
    }
  }

  async function promoteSelectedFilesToKnowledge() {
    if (!xpert || !conversationId || !knowledgeTargetId || selectedFileIds.length === 0) return;
    setPromotingFiles(true);
    setError("");
    try {
      const draftResponse = await fetch(
        `/api/rag/pipeline/draft?kb_id=${encodeURIComponent(knowledgeTargetId)}`,
      );
      if (!draftResponse.ok) throw new Error(await responseError(draftResponse));
      const draft = (await draftResponse.json()) as { version: number };
      const response = await fetch(`/api/rag/pipeline/draft/${knowledgeTargetId}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          draft_version: draft.version,
          source_document_ids: [],
          xpert_file_refs: selectedFileIds.map((assetId) => ({
            xpert_id: xpert.id,
            conversation_id: conversationId,
            asset_id: assetId,
          })),
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const job = (await response.json()) as { job_id: string };
      navigate(
        `/rag?kb_id=${encodeURIComponent(knowledgeTargetId)}&job_id=${encodeURIComponent(job.job_id)}`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "附件加入知识库失败");
    } finally {
      setPromotingFiles(false);
    }
  }

  async function rememberMessage(message: XpertConversationMessage) {
    if (!xpert || !conversationId) return;
    await createXpertMemory(xpert.id, {
      content: message.content,
      scope: "xpert",
      conversation_id: conversationId,
      source_type: "user_action",
      source_id: message.message_id,
    });
    await refreshContext();
    setShowContext(true);
    setShowTrace(false);
  }

  async function createTodo() {
    if (!xpert || !conversationId || !newTodoTitle.trim()) return;
    setTodoBusy(true);
    try {
      const response = await fetch("/api/runtime/todos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope_type: "conversation",
          scope_id: todoScopeId(xpert.id, conversationId),
          title: newTodoTitle.trim(),
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setNewTodoTitle("");
      setTodos(await listConversationTodos(xpert.id, conversationId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Todo 创建失败");
    } finally {
      setTodoBusy(false);
    }
  }

  async function patchTodo(
    todo: RuntimeTodoItem,
    patch: Partial<Pick<RuntimeTodoItem, "status" | "order" | "title">>,
  ) {
    if (!xpert || !conversationId) return;
    setTodoBusy(true);
    try {
      const response = await fetch(`/api/runtime/todos/${todo.todo_id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope_type: "conversation",
          scope_id: todoScopeId(xpert.id, conversationId),
          revision: todo.revision,
          ...patch,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      setTodos(await listConversationTodos(xpert.id, conversationId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Todo 更新失败");
    } finally {
      setTodoBusy(false);
    }
  }

  async function archiveTodo(todo: RuntimeTodoItem) {
    if (!xpert || !conversationId) return;
    setTodoBusy(true);
    try {
      const scopeId = todoScopeId(xpert.id, conversationId);
      const response = await fetch(
        `/api/runtime/todos/${todo.todo_id}?scope_type=conversation&scope_id=${encodeURIComponent(scopeId)}&revision=${todo.revision}`,
        { method: "DELETE" },
      );
      if (!response.ok) throw new Error(await responseError(response));
      setTodos(await listConversationTodos(xpert.id, conversationId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Todo 归档失败");
    } finally {
      setTodoBusy(false);
    }
  }

  async function loadTrace(nextRunId: string, nextTaskId: string) {
    try {
      const [runResponse, checkpointsResponse, childrenResponse, observationResponse] =
        await Promise.all([
          fetch(`/api/runtime/runs/${nextRunId}`),
          fetch(`/api/runtime/runs/${nextRunId}/checkpoints`),
          fetch(`/api/runtime/runs?parent_run_id=${encodeURIComponent(nextRunId)}&limit=50`),
          nextTaskId
            ? fetch(`/api/workflow/runtime-events/${nextTaskId}`)
            : Promise.resolve(null),
        ]);
      const run = runResponse.ok
        ? ((await runResponse.json()) as RuntimeRunSummary)
        : null;
      const checkpoints = checkpointsResponse.ok
        ? ((await checkpointsResponse.json()) as RuntimeCheckpoint[])
        : [];
      const childRuns = childrenResponse.ok
        ? ((await childrenResponse.json()) as RuntimeRunSummary[])
        : [];
      const childCheckpointEntries = await Promise.all(
        childRuns.map(async (child) => {
          const response = await fetch(`/api/runtime/runs/${child.run_id}/checkpoints`);
          return [
            child.run_id,
            response.ok ? ((await response.json()) as RuntimeCheckpoint[]) : [],
          ] as const;
        }),
      );
      const observation = observationResponse?.ok
        ? ((await observationResponse.json()) as { tool_audit_records?: ToolAuditRecord[] })
        : null;
      setTrace({
        run,
        childRuns,
        checkpoints,
        childCheckpoints: Object.fromEntries(childCheckpointEntries),
        audits: observation?.tool_audit_records ?? [],
      });
    } catch {
      setTrace(null);
    }
  }

  async function sendMessage(messageOverride?: string) {
    const message = (messageOverride ?? input).trim();
    if (!message || !xpert || !version || running || contextLoading || !conversationId) return;

    const history = messages.slice(-20);
    const { fileAssetIdsForRun, nextSelectedFileIds } = consumeSelectedXpertFiles(
      fileUploadEnabled,
      selectedFileIds,
    );
    setMessages((current) => [...current, { role: "user", content: message }]);
    setInput("");
    setSelectedFileIds(nextSelectedFileIds);
    setEvents([]);
    setTrace(null);
    setRunId("");
    setTaskId("");
    setRunning(true);
    setError("");

    try {
      const response = await fetch(`/api/xperts/${xpert.id}/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          messages: history,
          version,
          conversation_id: conversationId,
          file_asset_ids: fileAssetIdsForRun,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      if (!response.body) throw new Error("浏览器未收到流式响应。 ");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalOutput = "";
      let nextRunId = "";
      let nextTaskId = "";
      let approvalPending = false;
      let clientToolPending = false;
      let finalSuggestions: string[] = [];
      let finalConversationTitle = "";

      const processBlock = (block: string) => {
        for (const line of block.split(/\r?\n/)) {
          if (!line.startsWith("data:")) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          const event = JSON.parse(raw) as XpertRunEvent;
          setEvents((current) => [...current.slice(-79), event]);
          if (event.run_id) {
            nextRunId = event.run_id;
            setRunId(event.run_id);
          }
          if (event.task_id) {
            nextTaskId = event.task_id;
            setTaskId(event.task_id);
          }
          if (event.event === "workflow_end") {
            finalOutput = event.final_output || "";
            finalSuggestions = Array.isArray(event.suggestions) ? event.suggestions : [];
            finalConversationTitle = event.conversation_title || "";
          }
          if (event.event === "runtime_approval_pending") {
            approvalPending = true;
          }
          if (event.event === "client_tool_waiting") clientToolPending = true;
          if (event.event === "error") {
            throw new Error(event.message || "智能体运行失败");
          }
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let match = buffer.match(/\r?\n\r?\n/);
        while (match?.index !== undefined) {
          processBlock(buffer.slice(0, match.index));
          buffer = buffer.slice(match.index + match[0].length);
          match = buffer.match(/\r?\n\r?\n/);
        }
        if (done) break;
      }
      if (buffer.trim()) processBlock(buffer);
      if (!approvalPending && !clientToolPending) {
        await syncCompletedAssistantMessage({
          role: "assistant",
          content: finalOutput || "运行完成，但没有返回文本输出。",
          suggestions: finalSuggestions,
          source_task_id: nextTaskId || null,
          source_run_id: nextRunId || null,
        });
        if (finalConversationTitle) {
          setConversations((current) => current.map((item) => (
            item.conversation_id === conversationId
              ? { ...item, title: finalConversationTitle }
              : item
          )));
        }
      }
      if (nextRunId) await loadTrace(nextRunId, nextTaskId);
      window.setTimeout(() => void refreshContext(), 800);
      window.setTimeout(() => void refreshKnowledgeProposals(), 800);
    } catch (caught) {
      const messageText = caught instanceof Error ? caught.message : "智能体运行失败";
      setError(messageText);
      setMessages((current) => [...current, { role: "assistant", content: `运行失败：${messageText}` }]);
    } finally {
      setRunning(false);
    }
  }

  async function resumeApprovalExecution() {
    if (!taskId || running) return;
    setRunning(true);
    setError("");
    try {
      const response = await fetch(`/api/workflow/run/${taskId}/stream?after_sequence=0`);
      if (!response.ok) throw new Error(await responseError(response));
      if (!response.body) throw new Error("浏览器未收到恢复执行流。");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalOutput = "";
      let approvalPending = false;
      let clientToolPending = false;
      let nextRunId = runId;
      let finalSuggestions: string[] = [];
      let finalConversationTitle = "";
      setEvents([]);

      const processBlock = (block: string) => {
        for (const line of block.split(/\r?\n/)) {
          if (!line.startsWith("data:")) continue;
          const raw = line.slice(5).trim();
          if (!raw) continue;
          const event = JSON.parse(raw) as XpertRunEvent;
          setEvents((current) => [...current.slice(-79), event]);
          if (event.run_id) {
            nextRunId = event.run_id;
            setRunId(event.run_id);
          }
          if (event.event === "workflow_end") {
            finalOutput = event.final_output || "";
            finalSuggestions = Array.isArray(event.suggestions) ? event.suggestions : [];
            finalConversationTitle = event.conversation_title || "";
          }
          if (event.event === "runtime_approval_pending") approvalPending = true;
          if (event.event === "client_tool_waiting") clientToolPending = true;
          if (event.event === "error") throw new Error(event.message || "智能体恢复执行失败");
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let match = buffer.match(/\r?\n\r?\n/);
        while (match?.index !== undefined) {
          processBlock(buffer.slice(0, match.index));
          buffer = buffer.slice(match.index + match[0].length);
          match = buffer.match(/\r?\n\r?\n/);
        }
        if (done) break;
      }
      if (buffer.trim()) processBlock(buffer);
      if (!approvalPending && !clientToolPending && finalOutput) {
        await syncCompletedAssistantMessage({
          role: "assistant",
          content: finalOutput,
          suggestions: finalSuggestions,
          source_task_id: taskId,
          source_run_id: nextRunId || null,
        });
        if (finalConversationTitle) {
          setConversations((current) => current.map((item) => (
            item.conversation_id === conversationId
              ? { ...item, title: finalConversationTitle }
              : item
          )));
        }
      }
      if (nextRunId) await loadTrace(nextRunId, taskId);
      window.setTimeout(() => void refreshContext(), 800);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "智能体恢复执行失败");
    } finally {
      setRunning(false);
    }
  }

  function openGoalComposer() {
    const lastUserMessage = [...messages].reverse().find((message) => message.role === "user");
    const objective = lastUserMessage?.content || input.trim() || openingQuestions[0] || "";
    setGoalTitle(objective ? `长期目标：${objective.slice(0, 36)}` : `长期目标：${xpert?.name ?? "智能体"}`);
    setGoalObjective(objective);
    setPlannerXpertId(xpert?.id ?? publishedXperts[0]?.id ?? "");
    setShowGoalComposer(true);
  }

  async function createLongGoal() {
    if (!xpert || !goalTitle.trim() || !goalObjective.trim() || !plannerXpertId) return;
    setCreatingGoal(true);
    setError("");
    try {
      const created = await createGoal({
        title: goalTitle.trim(),
        objective: goalObjective.trim(),
        planner_xpert_id: plannerXpertId,
        source_xpert_id: xpert.id,
        source_conversation_id: conversationId,
        file_asset_ids: fileUploadEnabled ? selectedFileIds : [],
        messages: messages.slice(-20),
        max_parallel: 2,
      });
      navigate(`/agents/goals/${created.goal_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "长期 Goal 创建失败");
    } finally {
      setCreatingGoal(false);
    }
  }

  if (loading) {
    return (
      <PageContainer activeResource="agents" maxWidthClassName="max-w-[1560px]">
        <div className="h-[70vh] animate-pulse rounded-lg border border-white/10 bg-white/[0.04]" />
      </PageContainer>
    );
  }

  if (!xpert) {
    return (
      <PageContainer activeResource="agents">
        <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 p-5 text-sm text-rose-100">{error || "智能体不存在。"}</div>
      </PageContainer>
    );
  }

  if (xpert.status !== "published" || !xpert.published_version || !version) {
    return (
      <PageContainer activeResource="agents">
        <div className="rounded-lg border border-white/10 bg-white/[0.04] p-8 text-center">
          <h1 className="text-xl font-semibold text-white">{xpert.name} 尚未发布</h1>
          <p className="mt-2 text-sm text-slate-400">先完成发布预检并生成一个不可变版本，再从聊天入口运行。</p>
          <Link className="mt-4 inline-flex rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950" to={`/agents/studio/${xpert.id}`}>进入 Studio</Link>
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1560px]">
      <div className="grid min-h-[calc(100vh-9rem)] gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        <section className="flex min-h-[680px] min-w-0 flex-col overflow-hidden rounded-lg border border-white/10 bg-ink-950/76">
          <header className="flex flex-col gap-3 border-b border-white/10 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-hire-300/25 bg-hire-300/10 text-xs font-bold text-hire-100">XP</span>
                <div className="min-w-0">
                  <h1 className="truncate text-base font-semibold text-white">{xpert.name}</h1>
                  <p className="truncate text-xs text-slate-500">/{xpert.slug}</p>
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <select
                className="h-9 max-w-44 rounded-lg border border-white/10 bg-white/[0.055] px-3 text-xs text-white outline-none"
                disabled={conversationNavigationLocked}
                onChange={(event) => void selectConversation(event.target.value)}
                value={conversationId}
              >
                {conversations.map((item) => (
                  <option className="bg-ink-950" key={item.conversation_id} value={item.conversation_id}>
                    {item.title}
                  </option>
                ))}
              </select>
              <button
                className="inline-flex h-9 items-center rounded-lg border border-white/10 bg-white/[0.04] px-3 text-xs font-semibold text-slate-300 hover:bg-white/[0.08]"
                disabled={conversationNavigationLocked}
                onClick={() => void startConversation()}
                type="button"
              >
                {"+ \u65b0\u4f1a\u8bdd"}
              </button>
              <button
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 text-xs font-semibold text-cyan-100 transition hover:border-cyan-200/45 hover:bg-cyan-300/15"
                onClick={openGoalComposer}
                type="button"
              >
                <span aria-hidden="true" className="text-[10px] font-bold">GL</span>转为长期目标
              </button>
              <Link
                className="inline-flex h-9 items-center gap-2 rounded-lg border border-violet-300/25 bg-violet-300/10 px-3 text-xs font-semibold text-violet-100 transition hover:border-violet-200/45 hover:bg-violet-300/15"
                to={`/agents/automations?xpert_id=${xpert.id}`}
              >
                <span aria-hidden="true" className="text-[10px] font-bold">AT</span>创建自动化
              </Link>
              <select className="h-9 rounded-lg border border-white/10 bg-white/[0.055] px-3 text-xs text-white outline-none" onChange={(event) => setVersion(Number(event.target.value))} value={version}>
                {publishedVersions.map((item) => <option className="bg-ink-950" key={item.version} value={item.version}>v{item.version} · revision {item.draft_revision}</option>)}
              </select>
              <Link className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-slate-300" to={`/agents/studio/${xpert.id}`}>编辑</Link>
            </div>
          </header>

          {showGoalComposer ? (
            <section className="border-b border-cyan-300/20 bg-cyan-300/[0.055] p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-white">创建长期 Goal</h2>
                  <p className="mt-1 text-xs leading-5 text-slate-400">Planner 先生成可编辑计划，审核后才开始执行。</p>
                </div>
                <button aria-label="关闭长期目标创建区" className="rounded-md p-1.5 text-base text-slate-400 hover:bg-white/10 hover:text-white" onClick={() => setShowGoalComposer(false)} type="button">×</button>
              </div>
              <div className="mt-3 grid gap-3 lg:grid-cols-2">
                <label className="text-xs font-semibold text-slate-400">标题<input className="mt-1 h-9 w-full rounded-lg border border-white/10 bg-ink-950/50 px-3 text-sm text-white outline-none focus:border-cyan-300/40" onChange={(event) => setGoalTitle(event.target.value)} value={goalTitle} /></label>
                <label className="text-xs font-semibold text-slate-400">规划智能体<select className="mt-1 h-9 w-full rounded-lg border border-white/10 bg-ink-950/50 px-3 text-sm text-white outline-none" onChange={(event) => setPlannerXpertId(event.target.value)} value={plannerXpertId}>{publishedXperts.map((item) => <option className="bg-ink-950" key={item.id} value={item.id}>{item.name} / v{item.published_version}</option>)}</select></label>
              </div>
              <label className="mt-3 block text-xs font-semibold text-slate-400">目标<textarea className="mt-1 min-h-24 w-full resize-y rounded-lg border border-white/10 bg-ink-950/50 p-3 text-sm leading-6 text-white outline-none focus:border-cyan-300/40" onChange={(event) => setGoalObjective(event.target.value)} value={goalObjective} /></label>
              <div className="mt-3 flex justify-end"><button className="h-9 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-50" disabled={creatingGoal || !goalTitle.trim() || !goalObjective.trim() || !plannerXpertId} onClick={() => void createLongGoal()} type="button">{creatingGoal ? "创建中..." : "创建并规划"}</button></div>
            </section>
          ) : null}

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {messages.length === 0 ? (
              <div className="mx-auto max-w-2xl py-10 text-center">
                <h2 className="text-xl font-semibold text-white">开始与 {xpert.name} 协作</h2>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-400">
                  {openingMessage || xpert.description || "这个智能体将运行已发布的工作流版本。"}
                </p>
                {openingQuestions.length > 0 ? (
                  <div className="mt-6 grid gap-2 sm:grid-cols-2">
                    {openingQuestions.map((starter) => (
                      <button className="rounded-lg border border-white/10 bg-white/[0.04] p-3 text-left text-sm leading-5 text-slate-300 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100" key={starter} onClick={() => void sendMessage(starter)} type="button">{starter}</button>
                    ))}
                  </div>
                ) : null}
                {commandProfiles.length > 0 ? (
                  <div className="mt-5 flex flex-wrap justify-center gap-2">
                    {commandProfiles.flatMap((profile) =>
                      profile.aliases.map((alias) => (
                        <button
                          className="rounded-md border border-violet-300/25 bg-violet-300/[0.08] px-3 py-2 text-xs text-violet-100 transition hover:bg-violet-300/15"
                          key={`${profile.profile_id}-${alias}`}
                          onClick={() => setInput(`/${alias} `)}
                          title={profile.description}
                          type="button"
                        >
                          /{alias} {profile.argument_hint ? `· ${profile.argument_hint}` : ""}
                        </button>
                      )),
                    )}
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="space-y-4">
                {messages.map((message, index) => {
                  const captureSource = xpert
                    ? xpertMessageCaptureSource(message, xpert.id, conversationId)
                    : null;
                  const messageOutputs = message.role === "assistant"
                    ? fileOutputsForRun(fileOutputs, message.source_run_id)
                    : [];
                  return (
                    <article className={`max-w-[86%] rounded-lg border p-3 ${message.role === "user" ? "ml-auto border-hire-300/25 bg-hire-300/10" : "border-white/10 bg-white/[0.045]"}`} key={`${message.role}-${message.message_id ?? index}`}>
                    <p className="text-[10px] font-semibold uppercase text-slate-500">{roleCopy(message.role)}</p>
                    {message.role === "assistant" ? <DataXResultCard content={message.content} /> : null}
                    <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-100">{message.content}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <button
                        className="text-[10px] font-semibold text-cyan-200/75 transition hover:text-cyan-100"
                        onClick={() => void rememberMessage(message)}
                        type="button"
                      >
                        {"\u8bb0\u4f4f\u8fd9\u6761"}
                      </button>
                      {message.role === "assistant" && audioCapabilities?.text_to_speech.enabled ? (
                        <button
                          className="text-[10px] font-semibold text-violet-200/75 transition hover:text-violet-100 disabled:opacity-50"
                          disabled={Boolean(speakingMessageId)}
                          onClick={() => void speakMessage(message, index)}
                          type="button"
                        >
                          {speakingMessageId === (message.message_id || `assistant-${index}`)
                            ? "生成语音中..."
                            : "播放"}
                        </button>
                      ) : null}
                      {captureSource ? (
                        <SkillCreatorCaptureButton
                          enabled={skillCaptureEnabled}
                          source={captureSource}
                        />
                      ) : null}
                    </div>
                    {message.role === "assistant" && message.suggestions?.length ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {message.suggestions.map((suggestion) => (
                          <button
                            className="rounded-md border border-white/10 bg-white/[0.035] px-2.5 py-1.5 text-left text-[11px] text-slate-300 transition hover:border-hire-300/30 hover:text-hire-100"
                            key={suggestion}
                            onClick={() => void sendMessage(suggestion)}
                            type="button"
                          >
                            {suggestion}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {messageOutputs.length > 0 ? (
                      <FileOutputTray
                        onChange={(next) => setFileOutputs((current) =>
                          replaceFileOutputSubset(current, messageOutputs, next))}
                        onReuse={prepareXpertOutputReuse}
                        outputs={messageOutputs}
                        purpose="agent"
                        reuseTargetId={xpert.id}
                        scopeId={xpertOutputScopeId(xpert.id, conversationId)}
                        title="本次运行文件输出"
                      />
                    ) : null}
                    </article>
                  );
                })}
                {unassociatedXpertFileOutputs(fileOutputs, messages).length > 0 ? (
                  <FileOutputTray
                    onChange={(next) => setFileOutputs((current) =>
                      replaceFileOutputSubset(
                        current,
                        unassociatedXpertFileOutputs(current, messages),
                        next,
                      ))}
                    outputs={unassociatedXpertFileOutputs(fileOutputs, messages)}
                    purpose="agent"
                    onReuse={prepareXpertOutputReuse}
                    reuseTargetId={xpert.id}
                    scopeId={xpertOutputScopeId(xpert.id, conversationId)}
                    title="已恢复的文件输出"
                  />
                ) : null}
                {running ? (
                  <div className="max-w-[86%] rounded-lg border border-white/10 bg-white/[0.04] p-3 text-sm text-slate-400">智能体正在执行已发布工作流...</div>
                ) : null}
              </div>
            )}
          </div>

          <footer className="border-t border-white/10 p-4">
            <div className="mb-3">
              <AuthoringProposalNotice
                sourceId={conversationId}
                sourceXpertId={xpert.id}
              />
            </div>
            {taskId ? (
              <div className="mb-3">
                <RuntimeApprovalPanel
                  compact
                  onResolved={() => resumeApprovalExecution()}
                  taskId={taskId}
                  title="智能体等待审批"
                />
              </div>
            ) : null}
            {xpert && conversationId ? (
              <div className="mb-3 overflow-hidden rounded-lg border border-white/10">
                <SandboxWorkspacePanel
                  compact
                  scopeId={`${xpert.id}:${conversationId}`}
                  scopeType="conversation"
                />
                <BrowserSessionPanel
                  compact
                  scopeId={`${xpert.id}:${conversationId}`}
                  scopeType="conversation"
                />
                <ClientToolPanel
                  compact
                  onResolved={() => resumeApprovalExecution()}
                  scopeId={`${xpert.id}:${conversationId}`}
                  scopeType="conversation"
                />
              </div>
            ) : null}
            {error ? <p className="mb-3 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs text-rose-100">{error}</p> : null}
            {selectedFileIds.length > 0 ? (
              <div className="mb-2 flex flex-wrap gap-2">
                {files.filter((file) => selectedFileIds.includes(file.asset_id)).map((file) => (
                  <button
                    aria-label={`移除本轮附件：${file.filename}`}
                    className="rounded-md border border-cyan-300/20 bg-cyan-300/10 px-2 py-1 text-[10px] text-cyan-100"
                    key={file.asset_id}
                    onClick={() => setSelectedFileIds((current) => current.filter((item) => item !== file.asset_id))}
                    title="移除本轮附件，不删除文件"
                    type="button"
                  >
                    {file.filename} {"\u00d7"}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="flex items-end gap-2">
              <input
                accept={fileAccept}
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleFileUpload(file);
                }}
                ref={fileInputRef}
                type="file"
              />
              {fileUploadEnabled ? (
                <button
                  className="h-12 rounded-lg border border-white/10 bg-white/[0.04] px-3 text-xs font-semibold text-slate-300 hover:bg-white/[0.08] disabled:opacity-50"
                  disabled={running || contextLoading || uploading || !conversationId || selectedFileIds.length >= maxFilesPerRun}
                  onClick={() => fileInputRef.current?.click()}
                  title={`允许 ${allowedFileExtensions.join(", ")}，每次最多 ${maxFilesPerRun} 个`}
                  type="button"
                >
                  {uploading ? "\u4e0a\u4f20\u4e2d..." : "\u9644\u4ef6"}
                </button>
              ) : null}
              <input
                accept="audio/*"
                className="hidden"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleAudioTranscription(file);
                }}
                ref={audioInputRef}
                type="file"
              />
              {audioCapabilities?.speech_to_text.enabled ? (
                <button
                  className="h-12 rounded-lg border border-violet-300/20 bg-violet-300/[0.07] px-3 text-xs font-semibold text-violet-100 hover:bg-violet-300/[0.12] disabled:opacity-50"
                  disabled={running || contextLoading || transcribing || !audioCapabilities.gateway_configured}
                  onClick={() => audioInputRef.current?.click()}
                  title={audioCapabilities.gateway_configured ? "上传音频并转写" : "模型网关尚未配置"}
                  type="button"
                >
                  {transcribing ? "转写中..." : "语音"}
                </button>
              ) : null}
              <textarea className="min-h-12 flex-1 resize-none rounded-lg border border-white/10 bg-white/[0.055] px-3 py-3 text-sm leading-6 text-white outline-none focus:border-hire-300/60" disabled={messageInputLocked} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }} placeholder="输入任务，Enter 发送，Shift+Enter 换行" rows={2} value={input} />
              <button className="h-12 rounded-lg bg-hire-300 px-5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50" disabled={messageInputLocked || !input.trim()} onClick={() => void sendMessage()} type="button">{running ? "执行中" : "发送"}</button>
            </div>
          </footer>
        </section>

        <aside className="surface-panel flex min-h-[680px] flex-col rounded-lg p-4">
          <div className="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
            <div>
              <h2 className="text-sm font-semibold text-white">运行轨迹</h2>
              <p className="mt-1 text-xs leading-5 text-slate-400">SSE 节点事件、RunRegistry、checkpoint 与工具审计摘要。</p>
            </div>
            <button className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-slate-300" onClick={() => setShowTrace((current) => !current)} type="button">{showTrace ? "收起" : "展开"}</button>
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg border border-white/10 bg-white/[0.04] p-3"><dt className="text-slate-500">版本</dt><dd className="mt-1 font-semibold text-white">v{version}</dd></div>
            <div className="rounded-lg border border-white/10 bg-white/[0.04] p-3"><dt className="text-slate-500">状态</dt><dd className="mt-1 font-semibold text-white">{running ? "运行中" : trace?.run?.status ?? "待运行"}</dd></div>
            <div className="col-span-2 rounded-lg border border-white/10 bg-white/[0.04] p-3"><dt className="text-slate-500">Run ID</dt><dd className="mt-1 break-all font-mono text-[11px] text-slate-300">{runId || "-"}</dd></div>
          </dl>

          <button
            className="mt-3 w-full rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-xs font-semibold text-cyan-100"
            onClick={() => { setShowContext((current) => !current); setShowTrace(false); }}
            type="button"
          >
            {showContext ? "\u6536\u8d77\u6587\u4ef6\u4e0e\u8bb0\u5fc6" : "\u6587\u4ef6\u4e0e\u8bb0\u5fc6"}
          </button>

          {showContext ? (
            <div className="mt-4 min-h-0 flex-1 space-y-5 overflow-y-auto">
              {summaryRevision > 0 ? (
                <div className="rounded-lg border border-cyan-300/20 bg-cyan-300/[0.06] px-3 py-2 text-[11px] text-cyan-100">
                  会话上下文摘要已生效 · revision {summaryRevision}
                </div>
              ) : null}
              <section>
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-white">{"\u4f1a\u8bdd\u9644\u4ef6"}</h3>
                  <span className="text-[10px] text-slate-500">
                    {selectedFileIds.length} / {maxFilesPerRun} 本次选择
                  </span>
                </div>
                <p className="mt-1 text-[10px] leading-4 text-slate-500">
                  {fileUploadEnabled
                    ? `历史附件默认不选。仅勾选的文件用于下一轮，发送后自动清空选择；允许 ${allowedFileExtensions.join(", ")}，每轮最多 ${maxFilesPerRun} 个。`
                    : "当前发布版本未启用文件能力。"}
                </p>
                <div className="mt-2 space-y-2">
                  {files.length ? files.map((file) => {
                    const selectedForRun = selectedFileIds.includes(file.asset_id);
                    return (
                    <div className="flex items-start gap-2 rounded-lg border border-white/10 bg-white/[0.035] p-2.5" key={file.asset_id}>
                      <input
                        aria-label={`${selectedForRun ? "移除" : "选择"}本轮附件：${file.filename}`}
                        checked={selectedForRun}
                        className="mt-1"
                        disabled={!fileUploadEnabled || running}
                        onChange={(event) => setSelectedFileIds((current) => {
                          if (!event.target.checked) return current.filter((item) => item !== file.asset_id);
                          if (current.includes(file.asset_id) || current.length >= maxFilesPerRun) return current;
                          return [...current, file.asset_id];
                        })}
                        type="checkbox"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[11px] font-semibold text-white">{file.filename}</p>
                        <p className="mt-1 text-[10px] text-slate-500">{file.character_count} chars / {(file.size_bytes / 1024).toFixed(1)} KB</p>
                        <p className={`mt-1 text-[10px] ${selectedForRun ? "text-cyan-200" : "text-slate-500"}`}>
                          {selectedForRun ? "本轮已选" : "本轮未选"}
                        </p>
                      </div>
                      <button
                        className="text-[10px] font-semibold text-rose-200 transition hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
                        disabled={running || Boolean(deletingFileId)}
                        onClick={() => void permanentlyDeleteFile(file)}
                        type="button"
                      >
                        {deletingFileId === file.asset_id ? "删除中..." : "彻底删除"}
                      </button>
                    </div>
                  );
                  }) : <p className="rounded-lg border border-dashed border-white/10 p-3 text-center text-xs text-slate-500">{"\u5c1a\u672a\u4e0a\u4f20\u9644\u4ef6"}</p>}
                </div>
                <div className="mt-3 border-t border-white/10 pt-3">
                  <div className="text-[11px] font-semibold text-white">加入知识库</div>
                  <p className="mt-1 text-[10px] leading-4 text-slate-500">
                    仅发送当前勾选附件，生成候选索引后到知识库页面预览并人工激活。
                  </p>
                  <div className="mt-2 flex gap-2">
                    <select
                      className="min-w-0 flex-1 rounded-md border border-white/10 bg-ink-950/60 px-2 py-1.5 text-[11px] text-white outline-none"
                      onChange={(event) => setKnowledgeTargetId(event.target.value)}
                      value={knowledgeTargetId}
                    >
                      {knowledgeBases.length === 0 ? (
                        <option value="">暂无知识库</option>
                      ) : knowledgeBases.map((kb) => (
                        <option className="bg-ink-950" key={kb.id} value={kb.id}>
                          {kb.name} · {kb.document_count} 文档
                        </option>
                      ))}
                    </select>
                    <button
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-hire-300 px-2.5 py-1.5 text-[11px] font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={!fileUploadEnabled || promotingFiles || selectedFileIds.length === 0 || !knowledgeTargetId}
                      onClick={() => void promoteSelectedFilesToKnowledge()}
                      type="button"
                    >
                      {promotingFiles ? "提交中..." : "创建候选"}
                    </button>
                  </div>
                </div>
              </section>

              <section>
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-white">知识写入待审批</h3>
                  <span className="text-[10px] text-slate-500">{knowledgeProposals.length}</span>
                </div>
                <p className="mt-1 text-[10px] leading-4 text-slate-500">
                  智能体只能提出写入，正式审批统一在对应资料库 Inbox 完成。
                </p>
                <div className="mt-2 space-y-2">
                  {knowledgeProposals.length ? knowledgeProposals.slice(0, 5).map((proposal) => (
                    <Link
                      className="block rounded-lg border border-amber-300/20 bg-amber-300/[0.06] p-2.5 transition hover:bg-amber-300/[0.1]"
                      key={proposal.proposal_id}
                      to={`/rag/${encodeURIComponent(proposal.kb_id)}/inbox?proposal_id=${encodeURIComponent(proposal.proposal_id)}`}
                    >
                      <p className="truncate text-[11px] font-semibold text-amber-100">{proposal.title}</p>
                      <p className="mt-1 text-[10px] text-slate-500">打开 Knowledge Inbox 审批</p>
                    </Link>
                  )) : (
                    <p className="rounded-lg border border-dashed border-white/10 p-3 text-center text-xs text-slate-500">
                      暂无待审批知识写入
                    </p>
                  )}
                </div>
              </section>

              <section>
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-white">Todo 规划</h3>
                  <span className="text-[10px] text-slate-500">
                    {todos.filter((todo) => todo.status !== "archived").length}
                  </span>
                </div>
                <p className="mt-1 text-[10px] leading-4 text-slate-500">
                  当前会话独立保存；Agent 可通过 Todo Runtime 工具同步维护。
                </p>
                <div className="mt-2 flex gap-2">
                  <input
                    className="min-w-0 flex-1 rounded-md border border-white/10 bg-ink-950/60 px-2 py-1.5 text-[11px] text-white outline-none focus:border-indigo-300/50"
                    disabled={todoBusy}
                    onChange={(event) => setNewTodoTitle(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void createTodo();
                    }}
                    placeholder="新增 Todo"
                    value={newTodoTitle}
                  />
                  <button
                    className="rounded-md bg-indigo-300 px-2.5 py-1.5 text-[11px] font-semibold text-ink-950 disabled:opacity-50"
                    disabled={todoBusy || !newTodoTitle.trim()}
                    onClick={() => void createTodo()}
                    type="button"
                  >
                    添加
                  </button>
                </div>
                <div className="mt-2 space-y-2">
                  {todos.filter((todo) => todo.status !== "archived").map((todo, index) => (
                    <div
                      className="flex items-start gap-2 rounded-lg border border-indigo-300/15 bg-indigo-300/[0.05] p-2.5"
                      key={todo.todo_id}
                    >
                      <input
                        checked={todo.status === "completed"}
                        className="mt-1"
                        disabled={todoBusy}
                        onChange={(event) =>
                          void patchTodo(todo, {
                            status: event.target.checked ? "completed" : "pending",
                          })
                        }
                        type="checkbox"
                      />
                      <div className="min-w-0 flex-1">
                        <p className={`text-[11px] font-semibold ${todo.status === "completed" ? "text-slate-500 line-through" : "text-slate-100"}`}>
                          {todo.title}
                        </p>
                        <p className="mt-1 text-[10px] text-slate-500">
                          {todo.status} · rev {todo.revision}
                        </p>
                      </div>
                      <div className="flex items-center gap-1">
                        <button
                          className="px-1 text-[10px] text-slate-400 disabled:opacity-30"
                          disabled={todoBusy || index === 0}
                          onClick={() => void patchTodo(todo, { order: Math.max(0, todo.order - 1) })}
                          title="上移"
                          type="button"
                        >
                          ↑
                        </button>
                        <button
                          className="px-1 text-[10px] text-slate-400 disabled:opacity-30"
                          disabled={
                            todoBusy ||
                            index ===
                              todos.filter((item) => item.status !== "archived")
                                .length -
                                1
                          }
                          onClick={() => void patchTodo(todo, { order: todo.order + 1 })}
                          title="下移"
                          type="button"
                        >
                          ↓
                        </button>
                        <button
                          className="px-1 text-[10px] text-rose-200"
                          disabled={todoBusy}
                          onClick={() => void archiveTodo(todo)}
                          type="button"
                        >
                          归档
                        </button>
                      </div>
                    </div>
                  ))}
                  {todos.filter((todo) => todo.status !== "archived").length === 0 ? (
                    <p className="rounded-lg border border-dashed border-white/10 p-3 text-center text-xs text-slate-500">
                      暂无 Todo
                    </p>
                  ) : null}
                </div>
              </section>

              <section>
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold text-white">Tool Memory</h3>
                  <span className="text-[10px] text-slate-500">{toolMemories.length}</span>
                </div>
                <p className="mt-1 text-[10px] leading-4 text-slate-500">
                  仅保存当前私有会话的受限结果摘要；公共 App 不会跨调用持久化。
                </p>
                <div className="mt-2 space-y-2">
                  {toolMemories.map((memory) => (
                    <div
                      className="rounded-lg border border-emerald-300/15 bg-emerald-300/[0.05] p-2.5"
                      key={memory.memory_id}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-[11px] font-semibold text-emerald-100">
                            {memory.tool_name}
                          </p>
                          <p className="mt-0.5 text-[10px] text-slate-500">{memory.provider || "runtime"}</p>
                        </div>
                        <button
                          className="shrink-0 text-[10px] text-rose-200"
                          onClick={() => void clearToolMemory(memory.memory_id)}
                          type="button"
                        >
                          清除
                        </button>
                      </div>
                      <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-[10px] leading-4 text-slate-300">
                        {memory.summary}
                      </p>
                      {Object.keys(memory.parameter_summary ?? {}).length > 0 ? (
                        <p className="mt-1 truncate font-mono text-[9px] text-slate-600">
                          {JSON.stringify(memory.parameter_summary)}
                        </p>
                      ) : null}
                    </div>
                  ))}
                  {toolMemories.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-white/10 p-3 text-center text-xs text-slate-500">
                      暂无会话级 Tool Memory
                    </p>
                  ) : null}
                </div>
              </section>

              {xpert ? (
                <FileMemoryPanel
                  candidates={memoryCandidates}
                  conversationId={conversationId}
                  memories={memories}
                  onError={setError}
                  onRefresh={refreshContext}
                  xpertId={xpert.id}
                />
              ) : null}
            </div>
          ) : null}

          {showTrace ? (
            <div className="mt-4 min-h-0 flex-1 space-y-4 overflow-y-auto">
              <section>
                <div className="flex items-center justify-between"><h3 className="text-xs font-semibold text-white">即时事件</h3><span className="text-[10px] text-slate-500">{events.length}</span></div>
                <div className="mt-2 space-y-2">
                  {events.length > 0 ? events.slice(-30).map((event, index) => (
                    <div className="rounded-lg border border-white/10 bg-ink-950/55 px-3 py-2" key={`${event.event}-${event.node_id ?? index}-${index}`}>
                      <div className="flex items-center justify-between gap-2"><span className="text-[11px] font-semibold text-hire-100">{event.node_title || event.event}</span><span className="text-[10px] text-slate-600">{event.node_type || event.event}</span></div>
                      <p className="mt-1 line-clamp-3 text-[11px] leading-4 text-slate-400">{eventSummary(event)}</p>
                    </div>
                  )) : <p className="rounded-lg border border-dashed border-white/10 p-3 text-center text-xs text-slate-500">运行后显示节点事件</p>}
                </div>
              </section>

              <section>
                <div className="flex items-center justify-between"><h3 className="text-xs font-semibold text-white">Checkpoint</h3><span className="text-[10px] text-slate-500">{trace?.checkpoints.length ?? 0}</span></div>
                <div className="mt-2 space-y-2">
                  {(trace?.checkpoints ?? []).slice(0, 20).map((checkpoint) => (
                    <div className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2" key={checkpoint.checkpoint_id}>
                      <div className="flex items-center justify-between gap-2"><span className="text-[11px] font-semibold text-white">{checkpoint.title}</span><span className="text-[10px] text-slate-500">{checkpoint.severity}</span></div>
                      <p className="mt-1 line-clamp-2 text-[11px] text-slate-400">{checkpoint.summary || checkpoint.event_type}</p>
                    </div>
                  ))}
                </div>
              </section>

              <section>
                <div className="flex items-center justify-between"><h3 className="text-xs font-semibold text-white">节点子 Run</h3><span className="text-[10px] text-slate-500">{trace?.childRuns.length ?? 0}</span></div>
                <div className="mt-2 space-y-2">
                  {(trace?.childRuns ?? []).map((run) => {
                    const checkpoints = trace?.childCheckpoints[run.run_id] ?? [];
                    return (
                      <div className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2" key={run.run_id}>
                        <div className="flex items-center justify-between gap-2"><span className="text-[11px] font-semibold text-white">{run.title}</span><span className="text-[10px] text-slate-500">{run.status}</span></div>
                        <p className="mt-1 text-[10px] text-slate-500">{run.run_type} · {checkpoints.length} checkpoints</p>
                        {checkpoints.slice(0, 3).map((checkpoint) => (
                          <p className="mt-1 line-clamp-1 text-[10px] text-slate-400" key={checkpoint.checkpoint_id}>{checkpoint.event_type}: {checkpoint.summary || checkpoint.title}</p>
                        ))}
                      </div>
                    );
                  })}
                </div>
              </section>

              <section>
                <div className="flex items-center justify-between"><h3 className="text-xs font-semibold text-white">工具审计</h3><span className="text-[10px] text-slate-500">{trace?.audits.length ?? 0}</span></div>
                <div className="mt-2 space-y-2">
                  {(trace?.audits ?? []).slice(0, 20).map((audit) => (
                    <div className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2" key={audit.record_id}>
                      <div className="flex items-center justify-between gap-2"><span className="text-[11px] font-semibold text-white">{audit.tool_name}</span><span className="text-[10px] text-slate-500">{audit.status}</span></div>
                      <p className="mt-1 text-[10px] text-slate-500">{audit.duration_ms != null ? `${audit.duration_ms.toFixed(0)}ms` : "-"} · {audit.output_length ?? 0} chars</p>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          ) : (
            <p className="mt-4 rounded-lg border border-dashed border-white/10 p-4 text-center text-xs leading-5 text-slate-500">展开后查看最近一次运行的完整摘要。不会展示完整 prompt、工具输出或密钥。</p>
          )}
          {taskId ? <p className="mt-3 break-all text-[10px] text-slate-600">task: {taskId}</p> : null}
        </aside>
      </div>
    </PageContainer>
  );
}
