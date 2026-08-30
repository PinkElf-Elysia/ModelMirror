import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type OnConnectEnd,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ShieldCheck, Upload, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DEFAULT_WORKFLOW_AGENT_MODEL_ID } from "../../data/modelOptions";
import { models } from "../../data/models";
import {
  type CodeOperation,
  type NodeRunStatus,
  type SafeTextOperation,
  type WorkflowDefinition,
  type WorkflowEdge,
  type WorkflowMcpArgumentBinding,
  type WorkflowNode,
  type WorkflowNodeData,
  type WorkflowNodeKind,
  type WorkflowValue,
  type WorkflowVariableDeclaration,
  type WorkflowVariablePackBinding,
} from "../../types/workflow";
import {
  fetchRuntimeMiddlewareNodes,
  type RuntimeMiddlewareField,
  type RuntimeMiddlewareNode,
} from "../../types/runtimeMiddleware";
import { type XpertListResponse, type XpertSummary } from "../../types/xpert";
import {
  createXpert,
  toXpertDraftWorkflow,
  updateXpert,
} from "../../utils/xpertApi";
import {
  createWorkflowProject,
  activateWorkflowVersion,
  deactivateWorkflowVersion,
  fetchWorkflowExecutions,
  fetchWorkflowProject,
  publishWorkflowProject,
  rotateWorkflowFormKey,
  rotateWorkflowWebhookKey,
  saveWorkflowProjectDraft,
  type WorkflowDeploymentSummary,
  type WorkflowExecutionSummary,
  type WorkflowFormPublicationSummary,
  type WorkflowRssSubscriptionSummary,
  type WorkflowEmailSubscriptionSummary,
} from "../../utils/workflowDeployments";
import {
  isLegacyStarterWorkflow,
  readStoredWorkflow,
  saveStoredWorkflow,
} from "../../utils/workflowStorage";
import { reconcileRuntimeMiddlewareNodes } from "../../utils/runtimeMiddlewareMigration";
import {
  creatorHandoffMiddlewareConfig,
  isLegacySkillCreatorMiddleware,
  isSkillCreatorMiddleware,
} from "../../utils/skillCreatorMiddleware";
import {
  getSkillCatalogApprovalState,
  reconcileSkillCatalogApprovals,
} from "../../utils/skillCatalogApproval";
import NodePalette from "./NodePalette";
import {
  fetchWorkflowNodeRegistry,
  workflowNodeRegistryFallback,
  type WorkflowNodeContractProjection,
  type WorkflowNodeRegistryResponse,
} from "./workflowNodeRegistry";
import WorkflowTypedDataNodeConfig from "./WorkflowTypedDataNodeConfig";
import WorkflowFailureRoutingConfig from "./WorkflowFailureRoutingConfig";
import WorkflowControlDataNodeConfig from "./WorkflowControlDataNodeConfig";
import WorkflowHttpRequestNodeConfig from "./WorkflowHttpRequestNodeConfig";
import WorkflowFileDataNodeConfig from "./WorkflowFileDataNodeConfig";
import WorkflowDeploymentNodeConfig from "./WorkflowDeploymentNodeConfig";
import WorkflowContentPolicyConfig from "./WorkflowContentPolicyConfig";
import WorkflowNodeCard from "./WorkflowNodeCard";
import WorkflowRun from "./WorkflowRun";
import WorkflowVariableCenter from "./WorkflowVariableCenter";
import WorkflowVariableField from "./WorkflowVariableField";
import {
  ParameterExtractorConfig,
  QuestionClassifierConfig,
} from "./WorkflowTypedAiNodeConfig";
import {
  migrateLegacyParameterExtractor,
  migrateLegacyQuestionClassifier,
  type TypedAiMigrationResult,
} from "./workflowTypedAiMigration";
import {
  isSafeTextV2,
  migrateLegacyCodeNode,
  migrateLegacyTemplateTransform,
} from "./workflowSafeTextMigration";
import {
  isVariablePackV2,
  migrateLegacyVariableAggregator,
} from "./workflowVariablePackMigration";
import {
  isIterationV2,
  migrateLegacyIteration,
} from "./workflowIterationMigration";
import {
  analyzeWorkflowVariables,
  planWorkflowVariableRename,
  type WorkflowVariableRenamePlan,
  type WorkflowVariableValueType,
} from "./workflowVariables";
import {
  analyzeXpertWorkflowConversion,
  INDEPENDENT_DEPLOYMENT_NODE_KINDS,
  validateXpertConversionGraph,
  type XpertConversionAnalysis,
} from "./workflowXpertConversion";
import TrustedSkillSelect, {
  type TrustSelectableSkill,
} from "../skill-trust/TrustedSkillSelect";
import SkillCreatorMiddlewareModePanel from "../skill-creator/SkillCreatorMiddlewareModePanel";
import {
  createDataTableNodeData,
  createTypedCanvasNodeData,
  normalizeRecentlyEnabledNodeData,
} from "./workflowDataTableNodeDefaults";

const nodeTypes = {
  workflowNode: WorkflowNodeCard,
};

// 模块级资源缓存：NodeConfig 每次挂载都触发 fetch（xperts/skills/vision 等），
// 切节点/切面板即重复请求。这里按 URL 缓存原始数据，带 TTL 保新鲜度。
const nodeConfigResourceCache = new Map<
  string,
  { data: unknown; at: number }
>();
const NODE_CONFIG_CACHE_TTL_MS = 60_000;

const PALETTE_NODE_ESTIMATED_WIDTH = 144;
const PALETTE_NODE_ESTIMATED_HEIGHT = 96;
const PALETTE_NODE_GAP = 24;
const PALETTE_NODE_STEP_X = 192;
const PALETTE_NODE_STEP_Y = 136;

export function findAvailablePalettePosition(
  preferred: { x: number; y: number },
  nodes: WorkflowNode[],
): { x: number; y: number } {
  const overlapsExistingNode = (candidate: { x: number; y: number }) =>
    nodes.some((node) => {
      if (!Number.isFinite(node.position.x) || !Number.isFinite(node.position.y)) {
        return false;
      }
      const measuredWidth = node.measured?.width;
      const measuredHeight = node.measured?.height;
      const existingWidth =
        typeof measuredWidth === "number" && Number.isFinite(measuredWidth)
          ? measuredWidth
          : PALETTE_NODE_ESTIMATED_WIDTH;
      const existingHeight =
        typeof measuredHeight === "number" && Number.isFinite(measuredHeight)
          ? measuredHeight
          : PALETTE_NODE_ESTIMATED_HEIGHT;

      return (
        candidate.x < node.position.x + existingWidth + PALETTE_NODE_GAP &&
        candidate.x + PALETTE_NODE_ESTIMATED_WIDTH + PALETTE_NODE_GAP >
          node.position.x &&
        candidate.y < node.position.y + existingHeight + PALETTE_NODE_GAP &&
        candidate.y + PALETTE_NODE_ESTIMATED_HEIGHT + PALETTE_NODE_GAP >
          node.position.y
      );
    });

  if (!overlapsExistingNode(preferred)) return preferred;

  for (let radius = 1; radius <= nodes.length + 1; radius += 1) {
    const offsets = [
      [0, radius],
      [radius, 0],
      [-radius, 0],
      [0, -radius],
      [radius, radius],
      [-radius, radius],
      [radius, -radius],
      [-radius, -radius],
    ];
    for (const [offsetX, offsetY] of offsets) {
      const candidate = {
        x: preferred.x + offsetX * PALETTE_NODE_STEP_X,
        y: preferred.y + offsetY * PALETTE_NODE_STEP_Y,
      };
      if (!overlapsExistingNode(candidate)) return candidate;
    }
  }

  return {
    x: preferred.x,
    y: preferred.y + (nodes.length + 2) * PALETTE_NODE_STEP_Y,
  };
}

export function parseSkillRuntimeIds(value: unknown): string[] {
  return [...new Set(
    String(value ?? "")
      .split(/[,\n]+/)
      .map((item) => item.trim())
      .filter(Boolean),
  )];
}

export function dataMergeConnectionError(
  targetKind: WorkflowNodeKind | undefined,
  targetNodeId: string | null,
  targetHandle: string | null,
  edges: WorkflowEdge[],
): string | null {
  const handle = targetHandle ?? "";
  if (targetKind === "data_merge") {
    if (!new Set(["left", "right"]).has(handle)) {
      return "数据合流必须连接到“左侧数据”或“右侧数据”入口。";
    }
    if (targetNodeId && edges.some((edge) =>
      edge.target === targetNodeId && edge.targetHandle === handle
    )) {
      return `数据合流的${handle === "left" ? "左侧" : "右侧"}入口只能连接一次。`;
    }
    return null;
  }
  if (["left", "right"].includes(handle)) {
    return "左右数据入口只属于数据合流节点。";
  }
  return null;
}

export function errorOutputConnectionError(
  sourceData: WorkflowNodeData | undefined,
  sourceNodeId: string | null,
  sourceHandle: string | null,
  edges: WorkflowEdge[],
): string | null {
  if (sourceHandle !== "error") return null;
  const supportsErrorOutput = sourceData?.failureAction === "error_output" && (
    sourceData.kind === "data_table_query"
    || (
      ["http_request", "knowledge_retrieval"].includes(sourceData.kind)
      && Number(sourceData.contractVersion) === 2
    )
  );
  if (!supportsErrorOutput) {
    return "该节点当前配置没有可用的错误出口。";
  }
  if (sourceNodeId && edges.some((edge) =>
    edge.source === sourceNodeId && edge.sourceHandle === "error"
  )) {
    return "错误出口只能连接一次。";
  }
  return null;
}

export function errorOutputConnectionErrorForNodes(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  sourceNodeId: string | null,
  sourceHandle: string | null,
) {
  return errorOutputConnectionError(
    nodes.find((node) => node.id === sourceNodeId)?.data,
    sourceNodeId,
    sourceHandle,
    edges,
  );
}

export function updateSkillRuntimeIds(
  value: unknown,
  skillId: string,
  action: "add" | "remove",
): string {
  const cleanSkillId = skillId.trim();
  const current = parseSkillRuntimeIds(value);
  if (!cleanSkillId) return current.join(", ");
  if (action === "remove") {
    return current.filter((item) => item !== cleanSkillId).join(", ");
  }
  return [...new Set([...current, cleanSkillId])].join(", ");
}

async function cachedFetchResource<T>(
  key: string,
  fetcher: () => Promise<T>,
): Promise<T> {
  const hit = nodeConfigResourceCache.get(key);
  if (hit && Date.now() - hit.at < NODE_CONFIG_CACHE_TTL_MS) {
    return hit.data as T;
  }
  const data = await fetcher();
  nodeConfigResourceCache.set(key, { data, at: Date.now() });
  return data;
}

/** MiniMap 节点按 kind 大类着色，与 WorkflowNodeCard 的 nodeMeta 配色呼应。 */
function minimapNodeColor(kind: string): string {
  if (["llm", "code", "variable_assign", "template_transform", "variable_aggregator", "parameter_extractor", "data_aggregate", "data_merge", "json_serialize", "json_deserialize"].includes(kind)) {
    return "#22d3ee"; // brand 青
  }
  if (["condition", "multi_route", "terminate_error", "iteration"].includes(kind)) return "#fbbf24"; // amber
  if (["knowledge_retrieval", "knowledge_citation", "document_extractor", "vision_understanding", "knowledge_base"].includes(kind)) {
    return "#2dd4bf"; // teal
  }
  if (["agent", "workflow_agent", "external_xpert", "agent_task", "agent_handoff", "handoff_router", "question_classifier"].includes(kind)) {
    return "#a78bfa"; // violet
  }
  if (["toolset_resource", "plugin_resource", "mcp_tool", "time_tool", "http_request", "list_operation", "data_table_query", "data_table_insert", "data_table_update", "data_table_delete", "runtime_middleware"].includes(kind)) {
    return "#38bdf8"; // sky
  }
  return "#64748b"; // slate（输入输出/人工/注释）
}

function MenuItem({
  children,
  onClick,
  disabled = false,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      className={`w-full px-3 py-2 text-left text-sm transition ${
        disabled
          ? "cursor-not-allowed text-slate-500"
          : "text-slate-200 hover:bg-white/10 hover:text-white"
      }`}
      disabled={disabled}
      onClick={onClick}
      type="button"
    >
      {children}
    </button>
  );
}

