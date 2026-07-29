import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import {
  DEFAULT_EMBEDDING_MODEL_ID,
  embeddingModelOptions,
  rerankModelOptions,
} from "../data/modelOptions";
import { models } from "../data/models";

type GraphNodeKind =
  | "data_source"
  | "structured_processor"
  | "recursive_chunker"
  | "parent_child_chunker"
  | "embedding"
  | "dual_index"
  | "retrieval"
  | "image_understanding";

interface GraphNodeData extends Record<string, unknown> {
  kind: GraphNodeKind;
  title: string;
  config: Record<string, unknown>;
  enabled: boolean;
  invalid?: boolean;
}

type GraphFlowNode = Node<GraphNodeData, "knowledgeNode">;

interface GraphIssue {
  code: string;
  message: string;
  node_id?: string | null;
  edge_id?: string | null;
}

interface GraphResponse {
  kb_id: string;
  graph_id: string;
  graph_revision: number;
  compiled_draft_version: number;
  updated_at: number;
  valid: boolean;
  issues: GraphIssue[];
  graph: {
    version: string;
    kb_id: string;
    nodes: Array<{
      id: string;
      kind: GraphNodeKind;
      title: string;
      position: { x: number; y: number };
      config: Record<string, unknown>;
      enabled: boolean;
    }>;
    edges: Array<{
      id: string;
      source: string;
      target: string;
      source_port?: string | null;
      target_port?: string | null;
    }>;
  };
}

interface KnowledgeBase {
  id: string;
  name: string;
  document_count: number;
}

interface RagDocument {
  id: string;
  filename: string;
  size: number;
  ingestion_status?: string;
  visual_candidate?: boolean;
}

interface PipelineJob {
  job_id: string;
  status: string;
  graph_revision?: number | null;
  candidate_version: number;
  error?: string | null;
  stages: Array<{ id: string; title: string; status: string; progress: number }>;
  document_results?: Array<{
    vision_status?: string;
    vision_processed_page_count?: number;
    vision_failed_page_count?: number;
    vision_block_count?: number;
  }>;
  created_at: number;
}

interface PipelineVersion {
  version_id: string;
  version: number;
  status: string;
  active: boolean;
  chunk_count: number;
  vision_processed_page_count?: number;
  vision_failed_page_count?: number;
  vision_block_count?: number;
  created_at: number;
}

interface NodePreview {
  node_id: string;
  kind: string;
  preview_type: string;
  item_count: number;
  items: Array<Record<string, unknown>>;
  metadata?: Record<string, unknown>;
  warnings?: string[];
  truncated?: boolean;
}

const PORTS: Record<GraphNodeKind, { input: string | null; output: string | null }> = {
  data_source: { input: null, output: "documents" },
  structured_processor: { input: "documents", output: "blocks" },
  recursive_chunker: { input: "blocks", output: "chunks" },
  parent_child_chunker: { input: "blocks", output: "chunks" },
  embedding: { input: "chunks", output: "embeddings" },
  dual_index: { input: "embeddings", output: "index" },
  retrieval: { input: "index", output: null },
  image_understanding: { input: "documents", output: "documents" },
};

const NODE_META: Record<
  GraphNodeKind,
  { title: string; eyebrow: string; tone: string; stage: string; disabled?: boolean }
> = {
  data_source: { title: "上传文件", eyebrow: "数据源", tone: "border-sky-300/35", stage: "source" },
  structured_processor: { title: "结构化处理器", eyebrow: "处理器", tone: "border-emerald-300/35", stage: "processor" },
  recursive_chunker: { title: "递归分块", eyebrow: "分块器", tone: "border-amber-300/40", stage: "chunker" },
  parent_child_chunker: { title: "父子分块", eyebrow: "分块器", tone: "border-orange-300/40", stage: "chunker" },
  embedding: { title: "Embedding", eyebrow: "索引", tone: "border-cyan-300/35", stage: "embedding" },
  dual_index: { title: "向量 + 全文", eyebrow: "双索引", tone: "border-indigo-300/35", stage: "index" },
  retrieval: { title: "混合检索", eyebrow: "检索", tone: "border-fuchsia-300/35", stage: "retrieval" },
  image_understanding: { title: "图像理解", eyebrow: "视觉预处理", tone: "border-violet-300/40", stage: "vision" },
};

const nodeTypes = { knowledgeNode: KnowledgePipelineNode };
const visionModels = models
  .filter(
    (model) =>
      model.active &&
      model.categories.includes("chat") &&
      model.input_modalities.includes("image"),
  );

