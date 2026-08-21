import { type Edge, type Node } from "@xyflow/react";
import { type RuntimeMiddlewareField } from "./runtimeMiddleware";

export type WorkflowNodeKind =
  | "input"
  | "scheduled_start"
  | "http_event_entry"
  | "failure_event_entry"
  | "workflow_call_entry"
  | "invoke_workflow"
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
  | "terminate_error"
  | "multi_route"
  | "list_operation"
  | "data_aggregate"
  | "iteration"
  | "json_serialize"
  | "json_deserialize"
  | "data_table_query"
  | "data_table_insert"
  | "data_table_update"
  | "data_table_delete"
  | "annotation"
  | "runtime_middleware"
  | "suspend_wait"
  | "http_event_reply"
  | "output";

export type WorkflowValue =
  | null
  | string
  | number
  | boolean
  | WorkflowValue[]
  | { [key: string]: WorkflowValue };

export type WorkflowVariableDeclarationKind = "input" | "constant";

export type WorkflowVariableDeclarationValueType =
  | "text"
  | "number"
  | "boolean"
  | "json";

export interface WorkflowVariableDeclaration {
  id: string;
  name: string;
  kind: WorkflowVariableDeclarationKind;
  valueType: WorkflowVariableDeclarationValueType;
  defaultValue?: WorkflowValue;
  description?: string;
}

export type ConditionOperator = "equals" | "contains";

export type CodeOperation = "upper" | "lower" | "replace" | "concat" | "python";

export type HttpRequestMethod = "GET" | "POST";

export type WorkflowComparisonOperator =
  | "equals"
  | "not_equals"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "contains"
  | "in"
  | "is_null";

export type WorkflowComparisonValueType =
  | "text"
  | "number"
  | "boolean"
  | "null"
  | "json";

export interface WorkflowComparisonRule {
  id?: `route_${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8}`;
  label?: string;
  field?: string;
  operator: WorkflowComparisonOperator;
  valueType?: WorkflowComparisonValueType;
  value?: WorkflowValue;
}

export interface WorkflowSortKey {
  field: string;
  direction: "asc" | "desc";
  nulls: "first" | "last";
}

export interface WorkflowAggregateMeasure {
  outputField: string;
  operation: "count" | "sum" | "avg" | "min" | "max";
  sourceField?: string;
}

export type ListOperationOperator =
  | "length"
  | "join"
  | "first"
  | "last"
  | "filter"
  | "sort"
  | "deduplicate";

/** 画布节点运行态视觉状态（不持久化，运行时由 WorkflowRun 写回）。 */
export type NodeRunStatus = "running" | "done" | "error";

export interface WorkflowNodeData extends Record<string, unknown> {
  kind: WorkflowNodeKind;
  title: string;
  description: string;
  variableName?: string;
  eventVariable?: string;
  scheduleType?: "once" | "interval" | "cron";
  onceAt?: string;
  intervalSeconds?: number | string;
  cronExpression?: string;
  timezone?: string;
  acceptedContentType?: "both" | "json" | "text";
  maxBodyBytes?: number | string;
  waitMode?: "duration" | "until";
  untilInputMode?: "fixed" | "template";
  untilTimezone?: string;
  durationSeconds?: number | string;
  untilTemplate?: string;
  statusCode?: number | string;
  responseBodyType?: "text" | "json";
  bodyTemplate?: string;
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
  targetProjectId?: string;
  targetVersion?: number | string;
  inputBindings?: Record<string, unknown>;
  timeoutSeconds?: number | string;
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
  errorCode?: string;
  message?: string;
  sourceProjectIds?: string[];
  inputVariable?: string;
  format?: "compact" | "pretty";
  content?: string;
  operator?: ListOperationOperator;
  joinSeparator?: string;
  routes?: WorkflowComparisonRule[];
  filterMode?: "all" | "any";
  filterRules?: WorkflowComparisonRule[];
  sortKeys?: WorkflowSortKey[];
  deduplicateFields?: string[];
  groupByFields?: string[];
  measures?: WorkflowAggregateMeasure[];
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
  variables?: WorkflowVariableDeclaration[];
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
    | "timer_waiting"
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
    | "skill_creator_handoff"
    | "heartbeat"
    | "node_end"
    | "workflow_cancelled"
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
  wait_kind?: string;
  wait_id?: string;
  resume_at?: number;
  host_id?: string;
  session_id?: string;
  error_code?: string;
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
  code?: string;
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
