import type {
  WorkflowEdge,
  WorkflowNode,
  WorkflowNodeKind,
  WorkflowValue,
  WorkflowVariableDeclaration,
  WorkflowVariableDeclarationValueType,
} from "../../types/workflow";
import type {
  WorkflowNodeContractProjection,
  WorkflowValueSchemaProjection,
} from "./workflowNodeRegistry";

export type WorkflowVariableValueType =
  | "text"
  | "number"
  | "boolean"
  | "json"
  | "file_asset"
  | "unknown";

export type WorkflowVariableSourceKind =
  | "workflow_input"
  | "workflow_constant"
  | "file_asset"
  | "node_output";

export type WorkflowVariableAvailability =
  | "inventory"
  | "available"
  | "conditional"
  | "unavailable"
  | "conflict";

export interface WorkflowVariableSource {
  nodeId: string;
  nodeTitle: string;
  nodeKind: WorkflowNodeKind;
  field: string;
  sourceKind: WorkflowVariableSourceKind;
  valueType: WorkflowVariableValueType;
  conditional: boolean;
  declarationId?: string;
}

export type WorkflowVariableReferenceMode =
  | "binding"
  | "binding-list"
  | "template"
  | "structured"
  | "ambiguous";

export type WorkflowVariableReferenceIssue =
  | "type_mismatch"
  | "ambiguous";

export interface WorkflowVariableReference {
  nodeId: string;
  nodeTitle: string;
  nodeKind: WorkflowNodeKind;
  field: string;
  mode: WorkflowVariableReferenceMode;
  expectedTypes: WorkflowVariableValueType[];
  editable: boolean;
  issue?: WorkflowVariableReferenceIssue;
  issueReason?: string;
}

export interface WorkflowVariableDescriptor {
  name: string;
  valueType: WorkflowVariableValueType;
  sources: WorkflowVariableSource[];
  references: WorkflowVariableReference[];
  availability: WorkflowVariableAvailability;
  availabilityReason: string;
}

export interface WorkflowVariableRenameChange {
  nodeId?: string;
  nodeTitle: string;
  field: string;
  mode: WorkflowVariableReferenceMode | "declaration";
}

export interface WorkflowVariableRenamePlan {
  allowed: boolean;
  oldName: string;
  newName: string;
  changes: WorkflowVariableRenameChange[];
  blockers: string[];
  nodes: WorkflowNode[];
  declarations: WorkflowVariableDeclaration[];
}

const VARIABLE_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const SENSITIVE_VARIABLE_NAME_PATTERN =
  /(?:^|_)(?:secret|password|passwd|api_key|access_token|refresh_token|credential|private_key|env|environment)(?:$|_)/i;