function KnowledgePipelineNode({ data, selected }: NodeProps<GraphFlowNode>) {
  const meta = NODE_META[data.kind];
  const ports = PORTS[data.kind];
  return (
    <div
      className={`w-56 rounded-lg border bg-surface-900/95 p-3 shadow-xl backdrop-blur ${
        data.invalid ? "border-rose-300/70" : selected ? "border-hire-200/70 ring-4 ring-hire-300/15" : meta.tone
      } ${meta.disabled ? "opacity-55" : ""}`}
    >
      {ports.input ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-slate-200"
          id={ports.input}
          position={Position.Left}
          type="target"
        />
      ) : null}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase text-slate-400">{meta.eyebrow}</p>
          <h3 className="mt-1 truncate text-sm font-semibold text-white">{data.title || meta.title}</h3>
        </div>
        <span className={`mt-0.5 h-2.5 w-2.5 rounded-full ${data.invalid ? "bg-rose-300" : "bg-emerald-300"}`} />
      </div>
      <p className="mt-3 truncate border-t border-white/10 pt-2 text-[11px] text-slate-400">
        {nodeSummary(data)}
      </p>
      {ports.output ? (
        <Handle
          className="!h-3 !w-3 !border-2 !border-surface-900 !bg-hire-200"
          id={ports.output}
          position={Position.Right}
          type="source"
        />
      ) : null}
    </div>
  );
}

function nodeSummary(data: GraphNodeData) {
  const config = data.config;
  if (data.kind === "structured_processor") return `${String(config.mode || "general")} · ${String(config.failure_policy || "continue_on_error")}`;
  if (data.kind === "recursive_chunker") return `${String(config.chunk_size || 500)} / overlap ${String(config.chunk_overlap || 50)}`;
  if (data.kind === "parent_child_chunker") return `parent ${String(config.parent_chunk_size || 1500)} · child ${String(config.child_chunk_size || 400)}`;
  if (data.kind === "embedding") return String(config.model || "default embedding");
  if (data.kind === "dual_index") return "vector + sqlite fts5";
  if (data.kind === "retrieval") return `${String(config.mode || "hybrid")} · top ${String(config.top_k || 5)}`;
  if (data.kind === "image_understanding") return `${String(config.vision_model_id || "选择视觉模型")} · ${String(config.pdf_page_strategy || "auto")}`;
  return "uploaded files";
}

function apiGraph(nodes: GraphFlowNode[], edges: Edge[], kbId: string) {
  return {
    version: "knowledge-pipeline-graph-v1",
    kb_id: kbId,
    nodes: nodes.map((node) => ({
      id: node.id,
      kind: node.data.kind,
      title: node.data.title,
      position: node.position,
      config: node.data.config,
      enabled: node.data.enabled,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      source_port: edge.sourceHandle,
      target_port: edge.targetHandle,
    })),
  };
}

function flowState(response: GraphResponse) {
  const invalidNodes = new Set(response.issues.map((item) => item.node_id).filter(Boolean));
  const nodes: GraphFlowNode[] = response.graph.nodes.map((node) => ({
    id: node.id,
    type: "knowledgeNode",
    position: node.position,
    data: {
      kind: node.kind,
      title: node.title,
      config: node.config,
      enabled: node.enabled,
      invalid: invalidNodes.has(node.id),
    },
  }));
  const edges: Edge[] = response.graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.source_port ?? undefined,
    targetHandle: edge.target_port ?? undefined,
    animated: true,
    style: { stroke: "rgba(148, 163, 184, 0.65)", strokeWidth: 1.5 },
  }));
  return { nodes, edges };
}

function parseError(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
    return String((detail as { message: string }).message);
  }
  return fallback;
}

