import { type Edge, type Node } from "@xyflow/react";
import { type RuntimeMiddlewareField } from "./runtimeMiddleware";

export type WorkflowNodeKind =
  | "input"
  | "llm"
  | "condition"
  | "code"
  | "variable_assign"
  | "template_transform"
  | "variable_aggregator"
  | "parameter_extractor"
  | "knowledge_retrieval"
  | "knowledge_citation"
  | "document_extractor"
  | "vision_understanding"
  | "human_intervention"
  | "question_classifier"
  | "agent"
  | "workflow_agent"
  | "external_xpert"
  | "knowledge_base"
  | "toolset_resource"
  | "plugin_resource"
  | "agent_task"
  | "agent_handoff"
  | "handoff_router"
  | "mcp_tool"
  | "time_tool"
  | "http_request"
  | "list_operation"
  | "iteration"
  | "json_serialize"
  | "json_deserialize"
  | "data_table_query"
  | "data_table_insert"
  | "data_table_update"
  | "data_table_delete"
  | "annotation"
  | "runtime_middleware"
  | "output";

export type WorkflowValue =
  | null
  | string
  | number
  | boolean
  | WorkflowValue[]
  | { [key: string]: WorkflowValue };

export type ConditionOperator = "equals" | "contains";

export type CodeOperation = "upper" | "lower" | "replace" | "concat" | "python";

export type HttpRequestMethod = "GET" | "POST";

export type ListOperationOperator = "length" | "join" | "first" | "last";

/** 画布节点运行态视觉状态（不持久化，运行时由 WorkflowRun 写回）。 */
export type NodeRunStatus = "running" | "done" | "error";

export interface WorkflowNodeData extends Record<string, unknown> {
  kind: WorkflowNodeKind;
  title: string;
  description: string;
  variableName?: string;
  modelId?: string;
  prompt?: string;
  outputVariable?: string;
  conditionVariable?: string;
  conditionOperator?: ConditionOperator;
  conditionValue?: string;
  codeOperation?: CodeOperation;
  codeInputVariable?: string;
  codeOutputVariable?: string;
  replaceFrom?: string;
  replaceTo?: string;
  concatValue?: string;
  pythonCode?: string;
  template?: string;
  variableNames?: string;
  outputTemplate?: string;
  schema?: string;
  queryVariable?: string;
  knowledgeBaseId?: string;
  contractVersion?: number | string;
  toolsetId?: string;
  pluginId?: string;
  xpertId?: string;
  versionPolicy?: string;
  pinnedVersion?: string;
  tableId?: string;
  pinnedSchemaVersion?: number | string;
  selectFields?: string[];
  filter?: Record<string, unknown>;
  sort?: Array<{ field: string; direction: "asc" | "desc" }>;
  limit?: number | string;
  returnMode?: "list" | "first" | "context" | "result";
  valueBindings?: Record<string, unknown>;
  topK?: string;
  scoreThreshold?: string;
  top_k?: string;
  assetIdVariable?: string;
  visionModelId?: string;
  pdfPageStrategy?: "auto" | "all" | "scanned_only";
  maxPages?: number | string;
  maxImageEdge?: number | string;
  failurePolicy?: "continue_on_error" | "strict";
  /** One-release read-only compatibility for previously saved path graphs. */
  sourcePathVariable?: string;
  categories?: string;
  defaultCategory?: string;
  matchMode?: string;
  caseSensitive?: string;
  useLlmFallback?: string;
  llmFallbackPrompt?: string;
  agentName?: string;
  agentMode?: string;
  agentStrategy?: "auto" | "function_calling" | "react";
  toolMode?: string;
  rolePrompt?: string;
  taskTitle?: string;
  taskInput?: string;
  assignedAgent?: string;
  taskIdVariable?: string;
  sourceVariable?: string;
  sourceAgent?: string;
  targetAgent?: string;
  executionMode?: string;
  waitForCompletion?: string;
  resultVariable?: string;
  waitTimeoutSeconds?: string;
  reason?: string;
  reasonTemplate?: string;
  instruction?: string;
  toolNames?: string;
  maxIterations?: string;
  temperature?: string;
  promptSuffix?: string;
  disableOutput?: string;
  enableFileUnderstanding?: string;
  parallelToolCalls?: string;
  maxToolConcurrency?: string;
  maxToolCalls?: string;
  maxToolDepth?: string;
  retryOnFailure?: string;
  fallbackModelId?: string;
  exceptionHandling?: string;
  outputSchemaMode?: string;
  outputSchemaJson?: string;
  memoryReadEnabled?: string;
  memoryReadScope?: string;
  memoryWriteEnabled?: string;
  memoryWriteTarget?: string;
  knowledgeReadEnabled?: string;
  knowledgeWriteEnabled?: string;
  knowledgeBaseIds?: string;
  nodeParametersJson?: string;
  toolName?: string;
  argumentsJson?: string;
  errorMode?: string;
  operation?: string;
  formatString?: string;
  url?: string;
  method?: HttpRequestMethod;
  headersJson?: string;
  bodyVariable?: string;
  inputVariable?: string;
  format?: "compact" | "pretty";
  content?: string;
  operator?: ListOperationOperator;
  joinSeparator?: string;
  iterationVariable?: string;
  itemTemplate?: string;
  runtimeMiddlewareId?: string;
  runtimeMiddlewareKind?: string;
  runtimeMiddlewareFields?: RuntimeMiddlewareField[];
  runtimeMiddlewareMetadata?: Record<string, unknown>;
  runtimeMiddlewareConfig?: Record<string, unknown>;
  middlewarePriority?: string;
  /** 运行时状态（画布高亮），不参与持久化与运行序列化。 */
  runStatus?: NodeRunStatus;
}

export type WorkflowNode = Node<WorkflowNodeData, "workflowNode">;

export type WorkflowEdge = Edge;

export interface WorkflowDefinition {
  id: string;
  title: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  updatedAt: string;
}

export interface WorkflowRunEvent {
  event:
    | "workflow_meta"
    | "node_start"
    | "node_delta"
    | "human_intervention_pending"
    | "runtime_approval_pending"
    | "runtime_approval_resolved"
    | "client_tool_waiting"
    | "client_tool_dispatched"
    | "client_tool_completed"
    | "office_document_bound"
    | "office_operation_started"
    | "office_operation_finished"
    | "office_operation_uncertain"
    | "client_tool_failed"
    | "sandbox_operation_started"
    | "sandbox_operation_finished"
    | "sandbox_artifact_published"
    | "skill_runtime_status"
    | "heartbeat"
    | "node_end"
    | "workflow_end"
    | "error";
  task_id?: string;
  run_id?: string;
  node_id?: string;
  node_title?: string;
  node_type?: WorkflowNodeKind;
  prompt?: string;
  approval_id?: string;
  approval_status?: string;
  request_id?: string;
  request_status?: string;
  host_id?: string;
  request_type?: "tool_call" | "final_output" | "manual_input";
  tool_name?: string;
  workspace_id?: string;
  operation_id?: string;
  artifact_id?: string;
  sequence?: number;
  output?: string;
  output_variable?: string;
  variable?: string;
  final_output?: string;
  variables?: Record<string, WorkflowValue>;
  message?: string;
  at?: number;
  strategy?: "function_calling" | "react";
  iteration?: number;
  status?: string;
  tool_call_id?: string;
  duration_ms?: number;
  candidate_id?: string;
  activated_skill_id?: string;
  source_ref?: string;
  result_count?: number;
}