/** 拖拽连线松手在空白处时的迷你节点选择器。 */
function QuickNodePicker({
  x,
  y,
  onClose,
  onPick,
}: {
  x: number;
  y: number;
  onClose: () => void;
  onPick: (kind: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [registry, setRegistry] = useState<WorkflowNodeRegistryResponse>(
    workflowNodeRegistryFallback,
  );
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let isMounted = true;
    fetchWorkflowNodeRegistry()
      .then((next) => {
        if (isMounted) {
          setRegistry(next);
          setLoadFailed(false);
        }
      })
      .catch(() => {
        if (isMounted) setLoadFailed(true);
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const items = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return registry.sections
      .flatMap((section) => section.items)
      .filter((item) => item.enabled !== false)
      .filter(
        (item) =>
          !normalized ||
          item.title.toLowerCase().includes(normalized) ||
          item.description.toLowerCase().includes(normalized),
      );
  }, [query, registry.sections]);

  return (
    <>
      <div
        aria-hidden="true"
        className="fixed inset-0 z-40"
        onClick={onClose}
        onContextMenu={(event) => {
          event.preventDefault();
          onClose();
        }}
      />
      <div
        className="fixed z-50 w-72 overflow-hidden rounded-lg border border-white/10 bg-[#101828] shadow-xl shadow-ink-950/60"
        style={{
          left: Math.min(x, window.innerWidth - 300),
          top: Math.min(y, window.innerHeight - 360),
        }}
      >
        <div className="border-b border-white/10 p-3">
          <input
            autoFocus
            className="w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索节点..."
            value={query}
          />
        </div>
        <div className="max-h-64 overflow-y-auto p-1.5">
          {loading ? (
            <p className="px-3 py-2 text-xs text-slate-500">同步节点库...</p>
          ) : null}
          {!loading && items.length === 0 ? (
            <p className="px-3 py-2 text-xs text-slate-500">
              {loadFailed
                ? "节点注册表不可用，暂时不能新增节点。"
                : "没有匹配的节点。"}
            </p>
          ) : null}
          {items.map((item) => (
            <button
              className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition hover:bg-white/10"
              key={item.kind}
              onClick={() => onPick(item.kind)}
              type="button"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-hire-300/25 bg-hire-300/10 text-[11px] font-semibold text-hire-100">
                {item.icon}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold text-white">
                  {item.title}
                </span>
                <span className="block truncate text-xs text-slate-400">
                  {item.description}
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

interface RuntimeMiddlewareDragPayload {
  kind: "runtime_middleware";
  runtimeMiddlewareId?: string;
  runtimeMiddlewareKind?: string;
  title?: string;
  description?: string;
  fields?: RuntimeMiddlewareField[];
  metadata?: Record<string, unknown>;
}

const runtimeMiddlewareFieldTypes = new Set([
  "text",
  "textarea",
  "select",
  "boolean",
  "number",
  "json",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isRuntimeMiddlewareField(value: unknown): value is RuntimeMiddlewareField {
  return (
    isRecord(value) &&
    typeof value.name === "string" &&
    typeof value.label === "string" &&
    typeof value.type === "string" &&
    runtimeMiddlewareFieldTypes.has(value.type)
  );
}

function parseRuntimeMiddlewarePayload(
  raw: string,
): RuntimeMiddlewareDragPayload | null {
  if (!raw.trim().startsWith("{")) {
    return null;
  }

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.kind !== "runtime_middleware") {
      return null;
    }

    return {
      kind: "runtime_middleware",
      runtimeMiddlewareId:
        typeof parsed.runtimeMiddlewareId === "string"
          ? parsed.runtimeMiddlewareId
          : undefined,
      runtimeMiddlewareKind:
        typeof parsed.runtimeMiddlewareKind === "string"
          ? parsed.runtimeMiddlewareKind
          : undefined,
      title: typeof parsed.title === "string" ? parsed.title : undefined,
      description:
        typeof parsed.description === "string" ? parsed.description : undefined,
      fields: Array.isArray(parsed.fields)
        ? parsed.fields.filter(isRuntimeMiddlewareField)
        : [],
      metadata: isRecord(parsed.metadata) ? parsed.metadata : {},
    };
  } catch {
    return null;
  }
}

function createRuntimeMiddlewareConfig(
  fields: RuntimeMiddlewareField[],
  middlewareId: string,
): Record<string, unknown> {
  const config = fields.reduce<Record<string, unknown>>((current, field) => {
    if (field.default !== undefined) {
      current[field.name] = field.default;
    }
    return current;
  }, {});
  if (
    middlewareId === "skill_creator"
    && config.authoring_mode === "creator_handoff"
  ) {
    return creatorHandoffMiddlewareConfig();
  }
  if (middlewareId === "plugin_hooks" && config.hook_mode === "typed_v2") {
    return {
      hook_mode: "typed_v2",
      skill_ids: String(config.skill_ids || ""),
    };
  }
  return config;
}

export function createNodeData(
  kind: WorkflowNodeKind,
  payload?: RuntimeMiddlewareDragPayload,
): WorkflowNodeData {
  const typedCanvasNode = createTypedCanvasNodeData(kind);
  if (typedCanvasNode) {
    return typedCanvasNode;
  }

  const dataTableNode = createDataTableNodeData(kind);
  if (dataTableNode) {
    return dataTableNode;
  }

  if (kind === "input") {
    return {
      kind,
      title: "接待处输入",
      description: "收集用户给流水线的原始任务。",
      variableName: "user_input",
    };
  }

  if (kind === "scheduled_start") {
    return {
      kind,
      title: "定时启动",
      description: "从已发布版本按单次、固定间隔或日历规则启动。",
      scheduleType: "interval",
      intervalSeconds: 30,
      onceAt: new Date(Date.now() + 60_000).toISOString(),
      cronExpression: "*/5 * * * *",
      timezone: "UTC",
      eventVariable: "schedule_event",
    };
  }

  if (kind === "http_event_entry") {
    return {
      kind,
      title: "HTTP 事件入口",
      description: "接收带私有密钥与幂等键的 POST 事件。",
      eventVariable: "http_event",
      acceptedContentType: "both",
      maxBodyBytes: 1_048_576,
      bodyVariable: "request_body",
    };
  }

  if (kind === "form_event_entry") {
    return {
      kind,
      title: "表单提交入口",
      description: "通过模镜同源签名表单接收严格类型化提交。",
      contractVersion: 1,
      formTitle: "需求登记",
      formDescription: "请填写以下信息，我们会在收到后开始处理。",
      submitLabel: "提交登记",
      privacyNotice: "提交内容仅用于本次流程；长期保存需要工作流显式写入数据表。",
      successTitle: "已收到提交",
      successMessage: "你的信息已被安全接收，可以关闭此页面。",
      theme: "light",
      eventVariable: "form_event",
      submissionVariable: "form_submission",
      fields: [
        {
          id: "field_name",
          outputVariable: "name",
          label: "姓名",
          helpText: "用于识别本次登记。",
          placeholder: "请输入姓名",
          type: "short_text",
          required: true,
          options: [],
        },
        {
          id: "field_email",
          outputVariable: "email",
          label: "联系邮箱",
          helpText: "请填写可联系的邮箱地址。",
          placeholder: "name@example.com",
          type: "email",
          required: true,
          options: [],
        },
      ] as unknown as WorkflowNodeData["fields"],
    };
  }

  if (kind === "rss_event_entry") {
    return {
      kind,
      title: "RSS/Atom 订阅入口",
      description: "安全轮询公网 HTTPS 订阅源，并为每个新条目独立启动。",
      contractVersion: 1,
      feedUrl: "https://",
      pollIntervalMinutes: 15,
      eventVariable: "rss_event",
      itemVariable: "rss_item",
    };
  }

  if (kind === "email_event_entry") {
    return {
      kind,
      title: "邮件到达入口",
      description: "只读检查公网 IMAPS INBOX，并为每封新邮件独立启动。",
      contractVersion: 1,
      host: "",
      credentialId: "",
      pollIntervalMinutes: 15,
      eventVariable: "email_event",
      messageVariable: "email_message",
      contentVariable: "email_content",
    };
  }

  if (kind === "failure_event_entry") {
    return {
      kind,
      title: "失败处置入口",
      description: "监听所选已发布工作流的失败并接收脱敏事件。",
      sourceProjectIds: [],
      eventVariable: "failure_event",
    };
  }

  if (kind === "workflow_call_entry") {
    return {
      kind,
      title: "子流程入口",
      description: "接收其他已发布工作流的同步调用。",
      eventVariable: "call_event",
    };
  }

  if (kind === "invoke_workflow") {
    return {
      kind,
      title: "调用已发布工作流",
      description: "同步调用一个已启用的固定版本。",
      targetProjectId: "",
      targetVersion: "",
      inputBindings: {},
      resultVariable: "workflow_result",
      timeoutSeconds: 60,
    };
  }

  if (kind === "suspend_wait") {
    return {
      kind,
      title: "挂起等待",
      description: "持久挂起至指定持续时间或带时区时间点。",
      waitMode: "duration",
      durationSeconds: 60,
      untilTemplate: "",
      untilInputMode: "fixed",
      untilTimezone: "UTC",
      outputVariable: "resume_event",
    };
  }

  if (kind === "http_event_reply") {
    return {
      kind,
      title: "HTTP 事件回执",
      description: "以文本或 JSON 终止 HTTP 事件工作流。",
      statusCode: 200,
      responseBodyType: "json",
      bodyTemplate: '{"ok":true}',
    };
  }

  if (kind === "llm") {
    return {
      kind,
      title: "模型工位",
      description: "调用模型，把上游变量加工成新结果。",
      modelId: DEFAULT_WORKFLOW_AGENT_MODEL_ID,
      prompt: "请基于以下输入给出清晰回答：\n\n{{user_input}}",
      outputVariable: "llm_output",
    };
  }

  if (kind === "condition") {
    return {
      kind,
      title: "类型化条件",
      description: "按明确类型判断变量，分别走“是”或“否”出口。",
      contractVersion: 2,
      inputVariable: "user_input",
      field: "",
      operator: "contains",
      valueType: "text",
      value: "",
    };
  }

  if (kind === "multi_route") {
    return {
      kind,
      title: "多路分派",
      description: "按顺序匹配规则，只执行首个命中的出口。",
      inputVariable: "user_input",
      routes: [
        {
          id: "route_1",
          label: "第一种情况",
          operator: "equals",
          valueType: "text",
          value: "",
        },
        {
          id: "route_2",
          label: "第二种情况",
          operator: "equals",
          valueType: "text",
          value: "",
        },
      ],
    };
  }

  if (kind === "terminate_error") {
    return {
      kind,
      title: "主动终止",
      description: "使用固定安全错误结束当前执行。",
      errorCode: "WORKFLOW_STOPPED",
      message: "工作流已按规则主动终止。",
    };
  }

  if (kind === "code") {
    return {
      kind,
      title: "安全文本加工",
      description: "把变量稳定转换为文本后，执行受控的大小写、替换或拼接操作。",
      contractVersion: 2,
      operation: "upper",
      inputVariable: "user_input",
      outputVariable: "code_output",
      replaceFrom: "",
      replaceTo: "",
      concatValue: "",
    };
  }

  if (kind === "variable_assign") {
    return {
      kind,
      title: "变量赋值",
      description: "把类型化字面量、变量副本或模板文本写入变量。",
      contractVersion: 2,
      outputVariable: "assigned_value",
      valueSource: "template",
      template: "收到：{{user_input}}",
    };
  }

  if (kind === "template_transform") {
    return {
      kind,
      title: "模板转换",
      description: "把变量填入长文本模板，产出报告或结构化文本。",
      template: "## 处理结果\n\n用户输入：{{user_input}}\n",
      outputVariable: "template_output",
    };
  }

  if (kind === "variable_aggregator") {
    return {
      kind,
      title: "变量打包",
      description: "把多个类型化变量深复制到一个 JSON 对象。",
      contractVersion: 2,
      bindings: [
        {
          id: "binding_1",
          sourceVariable: "user_input",
          outputField: "user_input",
        },
      ],
      outputVariable: "packed_variables",
    };
  }

  if (kind === "parameter_extractor") {
    return {
      kind,
      title: "参数提取器",
      description: "调用模型从文本中抽取字段，输出经过 Schema 校验的 JSON。",
      contractVersion: 2,
      inputVariable: "user_input",
      schemaMode: "fields",
      outputShape: "object",
      fields: [
        {
          id: "field_1",
          name: "name",
          description: "客户姓名",
          valueType: "string",
          required: true,
          nullable: false,
        },
        {
          id: "field_2",
          name: "email_address",
          description: "客户邮箱地址",
          valueType: "string",
          required: false,
          nullable: true,
        },
      ],
      repairAttempts: 0,
      modelId: DEFAULT_WORKFLOW_AGENT_MODEL_ID,
      outputVariable: "parameters",
    };
  }

  if (kind === "knowledge_retrieval") {
    return {
      kind,
      title: "知识检索",
      description: "检索指定知识库的活动版本。",
      contractVersion: 2,
      knowledgeBaseId: "",
      queryVariable: "user_input",
      top_k: "5",
      returnMode: "result",
      outputVariable: "knowledge_result",
      failureAction: "stop",
    };
  }

  if (kind === "knowledge_write_proposal") {
    return {
      kind,
      title: "知识写入提议",
      description: "把确定性文本提交到 Knowledge Inbox，等待人工审批。",
      contractVersion: 1,
      knowledgeBaseId: "",
      titleTemplate: "知识更新提议",
      contentVariable: "user_input",
      tags: [],
      outputVariable: "knowledge_proposal",
    };
  }

  if (kind === "knowledge_citation") {
    return {
      kind,
      title: "知识引用锚点",
      description: "把本地 RAG 检索结果转换为 CitationAnchor JSON。",
      queryVariable: "user_input",
      knowledgeBaseId: "",
      top_k: "4",
      outputVariable: "citation_anchors_json",
    };
  }

  if (kind === "document_extractor") {
    return {
      kind,
      title: "内容解析",
      description: "把安全 HTTP 响应或明确共享的文件解析为结构化内容。",
      contractVersion: 3,
      sourceMode: "http_response",
      inputVariable: "http_response",
      format: "auto",
      outputMode: "structured",
      outputVariable: "parsed_content",
    };
  }

  if (kind === "vision_understanding") {
    return {
      kind,
      title: "视觉理解",
      description: "理解显式共享的图片或扫描 PDF，并输出类型化视觉结果。",
      assetIdVariable: "selected_file_asset_id",
      visionModelId: "",
      pdfPageStrategy: "auto",
      maxPages: 100,
      maxImageEdge: 2048,
      failurePolicy: "continue_on_error",
      outputVariable: "vision_result",
    };
  }

  if (kind === "human_intervention") {
    return {
      kind,
      title: "人工确认",
      description: "暂停执行，等待人工输入或批准后从断点继续。",
      contractVersion: 2,
      interactionMode: "input",
      prompt: "请确认或补充这段内容：\n\n{{user_input}}",
      outputVariable: "human_input",
      timeoutSeconds: 3600,
    };
  }

  if (kind === "question_classifier") {
    return {
      kind,
      title: "问题分类",
      description: "按稳定分类出口分派问题，可选模型兜底。",
      contractVersion: 2,
      inputVariable: "user_input",
      classificationMode: "rules_only",
      categoriesV2: [
        {
          id: "category_1",
          label: "投诉与退款",
          description: "投诉、退款或服务不满",
          keywords: ["投诉", "退款", "不满意"],
          matchMode: "contains_any",
        },
        {
          id: "category_2",
          label: "产品咨询",
          description: "产品信息、使用方法或购买咨询",
          keywords: ["咨询", "如何", "怎么"],
          matchMode: "contains_any",
        },
      ],
      outputVariable: "category",
      defaultLabel: "未分类",
      caseSensitive: false,
      modelId: "",
    };
  }

  if (kind === "agent") {
    return {
      kind,
      title: "Agent",
      description: "模型驱动的任务执行节点。",
      agentMode: "tool_first",
      agentStrategy: "auto",
      instruction: "{{user_input}}",
      modelId: "",
      toolNames: "",
      outputVariable: "agent_output",
      maxIterations: "5",
      temperature: "0.7",
      promptSuffix: "",
      disableOutput: "false",
      enableFileUnderstanding: "false",
      parallelToolCalls: "false",
      maxToolConcurrency: "2",
      maxToolCalls: "12",
      maxToolDepth: "4",
      exceptionHandling: "none",
      outputSchemaMode: "default",
      outputSchemaJson: "",
      memoryReadEnabled: "false",
      memoryReadScope: "both",
      memoryWriteEnabled: "false",
      memoryWriteTarget: "xpert",
    };
  }

  if (kind === "workflow_agent") {
    return {
      kind,
      title: "工作流智能体",
      description: "模型驱动的单步智能体执行节点。",
      agentName: "workflow-agent",
      modelId: DEFAULT_WORKFLOW_AGENT_MODEL_ID,
      rolePrompt: "你是负责执行当前工作流步骤的智能体，请直接输出结果。",
      taskInput: "{{user_input}}",
      toolMode: "none",
      agentStrategy: "auto",
      toolNames: "",
      maxIterations: "5",
      promptSuffix: "",
      outputVariable: "agent_output",
      disableOutput: "false",
      enableFileUnderstanding: "false",
      parallelToolCalls: "false",
      maxToolConcurrency: "2",
      maxToolCalls: "12",
      maxToolDepth: "4",
      retryOnFailure: "false",
      fallbackModelId: "",
      exceptionHandling: "none",
      outputSchemaMode: "default",
      outputSchemaJson: "",
      memoryReadEnabled: "false",
      memoryReadScope: "both",
      memoryWriteEnabled: "false",
      memoryWriteTarget: "xpert",
      knowledgeReadEnabled: "false",
      knowledgeWriteEnabled: "false",
      knowledgeBaseIds: "",
      nodeParametersJson: "[]",
    };
  }

  if (kind === "external_xpert") {
    return {
      kind,
      title: "外部智能体",
      description: "将已发布智能体作为当前智能体的同步协作者工具。",
      xpertId: "",
      toolName: "external_expert",
      versionPolicy: "current_published",
      pinnedVersion: "",
    };
  }

  if (kind === "knowledge_base") {
    return {
      kind,
      title: "知识库资源",
      description: "将一个知识库绑定到当前智能体的只读知识工具。",
      knowledgeBaseId: "",
      topK: "5",
      scoreThreshold: "0",
    };
  }

  if (kind === "toolset_resource") {
    return {
      kind,
      title: "MCP Toolset",
      description: "将已发布的 MCP Toolset 版本绑定到当前智能体。",
      toolsetId: "",
      versionPolicy: "current_published",
      pinnedVersion: "",
    };
  }

  if (kind === "plugin_resource") {
    return {
      kind,
      title: "Plugin 资源",
      description: "将已发布 Plugin 的固定资源包绑定到当前工作流智能体。",
      pluginId: "",
      versionPolicy: "latest",
      pinnedVersion: "",
    };
  }

  if (kind === "agent_task") {
    return {
      kind,
      title: "创建协作任务",
      description: "把工作内容登记为可追踪任务，供人工或智能体接手。",
      contractVersion: 2,
      taskTitle: "处理用户请求",
      taskInput: "{{user_input}}",
      assignedAgent: "review-agent",
      outputVariable: "agent_task_receipt",
    };
  }

  if (kind === "agent_handoff") {
    return {
      kind,
      title: "移交已有任务",
      description: "把上游任务凭证交给人工队列或固定版本智能体。",
      contractVersion: 2,
      taskVariable: "agent_task_receipt",
      taskValueKind: "receipt",
      sourceAgent: "workflow",
      targetMode: "inbox",
      inboxTarget: "review-agent",
      targetXpertId: "",
      targetVersion: 0,
      waitForCompletion: false,
      resultVariable: "handoff_result",
      timeoutSeconds: 120,
      reason: "请审核并处理这项任务。",
      outputVariable: "handoff_receipt",
    };
  }

  if (kind === "handoff_router") {
    return {
      kind,
      title: "创建并移交任务",
      description: "将上游结果直接包装成任务并交给人工或固定版本智能体。",
      contractVersion: 2,
      sourceVariable: "agent_output",
      taskTitle: "来自工作流智能体的任务",
      targetMode: "inbox",
      inboxTarget: "review-agent",
      targetXpertId: "",
      targetVersion: 0,
      waitForCompletion: false,
      resultVariable: "handoff_result",
      timeoutSeconds: 120,
      sourceAgent: "workflow-agent",
      reasonTemplate: "请审核并处理上游结果。",
      outputVariable: "handoff_receipt",
    };
  }

  if (kind === "mcp_tool") {
    return {
      kind,
      title: "MCP Tool",
      description: "按服务器、工具和 Schema 指纹调用已注册的 MCP 工具。",
      contractVersion: 2,
      serverId: "",
      toolName: "",
      inputSchemaChecksum: "",
      argumentMode: "fields",
      argumentBindings: [],
      argumentsVariable: "mcp_arguments",
      outputVariable: "mcp_output",
    };
  }

  if (kind === "time_tool") {
    return {
      kind,
      title: "时间工具",
      description: "按时区获取、转换和计算日期时间。",
      contractVersion: 2,
      operation: "now",
      timezone: "UTC",
      inputVariable: "source_time",
      rightVariable: "compare_time",
      amount: 1,
      unit: "days",
      formatString: "%Y-%m-%d %H:%M:%S",
      outputVariable: "current_time",
    };
  }

  if (kind === "http_request") {
    return {
      kind,
      title: "安全 HTTP 请求",
      description: "调用公网 HTTP 接口，并把安全结构化响应写入变量。",
      contractVersion: 2,
      url: "https://example.com",
      method: "GET",
      queryItems: [],
      headerItems: [],
      bodyMode: "none",
      formFields: [],
      authType: "none",
      credentialId: "",
      apiKeyLocation: "header",
      apiKeyName: "X-API-Key",
      timeoutSeconds: 30,
      redirectLimit: 0,
      responseLimitBytes: 1_048_576,
      responseMode: "auto",
      statusPolicy: "success_only",
      outputVariable: "http_response",
      failureAction: "stop",
    };
  }

  if (kind === "list_operation") {
    return {
      kind,
      title: "列表操作",
      description: "对类型化数组执行列表转换，并兼容旧文本列表。",
      inputVariable: "user_input",
      operator: "length",
      joinSeparator: " / ",
      filterMode: "all",
      filterRules: [
        {
          field: "",
          operator: "equals",
          valueType: "text",
          value: "",
        },
      ],
      sortKeys: [{ field: "", direction: "asc", nulls: "last" }],
      deduplicateFields: [],
      count: 10,
      startIndex: 0,
      endIndex: 10,
      outputVariable: "list_output",
    };
  }

  if (kind === "data_aggregate") {
    return {
      kind,
      title: "数据聚合",
      description: "对对象数组分组并计算类型化度量。",
      inputVariable: "user_input",
      outputVariable: "aggregate_result",
      groupByFields: [],
      measures: [{ outputField: "row_count", operation: "count" }],
    };
  }

  if (kind === "data_merge") {
    return {
      kind,
      title: "数据合流",
      description: "等待左右两条路径都到达，再拼接数组或按键一对一合并。",
      contractVersion: 1,
      mergeMode: "append",
      leftVariable: "left_rows",
      rightVariable: "right_rows",
      outputVariable: "merged_rows",
      keyFields: [],
    };
  }

  if (kind === "dataset_compare") {
    return {
      kind,
      title: "数据集对照",
      description: "按稳定键对照两份对象数组，识别新增、删除、变化和未变化记录。",
      leftVariable: "before_rows",
      rightVariable: "after_rows",
      keyFields: ["id"],
      includeUnchanged: false,
      outputVariable: "dataset_difference",
    };
  }

  if (kind === "object_transform") {
    return {
      kind,
      title: "对象转换",
      description: "按顺序整理 JSON 对象的顶层字段。",
      inputVariable: "source_object",
      outputVariable: "transformed_object",
      operations: [
        {
          id: "operation_1",
          operation: "set_default",
          targetField: "status",
          binding: { source: "literal", valueType: "text", value: "pending" },
        },
      ],
    };
  }

  if (kind === "file_output") {
    return {
      kind,
      title: "生成文件",
      description: "把变量安全生成可预览、下载和复用的文件。",
      inputVariable: "report_content",
      outputVariable: "generated_file",
      format: "markdown",
      filenameTemplate: "workflow-report",
      titleTemplate: "工作流报告",
      columns: [{ id: "column_1", field: "id", label: "ID" }],
    };
  }

  if (kind === "iteration") {
    return {
      kind,
      title: "批量处理",
      description: "逐项渲染安全模板，或按顺序调用固定版本子流程。",
      contractVersion: 2,
      mode: "template_map",
      inputVariable: "batch_items",
      itemVariable: "item",
      indexVariable: "item_index",
      itemTemplate: "处理：{{item}}",
      outputVariable: "iteration_output",
      targetProjectId: "",
      targetVersion: 0,
      inputBindings: {},
      timeoutSeconds: 60,
    };
  }

  if (kind === "runtime_middleware") {
    const fields = payload?.fields ?? [];
    const middlewareId = payload?.runtimeMiddlewareId ?? "unknown";
    const middlewareKind =
      payload?.runtimeMiddlewareKind ?? "runtime_middleware.unknown";
    return {
      kind,
      title: payload?.title ?? "中间件节点",
      description: payload?.description ?? "运行时中间件节点。",
      runtimeMiddlewareId: middlewareId,
      runtimeMiddlewareKind: middlewareKind,
      runtimeMiddlewareFields: fields,
      runtimeMiddlewareMetadata: payload?.metadata ?? {},
      runtimeMiddlewareConfig: createRuntimeMiddlewareConfig(fields, middlewareId),
      middlewarePriority: "100",
    };
  }

  if (kind === "output") {
    return {
      kind,
      title: "最终交付",
      description: "把指定变量作为工作流结果交付。",
      outputVariable: "llm_output",
    };
  }

  throw new Error(`不支持的工作流节点类型：${kind}`);
}

function createNode(
  kind: WorkflowNodeKind,
  x: number,
  y: number,
  payload?: RuntimeMiddlewareDragPayload,
): WorkflowNode {
  return {
    id: `${kind}-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`,
    type: "workflowNode",
    position: { x, y },
    data: createNodeData(kind, payload),
  };
}

export function migrateDocumentExtractorFileToV3(
  data: WorkflowNodeData,
): { data?: WorkflowNodeData; reason?: string } {
  const contractVersion = Number(data.contractVersion ?? 0);
  if (
    data.kind !== "document_extractor"
    || Boolean(data.sourcePathVariable)
    || ![0, 2].includes(contractVersion)
  ) {
    return { reason: "只有 V2 或旧安全文件资产配置可以直接升级。" };
  }
  const assetIdVariable = String(data.assetIdVariable ?? "").trim();
  const outputVariable = String(data.outputVariable ?? "").trim();
  const variablePattern = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
  if (!variablePattern.test(assetIdVariable) || !variablePattern.test(outputVariable)) {
    return { reason: "请先补齐合法的文件资产变量和输出变量。" };
  }
  if (assetIdVariable === outputVariable) {
    return { reason: "输出变量不能覆盖文件资产变量。" };
  }
  return {
    data: {
      ...data,
      title: "内容解析",
      description: "把安全 HTTP 响应或明确共享的文件解析为结构化内容。",
      contractVersion: 3,
      sourceMode: "file_asset",
      format: "auto",
      outputMode: "text",
      inputVariable: undefined,
    },
  };
}

function initialDefinition(workflowId: string): WorkflowDefinition {
  const inputNode: WorkflowNode = {
    id: "input-1",
    type: "workflowNode",
    position: { x: 0, y: 80 },
    data: createNodeData("input"),
  };
  const workflowAgentNode: WorkflowNode = {
    id: "workflow-agent-1",
    type: "workflowNode",
    position: { x: 340, y: 80 },
    data: createNodeData("workflow_agent"),
  };
  const outputNode: WorkflowNode = {
    id: "output-1",
    type: "workflowNode",
    position: { x: 700, y: 80 },
    data: {
      ...createNodeData("output"),
      outputVariable: "agent_output",
    },
  };

  return {
    id: workflowId,
    title: "新建 AI 流水线",
    nodes: [inputNode, workflowAgentNode, outputNode],
    edges: [
      {
        id: "edge-input-workflow-agent",
        source: inputNode.id,
        target: workflowAgentNode.id,
      },
      {
        id: "edge-workflow-agent-output",
        source: workflowAgentNode.id,
        target: outputNode.id,
      },
    ],
    updatedAt: new Date().toISOString(),
  };
}

export function normalizeWorkflowNodePositions(
  nodes: WorkflowNode[],
): WorkflowNode[] {
  return nodes.map((node, index) => {
    const position = node.position as
      | { x?: unknown; y?: unknown }
      | null
      | undefined;
    const validPosition =
      typeof position?.x === "number" &&
      Number.isFinite(position.x) &&
      typeof position.y === "number" &&
      Number.isFinite(position.y);
    if (validPosition && node.type === "workflowNode") {
      return node;
    }
    return {
      ...node,
      type: "workflowNode",
      position: validPosition
        ? { x: position.x as number, y: position.y as number }
        : {
            x: (index % 4) * 320,
            y: Math.floor(index / 4) * 180 + 80,
          },
    };
  });
}

function cloneDefinition(definition: WorkflowDefinition): WorkflowDefinition {
  return {
    ...definition,
    nodes: normalizeWorkflowNodePositions(definition.nodes).map((node) => ({
      ...node,
      position: { ...node.position },
      data: normalizeRecentlyEnabledNodeData({ ...node.data }),
    })),
    edges: definition.edges.map((edge) => ({ ...edge })),
    variables: definition.variables?.map((variable) => ({
      ...variable,
      defaultValue:
        variable.defaultValue === undefined
          ? undefined
          : structuredClone(variable.defaultValue),
    })),
  };
}

function loadDefinition(
  workflowId: string,
  controlledDefinition?: WorkflowDefinition,
) {
  if (controlledDefinition) {
    return cloneDefinition(controlledDefinition);
  }
  const storedDefinition = readStoredWorkflow(workflowId);
  if (!storedDefinition) {
    return initialDefinition(workflowId);
  }
  if (isLegacyStarterWorkflow(storedDefinition)) {
    const upgradedDefinition = initialDefinition(workflowId);
    saveStoredWorkflow(upgradedDefinition);
    return upgradedDefinition;
  }
  return cloneDefinition(storedDefinition);
}

function LocalDraftRecoveryDialog({
  draft,
  onRestore,
  onStartBlank,
}: {
  draft: WorkflowDefinition;
  onRestore: () => void;
  onStartBlank: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
    return () => {
      if (typeof dialog.close === "function" && dialog.open) {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    };
  }, []);

  return (
    <dialog
      aria-describedby="local-draft-recovery-description"
      aria-labelledby="local-draft-recovery-title"
      className="m-auto w-[calc(100%-2rem)] max-w-xl rounded-xl border border-cyan-300/25 bg-[#0d1728] p-0 text-left text-slate-100 shadow-lg backdrop:bg-slate-950/90 backdrop:backdrop-blur-sm"
      onCancel={(event) => event.preventDefault()}
      ref={dialogRef}
    >
      <section className="p-5 sm:p-6">
        <p className="text-xs font-semibold text-cyan-200">本地草稿恢复</p>
        <h2
          className="mt-2 text-lg font-semibold text-white"
          id="local-draft-recovery-title"
        >
          发现一个未发布的本地草稿
        </h2>
        <p
          className="mt-2 text-sm leading-6 text-slate-300"
          id="local-draft-recovery-description"
        >
          “{draft.title || "未命名工作流"}”包含 {draft.nodes.length} 个节点和 {draft.edges.length} 条连线。请选择如何进入画布，避免把旧流程当成空白工作流运行。
        </p>
        <p className="mt-3 text-xs leading-5 text-slate-400">
          使用默认工作流不会立即删除旧草稿；保存或转换此工作流后，本地副本才会被替换。
        </p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            className="min-h-11 rounded-lg border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-cyan-200/35 hover:bg-cyan-300/10 hover:text-cyan-100"
            onClick={onRestore}
            type="button"
          >
            恢复本地草稿
          </button>
          <button
            autoFocus
            className="min-h-11 rounded-lg bg-cyan-300 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 active:scale-[0.98]"
            onClick={onStartBlank}
            type="button"
          >
            使用默认工作流新建
          </button>
        </div>
      </section>
    </dialog>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-300">{label}</span>
      <div className="mt-2">{children}</div>
    </label>
  );
}

function textInputClass() {
  return "modelmirror-form-control w-full rounded-lg border border-white/10 bg-[#0f1728] px-3 py-2 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10";
}

function runtimeMiddlewareFieldValue(
  config: Record<string, unknown> | undefined,
  field: RuntimeMiddlewareField,
): unknown {
  if (config && Object.prototype.hasOwnProperty.call(config, field.name)) {
    return config[field.name];
  }
  return field.default;
}

function runtimeMiddlewareStringValue(
  config: Record<string, unknown> | undefined,
  field: RuntimeMiddlewareField,
): string {
  const value = runtimeMiddlewareFieldValue(config, field);
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value, null, 2);
}

function runtimeMiddlewareBooleanValue(
  config: Record<string, unknown> | undefined,
  field: RuntimeMiddlewareField,
): boolean {
  const value = runtimeMiddlewareFieldValue(config, field);
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    return value.toLowerCase() === "true";
  }
  return false;
}

interface RegistryToolOption {
  name: string;
  server_id: string;
  description?: string;
  input_schema: Record<string, unknown>;
  schema_checksum: string;
}

export function workflowTypesForMcpSchema(
  schema: unknown,
): WorkflowVariableValueType[] {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    return ["unknown"];
  }
  const record = schema as Record<string, unknown>;
  const variants = Array.isArray(record.anyOf)
    ? record.anyOf
    : Array.isArray(record.oneOf)
      ? record.oneOf
      : [record];
  const accepted = new Set<WorkflowVariableValueType>();
  variants.forEach((variant) => {
    if (!variant || typeof variant !== "object" || Array.isArray(variant)) return;
    const rawType = (variant as Record<string, unknown>).type;
    const types = Array.isArray(rawType) ? rawType : [rawType];
    types.forEach((type) => {
      if (type === "string") accepted.add("text");
      else if (type === "number" || type === "integer") accepted.add("number");
      else if (type === "boolean") accepted.add("boolean");
      else if (type === "object" || type === "array" || type === "null") accepted.add("json");
    });
  });
  return accepted.size ? [...accepted] : ["unknown"];
}

export function reconcileMcpArgumentBindings(
  inputSchema: Record<string, unknown>,
  currentBindings: WorkflowMcpArgumentBinding[] = [],
): {
  argumentMode: "fields" | "object_variable";
  argumentBindings: WorkflowMcpArgumentBinding[];
} {
  const properties = inputSchema.properties;
  const fieldsSupported =
    (inputSchema.type === undefined || inputSchema.type === "object")
    && properties !== null
    && typeof properties === "object"
    && !Array.isArray(properties);
  if (!fieldsSupported) {
    return { argumentMode: "object_variable", argumentBindings: [] };
  }

  const currentByName = new Map(
    currentBindings.map((binding) => [binding.name, binding]),
  );
  const propertyEntries = Object.entries(properties as Record<string, unknown>);
  const retainedIds = new Set(
    propertyEntries
      .map(([name]) => currentByName.get(name)?.id)
      .filter((id): id is string => Boolean(id)),
  );
  let nextId = 1;
  const createId = () => {
    while (retainedIds.has(`argument_${nextId}`)) nextId += 1;
    const id = `argument_${nextId}`;
    retainedIds.add(id);
    nextId += 1;
    return id;
  };

  return {
    argumentMode: "fields",
    argumentBindings: propertyEntries.map(([name, schema]) => {
      const current = currentByName.get(name);
      if (current) return current;
      return {
        id: createId(),
        name,
        binding: {
          source: "literal" as const,
          value: defaultLiteralForSchema(schema),
        },
      };
    }),
  };
}

function isRegistryToolOption(value: unknown): value is RegistryToolOption {
  return (
    typeof value === "object" &&
    value !== null &&
    "name" in value &&
    typeof (value as { name?: unknown }).name === "string" &&
    "server_id" in value &&
    typeof (value as { server_id?: unknown }).server_id === "string" &&
    "schema_checksum" in value &&
    typeof (value as { schema_checksum?: unknown }).schema_checksum === "string" &&
    "input_schema" in value &&
    typeof (value as { input_schema?: unknown }).input_schema === "object" &&
    (value as { input_schema?: unknown }).input_schema !== null
  );
}

function literalKind(value: WorkflowValue | undefined) {
  if (value === null) return "null";
  if (Array.isArray(value) || (typeof value === "object" && value !== null)) return "json";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "text";
}

function defaultLiteralForSchema(schema: unknown): WorkflowValue {
  if (!schema || typeof schema !== "object") return "";
  const type = (schema as { type?: unknown }).type;
  if (type === "number" || type === "integer") return 0;
  if (type === "boolean") return false;
  if (type === "null") return null;
  if (type === "array") return [];
  if (type === "object") return {};
  return "";
}

function JsonLiteralEditor({
  value,
  onChange,
  ariaLabel,
}: {
  value: WorkflowValue;
  onChange: (value: WorkflowValue) => void;
  ariaLabel: string;
}) {
  const [draft, setDraft] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState("");

  useEffect(() => {
    setDraft(JSON.stringify(value, null, 2));
    setError("");
  }, [value]);

  return (
    <>
      <textarea
        aria-label={ariaLabel}
        className={`${textInputClass()} min-h-28 resize-y font-mono text-xs leading-5`}
        onBlur={() => {
          try {
            const parsed = JSON.parse(draft) as WorkflowValue;
            onChange(parsed);
            setError("");
          } catch {
            setError("JSON 格式无效，尚未写入节点配置。");
          }
        }}
        onChange={(event) => setDraft(event.target.value)}
        value={draft}
      />
      {error ? <p className="mt-1 text-xs text-rose-200">{error}</p> : null}
    </>
  );
}

function workflowBooleanValue(value: unknown): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  if (typeof value === "string") {
    return value.toLowerCase() === "true";
  }
  return false;
}

function ConfigSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
      <div className="mb-3">
        <h4 className="text-sm font-semibold text-slate-100">{title}</h4>
        {description ? (
          <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
        ) : null}
      </div>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function ConfigSwitch({
  label,
  description,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className={`flex items-start justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 ${disabled ? "opacity-55" : ""}`}
    >
      <span>
        <span className="block text-sm font-medium text-slate-100">{label}</span>
        {description ? (
          <span className="mt-1 block text-xs leading-5 text-slate-500">
            {description}
          </span>
        ) : null}
      </span>
      <input
        checked={checked}
        className="mt-1 h-4 w-4 shrink-0 rounded border-white/20 bg-slate-950 text-brand-300"
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
    </label>
  );
}

function HandoffExecutionConfig({
  node,
  nodes,
  edges,
  variableContract,
  data,
  update,
  publishedXperts,
  publishedXpertsError,
  onMigrate,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  variableContract: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
  publishedXperts: XpertSummary[];
  publishedXpertsError: string;
  onMigrate: () => string;
}) {
  const [xpertSearch, setXpertSearch] = useState("");
  const legacy = String(data.contractVersion ?? "1") !== "2";
  const executionMode = data.executionMode ?? "manual";
  const waitForCompletion = workflowBooleanValue(data.waitForCompletion);
  const targetMode: "inbox" | "xpert" = legacy
    ? executionMode === "xpert_auto" ? "xpert" : "inbox"
    : data.targetMode === "xpert" ? "xpert" : "inbox";
  const automatic = targetMode === "xpert";
  const selectedXpert = publishedXperts.find(
    (item) => item.id === String(data.targetXpertId ?? ""),
  );
  const normalizedXpertSearch = xpertSearch.trim().toLocaleLowerCase();
  const matchingXperts = normalizedXpertSearch
    ? publishedXperts.filter((item) =>
        [item.name, item.slug, item.id].some((value) =>
          String(value ?? "").toLocaleLowerCase().includes(normalizedXpertSearch),
        ),
      )
    : publishedXperts;
  const visibleXperts = selectedXpert
    && !matchingXperts.some((item) => item.id === selectedXpert.id)
    ? [selectedXpert, ...matchingXperts]
    : matchingXperts;

  if (legacy) {
    return (
      <ConfigSection
        description="旧版只保存字符串 ID，无法固定目标版本或可靠恢复等待。"
        title="升级协作合同"
      >
        <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
          <p>这是旧版移交配置。既有版本仍可运行，但发布新版本前需要升级。</p>
          <button
            className="mt-3 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-45"
            onClick={onMigrate}
            type="button"
          >
            升级为可恢复移交
          </button>
        </div>
      </ConfigSection>
    );
  }

  return (
    <ConfigSection
      description="选择接收方，并决定源工作流是立即继续还是等待结果。"
      title="2. 选择接收方"
    >
      <div className="grid grid-cols-2 gap-2" role="group" aria-label="接收方类型">
        {([
          ["inbox", "人工队列", "交给人员领取和处理"],
          ["xpert", "已发布智能体", "固定版本自动执行"],
        ] as const).map(([mode, label, description]) => (
          <button
            className={`rounded-lg border p-3 text-left transition-colors ${targetMode === mode ? "border-violet-300/50 bg-violet-300/12 text-white" : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-white/20"}`}
            key={mode}
            onClick={() => {
              const first = publishedXperts.find((item) => item.published_version);
              update({
                targetMode: mode,
                targetXpertId: mode === "xpert" ? selectedXpert?.id ?? first?.id ?? "" : "",
                targetVersion: mode === "xpert" ? selectedXpert?.published_version ?? first?.published_version ?? 0 : 0,
              });
            }}
            type="button"
          >
            <span className="block text-sm font-semibold">{label}</span>
            <span className="mt-1 block text-xs leading-5 text-slate-500">{description}</span>
          </button>
        ))}
      </div>

      {automatic ? (
        <Field label="固定执行目标">
          <input
            aria-label="搜索已发布智能体"
            className={`${textInputClass()} mb-2`}
            onChange={(event) => setXpertSearch(event.target.value)}
            placeholder="按名称、Slug 或 ID 搜索"
            type="search"
            value={xpertSearch}
          />
          <select
            className={textInputClass()}
            onChange={(event) => {
              const target = publishedXperts.find((item) => item.id === event.target.value);
              update({
                targetXpertId: target?.id ?? "",
                targetVersion: target?.published_version ?? 0,
              });
            }}
            value={data.targetXpertId ?? ""}
          >
            <option className="bg-slate-950" value="">
              {publishedXperts.length ? "选择已发布智能体" : "暂无已发布智能体"}
            </option>
            {visibleXperts.map((xpert) => (
              <option
                className="bg-slate-950"
                key={xpert.id}
                disabled={!xpert.published_version}
                value={xpert.id}
              >
                {xpert.name} · {xpert.published_version ? `固定 v${xpert.published_version}` : "未发布"}
              </option>
            ))}
          </select>
          {normalizedXpertSearch && matchingXperts.length === 0 ? (
            <p className="mt-2 text-xs leading-5 text-slate-500">
              没有匹配的已发布智能体；可修改搜索词后重试。
            </p>
          ) : null}
          {publishedXpertsError ? (
            <p className="mt-2 text-xs leading-5 text-amber-200">
              {publishedXpertsError}
            </p>
          ) : null}
        </Field>
      ) : (
        <Field label="人工队列名称">
          <input
            className={textInputClass()}
            onChange={(event) => update({ inboxTarget: event.target.value })}
            placeholder="例如：review-agent"
            value={data.inboxTarget ?? ""}
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs leading-5">
            <p className="text-slate-500">任务会出现在该队列的 Handoff Inbox，等待人员领取。</p>
            <a
              className="font-semibold text-violet-200 transition hover:text-violet-100"
              href="/agents/meta-agent#handoff-inbox"
              target="_blank"
              rel="noreferrer"
            >
              打开 Handoff Inbox
            </a>
          </div>
        </Field>
      )}

      <ConfigSwitch
        checked={waitForCompletion}
        description="开启后持久挂起，服务重启也会继续等待；关闭后提交成功即继续下游。"
        label="等待接收方完成"
        onChange={(checked) => update({ waitForCompletion: checked })}
      />
      {waitForCompletion ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="完成结果写入">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="resultVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ resultVariable: value })}
              value={data.resultVariable ?? "handoff_result"}
            />
          </Field>
          <Field label="最长等待（秒）">
            <input
              className={textInputClass()}
              max={600}
              min={5}
              onChange={(event) => update({ timeoutSeconds: Number(event.target.value) })}
              type="number"
              value={data.timeoutSeconds ?? 120}
            />
          </Field>
        </div>
      ) : null}
      <div className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2 text-xs leading-5 text-slate-400">
        {automatic
          ? `将调用 ${selectedXpert?.name ?? "尚未选择的智能体"}，并固定在 v${data.targetVersion || "-"}。`
          : `将投递到 ${data.inboxTarget || "尚未命名的人工队列"}。`}
        {waitForCompletion ? " 源工作流会等待最终结果。" : " 提交后源工作流立即继续。"}
      </div>
    </ConfigSection>
  );
}

function AgentStudioPanel({
  node,
  nodes,
  edges,
  declarations,
  variableContract,
  data,
  update,
  registryTools,
  registryToolsError,
  boundMiddlewares,
  boundResources,
  onSelectNode,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  declarations: WorkflowVariableDeclaration[];
  variableContract: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
  registryTools: RegistryToolOption[];
  registryToolsError: string;
  boundMiddlewares: WorkflowNode[];
  boundResources: WorkflowNode[];
  onSelectNode: (nodeId: string) => void;
}) {
  const isWorkflowAgent = data.kind === "workflow_agent";
  const toolsEnabled = isWorkflowAgent
    ? data.toolMode === "mcp_tools"
    : data.agentMode !== "direct";
  const selectedStrategy = data.agentStrategy ?? "auto";
  const [knowledgeBases, setKnowledgeBases] = useState<
    Array<{ id: string; name: string }>
  >([]);
  const [knowledgeBasesError, setKnowledgeBasesError] = useState("");
  const selectedKnowledgeBaseIds = useMemo(
    () =>
      new Set(
        String(data.knowledgeBaseIds ?? "")
          .split(/[,\n]/)
          .map((item) => item.trim())
          .filter(Boolean),
      ),
    [data.knowledgeBaseIds],
  );

  useEffect(() => {
    if (!isWorkflowAgent) return;
    let cancelled = false;
    void fetch("/api/rag/knowledge_bases")
      .then(async (response) => {
        if (!response.ok) throw new Error("知识库列表暂不可用。");
        return (await response.json()) as {
          knowledge_bases?: Array<{ id: string; name: string }>;
        };
      })
      .then((payload) => {
        if (!cancelled) {
          setKnowledgeBases(payload.knowledge_bases ?? []);
          setKnowledgeBasesError("");
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setKnowledgeBasesError(
            error instanceof Error ? error.message : "知识库列表暂不可用。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isWorkflowAgent]);

  function toggleKnowledgeBase(kbId: string, checked: boolean) {
    const next = new Set(selectedKnowledgeBaseIds);
    if (checked) next.add(kbId);
    else next.delete(kbId);
    update({ knowledgeBaseIds: Array.from(next).slice(0, 5).join(",") });
  }
  const setStringBoolean = (
    key:
      | "disableOutput"
      | "enableFileUnderstanding"
      | "parallelToolCalls"
      | "retryOnFailure"
      | "memoryReadEnabled"
      | "memoryWriteEnabled"
      | "knowledgeReadEnabled"
      | "knowledgeWriteEnabled",
    checked: boolean,
  ) => update({ [key]: checked ? "true" : "false" });
  const toolNamesPlaceholder = registryTools.length
    ? registryTools.map((tool) => tool.name).slice(0, 3).join(", ")
    : "先在 MCP 页面连接工具 Server";

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-brand-300/25 bg-brand-300/10 px-3 py-2 text-xs leading-5 text-brand-50">
        Agent Strategy V2：工具模式支持原生 Function Calling 与 ReAct，所有调用继续经过权限、中间件和审计链路。
      </div>

      <ConfigSection
        description="控制节点身份和基础运行开关。"
        title="节点"
      >
        {isWorkflowAgent ? (
          <Field label="智能体名称">
            <input
              className={textInputClass()}
              onChange={(event) => update({ agentName: event.target.value })}
              value={data.agentName ?? ""}
            />
          </Field>
        ) : (
          <Field label="执行模式">
            <select
              className={textInputClass()}
              onChange={(event) => update({ agentMode: event.target.value })}
              value={data.agentMode ?? "tool_first"}
            >
              <option className="bg-slate-950" value="tool_first">
                tool_first：优先规划工具调用
              </option>
              <option className="bg-slate-950" value="direct">
                direct：直接回答
              </option>
            </select>
          </Field>
        )}

        <div className="grid gap-2">
          <ConfigSwitch
            checked={workflowBooleanValue(data.disableOutput)}
            description="节点仍会执行；开启后不把最终结果写入输出变量。"
            label="禁用输出"
            onChange={(checked) => setStringBoolean("disableOutput", checked)}
          />
          <ConfigSwitch
            checked={workflowBooleanValue(data.enableFileUnderstanding)}
            description="仅显示已有配置的只读状态；通用文件资产变量尚未接入，当前不能新建或修改。"
            disabled
            label="文件理解（只读）"
            onChange={(checked) =>
              setStringBoolean("enableFileUnderstanding", checked)
            }
          />
          <ConfigSwitch
            checked={workflowBooleanValue(data.parallelToolCalls)}
            description={
              !toolsEnabled
                ? "当前为直接回答模式，不会调度工具。"
                : selectedStrategy === "react"
                  ? "ReAct 每轮只允许一个 Action；切换到 auto 或 function_calling 后可启用。"
                  : "允许原生 Function Calling 在同一轮并发执行多个工具；默认关闭以保护有副作用的工具。"
            }
            disabled={!toolsEnabled || selectedStrategy === "react"}
            label="并行工具调用"
            onChange={(checked) =>
              setStringBoolean("parallelToolCalls", checked)
            }
          />
        </div>
      </ConfigSection>

      {!isWorkflowAgent ? (
        <ConfigSection
          description="旧 Agent 的历史参数草稿；迁移前请确认是否仍需要。"
          title="参数"
        >
          <Field label="参数 JSON">
            <textarea
              className={`${textInputClass()} min-h-24 resize-none font-mono text-xs leading-5`}
              onChange={(event) => update({ nodeParametersJson: event.target.value })}
              placeholder='[{"name":"topic","optional":false}]'
              value={data.nodeParametersJson ?? "[]"}
            />
          </Field>
        </ConfigSection>
      ) : null}

      <ConfigSection title="提示词 / 模型">
        <Field label="调用模型">
          <select
            className={textInputClass()}
            onChange={(event) => update({ modelId: event.target.value })}
            value={data.modelId ?? ""}
          >
            <option className="bg-slate-950" value="">
              请选择模型
            </option>
            {models.map((model) => (
              <option
                className="bg-slate-950 text-white"
                key={model.id}
                value={model.id}
              >
                {model.name}
              </option>
            ))}
          </select>
        </Field>

        {isWorkflowAgent ? (
          <>
            <Field label="角色提示词（支持 {{变量}}）">
              <WorkflowVariableField
                className="min-h-32 resize-none leading-6"
                contract={variableContract}
                declarations={declarations}
                edges={edges}
                fieldName="rolePrompt"
                multiline
                node={node}
                nodes={nodes}
                onChange={(value) => update({ rolePrompt: value })}
                value={data.rolePrompt ?? ""}
              />
            </Field>
            <Field label="任务输入（支持 {{变量}}）">
              <WorkflowVariableField
                className="min-h-32 resize-none leading-6"
                contract={variableContract}
                declarations={declarations}
                edges={edges}
                fieldName="taskInput"
                multiline
                node={node}
                nodes={nodes}
                onChange={(value) => update({ taskInput: value })}
                value={data.taskInput ?? ""}
              />
            </Field>
          </>
        ) : (
          <>
            <Field label="任务指令（支持 {{变量}}）">
              <WorkflowVariableField
                className="min-h-36 resize-none leading-6"
                contract={variableContract}
                declarations={declarations}
                edges={edges}
                fieldName="instruction"
                multiline
                node={node}
                nodes={nodes}
                onChange={(value) => update({ instruction: value })}
                placeholder="例如：请基于 {{user_input}} 制定处理计划。"
                value={data.instruction ?? ""}
              />
            </Field>
            <Field label="Temperature">
              <input
                className={textInputClass()}
                max={2}
                min={0}
                onChange={(event) => update({ temperature: event.target.value })}
                step={0.1}
                type="number"
                value={data.temperature ?? "0.7"}
              />
            </Field>
          </>
        )}
      </ConfigSection>

      <ConfigSection
        description="通过紫色绑定边附加 Agent 级能力，按优先级稳定执行。HITL 会在工具调用或最终输出处持久暂停。"
        title="中间件"
      >
        {boundMiddlewares.length ? (
          <div className="space-y-2">
            {boundMiddlewares.map((middleware, index) => (
              <button
                className="flex w-full items-center gap-3 rounded-lg border border-indigo-300/20 bg-indigo-300/10 px-3 py-2 text-left transition hover:border-indigo-200/45"
                key={middleware.id}
                onClick={() => onSelectNode(middleware.id)}
                type="button"
              >
                <span className="flex h-7 w-7 items-center justify-center rounded-md bg-indigo-300/15 text-xs font-semibold text-indigo-100">
                  {index + 1}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-semibold text-indigo-50">
                    {middleware.data.title}
                  </span>
                  <span className="block truncate text-[11px] text-indigo-200/70">
                    {String(middleware.data.runtimeMiddlewareId ?? "middleware")}
                  </span>
                </span>
                <span className="text-[10px] text-indigo-200/70">
                  P{String(middleware.data.middlewarePriority ?? "100")}
                </span>
              </button>
            ))}
          </div>
        ) : (
          <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
            暂无绑定中间件。可从节点库拖入运行时中间件，并从紫色端口连接到当前 Agent。
          </p>
        )}
      </ConfigSection>

      <ConfigSection
        description="知识工具读取活动版本；写入只创建待审批提议，不会直接修改活动索引。"
        title="知识库"
      >
        {isWorkflowAgent ? (
          <div className="space-y-3">
            <ConfigSwitch
              checked={workflowBooleanValue(data.knowledgeReadEnabled)}
              label="启用知识检索、原文与引用工具"
              onChange={(checked) => {
                setStringBoolean("knowledgeReadEnabled", checked);
                if (checked && data.toolMode !== "mcp_tools") {
                  update({ toolMode: "mcp_tools" });
                }
              }}
            />
            <ConfigSwitch
              checked={workflowBooleanValue(data.knowledgeWriteEnabled)}
              description="模型只能提出写入，必须在 Knowledge Inbox 中人工审批。"
              label="允许提出知识写入"
              onChange={(checked) => {
                setStringBoolean("knowledgeWriteEnabled", checked);
                if (checked && data.toolMode !== "mcp_tools") {
                  update({ toolMode: "mcp_tools" });
                }
              }}
            />
            <div className="space-y-2">
              <p className="text-xs font-semibold text-slate-300">
                可访问知识库（最多 5 个）
              </p>
              {knowledgeBases.length ? (
                knowledgeBases.map((kb) => (
                  <label
                    className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-xs text-slate-300"
                    key={kb.id}
                  >
                    <input
                      checked={selectedKnowledgeBaseIds.has(kb.id)}
                      disabled={
                        !selectedKnowledgeBaseIds.has(kb.id) &&
                        selectedKnowledgeBaseIds.size >= 5
                      }
                      onChange={(event) =>
                        toggleKnowledgeBase(kb.id, event.target.checked)
                      }
                      type="checkbox"
                    />
                    <span className="min-w-0 flex-1 truncate">{kb.name}</span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {kb.id}
                    </span>
                  </label>
                ))
              ) : (
                <p className="rounded-md border border-dashed border-white/15 px-3 py-2 text-xs text-slate-400">
                  {knowledgeBasesError || "暂无知识库，请先在 RAG 页面创建。"}
                </p>
              )}
            </div>
          </div>
        ) : (
          <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
            动态知识工具仅对 workflow_agent 开放；普通 Agent 可继续连接知识节点。
          </p>
        )}
      </ConfigSection>

      <ConfigSection title="工具">
        {isWorkflowAgent ? (
          <Field label="Runtime 工具模式">
            <select
              className={textInputClass()}
              onChange={(event) => update({ toolMode: event.target.value })}
              value={data.toolMode ?? "none"}
            >
              <option className="bg-slate-950" value="none">
                none：直接调用模型
              </option>
              <option className="bg-slate-950" value="mcp_tools">
                mcp_tools：启用 MCP / Memory / Knowledge 工具
              </option>
            </select>
          </Field>
        ) : null}

        {toolsEnabled ? (
          <>
            <Field label="Agent 策略">
              <select
                className={textInputClass()}
                onChange={(event) => {
                  const nextStrategy = event.target.value as
                    | "auto"
                    | "function_calling"
                    | "react";
                  update({
                    agentStrategy: nextStrategy,
                    ...(nextStrategy === "react"
                      ? { parallelToolCalls: "false" }
                      : {}),
                  });
                }}
                value={selectedStrategy}
              >
                <option className="bg-slate-950" value="auto">
                  auto：优先 Function Calling，安全回退 ReAct
                </option>
                <option className="bg-slate-950" value="function_calling">
                  function_calling：原生工具调用
                </option>
                <option className="bg-slate-950" value="react">
                  react：文本 Action / Observation 循环
                </option>
              </select>
            </Field>
            <Field label="允许工具名（逗号分隔，留空代表全部已注册工具）">
              <input
                className={textInputClass()}
                onChange={(event) => update({ toolNames: event.target.value })}
                placeholder={toolNamesPlaceholder}
                value={data.toolNames ?? ""}
              />
              {registryToolsError ? (
                <p className="mt-2 text-xs text-rose-200">
                  {registryToolsError}
                </p>
              ) : null}
            </Field>
            <Field label="最大工具循环次数">
              <input
                className={textInputClass()}
                inputMode="numeric"
                max={20}
                min={1}
                onChange={(event) =>
                  update({ maxIterations: event.target.value })
                }
                type="number"
                value={data.maxIterations ?? "5"}
              />
            </Field>
            {isWorkflowAgent ? (
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="最大并发">
                  <input
                    className={textInputClass()}
                    inputMode="numeric"
                    max={8}
                    min={1}
                    onChange={(event) =>
                      update({ maxToolConcurrency: event.target.value })
                    }
                    type="number"
                    value={data.maxToolConcurrency ?? "2"}
                  />
                </Field>
                <Field label="总调用预算">
                  <input
                    className={textInputClass()}
                    inputMode="numeric"
                    max={50}
                    min={1}
                    onChange={(event) =>
                      update({ maxToolCalls: event.target.value })
                    }
                    type="number"
                    value={data.maxToolCalls ?? "12"}
                  />
                </Field>
                <Field label="嵌套深度">
                  <input
                    className={textInputClass()}
                    inputMode="numeric"
                    max={4}
                    min={1}
                    onChange={(event) =>
                      update({ maxToolDepth: event.target.value })
                    }
                    type="number"
                    value={data.maxToolDepth ?? "4"}
                  />
                </Field>
              </div>
            ) : null}
          </>
        ) : (
          <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
            当前为 none 模式，不会进入 MCP 工具循环。
          </p>
        )}

        <Field label="补充提示词（可选，支持 {{变量}}）">
          <WorkflowVariableField
            className="min-h-24 resize-none leading-6"
            contract={variableContract}
            declarations={declarations}
            edges={edges}
            fieldName="promptSuffix"
            multiline
            node={node}
            nodes={nodes}
            onChange={(value) => update({ promptSuffix: value })}
            placeholder="可加入输出格式、语气或额外约束。"
            value={data.promptSuffix ?? ""}
          />
        </Field>
      </ConfigSection>

      <ConfigSection
        description={isWorkflowAgent ? "选择真实生效的失败语义。" : "旧 Agent 的历史运行策略。"}
        title="运行策略"
      >
        {!isWorkflowAgent ? (
          <>
            <ConfigSwitch
              checked={workflowBooleanValue(data.retryOnFailure)}
              label="失败时重试（旧配置）"
              onChange={(checked) => setStringBoolean("retryOnFailure", checked)}
            />
            <Field label="备用模型（旧配置）">
              <select
                className={textInputClass()}
                onChange={(event) => update({ fallbackModelId: event.target.value })}
                value={data.fallbackModelId ?? ""}
              >
                <option className="bg-slate-950" value="">不使用备用模型</option>
                {models.map((model) => (
                  <option className="bg-slate-950 text-white" key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </Field>
          </>
        ) : null}
        <Field label="异常处理">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({ exceptionHandling: event.target.value })
            }
            value={data.exceptionHandling ?? "none"}
          >
            <option className="bg-slate-950" value="none">
              无
            </option>
            <option className="bg-slate-950" value="empty_output">
              失败时写入空输出
            </option>
            <option className="bg-slate-950" value="fail">
              标记失败
            </option>
          </select>
        </Field>
      </ConfigSection>

      <ConfigSection title="输出结构">
        <Field label="输出变量">
          <WorkflowVariableField
            contract={variableContract}
            declarations={declarations}
            edges={edges}
            fieldName="outputVariable"
            node={node}
            nodes={nodes}
            onChange={(value) => update({ outputVariable: value })}
            value={data.outputVariable ?? ""}
          />
        </Field>
        <Field label="输出结构模式">
          <select
            className={textInputClass()}
            onChange={(event) => update({ outputSchemaMode: event.target.value })}
            value={data.outputSchemaMode ?? "default"}
          >
            <option className="bg-slate-950" value="default">
              默认
            </option>
            <option className="bg-slate-950" value="text">
              文本
            </option>
            <option className="bg-slate-950" value="json">
              JSON
            </option>
          </select>
        </Field>
        <Field label="输出结构 JSON（可选）">
          <textarea
            className={`${textInputClass()} min-h-24 resize-none font-mono text-xs leading-5`}
            onChange={(event) => update({ outputSchemaJson: event.target.value })}
            placeholder='{"content":"string"}'
            value={data.outputSchemaJson ?? ""}
          />
        </Field>
      </ConfigSection>

      <ConfigSection
        description="当前仅保存配置草稿，不会写入长期记忆。"
        title="记忆写入"
      >
        <ConfigSwitch
          checked={workflowBooleanValue(data.memoryReadEnabled)}
          label={"\u8bfb\u53d6\u76f8\u5173\u8bb0\u5fc6"}
          onChange={(checked) =>
            setStringBoolean("memoryReadEnabled", checked)
          }
        />
        <Field label={"\u8bb0\u5fc6\u8bfb\u53d6\u8303\u56f4"}>
          <select
            className={textInputClass()}
            disabled={!workflowBooleanValue(data.memoryReadEnabled)}
            onChange={(event) => update({ memoryReadScope: event.target.value })}
            value={data.memoryReadScope ?? "both"}
          >
            <option className="bg-slate-950" value="both">{"\u4f1a\u8bdd + \u667a\u80fd\u4f53"}</option>
            <option className="bg-slate-950" value="conversation">{"\u4ec5\u5f53\u524d\u4f1a\u8bdd"}</option>
            <option className="bg-slate-950" value="xpert">{"\u4ec5\u667a\u80fd\u4f53\u957f\u671f\u8bb0\u5fc6"}</option>
          </select>
        </Field>
        <ConfigSwitch
          checked={workflowBooleanValue(data.memoryWriteEnabled)}
          label="写入记忆"
          onChange={(checked) =>
            setStringBoolean("memoryWriteEnabled", checked)
          }
        />
        <Field label="记忆目标">
          <input
            className={textInputClass()}
            disabled={!workflowBooleanValue(data.memoryWriteEnabled)}
            onChange={(event) =>
              update({ memoryWriteTarget: event.target.value })
            }
            placeholder="例如：agent_memory"
            value={data.memoryWriteTarget ?? ""}
          />
        </Field>
      </ConfigSection>

      {isWorkflowAgent ? (
        <ConfigSection
          description="资源绑定不进入控制流，只向当前智能体开放对应 Runtime 工具。"
          title="资源绑定"
        >
          {boundResources.length ? (
            <div className="space-y-2">
              {boundResources.map((resource) => (
                <button
                  className="flex w-full items-center gap-3 rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-3 py-2 text-left transition hover:border-cyan-200/45"
                  key={resource.id}
                  onClick={() => onSelectNode(resource.id)}
                  type="button"
                >
                  <span className="flex h-7 w-7 items-center justify-center rounded-md bg-cyan-300/15 text-[10px] font-semibold text-cyan-100">
                    {resource.data.kind === "external_xpert"
                      ? "XP"
                      : resource.data.kind === "knowledge_base"
                        ? "KB"
                        : resource.data.kind === "toolset_resource"
                          ? "TS"
                          : "PL"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-cyan-50">
                      {resource.data.title}
                    </span>
                    <span className="block truncate text-[11px] text-cyan-200/70">
                      {resource.data.kind === "external_xpert"
                        ? String(resource.data.toolName || "external_xpert")
                        : resource.data.kind === "knowledge_base"
                          ? String(resource.data.knowledgeBaseId || "未选择知识库")
                          : resource.data.kind === "toolset_resource"
                            ? String(resource.data.toolsetId || "未选择 Toolset")
                            : String(resource.data.pluginId || "未选择 Plugin")}
                    </span>
                    {resource.data.kind === "plugin_resource" ? (
                      <span className="mt-1 block text-[10px] text-cyan-100/70">
                        插件提供的 Skill 为可选，Agent 主动启用后才必须读取
                      </span>
                    ) : null}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-3 text-xs leading-5 text-slate-400">
              从节点库拖入外部智能体、知识库、Toolset 或 Plugin，再用专用端口绑定到当前智能体。
            </p>
          )}
        </ConfigSection>
      ) : null}
    </div>
  );
}

interface WorkflowResourceOption {
  id: string;
  slug?: string;
  name: string;
  description?: string;
  status?: string;
  published_version?: number | null;
  active_version_id?: string | null;
  document_count?: number;
  corpus_locked?: boolean;
  provisioning_status?: string;
  tool_count?: number;
  prompt_count?: number;
  skill_count?: number;
  middleware_count?: number;
}

function useWorkflowResourceOptions(resourceKind: string) {
  const [options, setOptions] = useState<WorkflowResourceOption[]>([]);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void fetch(`/api/workflow/resource-options?kind=${resourceKind}`)
      .then(async (response) => {
        const payload = (await response.json()) as {
          items?: WorkflowResourceOption[];
        };
        if (!response.ok || !Array.isArray(payload.items)) {
          throw new Error("资源选项暂不可用。");
        }
        if (!cancelled) {
          setOptions(payload.items);
          setLoadError("");
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setOptions([]);
          setLoadError(
            error instanceof Error ? error.message : "资源选项加载失败。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [resourceKind]);

  return { loadError, options };
}

function ResourceNodeConfig({
  data,
  update,
}: {
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
}) {
  const resourceKind =
    data.kind === "external_xpert"
      ? "external_xpert"
      : data.kind === "toolset_resource"
        ? "toolset"
        : data.kind === "plugin_resource"
          ? "plugin"
          : "knowledge_base";
  const { loadError, options } = useWorkflowResourceOptions(resourceKind);

  if (data.kind === "external_xpert") {
    return (
      <ConfigSection
        description="发布智能体时会把当前发布版本解析为不可变固定版本。"
        title="外部智能体"
      >
        <Field label="已发布智能体">
          <select
            className={textInputClass()}
            onChange={(event) => {
              const selected = options.find(
                (item) => item.id === event.target.value,
              );
              update({
                xpertId: event.target.value,
                description: selected?.description || data.description,
                pinnedVersion:
                  data.versionPolicy === "pinned"
                    ? String(selected?.published_version ?? "")
                    : data.pinnedVersion,
              });
            }}
            value={data.xpertId ?? ""}
          >
            <option className="bg-slate-950" value="">
              选择外部智能体
            </option>
            {options.map((item) => (
              <option
                className="bg-slate-950"
                disabled={item.status !== "published"}
                key={item.id}
                value={item.id}
              >
                {item.name} · {item.status} · v{item.published_version ?? "-"}
              </option>
            ))}
          </select>
        </Field>
        <Field label="工具名称">
          <input
            className={textInputClass()}
            onChange={(event) => update({ toolName: event.target.value })}
            placeholder="research_expert"
            value={data.toolName ?? ""}
          />
        </Field>
        <Field label="版本策略">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({
                versionPolicy: event.target.value,
                pinnedVersion:
                  event.target.value === "pinned"
                    ? String(
                        options.find((item) => item.id === data.xpertId)
                          ?.published_version ?? "",
                      )
                    : "",
              })
            }
            value={data.versionPolicy ?? "current_published"}
          >
            <option className="bg-slate-950" value="current_published">
              草稿跟随当前发布版本
            </option>
            <option className="bg-slate-950" value="pinned">
              草稿固定版本
            </option>
          </select>
        </Field>
        {data.versionPolicy === "pinned" ? (
          <Field label="固定版本">
            <input
              className={textInputClass()}
              min={1}
              onChange={(event) =>
                update({ pinnedVersion: event.target.value })
              }
              type="number"
              value={data.pinnedVersion ?? ""}
            />
          </Field>
        ) : null}
        {loadError ? (
          <p className="text-xs leading-5 text-amber-200">{loadError}</p>
        ) : null}
      </ConfigSection>
    );
  }

  if (data.kind === "toolset_resource") {
    return (
      <ConfigSection
        description="草稿可跟随当前发布版本；发布智能体时会解析并固定不可变 Toolset 版本。"
        title="MCP Toolset"
      >
        <Field label="已发布 Toolset">
          <select
            className={textInputClass()}
            onChange={(event) => {
              const selected = options.find(
                (item) => item.id === event.target.value,
              );
              update({
                toolsetId: event.target.value,
                description: selected?.description || data.description,
                pinnedVersion:
                  data.versionPolicy === "pinned"
                    ? String(selected?.published_version ?? "")
                    : data.pinnedVersion,
              });
            }}
            value={data.toolsetId ?? ""}
          >
            <option className="bg-slate-950" value="">
              选择 Toolset
            </option>
            {options.map((item) => (
              <option
                className="bg-slate-950"
                disabled={item.status !== "published"}
                key={item.id}
                value={item.id}
              >
                {item.name} · {item.status} · v{item.published_version ?? "-"} ·{" "}
                {item.tool_count ?? 0} tools
              </option>
            ))}
          </select>
        </Field>
        <Field label="版本策略">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({
                versionPolicy: event.target.value,
                pinnedVersion:
                  event.target.value === "pinned"
                    ? String(
                        options.find((item) => item.id === data.toolsetId)
                          ?.published_version ?? "",
                      )
                    : "",
              })
            }
            value={data.versionPolicy ?? "current_published"}
          >
            <option className="bg-slate-950" value="current_published">
              草稿跟随当前发布版本
            </option>
            <option className="bg-slate-950" value="pinned">
              草稿固定版本
            </option>
          </select>
        </Field>
        {data.versionPolicy === "pinned" ? (
          <Field label="固定版本">
            <input
              className={textInputClass()}
              min={1}
              onChange={(event) =>
                update({ pinnedVersion: event.target.value })
              }
              type="number"
              value={data.pinnedVersion ?? ""}
            />
          </Field>
        ) : null}
        <a
          className="inline-flex text-xs font-semibold text-cyan-200 hover:text-cyan-100"
          href="/toolsets"
        >
          打开 Toolset 管理页
        </a>
        {loadError ? (
          <p className="text-xs leading-5 text-amber-200">{loadError}</p>
        ) : null}
      </ConfigSection>
    );
  }

  if (data.kind === "plugin_resource") {
    return (
      <ConfigSection
        description="草稿可跟随当前发布版本；发布智能体时会解析并固定不可变 Plugin 版本。"
        title="Plugin 资源"
      >
        <Field label="已发布 Plugin">
          <select
            className={textInputClass()}
            onChange={(event) => {
              const selected = options.find(
                (item) => item.id === event.target.value,
              );
              update({
                pluginId: event.target.value,
                description: selected?.description || data.description,
                pinnedVersion:
                  data.versionPolicy === "pinned"
                    ? String(selected?.published_version ?? "")
                    : data.pinnedVersion,
              });
            }}
            value={data.pluginId ?? ""}
          >
            <option className="bg-slate-950" value="">
              选择 Plugin
            </option>
            {options.map((item) => (
              <option
                className="bg-slate-950"
                disabled={item.status !== "published"}
                key={item.id}
                value={item.id}
              >
                {item.name} · {item.status} · v{item.published_version ?? "-"} ·{" "}
                {(item.prompt_count ?? 0) + (item.skill_count ?? 0) + (item.tool_count ?? 0) + (item.middleware_count ?? 0)} resources
              </option>
            ))}
          </select>
        </Field>
        <Field label="版本策略">
          <select
            className={textInputClass()}
            onChange={(event) =>
              update({
                versionPolicy: event.target.value,
                pinnedVersion:
                  event.target.value === "pinned"
                    ? String(
                        options.find((item) => item.id === data.pluginId)
                          ?.published_version ?? "",
                      )
                    : "",
              })
            }
            value={data.versionPolicy ?? "latest"}
          >
            <option className="bg-slate-950" value="latest">
              草稿跟随当前发布版本
            </option>
            <option className="bg-slate-950" value="pinned">
              草稿固定版本
            </option>
          </select>
        </Field>
        {data.versionPolicy === "pinned" ? (
          <Field label="固定版本">
            <input
              className={textInputClass()}
              min={1}
              onChange={(event) =>
                update({ pinnedVersion: event.target.value })
              }
              type="number"
              value={data.pinnedVersion ?? ""}
            />
          </Field>
        ) : null}
        <a
          className="inline-flex text-xs font-semibold text-cyan-200 hover:text-cyan-100"
          href="/plugins"
        >
          打开 Plugin 管理页
        </a>
        {loadError ? (
          <p className="text-xs leading-5 text-amber-200">{loadError}</p>
        ) : null}
      </ConfigSection>
    );
  }

  return (
    <ConfigSection
      description="使用知识库活动索引的 Retrieval Profile；该绑定只提供读取、原文和引用工具。"
      title="知识库资源"
    >
      <Field label="知识库">
        <select
          className={textInputClass()}
          onChange={(event) =>
            update({ knowledgeBaseId: event.target.value })
          }
          value={data.knowledgeBaseId ?? ""}
        >
          <option className="bg-slate-950" value="">
            选择知识库
          </option>
          {options.map((item) => (
            <option className="bg-slate-950" key={item.id} value={item.id}>
              {item.name} ·{" "}
              {item.status === "active"
                ? `active ${item.active_version_id ?? ""}`
                : "暂无活动索引"}
            </option>
          ))}
        </select>
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Top-K">
          <input
            className={textInputClass()}
            max={10}
            min={1}
            onChange={(event) => update({ topK: event.target.value })}
            type="number"
            value={data.topK ?? "5"}
          />
        </Field>
        <Field label="Score 阈值">
          <input
            className={textInputClass()}
            max={1}
            min={0}
            onChange={(event) =>
              update({ scoreThreshold: event.target.value })
            }
            step={0.05}
            type="number"
            value={data.scoreThreshold ?? "0"}
          />
        </Field>
      </div>
      {loadError ? (
        <p className="text-xs leading-5 text-amber-200">{loadError}</p>
      ) : null}
    </ConfigSection>
  );
}

function KnowledgeBaseSelector({
  allowLegacyEmpty = false,
  onChange,
  value,
}: {
  allowLegacyEmpty?: boolean;
  onChange: (value: string) => void;
  value: string;
}) {
  const { loadError, options } = useWorkflowResourceOptions("knowledge_base");
  return (
    <>
      <select
        className={textInputClass()}
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        <option className="bg-slate-950" value="">
          {allowLegacyEmpty ? "未固定（仅单知识库旧图兼容）" : "选择知识库"}
        </option>
        {options.map((item) => (
          <option className="bg-slate-950" key={item.id} value={item.id}>
            {item.name} · {item.active_version_id ? "活动索引可用" : "暂无活动索引"}
          </option>
        ))}
      </select>
      {loadError ? (
        <p className="mt-2 text-xs leading-5 text-amber-200">{loadError}</p>
      ) : null}
    </>
  );
}

function KnowledgeRetrievalNodeConfig({
  node,
  nodes,
  edges,
  declarations,
  variableContract,
  data,
  update,
  onOpenVariableCenter,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  declarations: WorkflowVariableDeclaration[];
  variableContract: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
  onOpenVariableCenter: () => void;
}) {
  const isLegacy = Number(data.contractVersion ?? 1) !== 2;
  return (
    <>
      <div className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-50">
        {isLegacy
          ? "兼容旧版文本契约。保存现有工作流不会自动改变输出类型。"
          : "V2 只执行活动知识版本检索；结果模式返回上下文、来源、CitationAnchor 与安全诊断。"}
      </div>
      <Field label="知识库">
        <KnowledgeBaseSelector
          allowLegacyEmpty={isLegacy}
          onChange={(value) => update({ knowledgeBaseId: value })}
          value={data.knowledgeBaseId ?? ""}
        />
      </Field>
      <Field label="查询变量">
        <WorkflowVariableField
          contract={variableContract}
          edges={edges}
          fieldName="queryVariable"
          node={node}
          nodes={nodes}
          onChange={(value) => update({ queryVariable: value })}
          value={data.queryVariable ?? ""}
        />
      </Field>
      <Field label="返回片段数 Top K">
        <input
          className={textInputClass()}
          inputMode="numeric"
          max={isLegacy ? 20 : 10}
          min={1}
          onChange={(event) => update({ top_k: event.target.value })}
          type="number"
          value={data.top_k ?? "5"}
        />
      </Field>
      {!isLegacy ? (
        <Field label="返回模式">
          <select
            className={textInputClass()}
            onChange={(event) => update({ returnMode: event.target.value as "context" | "result" })}
            value={data.returnMode ?? "result"}
          >
            <option className="bg-slate-950" value="result">
              类型化结果（推荐）
            </option>
            <option className="bg-slate-950" value="context">
              纯文本上下文
            </option>
          </select>
        </Field>
      ) : null}
      <Field label="输出变量">
        <WorkflowVariableField
          contract={variableContract}
          edges={edges}
          fieldName="outputVariable"
          node={node}
          nodes={nodes}
          onChange={(value) => update({ outputVariable: value })}
          value={data.outputVariable ?? ""}
        />
      </Field>
      {!isLegacy ? (
        <WorkflowFailureRoutingConfig
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          node={node}
          nodes={nodes}
          onChange={update}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}
    </>
  );
}

function KnowledgeWriteProposalNodeConfig({
  node,
  nodes,
  edges,
  variableContract,
  data,
  update,
  onOpenVariableCenter,
  featureEnabled,
  featureDisabledReason,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  variableContract: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
  onOpenVariableCenter: () => void;
  featureEnabled: boolean;
  featureDisabledReason: string;
}) {
  const { loadError, options } = useWorkflowResourceOptions("knowledge_base");
  const [search, setSearch] = useState("");
  const normalizedSearch = search.trim().toLowerCase();
  const matchingOptions = options.filter((item) =>
    !normalizedSearch
    || item.name.toLowerCase().includes(normalizedSearch)
    || item.id.toLowerCase().includes(normalizedSearch));
  const selected = options.find((item) => item.id === data.knowledgeBaseId);
  const tags = Array.isArray(data.tags) ? data.tags : [];

  return (
    <ConfigSection
      description="只创建待审批提议，不会自动批准、构建或切换活动知识版本。"
      title="知识写入提议"
    >
      {!featureEnabled ? (
        <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
          当前功能开关关闭：可继续编辑和静态发布，但激活、私有智能体发布和实际运行会失败关闭。{featureDisabledReason ? ` ${featureDisabledReason}` : ""}
        </div>
      ) : null}
      <div className="rounded-lg border border-teal-300/25 bg-teal-300/10 px-3 py-2 text-xs leading-5 text-teal-50">
        正文会持久保存到 Knowledge Inbox，仍需人工审批。审批只构建候选版本，不会自动改变活动知识版本。
      </div>
      <Field label="搜索知识库">
        <input
          className={textInputClass()}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="按名称或 ID 搜索"
          type="search"
          value={search}
        />
      </Field>
      <Field label="写入目标">
        <select
          className={textInputClass()}
          onChange={(event) => update({ knowledgeBaseId: event.target.value })}
          value={data.knowledgeBaseId ?? ""}
        >
          <option className="bg-slate-950" value="">选择可写知识库</option>
          {matchingOptions.map((item) => {
            const unavailable = item.corpus_locked || item.provisioning_status !== "ready";
            return (
              <option
                className="bg-slate-950"
                disabled={unavailable}
                key={item.id}
                value={item.id}
              >
                {item.name} · {unavailable ? "只读或未就绪" : "可提交待审提议"}
              </option>
            );
          })}
        </select>
        {!loadError && options.length > 0 && matchingOptions.length === 0 ? (
          <p className="mt-2 text-xs leading-5 text-slate-300">没有匹配的知识库，请调整搜索词。</p>
        ) : null}
        {selected?.corpus_locked ? (
          <p className="mt-2 text-xs leading-5 text-amber-200">该知识库为锁定语料，不能接收写入提议。</p>
        ) : null}
        {selected && !selected.corpus_locked && selected.provisioning_status !== "ready" ? (
          <p className="mt-2 text-xs leading-5 text-amber-200">该知识库仍在准备中，完成后才能接收写入提议。</p>
        ) : null}
        {loadError ? <p className="mt-2 text-xs leading-5 text-amber-200">{loadError}</p> : null}
      </Field>
      <Field label="提议标题模板">
        <textarea
          className={`${textInputClass()} min-h-24 resize-y`}
          maxLength={2000}
          onChange={(event) => update({ titleTemplate: event.target.value })}
          placeholder="例如：产品公告更新（可插入 {{变量名}}）"
          value={data.titleTemplate ?? ""}
        />
        <p className="mt-1 text-[11px] leading-5 text-slate-400">
          已输入 {(data.titleTemplate ?? "").length}/2000；运行时渲染后需为 1–160 个字符。
        </p>
      </Field>
      <Field label="正文变量（必须是文本）">
        <WorkflowVariableField
          ariaLabel="正文变量（必须是文本）"
          contract={variableContract}
          edges={edges}
          fieldName="contentVariable"
          node={node}
          nodes={nodes}
          onChange={(value) => update({ contentVariable: value })}
          value={data.contentVariable ?? ""}
        />
      </Field>
      <Field label="固定标签（最多 20 个）">
        <div className="space-y-2">
          {tags.map((tag, index) => (
            <div className="flex gap-2" key={index}>
              <input
                aria-label={`标签 ${index + 1}`}
                className={textInputClass()}
                maxLength={50}
                onChange={(event) => {
                  const next = [...tags];
                  next[index] = event.target.value;
                  update({ tags: next });
                }}
                placeholder="例如：公告"
                value={tag}
              />
              <button
                aria-label={`删除标签 ${index + 1}`}
                className="rounded-md border border-rose-300/20 px-2.5 text-rose-100"
                onClick={() => update({ tags: tags.filter((_, itemIndex) => itemIndex !== index) })}
                type="button"
              >
                删除
              </button>
            </div>
          ))}
          <button
            className="rounded-md border border-white/10 px-3 py-1.5 text-xs font-semibold text-cyan-100 disabled:opacity-40"
            disabled={tags.length >= 20}
            onClick={() => update({ tags: [...tags, ""] })}
            type="button"
          >
            添加标签
          </button>
          <p className="text-[11px] leading-5 text-slate-400">已添加 {tags.length}/20 个固定标签。</p>
        </div>
      </Field>
      <Field label="回执输出变量">
        <WorkflowVariableField
          ariaLabel="回执输出变量"
          contract={variableContract}
          edges={edges}
          fieldName="outputVariable"
          node={node}
          nodes={nodes}
          onChange={(value) => update({ outputVariable: value })}
          value={data.outputVariable ?? ""}
        />
      </Field>
      <div className="flex flex-wrap gap-3 text-xs font-semibold">
        <button
          className="text-cyan-200 underline underline-offset-4"
          onClick={onOpenVariableCenter}
          type="button"
        >
          打开变量中心
        </button>
        <a
          className="text-teal-200 underline underline-offset-4"
          href={data.knowledgeBaseId ? `/rag/${encodeURIComponent(data.knowledgeBaseId)}/inbox` : "/rag"}
          target="_blank"
          rel="noreferrer"
        >
          {data.knowledgeBaseId ? "打开 Knowledge Inbox" : "打开知识库管理"}
        </a>
      </div>
    </ConfigSection>
  );
}

function LegacyKnowledgeCitationConfig({
  node,
  nodes,
  edges,
  variableContract,
  data,
  update,
  onMigrate,
}: {
  node: WorkflowNode;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  variableContract: WorkflowNodeContractProjection | null;
  data: WorkflowNodeData;
  update: (patch: Partial<WorkflowNodeData>) => void;
  onMigrate: () => void;
}) {
  return (
    <>
      <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
        <p>该节点已退役，仅用于旧工作流兼容，不能创建新发布版本。</p>
        <button
          className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950"
          onClick={onMigrate}
          type="button"
        >
          迁移到知识检索 V2
        </button>
      </div>
      <Field label="查询变量">
        <WorkflowVariableField
          contract={variableContract}
          edges={edges}
          fieldName="queryVariable"
          node={node}
          nodes={nodes}
          onChange={(value) => update({ queryVariable: value })}
          value={data.queryVariable ?? ""}
        />
      </Field>
      <Field label="知识库">
        <KnowledgeBaseSelector
          allowLegacyEmpty
          onChange={(value) => update({ knowledgeBaseId: value })}
          value={data.knowledgeBaseId ?? ""}
        />
      </Field>
      <Field label="返回引用数 Top K">
        <input
          className={textInputClass()}
          inputMode="numeric"
          max={10}
          min={1}
          onChange={(event) => update({ top_k: event.target.value })}
          type="number"
          value={data.top_k ?? "4"}
        />
      </Field>
      <Field label="输出变量">
        <WorkflowVariableField
          contract={variableContract}
          edges={edges}
          fieldName="outputVariable"
          node={node}
          nodes={nodes}
          onChange={(value) => update({ outputVariable: value })}
          value={data.outputVariable ?? ""}
        />
      </Field>
    </>
  );
}

interface NodeConfigProps {
  workflowId: string;
  node: WorkflowNode | null;
  declarations: WorkflowVariableDeclaration[];
  onChange: (nodeId: string, data: Partial<WorkflowNodeData>) => void;
  onRuntimeMiddlewareConfigChange: (
    nodeId: string,
    fieldName: string,
    value: unknown,
  ) => void;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  onSelectNode: (nodeId: string) => void;
  onOpenRunFileInput: (variableName: string) => void;
  onOpenVariableCenter: () => void;
  onMigrateTypedAiNode: (nodeId: string) => string;
  onReplaceNodeData: (nodeId: string, data: WorkflowNodeData) => void;
  onReplaceNodeDataBatch: (
    replacements: Array<{ nodeId: string; data: WorkflowNodeData }>,
    notice: string,
  ) => void;
}

function NodeConfig({
  workflowId,
  node,
  declarations,
  onChange,
  onRuntimeMiddlewareConfigChange,
  nodes,
  edges,
  onSelectNode,
  onOpenRunFileInput,
  onOpenVariableCenter,
  onMigrateTypedAiNode,
  onReplaceNodeData,
  onReplaceNodeDataBatch,
}: NodeConfigProps) {
  const [registryTools, setRegistryTools] = useState<RegistryToolOption[]>([]);
  const [registryToolsError, setRegistryToolsError] = useState("");
  const [migrationNotice, setMigrationNotice] = useState("");
  const [publishedXperts, setPublishedXperts] = useState<XpertSummary[]>([]);
  const [publishedXpertsError, setPublishedXpertsError] = useState("");
  const [installedSkills, setInstalledSkills] = useState<TrustSelectableSkill[]>([]);
  const [showSkillAdvancedOptions, setShowSkillAdvancedOptions] = useState(false);
  const [visionModels, setVisionModels] = useState<
    Array<{ model_id: string; label: string }>
  >([]);
  const [visionCapabilityError, setVisionCapabilityError] = useState("");
  const [variableNodeContracts, setVariableNodeContracts] = useState<
    Map<WorkflowNodeKind, WorkflowNodeContractProjection>
  >(new Map());
  const [nodeRegistryMetadata, setNodeRegistryMetadata] = useState<
    Map<WorkflowNodeKind, Record<string, unknown>>
  >(new Map());
  const migrationVariableDescriptors = useMemo(
    () => analyzeWorkflowVariables(nodes, edges, node?.id ?? null, declarations)
      .filter((variable) => variable.availability === "available"),
    [declarations, edges, node?.id, nodes],
  );
  const migrationAvailableVariables = useMemo(
    () => new Set(migrationVariableDescriptors.map((variable) => variable.name)),
    [migrationVariableDescriptors],
  );
  const [clientHosts, setClientHosts] = useState<Array<{
    host_id: string;
    name: string;
    status: string;
    host_type?: "chrome" | "office";
    office_app?: "word" | "excel" | "powerpoint" | "";
    bound_tab?: { bound?: boolean; title?: string; origin?: string };
    document_binding?: { bound?: boolean; title?: string; binding_id?: string };
    revoked?: boolean;
  }>>([]);

  useEffect(() => {
    let cancelled = false;

    async function loadRegistryTools() {
      try {
        // MCP 会话与 Schema 可在编辑期间变化，不能复用其他静态目录的 60 秒缓存。
        const response = await fetch("/api/registry/tools");
        if (!response.ok) {
          throw new Error("工具注册表暂时不可用。");
        }
        const payload: unknown = await response.json();
        if (
          !payload
          || typeof payload !== "object"
          || !("tools" in payload)
          || !Array.isArray((payload as { tools?: unknown }).tools)
        ) {
          throw new Error("工具注册表响应格式无效。");
        }
        const tools = (payload as { tools: unknown[] }).tools;
        if (!cancelled) {
          setRegistryTools(tools.filter(isRegistryToolOption));
          setRegistryToolsError("");
        }
      } catch (error) {
        if (!cancelled) {
          setRegistryTools([]);
          setRegistryToolsError(
            error instanceof Error ? error.message : "工具注册表加载失败。",
          );
        }
      }
    }

    async function loadPublishedXperts() {
      try {
        const payload = await cachedFetchResource<XpertListResponse>(
          "/api/xperts?status=published&limit=200",
          async () => {
            const response = await fetch("/api/xperts?status=published&limit=200");
            const body = (await response.json()) as XpertListResponse;
            if (!response.ok || !Array.isArray(body.items)) {
              throw new Error("已发布智能体列表暂时不可用。");
            }
            return body;
          },
        );
        if (!cancelled) {
          setPublishedXperts(payload.items);
          setPublishedXpertsError("");
        }
      } catch (error) {
        if (!cancelled) {
          setPublishedXperts([]);
          setPublishedXpertsError(
            error instanceof Error ? error.message : "已发布智能体列表加载失败。",
          );
        }
      }
    }

    async function loadClientHosts() {
      try {
        const payload = await cachedFetchResource<{ hosts?: typeof clientHosts }>(
          "/api/runtime/client-hosts",
          async () => {
            const response = await fetch("/api/runtime/client-hosts");
            const body = (await response.json()) as { hosts?: typeof clientHosts };
            if (!response.ok) throw new Error("客户端宿主列表暂不可用");
            return body;
          },
        );
        if (!cancelled) setClientHosts(payload.hosts ?? []);
      } catch {
        if (!cancelled) setClientHosts([]);
      }
    }

    async function loadInstalledSkills() {
      try {
        const payload = await cachedFetchResource<{ skills?: TrustSelectableSkill[] }>(
          "/api/skills/installed",
          async () => {
            const response = await fetch("/api/skills/installed");
            const body = (await response.json()) as { skills?: TrustSelectableSkill[] };
            if (!response.ok || !Array.isArray(body.skills)) {
              throw new Error("已安装 Skill 列表暂不可用");
            }
            return body;
          },
        );
        if (!cancelled) setInstalledSkills(payload.skills ?? []);
      } catch {
        if (!cancelled) setInstalledSkills([]);
      }
    }

    async function loadVisionCapabilities() {
      try {
        const payload = await cachedFetchResource<{
          models?: Array<{ model_id: string; label: string }>;
        }>("/api/workflow/vision-capabilities", async () => {
          const response = await fetch("/api/workflow/vision-capabilities");
          const body = (await response.json()) as {
            models?: Array<{ model_id: string; label: string }>;
          };
          if (!response.ok || !Array.isArray(body.models)) {
            throw new Error("视觉模型目录暂不可用。");
          }
          return body;
        });
        if (!cancelled) {
          setVisionModels(payload.models ?? []);
          setVisionCapabilityError("");
        }
      } catch (error) {
        if (!cancelled) {
          setVisionModels([]);
          setVisionCapabilityError(
            error instanceof Error ? error.message : "视觉模型目录暂不可用。",
          );
        }
      }
    }

    void loadRegistryTools();
    void loadPublishedXperts();
    void loadClientHosts();
    void loadInstalledSkills();
    void loadVisionCapabilities();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchWorkflowNodeRegistry()
      .then((registry) => {
        if (cancelled) return;
        const contracts = new Map<WorkflowNodeKind, WorkflowNodeContractProjection>();
        const metadata = new Map<WorkflowNodeKind, Record<string, unknown>>();
        [
          ...registry.sections.flatMap((section) => section.items),
          ...registry.knowledge_pipeline.items,
        ].forEach((item) => {
          if (item.contract) contracts.set(item.kind, item.contract);
          metadata.set(item.kind, item.metadata ?? {});
        });
        setVariableNodeContracts(contracts);
        setNodeRegistryMetadata(metadata);
      })
      .catch(() => {
        // compatibility 节点会继续使用字段表中的保守类型回退。
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!node) {
    return (
      <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-4 py-8 text-center text-sm leading-6 text-slate-400">
        点击画布上的工位牌，即可编辑节点配置。
      </div>
    );
  }

  const data = node.data;
  const update = (patch: Partial<WorkflowNodeData>) => onChange(node.id, patch);
  const selectedRegistryTool = data.kind === "mcp_tool"
    ? registryTools.find(
        (tool) => tool.server_id === data.serverId && tool.name === data.toolName,
      )
    : undefined;
  const mcpSchemaDrift =
    String(data.contractVersion ?? "1") === "2"
    && Boolean(data.toolName)
    && Boolean(selectedRegistryTool)
    && data.inputSchemaChecksum !== selectedRegistryTool?.schema_checksum;
  const variableContract = variableNodeContracts.get(data.kind) ?? null;
  const documentRegistryMetadata = nodeRegistryMetadata.get("document_extractor") ?? {};
  const knowledgeProposalMetadata = nodeRegistryMetadata.get("knowledge_write_proposal") ?? {};
  const rssRegistryMetadata = nodeRegistryMetadata.get("rss_event_entry") ?? {};
  const emailRegistryMetadata = nodeRegistryMetadata.get("email_event_entry") ?? {};
  const documentFileAssetModeEnabled =
    documentRegistryMetadata.file_asset_mode_enabled !== false;
  const documentFileAssetModeReason = String(
    documentRegistryMetadata.file_asset_mode_reason
      ?? "Workflow 文件资产变量当前未启用。",
  );
  const collaborationInventory = analyzeWorkflowVariables(
    nodes,
    edges,
    null,
    declarations,
  );
  const referencesFor = (variableName: string) =>
    collaborationInventory.find((item) => item.name === variableName)?.references ?? [];

  const migrateLegacyHandoffData = (
    candidate: WorkflowNode,
    taskVariable: string,
    taskValueKind: "receipt" | "task_id",
  ): { ok: true; data: WorkflowNodeData } | { ok: false; message: string } => {
    const legacyData = candidate.data;
    const executionMode = String(legacyData.executionMode ?? "manual");
    const automatic = executionMode === "xpert_auto";
    const legacyTarget = String(legacyData.targetAgent ?? "").trim();
    const legacyXpertReference = legacyTarget.startsWith("xpert:")
      ? legacyTarget.slice("xpert:".length)
      : "";
    const matchingXperts = publishedXperts.filter(
      (item) => (item.id === legacyXpertReference || item.slug === legacyXpertReference)
        && Boolean(item.published_version),
    );
    if (automatic && matchingXperts.length !== 1) {
      return {
        ok: false,
        message: "迁移被阻止：原自动目标无法唯一解析为一个已发布智能体版本。",
      };
    }
    if (!automatic && !legacyTarget) {
      return { ok: false, message: "迁移被阻止：请先填写人工队列名称。" };
    }
    const outputVariable = String(
      legacyData.outputVariable ?? "agent_handoff_id",
    ).trim();
    const incompatibleReferences = referencesFor(outputVariable).filter(
      (reference) => reference.nodeId !== candidate.id
        && !reference.expectedTypes.includes("json"),
    );
    if (incompatibleReferences.length) {
      return {
        ok: false,
        message: `迁移被阻止：${incompatibleReferences[0].nodeTitle} 仍把 ${outputVariable} 当作字符串使用。`,
      };
    }
    const matchedXpert = matchingXperts[0];
    const migrated: WorkflowNodeData = {
      ...legacyData,
      contractVersion: 2,
      taskVariable,
      taskValueKind,
      sourceAgent: String(legacyData.sourceAgent ?? "workflow").trim() || "workflow",
      targetMode: automatic ? "xpert" : "inbox",
      inboxTarget: automatic ? "" : legacyTarget,
      targetXpertId: matchedXpert?.id ?? "",
      targetVersion: matchedXpert?.published_version ?? 0,
      waitForCompletion: workflowBooleanValue(legacyData.waitForCompletion),
      timeoutSeconds: Number(legacyData.waitTimeoutSeconds ?? 120),
      resultVariable: String(legacyData.resultVariable ?? "handoff_result"),
      outputVariable: outputVariable || "handoff_receipt",
    };
    delete migrated.taskIdVariable;
    delete migrated.targetAgent;
    delete migrated.executionMode;
    delete migrated.waitTimeoutSeconds;
    return { ok: true, data: migrated };
  };

  const migrateCollaborationNode = (): string => {
    if (String(data.contractVersion ?? "1") === "2") {
      return "当前节点已经使用 V2 协作合同。";
    }
    if (data.kind === "agent_task") {
      const outputVariable = String(data.outputVariable ?? "agent_task_id").trim();
      const references = referencesFor(outputVariable).filter(
        (reference) => reference.nodeId !== node.id,
      );
      const linkedHandoffs: WorkflowNode[] = [];
      for (const reference of references) {
        const consumer = nodes.find((candidate) => candidate.id === reference.nodeId);
        const isLegacyHandoff = consumer?.data.kind === "agent_handoff"
          && String(consumer.data.contractVersion ?? "1") !== "2"
          && reference.field === "taskIdVariable"
          && edges.some((edge) => edge.source === node.id && edge.target === consumer.id);
        if (!consumer || !isLegacyHandoff) {
          const message = `迁移被阻止：${reference.nodeTitle} 仍把 ${outputVariable} 当作任务 ID 字符串使用。`;
          setMigrationNotice(message);
          return message;
        }
        if (!linkedHandoffs.some((candidate) => candidate.id === consumer.id)) {
          linkedHandoffs.push(consumer);
        }
      }
      const replacements: Array<{ nodeId: string; data: WorkflowNodeData }> = [{
        nodeId: node.id,
        data: {
          ...data,
          contractVersion: 2,
          outputVariable: outputVariable || "agent_task_receipt",
        },
      }];
      for (const handoff of linkedHandoffs) {
        const migrated = migrateLegacyHandoffData(
          handoff,
          outputVariable,
          "receipt",
        );
        if (!migrated.ok) {
          setMigrationNotice(migrated.message);
          return migrated.message;
        }
        replacements.push({ nodeId: handoff.id, data: migrated.data });
      }
      const message = linkedHandoffs.length
        ? `已原子升级任务节点及 ${linkedHandoffs.length} 个相连移交节点；可使用撤销恢复。`
        : "任务节点已升级为类型化凭证；可使用撤销恢复。";
      onReplaceNodeDataBatch(replacements, message);
      setMigrationNotice(message);
      return message;
    }
    if (data.kind === "agent_handoff") {
      const migrated = migrateLegacyHandoffData(
        node,
        String(data.taskIdVariable ?? "agent_task_id"),
        "task_id",
      );
      if (!migrated.ok) {
        setMigrationNotice(migrated.message);
        return migrated.message;
      }
      const message = "移交节点已升级；旧任务 ID 输入保留为兼容绑定，可使用撤销恢复。";
      onReplaceNodeDataBatch([{ nodeId: node.id, data: migrated.data }], message);
      setMigrationNotice(message);
      return message;
    }
    if (data.kind === "handoff_router") {
      const executionMode = String(data.executionMode ?? "manual");
      const automatic = executionMode === "xpert_auto";
      const legacyTarget = String(data.targetAgent ?? "").trim();
      const xpertReference = legacyTarget.startsWith("xpert:")
        ? legacyTarget.slice("xpert:".length)
        : "";
      const matchingXperts = publishedXperts.filter(
        (item) => (item.id === xpertReference || item.slug === xpertReference)
          && Boolean(item.published_version),
      );
      if ((automatic && matchingXperts.length !== 1) || (!automatic && !legacyTarget)) {
        const message = automatic
          ? "迁移被阻止：原自动目标无法唯一解析为一个已发布智能体版本。"
          : "迁移被阻止：请先填写人工队列名称。";
        setMigrationNotice(message);
        return message;
      }
      const outputVariable = String(data.outputVariable ?? "agent_handoff_id").trim();
      const incompatibleReferences = referencesFor(outputVariable).filter(
        (reference) => reference.nodeId !== node.id
          && !reference.expectedTypes.includes("json"),
      );
      if (incompatibleReferences.length) {
        const message = `迁移被阻止：${incompatibleReferences[0].nodeTitle} 仍把 ${outputVariable} 当作字符串使用。`;
        setMigrationNotice(message);
        return message;
      }
      const matchedXpert = matchingXperts[0];
      const migrated: WorkflowNodeData = {
        ...data,
        contractVersion: 2,
        targetMode: automatic ? "xpert" : "inbox",
        inboxTarget: automatic ? "" : legacyTarget,
        targetXpertId: matchedXpert?.id ?? "",
        targetVersion: matchedXpert?.published_version ?? 0,
        waitForCompletion: workflowBooleanValue(data.waitForCompletion),
        timeoutSeconds: Number(data.waitTimeoutSeconds ?? 120),
        resultVariable: String(data.resultVariable ?? "handoff_result"),
        outputVariable: outputVariable || "handoff_receipt",
      };
      delete migrated.targetAgent;
      delete migrated.executionMode;
      delete migrated.waitTimeoutSeconds;
      const message = "创建并移交节点已升级为原子 V2 合同；可使用撤销恢复。";
      onReplaceNodeDataBatch([{ nodeId: node.id, data: migrated }], message);
      setMigrationNotice(message);
      return message;
    }
    return "该节点不支持协作合同升级。";
  };
  const legacyCodeMigration =
    data.kind === "code" && !isSafeTextV2(data)
      ? migrateLegacyCodeNode(data, migrationAvailableVariables)
      : null;
  const legacyTemplateMigration = data.kind === "template_transform"
    ? migrateLegacyTemplateTransform(data, migrationAvailableVariables)
    : null;
  const legacyVariablePackMigration = data.kind === "variable_aggregator"
    && !isVariablePackV2(data)
    ? migrateLegacyVariableAggregator(
        node,
        nodes,
        edges,
        migrationAvailableVariables,
      )
    : null;
  const legacyIterationMigration = data.kind === "iteration" && !isIterationV2(data)
    ? migrateLegacyIteration(
        data,
        migrationAvailableVariables,
        migrationVariableDescriptors.find(
          (variable) => variable.name === String(data.inputVariable ?? ""),
        )?.valueType,
      )
    : null;
  const runtimeMiddlewareConfig = isRecord(data.runtimeMiddlewareConfig)
    ? data.runtimeMiddlewareConfig
    : undefined;
  const skillCreatorMiddleware = isSkillCreatorMiddleware(data);
  const legacySkillCreatorMiddleware = isLegacySkillCreatorMiddleware(data);
  const skillRuntimeMiddleware =
    data.kind === "runtime_middleware"
    && data.runtimeMiddlewareId === "skills_runtime";
  const pluginHookMiddleware =
    data.kind === "runtime_middleware"
    && data.runtimeMiddlewareId === "plugin_hooks";
  const pluginHookMode = String(runtimeMiddlewareConfig?.hook_mode || "legacy_argv");
  const legacyPluginHookMiddleware = pluginHookMiddleware && pluginHookMode !== "typed_v2";
  const contentPolicyMiddleware =
    data.kind === "runtime_middleware"
    && data.runtimeMiddlewareId === "content_policy";
  const visibleRuntimeMiddlewareFields = (data.runtimeMiddlewareFields ?? []).filter(
    (field) => {
      if (contentPolicyMiddleware) return false;
      if (
        skillRuntimeMiddleware
        && field.name !== "skill_ids"
        && !showSkillAdvancedOptions
      ) return false;
      if (!skillCreatorMiddleware) return true;
      if (field.name === "authoring_mode") return false;
      if (legacySkillCreatorMiddleware) return true;
      return !["allow_create", "allow_update", "allowed_draft_ids"].includes(
        field.name,
      );
    },
  ).filter((field) => {
    if (!pluginHookMiddleware) return true;
    if (field.name === "hook_mode") return false;
    if (!legacyPluginHookMiddleware && field.name === "fail_closed") return false;
    return true;
  });
  const selectableInstalledSkills = pluginHookMiddleware && !legacyPluginHookMiddleware
    ? installedSkills.filter((skill) => skill.hook_capability?.runnable)
    : installedSkills;
  const appendTrustedSkill = (skillId: string) => {
    if (!skillId || !runtimeMiddlewareConfig) return;
    onRuntimeMiddlewareConfigChange(
      node.id,
      "skill_ids",
      updateSkillRuntimeIds(runtimeMiddlewareConfig.skill_ids, skillId, "add"),
    );
  };
  const selectedRuntimeSkillIds = parseSkillRuntimeIds(
    runtimeMiddlewareConfig?.skill_ids,
  );
  const selectedHookSkills = pluginHookMiddleware
    ? selectedRuntimeSkillIds.flatMap((skillId) => {
        const skill = installedSkills.find((item) => item.skill_id === skillId);
        return skill ? [skill] : [];
      })
    : [];
  const hookUpgradeReady = Boolean(
    selectedRuntimeSkillIds.length
    && selectedHookSkills.length === selectedRuntimeSkillIds.length
    && selectedHookSkills.every((skill) => skill.hook_capability?.runnable),
  );
  const boundMiddlewares =
    data.kind === "workflow_agent"
      ? edges
          .filter(
            (edge) =>
              edge.target === node.id && edge.targetHandle === "middleware",
          )
          .map((edge) => nodes.find((candidate) => candidate.id === edge.source))
          .filter(
            (candidate): candidate is WorkflowNode =>
              candidate?.data.kind === "runtime_middleware",
          )
          .sort((left, right) => {
            const priorityDifference =
              Number(left.data.middlewarePriority ?? 100) -
              Number(right.data.middlewarePriority ?? 100);
            return priorityDifference || left.id.localeCompare(right.id);
          })
      : [];
  const boundResources =
    data.kind === "workflow_agent"
      ? edges
          .filter(
            (edge) =>
              edge.target === node.id &&
              ["expert", "knowledge", "toolset", "plugin"].includes(
                edge.targetHandle ?? "",
              ),
          )
          .map((edge) => nodes.find((candidate) => candidate.id === edge.source))
          .filter(
            (candidate): candidate is WorkflowNode =>
              candidate?.data.kind === "external_xpert" ||
              candidate?.data.kind === "knowledge_base" ||
              candidate?.data.kind === "toolset_resource" ||
              candidate?.data.kind === "plugin_resource",
          )
      : [];
  const updateRuntimeMiddlewareConfig = (fieldName: string, value: unknown) =>
    onRuntimeMiddlewareConfigChange(node.id, fieldName, value);
  const skillCatalogApprovalState =
    data.kind === "runtime_middleware" &&
    data.runtimeMiddlewareId === "skills_runtime"
      ? getSkillCatalogApprovalState(nodes, edges, node.id)
      : null;

  return (
    <div className="space-y-4">
      <Field label="工位名称">
        <input
          className={textInputClass()}
          onChange={(event) => update({ title: event.target.value })}
          value={data.title}
        />
      </Field>

      <Field label="说明">
        <textarea
          className={`${textInputClass()} min-h-20 resize-none leading-6`}
          onChange={(event) => update({ description: event.target.value })}
          value={data.description}
        />
      </Field>

      {[
        "json_serialize",
        "json_deserialize",
        "annotation",
        "data_table_query",
        "data_table_insert",
        "data_table_update",
        "data_table_delete",
      ].includes(data.kind) ? (
        <WorkflowTypedDataNodeConfig
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          node={node}
          nodes={nodes}
          onChange={update}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}

      {data.kind === "input" ? (
        <Field label="输入变量名">
          <WorkflowVariableField
            contract={variableContract}
            edges={edges}
            fieldName="variableName"
            node={node}
            nodes={nodes}
            onChange={(value) => update({ variableName: value })}
            value={data.variableName ?? ""}
          />
        </Field>
      ) : null}

      {(["scheduled_start", "http_event_entry", "form_event_entry", "rss_event_entry", "email_event_entry", "failure_event_entry", "workflow_call_entry", "invoke_workflow", "suspend_wait", "http_event_reply"].includes(data.kind)
        || (data.kind === "iteration" && isIterationV2(data))) ? (
        <WorkflowDeploymentNodeConfig
          currentProjectId={workflowId.startsWith("wf_") ? workflowId : undefined}
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          featureDisabledReason={String((data.kind === "email_event_entry" ? emailRegistryMetadata : rssRegistryMetadata).feature_disabled_reason ?? "")}
          featureEnabled={data.kind === "rss_event_entry" ? rssRegistryMetadata.feature_enabled === true : data.kind === "email_event_entry" ? emailRegistryMetadata.feature_enabled === true : true}
          node={node}
          nodes={nodes}
          onChange={update}
        />
      ) : null}

      {["condition", "terminate_error", "multi_route", "list_operation", "data_aggregate", "data_merge", "dataset_compare", "object_transform"].includes(data.kind) ? (
        <WorkflowControlDataNodeConfig
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          node={node}
          nodes={nodes}
          onChange={update}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}

      {["time_tool", "file_output"].includes(data.kind) ? (
        <WorkflowFileDataNodeConfig
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          node={node}
          nodes={nodes}
          onChange={update}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}

      {data.kind === "llm" ? (
        <>
          <Field label="调用模型">
            <select
              className={textInputClass()}
              onChange={(event) => update({ modelId: event.target.value })}
              value={data.modelId ?? DEFAULT_WORKFLOW_AGENT_MODEL_ID}
            >
              {models.map((model) => (
                <option
                  className="bg-slate-950 text-white"
                  key={model.id}
                  value={model.id}
                >
                  {model.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="提示词（支持 {{变量}}）">
            <WorkflowVariableField
              className="min-h-36 resize-none leading-6"
              contract={variableContract}
              edges={edges}
              fieldName="prompt"
              multiline
              node={node}
              nodes={nodes}
              onChange={(value) => update({ prompt: value })}
              value={data.prompt ?? ""}
            />
          </Field>
          <Field label="输出变量名">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "code" ? (
        isSafeTextV2(data) ? (
          <>
            <div className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-50">
              输入会按稳定规则转成文本，再执行受控操作；这里不会运行任意代码。
              <button
                className="ml-1 font-semibold underline underline-offset-4"
                onClick={onOpenVariableCenter}
                type="button"
              >
                打开变量中心
              </button>
            </div>
            <Field label="文本操作">
              <select
                className={textInputClass()}
                onChange={(event) => update({ operation: event.target.value as SafeTextOperation })}
                value={String(data.operation ?? "upper")}
              >
                <option className="bg-slate-950" value="upper">转为大写</option>
                <option className="bg-slate-950" value="lower">转为小写</option>
                <option className="bg-slate-950" value="replace">替换文本</option>
                <option className="bg-slate-950" value="concat">追加文本</option>
              </select>
            </Field>
            <Field label="输入变量">
              <WorkflowVariableField
                contract={variableContract}
                edges={edges}
                fieldName="inputVariable"
                node={node}
                nodes={nodes}
                onChange={(value) => update({ inputVariable: value })}
                value={data.inputVariable ?? ""}
              />
            </Field>
            <Field label="输出变量">
              <WorkflowVariableField
                contract={variableContract}
                edges={edges}
                fieldName="outputVariable"
                node={node}
                nodes={nodes}
                onChange={(value) => update({ outputVariable: value })}
                value={data.outputVariable ?? ""}
              />
            </Field>
            {data.operation === "replace" ? (
              <div className="grid grid-cols-2 gap-3">
                <Field label="查找文本">
                  <input
                    className={textInputClass()}
                    onChange={(event) => update({ replaceFrom: event.target.value })}
                    value={data.replaceFrom ?? ""}
                  />
                </Field>
                <Field label="替换为">
                  <input
                    className={textInputClass()}
                    onChange={(event) => update({ replaceTo: event.target.value })}
                    value={data.replaceTo ?? ""}
                  />
                </Field>
              </div>
            ) : null}
            {data.operation === "concat" ? (
              <Field label="追加文本">
                <input
                  className={textInputClass()}
                  onChange={(event) => update({ concatValue: event.target.value })}
                  value={data.concatValue ?? ""}
                />
              </Field>
            ) : null}
          </>
        ) : (
          <>
            <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
              <p>
                {data.codeOperation === "python"
                  ? "这是 Python legacy 历史配置，仅用于读取既有内容；当前草稿不能执行或发布。"
                  : legacyCodeMigration?.ok
                    ? "这是 Code V1 旧配置，可继续打开和手动运行；发布新版本前必须显式迁移。"
                    : "这是无法安全解释的 Code V1 配置，当前草稿不能执行或迁移；请按下方原因修正。"}
              </p>
              <p className="mt-1 text-amber-100/80">{legacyCodeMigration?.message}</p>
              {legacyCodeMigration?.ok && legacyCodeMigration.data ? (
                <button
                  className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
                  onClick={() => {
                    onReplaceNodeData(node.id, legacyCodeMigration.data!);
                    setMigrationNotice(legacyCodeMigration.message);
                  }}
                  type="button"
                >
                  升级为安全文本加工 V2
                </button>
              ) : null}
            </div>
            <Field label="旧版操作">
              {data.codeOperation === "python" ? (
                <div
                  aria-label="Python legacy，只读"
                  className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-slate-300"
                >
                  Python legacy（只读）
                </div>
              ) : (
                <select
                  className={textInputClass()}
                  onChange={(event) => update({ codeOperation: event.target.value as CodeOperation })}
                  value={data.codeOperation ?? "upper"}
                >
                  <option className="bg-slate-950" value="upper">转大写</option>
                  <option className="bg-slate-950" value="lower">转小写</option>
                  <option className="bg-slate-950" value="replace">替换</option>
                  <option className="bg-slate-950" value="concat">拼接</option>
                </select>
              )}
            </Field>
            <Field label="旧版输入变量">
              <WorkflowVariableField
                contract={variableContract}
                edges={edges}
                fieldName="codeInputVariable"
                node={node}
                nodes={nodes}
                onChange={(value) => update({ codeInputVariable: value })}
                value={data.codeInputVariable ?? ""}
              />
            </Field>
            <Field label="旧版输出变量">
              <WorkflowVariableField
                contract={variableContract}
                edges={edges}
                fieldName="codeOutputVariable"
                node={node}
                nodes={nodes}
                onChange={(value) => update({ codeOutputVariable: value })}
                value={data.codeOutputVariable ?? ""}
              />
            </Field>
            {data.codeOperation === "replace" ? (
              <div className="grid grid-cols-2 gap-3">
                <Field label="把">
                  <input className={textInputClass()} onChange={(event) => update({ replaceFrom: event.target.value })} value={data.replaceFrom ?? ""} />
                </Field>
                <Field label="替换为">
                  <input className={textInputClass()} onChange={(event) => update({ replaceTo: event.target.value })} value={data.replaceTo ?? ""} />
                </Field>
              </div>
            ) : null}
            {data.codeOperation === "concat" ? (
              <Field label="追加内容">
                <input className={textInputClass()} onChange={(event) => update({ concatValue: event.target.value })} value={data.concatValue ?? ""} />
              </Field>
            ) : null}
            {data.codeOperation === "python" ? (
              <Field label="旧版 Python 代码">
                <textarea
                  className={`${textInputClass()} min-h-40 resize-none font-mono text-xs leading-5`}
                  readOnly
                  value={data.pythonCode ?? ""}
                />
              </Field>
            ) : null}
            {migrationNotice ? <p aria-live="polite" className="text-xs text-emerald-200">{migrationNotice}</p> : null}
          </>
        )
      ) : null}

      {data.kind === "variable_assign" ? (
        <>
          {String(data.contractVersion ?? "1") !== "2" ? (
            <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
              <p>这是旧版文本赋值配置。发布新版本前需要显式升级；升级后仍可撤销。</p>
              <button
                className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950"
                onClick={() => {
                  onReplaceNodeData(node.id, {
                    kind: "variable_assign",
                    title: data.title,
                    description: "把类型化字面量、变量副本或模板文本写入变量。",
                    contractVersion: 2,
                    outputVariable: String(data.variableName ?? "assigned_text"),
                    valueSource: "template",
                    template: String(data.template ?? ""),
                  });
                  setMigrationNotice("变量赋值已升级为 V2 模板模式。");
                }}
                type="button"
              >
                升级为 V2
              </button>
            </div>
          ) : (
            <>
              <Field label="值来源">
                <select
                  className={textInputClass()}
                  onChange={(event) => {
                    const valueSource = event.target.value as "literal" | "variable" | "template";
                    update({
                      valueSource,
                      ...(valueSource === "literal" && data.literalValue === undefined
                        ? { literalValue: "" }
                        : {}),
                    });
                  }}
                  value={data.valueSource ?? "template"}
                >
                  <option value="literal">固定类型化值</option>
                  <option value="variable">复制已有变量</option>
                  <option value="template">渲染文本模板</option>
                </select>
              </Field>
              {data.valueSource === "literal" ? (
                <Field label="固定值">
                  <select
                    className={textInputClass()}
                    onChange={(event) => {
                      const type = event.target.value;
                      update({
                        literalValue:
                          type === "number" ? 0
                            : type === "boolean" ? false
                              : type === "null" ? null
                                : type === "json" ? {}
                                  : "",
                      });
                    }}
                    value={literalKind(data.literalValue as WorkflowValue | undefined)}
                  >
                    <option value="text">文本</option>
                    <option value="number">数字</option>
                    <option value="boolean">布尔</option>
                    <option value="null">null</option>
                    <option value="json">对象或数组</option>
                  </select>
                  {literalKind(data.literalValue as WorkflowValue | undefined) === "text" ? (
                    <input
                      className={`${textInputClass()} mt-2`}
                      onChange={(event) => update({ literalValue: event.target.value })}
                      value={String(data.literalValue ?? "")}
                    />
                  ) : literalKind(data.literalValue as WorkflowValue | undefined) === "number" ? (
                    <input
                      className={`${textInputClass()} mt-2`}
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        if (Number.isFinite(value)) update({ literalValue: value });
                      }}
                      type="number"
                      value={String(data.literalValue ?? 0)}
                    />
                  ) : literalKind(data.literalValue as WorkflowValue | undefined) === "boolean" ? (
                    <select
                      className={`${textInputClass()} mt-2`}
                      onChange={(event) => update({ literalValue: event.target.value === "true" })}
                      value={data.literalValue === true ? "true" : "false"}
                    >
                      <option value="true">true</option>
                      <option value="false">false</option>
                    </select>
                  ) : literalKind(data.literalValue as WorkflowValue | undefined) === "null" ? (
                    <p className="mt-2 rounded-md bg-white/[0.04] px-3 py-2 text-xs text-slate-400">固定写入 null</p>
                  ) : (
                    <div className="mt-2">
                      <JsonLiteralEditor
                        ariaLabel="固定 JSON 值"
                        onChange={(value) => update({ literalValue: value })}
                        value={(data.literalValue ?? {}) as WorkflowValue}
                      />
                    </div>
                  )}
                </Field>
              ) : null}
              {data.valueSource === "variable" ? (
                <Field label="来源变量">
                  <WorkflowVariableField
                    contract={variableContract}
                    edges={edges}
                    fieldName="sourceVariable"
                    node={node}
                    nodes={nodes}
                    onChange={(value) => update({ sourceVariable: value })}
                    value={data.sourceVariable ?? ""}
                  />
                </Field>
              ) : null}
              {data.valueSource === "template" ? (
                <Field label="文本模板（支持 {{变量}}）">
                  <WorkflowVariableField
                    className="min-h-28 resize-none leading-6"
                    contract={variableContract}
                    declarations={declarations}
                    edges={edges}
                    fieldName="template"
                    multiline
                    node={node}
                    nodes={nodes}
                    onChange={(value) => update({ template: value })}
                    value={data.template ?? ""}
                  />
                </Field>
              ) : null}
              <Field label="输出变量">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="outputVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ outputVariable: value })}
                  value={data.outputVariable ?? ""}
                />
              </Field>
            </>
          )}
          {migrationNotice ? <p className="text-xs text-emerald-200">{migrationNotice}</p> : null}
        </>
      ) : null}

      {data.kind === "template_transform" ? (
        <>
          <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
            <p>模板转换已退役，旧流程仍可打开和运行。发布新版本前，请迁移到变量赋值 V2 模板模式。</p>
            <p className="mt-1 text-amber-100/80">{legacyTemplateMigration?.message}</p>
            {legacyTemplateMigration?.ok && legacyTemplateMigration.data ? (
              <button
                className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
                onClick={() => {
                  onReplaceNodeData(node.id, legacyTemplateMigration.data!);
                  setMigrationNotice(legacyTemplateMigration.message);
                }}
                type="button"
              >
                迁移为变量赋值 V2
              </button>
            ) : null}
          </div>
          <Field label="模板内容（支持 {{变量}}）">
            <WorkflowVariableField
              className="min-h-36 resize-none leading-6"
              contract={variableContract}
              edges={edges}
              fieldName="template"
              multiline
              node={node}
              nodes={nodes}
              onChange={(value) => update({ template: value })}
              value={data.template ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
          <button
            className="text-left text-xs font-semibold text-cyan-200 underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200"
            onClick={onOpenVariableCenter}
            type="button"
          >
            打开变量中心检查引用
          </button>
          {migrationNotice ? <p aria-live="polite" className="text-xs text-emerald-200">{migrationNotice}</p> : null}
        </>
      ) : null}

      {data.kind === "variable_aggregator" ? (
        <>
          {!isVariablePackV2(data) ? (
            <div className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-3 text-xs leading-5 text-amber-100">
              <p>这是旧版变量聚合配置，可继续打开和手动运行；发布前必须显式迁移。</p>
              {legacyVariablePackMigration?.ok ? (
                <button
                  className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
                  onClick={() => {
                    if (
                      !String(data.outputTemplate ?? "")
                      && !window.confirm(
                        "迁移后输出会从 JSON 字符串变为真正的 JSON 对象。节点 ID、位置和连线不变，是否继续？",
                      )
                    ) return;
                    onReplaceNodeData(node.id, legacyVariablePackMigration.data!);
                    setMigrationNotice(legacyVariablePackMigration.message);
                  }}
                  type="button"
                >
                  {String(data.outputTemplate ?? "")
                    ? "迁移为变量赋值 V2"
                    : "迁移为变量打包 V2"}
                </button>
              ) : (
                <p className="mt-2 text-amber-200">
                  迁移被阻止：{legacyVariablePackMigration?.message ?? "旧配置无法识别。"}
                </p>
              )}
            </div>
          ) : null}
          {isVariablePackV2(data) ? (
            <Field label="打包字段（1–50 项）">
              <div className="space-y-2">
                {(data.bindings ?? []).map((binding, index, bindings) => (
                  <div
                    className="rounded-lg border border-white/10 bg-white/[0.03] p-2"
                    key={binding.id}
                  >
                    <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
                      <WorkflowVariableField
                        ariaLabel={`${binding.id} 来源变量`}
                        contract={variableContract}
                        declarations={declarations}
                        edges={edges}
                        fieldName="sourceVariable"
                        node={node}
                        nodes={nodes}
                        onChange={(sourceVariable) => {
                          const next = [...bindings];
                          next[index] = { ...binding, sourceVariable };
                          update({ bindings: next });
                        }}
                        placeholder="选择来源变量"
                        value={binding.sourceVariable}
                      />
                      <input
                        aria-label={`${binding.id} 输出字段`}
                        className={textInputClass()}
                        onChange={(event) => {
                          const next = [...bindings];
                          next[index] = {
                            ...binding,
                            outputField: event.target.value,
                          };
                          update({ bindings: next });
                        }}
                        placeholder="对象字段名"
                        value={binding.outputField}
                      />
                      <div className="flex gap-1">
                        <button
                          aria-label={`${binding.id} 上移`}
                          className="rounded border border-white/10 px-2 text-slate-200 disabled:opacity-30"
                          disabled={index === 0}
                          onClick={() => {
                            const next = [...bindings];
                            [next[index - 1], next[index]] = [next[index], next[index - 1]];
                            update({ bindings: next });
                          }}
                          type="button"
                        >
                          ↑
                        </button>
                        <button
                          aria-label={`${binding.id} 下移`}
                          className="rounded border border-white/10 px-2 text-slate-200 disabled:opacity-30"
                          disabled={index === bindings.length - 1}
                          onClick={() => {
                            const next = [...bindings];
                            [next[index], next[index + 1]] = [next[index + 1], next[index]];
                            update({ bindings: next });
                          }}
                          type="button"
                        >
                          ↓
                        </button>
                        <button
                          aria-label={`${binding.id} 删除`}
                          className="rounded border border-rose-300/20 px-2 text-rose-200 disabled:opacity-30"
                          disabled={bindings.length <= 1}
                          onClick={() => update({
                            bindings: bindings.filter((item) => item.id !== binding.id),
                          })}
                          type="button"
                        >
                          ×
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
                <button
                  className="rounded-md border border-white/10 px-3 py-1.5 text-xs font-semibold text-cyan-100 disabled:opacity-40"
                  disabled={(data.bindings ?? []).length >= 50}
                  onClick={() => {
                    const bindings = data.bindings ?? [];
                    const used = new Set(bindings.map((binding) => binding.id));
                    let index = 1;
                    while (used.has(`binding_${index}`)) index += 1;
                    const next: WorkflowVariablePackBinding = {
                      id: `binding_${index}`,
                      sourceVariable: "",
                      outputField: `field_${index}`,
                    };
                    update({ bindings: [...bindings, next] });
                  }}
                  type="button"
                >
                  添加字段
                </button>
              </div>
            </Field>
          ) : (
            <>
              <Field label="旧变量名列表（逗号分隔）">
                <WorkflowVariableField
                  contract={variableContract}
                  declarations={declarations}
                  edges={edges}
                  fieldName="variableNames"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ variableNames: value })}
                  value={data.variableNames ?? ""}
                />
              </Field>
              <Field label="旧输出模板（可选，支持 {name} / {value}）">
                <textarea
                  className={`${textInputClass()} min-h-24 resize-none font-mono text-xs leading-5`}
                  onChange={(event) => update({ outputTemplate: event.target.value })}
                  value={data.outputTemplate ?? ""}
                />
              </Field>
            </>
          )}
          <Field label="输出变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
          <button
            className="text-left text-xs font-semibold text-cyan-200 underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200"
            onClick={onOpenVariableCenter}
            type="button"
          >
            打开变量中心检查来源与输出
          </button>
          {migrationNotice ? (
            <p aria-live="polite" className="text-xs text-emerald-200">
              {migrationNotice}
            </p>
          ) : null}
        </>
      ) : null}

      {data.kind === "parameter_extractor" ? (
        <ParameterExtractorConfig
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          models={models}
          node={node}
          nodes={nodes}
          onChange={update}
          onMigrate={() => onMigrateTypedAiNode(node.id)}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}

      {data.kind === "knowledge_retrieval" ? (
        <KnowledgeRetrievalNodeConfig
          data={data}
          declarations={declarations}
          edges={edges}
          node={node}
          nodes={nodes}
          update={update}
          variableContract={variableContract}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}

      {data.kind === "knowledge_write_proposal" ? (
        <KnowledgeWriteProposalNodeConfig
          data={data}
          edges={edges}
          featureDisabledReason={String(
            knowledgeProposalMetadata.feature_disabled_reason ?? "",
          )}
          featureEnabled={knowledgeProposalMetadata.feature_enabled === true}
          node={node}
          nodes={nodes}
          onOpenVariableCenter={onOpenVariableCenter}
          update={update}
          variableContract={variableContract}
        />
      ) : null}

      {data.kind === "knowledge_citation" ? (
        <>
          <LegacyKnowledgeCitationConfig
            data={data}
            edges={edges}
            node={node}
            nodes={nodes}
            update={update}
            variableContract={variableContract}
            onMigrate={() => {
            const knowledgeBaseId = String(data.knowledgeBaseId ?? "").trim();
            const queryVariable = String(data.queryVariable ?? "").trim();
            const outputVariable = String(data.outputVariable ?? "").trim();
            const topK = Number(data.top_k ?? data.topK ?? 4);
            if (!knowledgeBaseId || !queryVariable || !outputVariable || !Number.isInteger(topK) || topK < 1 || topK > 10) {
              setMigrationNotice("迁移被阻止：请先补齐知识库、查询变量、输出变量和合法 Top K。");
              return;
            }
            if (!window.confirm("迁移会保留节点 ID、位置和连线，但输出将从 Citation JSON 字符串变为类型化检索结果。是否继续？")) return;
            onReplaceNodeData(node.id, {
              kind: "knowledge_retrieval",
              title: "知识检索",
              description: "检索指定知识库的活动版本。",
              contractVersion: 2,
              knowledgeBaseId,
              queryVariable,
              top_k: String(topK),
              returnMode: "result",
              outputVariable,
            });
            setMigrationNotice("知识引用已迁移为知识检索 V2；请检查下游类型。");
            }}
          />
          {migrationNotice ? <p className="text-xs text-amber-100">{migrationNotice}</p> : null}
        </>
      ) : null}

      {data.kind === "document_extractor" ? (
        <>
          {data.sourcePathVariable && !data.assetIdVariable ? (
            <>
              <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
                这是旧版路径配置，仅保留一个发布周期的读取兼容。当前不能修改或新建路径型配置，请迁移到文件资产变量。
              </div>
              <Field label="旧路径变量（只读）">
                <input
                  aria-readonly="true"
                  className={`${textInputClass()} cursor-not-allowed opacity-70`}
                  readOnly
                  value={String(data.sourcePathVariable)}
                />
              </Field>
            </>
          ) : Number(data.contractVersion ?? 0) === 2
            || (Number(data.contractVersion ?? 0) === 0 && Boolean(data.assetIdVariable)) ? (
            <>
              <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
                <p>{Number(data.contractVersion ?? 0) === 2 ? "这是 V2 文件提取配置，可继续运行和发布。" : "这是旧安全文件资产配置，仍可兼容运行。"} 升级后仍使用同一文件变量，但输出会进入 V3 内容合同。</p>
                <button
                  className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950"
                  onClick={() => {
                    const migration = migrateDocumentExtractorFileToV3(data);
                    if (!migration.data) {
                      setMigrationNotice(`迁移被阻止：${migration.reason}`);
                      return;
                    }
                    if (!window.confirm("升级会保留节点 ID、位置和连线，并将输出设为 V3 纯文本模式。是否继续？")) return;
                    onReplaceNodeData(node.id, migration.data);
                    setMigrationNotice("已升级为内容解析 V3 文件模式；请检查下游是否仍按文本读取输出。");
                  }}
                  type="button"
                >
                  升级到内容解析 V3
                </button>
              </div>
              <Field label="文件资产变量">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="assetIdVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ assetIdVariable: value })}
                  value={data.assetIdVariable ?? ""}
                />
              </Field>
              <Field label="输出变量">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="outputVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ outputVariable: value })}
                  value={data.outputVariable ?? ""}
                />
              </Field>
            </>
          ) : Number(data.contractVersion ?? 0) === 3 ? (
            <>
              <div className="rounded-lg border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs leading-5 text-sky-50">
                解析结果默认保留标题、章节和结构数据。来自网页或文件的正文会标记为不可信内容；节点不会执行页面脚本、XML 实体或其中的指令。
              </div>
              <Field label="内容来源">
                <select
                  className={textInputClass()}
                  onChange={(event) => {
                    const sourceMode = event.target.value as "http_response" | "file_asset";
                    update(
                      sourceMode === "http_response"
                        ? {
                            sourceMode,
                            inputVariable: data.inputVariable || "http_response",
                            assetIdVariable: undefined,
                          }
                        : {
                            sourceMode,
                            inputVariable: undefined,
                            assetIdVariable: data.assetIdVariable || "selected_file_asset_id",
                            format: "auto",
                          },
                    );
                  }}
                  value={data.sourceMode ?? "http_response"}
                >
                  <option className="bg-slate-950" value="http_response">
                    安全 HTTP 响应
                  </option>
                  <option
                    className="bg-slate-950"
                    disabled={!documentFileAssetModeEnabled}
                    value="file_asset"
                  >
                    文件资产{documentFileAssetModeEnabled ? "" : "（当前未启用）"}
                  </option>
                </select>
              </Field>
              {data.sourceMode === "file_asset" ? (
                <>
                  {!documentFileAssetModeEnabled ? (
                    <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-50">
                      {documentFileAssetModeReason}
                    </div>
                  ) : null}
                  <Field label="文件资产变量">
                    <WorkflowVariableField
                      contract={variableContract}
                      edges={edges}
                      fieldName="assetIdVariable"
                      node={node}
                      nodes={nodes}
                      onChange={(value) => update({ assetIdVariable: value })}
                      value={data.assetIdVariable ?? ""}
                    />
                  </Field>
                </>
              ) : (
                <Field label="HTTP 响应变量">
                  <WorkflowVariableField
                    contract={variableContract}
                    edges={edges}
                    fieldName="inputVariable"
                    node={node}
                    nodes={nodes}
                    onChange={(value) => update({ inputVariable: value })}
                    value={data.inputVariable ?? ""}
                  />
                </Field>
              )}
              <Field label="内容格式">
                <select
                  className={textInputClass()}
                  disabled={data.sourceMode === "file_asset"}
                  onChange={(event) => update({ format: event.target.value as WorkflowNodeData["format"] })}
                  value={data.sourceMode === "file_asset" ? "auto" : (data.format ?? "auto")}
                >
                  <option className="bg-slate-950" value="auto">根据 Content-Type 自动识别</option>
                  <option className="bg-slate-950" value="html">HTML 网页</option>
                  <option className="bg-slate-950" value="markdown">Markdown 文本</option>
                  <option className="bg-slate-950" value="xml">XML 数据</option>
                </select>
                {data.sourceMode !== "file_asset" && data.format === "auto" ? (
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    text/plain 或缺少 Content-Type 时请明确选择格式，系统不会猜测正文。
                  </p>
                ) : null}
              </Field>
              <Field label="输出方式">
                <select
                  className={textInputClass()}
                  onChange={(event) => update({ outputMode: event.target.value as "structured" | "text" })}
                  value={data.outputMode ?? "structured"}
                >
                  <option className="bg-slate-950" value="structured">结构化对象（推荐）</option>
                  <option className="bg-slate-950" value="text">带不可信边界的纯文本</option>
                </select>
              </Field>
              <Field label="输出变量">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="outputVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ outputVariable: value })}
                  value={data.outputVariable ?? ""}
                />
              </Field>
            </>
          ) : null}
          {migrationNotice ? <p className="text-xs text-amber-100">{migrationNotice}</p> : null}
        </>
      ) : null}

      {data.kind === "vision_understanding" ? (
        <>
          <div className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-50">
            运行前为附件变量选择图片或 PDF。节点只读取当前私有运行显式共享的附件，不会创建知识索引。
          </div>
          <Field label="附件资产变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="assetIdVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ assetIdVariable: value })}
              value={data.assetIdVariable ?? ""}
            />
          </Field>
          <div>
            <button
              className="flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-cyan-300/30 bg-cyan-300/10 px-3 py-2 text-sm font-semibold text-cyan-50 transition hover:bg-cyan-300/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.03] disabled:text-slate-500"
              disabled={!String(data.assetIdVariable ?? "").trim()}
              onClick={() =>
                onOpenRunFileInput(String(data.assetIdVariable ?? "").trim())
              }
              type="button"
            >
              <Upload aria-hidden="true" className="h-4 w-4" />
              上传或选择运行附件
            </button>
            <p className="mt-1.5 text-xs leading-5 text-slate-500">
              附件按本次运行选择，不会把文件正文写入节点草稿。
            </p>
          </div>
          <Field label="视觉模型">
            <select
              className={textInputClass()}
              onChange={(event) => update({ visionModelId: event.target.value })}
              value={data.visionModelId ?? ""}
            >
              <option value="">选择支持图片输入的模型</option>
              {visionModels.map((model) => (
                <option key={model.model_id} value={model.model_id}>
                  {model.label}
                </option>
              ))}
            </select>
          </Field>
          {visionCapabilityError ? (
            <p className="text-xs leading-5 text-amber-200">
              {visionCapabilityError}
            </p>
          ) : null}
          <Field label="PDF 页面策略">
            <select
              className={textInputClass()}
              onChange={(event) =>
                update({
                  pdfPageStrategy: event.target.value as
                    | "auto"
                    | "all"
                    | "scanned_only",
                })
              }
              value={data.pdfPageStrategy ?? "auto"}
            >
              <option value="auto">自动选择</option>
              <option value="scanned_only">仅扫描页</option>
              <option value="all">全部页面</option>
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="最大页数">
              <input
                className={textInputClass()}
                max={200}
                min={1}
                onChange={(event) => update({ maxPages: event.target.value })}
                type="number"
                value={data.maxPages ?? 100}
              />
            </Field>
            <Field label="最大图像边长">
              <input
                className={textInputClass()}
                max={4096}
                min={512}
                onChange={(event) => update({ maxImageEdge: event.target.value })}
                step={128}
                type="number"
                value={data.maxImageEdge ?? 2048}
              />
            </Field>
          </div>
          <Field label="失败策略">
            <select
              className={textInputClass()}
              onChange={(event) =>
                update({
                  failurePolicy: event.target.value as
                    | "continue_on_error"
                    | "strict",
                })
              }
              value={data.failurePolicy ?? "continue_on_error"}
            >
              <option value="continue_on_error">保留成功页面</option>
              <option value="strict">任一失败即停止</option>
            </select>
          </Field>
          <Field label="输出变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "human_intervention" ? (
        <>
          <div className="rounded-lg border border-sky-300/25 bg-sky-300/10 px-3 py-2 text-xs leading-5 text-sky-50">
            运行到这里会持久暂停；服务重启后仍可从审批记录恢复。超时不会自动批准。
          </div>
          {String(data.contractVersion ?? "1") !== "2" ? (
            <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
              <p>这是旧版人工介入配置。发布新版本前需要显式升级。</p>
              <button
                className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950"
                onClick={() => {
                  onReplaceNodeData(node.id, {
                    kind: "human_intervention",
                    title: data.title,
                    description: "暂停执行，等待人工输入或批准后从断点继续。",
                    contractVersion: 2,
                    interactionMode:
                      data.interactionMode === "approval" ? "approval" : "input",
                    prompt: String(data.prompt ?? "请补充内容。"),
                    outputVariable: String(data.outputVariable ?? "human_input"),
                    timeoutSeconds: 3600,
                  });
                  setMigrationNotice("人工介入已升级为 V2。");
                }}
                type="button"
              >
                升级为 V2
              </button>
            </div>
          ) : (
            <Field label="交互模式">
              <select
                className={textInputClass()}
                onChange={(event) =>
                  update({ interactionMode: event.target.value as "input" | "approval" })
                }
                value={data.interactionMode ?? "input"}
              >
                <option value="input">提交人工文本</option>
                <option value="approval">批准或拒绝</option>
              </select>
            </Field>
          )}
          <Field label="提示文案（支持 {{变量}}）">
            <WorkflowVariableField
              className="min-h-32 resize-none leading-6"
              contract={variableContract}
              edges={edges}
              fieldName="prompt"
              multiline
              node={node}
              nodes={nodes}
              onChange={(value) => update({ prompt: value })}
              value={data.prompt ?? ""}
            />
          </Field>
          <Field label="写入变量名">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
          {String(data.contractVersion ?? "1") === "2" ? (
            <Field label="等待时限（秒）">
              <input
                className={textInputClass()}
                max={86400}
                min={30}
                onChange={(event) => update({ timeoutSeconds: Number(event.target.value) })}
                type="number"
                value={data.timeoutSeconds ?? 3600}
              />
              <p className="mt-1 text-xs leading-5 text-slate-500">30 秒至 24 小时；过期后需重新打开审批请求。</p>
            </Field>
          ) : null}
          {migrationNotice ? <p className="text-xs text-emerald-200">{migrationNotice}</p> : null}
        </>
      ) : null}

      {data.kind === "question_classifier" ? (
        <QuestionClassifierConfig
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          models={models}
          node={node}
          nodes={nodes}
          onChange={update}
          onMigrate={() => onMigrateTypedAiNode(node.id)}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}

      {data.kind === "external_xpert" ||
      data.kind === "knowledge_base" ||
      data.kind === "toolset_resource" ||
      data.kind === "plugin_resource" ? (
        <ResourceNodeConfig data={data} update={update} />
      ) : null}

      {data.kind === "agent" ? (
        <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
          <p className="font-semibold">旧 Agent 节点已退役</p>
          <p className="mt-1 text-amber-100/80">
            新工作流请使用“智能体工作流”。只有确认旧版的未执行参数不会改变语义后，才能无损迁移。
          </p>
          {(() => {
            const blocked =
              String(data.temperature ?? "0.7") !== "0.7"
              || workflowBooleanValue(data.retryOnFailure)
              || Boolean(String(data.fallbackModelId ?? "").trim())
              || !["", "[]"].includes(String(data.nodeParametersJson ?? "[]").trim())
              || workflowBooleanValue(data.memoryReadEnabled)
              || workflowBooleanValue(data.memoryWriteEnabled)
              || String(data.outputSchemaMode ?? "default") !== "default";
            return (
              <>
                {blocked ? (
                  <p className="mt-2 text-amber-200">
                    迁移被阻止：请先清理非默认 Temperature、重试/备用模型、参数、记忆或自定义输出配置。
                  </p>
                ) : null}
                <button
                  className="mt-3 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={blocked}
                  onClick={() => update({
                    kind: "workflow_agent",
                    title: data.title === "Agent" ? "工作流智能体" : data.title,
                    description: "模型驱动的单步智能体执行节点。",
                    agentName: "workflow-agent",
                    rolePrompt: "你是负责执行当前工作流步骤的智能体，请直接输出结果。",
                    taskInput: data.instruction ?? "{{user_input}}",
                    toolMode: data.agentMode === "direct" ? "none" : "mcp_tools",
                    temperature: undefined,
                    retryOnFailure: undefined,
                    fallbackModelId: undefined,
                    nodeParametersJson: undefined,
                    instruction: undefined,
                    agentMode: undefined,
                  })}
                  type="button"
                >
                  迁移为智能体工作流
                </button>
              </>
            );
          })()}
        </div>
      ) : null}

      {data.kind === "agent" || data.kind === "workflow_agent" ? (
        <AgentStudioPanel
          boundMiddlewares={boundMiddlewares}
          boundResources={boundResources}
          data={data}
          declarations={declarations}
          edges={edges}
          node={node}
          nodes={nodes}
          onSelectNode={onSelectNode}
          registryTools={registryTools}
          registryToolsError={registryToolsError}
          update={update}
          variableContract={variableContract}
        />
      ) : null}

      {data.kind === "agent_task" ? (
        <>
          {String(data.contractVersion ?? "1") !== "2" ? (
            <ConfigSection
              description="旧版只输出任务 ID 字符串，也无法保证重复恢复时只创建一次任务。"
              title="升级协作合同"
            >
              <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
                <p>升级会同时检查并转换相连的旧版移交节点；无法确认类型安全时会明确阻止。</p>
                <button
                  className="mt-3 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950"
                  onClick={migrateCollaborationNode}
                  type="button"
                >
                  安全升级整条协作链
                </button>
                {migrationNotice ? (
                  <p aria-live="polite" className="mt-2 text-amber-100">{migrationNotice}</p>
                ) : null}
              </div>
            </ConfigSection>
          ) : null}
          <ConfigSection
            description="标题用于队列识别，任务内容会保存在受控任务 Store 中。"
            title="1. 定义任务"
          >
            <Field label="任务标题（支持 {{变量}}）">
              <WorkflowVariableField
                contract={variableContract}
                edges={edges}
                fieldName="taskTitle"
                node={node}
                nodes={nodes}
                onChange={(value) => update({ taskTitle: value })}
                value={data.taskTitle ?? ""}
              />
            </Field>
            <Field label="交给接收方的任务内容">
              <WorkflowVariableField
                className="min-h-32 resize-none leading-6"
                contract={variableContract}
                edges={edges}
                fieldName="taskInput"
                multiline
                node={node}
                nodes={nodes}
                onChange={(value) => update({ taskInput: value })}
                value={data.taskInput ?? ""}
              />
            </Field>
          </ConfigSection>
          <ConfigSection
            description="这里是任务的初始责任人标签；真正投递由后续移交节点完成。"
            title="2. 责任与凭证"
          >
            <Field label="初始负责人">
              <input
                className={textInputClass()}
                onChange={(event) => update({ assignedAgent: event.target.value })}
                placeholder="例如：review-agent"
                value={data.assignedAgent ?? ""}
              />
            </Field>
            <Field label="任务凭证变量">
              <WorkflowVariableField
                contract={variableContract}
                edges={edges}
                fieldName="outputVariable"
                node={node}
                nodes={nodes}
                onChange={(value) => update({ outputVariable: value })}
                value={data.outputVariable ?? "agent_task_receipt"}
              />
              <p className="mt-2 text-xs leading-5 text-slate-500">
                输出包含任务 ID、运行 ID、状态和负责人；下一个“移交已有任务”可直接选择它。
              </p>
            </Field>
          </ConfigSection>
        </>
      ) : null}

      {data.kind === "agent_handoff" ? (
        <>
          {String(data.contractVersion ?? "1") === "2" ? (
            <ConfigSection
              description="优先选择上游“创建协作任务”输出的 JSON 凭证。"
              title="1. 选择任务"
            >
              <Field label="任务凭证变量">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="taskVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ taskVariable: value })}
                  value={data.taskVariable ?? "agent_task_receipt"}
                />
              </Field>
              <Field label="变量内容类型">
                <select
                  className={textInputClass()}
                  onChange={(event) => update({
                    taskValueKind: event.target.value as WorkflowNodeData["taskValueKind"],
                  })}
                  value={data.taskValueKind ?? "receipt"}
                >
                  <option className="bg-slate-950" value="receipt">任务凭证（推荐）</option>
                  <option className="bg-slate-950" value="task_id">旧任务 ID 字符串</option>
                </select>
              </Field>
              <Field label="来源标记">
                <input
                  className={textInputClass()}
                  onChange={(event) => update({ sourceAgent: event.target.value })}
                  value={data.sourceAgent ?? "workflow"}
                />
              </Field>
            </ConfigSection>
          ) : null}
          <HandoffExecutionConfig
            data={data}
            edges={edges}
            node={node}
            nodes={nodes}
            publishedXperts={publishedXperts}
            publishedXpertsError={publishedXpertsError}
            onMigrate={migrateCollaborationNode}
            update={update}
            variableContract={variableContract}
          />
          {String(data.contractVersion ?? "1") !== "2" && migrationNotice ? (
            <p aria-live="polite" className="text-xs leading-5 text-amber-100">{migrationNotice}</p>
          ) : null}
          {String(data.contractVersion ?? "1") === "2" ? (
            <ConfigSection description="理由会交给接收方，但不会进入公开运行摘要。" title="3. 说明与输出">
              <Field label="移交说明（支持 {{变量}}）">
                <WorkflowVariableField
                  className="min-h-24 resize-none leading-6"
                  contract={variableContract}
                  edges={edges}
                  fieldName="reason"
                  multiline
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ reason: value })}
                  value={data.reason ?? ""}
                />
              </Field>
              <Field label="移交凭证变量">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="outputVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ outputVariable: value })}
                  value={data.outputVariable ?? "handoff_receipt"}
                />
              </Field>
            </ConfigSection>
          ) : null}
        </>
      ) : null}

      {data.kind === "handoff_router" ? (
        <>
          {String(data.contractVersion ?? "1") === "2" ? (
            <ConfigSection description="该节点会原子地创建任务并完成投递，不需要先放置任务节点。" title="1. 定义协作任务">
              <Field label="任务内容来源">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="sourceVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ sourceVariable: value })}
                  value={data.sourceVariable ?? ""}
                />
              </Field>
              <Field label="任务标题（支持 {{变量}}）">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="taskTitle"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ taskTitle: value })}
                  value={data.taskTitle ?? ""}
                />
              </Field>
              <Field label="来源标记">
                <input
                  className={textInputClass()}
                  onChange={(event) => update({ sourceAgent: event.target.value })}
                  value={data.sourceAgent ?? "workflow-agent"}
                />
              </Field>
            </ConfigSection>
          ) : null}
          <HandoffExecutionConfig
            data={data}
            edges={edges}
            node={node}
            nodes={nodes}
            publishedXperts={publishedXperts}
            publishedXpertsError={publishedXpertsError}
            onMigrate={migrateCollaborationNode}
            update={update}
            variableContract={variableContract}
          />
          {String(data.contractVersion ?? "1") !== "2" && migrationNotice ? (
            <p aria-live="polite" className="text-xs leading-5 text-amber-100">{migrationNotice}</p>
          ) : null}
          {String(data.contractVersion ?? "1") === "2" ? (
            <ConfigSection description="说明仅交给接收方；运行区只显示安全摘要和关系 ID。" title="3. 说明与输出">
              <Field label="移交说明（支持 {{变量}}）">
                <WorkflowVariableField
                  className="min-h-24 resize-none leading-6"
                  contract={variableContract}
                  edges={edges}
                  fieldName="reasonTemplate"
                  multiline
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ reasonTemplate: value })}
                  value={data.reasonTemplate ?? ""}
                />
              </Field>
              <Field label="协作凭证变量">
                <WorkflowVariableField
                  contract={variableContract}
                  edges={edges}
                  fieldName="outputVariable"
                  node={node}
                  nodes={nodes}
                  onChange={(value) => update({ outputVariable: value })}
                  value={data.outputVariable ?? "handoff_receipt"}
                />
              </Field>
            </ConfigSection>
          ) : null}
        </>
      ) : null}

      {data.kind === "mcp_tool" ? (
        <>
          <div className="rounded-lg border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs leading-5 text-emerald-50">
            工具按服务器、名称和 Schema 指纹固定；运行时重新解析当前会话，不保存短生命周期 sessionId。
          </div>
          {String(data.contractVersion ?? "1") !== "2" ? (
            <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
              <p>这是旧版 argumentsJson 配置。发布新版本前需要显式迁移。</p>
              <button
                className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950"
                onClick={() => {
                  const toolName = String(data.toolName ?? "").trim();
                  const matches = registryTools.filter((tool) => tool.name === toolName);
                  if (matches.length !== 1) {
                    setMigrationNotice(matches.length > 1
                      ? "迁移被阻止：存在同名工具，请先在 V2 中明确选择服务器。"
                      : "迁移被阻止：当前 Registry 无法唯一解析旧工具。");
                    return;
                  }
                  const raw = String(data.argumentsJson ?? "{}");
                  if (raw.includes("{{")) {
                    setMigrationNotice("迁移被阻止：旧参数包含混合模板，无法无损转成类型化绑定。");
                    return;
                  }
                  let parsed: Record<string, WorkflowValue>;
                  try {
                    const value = JSON.parse(raw) as unknown;
                    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
                    parsed = value as Record<string, WorkflowValue>;
                  } catch {
                    setMigrationNotice("迁移被阻止：旧参数不是合法 JSON 对象。");
                    return;
                  }
                  const tool = matches[0];
                  const properties = tool.input_schema.properties;
                  if (tool.input_schema.type !== "object" || !properties || typeof properties !== "object" || Array.isArray(properties)) {
                    setMigrationNotice("迁移被阻止：该工具需要对象变量模式，旧 JSON 无法自动映射。");
                    return;
                  }
                  const propertyNames = new Set(Object.keys(properties));
                  const required = new Set(Array.isArray(tool.input_schema.required) ? tool.input_schema.required.filter((item): item is string => typeof item === "string") : []);
                  if (Object.keys(parsed).some((name) => !propertyNames.has(name)) || [...required].some((name) => !(name in parsed))) {
                    setMigrationNotice("迁移被阻止：旧参数与当前工具 Schema 不一致。");
                    return;
                  }
                  if (!window.confirm("迁移后工具将固定到当前服务器和 Schema；旧 argumentsJson 会转换为类型化字面量绑定。是否继续？")) return;
                  onReplaceNodeData(node.id, {
                    kind: "mcp_tool",
                    title: data.title,
                    description: "按服务器、工具和 Schema 指纹调用已注册的 MCP 工具。",
                    contractVersion: 2,
                    serverId: tool.server_id,
                    toolName: tool.name,
                    inputSchemaChecksum: tool.schema_checksum,
                    argumentMode: "fields",
                    argumentBindings: Object.entries(parsed).map(([name, value], index) => ({
                      id: `argument_${index + 1}`,
                      name,
                      binding: { source: "literal", value },
                    })),
                    argumentsVariable: "mcp_arguments",
                    outputVariable: String(data.outputVariable ?? "mcp_output"),
                  });
                  setMigrationNotice("MCP 工具已升级为 V2 固定绑定。");
                }}
                type="button"
              >
                尝试无损迁移
              </button>
            </div>
          ) : null}
          <Field label="MCP 工具">
            <select
              className={textInputClass()}
              disabled={String(data.contractVersion ?? "1") !== "2"}
              onChange={(event) => {
                if (!event.target.value) {
                  update({ serverId: "", toolName: "", inputSchemaChecksum: "", argumentBindings: [] });
                  return;
                }
                const [serverId, toolName] = JSON.parse(event.target.value) as [string, string];
                const tool = registryTools.find((item) => item.server_id === serverId && item.name === toolName);
                if (!tool) return;
                const reconciled = reconcileMcpArgumentBindings(tool.input_schema);
                update({
                  serverId,
                  toolName,
                  inputSchemaChecksum: tool.schema_checksum,
                  ...reconciled,
                });
              }}
              value={data.serverId && data.toolName ? JSON.stringify([data.serverId, data.toolName]) : ""}
            >
              <option className="bg-slate-950" value="">
                {registryTools.length ? "请选择工具" : "暂无已注册工具"}
              </option>
              {registryTools.map((tool) => (
                <option className="bg-slate-950" key={`${tool.server_id}:${tool.name}`} value={JSON.stringify([tool.server_id, tool.name])}>
                  {tool.name} · {tool.server_id}
                </option>
              ))}
            </select>
            {registryToolsError ? (
              <p className="mt-2 text-xs text-rose-200">{registryToolsError}</p>
            ) : null}
          </Field>
          {String(data.contractVersion ?? "1") === "2" && data.toolName ? (
            <>
              {mcpSchemaDrift && selectedRegistryTool ? (
                <div className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
                  <p className="font-semibold">工具 Schema 已变化，当前配置不能发布或运行。</p>
                  <p className="mt-1 break-all text-amber-100/80">
                    已保存 {String(data.inputSchemaChecksum ?? "").slice(0, 12)}… · 当前 {selectedRegistryTool.schema_checksum.slice(0, 12)}…
                  </p>
                  <button
                    className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950"
                    onClick={() => {
                      const reconciled = reconcileMcpArgumentBindings(
                        selectedRegistryTool.input_schema,
                        data.argumentMode === "fields" ? data.argumentBindings ?? [] : [],
                      );
                      update({
                        inputSchemaChecksum: selectedRegistryTool.schema_checksum,
                        ...reconciled,
                      });
                      setMigrationNotice("已重新确认当前 Schema；同名字段保留原绑定，请检查新增字段后再发布。");
                    }}
                    type="button"
                  >
                    重新确认当前 Schema
                  </button>
                </div>
              ) : null}
              <p className="break-all rounded-md bg-white/[0.04] px-3 py-2 font-mono text-[10px] leading-4 text-slate-400">
                Schema {String(data.inputSchemaChecksum ?? "").slice(0, 16)}…
              </p>
              <Field label="参数方式">
                <select
                  className={textInputClass()}
                  onChange={(event) => update({ argumentMode: event.target.value as "fields" | "object_variable" })}
                  value={data.argumentMode ?? "fields"}
                >
                  <option value="fields">按字段绑定</option>
                  <option value="object_variable">绑定完整 JSON 对象变量</option>
                </select>
              </Field>
              {data.argumentMode === "object_variable" ? (
                <Field label="参数对象变量">
                  <WorkflowVariableField
                    contract={variableContract}
                    edges={edges}
                    fieldName="argumentsVariable"
                    node={node}
                    nodes={nodes}
                    onChange={(value) => update({ argumentsVariable: value })}
                    value={data.argumentsVariable ?? ""}
                  />
                </Field>
              ) : (
                <div className="space-y-3">
                  {(data.argumentBindings ?? []).map((item, index) => (
                    <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3" key={item.id}>
                      <p className="text-xs font-semibold text-slate-200">{item.name}</p>
                      <select
                        className={`${textInputClass()} mt-2`}
                        onChange={(event) => {
                          const next = [...(data.argumentBindings ?? [])];
                          next[index] = {
                            ...item,
                            binding: event.target.value === "variable"
                              ? { source: "variable", variable: "" }
                              : { source: "literal", value: "" },
                          };
                          update({ argumentBindings: next });
                        }}
                        value={item.binding.source}
                      >
                        <option value="literal">固定值</option>
                        <option value="variable">工作流变量</option>
                      </select>
                      {item.binding.source === "variable" ? (
                        <div className="mt-2">
                          <WorkflowVariableField
                            contract={variableContract}
                            descriptor={{
                              nodeKind: "mcp_tool",
                              field: `argumentBindings.${index}.binding.variable`,
                              mode: "binding",
                              fallbackTypes: workflowTypesForMcpSchema(
                                (registryTools.find(
                                  (tool) => tool.server_id === data.serverId && tool.name === data.toolName,
                                )?.input_schema.properties as Record<string, unknown> | undefined)?.[item.name],
                              ),
                            }}
                            edges={edges}
                            fieldName="argumentsVariable"
                            node={node}
                            nodes={nodes}
                            onChange={(value) => {
                              const next = [...(data.argumentBindings ?? [])];
                              next[index] = { ...item, binding: { source: "variable", variable: value } };
                              update({ argumentBindings: next });
                            }}
                            value={item.binding.variable ?? ""}
                          />
                        </div>
                      ) : (
                        <div className="mt-2">
                          <JsonLiteralEditor
                            ariaLabel={`${item.name || `参数 ${index + 1}`}固定 JSON 值`}
                            onChange={(value) => {
                              const next = [...(data.argumentBindings ?? [])];
                              next[index] = { ...item, binding: { source: "literal", value } };
                              update({ argumentBindings: next });
                            }}
                            value={item.binding.value ?? ""}
                          />
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : String(data.contractVersion ?? "1") !== "2" ? (
            <Field label="旧参数 JSON（只读迁移源）">
              <textarea className={`${textInputClass()} min-h-28 font-mono text-xs`} readOnly value={data.argumentsJson ?? "{}"} />
            </Field>
          ) : null}
          <Field label="输出变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
          {migrationNotice ? <p className="text-xs text-amber-100">{migrationNotice}</p> : null}
        </>
      ) : null}

      {data.kind === "time_tool" && String(data.contractVersion ?? "1") !== "2" ? (
        <>
          <Field label="时间操作">
            <select
              className={textInputClass()}
              onChange={(event) => update({ operation: event.target.value })}
              value={data.operation ?? "now_iso"}
            >
              <option className="bg-slate-950" value="now_iso">
                当前时间 ISO
              </option>
              <option className="bg-slate-950" value="now_epoch">
                当前时间戳
              </option>
              <option className="bg-slate-950" value="format">
                按格式输出
              </option>
            </select>
          </Field>
          <Field label="格式字符串">
            <input
              className={textInputClass()}
              disabled={data.operation !== "format"}
              onChange={(event) => update({ formatString: event.target.value })}
              placeholder="%Y-%m-%d %H:%M:%S"
              value={data.formatString ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
        </>
      ) : null}

      {data.kind === "http_request" ? (
        <WorkflowHttpRequestNodeConfig
          contract={variableContract}
          data={data}
          declarations={declarations}
          edges={edges}
          node={node}
          nodes={nodes}
          onChange={update}
          onOpenVariableCenter={onOpenVariableCenter}
        />
      ) : null}

      {data.kind === "iteration" && !isIterationV2(data) ? (
        <>
          <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-50">
            <p>这是旧版迭代配置，可继续打开和手动运行；发布新版本前必须显式升级。</p>
            <p className="mt-1 text-amber-100/80">{legacyIterationMigration?.message}</p>
            {legacyIterationMigration?.ok && legacyIterationMigration.data ? (
              <button
                className="mt-2 rounded-md bg-amber-200 px-3 py-1.5 font-semibold text-ink-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
                onClick={() => {
                  onReplaceNodeData(node.id, legacyIterationMigration.data!);
                  setMigrationNotice(legacyIterationMigration.message);
                }}
                type="button"
              >
                升级为批量处理 V2
              </button>
            ) : null}
          </div>
          <Field label="输入列表变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="inputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ inputVariable: value })}
              value={data.inputVariable ?? ""}
            />
          </Field>
          <Field label="单项变量名">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="iterationVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ iterationVariable: value })}
              value={data.iterationVariable ?? ""}
            />
          </Field>
          <Field label="单项模板（支持 {{单项变量}}）">
            <WorkflowVariableField
              className="min-h-28 resize-none leading-6"
              contract={variableContract}
              edges={edges}
              fieldName="itemTemplate"
              multiline
              node={node}
              nodes={nodes}
              onChange={(value) => update({ itemTemplate: value })}
              value={data.itemTemplate ?? ""}
            />
          </Field>
          <Field label="输出变量">
            <WorkflowVariableField
              contract={variableContract}
              edges={edges}
              fieldName="outputVariable"
              node={node}
              nodes={nodes}
              onChange={(value) => update({ outputVariable: value })}
              value={data.outputVariable ?? ""}
            />
          </Field>
          {migrationNotice ? <p aria-live="polite" className="text-xs text-emerald-200">{migrationNotice}</p> : null}
        </>
      ) : null}

      {data.kind === "runtime_middleware" ? (
        <div className="space-y-4">
          {skillCreatorMiddleware ? (
            <SkillCreatorMiddlewareModePanel
              legacy={legacySkillCreatorMiddleware}
              onUpgrade={() =>
                update({
                  runtimeMiddlewareConfig: creatorHandoffMiddlewareConfig(),
                })
              }
            />
          ) : pluginHookMiddleware ? (
            <div className={`rounded-lg border px-3 py-3 text-xs leading-5 ${legacyPluginHookMiddleware ? "border-amber-300/25 bg-amber-300/10 text-amber-50" : "border-emerald-300/20 bg-emerald-300/[0.07] text-emerald-50"}`}>
              <p className="flex items-center gap-2 font-semibold">
                <ShieldCheck aria-hidden="true" size={15} />
                {legacyPluginHookMiddleware ? "Legacy argv Hook" : "Typed Hook V2"}
              </p>
              <p className="mt-1">
                {legacyPluginHookMiddleware
                  ? "旧节点仍按可编辑 argv 配置运行。升级后，事件、模式、工具范围与故障策略全部来自已安装 Skill 的不可变 manifest。"
                  : "Hook 只能返回 annotation、validation 或 deny；不能改写工具参数、输出、审批或权限。"}
              </p>
              {legacyPluginHookMiddleware ? (
                <div className="mt-3">
                  <button
                    className="min-h-10 rounded-full border border-amber-200/30 px-4 text-xs font-semibold text-amber-50 disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={!hookUpgradeReady}
                    onClick={() => update({
                      runtimeMiddlewareConfig: {
                        hook_mode: "typed_v2",
                        skill_ids: selectedRuntimeSkillIds.join(", "),
                      },
                    })}
                    type="button"
                  >
                    升级当前节点为 Hook V2
                  </button>
                  {!hookUpgradeReady ? (
                    <p className="mt-2 text-amber-100/75">
                      只有当前选中的每个 Skill 都具备可运行 V2 manifest 时才能升级。
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="rounded-lg border border-indigo-300/25 bg-indigo-300/10 px-3 py-2 text-xs leading-5 text-indigo-50">
              使用紫色端口绑定到一个 workflow_agent，或使用普通端口作为线性中间件。两种连接方式不可混用。
            </div>
          )}
          <Field label="执行优先级（0-1000）">
            <input
              className={textInputClass()}
              max={1000}
              min={0}
              onChange={(event) =>
                update({ middlewarePriority: event.target.value })
              }
              type="number"
              value={data.middlewarePriority ?? "100"}
            />
          </Field>
          {!skillCreatorMiddleware ? (
            <div className="rounded-lg border border-indigo-300/25 bg-indigo-300/10 px-3 py-2 text-xs leading-5 text-indigo-50">
              绑定模式仅作用于目标 workflow_agent；线性模式会影响其后执行的智能体。核心中间件已接入真实 MiddlewarePipeline。
            </div>
          ) : null}
          <div className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2">
            <p className="text-xs font-semibold text-slate-200">
              {data.runtimeMiddlewareKind ?? "runtime_middleware.unknown"}
            </p>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              ID：{data.runtimeMiddlewareId ?? "unknown"}
            </p>
          </div>
          {contentPolicyMiddleware ? (
            <WorkflowContentPolicyConfig
              config={runtimeMiddlewareConfig}
              onChange={updateRuntimeMiddlewareConfig}
            />
          ) : visibleRuntimeMiddlewareFields.length === 0 ? (
            <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-3 py-4 text-sm leading-6 text-slate-400">
              {skillCreatorMiddleware
                ? "Creator V2 不需要额外权限配置。运行成功后由你前往 Creator 继续规划。"
                : "此中间件暂无可配置字段。"}
            </p>
          ) : (
            <>
              <p className="text-xs font-semibold text-slate-300">
                {skillRuntimeMiddleware
                  ? "必须实际应用的 Skill"
                  : pluginHookMiddleware
                    ? legacyPluginHookMiddleware ? "Legacy Hook 配置" : "已安装 Hook Skill"
                    : "中间件配置"}
              </p>
              {visibleRuntimeMiddlewareFields.map((field) => (
                <Field
                  key={field.name}
                  label={`${field.label}${field.required ? " *" : ""}`}
                >
                  {field.type === "text" && field.name === "clientHostId" ? (
                    <select
                      className={textInputClass()}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(field.name, event.target.value)
                      }
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    >
                      <option className="bg-slate-950" value="">
                        {data.runtimeMiddlewareId === "office_automation"
                          ? "选择已配对 Office 宿主"
                          : "选择已配对 Chrome 宿主"}
                      </option>
                      {clientHosts
                        .filter(
                          (host) =>
                            !host.revoked &&
                            (data.runtimeMiddlewareId === "office_automation"
                              ? host.host_type === "office"
                              : (host.host_type ?? "chrome") === "chrome"),
                        )
                        .map((host) => (
                        <option className="bg-slate-950" key={host.host_id} value={host.host_id}>
                          {host.name} · {host.status}
                          {host.host_type === "office"
                            ? host.document_binding?.bound
                              ? ` · ${host.office_app ?? "office"} 已绑定`
                              : " · 文档未绑定"
                            : host.bound_tab?.bound
                              ? " · 已绑定标签页"
                              : " · 未绑定标签页"}
                        </option>
                      ))}
                    </select>
                  ) : null}

                  {field.type === "text" && field.name !== "clientHostId" ? (
                    <input
                      className={textInputClass()}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      placeholder={field.placeholder}
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.type === "textarea" && field.name === "skill_ids" ? (
                    <div className="space-y-2">
                      <TrustedSkillSelect
                        ariaLabel={pluginHookMiddleware ? "添加已安装 Hook Skill" : "添加已安装 Skill"}
                        onChange={appendTrustedSkill}
                        placeholder={pluginHookMiddleware ? "选择一个可运行 Hook Skill" : "选择一个可激活 Skill 添加"}
                        skills={selectableInstalledSkills}
                        value=""
                      />
                      {selectedRuntimeSkillIds.length > 0 ? (
                        <div className="flex flex-wrap gap-2 rounded-lg border border-white/10 bg-white/[0.035] p-2.5">
                          {selectedRuntimeSkillIds.map((skillId) => {
                            const installed = selectableInstalledSkills.find(
                              (skill) => skill.skill_id === skillId,
                            );
                            return (
                              <span
                                className="inline-flex max-w-full items-center gap-2 rounded-full border border-cyan-300/25 bg-cyan-300/10 py-1 pl-2.5 pr-1.5 text-xs text-cyan-50"
                                key={skillId}
                              >
                                <span className="min-w-0 truncate">
                                  {installed?.name || skillId}
                                </span>
                                <button
                                  aria-label={`${pluginHookMiddleware ? "移除 Hook Skill" : "移除必用 Skill"}：${installed?.name || skillId}`}
                                  className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-cyan-100 transition hover:bg-cyan-200/15 focus-visible:outline-none"
                                  onClick={() =>
                                    updateRuntimeMiddlewareConfig(
                                      field.name,
                                      updateSkillRuntimeIds(
                                        runtimeMiddlewareConfig?.skill_ids,
                                        skillId,
                                        "remove",
                                      ),
                                    )
                                  }
                                  type="button"
                                >
                                  <X aria-hidden="true" size={13} />
                                </button>
                              </span>
                            );
                          })}
                        </div>
                      ) : (
                        <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.025] px-3 py-3 text-xs leading-5 text-slate-400">
                          {pluginHookMiddleware
                            ? "尚未选择 Hook Skill。这里只显示已安装、已确认且 manifest 可运行的项目。"
                            : "尚未选择必用 Skill。选择后，Agent 必须先调用 skill_read 才能提交答案或执行副作用工具。"}
                        </p>
                      )}
                      {pluginHookMiddleware && !legacyPluginHookMiddleware && selectedHookSkills.length ? (
                        <div className="space-y-3 border-l-2 border-emerald-300/25 pl-3">
                          {selectedHookSkills.map((skill) => (
                            <div key={skill.skill_id}>
                              <p className="text-xs font-semibold text-slate-200">{skill.name}</p>
                              {(skill.hook_capability?.hooks ?? []).map((hook) => (
                                <p className="mt-1 text-[11px] leading-5 text-slate-400" key={hook.hookId}>
                                  <span className="font-mono text-emerald-100">{hook.hookId}</span>
                                  {` · ${hook.event} · ${hook.mode}`}
                                  {hook.toolNames.length ? ` · ${hook.toolNames.join("、")}` : ""}
                                  {` · ${hook.mode === "annotation" ? "技术故障告警后继续" : "技术故障失败关闭"}`}
                                </p>
                              ))}
                            </div>
                          ))}
                        </div>
                      ) : null}
                      <p className="text-[11px] leading-5 text-slate-500">
                        {pluginHookMiddleware
                          ? legacyPluginHookMiddleware
                            ? "Legacy 节点保留旧行为；fail_closed 仍由画布配置。"
                            : "Hook 脚本由中间件在离线 Sandbox 内执行，但 sandbox_shell 不会暴露给模型。"
                          : "脚本不会自动运行。如 Skill 需要执行脚本，还要单独绑定 Sandbox Shell 和命令白名单。"}
                      </p>
                      {!pluginHookMiddleware ? <button
                        aria-expanded={showSkillAdvancedOptions}
                        className="text-left text-xs font-semibold text-slate-300 underline decoration-white/20 underline-offset-4 transition hover:text-white"
                        onClick={() => setShowSkillAdvancedOptions((current) => !current)}
                        type="button"
                      >
                        {showSkillAdvancedOptions ? "收起高级选项" : "展开高级选项"}
                      </button> : null}
                      {!pluginHookMiddleware && !showSkillAdvancedOptions ? (
                        <p className="text-[11px] leading-5 text-slate-500">
                          高级选项包含自动发现、目录检索和经审批安装。候选不会自动变成必用 Skill。
                        </p>
                      ) : null}
                    </div>
                  ) : null}

                  {field.type === "textarea" && field.name !== "skill_ids" ? (
                    <textarea
                      className={`${textInputClass()} min-h-24 resize-none leading-6`}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      placeholder={field.placeholder}
                      rows={field.rows ?? 3}
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.type === "boolean" ? (
                    <>
                      <label
                        className={`flex items-start gap-3 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm text-slate-200 ${
                          field.name === "catalog_search" &&
                          skillCatalogApprovalState?.enabled
                            ? "opacity-65"
                            : ""
                        }`}
                      >
                        <input
                          checked={runtimeMiddlewareBooleanValue(
                            runtimeMiddlewareConfig,
                            field,
                          )}
                          className="mt-1 h-4 w-4 shrink-0 rounded border-white/20 bg-slate-950 text-brand-300"
                          disabled={
                            field.name === "catalog_search" &&
                            skillCatalogApprovalState?.enabled
                          }
                          onChange={(event) =>
                            updateRuntimeMiddlewareConfig(
                              field.name,
                              event.target.checked,
                            )
                          }
                          type="checkbox"
                        />
                        <span className="leading-6">
                          {field.description ?? field.label}
                          {field.name === "catalog_search" &&
                          skillCatalogApprovalState?.enabled ? (
                            <span className="mt-1 block text-xs text-indigo-200">
                              目录安装已开启，检索会保持启用。
                            </span>
                          ) : null}
                        </span>
                      </label>
                      {field.name === "catalog_install" ? (
                        <div
                          aria-live="polite"
                          className={`mt-2 rounded-lg border px-3 py-2 text-xs leading-5 ${
                            !skillCatalogApprovalState?.enabled
                              ? "border-white/10 bg-white/[0.035] text-slate-400"
                              : skillCatalogApprovalState.covered
                                ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                          }`}
                        >
                          {!skillCatalogApprovalState?.enabled
                            ? "开启后会同时启用目录检索，并自动添加或更新人机审批中间件。每次安装仍需你明确批准。"
                            : skillCatalogApprovalState.covered
                              ? "审批保护已就绪：skill_install 执行前会暂停，等待你批准。Skill 会全局安装，但只授权当前运行使用。"
                              : skillCatalogApprovalState.approvalNodeId
                                ? "已添加并配置人机审批。请将本节点与“人机审批”都通过紫色端口绑定到同一个 workflow_agent。"
                                : "暂未能自动添加人机审批，请等待中间件注册表加载后重试。目录安装在审批保护就绪前不会通过校验。"}
                        </div>
                      ) : null}
                    </>
                  ) : null}

                  {field.type === "number" ? (
                    <input
                      className={textInputClass()}
                      max={field.maxValue ?? field.max_value}
                      min={field.minValue ?? field.min_value}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value === ""
                            ? ""
                            : Number(event.target.value),
                        )
                      }
                      placeholder={field.placeholder}
                      type="number"
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.type === "select" ? (
                    <select
                      className={textInputClass()}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    >
                      <option className="bg-slate-950" value="">
                        请选择
                      </option>
                      {(field.options ?? []).map((option) => (
                        <option
                          className="bg-slate-950"
                          key={option}
                          value={option}
                        >
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : null}

                  {field.type === "json" ? (
                    <textarea
                      className={`${textInputClass()} min-h-28 resize-none font-mono text-xs leading-5`}
                      onChange={(event) =>
                        updateRuntimeMiddlewareConfig(
                          field.name,
                          event.target.value,
                        )
                      }
                      placeholder={field.placeholder ?? '{"key":"value"}'}
                      rows={field.rows ?? 4}
                      value={runtimeMiddlewareStringValue(
                        runtimeMiddlewareConfig,
                        field,
                      )}
                    />
                  ) : null}

                  {field.description && field.type !== "boolean" ? (
                    <p className="mt-2 text-xs leading-5 text-slate-500">
                      {field.description}
                    </p>
                  ) : null}
                </Field>
              ))}
            </>
          )}
        </div>
      ) : null}

      {data.kind === "output" ? (
        <Field label="最终输出变量">
          <WorkflowVariableField
            contract={variableContract}
            edges={edges}
            fieldName="outputVariable"
            node={node}
            nodes={nodes}
            onChange={(value) => update({ outputVariable: value })}
            value={data.outputVariable ?? ""}
          />
        </Field>
      ) : null}
    </div>
  );
}

interface WorkflowCanvasProps {
  workflowId: string;
  initialDefinition?: WorkflowDefinition;
  onSave?: (definition: WorkflowDefinition) => Promise<void> | void;
  saveLabel?: string;
}

type WorkflowWorkspaceTab = "config" | "run";

interface WorkflowFileInputFocusRequest {
  requestId: number;
  variableName: string;
}

export function workflowProjectPending(
  workflowId: string,
  hasSaveHandler: boolean,
  projectRevision: number | null,
): boolean {
  return !hasSaveHandler && workflowId.startsWith("wf_") && projectRevision === null;
}

function WorkflowCanvas({
  workflowId,
  initialDefinition: controlledDefinition,
  onSave,
  saveLabel = "保存草稿",
}: WorkflowCanvasProps) {
  const localDraftCandidate = useMemo(() => {
    if (controlledDefinition || onSave || workflowId !== "draft") return null;
    const storedDefinition = readStoredWorkflow(workflowId);
    if (!storedDefinition || isLegacyStarterWorkflow(storedDefinition)) {
      return null;
    }
    return cloneDefinition(storedDefinition);
  }, [controlledDefinition, onSave, workflowId]);
  const loadedDefinition = useMemo(
    () => loadDefinition(workflowId, controlledDefinition),
    [controlledDefinition, workflowId],
  );
  const [title, setTitle] = useState(loadedDefinition.title);
  const [nodes, setNodes, onNodesChange] = useNodesState<WorkflowNode>(
    loadedDefinition.nodes,
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<WorkflowEdge>(
    loadedDefinition.edges,
  );
  const [variables, setVariables] = useState<WorkflowVariableDeclaration[]>(
    loadedDefinition.variables ?? [],
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState("");
  const [errorNotice, setErrorNotice] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isConverting, setIsConverting] = useState(false);
  const [xpertRegistry, setXpertRegistry] =
    useState<WorkflowNodeRegistryResponse | null>(null);
  const [xpertRegistryError, setXpertRegistryError] = useState("");
  const [conversionReview, setConversionReview] =
    useState<XpertConversionAnalysis | null>(null);
  const [conversionInputVariable, setConversionInputVariable] = useState("");
  const [projectRevision, setProjectRevision] = useState<number | null>(null);
  const [activeDeployment, setActiveDeployment] =
    useState<WorkflowDeploymentSummary | null>(null);
  const [formPublication, setFormPublication] =
    useState<WorkflowFormPublicationSummary | null>(null);
  const [rssSubscription, setRssSubscription] =
    useState<WorkflowRssSubscriptionSummary | null>(null);
  const [emailSubscription, setEmailSubscription] =
    useState<WorkflowEmailSubscriptionSummary | null>(null);
  const [deploymentExecutions, setDeploymentExecutions] = useState<
    WorkflowExecutionSummary[]
  >([]);
  const [isPublishing, setIsPublishing] = useState(false);
  const [oneTimeWebhookKey, setOneTimeWebhookKey] = useState("");
  const [oneTimeFormShareUrl, setOneTimeFormShareUrl] = useState("");
  const [isNodePaletteOpen, setIsNodePaletteOpen] = useState(false);
  const [isVariableCenterOpen, setIsVariableCenterOpen] = useState(false);
  const variableCenterTriggerRef = useRef<HTMLButtonElement>(null);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    type: "node" | "pane";
    nodeId?: string;
  } | null>(null);
  const [clipboard, setClipboard] = useState<{
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
  } | null>(null);
  const [quickAddMenu, setQuickAddMenu] = useState<{
    x: number;
    y: number;
    sourceNodeId: string;
    sourceHandle?: string;
  } | null>(null);
  type WorkflowHistorySnapshot = {
    nodes: WorkflowNode[];
    edges: WorkflowEdge[];
    variables: WorkflowVariableDeclaration[];
  };
  const historyPastRef = useRef<WorkflowHistorySnapshot[]>([]);
  const historyFutureRef = useRef<WorkflowHistorySnapshot[]>([]);
  const [workspaceTab, setWorkspaceTab] =
    useState<WorkflowWorkspaceTab>("config");
  const [pendingLocalDraft, setPendingLocalDraft] =
    useState<WorkflowDefinition | null>(localDraftCandidate);
  const [fileInputFocusRequest, setFileInputFocusRequest] =
    useState<WorkflowFileInputFocusRequest | null>(null);
  const [runtimeMiddlewareRegistry, setRuntimeMiddlewareRegistry] = useState<
    RuntimeMiddlewareNode[]
  >([]);
  const projectPending = workflowProjectPending(
    workflowId,
    Boolean(onSave),
    projectRevision,
  );
  const hasFormEntry = nodes.some((node) => node.data.kind === "form_event_entry");
  const hasRssEntry = nodes.some((node) => node.data.kind === "rss_event_entry");
  const hasEmailEntry = nodes.some((node) => node.data.kind === "email_event_entry");
  const { screenToFlowPosition, fitView } = useReactFlow();
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    fetchWorkflowNodeRegistry()
      .then((registry) => {
        if (cancelled) return;
        setXpertRegistry(registry);
        setXpertRegistryError("");
      })
      .catch(() => {
        if (cancelled) return;
        setXpertRegistry(null);
        setXpertRegistryError(
          "节点 Registry 暂不可用，已暂停智能体转换与入口修复。",
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (onSave || !workflowId.startsWith("wf_")) return;
    let cancelled = false;
    fetchWorkflowProject(workflowId)
      .then((project) => {
        if (cancelled) return;
        const draft = cloneDefinition(project.draft);
        setTitle(draft.title);
        setNodes(draft.nodes);
        setEdges(draft.edges);
        setVariables(draft.variables ?? []);
        setProjectRevision(project.draft_revision);
        setActiveDeployment(project.active_deployment ?? null);
        setFormPublication(project.form_publication ?? null);
        setRssSubscription(project.rss_subscription ?? null);
        setEmailSubscription(project.email_subscription ?? null);
      })
      .catch((error) => {
        if (!cancelled) {
          setErrorNotice(
            error instanceof Error ? error.message : "服务端工作流加载失败。",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [onSave, setEdges, setNodes, workflowId]);

  useEffect(() => {
    if (onSave || workspaceTab !== "run" || !workflowId.startsWith("wf_")) {
      return;
    }
    let cancelled = false;
    const refresh = () => {
      void fetchWorkflowExecutions(workflowId)
        .then((response) => {
          if (!cancelled) setDeploymentExecutions(response.items);
        })
        .catch(() => {
          if (!cancelled) setDeploymentExecutions([]);
        });
    };
    refresh();
    const intervalId = window.setInterval(refresh, 2_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [onSave, workflowId, workspaceTab]);

  useEffect(() => {
    if (onSave || (!hasRssEntry && !hasEmailEntry) || !workflowId.startsWith("wf_")) return;
    let cancelled = false;
    const refresh = () => {
      void fetchWorkflowProject(workflowId)
        .then((project) => {
          if (cancelled) return;
          setActiveDeployment(project.active_deployment ?? null);
          setRssSubscription(project.rss_subscription ?? null);
          setEmailSubscription(project.email_subscription ?? null);
        })
        .catch(() => undefined);
    };
    const intervalId = window.setInterval(refresh, 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [hasEmailEntry, hasRssEntry, onSave, workflowId]);

  const openRunFileInput = useCallback((variableName: string) => {
    if (!variableName) return;
    setFileInputFocusRequest((current) => ({
      requestId: (current?.requestId ?? 0) + 1,
      variableName,
    }));
    setWorkspaceTab("run");
  }, []);

  useEffect(() => {
    let isMounted = true;
    fetchRuntimeMiddlewareNodes()
      .then((registryNodes) => {
        if (!isMounted) return;
        setRuntimeMiddlewareRegistry(registryNodes);
        setNodes((currentNodes) =>
          reconcileRuntimeMiddlewareNodes(currentNodes, registryNodes),
        );
      })
      .catch((error) => {
        console.error("Failed to refresh existing middleware nodes:", error);
      });
    return () => {
      isMounted = false;
    };
  }, [setNodes]);

  const humanApprovalDefinition = useMemo(
    () =>
      runtimeMiddlewareRegistry.find(
        (definition) =>
          definition.id === "human_in_the_loop" && definition.enabled,
      ),
    [runtimeMiddlewareRegistry],
  );

  useEffect(() => {
    if (!humanApprovalDefinition) return;
    const reconciled = reconcileSkillCatalogApprovals(
      nodes,
      edges,
      humanApprovalDefinition,
    );
    if (reconciled.nodes !== nodes) setNodes(reconciled.nodes);
    if (reconciled.edges !== edges) setEdges(reconciled.edges);
  }, [edges, humanApprovalDefinition, nodes, setEdges, setNodes]);

  const definition = useMemo<WorkflowDefinition>(
    () => ({
      id: workflowId,
      title,
      nodes,
      edges,
      variables,
      updatedAt: new Date().toISOString(),
    }),
    [edges, nodes, title, variables, workflowId],
  );

  const startBlankWorkflow = useCallback(() => {
    const blankDefinition = initialDefinition(workflowId);
    setTitle(blankDefinition.title);
    setNodes(blankDefinition.nodes);
    setEdges(blankDefinition.edges);
    setVariables(blankDefinition.variables ?? []);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setWorkspaceTab("config");
    historyPastRef.current = [];
    historyFutureRef.current = [];
    setPendingLocalDraft(null);
    setErrorNotice("");
    setSaveNotice("已打开默认工作流；原本地草稿仍保留至保存或转换");
    window.setTimeout(() => setSaveNotice(""), 3200);
  }, [setEdges, setNodes, workflowId]);
  const xpertEntryRepair = useMemo(() => {
    if (
      !onSave ||
      !xpertRegistry ||
      nodes.some((node) => node.data.kind === "input") ||
      !nodes.some((node) => node.data.kind === "workflow_call_entry")
    ) {
      return null;
    }
    return analyzeXpertWorkflowConversion(
      definition,
      xpertRegistry,
      conversionInputVariable || undefined,
    );
  }, [conversionInputVariable, definition, nodes, onSave, xpertRegistry]);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );
  const workflowVariables = useMemo(
    () => analyzeWorkflowVariables(nodes, edges, selectedNodeId, variables),
    [edges, nodes, selectedNodeId, variables],
  );
  // 结构级变更历史（添加/删除/连线/粘贴等），用于撤销重做。
  // 位置拖动不入栈——避免每次拖动都产生快照。
  const commitHistory = useCallback(() => {
    historyPastRef.current.push({ nodes, edges, variables });
    if (historyPastRef.current.length > 60) historyPastRef.current.shift();
    historyFutureRef.current = [];
  }, [edges, nodes, variables]);

  const undo = useCallback(() => {
    const prev = historyPastRef.current.pop();
    if (!prev) return;
    historyFutureRef.current.push({ nodes, edges, variables });
    setNodes(prev.nodes);
    setEdges(prev.edges);
    setVariables(prev.variables);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }, [edges, nodes, setEdges, setNodes, variables]);

  const redo = useCallback(() => {
    const next = historyFutureRef.current.pop();
    if (!next) return;
    historyPastRef.current.push({ nodes, edges, variables });
    setNodes(next.nodes);
    setEdges(next.edges);
    setVariables(next.variables);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  }, [edges, nodes, setEdges, setNodes, variables]);

  const renderedEdges = useMemo(
    () =>
      edges.map((edge) =>
        edge.id === selectedEdgeId
          ? {
              ...edge,
              className: `${edge.className ?? ""} modelmirror-workflow-edge-selected`.trim(),
              style: {
                ...edge.style,
                stroke: "#fb923c",
                strokeWidth: 3,
              },
            }
          : edge,
      ),
    [edges, selectedEdgeId],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      const sourceNode = nodes.find((node) => node.id === connection.source);
      const targetNode = nodes.find((node) => node.id === connection.target);
      if (sourceNode?.data.kind === "multi_route") {
        const configuredHandles = new Set([
          ...(sourceNode.data.routes ?? []).map((route) => route.id),
          "default",
        ]);
        const sourceHandle = connection.sourceHandle ?? "";
        if (!configuredHandles.has(sourceHandle)) {
          setErrorNotice("多路分派只能从已配置规则或默认出口连线。");
          return;
        }
        if (
          edges.some(
            (edge) =>
              edge.source === sourceNode.id && edge.sourceHandle === sourceHandle,
          )
        ) {
          setErrorNotice("多路分派的每个出口只能连接一次。");
          return;
        }
      }
      if (
        sourceNode?.data.kind === "question_classifier" &&
        Number(sourceNode.data.contractVersion) === 2
      ) {
        const configuredHandles = new Set([
          ...(sourceNode.data.categoriesV2 ?? []).map((category) => category.id),
          "default",
        ]);
        const sourceHandle = connection.sourceHandle ?? "";
        if (!configuredHandles.has(sourceHandle)) {
          setErrorNotice("问题分类器只能从已配置类别或默认出口连线。");
          return;
        }
        if (edges.some((edge) =>
          edge.source === sourceNode.id && edge.sourceHandle === sourceHandle
        )) {
          setErrorNotice("问题分类器的每个出口只能连接一次。");
          return;
        }
      }
      const errorConnectionError = errorOutputConnectionErrorForNodes(
        nodes,
        edges,
        connection.source,
        connection.sourceHandle,
      );
      if (errorConnectionError) {
        setErrorNotice(errorConnectionError);
        return;
      }
      const mergeConnectionError = dataMergeConnectionError(
        targetNode?.data.kind,
        connection.target,
        connection.targetHandle,
        edges,
      );
      if (mergeConnectionError) {
        setErrorNotice(mergeConnectionError);
        return;
      }
      const middlewareBinding = connection.targetHandle === "middleware";
      const resourceBinding =
        connection.targetHandle === "expert" ||
        connection.targetHandle === "knowledge" ||
        connection.targetHandle === "toolset" ||
        connection.targetHandle === "plugin";
      if (resourceBinding) {
        const expected =
          connection.targetHandle === "expert"
            ? {
                sourceKind: "external_xpert",
                sourceHandle: "expert-binding",
                color: "#93c5fd",
              }
            : connection.targetHandle === "knowledge"
              ? {
                sourceKind: "knowledge_base",
                sourceHandle: "knowledge-binding",
                color: "#5eead4",
                }
              : connection.targetHandle === "toolset"
                ? {
                  sourceKind: "toolset_resource",
                  sourceHandle: "toolset-binding",
                  color: "#fcd34d",
                  }
                : {
                    sourceKind: "plugin_resource",
                    sourceHandle: "plugin-binding",
                    color: "#c4b5fd",
                  };
        if (
          connection.sourceHandle !== expected.sourceHandle ||
          sourceNode?.data.kind !== expected.sourceKind ||
          targetNode?.data.kind !== "workflow_agent"
        ) {
          setErrorNotice("资源节点必须连接到 workflow_agent 对应的资源入口。");
          return;
        }
        if (edges.some((edge) => edge.source === connection.source)) {
          setErrorNotice("一个资源节点只能绑定一个工作流智能体。");
          return;
        }
        setEdges((currentEdges) =>
          addEdge(
            {
              ...connection,
              animated: true,
              className:
                "modelmirror-workflow-edge modelmirror-resource-binding-edge",
              style: {
                stroke: expected.color,
                strokeDasharray: "7 5",
                strokeWidth: 2,
              },
            },
            currentEdges,
          ),
        );
        commitHistory();
        if (targetNode.data.toolMode !== "mcp_tools") {
          setNodes((currentNodes) =>
            currentNodes.map((item) =>
              item.id === targetNode.id
                ? {
                    ...item,
                    data: { ...item.data, toolMode: "mcp_tools" },
                  }
                : item,
            ),
          );
        }
        setSaveNotice("");
        setErrorNotice("");
        return;
      }
      if (
        ["expert-binding", "knowledge-binding", "toolset-binding", "plugin-binding"].includes(
          connection.sourceHandle ?? "",
        ) ||
        sourceNode?.data.kind === "external_xpert" ||
        sourceNode?.data.kind === "knowledge_base" ||
        sourceNode?.data.kind === "toolset_resource" ||
        sourceNode?.data.kind === "plugin_resource"
      ) {
        setErrorNotice("资源节点只能通过专用端口绑定到 workflow_agent。");
        return;
      }
      if (middlewareBinding) {
        if (
          connection.sourceHandle !== "middleware-binding" ||
          sourceNode?.data.kind !== "runtime_middleware" ||
          targetNode?.data.kind !== "workflow_agent"
        ) {
          setErrorNotice("中间件绑定必须从 runtime_middleware 的紫色端口连接到 workflow_agent。")
          return;
        }
        if (edges.some((edge) => edge.source === connection.source)) {
          setErrorNotice("一个中间件节点只能绑定一个 Agent，且不能同时连接控制流。")
          return;
        }
      } else if (connection.sourceHandle === "middleware-binding") {
        setErrorNotice("紫色中间件端口只能连接 workflow_agent 的 middleware 入口。")
        return;
      } else if (
        sourceNode?.data.kind === "runtime_middleware" &&
        edges.some(
          (edge) =>
            edge.source === connection.source && edge.targetHandle === "middleware",
        )
      ) {
        setErrorNotice("已绑定 Agent 的中间件不能同时连接控制流。")
        return;
      }
      setEdges((currentEdges) =>
        addEdge(
          {
            ...connection,
            animated: true,
            className: middlewareBinding
              ? "modelmirror-workflow-edge modelmirror-middleware-binding-edge"
              : "modelmirror-workflow-edge",
            style: middlewareBinding
              ? { stroke: "#a5b4fc", strokeDasharray: "7 5", strokeWidth: 2 }
              : undefined,
          },
          currentEdges,
        ),
      );
      commitHistory();
      setSaveNotice("");
      setErrorNotice("");
    },
    [edges, nodes, setEdges, commitHistory],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange<WorkflowNode>[]) => {
      onNodesChange(changes);
    },
    [onNodesChange],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<WorkflowEdge>[]) => {
      onEdgesChange(changes);
      if (
        selectedEdgeId &&
        changes.some((change) => change.type === "remove" && change.id === selectedEdgeId)
      ) {
        setSelectedEdgeId(null);
      }
    },
    [onEdgesChange, selectedEdgeId],
  );

  const deleteSelectedEdge = useCallback(() => {
    if (!selectedEdgeId) return;
    commitHistory();
    setEdges((currentEdges) =>
      currentEdges.filter((edge) => edge.id !== selectedEdgeId),
    );
    setSelectedEdgeId(null);
  }, [commitHistory, selectedEdgeId, setEdges]);

  const deleteSelectedNode = useCallback(() => {
    if (!selectedNodeId) return;
    commitHistory();
    setNodes((currentNodes) =>
      currentNodes.filter((node) => node.id !== selectedNodeId),
    );
    setEdges((currentEdges) =>
      currentEdges.filter(
        (edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId,
      ),
    );
    setSelectedNodeId(null);
  }, [commitHistory, selectedNodeId, setEdges, setNodes]);

  function updateNodeData(nodeId: string, patch: Partial<WorkflowNodeData>) {
    // 配置面板与保存/运行共用同一份同步状态，避免输入后立即操作时
    // 仍有防抖 patch 留在计时器中而被序列化遗漏。
    setNodes((currentNodes) =>
      currentNodes.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              data: {
                ...node.data,
                ...patch,
              },
            }
          : node,
      ),
    );
  }

  function replaceNodeData(nodeId: string, data: WorkflowNodeData) {
    commitHistory();
    setNodes((currentNodes) =>
      currentNodes.map((node) =>
        node.id === nodeId ? { ...node, type: "workflowNode", data } : node,
      ),
    );
    setSaveNotice("节点已显式迁移；可使用撤销恢复旧配置。");
    window.setTimeout(() => setSaveNotice(""), 2600);
  }

  function replaceNodeDataBatch(
    replacements: Array<{ nodeId: string; data: WorkflowNodeData }>,
    notice: string,
  ) {
    if (!replacements.length) return;
    const byId = new Map(replacements.map((item) => [item.nodeId, item.data]));
    commitHistory();
    setNodes((currentNodes) => currentNodes.map((candidate) => {
      const replacement = byId.get(candidate.id);
      return replacement
        ? { ...candidate, type: "workflowNode", data: replacement }
        : candidate;
    }));
    setSaveNotice(notice);
    window.setTimeout(() => setSaveNotice(""), 3200);
  }

  function migrateTypedAiNode(nodeId: string): string {
    const node = nodes.find((candidate) => candidate.id === nodeId);
    if (!node) return "未找到要升级的节点。";
    const result: TypedAiMigrationResult = node.data.kind === "parameter_extractor"
      ? migrateLegacyParameterExtractor(node.data)
      : node.data.kind === "question_classifier"
        ? migrateLegacyQuestionClassifier(
            node.data,
            edges.filter((edge) => edge.source === nodeId),
            edges.map((edge) => edge.id),
          )
        : { ok: false, message: "该节点不支持 V2 升级。" };
    if (!result.ok || !result.patch) return result.message;
    commitHistory();
    setNodes((currentNodes) => currentNodes.map((candidate) =>
      candidate.id === nodeId
        ? { ...candidate, data: { ...candidate.data, ...result.patch } }
        : candidate,
    ));
    if (result.outgoingEdges) {
      setEdges((currentEdges) => [
        ...currentEdges.filter((edge) => edge.source !== nodeId),
        ...result.outgoingEdges!,
      ]);
    }
    setSaveNotice(result.message);
    window.setTimeout(() => setSaveNotice(""), 3200);
    return result.message;
  }

  function updateRuntimeMiddlewareConfig(
    nodeId: string,
    fieldName: string,
    value: unknown,
  ) {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        if (node.id !== nodeId) return node;
        const existingConfig = isRecord(node.data.runtimeMiddlewareConfig)
          ? node.data.runtimeMiddlewareConfig
          : {};
        const enablesCatalogInstall =
          node.data.runtimeMiddlewareId === "skills_runtime" &&
          fieldName === "catalog_install" &&
          value === true;
        return {
          ...node,
          data: {
            ...node.data,
            runtimeMiddlewareConfig: {
              ...existingConfig,
              ...(enablesCatalogInstall ? { catalog_search: true } : {}),
              [fieldName]: value,
            },
          },
        };
      }),
    );
    if (fieldName === "catalog_install" && value === true) {
      setSaveNotice("已自动配置 skill_install 人工审批");
      window.setTimeout(() => setSaveNotice(""), 2600);
    }
  }

  const handleStepSelect = useCallback(
    (nodeId: string) => {
      setSelectedEdgeId(null);
      setSelectedNodeId(nodeId);
      const node = nodes.find((item) => item.id === nodeId);
      if (node) {
        void fitView({ nodes: [{ id: nodeId }], duration: 350, padding: 1.6 });
      }
    },
    [fitView, nodes],
  );

  const handleVariableSourceLocate = useCallback(
    (nodeId: string) => {
      handleStepSelect(nodeId);
      setWorkspaceTab("config");
    },
    [handleStepSelect],
  );

  const createWorkflowVariable = useCallback(
    (declaration: WorkflowVariableDeclaration) => {
      commitHistory();
      setVariables((current) => [...current, declaration]);
      setSaveNotice("工作流变量已创建");
    },
    [commitHistory],
  );

  const updateWorkflowVariable = useCallback(
    (declaration: WorkflowVariableDeclaration) => {
      commitHistory();
      setVariables((current) =>
        current.map((candidate) =>
          candidate.id === declaration.id ? declaration : candidate,
        ),
      );
      setSaveNotice("工作流变量已更新");
    },
    [commitHistory],
  );

  const previewWorkflowVariableRename = useCallback(
    (oldName: string, newName: string) =>
      planWorkflowVariableRename(oldName, newName, nodes, edges, variables),
    [edges, nodes, variables],
  );

  const applyWorkflowVariableRename = useCallback(
    (plan: WorkflowVariableRenamePlan) => {
      if (!plan.allowed) return;
      commitHistory();
      setNodes(plan.nodes);
      setVariables(plan.declarations);
      setSaveNotice("变量名称及已知引用已更新");
    },
    [commitHistory, setNodes],
  );

  const deleteWorkflowVariable = useCallback(
    (declarationId: string) => {
      const declaration = variables.find(
        (candidate) => candidate.id === declarationId,
      );
      if (!declaration) return "变量已不存在。";
      const descriptor = analyzeWorkflowVariables(
        nodes,
        edges,
        null,
        variables,
      ).find((candidate) => candidate.name === declaration.name);
      if ((descriptor?.references.length ?? 0) > 0) {
        return "变量仍被引用，请先清空引用。";
      }
      commitHistory();
      setVariables((current) =>
        current.filter((candidate) => candidate.id !== declarationId),
      );
      setSaveNotice("工作流变量已删除");
      return null;
    },
    [commitHistory, edges, nodes, variables],
  );

  const handleNodeStatusChange = useCallback(
    (nodeId: string, status: NodeRunStatus | "idle") => {
      setNodes((currentNodes) =>
        currentNodes.map((node) =>
          node.id === nodeId
            ? {
                ...node,
                data: {
                  ...node.data,
                  runStatus: status === "idle" ? undefined : status,
                },
              }
            : node,
        ),
      );
    },
    [setNodes],
  );

  function stripRunStatus(def: WorkflowDefinition): WorkflowDefinition {
    return {
      ...def,
      nodes: def.nodes.map((node) => ({
        ...node,
        data: { ...node.data, runStatus: undefined },
      })),
    };
  }

  const copySelectedNode = useCallback(() => {
    if (!selectedNodeId) return;
    const node = nodes.find((item) => item.id === selectedNodeId);
    if (!node) return;
    const nodeEdges = edges.filter(
      (edge) => edge.source === selectedNodeId || edge.target === selectedNodeId,
    );
    setClipboard({
      nodes: [
        {
          ...node,
          position: { ...node.position },
          data: { ...node.data, runStatus: undefined },
        },
      ],
      edges: nodeEdges.map((edge) => ({ ...edge })),
    });
    setContextMenu(null);
    setSaveNotice("已复制节点，在画布空白处右键可粘贴。");
  }, [edges, nodes, selectedNodeId]);

  const pasteClipboard = useCallback(
    (position: { x: number; y: number }) => {
      if (!clipboard || clipboard.nodes.length === 0) return;
      const stamp = `${Date.now().toString(36)}${Math.random().toString(16).slice(2, 5)}`;
      const anchor = clipboard.nodes[0];
      const idMap = new Map<string, string>();
      const pastedNodes = clipboard.nodes.map((node) => {
        const newId = `${node.data.kind}-${stamp}-${node.id}`;
        idMap.set(node.id, newId);
        return {
          ...node,
          id: newId,
          position: {
            x: position.x + (node.position.x - anchor.position.x),
            y: position.y + (node.position.y - anchor.position.y),
          },
          data: { ...node.data, runStatus: undefined },
        };
      });
      const pastedEdges = clipboard.edges
        .map((edge) => ({
          ...edge,
          id: `${edge.id}-${stamp}`,
          source: idMap.get(edge.source) ?? edge.source,
          target: idMap.get(edge.target) ?? edge.target,
        }))
        .filter(
          (edge) =>
            idMap.has(edge.source) &&
            idMap.has(edge.target) &&
            pastedNodes.some((n) => n.id === edge.source) &&
            pastedNodes.some((n) => n.id === edge.target),
        );
      commitHistory();
      setNodes((currentNodes) => [...currentNodes, ...pastedNodes]);
      setEdges((currentEdges) => [...currentEdges, ...pastedEdges]);
      setSelectedNodeId(pastedNodes[0]?.id ?? null);
      setContextMenu(null);
    },
    [clipboard, commitHistory, setEdges, setNodes],
  );

  const pasteAtViewportCenter = useCallback(() => {
    if (!clipboard || clipboard.nodes.length === 0) return;
    const { x, y } = screenToFlowPosition({
      x: window.innerWidth / 2,
      y: window.innerHeight / 2,
    });
    pasteClipboard({ x, y });
  }, [clipboard, pasteClipboard, screenToFlowPosition]);

  const addAnnotationAt = useCallback(
    (position: { x: number; y: number }) => {
      commitHistory();
      const node = createNode("annotation", position.x, position.y);
      setNodes((currentNodes) => [...currentNodes, node]);
      setSelectedNodeId(node.id);
      setContextMenu(null);
    },
    [commitHistory, setNodes],
  );

  const handleQuickAddPick = useCallback(
    (kind: string) => {
      if (!quickAddMenu) return;
      if (onSave && INDEPENDENT_DEPLOYMENT_NODE_KINDS.has(kind as WorkflowNodeKind)) {
        setErrorNotice("Xpert 内嵌画布不能使用独立部署节点。");
        setQuickAddMenu(null);
        return;
      }
      const errorConnectionError = errorOutputConnectionErrorForNodes(
        nodes,
        edges,
        quickAddMenu.sourceNodeId,
        quickAddMenu.sourceHandle ?? null,
      );
      if (errorConnectionError) {
        setErrorNotice(errorConnectionError);
        setQuickAddMenu(null);
        return;
      }
      const position = screenToFlowPosition({
        x: quickAddMenu.x,
        y: quickAddMenu.y,
      });
      const node = createNode(kind as WorkflowNodeKind, position.x, position.y);
      commitHistory();
      setNodes((currentNodes) => [...currentNodes, node]);
      setEdges((currentEdges) =>
        addEdge(
          {
            source: quickAddMenu.sourceNodeId,
            target: node.id,
            sourceHandle: quickAddMenu.sourceHandle ?? null,
            targetHandle: null,
          },
          currentEdges,
        ),
      );
      setSelectedNodeId(node.id);
      setQuickAddMenu(null);
    },
    [commitHistory, edges, nodes, onSave, quickAddMenu, screenToFlowPosition, setEdges, setNodes],
  );

  const handleConnectEnd: OnConnectEnd = useCallback((event, connectionState) => {
      if (connectionState.isValid || !connectionState.fromNode) return;
      if (!("clientX" in event)) return;
      const { fromHandle, fromNode } = connectionState;
      if (!fromHandle) return;
      const fromHandleId = fromHandle.id ?? "";
      // 资源/中间件绑定端口必须手动连到 workflow_agent，不允许松手创建节点。
      if (
        [
          "expert-binding",
          "knowledge-binding",
          "toolset-binding",
          "plugin-binding",
          "middleware-binding",
        ].includes(fromHandleId)
      ) {
        return;
      }
      if (
        fromNode.data.kind === "output" ||
        fromNode.data.kind === "http_event_reply" ||
        fromNode.data.kind === "annotation"
      ) {
        return;
      }
      setQuickAddMenu({
        x: event.clientX,
        y: event.clientY,
        sourceNodeId: fromNode.id,
        sourceHandle: fromHandle.id ?? undefined,
      });
    },
    [],
  );

  async function persistIndependentWorkflow(
    savedDefinition: WorkflowDefinition,
  ): Promise<string> {
    saveStoredWorkflow(savedDefinition);
    if (workflowId.startsWith("wf_") && projectRevision !== null) {
      const project = await saveWorkflowProjectDraft(
        workflowId,
        projectRevision,
        savedDefinition,
      );
      setProjectRevision(project.draft_revision);
      setActiveDeployment(project.active_deployment ?? null);
      setFormPublication(project.form_publication ?? null);
      setRssSubscription(project.rss_subscription ?? null);
      setEmailSubscription(project.email_subscription ?? null);
      return project.project_id;
    }
    const project = await createWorkflowProject(savedDefinition);
    setProjectRevision(project.draft_revision);
    setActiveDeployment(project.active_deployment ?? null);
    setFormPublication(project.form_publication ?? null);
    setRssSubscription(project.rss_subscription ?? null);
    setEmailSubscription(project.email_subscription ?? null);
    navigate(`/workflow/${project.project_id}`, { replace: true });
    return project.project_id;
  }

  async function saveWorkflow(): Promise<string | null> {
    const savedDefinition = stripRunStatus({
      ...definition,
      updatedAt: new Date().toISOString(),
    });
    setIsSaving(true);
    let savedProjectId: string | null = null;
    try {
      if (onSave) {
        await onSave(savedDefinition);
        setSaveNotice("智能体草稿已保存");
      } else {
        savedProjectId = await persistIndependentWorkflow(savedDefinition);
        setSaveNotice("服务端草稿已保存；本地副本已保留");
      }
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(
        error instanceof Error ? error.message : "草稿保存失败，请稍后重试",
      );
    } finally {
      setIsSaving(false);
    }
    window.setTimeout(() => setSaveNotice(""), 1800);
    return savedProjectId;
  }

  async function rotateWebhookKey() {
    if (!activeDeployment?.active || activeDeployment.trigger_kind !== "http") return;
    setIsPublishing(true);
    try {
      const deployment = await rotateWorkflowWebhookKey(
        workflowId,
        activeDeployment.version,
      );
      setActiveDeployment(deployment);
      setOneTimeWebhookKey(deployment.webhook_key ?? "");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "密钥轮换失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function publishAndEnableForm() {
    if (onSave || isPublishing || !hasFormEntry) return;
    setIsPublishing(true);
    try {
      const projectId = await saveWorkflow();
      if (!projectId) return;
      const release = await publishWorkflowProject(projectId);
      const deployment = await activateWorkflowVersion(projectId, release.version);
      setActiveDeployment(deployment);
      if (deployment.form_publication) {
        setFormPublication(deployment.form_publication);
      }
      if (deployment.form_share_url) {
        setOneTimeFormShareUrl(deployment.form_share_url);
      }
      setSaveNotice(
        deployment.form_share_url
          ? "表单已启用，请立即保存一次性分享链接"
          : "表单已切换到新版本，原分享链接继续有效",
      );
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "表单启用失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function rotateFormKey() {
    if (!activeDeployment?.active || activeDeployment.trigger_kind !== "form") return;
    setIsPublishing(true);
    try {
      const publication = await rotateWorkflowFormKey(
        workflowId,
        activeDeployment.version,
      );
      setFormPublication(publication);
      setOneTimeFormShareUrl(publication.form_share_url ?? "");
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "表单链接轮换失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function deactivateForm() {
    if (!activeDeployment?.active || activeDeployment.trigger_kind !== "form") return;
    setIsPublishing(true);
    try {
      await deactivateWorkflowVersion(workflowId, activeDeployment.version);
      const project = await fetchWorkflowProject(workflowId);
      setActiveDeployment(project.active_deployment ?? null);
      setFormPublication(project.form_publication ?? null);
      setSaveNotice("表单已停用，新提交将返回不可用");
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "表单停用失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function publishAndEnableRss() {
    if (onSave || isPublishing || !hasRssEntry) return;
    setIsPublishing(true);
    try {
      const projectId = await saveWorkflow();
      if (!projectId) return;
      const release = await publishWorkflowProject(projectId);
      await activateWorkflowVersion(projectId, release.version);
      const project = await fetchWorkflowProject(projectId);
      setActiveDeployment(project.active_deployment ?? null);
      setRssSubscription(project.rss_subscription ?? null);
      setSaveNotice("RSS 订阅已启用，首次检查只建立当前条目基线");
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "RSS 订阅启用失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function deactivateRss() {
    if (!activeDeployment?.active || activeDeployment.trigger_kind !== "rss") return;
    setIsPublishing(true);
    try {
      await deactivateWorkflowVersion(workflowId, activeDeployment.version);
      const project = await fetchWorkflowProject(workflowId);
      setActiveDeployment(project.active_deployment ?? null);
      setRssSubscription(project.rss_subscription ?? null);
      setSaveNotice("RSS 订阅已停用，不再接收新条目");
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "RSS 订阅停用失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function publishAndEnableEmail() {
    if (onSave || isPublishing || !hasEmailEntry) return;
    setIsPublishing(true);
    try {
      const projectId = await saveWorkflow();
      if (!projectId) return;
      const release = await publishWorkflowProject(projectId);
      await activateWorkflowVersion(projectId, release.version);
      const project = await fetchWorkflowProject(projectId);
      setActiveDeployment(project.active_deployment ?? null);
      setEmailSubscription(project.email_subscription ?? null);
      setSaveNotice("邮件订阅已启用，首次检查只建立 INBOX 当前基线");
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "邮件订阅启用失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function deactivateEmail() {
    if (!activeDeployment?.active || activeDeployment.trigger_kind !== "email") return;
    setIsPublishing(true);
    try {
      await deactivateWorkflowVersion(workflowId, activeDeployment.version);
      const project = await fetchWorkflowProject(workflowId);
      setActiveDeployment(project.active_deployment ?? null);
      setEmailSubscription(project.email_subscription ?? null);
      setSaveNotice("邮件订阅已停用，不再接收新邮件");
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(error instanceof Error ? error.message : "邮件订阅停用失败。");
    } finally {
      setIsPublishing(false);
    }
  }

  async function convertToXpertDraft(selectedInputVariable?: string) {
    if (onSave || isConverting) return;
    if (!xpertRegistry) {
      setErrorNotice(
        xpertRegistryError || "节点 Registry 正在加载，请稍后再试。",
      );
      return;
    }
    const currentDefinition = stripRunStatus({
      ...definition,
      updatedAt: new Date().toISOString(),
    });
    const analysis = analyzeXpertWorkflowConversion(
      currentDefinition,
      xpertRegistry,
      selectedInputVariable,
    );
    setConversionInputVariable(analysis.selectedInputVariable);
    if (analysis.status !== "ready" || !analysis.definition) {
      setConversionReview(analysis);
      return;
    }
    setConversionReview(null);

    setIsConverting(true);
    try {
      const staticValidation = await validateXpertConversionGraph(
        analysis.definition,
      );
      if (!staticValidation.valid) {
        const blockers = staticValidation.issues
          .filter((issue) => issue.severity === "error")
          .map((issue) =>
            `${issue.node_id ? `${issue.node_id}：` : ""}${issue.message}`,
          );
        setConversionReview({
          ...analysis,
          status: "blocked",
          blockers:
            blockers.length > 0 ? blockers : ["静态图校验未通过。"],
          definition: null,
        });
        return;
      }
      const created = await createXpert({
        name: title.trim() || "未命名智能体",
        description: "由经典工作流草稿转换。",
        tags: ["workflow-import"],
      });
      await updateXpert(created.id, {
        draft: {
          ...created.draft,
          workflow: toXpertDraftWorkflow(analysis.definition),
          input_variable: analysis.selectedInputVariable,
          output_variable: analysis.outputVariable,
        },
      });
      saveStoredWorkflow(currentDefinition);
      setConversionReview(null);
      navigate(`/agents/studio/${created.id}`);
    } catch (error) {
      setErrorNotice(
        error instanceof Error ? error.message : "转换智能体草稿失败，请稍后重试",
      );
    } finally {
      setIsConverting(false);
    }
  }

  function repairXpertEntry() {
    if (!onSave || !xpertRegistry || !xpertEntryRepair || isConverting) return;
    const selectedInput =
      conversionInputVariable || xpertEntryRepair.selectedInputVariable;
    const analysis = analyzeXpertWorkflowConversion(
      definition,
      xpertRegistry,
      selectedInput,
    );
    if (analysis.status !== "ready" || !analysis.definition) return;
    commitHistory();
    setNodes(analysis.definition.nodes);
    setVariables(analysis.definition.variables ?? []);
    setSelectedNodeId(analysis.convertedEntryNodeId);
    setConversionInputVariable("");
    setErrorNotice("");
    setSaveNotice("入口已转换；请显式保存智能体草稿");
    window.setTimeout(() => setSaveNotice(""), 2600);
  }

  const paletteInsertPosition = useCallback(
    () => findAvailablePalettePosition(
      screenToFlowPosition({
        x: window.innerWidth / 2,
        y: window.innerHeight / 2,
      }),
      nodes,
    ),
    [nodes, screenToFlowPosition],
  );

  const handlePaletteAddNode = useCallback(
    (kind: WorkflowNodeKind) => {
      if (onSave && INDEPENDENT_DEPLOYMENT_NODE_KINDS.has(kind)) {
        setErrorNotice("Xpert 内嵌画布不能使用独立部署节点。");
        return;
      }
      const position = paletteInsertPosition();
      try {
        const nextNode = createNode(kind, position.x, position.y);
        commitHistory();
        setNodes((currentNodes) => [...currentNodes, nextNode]);
        setSelectedNodeId(nextNode.id);
        setIsNodePaletteOpen(false);
        setSaveNotice("");
        setErrorNotice("");
      } catch (error) {
        setErrorNotice(
          error instanceof Error ? error.message : "无法创建该工作流节点。",
        );
      }
    },
    [commitHistory, onSave, paletteInsertPosition, setNodes],
  );

  const handlePaletteAddRuntimeMiddleware = useCallback(
    (middleware: RuntimeMiddlewareNode) => {
      const position = paletteInsertPosition();
      const payload: RuntimeMiddlewareDragPayload = {
        kind: "runtime_middleware",
        runtimeMiddlewareId: middleware.id,
        runtimeMiddlewareKind: middleware.kind,
        title: middleware.title,
        description: middleware.description,
        fields: middleware.fields,
        metadata: middleware.metadata ?? {},
      };
      const nextNode = createNode(
        "runtime_middleware",
        position.x,
        position.y,
        payload,
      );
      commitHistory();
      setNodes((currentNodes) => [...currentNodes, nextNode]);
      setSelectedNodeId(nextNode.id);
      setIsNodePaletteOpen(false);
      setSaveNotice("");
      setErrorNotice("");
    },
    [commitHistory, paletteInsertPosition, setNodes],
  );

  function onDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    const position = screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    });

    const runtimeMiddlewareRaw = event.dataTransfer.getData(
      "application/modelmirror-runtime-middleware",
    );
    const runtimeMiddlewarePayload = runtimeMiddlewareRaw
      ? parseRuntimeMiddlewarePayload(runtimeMiddlewareRaw)
      : null;
    if (runtimeMiddlewarePayload) {
      const nextNode = createNode(
        "runtime_middleware",
        position.x,
        position.y,
        runtimeMiddlewarePayload,
      );
      commitHistory();
      setNodes((currentNodes) => [...currentNodes, nextNode]);
      setSelectedNodeId(nextNode.id);
      setIsNodePaletteOpen(false);
      return;
    }

    const rawKind = event.dataTransfer.getData("application/modelmirror-node");
    const fallbackPayload = parseRuntimeMiddlewarePayload(rawKind);
    if (fallbackPayload) {
      const nextNode = createNode(
        "runtime_middleware",
        position.x,
        position.y,
        fallbackPayload,
      );
      commitHistory();
      setNodes((currentNodes) => [...currentNodes, nextNode]);
      setSelectedNodeId(nextNode.id);
      setIsNodePaletteOpen(false);
      return;
    }

    const kind = rawKind as WorkflowNodeKind;
    if (!kind) return;
    if (onSave && INDEPENDENT_DEPLOYMENT_NODE_KINDS.has(kind)) {
      setErrorNotice("Xpert 内嵌画布不能使用独立部署节点。");
      return;
    }

    try {
      const nextNode = createNode(kind, position.x, position.y);
      commitHistory();
      setNodes((currentNodes) => [...currentNodes, nextNode]);
      setSelectedNodeId(nextNode.id);
      setIsNodePaletteOpen(false);
      setSaveNotice("");
      setErrorNotice("");
    } catch (error) {
      setErrorNotice(
        error instanceof Error ? error.message : "无法创建该工作流节点。",
      );
    }
  }

  useEffect(() => {
    setSelectedNodeId((current) =>
      current && nodes.some((node) => node.id === current) ? current : null,
    );
  }, [nodes]);

  useEffect(() => {
    function handleEditorKeyDown(event: KeyboardEvent) {
      if (pendingLocalDraft) return;
      const target = event.target as HTMLElement | null;
      const isEditable =
        target?.isContentEditable ||
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.tagName === "SELECT";
      const mod = event.metaKey || event.ctrlKey;

      if (mod && event.key.toLowerCase() === "c") {
        if (isEditable || !selectedNodeId) return;
        event.preventDefault();
        copySelectedNode();
        return;
      }
      if (mod && event.key.toLowerCase() === "v") {
        if (isEditable || !clipboard) return;
        event.preventDefault();
        pasteAtViewportCenter();
        return;
      }
      if (mod && event.key.toLowerCase() === "z") {
        if (isEditable) return;
        event.preventDefault();
        if (event.shiftKey) {
          redo();
        } else {
          undo();
        }
        return;
      }
      if (mod && event.key.toLowerCase() === "y") {
        if (isEditable) return;
        event.preventDefault();
        redo();
        return;
      }
      if (!mod && event.key.toLowerCase() === "f" && !isEditable) {
        event.preventDefault();
        void fitView({ padding: 0.2 });
        return;
      }
      if (event.key !== "Delete" && event.key !== "Backspace") return;
      if (isEditable) return;
      if (selectedNodeId) {
        event.preventDefault();
        deleteSelectedNode();
      } else if (selectedEdgeId) {
        event.preventDefault();
        deleteSelectedEdge();
      }
    }
    window.addEventListener("keydown", handleEditorKeyDown);
    return () => window.removeEventListener("keydown", handleEditorKeyDown);
  }, [
    clipboard,
    copySelectedNode,
    deleteSelectedEdge,
    deleteSelectedNode,
    fitView,
    pasteAtViewportCenter,
    pendingLocalDraft,
    redo,
    selectedEdgeId,
    selectedNodeId,
    undo,
  ]);

  return (
    <div
      className={`grid min-h-[calc(100vh-8rem)] gap-5 ${
        isNodePaletteOpen
          ? "xl:grid-cols-[260px_minmax(0,1fr)_380px]"
          : "xl:grid-cols-[minmax(0,1fr)_380px]"
      }`}
    >
      {pendingLocalDraft ? (
        <LocalDraftRecoveryDialog
          draft={pendingLocalDraft}
          onRestore={() => setPendingLocalDraft(null)}
          onStartBlank={startBlankWorkflow}
        />
      ) : null}
      {isNodePaletteOpen ? (
        <aside className="surface-panel max-h-[50vh] overflow-y-auto rounded-lg p-4 xl:max-h-[calc(100vh-8rem)] xl:sticky xl:top-4">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-white">工位库</p>
              <p className="mt-1 text-xs leading-5 text-slate-400">
                拖拽节点到画布，像安排招聘会工位一样搭建 AI 流水线。
              </p>
            </div>
            <button
              className="shrink-0 rounded-md border border-white/10 px-2 py-1 text-xs text-slate-400 transition hover:bg-white/10 hover:text-white"
              onClick={() => setIsNodePaletteOpen(false)}
              title="收起节点库"
              type="button"
            >
              ×
            </button>
          </div>
          <div className="mt-4">
            <NodePalette
              excludeKinds={onSave ? Array.from(INDEPENDENT_DEPLOYMENT_NODE_KINDS) : []}
              onAddNode={handlePaletteAddNode}
              onAddRuntimeMiddleware={handlePaletteAddRuntimeMiddleware}
            />
          </div>
        </aside>
      ) : null}

      <section className="relative min-w-0 rounded-lg border border-white/10 bg-[#0d1424] shadow-md">
        <div className="flex flex-col gap-3 border-b border-white/10 bg-surface-900/90 p-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <input
              className="w-full bg-transparent text-xl font-semibold text-white outline-none"
              onChange={(event) => setTitle(event.target.value)}
              value={title}
            />
            <p className="mt-1 text-sm text-slate-400">
              线性 + 条件分支 MVP，支持本地保存和后端流式试运行。
            </p>
          </div>
          <div className="relative flex flex-wrap items-center gap-1.5">
            <button
              aria-expanded={isVariableCenterOpen}
              aria-haspopup="dialog"
              className={`rounded-md border px-3 py-1.5 text-xs font-semibold transition ${
                isVariableCenterOpen
                  ? "border-brand-300/50 bg-brand-300/15 text-brand-100"
                  : "border-white/10 bg-white/[0.05] text-slate-200 hover:border-brand-300/45 hover:bg-brand-300/10 hover:text-brand-100"
              }`}
              onClick={() => setIsVariableCenterOpen(true)}
              ref={variableCenterTriggerRef}
              type="button"
            >
              变量 {workflowVariables.length}
            </button>
            <button
              className="rounded-md border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-emerald-200/45 hover:bg-emerald-300/10 hover:text-emerald-100"
              onClick={() => navigate("/data-tables")}
              type="button"
            >
              数据表
            </button>
            <button
              className={`rounded-md border px-3 py-1.5 text-xs font-semibold transition ${
                isNodePaletteOpen
                  ? "border-brand-300/50 bg-brand-300/15 text-brand-100 hover:bg-brand-300/25"
                  : "border-white/10 bg-white/[0.05] text-slate-200 hover:border-hire-200/50 hover:bg-hire-300/10 hover:text-hire-100"
              }`}
              onClick={() => setIsNodePaletteOpen((current) => !current)}
              type="button"
            >
              {isNodePaletteOpen ? "收起节点库" : "节点库"}
            </button>
            {errorNotice ? (
              <span className="rounded-md border border-rose-300/40 bg-rose-400/10 px-2.5 py-1 text-[11px] font-semibold text-rose-100">
                {errorNotice}
              </span>
            ) : null}
            {saveNotice ? (
              <span className="rounded-md border border-emerald-300/25 bg-emerald-300/10 px-2.5 py-1 text-[11px] font-semibold text-emerald-100">
                {saveNotice}
              </span>
            ) : null}
            {!onSave && activeDeployment?.next_run_at ? (
              <span className="rounded-md border border-amber-300/20 bg-amber-300/10 px-2.5 py-1 text-[11px] text-amber-100">
                下次 {new Date(activeDeployment.next_run_at * 1000).toLocaleString()}
              </span>
            ) : null}
            {!onSave && hasFormEntry ? (
              <details className="group relative z-20" data-testid="form-publication-menu">
                <summary
                  aria-label="表单发布设置"
                  className={`flex cursor-pointer list-none items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-semibold transition marker:hidden [&::-webkit-details-marker]:hidden ${
                    formPublication?.active
                      ? "border-emerald-300/30 bg-emerald-300/10 text-emerald-100 hover:bg-emerald-300/15"
                      : "border-white/10 bg-white/[0.05] text-slate-200 hover:border-emerald-200/40 hover:text-emerald-100"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`h-1.5 w-1.5 rounded-full ${
                      formPublication?.active ? "bg-emerald-300" : "bg-slate-500"
                    }`}
                  />
                  <span>
                    {formPublication?.active
                      ? `表单 v${formPublication.version}`
                      : "表单发布"}
                  </span>
                  <span className="font-normal text-current/75">
                    {formPublication?.active
                      ? "已启用"
                      : formPublication
                        ? "已停用"
                        : "未启用"}
                  </span>
                  <span
                    aria-hidden="true"
                    className="text-[10px] transition-transform group-open:rotate-180"
                  >
                    ▾
                  </span>
                </summary>
                <div className="absolute right-0 top-[calc(100%+0.5rem)] w-64 rounded-lg border border-white/10 bg-slate-950 p-3 shadow-lg">
                  <div className="mb-3 border-b border-white/10 pb-3">
                    <p className="text-xs font-semibold text-white">
                      {formPublication?.active
                        ? "公开表单正在接收提交"
                        : formPublication
                          ? "公开表单已停用"
                          : "公开表单尚未启用"}
                    </p>
                    <p className="mt-1 text-[11px] leading-4 text-slate-400">
                      {formPublication?.active
                        ? `固定版本 v${formPublication.version} · 密钥 ${formPublication.form_key_prefix}`
                        : "发布草稿后会生成一次性分享链接。"}
                    </p>
                  </div>
                  <div className="space-y-2">
                    <button
                      className="w-full rounded-md bg-emerald-300 px-3 py-2 text-left text-xs font-semibold text-slate-950 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={isPublishing || isSaving}
                      onClick={() => void publishAndEnableForm()}
                      type="button"
                    >
                      {formPublication?.active
                        ? "发布新版本并切换"
                        : "发布并启用表单"}
                    </button>
                    {formPublication?.active ? (
                      <>
                        <button
                          className="w-full rounded-md border border-white/10 px-3 py-2 text-left text-xs font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:bg-cyan-300/10 hover:text-cyan-100 disabled:opacity-50"
                          disabled={isPublishing}
                          onClick={() => void rotateFormKey()}
                          type="button"
                        >
                          轮换分享链接
                        </button>
                        <button
                          className="w-full rounded-md px-3 py-2 text-left text-xs font-semibold text-rose-200 transition hover:bg-rose-300/10 disabled:opacity-50"
                          disabled={isPublishing}
                          onClick={() => void deactivateForm()}
                          type="button"
                        >
                          停用表单
                        </button>
                      </>
                    ) : null}
                  </div>
                </div>
              </details>
            ) : null}
            {!onSave && hasRssEntry ? (
              <details className="group relative z-20" data-testid="rss-subscription-menu">
                <summary
                  aria-label="RSS 订阅设置"
                  className={`flex cursor-pointer list-none items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-semibold transition marker:hidden [&::-webkit-details-marker]:hidden ${
                    rssSubscription?.active
                      ? "border-orange-300/30 bg-orange-300/10 text-orange-100 hover:bg-orange-300/15"
                      : "border-white/10 bg-white/[0.05] text-slate-200 hover:border-orange-200/40 hover:text-orange-100"
                  }`}
                >
                  <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${rssSubscription?.active ? "bg-orange-300" : "bg-slate-500"}`} />
                  <span>{rssSubscription?.active ? `RSS v${rssSubscription.version}` : "RSS 订阅"}</span>
                  <span className="font-normal text-current/75">
                    {rssSubscription?.active
                      ? rssSubscription.baseline_established ? "监听中" : "待建基线"
                      : rssSubscription ? "已停用" : "未启用"}
                  </span>
                  <span aria-hidden="true" className="text-[10px] transition-transform group-open:rotate-180">▾</span>
                </summary>
                <div className="absolute right-0 top-[calc(100%+0.5rem)] w-72 rounded-lg border border-white/10 bg-slate-950 p-3 shadow-lg">
                  <div className="mb-3 border-b border-white/10 pb-3">
                    <p className="text-xs font-semibold text-white">
                      {rssSubscription?.active
                        ? rssSubscription.baseline_established ? "正在监听后续新条目" : "等待首次检查建立基线"
                        : "RSS 订阅尚未启用"}
                    </p>
                    <p className="mt-1 text-[11px] leading-4 text-slate-400">
                      {rssSubscription?.active
                        ? `下次检查 ${new Date(rssSubscription.next_poll_at * 1000).toLocaleString()}${rssSubscription.consecutive_failures ? ` · 连续失败 ${rssSubscription.consecutive_failures} 次` : ""}`
                        : "发布并启用后，当前已有条目不会补跑。"}
                    </p>
                    {rssSubscription?.last_success_at ? (
                      <p className="mt-1 text-[11px] text-slate-500">上次成功 {new Date(rssSubscription.last_success_at * 1000).toLocaleString()}</p>
                    ) : null}
                  </div>
                  <div className="space-y-2">
                    <button
                      className="w-full rounded-md bg-orange-300 px-3 py-2 text-left text-xs font-semibold text-slate-950 transition hover:bg-orange-200 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={isPublishing || isSaving}
                      onClick={() => void publishAndEnableRss()}
                      type="button"
                    >
                      {rssSubscription?.active ? "发布新版本并切换" : "发布并启用订阅"}
                    </button>
                    {rssSubscription?.active ? (
                      <button
                        className="w-full rounded-md px-3 py-2 text-left text-xs font-semibold text-rose-200 transition hover:bg-rose-300/10 disabled:opacity-50"
                        disabled={isPublishing}
                        onClick={() => void deactivateRss()}
                        type="button"
                      >
                        停用订阅
                      </button>
                    ) : null}
                  </div>
                </div>
              </details>
            ) : null}
            {!onSave && hasEmailEntry ? (
              <details className="group relative z-20" data-testid="email-subscription-menu">
                <summary
                  aria-label="邮件订阅设置"
                  className={`flex cursor-pointer list-none items-center gap-2 rounded-md border px-3 py-1.5 text-xs font-semibold transition marker:hidden [&::-webkit-details-marker]:hidden ${
                    emailSubscription?.active
                      ? "border-sky-300/30 bg-sky-300/10 text-sky-100 hover:bg-sky-300/15"
                      : "border-white/10 bg-white/[0.05] text-slate-200 hover:border-sky-200/40 hover:text-sky-100"
                  }`}
                >
                  <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${emailSubscription?.active ? "bg-sky-300" : "bg-slate-500"}`} />
                  <span>{emailSubscription?.active ? `邮件 v${emailSubscription.version}` : "邮件订阅"}</span>
                  <span className="font-normal text-current/75">
                    {emailSubscription?.active
                      ? emailSubscription.baseline_established ? "监听中" : "待建基线"
                      : emailSubscription ? "已停用" : "未启用"}
                  </span>
                  <span aria-hidden="true" className="text-[10px] transition-transform group-open:rotate-180">▾</span>
                </summary>
                <div className="absolute right-0 top-[calc(100%+0.5rem)] w-72 rounded-lg border border-white/10 bg-slate-950 p-3 shadow-lg">
                  <div className="mb-3 border-b border-white/10 pb-3">
                    <p className="text-xs font-semibold text-white">
                      {emailSubscription?.active
                        ? emailSubscription.baseline_established ? "只读监听 INBOX 新邮件" : "等待首次检查建立基线"
                        : "邮件订阅尚未启用"}
                    </p>
                    <p className="mt-1 text-[11px] leading-4 text-slate-400">
                      {emailSubscription?.active
                        ? `下次检查 ${new Date(emailSubscription.next_poll_at * 1000).toLocaleString()}${emailSubscription.consecutive_failures ? ` · 连续失败 ${emailSubscription.consecutive_failures} 次` : ""}`
                        : "发布并启用后，当前已有邮件不会补跑，也不会被标记为已读。"}
                    </p>
                    {emailSubscription?.last_success_at ? (
                      <p className="mt-1 text-[11px] text-slate-500">上次成功 {new Date(emailSubscription.last_success_at * 1000).toLocaleString()}</p>
                    ) : null}
                    {emailSubscription?.last_error_code ? (
                      <p className="mt-1 break-all text-[11px] text-amber-200">最近错误 {emailSubscription.last_error_code}</p>
                    ) : null}
                  </div>
                  <div className="space-y-2">
                    <button
                      className="w-full rounded-md bg-sky-300 px-3 py-2 text-left text-xs font-semibold text-slate-950 transition hover:bg-sky-200 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={isPublishing || isSaving}
                      onClick={() => void publishAndEnableEmail()}
                      type="button"
                    >
                      {emailSubscription?.active ? "发布新版本并切换" : "发布并启用订阅"}
                    </button>
                    {emailSubscription?.active ? (
                      <button
                        className="w-full rounded-md px-3 py-2 text-left text-xs font-semibold text-rose-200 transition hover:bg-rose-300/10 disabled:opacity-50"
                        disabled={isPublishing}
                        onClick={() => void deactivateEmail()}
                        type="button"
                      >
                        停用订阅
                      </button>
                    ) : null}
                  </div>
                </div>
              </details>
            ) : null}
            {!onSave && activeDeployment?.active && activeDeployment.trigger_kind === "http" ? (
              <button
                className="rounded-md border border-cyan-300/30 bg-cyan-300/10 px-3 py-1.5 text-xs font-semibold text-cyan-100 disabled:opacity-50"
                disabled={isPublishing}
                onClick={() => void rotateWebhookKey()}
                type="button"
              >
                轮换密钥
              </button>
            ) : null}
            {!onSave ? (
              <button
                className="rounded-md border border-cyan-300/30 bg-cyan-300/10 px-3 py-1.5 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-300/20 disabled:opacity-50"
                disabled={isConverting || !xpertRegistry}
                onClick={() => void convertToXpertDraft()}
                title={
                  !xpertRegistry
                    ? xpertRegistryError || "节点 Registry 正在加载"
                    : undefined
                }
                type="button"
              >
                {isConverting ? "转换中..." : "转为智能体草稿"}
              </button>
            ) : null}
            <button
              className="rounded-md bg-brand-300 px-3.5 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-brand-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
              disabled={isSaving}
              onClick={() => void saveWorkflow()}
              type="button"
            >
              {isSaving ? "保存中..." : saveLabel}
            </button>
          </div>
        </div>

        {xpertRegistryError ? (
          <div
            aria-live="polite"
            className="border-b border-amber-300/20 bg-amber-300/[0.08] px-4 py-2 text-xs text-amber-100"
          >
            {xpertRegistryError}
          </div>
        ) : null}

        {!onSave && conversionReview ? (
          <div
            aria-live="polite"
            className={`border-b px-4 py-3 ${
              conversionReview.status === "blocked"
                ? "border-rose-300/20 bg-rose-400/[0.08]"
                : "border-cyan-300/20 bg-cyan-300/[0.08]"
            }`}
          >
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-white">
                  {conversionReview.status === "selection_required"
                    ? "选择智能体输入"
                    : "暂时不能转为智能体草稿"}
                </p>
                {conversionReview.status === "selection_required" ? (
                  <p className="mt-1 text-xs leading-5 text-slate-300">
                    子流程有多个输入候选。请选择智能体每次对话接收的主输入。
                  </p>
                ) : (
                  <ul className="mt-1 space-y-1 text-xs leading-5 text-rose-100">
                    {conversionReview.blockers.map((blocker) => (
                      <li key={blocker}>• {blocker}</li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-2">
                {conversionReview.inputCandidates.length > 1 ? (
                  <select
                    aria-label="智能体主输入"
                    className="rounded-md border border-white/15 bg-slate-950 px-2.5 py-1.5 text-xs text-white outline-none focus:border-cyan-300/60"
                    onChange={(event) =>
                      setConversionInputVariable(event.target.value)
                    }
                    value={
                      conversionInputVariable ||
                      conversionReview.selectedInputVariable
                    }
                  >
                    {conversionReview.inputCandidates.map((candidate) => (
                      <option key={candidate} value={candidate}>
                        {candidate}
                      </option>
                    ))}
                  </select>
                ) : null}
                {conversionReview.status === "selection_required" ||
                (conversionReview.status === "blocked" &&
                  conversionReview.inputCandidates.length > 1) ? (
                  <button
                    className="rounded-md bg-cyan-300 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:opacity-50"
                    disabled={isConverting}
                    onClick={() =>
                      void convertToXpertDraft(
                        conversionInputVariable ||
                          conversionReview.selectedInputVariable,
                      )
                    }
                    type="button"
                  >
                    {conversionReview.status === "selection_required"
                      ? "继续转换"
                      : "重新检查"}
                  </button>
                ) : null}
                <button
                  className="rounded-md border border-white/15 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-white/10 hover:text-white"
                  onClick={() => setConversionReview(null)}
                  type="button"
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        ) : null}

        {onSave && xpertEntryRepair ? (
          <div
            aria-live="polite"
            className={`border-b px-4 py-3 ${
              xpertEntryRepair.status === "blocked"
                ? "border-rose-300/20 bg-rose-400/[0.08]"
                : "border-amber-300/20 bg-amber-300/[0.08]"
            }`}
          >
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-white">
                  独立工作流入口需要转换
                </p>
                {xpertEntryRepair.status === "blocked" ? (
                  <ul className="mt-1 space-y-1 text-xs leading-5 text-rose-100">
                    {xpertEntryRepair.blockers.map((blocker) => (
                      <li key={blocker}>• {blocker}</li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-xs leading-5 text-slate-300">
                    只修改当前智能体草稿的入口；原独立工作流不会变化。修改可撤销，仍需显式保存。
                  </p>
                )}
              </div>
              {xpertEntryRepair.status !== "blocked" ||
              xpertEntryRepair.inputCandidates.length > 1 ? (
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  {xpertEntryRepair.inputCandidates.length > 1 ? (
                    <select
                      aria-label="修复后的智能体主输入"
                      className="rounded-md border border-white/15 bg-slate-950 px-2.5 py-1.5 text-xs text-white outline-none focus:border-amber-300/60"
                      onChange={(event) =>
                        setConversionInputVariable(event.target.value)
                      }
                      value={
                        conversionInputVariable ||
                        xpertEntryRepair.selectedInputVariable
                      }
                    >
                      {xpertEntryRepair.inputCandidates.map((candidate) => (
                        <option key={candidate} value={candidate}>
                          {candidate}
                        </option>
                      ))}
                    </select>
                  ) : null}
                  {xpertEntryRepair.status !== "blocked" ? (
                    <button
                      className="rounded-md bg-amber-300 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-amber-200"
                      onClick={repairXpertEntry}
                      type="button"
                    >
                      转换为智能体输入
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {oneTimeWebhookKey ? (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-6">
            <div className="w-full max-w-xl rounded-xl border border-cyan-300/30 bg-slate-950 p-5 shadow-2xl">
              <h3 className="text-base font-semibold text-white">Webhook 密钥仅显示一次</h3>
              <p className="mt-2 text-sm leading-6 text-slate-300">
                请立即复制并安全保存。关闭后服务端只保留 SHA-256 哈希，无法再次查看。
              </p>
              <code className="mt-4 block break-all rounded-lg border border-white/10 bg-black/30 p-3 text-sm text-cyan-100">
                {oneTimeWebhookKey}
              </code>
              <div className="mt-4 flex justify-end gap-2">
                <button
                  className="rounded-md border border-white/15 px-3 py-1.5 text-xs text-slate-200"
                  onClick={() => void navigator.clipboard.writeText(oneTimeWebhookKey)}
                  type="button"
                >
                  复制
                </button>
                <button
                  className="rounded-md bg-cyan-300 px-3 py-1.5 text-xs font-semibold text-slate-950"
                  onClick={() => setOneTimeWebhookKey("")}
                  type="button"
                >
                  我已保存，关闭
                </button>
              </div>
            </div>
          </div>
        ) : null}

        <div
          className="h-[calc(100vh-15rem)] min-h-[520px] overflow-hidden rounded-b-lg"
          onDragOver={(event) => {
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onDrop={onDrop}
        >
          <ReactFlow
            edges={renderedEdges}
            fitView
            nodeTypes={nodeTypes}
            nodes={nodes}
            onConnect={handleConnect}
            onConnectEnd={handleConnectEnd}
            onEdgeClick={(event, edge) => {
              event.stopPropagation();
              setSelectedNodeId(null);
              setSelectedEdgeId(edge.id);
              setContextMenu(null);
            }}
            onEdgesChange={handleEdgesChange}
            onNodeClick={(_, node) => {
              setSelectedEdgeId(null);
              setSelectedNodeId(node.id);
              setContextMenu(null);
            }}
            onNodeContextMenu={(event, node) => {
              event.preventDefault();
              setSelectedEdgeId(null);
              setSelectedNodeId(node.id);
              setContextMenu({
                x: event.clientX,
                y: event.clientY,
                type: "node",
                nodeId: node.id,
              });
            }}
            onNodesChange={handleNodesChange}
            onPaneClick={() => {
              setSelectedEdgeId(null);
              setSelectedNodeId(null);
              setContextMenu(null);
            }}
            onPaneContextMenu={(event) => {
              event.preventDefault();
              setSelectedEdgeId(null);
              setSelectedNodeId(null);
              setContextMenu({ x: event.clientX, y: event.clientY, type: "pane" });
            }}
          >
            <Controls className="modelmirror-flow-controls" />
            {selectedEdgeId ? (
              <Panel position="bottom-left">
                <button
                  className="mb-16 rounded-full border border-rose-300/35 bg-rose-400/15 px-3 py-1.5 text-xs font-semibold text-rose-100 shadow-lg shadow-rose-950/30 transition hover:bg-rose-400/25"
                  onClick={deleteSelectedEdge}
                  type="button"
                >
                  × 删除连线
                </button>
              </Panel>
            ) : null}
            {selectedNodeId ? (
              <Panel position="bottom-left">
                <button
                  className="mb-16 rounded-full border border-rose-300/35 bg-rose-400/15 px-3 py-1.5 text-xs font-semibold text-rose-100 shadow-lg shadow-rose-950/30 transition hover:bg-rose-400/25"
                  onClick={deleteSelectedNode}
                  type="button"
                >
                  × 删除节点
                </button>
              </Panel>
            ) : null}
            <MiniMap
              maskColor="rgba(6, 9, 22, 0.68)"
              nodeColor={(node) => minimapNodeColor(String(node.data.kind))}
              pannable
              zoomable
            />
          </ReactFlow>
        </div>

        {contextMenu ? (
          <>
            <div
              aria-hidden="true"
              className="fixed inset-0 z-40"
              onClick={() => setContextMenu(null)}
              onContextMenu={(event) => {
                event.preventDefault();
                setContextMenu(null);
              }}
            />
            <div
              className="fixed z-50 w-44 overflow-hidden rounded-lg border border-white/10 bg-[#101828] py-1 shadow-xl shadow-ink-950/60"
              style={{
                left: Math.min(contextMenu.x, window.innerWidth - 184),
                top: Math.min(
                  contextMenu.y,
                  window.innerHeight - (contextMenu.type === "node" ? 176 : 152),
                ),
              }}
            >
              {contextMenu.type === "node" ? (
                <>
                  <MenuItem onClick={() => setContextMenu(null)}>
                    配置节点
                  </MenuItem>
                  <MenuItem onClick={copySelectedNode}>复制</MenuItem>
                  <MenuItem
                    onClick={() => {
                      setContextMenu(null);
                      deleteSelectedNode();
                    }}
                  >
                    删除
                  </MenuItem>
                  <MenuItem
                    onClick={() => {
                      const node = nodes.find(
                        (item) => item.id === contextMenu.nodeId,
                      );
                      addAnnotationAt(
                        node
                          ? { x: node.position.x, y: node.position.y + 88 }
                          : { x: 0, y: 0 },
                      );
                    }}
                  >
                    添加注释
                  </MenuItem>
                </>
              ) : (
                <>
                  <MenuItem
                    disabled={!clipboard}
                    onClick={() =>
                      pasteClipboard(
                        screenToFlowPosition({
                          x: contextMenu.x,
                          y: contextMenu.y,
                        }),
                      )
                    }
                  >
                    {clipboard ? "粘贴" : "粘贴（先复制节点）"}
                  </MenuItem>
                  <MenuItem
                    disabled={historyPastRef.current.length === 0}
                    onClick={() => {
                      undo();
                      setContextMenu(null);
                    }}
                  >
                    撤销（Ctrl/⌘+Z）
                  </MenuItem>
                  <MenuItem
                    disabled={historyFutureRef.current.length === 0}
                    onClick={() => {
                      redo();
                      setContextMenu(null);
                    }}
                  >
                    重做（Ctrl/⌘+Y）
                  </MenuItem>
                  <MenuItem onClick={() => fitView()}>适配视图</MenuItem>
                  <MenuItem
                    onClick={() => {
                      setSelectedNodeId(null);
                      setSelectedEdgeId(null);
                      setContextMenu(null);
                    }}
                  >
                    清空选中
                  </MenuItem>
                </>
              )}
            </div>
          </>
        ) : null}

        {quickAddMenu ? (
          <QuickNodePicker
            onClose={() => setQuickAddMenu(null)}
            onPick={handleQuickAddPick}
            x={quickAddMenu.x}
            y={quickAddMenu.y}
          />
        ) : null}
      </section>

      <aside className="surface-panel flex min-h-[520px] min-w-0 flex-col rounded-lg p-4">
        <div className="flex items-start justify-between gap-3 border-b border-white/10 pb-3">
          <div>
            <p className="text-sm font-semibold text-white">工作台</p>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              在同一侧栏内切换节点配置与试运行，减少页面纵向滚动。
            </p>
          </div>
          <div className="flex shrink-0 rounded-full border border-white/10 bg-white/[0.04] p-1">
            <button
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                workspaceTab === "config"
                  ? "bg-hire-300 text-ink-950"
                  : "text-slate-400 hover:text-slate-100"
              }`}
              onClick={() => setWorkspaceTab("config")}
              type="button"
            >
              配置
            </button>
            <button
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                workspaceTab === "run"
                  ? "bg-hire-300 text-ink-950"
                  : "text-slate-400 hover:text-slate-100"
              } disabled:cursor-wait disabled:opacity-45`}
              disabled={projectPending}
              onClick={() => setWorkspaceTab("run")}
              title={projectPending ? "正在加载服务端工作流草稿" : undefined}
              type="button"
            >
              运行
            </button>
          </div>
        </div>

        <section
          className={
            workspaceTab === "config"
              ? "min-h-0 flex-1 overflow-y-auto pt-4"
              : "hidden"
          }
        >
          <p className="text-sm font-semibold text-white">工位配置</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            节点配置会立即写入画布，下次运行直接生效。
          </p>
          <div className="mt-4">
            <NodeConfig
              declarations={variables}
              edges={edges}
              node={selectedNode}
              nodes={nodes}
              onChange={updateNodeData}
              onMigrateTypedAiNode={migrateTypedAiNode}
              onRuntimeMiddlewareConfigChange={updateRuntimeMiddlewareConfig}
              onOpenRunFileInput={openRunFileInput}
              onOpenVariableCenter={() => setIsVariableCenterOpen(true)}
              onReplaceNodeData={replaceNodeData}
              onReplaceNodeDataBatch={replaceNodeDataBatch}
              onSelectNode={setSelectedNodeId}
              workflowId={workflowId}
            />
          </div>
        </section>

        <div
          className={
            workspaceTab === "run"
              ? "min-h-0 flex flex-1 flex-col pt-4"
              : "hidden"
          }
        >
          {!onSave && deploymentExecutions[0] ? (
            <div className="mb-3 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.06] p-3 text-xs text-slate-300">
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                <span>触发：{deploymentExecutions[0].trigger_kind}</span>
                <span>固定版本：v{deploymentExecutions[0].version}</span>
                <span>状态：{deploymentExecutions[0].status}</span>
                {deploymentExecutions[0].trigger_kind === "failure" ? (
                  <span>
                    失败来源：
                    {String(
                      deploymentExecutions[0].trigger_summary?.source_project_id ??
                        deploymentExecutions[0].source_execution_id ??
                        "未知",
                    )}
                  </span>
                ) : null}
                {deploymentExecutions[0].trigger_kind === "call" ? (
                  <>
                    <span>
                      父执行：{deploymentExecutions[0].parent_execution_id ?? "测试运行"}
                    </span>
                    <span>
                      根执行：{deploymentExecutions[0].root_execution_id ?? "未知"}
                    </span>
                    <span>
                      调用节点：{deploymentExecutions[0].call_node_id ?? "未知"}
                    </span>
                  </>
                ) : null}
                {deploymentExecutions[0].wait_kind ? (
                  <span>等待：{deploymentExecutions[0].wait_kind}</span>
                ) : null}
                {deploymentExecutions[0].resume_at ? (
                  <span>
                    恢复时间：
                    {new Date(deploymentExecutions[0].resume_at * 1000).toLocaleString()}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}
          {projectPending ? (
            <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-4 py-8 text-center text-sm leading-6 text-slate-400">
              正在加载服务端工作流草稿，加载完成后才能试运行。
            </div>
          ) : (
            <WorkflowRun
              definition={definition}
              embedded
              fileInputFocusRequest={fileInputFocusRequest}
              onNodeStatusChange={handleNodeStatusChange}
              onRunStart={() => setWorkspaceTab("run")}
              onStepSelect={handleStepSelect}
            />
          )}
        </div>
      </aside>
      {oneTimeFormShareUrl ? (
        <div aria-labelledby="form-share-title" aria-modal="true" className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/80 px-4" role="dialog">
          <div className="w-full max-w-xl rounded-xl border border-white/10 bg-slate-900 p-5 shadow-lg">
            <p className="text-lg font-semibold text-white" id="form-share-title">保存表单分享链接</p>
            <p className="mt-2 text-sm leading-6 text-slate-300">完整链接只显示这一次。关闭后无法再次查看；如有遗失，请轮换生成新链接，旧链接会立即失效。</p>
            <label className="mt-4 block text-xs font-semibold text-slate-200" htmlFor="form-share-url">一次性分享链接</label>
            <textarea className="mt-2 min-h-24 w-full resize-none rounded-lg border border-white/10 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-200" id="form-share-url" readOnly value={oneTimeFormShareUrl} />
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <button className="rounded-lg border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200" onClick={() => setOneTimeFormShareUrl("")} type="button">我已保存，关闭</button>
              <button className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-500" onClick={() => { void navigator.clipboard.writeText(oneTimeFormShareUrl); setSaveNotice("分享链接已复制"); }} type="button">复制分享链接</button>
            </div>
          </div>
        </div>
      ) : null}
      <WorkflowVariableCenter
        declarations={variables}
        nodes={nodes}
        onApplyRename={applyWorkflowVariableRename}
        onClose={() => setIsVariableCenterOpen(false)}
        onCreate={createWorkflowVariable}
        onDelete={deleteWorkflowVariable}
        onLocateSource={handleVariableSourceLocate}
        onPlanRename={previewWorkflowVariableRename}
        onUpdate={updateWorkflowVariable}
        open={isVariableCenterOpen}
        selectedNode={selectedNode}
        triggerRef={variableCenterTriggerRef}
        variables={workflowVariables}
      />
    </div>
  );
}

export default function WorkflowEditor(props: WorkflowCanvasProps) {
  return (
    <ReactFlowProvider>
      <WorkflowCanvas {...props} />
    </ReactFlowProvider>
  );
}
