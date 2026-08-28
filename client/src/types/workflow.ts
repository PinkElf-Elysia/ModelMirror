import { type Edge, type Node } from "@xyflow/react";
import { type RuntimeMiddlewareField } from "./runtimeMiddleware";

export type WorkflowNodeKind =
  | "input"
  | "scheduled_start"
  | "http_event_entry"
  | "form_event_entry"
  | "rss_event_entry"
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
  | "knowledge_write_proposal"
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
  | "data_merge"
  | "dataset_compare"
  | "object_transform"
  | "file_output"
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

export interface WorkflowVariablePackBinding {
  id: string;
  sourceVariable: string;
  outputField: string;
}

export type ConditionOperator = "equals" | "contains";

/** Legacy Code V1 operation set. Python remains readable for old drafts only. */
export type CodeOperation = "upper" | "lower" | "replace" | "concat" | "python";

/** Safe Text Processing V2 operations available to newly created nodes. */
export type SafeTextOperation = Exclude<CodeOperation, "python">;

export type HttpRequestMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

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

export interface WorkflowHttpBinding {
  source: "literal" | "variable";
  variable?: string;
  valueType?: WorkflowComparisonValueType;
  value?: WorkflowValue;
}

export interface WorkflowHttpParameter {
  id: string;
  name: string;
  binding: WorkflowHttpBinding;
}

export interface WorkflowMcpArgumentBinding {
  id: string;
  name: string;
  binding: {
    source: "literal" | "variable";
    value?: WorkflowValue;
    variable?: string;
  };
}

export interface WorkflowIterationInputBinding {
  source: "item" | "index" | "variable" | "literal";
  variable?: string;
  value?: WorkflowValue;
}

export type ListOperationOperator =
  | "length"
  | "join"
  | "first"
  | "last"
  | "filter"
  | "sort"
  | "deduplicate"
  | "take"
  | "skip"
  | "slice";

export interface WorkflowObjectBinding {
  source: "literal" | "variable";
  variable?: string;
  valueType?: WorkflowComparisonValueType;
  value?: WorkflowValue;
}

export interface WorkflowObjectOperation {
  id: string;
  operation: "set" | "set_default" | "rename" | "remove" | "keep_only";
  sourceField?: string;
  targetField?: string;
  fields?: string[];
  binding?: WorkflowObjectBinding;
}

export interface WorkflowFileColumn {
  id: string;
  field: string;
  label: string;
}

export type WorkflowExtractorFieldType =
  | "string"
  | "number"
  | "boolean"
  | "string_array"
  | "number_array";

export interface WorkflowExtractorField {
  id: string;
  name: string;
  description: string;
  valueType: WorkflowExtractorFieldType;
  required: boolean;
  nullable: boolean;
}

export type WorkflowFormFieldType =
  | "short_text"
  | "long_text"
  | "email"
  | "number"
  | "boolean"
  | "date"
  | "single_select"
  | "multi_select";

export interface WorkflowFormOption {
  id: string;
  value: string;
  label: string;
}

export interface WorkflowFormField {
  id: string;
  outputVariable: string;
  label: string;
  helpText: string;
  placeholder: string;
  type: WorkflowFormFieldType;
  required: boolean;
  options: WorkflowFormOption[];
}

export interface WorkflowClassifierCategory {
  id: string;
  label: string;
  description: string;
  keywords: string[];
  matchMode: "contains_any" | "contains_all";
}

/** 画布节点运行态视觉状态（不持久化，运行时由 WorkflowRun 写回）。 */
export type NodeRunStatus = "running" | "done" | "skipped" | "error";