const ABSOLUTE_PATH_PATTERN = /^(?:[A-Za-z]:[\\/]|\\\\|\/|~[\\/]|file:\/\/)/i;
const SENSITIVE_VARIABLE_VALUE_PATTERN = /^(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----|\$\{?[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\}?)$/i;

export type WorkflowVariableFieldMode =
  | "binding"
  | "binding-list"
  | "template"
  | "declaration";

export interface WorkflowVariableFieldDescriptor {
  nodeKind: WorkflowNodeKind;
  field: string;
  mode: WorkflowVariableFieldMode;
  /** NodeContract V3 中与该字段对应的输入端口。 */
  portName?: string;
  /** 兼容节点或无法从端口推导时的前端回退类型。 */
  fallbackTypes: WorkflowVariableValueType[];
  /** 仅在当前字段内可用，不进入工作流全局变量目录。 */
  localVariables?: Array<{
    name: string;
    label: string;
    valueType: WorkflowVariableValueType;
  }>;
}

const TEMPLATE_TYPES: WorkflowVariableValueType[] = [
  "text",
  "number",
  "boolean",
  "json",
  "unknown",
];
const TEXT_TYPES: WorkflowVariableValueType[] = ["text", "unknown"];
const JSON_TYPES: WorkflowVariableValueType[] = ["json", "unknown"];
const ANY_RENDERABLE_TYPES: WorkflowVariableValueType[] = [
  "text",
  "number",
  "boolean",
  "json",
  "unknown",
];

function field(
  nodeKind: WorkflowNodeKind,
  fieldName: string,
  mode: WorkflowVariableFieldMode,
  fallbackTypes: WorkflowVariableValueType[],
  portName?: string,
  localVariables?: WorkflowVariableFieldDescriptor["localVariables"],
): WorkflowVariableFieldDescriptor {
  return {
    nodeKind,
    field: fieldName,
    mode,
    fallbackTypes,
    portName,
    localVariables,
  };
}

/**
 * 所有工作流变量字段的唯一前端描述表。complete NodeContract 优先提供
 * 端口类型；compatibility 节点只使用这里的保守回退，不猜测额外能力。
 */
export const WORKFLOW_VARIABLE_FIELD_DESCRIPTORS: WorkflowVariableFieldDescriptor[] = [
  field("input", "variableName", "declaration", TEXT_TYPES),
  field("scheduled_start", "eventVariable", "declaration", JSON_TYPES),
  field("http_event_entry", "eventVariable", "declaration", JSON_TYPES),
  field("http_event_entry", "bodyVariable", "declaration", ANY_RENDERABLE_TYPES),
  field("failure_event_entry", "eventVariable", "declaration", JSON_TYPES),
  field("workflow_call_entry", "eventVariable", "declaration", JSON_TYPES),
  field("invoke_workflow", "resultVariable", "declaration", JSON_TYPES),
  field("suspend_wait", "untilTemplate", "template", TEMPLATE_TYPES),
  field("suspend_wait", "outputVariable", "declaration", JSON_TYPES),
  field("http_event_reply", "bodyTemplate", "template", TEMPLATE_TYPES),
  field("output", "outputVariable", "binding", ANY_RENDERABLE_TYPES, "result"),
  field("llm", "prompt", "template", TEMPLATE_TYPES),
  field("llm", "outputVariable", "declaration", TEXT_TYPES),
  field("condition", "conditionVariable", "binding", ANY_RENDERABLE_TYPES, "condition"),
  field("condition", "inputVariable", "binding", ANY_RENDERABLE_TYPES, "condition"),
  field("code", "codeInputVariable", "binding", ANY_RENDERABLE_TYPES, "input"),
  field("code", "codeOutputVariable", "declaration", TEXT_TYPES),
  field("variable_assign", "variableName", "declaration", TEXT_TYPES),
  field("variable_assign", "outputVariable", "declaration", ANY_RENDERABLE_TYPES),
  field("variable_assign", "sourceVariable", "binding", ANY_RENDERABLE_TYPES, "value"),
  field("variable_assign", "template", "template", TEMPLATE_TYPES),
  field("template_transform", "template", "template", TEMPLATE_TYPES),
  field("template_transform", "outputVariable", "declaration", TEXT_TYPES),
  field("variable_aggregator", "variableNames", "binding-list", ANY_RENDERABLE_TYPES, "values"),
  field("variable_aggregator", "outputVariable", "declaration", ANY_RENDERABLE_TYPES),
  field("parameter_extractor", "inputVariable", "binding", TEXT_TYPES, "text"),
  field("parameter_extractor", "outputVariable", "declaration", JSON_TYPES),
  field("knowledge_retrieval", "queryVariable", "binding", TEXT_TYPES, "query"),
  field("knowledge_retrieval", "outputVariable", "declaration", ["text", "json"]),
  field("knowledge_citation", "queryVariable", "binding", TEXT_TYPES, "query"),
  field("knowledge_citation", "outputVariable", "declaration", TEXT_TYPES),
  field("document_extractor", "assetIdVariable", "declaration", ["file_asset"]),
  field("document_extractor", "outputVariable", "declaration", TEXT_TYPES),
  field("vision_understanding", "assetIdVariable", "declaration", ["file_asset"]),
  field("vision_understanding", "outputVariable", "declaration", JSON_TYPES),
  field("human_intervention", "prompt", "template", TEMPLATE_TYPES),
  field("human_intervention", "outputVariable", "declaration", TEXT_TYPES),
  field("question_classifier", "inputVariable", "binding", TEXT_TYPES, "input"),
  field("question_classifier", "llmFallbackPrompt", "template", TEMPLATE_TYPES),
  field("question_classifier", "outputVariable", "declaration", TEXT_TYPES),
  field("agent", "instruction", "template", TEMPLATE_TYPES),
  field("agent", "promptSuffix", "template", TEMPLATE_TYPES),
  field("agent", "outputVariable", "declaration", TEXT_TYPES),
  field("workflow_agent", "rolePrompt", "template", TEMPLATE_TYPES),
  field("workflow_agent", "taskInput", "template", TEMPLATE_TYPES, "task"),
  field("workflow_agent", "promptSuffix", "template", TEMPLATE_TYPES),
  field("workflow_agent", "outputVariable", "declaration", TEXT_TYPES),
  field("agent_task", "taskTitle", "template", TEMPLATE_TYPES),
  field("agent_task", "taskInput", "template", TEMPLATE_TYPES),
  field("agent_task", "outputVariable", "declaration", TEXT_TYPES),
  field("agent_handoff", "taskIdVariable", "binding", TEXT_TYPES, "task_id"),
  field("agent_handoff", "reason", "template", TEMPLATE_TYPES),
  field("agent_handoff", "outputVariable", "declaration", TEXT_TYPES),
  field("agent_handoff", "resultVariable", "declaration", TEXT_TYPES),
  field("handoff_router", "sourceVariable", "binding", ANY_RENDERABLE_TYPES, "source"),
  field("handoff_router", "taskTitle", "template", TEMPLATE_TYPES),
  field("handoff_router", "reasonTemplate", "template", TEMPLATE_TYPES),
  field("handoff_router", "outputVariable", "declaration", TEXT_TYPES),
  field("handoff_router", "resultVariable", "declaration", TEXT_TYPES),
  field("mcp_tool", "argumentsJson", "template", TEMPLATE_TYPES),
  field("mcp_tool", "argumentsVariable", "binding", JSON_TYPES, "arguments"),
  field("mcp_tool", "outputVariable", "declaration", ANY_RENDERABLE_TYPES),
  field("time_tool", "inputVariable", "binding", ANY_RENDERABLE_TYPES, "input"),
  field("time_tool", "rightVariable", "binding", ANY_RENDERABLE_TYPES, "right"),
  field("time_tool", "outputVariable", "declaration", ANY_RENDERABLE_TYPES),
  field("http_request", "url", "template", TEMPLATE_TYPES),
  field("http_request", "headersJson", "template", TEMPLATE_TYPES),
  field("http_request", "bodyVariable", "binding", ANY_RENDERABLE_TYPES, "body"),
  field("http_request", "outputVariable", "declaration", ["text", "json"]),
  field("multi_route", "inputVariable", "binding", ANY_RENDERABLE_TYPES, "value"),
  field("list_operation", "inputVariable", "binding", ["json", "unknown"], "list"),
  field("list_operation", "outputVariable", "declaration", ["json", "unknown"]),
  field("data_aggregate", "inputVariable", "binding", JSON_TYPES, "rows"),
  field("data_aggregate", "outputVariable", "declaration", JSON_TYPES),
  field("dataset_compare", "leftVariable", "binding", JSON_TYPES, "left"),
  field("dataset_compare", "rightVariable", "binding", JSON_TYPES, "right"),
  field("dataset_compare", "outputVariable", "declaration", JSON_TYPES),
  field("object_transform", "inputVariable", "binding", JSON_TYPES, "object"),
  field("object_transform", "bindingVariable", "binding", ANY_RENDERABLE_TYPES),
  field("object_transform", "outputVariable", "declaration", JSON_TYPES),
  field("file_output", "inputVariable", "binding", ANY_RENDERABLE_TYPES, "content"),
  field("file_output", "filenameTemplate", "template", TEMPLATE_TYPES),
  field("file_output", "titleTemplate", "template", TEMPLATE_TYPES),
  field("file_output", "outputVariable", "declaration", JSON_TYPES),
  field("iteration", "inputVariable", "binding", ["json", "unknown"], "items"),
  field(
    "iteration",
    "itemTemplate",
    "template",
    TEMPLATE_TYPES,
    undefined,
    [{ name: "item", label: "当前迭代项", valueType: "unknown" }],
  ),
  field("iteration", "iterationVariable", "declaration", ANY_RENDERABLE_TYPES),
  field("iteration", "outputVariable", "declaration", TEXT_TYPES),
  field("json_serialize", "inputVariable", "binding", ANY_RENDERABLE_TYPES, "value"),
  field("json_serialize", "outputVariable", "declaration", TEXT_TYPES),
  field("json_deserialize", "inputVariable", "binding", TEXT_TYPES, "json"),
  field("json_deserialize", "outputVariable", "declaration", JSON_TYPES),
  field("data_table_query", "outputVariable", "declaration", JSON_TYPES),
  field("data_table_insert", "outputVariable", "declaration", JSON_TYPES),
  field("data_table_update", "outputVariable", "declaration", JSON_TYPES),
  field("data_table_delete", "outputVariable", "declaration", JSON_TYPES),
];

const FIELD_DESCRIPTOR_MAP = new Map(
  WORKFLOW_VARIABLE_FIELD_DESCRIPTORS.map((descriptor) => [
    `${descriptor.nodeKind}:${descriptor.field}`,
    descriptor,
  ]),
);

export function getWorkflowVariableFieldDescriptor(
  nodeKind: WorkflowNodeKind,
  fieldName: string,
) {
  return FIELD_DESCRIPTOR_MAP.get(`${nodeKind}:${fieldName}`) ?? null;
}

function schemaValueTypes(
  schema: WorkflowValueSchemaProjection | undefined,
): WorkflowVariableValueType[] {
  if (!schema) return [];
  const candidates = schema.any_of?.length ? schema.any_of : [schema];
  const types = new Set<WorkflowVariableValueType>();
  candidates.forEach((candidate) => {
    if (candidate.type === "string") types.add("text");
    else if (candidate.type === "object" || candidate.type === "array") {
      types.add("json");
    } else if (["number", "integer"].includes(candidate.type)) {
      types.add("number");
    } else if (candidate.type === "boolean") {
      types.add("boolean");
    } else if (candidate.type === "any") {
      types.add("text");
      types.add("json");
      types.add("unknown");
    }
  });
  return [...types];
}

export function resolveWorkflowVariableFieldTypes(
  descriptor: WorkflowVariableFieldDescriptor,
  contract?: WorkflowNodeContractProjection | null,
) {
  if (descriptor.mode === "template") return descriptor.fallbackTypes;
  if (contract?.contract_status === "complete" && descriptor.portName) {
    const port = contract.ports.find(
      (candidate) =>
        candidate.direction === "input" && candidate.name === descriptor.portName,
    );
    const inferred = schemaValueTypes(port?.value_schema);
    if (inferred.length > 0) return inferred;
  }
  return descriptor.fallbackTypes;
}

const RESOURCE_SOURCE_HANDLES = new Set([
  "expert-binding",
  "knowledge-binding",
  "toolset-binding",
  "plugin-binding",
  "middleware-binding",
]);

const RESOURCE_TARGET_HANDLES = new Set([
  "expert",
  "knowledge",
  "toolset",
  "plugin",
  "middleware",
]);

interface OutputSpec {
  field: "outputVariable" | "codeOutputVariable" | "variableName" | "eventVariable" | "bodyVariable" | "resultVariable";
  fallback: string;
  valueType:
    | WorkflowVariableValueType
    | ((node: WorkflowNode) => WorkflowVariableValueType);
  conditional?: (node: WorkflowNode) => boolean;
  enabled?: (node: WorkflowNode) => boolean;
}

const DEFAULT_OUTPUT_SPECS: Partial<Record<WorkflowNodeKind, OutputSpec[]>> = {
  scheduled_start: [
    { field: "eventVariable", fallback: "schedule_event", valueType: "json" },
  ],
  http_event_entry: [
    { field: "eventVariable", fallback: "http_event", valueType: "json" },
    {
      field: "bodyVariable",
      fallback: "request_body",
      valueType: "unknown",
      enabled: (node) => Boolean(String(node.data.bodyVariable ?? "").trim()),
    },
  ],
  failure_event_entry: [
    { field: "eventVariable", fallback: "failure_event", valueType: "json" },
  ],
  workflow_call_entry: [
    { field: "eventVariable", fallback: "call_event", valueType: "json" },
  ],
  invoke_workflow: [
    { field: "resultVariable", fallback: "workflow_result", valueType: "json" },
  ],
  suspend_wait: [
    { field: "outputVariable", fallback: "resume_event", valueType: "json" },
  ],
  llm: [{ field: "outputVariable", fallback: "llm_output", valueType: "text" }],
  code: [{ field: "codeOutputVariable", fallback: "code_output", valueType: "text" }],
  variable_assign: [
    {
      field: "variableName",
      fallback: "assigned_text",
      valueType: "text",
      enabled: (node) => String(node.data.contractVersion ?? "1") !== "2",
    },
    {
      field: "outputVariable",
      fallback: "assigned_value",
      valueType: "unknown",
      enabled: (node) => String(node.data.contractVersion ?? "1") === "2",
    },
  ],
  template_transform: [
    { field: "outputVariable", fallback: "template_output", valueType: "text" },
  ],
  variable_aggregator: [
    { field: "outputVariable", fallback: "aggregated_output", valueType: "unknown" },
  ],
  parameter_extractor: [
    {
      field: "outputVariable",
      fallback: "parameters_json",
      valueType: (node) => Number(node.data.contractVersion) === 2 ? "json" : "text",
    },
  ],
  knowledge_retrieval: [
    { field: "outputVariable", fallback: "knowledge_result", valueType: "json" },
  ],
  knowledge_citation: [
    { field: "outputVariable", fallback: "citation_anchors_json", valueType: "text" },
  ],
  document_extractor: [
    { field: "outputVariable", fallback: "document_text", valueType: "text" },
  ],
  vision_understanding: [
    { field: "outputVariable", fallback: "vision_result", valueType: "json" },
  ],
  human_intervention: [
    { field: "outputVariable", fallback: "human_input", valueType: "text" },
  ],
  question_classifier: [
    { field: "outputVariable", fallback: "category", valueType: "text" },
  ],
  agent: [{ field: "outputVariable", fallback: "agent_output", valueType: "text" }],
  workflow_agent: [
    { field: "outputVariable", fallback: "agent_output", valueType: "text" },
  ],
  agent_task: [
    { field: "outputVariable", fallback: "agent_task_id", valueType: "text" },
  ],
  agent_handoff: [
    { field: "outputVariable", fallback: "agent_handoff_id", valueType: "text" },
    {
      field: "resultVariable",
      fallback: "handoff_result",
      valueType: "text",
      conditional: () => true,
      enabled: (node) => String(node.data.waitForCompletion ?? "false") === "true",
    },
  ],
  handoff_router: [
    { field: "outputVariable", fallback: "agent_handoff_id", valueType: "text" },
    {
      field: "resultVariable",
      fallback: "handoff_result",
      valueType: "text",
      conditional: () => true,
      enabled: (node) => String(node.data.waitForCompletion ?? "false") === "true",
    },
  ],
  mcp_tool: [{
    field: "outputVariable",
    fallback: "mcp_output",
    valueType: (node) => String(node.data.contractVersion ?? "1") === "2" ? "json" : "unknown",
  }],
  time_tool: [{ field: "outputVariable", fallback: "current_time", valueType: "unknown" }],
  http_request: [
    {
      field: "outputVariable",
      fallback: "http_output",
      valueType: "text",
      enabled: (node) => String(node.data.contractVersion ?? "1") !== "2",
    },
    {
      field: "outputVariable",
      fallback: "http_response",
      valueType: "json",
      enabled: (node) => String(node.data.contractVersion ?? "1") === "2",
    },
  ],
  list_operation: [
    { field: "outputVariable", fallback: "list_output", valueType: "unknown" },
  ],
  data_aggregate: [
    { field: "outputVariable", fallback: "aggregate_result", valueType: "json" },
  ],
  dataset_compare: [
    { field: "outputVariable", fallback: "dataset_difference", valueType: "json" },
  ],
  object_transform: [
    { field: "outputVariable", fallback: "transformed_object", valueType: "json" },
  ],
  file_output: [
    { field: "outputVariable", fallback: "output_file", valueType: "json" },
  ],
  iteration: [
    { field: "outputVariable", fallback: "iteration_output", valueType: "text" },
  ],
  json_serialize: [
    { field: "outputVariable", fallback: "json_text", valueType: "text" },
  ],
  json_deserialize: [
    { field: "outputVariable", fallback: "json_value", valueType: "json" },
  ],
  data_table_query: [
    { field: "outputVariable", fallback: "table_result", valueType: "json" },
  ],
  data_table_insert: [
    { field: "outputVariable", fallback: "table_result", valueType: "json" },
  ],
  data_table_update: [
    { field: "outputVariable", fallback: "table_result", valueType: "json" },
  ],
  data_table_delete: [
    { field: "outputVariable", fallback: "table_result", valueType: "json" },
  ],
};

function trimmedString(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

export function isWorkflowControlFlowEdge(edge: WorkflowEdge) {
  return !(
    RESOURCE_SOURCE_HANDLES.has(edge.sourceHandle ?? "") ||
    RESOURCE_TARGET_HANDLES.has(edge.targetHandle ?? "")
  );
}

interface NamedWorkflowVariableSource {
  name: string;
  source: WorkflowVariableSource;
}

function declarationValueType(
  valueType: WorkflowVariableDeclarationValueType,
): WorkflowVariableValueType {
  return valueType;
}

function collectSources(
  nodes: WorkflowNode[],
  declarations: WorkflowVariableDeclaration[],
) {
  const sources: NamedWorkflowVariableSource[] = [];
  const add = (
    node: WorkflowNode,
    field: string,
    name: string,
    sourceKind: WorkflowVariableSourceKind,
    valueType: WorkflowVariableValueType,
    conditional = false,
  ) => {
    if (!name) return;
    sources.push({
      name,
      source: {
        nodeId: node.id,
        nodeTitle: String(node.data.title || node.id),
        nodeKind: node.data.kind,
        field,
        sourceKind,
        valueType,
        conditional,
      },
    });
  };

  declarations.forEach((declaration) => {
    const name = declaration.name.trim();
    if (!name) return;
    sources.push({
      name,
      source: {
        nodeId: `workflow-variable:${declaration.id}`,
        nodeTitle:
          declaration.kind === "constant" ? "工作流常量" : "工作流输入",
        nodeKind: "input",
        field: "name",
        sourceKind:
          declaration.kind === "constant"
            ? "workflow_constant"
            : "workflow_input",
        valueType: declarationValueType(declaration.valueType),
        conditional: false,
        declarationId: declaration.id,
      },
    });
  });

  nodes.forEach((node) => {
    if (node.data.kind === "input") {
      add(
        node,
        "variableName",
        trimmedString(node.data.variableName) || "user_input",
        "workflow_input",
        "text",
      );
    }

    if (
      node.data.kind === "document_extractor" ||
      node.data.kind === "vision_understanding"
    ) {
      add(
        node,
        "assetIdVariable",
        trimmedString(node.data.assetIdVariable),
        "file_asset",
        "file_asset",
      );
    }

    (DEFAULT_OUTPUT_SPECS[node.data.kind] ?? []).forEach((spec) => {
      if (spec.enabled && !spec.enabled(node)) return;
      add(
        node,
        spec.field,
        trimmedString(node.data[spec.field]) || spec.fallback,
        "node_output",
        typeof spec.valueType === "function" ? spec.valueType(node) : spec.valueType,
        spec.conditional?.(node) ?? false,
      );
    });
  });

  return sources;
}

function addReference(
  references: WorkflowVariableReference[],
  seen: Set<string>,
  node: WorkflowNode,
  field: string,
  name: string,
  mode: WorkflowVariableReferenceMode,
  expectedTypes: WorkflowVariableValueType[],
  editable = true,
) {
  if (!name) return;
  const key = `${node.id}:${field}:${name}:${mode}`;
  if (seen.has(key)) return;
  seen.add(key);
  references.push({
    nodeId: node.id,
    nodeTitle: String(node.data.title || node.id),
    nodeKind: node.data.kind,
    field,
    mode,
    expectedTypes,
    editable,
  });
}

function collectNestedVariableReferences(
  value: unknown,
  path: string,
  node: WorkflowNode,
  referencesByName: Map<string, WorkflowVariableReference[]>,
  seen: Set<string>,
) {
  if (Array.isArray(value)) {
    value.forEach((item, index) =>
      collectNestedVariableReferences(
        item,
        `${path}[${index}]`,
        node,
        referencesByName,
        seen,
      ),
    );
    return;
  }
  if (!value || typeof value !== "object") return;
  const record = value as Record<string, unknown>;
  if (record.source === "variable") {
    const name = trimmedString(record.variable);
    if (name) {
      const references = referencesByName.get(name) ?? [];
      addReference(
        references,
        seen,
        node,
        path,
        name,
        "structured",
        ANY_RENDERABLE_TYPES,
      );
      referencesByName.set(name, references);
    }
  }
  Object.entries(record).forEach(([key, child]) =>
    collectNestedVariableReferences(
      child,
      `${path}.${key}`,
      node,
      referencesByName,
      seen,
    ),
  );
}

const AMBIGUOUS_SCAN_IGNORED_FIELDS = new Set([
  "title",
  "kind",
  "modelId",
  "outputVariable",
  "codeOutputVariable",
  "variableName",
  "eventVariable",
  "resultVariable",
  "assetIdVariable",
  "iterationVariable",
]);

function collectReferences(nodes: WorkflowNode[], knownNames: Set<string>) {
  const referencesByName = new Map<string, WorkflowVariableReference[]>();
  const seen = new Set<string>();

  nodes.forEach((node) => {
    const localVariables = new Set<string>(
      WORKFLOW_VARIABLE_FIELD_DESCRIPTORS.filter(
        (descriptor) => descriptor.nodeKind === node.data.kind,
      ).flatMap((descriptor) =>
        (descriptor.localVariables ?? []).map((local) =>
          local.name === "item"
            ? trimmedString(node.data.iterationVariable) || local.name
            : local.name,
        ),
      ),
    );

    Object.entries(node.data).forEach(([field, value]) => {
      const descriptor = getWorkflowVariableFieldDescriptor(node.data.kind, field);
      if (descriptor?.mode === "binding") {
        const name = trimmedString(value);
        if (name) {
          const references = referencesByName.get(name) ?? [];
          addReference(
            references,
            seen,
            node,
            field,
            name,
            "binding",
            descriptor.fallbackTypes,
          );
          referencesByName.set(name, references);
        }
      }

      if (descriptor?.mode === "binding-list") {
        String(value ?? "")
          .split(/[,\n]+/)
          .map((item) => item.trim())
          .filter(Boolean)
          .forEach((name) => {
            const references = referencesByName.get(name) ?? [];
            addReference(
              references,
              seen,
              node,
              field,
              name,
              "binding-list",
              descriptor.fallbackTypes,
            );
            referencesByName.set(name, references);
          });
      }

      if (descriptor?.mode === "template" && typeof value === "string") {
        for (const match of value.matchAll(/{{\s*([A-Za-z_][\w.-]*)\s*}}/g)) {
          const name = match[1];
          if (localVariables.has(name)) continue;
          const references = referencesByName.get(name) ?? [];
          addReference(
            references,
            seen,
            node,
            field,
            name,
            "template",
            descriptor.fallbackTypes,
          );
          referencesByName.set(name, references);
        }
      }

      if (
        !descriptor &&
        !AMBIGUOUS_SCAN_IGNORED_FIELDS.has(field) &&
        typeof value === "string"
      ) {
        knownNames.forEach((name) => {
          if (!new RegExp(`(^|[^A-Za-z0-9_])${escapeRegExp(name)}([^A-Za-z0-9_]|$)`).test(value)) {
            return;
          }
          const strictTemplate = new RegExp(
            `{{\\s*${escapeRegExp(name)}\\s*}}`,
          );
          if (strictTemplate.test(value)) return;
          const references = referencesByName.get(name) ?? [];
          addReference(
            references,
            seen,
            node,
            field,
            name,
            "ambiguous",
            ANY_RENDERABLE_TYPES,
            false,
          );
          const latest = references.at(-1);
          if (latest) {
            latest.issue = "ambiguous";
            latest.issueReason = "自由文本中疑似引用该变量，无法安全自动改写。";
          }
          referencesByName.set(name, references);
        });
      }
    });

    collectNestedVariableReferences(
      node.data.valueBindings,
      "valueBindings",
      node,
      referencesByName,
      seen,
    );
    collectNestedVariableReferences(
      node.data.inputBindings,
      "inputBindings",
      node,
      referencesByName,
      seen,
    );
    collectNestedVariableReferences(
      node.data.argumentBindings,
      "argumentBindings",
      node,
      referencesByName,
      seen,
    );
    collectNestedVariableReferences(
      node.data.filter,
      "filter",
      node,
      referencesByName,
      seen,
    );
    collectNestedVariableReferences(
      node.data.operations,
      "operations",
      node,
      referencesByName,
      seen,
    );
  });

  return referencesByName;
}

function controlFlowGraph(nodes: WorkflowNode[], edges: WorkflowEdge[]) {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const predecessors = new Map<string, Set<string>>();
  const successors = new Map<string, Set<string>>();
  nodes.forEach((node) => {
    predecessors.set(node.id, new Set());
    successors.set(node.id, new Set());
  });
  edges.forEach((edge) => {
    if (
      !isWorkflowControlFlowEdge(edge) ||
      !nodeIds.has(edge.source) ||
      !nodeIds.has(edge.target)
    ) return;
    predecessors.get(edge.target)?.add(edge.source);
    successors.get(edge.source)?.add(edge.target);
  });
  return { nodeIds, predecessors, successors };
}

function reachable(
  source: string,
  target: string,
  successors: Map<string, Set<string>>,
) {
  if (source === target) return true;
  const visited = new Set<string>([source]);
  const pending = [...(successors.get(source) ?? [])];
  while (pending.length > 0) {
    const current = pending.pop();
    if (!current) continue;
    if (current === target) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    pending.push(...(successors.get(current) ?? []));
  }
  return false;
}

function dominators(
  nodeIds: Set<string>,
  predecessors: Map<string, Set<string>>,
) {
  const all = new Set(nodeIds);
  const result = new Map<string, Set<string>>();
  nodeIds.forEach((nodeId) => {
    result.set(
      nodeId,
      (predecessors.get(nodeId)?.size ?? 0) === 0
        ? new Set([nodeId])
        : new Set(all),
    );
  });

  for (let iteration = 0; iteration < nodeIds.size * 2; iteration += 1) {
    let changed = false;
    nodeIds.forEach((nodeId) => {
      const parents = [...(predecessors.get(nodeId) ?? [])];
      if (parents.length === 0) return;
      let next = new Set(result.get(parents[0]) ?? []);
      parents.slice(1).forEach((parent) => {
        const parentDominators = result.get(parent) ?? new Set<string>();
        next = new Set([...next].filter((item) => parentDominators.has(item)));
      });
      next.add(nodeId);
      const current = result.get(nodeId) ?? new Set<string>();
      if (
        current.size !== next.size ||
        [...current].some((item) => !next.has(item))
      ) {
        result.set(nodeId, next);
        changed = true;
      }
    });
    if (!changed) break;
  }
  return result;
}

function availabilityFor(
  sources: WorkflowVariableSource[],
  selectedNodeId: string | null,
  hasConflict: boolean,
  graph: ReturnType<typeof controlFlowGraph>,
  nodeDominators: Map<string, Set<string>>,
): Pick<WorkflowVariableDescriptor, "availability" | "availabilityReason"> {
  if (sources.length === 0) {
    return {
      availability: "unavailable",
      availabilityReason: "未找到变量生产者；保留旧引用但不会自动改写。",
    };
  }
  if (hasConflict) {
    return {
      availability: "conflict",
      availabilityReason: "存在多个变量生产者，请先修改重名输出。",
    };
  }
  if (!selectedNodeId) {
    return {
      availability: "inventory",
      availabilityReason: "选择节点后可查看该节点的引用范围。",
    };
  }
  if (sources.every((source) => source.sourceKind !== "node_output")) {
    return {
      availability: "available",
      availabilityReason: "运行开始时由工作流输入提供。",
    };
  }

  const source = sources.find((item) => item.sourceKind === "node_output");
  if (!source) {
    return { availability: "unavailable", availabilityReason: "变量来源不可用。" };
  }
  if (source.nodeId === selectedNodeId) {
    return {
      availability: "unavailable",
      availabilityReason: "当前节点尚未产生自己的输出。",
    };
  }
  const sourceReachesSelected = reachable(
    source.nodeId,
    selectedNodeId,
    graph.successors,
  );
  const selectedReachesSource = reachable(
    selectedNodeId,
    source.nodeId,
    graph.successors,
  );
  if (sourceReachesSelected && selectedReachesSource) {
    return {
      availability: "unavailable",
      availabilityReason: "变量位于循环依赖中，无法保证先于当前节点产生。",
    };
  }
  if (sourceReachesSelected) {
    if (source.conditional) {
      return {
        availability: "conditional",
        availabilityReason: "变量仅在节点的特定运行配置下产生。",
      };
    }
    if (nodeDominators.get(selectedNodeId)?.has(source.nodeId)) {
      return {
        availability: "available",
        availabilityReason: "变量在当前节点执行前确定产生。",
      };
    }
    return {
      availability: "conditional",
      availabilityReason: "变量来自非必经分支，运行时可能不存在。",
    };
  }
  if (selectedReachesSource) {
    return {
      availability: "unavailable",
      availabilityReason: "变量由当前节点的下游节点产生。",
    };
  }
  return {
    availability: "unavailable",
    availabilityReason: "变量来自与当前节点无控制流关系的旁支。",
  };
}

function sourceConflict(sources: WorkflowVariableSource[]) {
  return new Set(
    sources.map(
      (source) =>
        `${source.declarationId ?? source.nodeId}:${source.field}:${source.sourceKind}`,
    ),
  ).size > 1;
}

export function analyzeWorkflowVariables(
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  selectedNodeId: string | null = null,
  declarations: WorkflowVariableDeclaration[] = [],
): WorkflowVariableDescriptor[] {
  const sources = collectSources(nodes, declarations);
  const grouped = new Map<string, WorkflowVariableSource[]>();
  sources.forEach(({ name, source }) => {
    grouped.set(name, [...(grouped.get(name) ?? []), source]);
  });
  const referencesByName = collectReferences(nodes, new Set(grouped.keys()));

  const graph = controlFlowGraph(nodes, edges);
  const nodeDominators = dominators(graph.nodeIds, graph.predecessors);
  const names = new Set([...grouped.keys(), ...referencesByName.keys()]);
  return [...names]
    .map((name) => {
      const variableSources = grouped.get(name) ?? [];
      const types = new Set(variableSources.map((source) => source.valueType));
      const hasConflict = sourceConflict(variableSources);
      const references = (referencesByName.get(name) ?? []).map((reference) => {
        if (
          variableSources.length > 0 &&
          reference.mode !== "ambiguous" &&
          reference.expectedTypes.length > 0 &&
          !reference.expectedTypes.includes("unknown") &&
          !reference.expectedTypes.includes(
            types.size === 1
              ? ([...types][0] as WorkflowVariableValueType)
              : "unknown",
          )
        ) {
          return {
            ...reference,
            issue: "type_mismatch" as const,
            issueReason: "变量类型与该字段要求不兼容。",
          };
        }
        return reference;
      });
      return {
        name,
        valueType:
          types.size === 1 ? ([...types][0] as WorkflowVariableValueType) : "unknown",
        sources: variableSources,
        references,
        ...availabilityFor(
          variableSources,
          selectedNodeId,
          hasConflict,
          graph,
          nodeDominators,
        ),
      } satisfies WorkflowVariableDescriptor;
    })
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function valueContainsUnsafePathOrSecret(value: WorkflowValue): boolean {
  if (typeof value === "string") {
    const cleanValue = value.trim();
    return (
      ABSOLUTE_PATH_PATTERN.test(cleanValue) ||
      SENSITIVE_VARIABLE_VALUE_PATTERN.test(cleanValue)
    );
  }
  if (Array.isArray(value)) return value.some(valueContainsUnsafePathOrSecret);
  if (value && typeof value === "object") {
    return Object.entries(value).some(
      ([key, child]) =>
        SENSITIVE_VARIABLE_NAME_PATTERN.test(key) ||
        valueContainsUnsafePathOrSecret(child),
    );
  }
  return false;
}

function valueMatchesDeclarationType(
  valueType: WorkflowVariableDeclarationValueType,
  value: WorkflowValue,
) {
  if (valueType === "text") return typeof value === "string";
  if (valueType === "number") return typeof value === "number" && Number.isFinite(value);
  if (valueType === "boolean") return typeof value === "boolean";
  return true;
}

export function validateWorkflowVariableDeclaration(
  declaration: WorkflowVariableDeclaration,
  declarations: WorkflowVariableDeclaration[],
  nodes: WorkflowNode[],
) {
  const errors: string[] = [];
  if (!VARIABLE_NAME_PATTERN.test(declaration.name)) {
    errors.push("名称需以字母或下划线开头，只能包含字母、数字和下划线，最长 64 位。");
  }
  if (SENSITIVE_VARIABLE_NAME_PATTERN.test(declaration.name)) {
    errors.push("变量名称不能表示密钥、凭据或环境变量。");
  }
  if (
    declarations.some(
      (candidate) =>
        candidate.id !== declaration.id && candidate.name === declaration.name,
    )
  ) {
    errors.push("已有同名工作流变量。");
  }
  const nodeNames = new Set(
    collectSources(nodes, [])
      .map(({ name }) => name),
  );
  if (nodeNames.has(declaration.name)) {
    errors.push("名称与节点输出变量冲突。");
  }
  if (declaration.kind === "constant" && declaration.defaultValue === undefined) {
    errors.push("常量必须设置值。");
  }
  if (
    declaration.defaultValue !== undefined &&
    !valueMatchesDeclarationType(declaration.valueType, declaration.defaultValue)
  ) {
    errors.push("默认值与声明类型不一致。");
  }
  if (
    declaration.defaultValue !== undefined &&
    valueContainsUnsafePathOrSecret(declaration.defaultValue)
  ) {
    errors.push("变量值不能包含绝对路径或明显凭据字段。");
  }
  return errors;
}

function replaceStrictTemplateToken(value: string, oldName: string, newName: string) {
  return value.replace(
    new RegExp(`{{(\\s*)${escapeRegExp(oldName)}(\\s*)}}`, "g"),
    `{{$1${newName}$2}}`,
  );
}

function replaceStructuredVariable(value: unknown, oldName: string, newName: string): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => replaceStructuredVariable(item, oldName, newName));
  }
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;
  return Object.fromEntries(
    Object.entries(record).map(([key, child]) => {
      if (key === "variable" && record.source === "variable" && child === oldName) {
        return [key, newName];
      }
      return [key, replaceStructuredVariable(child, oldName, newName)];
    }),
  );
}