function formatDate(value: number) {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

export default function KnowledgePipelineCanvasPage() {
  const { kbId = "" } = useParams();
  const navigate = useNavigate();
  const [nodes, setNodes, onNodesChange] = useNodesState<GraphFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [graphRevision, setGraphRevision] = useState(1);
  const [draftVersion, setDraftVersion] = useState(1);
  const [issues, setIssues] = useState<GraphIssue[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [workbenchTab, setWorkbenchTab] = useState<"config" | "preview" | "run">("config");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [preview, setPreview] = useState<NodePreview | null>(null);
  const [jobs, setJobs] = useState<PipelineJob[]>([]);
  const [versions, setVersions] = useState<PipelineVersion[]>([]);
  const [busy, setBusy] = useState<"load" | "save" | "validate" | "preview" | "execute" | "">("load");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );

  const loadRuns = useCallback(async () => {
    if (!kbId) return;
    try {
      const [jobResponse, versionResponse] = await Promise.all([
        fetch(`/api/rag/pipeline/jobs?kb_id=${encodeURIComponent(kbId)}&limit=20`),
        fetch(`/api/rag/pipeline/versions?kb_id=${encodeURIComponent(kbId)}`),
      ]);
      if (jobResponse.ok) setJobs(((await jobResponse.json()) as { jobs: PipelineJob[] }).jobs || []);
      if (versionResponse.ok) setVersions(((await versionResponse.json()) as { versions: PipelineVersion[] }).versions || []);
    } catch {
      // The canvas remains editable when run history is temporarily unavailable.
    }
  }, [kbId]);

  const loadPage = useCallback(async () => {
    if (!kbId) return;
    setBusy("load");
    setError("");
    try {
      const [kbResponse, documentResponse, graphResponse] = await Promise.all([
        fetch("/api/rag/knowledge_bases"),
        fetch(`/api/rag/knowledge_bases/${encodeURIComponent(kbId)}/documents`),
        fetch(`/api/rag/pipeline/graph?kb_id=${encodeURIComponent(kbId)}`),
      ]);
      if (!graphResponse.ok) throw new Error(parseError(await graphResponse.json().catch(() => null), "知识流水线图加载失败。"));
      const graphPayload = (await graphResponse.json()) as GraphResponse;
      const state = flowState(graphPayload);
      setNodes(state.nodes);
      setEdges(state.edges);
      setGraphRevision(graphPayload.graph_revision);
      setDraftVersion(graphPayload.compiled_draft_version);
      setIssues(graphPayload.issues || []);
      if (state.nodes.length) setSelectedNodeId(state.nodes[0].id);
      if (kbResponse.ok) {
        const list = ((await kbResponse.json()) as { knowledge_bases: KnowledgeBase[] }).knowledge_bases || [];
        setKnowledgeBase(list.find((item) => item.id === kbId) ?? null);
      }
      if (documentResponse.ok) {
        const list = ((await documentResponse.json()) as { documents: RagDocument[] }).documents || [];
        setDocuments(list);
        setSelectedDocumentId((current) => current || list[0]?.id || "");
      }
      await loadRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "知识流水线图加载失败。");
    } finally {
      setBusy("");
    }
  }, [kbId, loadRuns, setEdges, setNodes]);

  useEffect(() => {
    void loadPage();
  }, [loadPage]);

  useEffect(() => {
    const running = jobs.some((job) => job.status === "queued" || job.status === "running");
    if (!running) return;
    const timer = window.setInterval(() => void loadRuns(), 1600);
    return () => window.clearInterval(timer);
  }, [jobs, loadRuns]);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((current) =>
        addEdge(
          {
            ...connection,
            id: `edge_${connection.source}_${connection.target}_${Date.now()}`,
            animated: true,
            style: { stroke: "rgba(148, 163, 184, 0.65)", strokeWidth: 1.5 },
          },
          current,
        ),
      );
    },
    [setEdges],
  );

  function updateSelectedConfig(patch: Record<string, unknown>) {
    if (!selectedNodeId) return;
    setNodes((current) =>
      current.map((node) =>
        node.id === selectedNodeId
          ? { ...node, data: { ...node.data, config: { ...node.data.config, ...patch } } }
          : node,
      ),
    );
  }

  function addOrSwitchNode(kind: GraphNodeKind) {
    const meta = NODE_META[kind];
    if (meta.disabled) return;
    const stageNode = nodes.find((node) => NODE_META[node.data.kind].stage === meta.stage);
    if (stageNode) {
      if (meta.stage === "chunker") {
        setNodes((current) =>
          current.map((node) =>
            node.id === stageNode.id
              ? { ...node, data: { ...node.data, kind, title: meta.title } }
              : node,
          ),
        );
        setSelectedNodeId(stageNode.id);
      }
      setPaletteOpen(false);
      return;
    }
    const id = `${meta.stage}_${Date.now()}`;
    setNodes((current) => [
      ...current,
      {
        id,
        type: "knowledgeNode",
        position: { x: 160 + current.length * 80, y: 160 + current.length * 25 },
        data: { kind, title: meta.title, config: defaultConfig(kind), enabled: true },
      },
    ]);
    if (kind === "image_understanding") {
      const source = nodes.find((node) => node.data.kind === "data_source");
      const processor = nodes.find((node) => node.data.kind === "structured_processor");
      if (source && processor) {
        setEdges((current) => [
          ...current.filter(
            (edge) => !(edge.source === source.id && edge.target === processor.id),
          ),
          {
            id: `edge_${source.id}_${id}`,
            source: source.id,
            target: id,
            sourceHandle: "documents",
            targetHandle: "documents",
            animated: true,
          },
          {
            id: `edge_${id}_${processor.id}`,
            source: id,
            target: processor.id,
            sourceHandle: "documents",
            targetHandle: "documents",
            animated: true,
          },
        ]);
      }
    }
    setSelectedNodeId(id);
    setPaletteOpen(false);
  }

  async function validateGraph() {
    setBusy("validate");
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/rag/pipeline/graph/${encodeURIComponent(kbId)}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph: apiGraph(nodes, edges, kbId) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(parseError(payload, "图校验失败。"));
      const nextIssues = (payload.issues || []) as GraphIssue[];
      setIssues(nextIssues);
      markInvalidNodes(nextIssues);
      setNotice(payload.valid ? "流水线图校验通过。" : `发现 ${nextIssues.length} 个图问题。`);
      return Boolean(payload.valid);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "图校验失败。");
      return false;
    } finally {
      setBusy("");
    }
  }

  function markInvalidNodes(nextIssues: GraphIssue[]) {
    const invalid = new Set(nextIssues.map((item) => item.node_id).filter(Boolean));
    setNodes((current) => current.map((node) => ({ ...node, data: { ...node.data, invalid: invalid.has(node.id) } })));
  }

  async function saveGraph(): Promise<GraphResponse | null> {
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const response = await fetch(`/api/rag/pipeline/graph/${encodeURIComponent(kbId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: graphRevision, graph: apiGraph(nodes, edges, kbId) }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(parseError(payload, response.status === 409 ? "图已被其他编辑更新，请刷新。" : "保存失败。"));
      const graphPayload = payload as GraphResponse;
      const state = flowState(graphPayload);
      setNodes(state.nodes);
      setEdges(state.edges);
      setGraphRevision(graphPayload.graph_revision);
      setDraftVersion(graphPayload.compiled_draft_version);
      setIssues(graphPayload.issues || []);
      setNotice(`已保存图 revision ${graphPayload.graph_revision}，编译为 Draft v${graphPayload.compiled_draft_version}。`);
      return graphPayload;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败。");
      return null;
    } finally {
      setBusy("");
    }
  }

  async function previewNode() {
    if (!selectedNode) return;
    setBusy("preview");
    setError("");
    setPreview(null);
    try {
      const response = await fetch(`/api/rag/pipeline/graph/${encodeURIComponent(kbId)}/preview-node`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          graph: apiGraph(nodes, edges, kbId),
          node_id: selectedNode.id,
          document_id: selectedDocumentId || null,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(parseError(payload, "节点预览失败。"));
      setPreview(payload as NodePreview);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "节点预览失败。");
    } finally {
      setBusy("");
    }
  }

  async function executeGraph() {
    const saved = await saveGraph();
    if (!saved) return;
    setBusy("execute");
    setError("");
    try {
      const response = await fetch(`/api/rag/pipeline/graph/${encodeURIComponent(kbId)}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph_revision: saved.graph_revision, draft_version: saved.compiled_draft_version }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(parseError(payload, "流水线启动失败。"));
      setNotice(`候选版本 v${payload.candidate_version} 已进入执行队列。`);
      setWorkbenchTab("run");
      await loadRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "流水线启动失败。");
    } finally {
      setBusy("");
    }
  }

  async function activateVersion(versionId: string) {
    setError("");
    const response = await fetch(`/api/rag/pipeline/versions/${encodeURIComponent(versionId)}/activate`, { method: "POST" });
    if (!response.ok) {
      setError(parseError(await response.json().catch(() => null), "版本激活失败。"));
      return;
    }
    setNotice("活动知识索引已切换。");
    await loadRuns();
  }

  if (!kbId) {
    navigate("/rag", { replace: true });
    return null;
  }

  return (
    <PageContainer activeResource="prompts" hideSidebar maxWidthClassName="max-w-[1880px]">
      <div className="space-y-4">
        <header className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <Link className="text-xs font-semibold text-hire-100 hover:text-hire-50" to="/rag">返回知识库</Link>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <h1 className="truncate text-2xl font-semibold text-white">{knowledgeBase?.name || "知识流水线"}</h1>
              <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-2.5 py-1 text-[11px] font-semibold text-hire-100">可执行画布 Beta</span>
            </div>
            <p className="mt-1 text-xs text-slate-400">Graph r{graphRevision} · Draft v{draftVersion} · {documents.length} 个数据源文档</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link className="rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-sm font-semibold text-emerald-100 hover:bg-emerald-300/15" to={`/rag/${encodeURIComponent(kbId)}/evaluation`}>质量评估</Link>
            <button className="rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/[0.09] disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void validateGraph()} type="button">校验</button>
            <button className="rounded-lg border border-hire-300/25 bg-hire-300/10 px-3 py-2 text-sm font-semibold text-hire-100 hover:bg-hire-300/15 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void saveGraph()} type="button">保存草稿</button>
            <button className="rounded-lg bg-hire-300 px-4 py-2 text-sm font-bold text-surface-950 hover:bg-hire-200 disabled:opacity-50" disabled={Boolean(busy) || documents.length === 0} onClick={() => void executeGraph()} type="button">执行流水线</button>
          </div>
        </header>

        {error || notice ? (
          <div className={`rounded-lg border px-4 py-3 text-sm ${error ? "border-rose-300/30 bg-rose-400/10 text-rose-100" : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"}`}>{error || notice}</div>
        ) : null}

        <div className="grid min-h-[720px] gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
          <section className="surface-panel relative min-h-[620px] overflow-hidden rounded-lg border border-white/10">
            {busy === "load" ? (
              <div className="flex h-[620px] items-center justify-center text-sm text-slate-400">正在加载流水线图...</div>
            ) : (
              <ReactFlow
                edges={edges}
                fitView
                minZoom={0.35}
                nodeTypes={nodeTypes}
                nodes={nodes}
                onConnect={onConnect}
                onEdgesChange={onEdgesChange}
                onNodeClick={(_, node) => {
                  setSelectedNodeId(node.id);
                  setWorkbenchTab("config");
                }}
                onNodesChange={onNodesChange}
                proOptions={{ hideAttribution: true }}
              >
                <Background color="rgba(148,163,184,0.22)" gap={24} size={1.4} variant={BackgroundVariant.Dots} />
                <MiniMap className="!border !border-white/10 !bg-surface-950/90" maskColor="rgba(2,6,23,0.55)" nodeColor="#67e8f9" />
                <Controls className="workflow-controls" showInteractive={false} />
                <Panel position="top-left">
                  <div className="relative">
                    <button className="rounded-lg border border-white/10 bg-surface-950/95 px-3 py-2 text-sm font-semibold text-slate-100 shadow-xl hover:bg-surface-900" onClick={() => setPaletteOpen((value) => !value)} type="button">节点库</button>
                    {paletteOpen ? (
                      <div className="absolute left-0 top-12 z-30 w-72 rounded-lg border border-white/10 bg-surface-950/98 p-3 shadow-2xl backdrop-blur-xl">
                        <p className="px-2 text-[11px] font-semibold uppercase text-slate-500">知识执行阶段</p>
                        <div className="mt-2 space-y-1">
                          {(Object.keys(NODE_META) as GraphNodeKind[]).map((kind) => {
                            const meta = NODE_META[kind];
                            return (
                              <button className="flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-sm text-slate-200 hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-45" disabled={meta.disabled} key={kind} onClick={() => addOrSwitchNode(kind)} type="button">
                                <span><span className="font-semibold">{meta.title}</span><span className="ml-2 text-xs text-slate-500">{meta.eyebrow}</span></span>
                                <span className="text-xs text-slate-500">{meta.disabled ? "待接入" : "+"}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ) : null}
                  </div>
                </Panel>
              </ReactFlow>
            )}
          </section>

          <aside className="surface-panel min-h-[620px] overflow-hidden rounded-lg border border-white/10">
            <div className="grid grid-cols-3 border-b border-white/10 bg-white/[0.025] p-1">
              {(["config", "preview", "run"] as const).map((tab) => (
                <button className={`rounded-md px-3 py-2 text-xs font-semibold ${workbenchTab === tab ? "bg-white/[0.09] text-white" : "text-slate-400 hover:text-slate-200"}`} key={tab} onClick={() => setWorkbenchTab(tab)} type="button">{{ config: "配置", preview: "预览", run: "运行" }[tab]}</button>
              ))}
            </div>
            <div className="max-h-[760px] overflow-y-auto p-4">
              {workbenchTab === "config" ? (
                selectedNode ? <NodeConfig node={selectedNode} onChange={updateSelectedConfig} /> : <EmptyPanel text="选择一个节点以编辑配置。" />
              ) : null}
              {workbenchTab === "preview" ? (
                <div className="space-y-4">
                  <div>
                    <h2 className="text-sm font-semibold text-white">节点预览</h2>
                    <p className="mt-1 text-xs leading-5 text-slate-400">预览最多返回 20 条截断数据，不写入索引或草稿。</p>
                  </div>
                  {selectedNode && ["image_understanding", "structured_processor", "recursive_chunker", "parent_child_chunker"].includes(selectedNode.data.kind) ? (
                    <label className="block text-xs font-semibold text-slate-300">预览文档
                      <select className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setSelectedDocumentId(event.target.value)} value={selectedDocumentId}>
                        {documents.map((item) => <option key={item.id} value={item.id}>{item.filename}{item.visual_candidate ? " · 视觉" : ""}</option>)}
                      </select>
                    </label>
                  ) : null}
                  <button className="w-full rounded-md border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-50" disabled={!selectedNode || busy === "preview"} onClick={() => void previewNode()} type="button">{busy === "preview" ? "预览中..." : "运行节点预览"}</button>
                  {preview ? <PreviewPanel preview={preview} /> : <EmptyPanel text="运行预览后，这里会显示安全摘要。" />}
                </div>
              ) : null}
              {workbenchTab === "run" ? <RunPanel jobs={jobs} versions={versions} onActivate={(id) => void activateVersion(id)} /> : null}
            </div>
          </aside>
        </div>

        {issues.length ? (
          <section className="rounded-lg border border-rose-300/25 bg-rose-400/10 p-4">
            <h2 className="text-sm font-semibold text-rose-100">图校验问题 ({issues.length})</h2>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {issues.map((issue, index) => <p className="text-xs leading-5 text-rose-100/85" key={`${issue.code}-${index}`}>{issue.node_id ? `${issue.node_id}: ` : ""}{issue.message}</p>)}
            </div>
          </section>
        ) : null}
      </div>
    </PageContainer>
  );
}

function NodeConfig({ node, onChange }: { node: GraphFlowNode; onChange: (patch: Record<string, unknown>) => void }) {
  const { kind, config } = node.data;
  const meta = NODE_META[kind];
  return (
    <div className="space-y-5">
      <div>
        <p className="text-[11px] font-semibold uppercase text-hire-100">{meta.eyebrow}</p>
        <h2 className="mt-1 text-base font-semibold text-white">{node.data.title}</h2>
        <p className="mt-1 font-mono text-[11px] text-slate-500">{node.id}</p>
      </div>
      {kind === "data_source" ? <ReadOnlyField label="来源模式" value="uploaded_files" /> : null}
      {kind === "structured_processor" ? (
        <>
          <SelectField label="处理模式" value={String(config.mode || "general")} options={["general", "qa", "summary"]} onChange={(value) => onChange({ mode: value })} />
          <TextField label="模型 ID" value={String(config.model_id || "")} onChange={(value) => onChange({ model_id: value })} />
          <SelectField label="失败策略" value={String(config.failure_policy || "continue_on_error")} options={["continue_on_error", "strict"]} onChange={(value) => onChange({ failure_policy: value })} />
          <NumberField label="最多生成项" min={1} max={50} value={Number(config.max_generated_items || 20)} onChange={(value) => onChange({ max_generated_items: value })} />
          <ToggleField label="保留表格" checked={config.preserve_tables !== false} onChange={(value) => onChange({ preserve_tables: value })} />
          <ToggleField label="保留代码块" checked={config.preserve_code_blocks !== false} onChange={(value) => onChange({ preserve_code_blocks: value })} />
          <ToggleField label="移除重复页眉页脚" checked={config.remove_repeated_headers_footers !== false} onChange={(value) => onChange({ remove_repeated_headers_footers: value })} />
        </>
      ) : null}
      {kind === "recursive_chunker" ? (
        <>
          <NumberField label="分块大小" min={100} max={4000} value={Number(config.chunk_size || 500)} onChange={(value) => onChange({ chunk_size: value })} />
          <NumberField label="重叠字符" min={0} max={3999} value={Number(config.chunk_overlap || 50)} onChange={(value) => onChange({ chunk_overlap: value })} />
          <SeparatorField label="分段标识符" value={config.separators} onChange={(value) => onChange({ separators: value })} />
        </>
      ) : null}
      {kind === "parent_child_chunker" ? (
        <>
          <NumberField label="父段大小" min={200} max={8000} value={Number(config.parent_chunk_size || 1500)} onChange={(value) => onChange({ parent_chunk_size: value })} />
          <NumberField label="父段重叠" min={0} max={7999} value={Number(config.parent_chunk_overlap || 100)} onChange={(value) => onChange({ parent_chunk_overlap: value })} />
          <NumberField label="子段大小" min={100} max={7999} value={Number(config.child_chunk_size || 400)} onChange={(value) => onChange({ child_chunk_size: value })} />
          <NumberField label="子段重叠" min={0} max={3999} value={Number(config.child_chunk_overlap || 50)} onChange={(value) => onChange({ child_chunk_overlap: value })} />
          <SeparatorField label="父段标识符" value={config.parent_separators} onChange={(value) => onChange({ parent_separators: value })} />
          <SeparatorField label="子段标识符" value={config.child_separators} onChange={(value) => onChange({ child_separators: value })} />
        </>
      ) : null}
      {kind === "embedding" ? (
        <EmbeddingModelField
          value={String(config.model || DEFAULT_EMBEDDING_MODEL_ID)}
          onChange={(value) => onChange({ model: value })}
        />
      ) : null}
      {kind === "dual_index" ? (
        <div className="space-y-2"><ReadOnlyField label="向量索引" value="enabled" /><ReadOnlyField label="全文索引" value="SQLite FTS5 · enabled" /></div>
      ) : null}
      {kind === "retrieval" ? (
        <>
          <SelectField label="检索模式" value={String(config.mode || "hybrid")} options={["vector", "fulltext", "hybrid"]} onChange={(value) => onChange({ mode: value })} />
          <NumberField label="向量权重 (%)" min={0} max={100} value={Math.round(Number(config.vector_weight ?? 0.7) * 100)} onChange={(value) => onChange({ vector_weight: value / 100, fulltext_weight: 1 - value / 100 })} />
          <NumberField label="Top-K" min={1} max={50} value={Number(config.top_k || 5)} onChange={(value) => onChange({ top_k: value })} />
          <NumberField label="Score 阈值 (%)" min={0} max={100} value={Math.round(Number(config.score_threshold || 0) * 100)} onChange={(value) => onChange({ score_threshold: value / 100 })} />
          <ToggleField label="启用 Rerank" checked={Boolean(config.rerank_enabled)} onChange={(value) => onChange({ rerank_enabled: value })} />
          <SelectField label="Rerank Provider" value={String(config.rerank_provider || "auto")} options={["auto", "api", "llm", "none"]} onChange={(value) => onChange({ rerank_provider: value })} />
          {config.rerank_enabled ? (
            <RerankModelField
              value={String(config.rerank_model || "")}
              onChange={(value) => onChange({ rerank_model: value })}
            />
          ) : null}
        </>
      ) : null}
      {kind === "image_understanding" ? (
        <>
          <div className="rounded-md border border-violet-300/20 bg-violet-300/[0.06] p-3 text-xs leading-5 text-violet-100/85">
            视觉内容先转换为 OCR、图片、表格与图表语义块，再进入同一处理器和双索引。
          </div>
          <VisionModelField value={String(config.vision_model_id || "")} onChange={(value) => onChange({ vision_model_id: value })} />
          <SelectField label="PDF 页面策略" value={String(config.pdf_page_strategy || "auto")} options={["auto", "all"]} onChange={(value) => onChange({ pdf_page_strategy: value })} />
          <NumberField label="渲染 DPI" min={72} max={300} value={Number(config.render_dpi || 144)} onChange={(value) => onChange({ render_dpi: value })} />
          <NumberField label="最大页数" min={1} max={200} value={Number(config.max_pages || 100)} onChange={(value) => onChange({ max_pages: value })} />
          <NumberField label="最长图像边" min={512} max={4096} value={Number(config.max_image_edge || 2048)} onChange={(value) => onChange({ max_image_edge: value })} />
          <SelectField label="失败策略" value={String(config.failure_policy || "continue_on_error")} options={["continue_on_error", "strict"]} onChange={(value) => onChange({ failure_policy: value })} />
        </>
      ) : null}
    </div>
  );
}

function defaultConfig(kind: GraphNodeKind): Record<string, unknown> {
  if (kind === "data_source") return { source_mode: "uploaded_files" };
  if (kind === "structured_processor") return { parser: "structured_local_parser", mode: "general", failure_policy: "continue_on_error", max_generated_items: 20, extract_title: true, preserve_tables: true, preserve_code_blocks: true, remove_repeated_headers_footers: true };
  if (kind === "recursive_chunker") return { strategy: "recursive_character", chunk_size: 500, chunk_overlap: 50, separators: ["\n\n", "\n", ". ", " ", ""] };
  if (kind === "parent_child_chunker") return { strategy: "parent_child", parent_chunk_size: 1500, parent_chunk_overlap: 100, child_chunk_size: 400, child_chunk_overlap: 50, parent_separators: ["\n\n", "\n", ". ", " ", ""], child_separators: ["\n\n", "\n", ". ", " ", ""] };
  if (kind === "embedding") return { model: DEFAULT_EMBEDDING_MODEL_ID };
  if (kind === "dual_index") return { vector_enabled: true, fulltext_enabled: true };
  if (kind === "retrieval") return { mode: "hybrid", vector_weight: 0.7, fulltext_weight: 0.3, top_k: 5, score_threshold: 0, candidate_multiplier: 4, rerank_enabled: false, rerank_provider: "auto", rerank_model: "", rerank_top_n: 5 };
  if (kind === "image_understanding") return { enabled: true, provider: "openai_compatible_vlm", vision_model_id: "", pdf_page_strategy: "auto", render_dpi: 144, max_pages: 100, max_image_edge: 2048, failure_policy: "continue_on_error" };
  return {};
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="block text-xs font-semibold text-slate-300">{label}<input className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" onChange={(event) => onChange(event.target.value)} value={value} /></label>;
}

function EmbeddingModelField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const hasCurrentValue = embeddingModelOptions.some(
    (option) => option.id === value,
  );
  return (
    <label className="block text-xs font-semibold text-slate-300">
      Embedding 模型
      <select
        className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        {!hasCurrentValue && value ? (
          <option value={value}>{value}（当前配置）</option>
        ) : null}
        {embeddingModelOptions.map((option) => (
          <option key={option.id} value={option.id}>
            {option.name} · {option.provider}
          </option>
        ))}
      </select>
    </label>
  );
}

function RerankModelField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const hasCurrentValue = rerankModelOptions.some(
    (option) => option.id === value,
  );
  return (
    <label className="block text-xs font-semibold text-slate-300">
      Rerank 模型
      <select
        className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        <option value="">使用服务端默认模型</option>
        {!hasCurrentValue && value ? (
          <option value={value}>{value}（当前配置）</option>
        ) : null}
        {rerankModelOptions.map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
      <span className="mt-1.5 block font-normal leading-5 text-slate-500">
        专用 API 可直接选择；LLM JSON 建议使用服务端默认模型。
      </span>
    </label>
  );
}

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <label className="block text-xs font-semibold text-slate-300">{label}<input className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/50" max={max} min={min} onChange={(event) => onChange(Number(event.target.value))} type="number" value={value} /></label>;
}