export interface WorkflowNodeData extends Record<string, unknown> {
  kind: WorkflowNodeKind;
  title: string;
  description: string;
  variableName?: string;
  valueSource?: "literal" | "variable" | "template";
  literalValue?: WorkflowValue;
  eventVariable?: string;
  submissionVariable?: string;
  feedUrl?: string;
  pollIntervalMinutes?: number | string;
  formTitle?: string;
  formDescription?: string;
  submitLabel?: string;
  privacyNotice?: string;
  successTitle?: string;
  successMessage?: string;
  theme?: "light" | "dark";
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
  interactionMode?: "input" | "approval";
  outputVariable?: string;
  conditionVariable?: string;
  conditionOperator?: ConditionOperator;
  conditionValue?: string;
  field?: string;
  valueType?: WorkflowComparisonValueType;
  value?: WorkflowValue;
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
  bindings?: WorkflowVariablePackBinding[];
  schema?: string;
  schemaMode?: "fields" | "json_schema";
  outputShape?: "object" | "object_list";
  fields?: WorkflowExtractorField[];
  jsonSchema?: Record<string, unknown>;
  repairAttempts?: number | string;
  queryVariable?: string;
  knowledgeBaseId?: string;
  titleTemplate?: string;
  contentVariable?: string;
  tags?: string[];
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
  categoriesV2?: WorkflowClassifierCategory[];
  classificationMode?: "rules_only" | "rules_then_model" | "model_only";
  defaultLabel?: string;
  defaultCategory?: string;
  matchMode?: string;
  caseSensitive?: string | boolean;
  useLlmFallback?: string | boolean;
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
  taskVariable?: string;
  taskValueKind?: "receipt" | "task_id";
  sourceVariable?: string;
  sourceAgent?: string;
  targetAgent?: string;
  targetMode?: "inbox" | "xpert";
  inboxTarget?: string;
  targetXpertId?: string;
  executionMode?: string;
  waitForCompletion?: string | boolean;
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
  serverId?: string;
  inputSchemaChecksum?: string;
  argumentMode?: "fields" | "object_variable";
  argumentBindings?: WorkflowMcpArgumentBinding[];
  argumentsVariable?: string;
  argumentsJson?: string;
  errorMode?: string;
  operation?: string;
  formatString?: string;
  amount?: number | string;
  unit?: string;
  url?: string;
  method?: HttpRequestMethod;
  headersJson?: string;
  bodyVariable?: string;
  queryItems?: WorkflowHttpParameter[];
  headerItems?: WorkflowHttpParameter[];
  bodyMode?: "none" | "json" | "text" | "form";
  bodyBinding?: WorkflowHttpBinding;
  formFields?: WorkflowHttpParameter[];
  authType?: "none" | "api_key" | "bearer" | "basic";
  credentialId?: string;
  apiKeyLocation?: "header" | "query";
  apiKeyName?: string;
  redirectLimit?: number | string;
  responseLimitBytes?: number | string;
  responseMode?: "auto" | "json" | "text";
  statusPolicy?: "success_only" | "capture_all";
  errorCode?: string;
  message?: string;
  sourceProjectIds?: string[];
  inputVariable?: string;
  sourceMode?: "http_response" | "file_asset";
  outputMode?: "structured" | "text";
  format?:
    | "auto"
    | "html"
    | "xml"
    | "compact"
    | "pretty"
    | "plain_text"
    | "markdown"
    | "json"
    | "csv"
    | "pdf"
    | "docx"
    | "xlsx";
  filenameTemplate?: string;
  columns?: WorkflowFileColumn[];
  content?: string;
  operator?: ListOperationOperator | WorkflowComparisonOperator;
  joinSeparator?: string;
  routes?: WorkflowComparisonRule[];
  filterMode?: "all" | "any";
  filterRules?: WorkflowComparisonRule[];
  sortKeys?: WorkflowSortKey[];
  deduplicateFields?: string[];
  count?: number | string;
  startIndex?: number | string;
  endIndex?: number | string;
  operations?: WorkflowObjectOperation[];
  groupByFields?: string[];
  measures?: WorkflowAggregateMeasure[];
  mergeMode?: "append" | "keyed_join";
  leftVariable?: string;
  rightVariable?: string;
  keyFields?: string[];
  includeUnchanged?: boolean;
  iterationVariable?: string;
  mode?: "template_map" | "workflow_map";
  itemVariable?: string;
  indexVariable?: string;
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

export interface ProviderRouteCallReceipt {
  call_sequence: number;
  model_id: string;
  actual_model?: string | null;
  dispatched?: boolean;
  status: "passed" | "failed" | "uncertain" | "cancelled";
  error_code?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
}

export interface ProviderRouteReceipt {
  contract_version: string;
  entry_id:
    | "meta_agent"
    | "workflow_interactive_llm"
    | "workflow_deployment_llm"
    | "workflow_interactive_agent"
    | "workflow_deployment_agent"
    | "xpert"
    | "xpert_app";
  routing_mode: "managed_required";
  run_reference: string;
  status: "running" | "passed" | "failed" | "uncertain" | "cancelled";
  call_count: number;
  reason_codes: string[];
  calls: ProviderRouteCallReceipt[];
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
    | "agent_handoff_waiting"
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
    | "skill_hook_status"
    | "skill_creator_handoff"
    | "heartbeat"
    | "node_end"
    | "node_skipped"
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
  agent_task_id?: string;
  agent_handoff_id?: string;
  target_kind?: "inbox" | "xpert";
  target_id?: string;
  target_version?: number;
  resume_at?: number;
  host_id?: string;
  session_id?: string;
  error_code?: string;
  request_type?: "tool_call" | "final_output" | "manual_input" | "execution_gate";
  revision?: number;
  interaction_mode?: "input" | "approval";
  expires_at?: number;
  contract_version?: number;
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
  skill_id?: string;
  hook_id?: string;
  hook_event?: "session_start" | "pre_tool_use" | "post_tool_use" | "session_end";
  hook_mode?: "annotation" | "validation" | "guard";
  skill_version_id?: string;
  requirement?: "required" | "available";
  required_skill_ids?: string[];
  available_skill_ids?: string[];
  resource_count?: number;
  resource_paths?: string[];
  source_ref?: string;
  result_count?: number;
  provider_route_receipts?: ProviderRouteReceipt;
}