export function planWorkflowVariableRename(
  oldName: string,
  newName: string,
  nodes: WorkflowNode[],
  edges: WorkflowEdge[],
  declarations: WorkflowVariableDeclaration[],
): WorkflowVariableRenamePlan {
  const normalized = newName.trim();
  const variables = analyzeWorkflowVariables(nodes, edges, null, declarations);
  const target = variables.find((variable) => variable.name === oldName);
  const blockers: string[] = [];
  if (!VARIABLE_NAME_PATTERN.test(normalized)) {
    blockers.push("新名称格式无效。");
  }
  if (SENSITIVE_VARIABLE_NAME_PATTERN.test(normalized)) {
    blockers.push("新名称不能表示密钥、凭据或环境变量。");
  }
  if (normalized !== oldName && variables.some((variable) => variable.name === normalized)) {
    blockers.push("新名称已被其他变量使用。");
  }
  if (!target || target.sources.length === 0) blockers.push("未找到变量生产者。");
  if ((target?.sources.length ?? 0) > 1) blockers.push("变量存在多个生产者，无法安全改名。");
  target?.references
    .filter((reference) => !reference.editable || reference.mode === "ambiguous")
    .forEach((reference) =>
      blockers.push(`${reference.nodeTitle} · ${reference.field} 含无法确认的自由文本引用。`),
    );

  const nextNodes = nodes.map((node) => ({
    ...node,
    data: structuredClone(node.data),
  }));
  const nextDeclarations = declarations.map((declaration) => ({
    ...declaration,
    defaultValue:
      declaration.defaultValue === undefined
        ? undefined
        : structuredClone(declaration.defaultValue),
  }));
  const changes: WorkflowVariableRenameChange[] = [];
  if (blockers.length === 0 && target) {
    target.sources.forEach((source) => {
      if (source.declarationId) {
        const declaration = nextDeclarations.find(
          (candidate) => candidate.id === source.declarationId,
        );
        if (declaration) declaration.name = normalized;
        changes.push({ nodeTitle: source.nodeTitle, field: "name", mode: "declaration" });
        return;
      }
      const node = nextNodes.find((candidate) => candidate.id === source.nodeId);
      if (node && node.data[source.field] === oldName) {
        node.data[source.field] = normalized;
        changes.push({
          nodeId: node.id,
          nodeTitle: source.nodeTitle,
          field: source.field,
          mode: "declaration",
        });
      }
    });
    target.references.forEach((reference) => {
      const node = nextNodes.find((candidate) => candidate.id === reference.nodeId);
      if (!node) return;
      const current = node.data[reference.field];
      if (reference.mode === "binding" && current === oldName) {
        node.data[reference.field] = normalized;
      } else if (reference.mode === "binding-list" && typeof current === "string") {
        node.data[reference.field] = current
          .split(/([,\n]+)/)
          .map((item) => (item.trim() === oldName ? item.replace(oldName, normalized) : item))
          .join("");
      } else if (reference.mode === "template" && typeof current === "string") {
        node.data[reference.field] = replaceStrictTemplateToken(current, oldName, normalized);
      } else if (reference.mode === "structured") {
        const root = reference.field.startsWith("valueBindings")
          ? "valueBindings"
          : reference.field.startsWith("inputBindings")
            ? "inputBindings"
            : reference.field.startsWith("argumentBindings")
              ? "argumentBindings"
              : reference.field.startsWith("operations")
                ? "operations"
                : "filter";
        const replaced = replaceStructuredVariable(node.data[root], oldName, normalized);
        if (root === "valueBindings") {
          node.data.valueBindings = replaced as typeof node.data.valueBindings;
        } else if (root === "inputBindings") {
          node.data.inputBindings = replaced as typeof node.data.inputBindings;
        } else if (root === "argumentBindings") {
          node.data.argumentBindings = replaced as typeof node.data.argumentBindings;
        } else if (root === "operations") {
          node.data.operations = replaced as typeof node.data.operations;
        } else {
          node.data.filter = replaced as typeof node.data.filter;
        }
      }
      changes.push({
        nodeId: node.id,
        nodeTitle: reference.nodeTitle,
        field: reference.field,
        mode: reference.mode,
      });
    });
  }

  return {
    allowed: blockers.length === 0,
    oldName,
    newName: normalized,
    changes,
    blockers: [...new Set(blockers)],
    nodes: nextNodes,
    declarations: nextDeclarations,
  };
}