function SelectField({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return <label className="block text-xs font-semibold text-slate-300">{label}<select className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => onChange(event.target.value)} value={value}>{options.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>;
}

function VisionModelField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="block text-xs font-semibold text-slate-300">
      视觉模型
      <select className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => onChange(event.target.value)} value={value}>
        <option value="">显式选择支持图像输入的模型</option>
        {visionModels.map((model) => <option key={model.id} value={model.id}>{model.name} · {model.provider}</option>)}
      </select>
    </label>
  );
}

function ToggleField({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center justify-between gap-4 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-xs font-semibold text-slate-300"><span>{label}</span><input checked={checked} className="h-4 w-4 accent-amber-300" onChange={(event) => onChange(event.target.checked)} type="checkbox" /></label>;
}

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md border border-white/10 bg-white/[0.03] px-3 py-2"><p className="text-[10px] font-semibold uppercase text-slate-500">{label}</p><p className="mt-1 text-sm text-slate-200">{value}</p></div>;
}

function SeparatorField({ label, value, onChange }: { label: string; value: unknown; onChange: (value: string[]) => void }) {
  const text = Array.isArray(value) ? value.map((item) => String(item).replaceAll("\n", "\\n")).join(" | ") : "\\n\\n | \\n | . |  |";
  return <label className="block text-xs font-semibold text-slate-300">{label}<textarea className="mt-2 min-h-20 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 font-mono text-xs text-white" onChange={(event) => onChange(event.target.value.split("|").map((item) => item.trim().replaceAll("\\n", "\n")))} value={text} /><span className="mt-1 block font-normal text-slate-500">使用 | 分隔，\\n 表示换行。</span></label>;
}

function EmptyPanel({ text }: { text: string }) {
  return <div className="rounded-md border border-dashed border-white/15 p-5 text-center text-xs leading-5 text-slate-500">{text}</div>;
}

function PreviewPanel({ preview }: { preview: NodePreview }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs"><span className="font-semibold text-white">{preview.preview_type}</span><span className="text-slate-400">{preview.item_count} 项</span></div>
      {preview.warnings?.map((warning) => <p className="rounded-md bg-amber-300/10 px-3 py-2 text-xs text-amber-100" key={warning}>{warning}</p>)}
      <div className="space-y-2">
        {preview.items.slice(0, 20).map((item, index) => (
          <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words rounded-md border border-white/10 bg-surface-950 p-3 text-[11px] leading-5 text-slate-300" key={index}>{JSON.stringify(item, null, 2)}</pre>
        ))}
      </div>
      {preview.truncated ? <p className="text-xs text-slate-500">结果已截断为前 20 项。</p> : null}
    </div>
  );
}

function RunPanel({ jobs, versions, onActivate }: { jobs: PipelineJob[]; versions: PipelineVersion[]; onActivate: (id: string) => void }) {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="text-sm font-semibold text-white">执行任务</h2>
        <div className="mt-3 space-y-3">
          {jobs.length ? jobs.map((job) => (
            <article className="rounded-md border border-white/10 bg-white/[0.03] p-3" key={job.job_id}>
              <div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold text-white">候选 v{job.candidate_version}</span><span className={`text-[11px] font-semibold ${job.status === "failed" ? "text-rose-200" : job.status === "succeeded" ? "text-emerald-200" : "text-amber-200"}`}>{job.status}</span></div>
              <p className="mt-1 text-[10px] text-slate-500">Graph r{job.graph_revision ?? "-"} · {formatDate(job.created_at)}</p>
              <div className="mt-3 space-y-2">{job.stages.map((stage) => <div key={stage.id}><div className="flex justify-between text-[10px] text-slate-400"><span>{stage.title}</span><span>{stage.progress}%</span></div><div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full bg-cyan-300" style={{ width: `${stage.progress}%` }} /></div></div>)}</div>
              {job.document_results?.some((item) => Number(item.vision_processed_page_count || 0) > 0 || Number(item.vision_failed_page_count || 0) > 0) ? (
                <p className="mt-2 text-[11px] text-violet-200/85">
                  视觉页 {job.document_results.reduce((sum, item) => sum + Number(item.vision_processed_page_count || 0), 0)} · 失败 {job.document_results.reduce((sum, item) => sum + Number(item.vision_failed_page_count || 0), 0)} · 块 {job.document_results.reduce((sum, item) => sum + Number(item.vision_block_count || 0), 0)}
                </p>
              ) : null}
              {job.error ? <p className="mt-2 text-xs text-rose-200">{job.error}</p> : null}
            </article>
          )) : <EmptyPanel text="还没有流水线任务。" />}
        </div>
      </section>
      <section>
        <h2 className="text-sm font-semibold text-white">索引版本</h2>
        <div className="mt-3 space-y-2">
          {versions.length ? versions.map((version) => (
            <article className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-white/[0.03] p-3" key={version.version_id}>
              <div><p className="text-xs font-semibold text-white">v{version.version} · {version.chunk_count} chunks</p>{Number(version.vision_processed_page_count || 0) > 0 ? <p className="mt-1 text-[10px] text-violet-200/80">视觉页 {version.vision_processed_page_count} · 块 {version.vision_block_count || 0} · 失败 {version.vision_failed_page_count || 0}</p> : null}<p className="mt-1 text-[10px] text-slate-500">{formatDate(version.created_at)}</p></div>
              {version.active ? <span className="text-[11px] font-semibold text-emerald-200">活动版本</span> : <button className="rounded-md border border-white/10 px-2 py-1 text-[11px] font-semibold text-slate-200 hover:bg-white/[0.06]" onClick={() => onActivate(version.version_id)} type="button">激活</button>}
            </article>
          )) : <EmptyPanel text="候选执行成功后会生成索引版本。" />}
        </div>
      </section>
    </div>
  );
}
