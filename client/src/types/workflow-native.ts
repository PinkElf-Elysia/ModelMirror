import type { WorkflowDefinition, WorkflowEdge, WorkflowNode } from "./workflow";

export type NativeNodeKind =
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
  | "human_intervention"
  | "question_classifier"
  | "agent"
  | "workflow_agent"
  | "mcp_tool"
  | "time_tool"
  | "http_request"
  | "list_operation"
  | "iteration"
  | "output";

export type DifyConceptNodeKind =
  | "start"
  | "llm"
  | "if-else"
  | "code"
  | "variable-assigner"
  | "template-transform"
  | "variable-aggregator"
  | "parameter-extractor"
  | "knowledge-retrieval"
  | "knowledge-citation"
  | "document-extractor"
  | "human-in-the-loop"
  | "question-classifier"
  | "agent"
  | "tool"
  | "time"
  | "http-request"
  | "list-operator"
  | "iteration"
  | "end";

export interface DifyNodeMapping {
  native: NativeNodeKind;
  dify: DifyConceptNodeKind;
  note: string;
}

export const difyNodeMappings: DifyNodeMapping[] = [
  {
    native: "input",
    dify: "start",
    note: "Native input holds initial variables; Dify start collects app inputs.",
  },
  {
    native: "llm",
    dify: "llm",
    note: "Both represent one model invocation with prompt and model config.",
  },
  {
    native: "condition",
    dify: "if-else",
    note: "Native condition starts with equals/contains; Dify supports richer branches.",
  },
  {
    native: "code",
    dify: "code",
    note: "Native code is restricted to safe built-ins; Dify runs sandboxed code.",
  },
  {
    native: "variable_assign",
    dify: "variable-assigner",
    note: "Native variable assignment renders a template into one variable.",
  },
  {
    native: "template_transform",
    dify: "template-transform",
    note: "Native template transform renders long text templates into one variable.",
  },
  {
    native: "variable_aggregator",
    dify: "variable-aggregator",
    note: "Native aggregator combines named string variables into text or JSON.",
  },
  {
    native: "parameter_extractor",
    dify: "parameter-extractor",
    note: "Native extractor asks a model to return JSON parameters from one input variable.",
  },
  {
    native: "knowledge_retrieval",
    dify: "knowledge-retrieval",
    note: "Native retrieval queries the local RAG service when a knowledge base exists.",
  },
  {
    native: "knowledge_citation",
    dify: "knowledge-citation",
    note: "Native citation maps local RAG retrieval results into CitationAnchor JSON.",
  },
  {
    native: "document_extractor",
    dify: "document-extractor",
    note: "Native document extraction reads only sandboxed local files.",
  },
  {
    native: "human_intervention",
    dify: "human-in-the-loop",
    note: "Native human intervention pauses the SSE run until a resume input arrives.",
  },
  {
    native: "question_classifier",
    dify: "question-classifier",
    note: "Native classifier uses keyword rules first; Dify can extend this with model-based classification.",
  },
  {
    native: "agent",
    dify: "agent",
    note: "Native Agent supports ReAct tool loop and direct answer modes.",
  },
  {
    native: "mcp_tool",
    dify: "tool",
    note: "Native MCP tool calls one registered MCP tool from the global tool registry.",
  },
  {
    native: "time_tool",
    dify: "time",
    note: "Native time tool provides current time without external dependencies.",
  },
  {
    native: "http_request",
    dify: "http-request",
    note: "Native HTTP request starts with GET/POST text responses and a safety switch.",
  },
  {
    native: "list_operation",
    dify: "list-operator",
    note: "Native list operation works on comma-separated strings before a richer array type exists.",
  },
  {
    native: "iteration",
    dify: "iteration",
    note: "Native iteration is local item templating, not a nested DAG loop yet.",
  },
  {
    native: "output",
    dify: "end",
    note: "Native output returns a selected variable; Dify end/answer terminates a flow.",
  },
];

export interface NativeWorkflowDefinition
  extends Omit<WorkflowDefinition, "nodes" | "edges"> {
  version: string;
  source: "workflow-native" | "classic" | "dify-import";
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface NativeValidationIssue {
  code: string;
  message: string;
  severity: "error" | "warning";
  node_id?: string;
  edge_id?: string;
}

export interface NativeValidateResponse {
  valid: boolean;
  issues: NativeValidationIssue[];
  order: string[];
  node_count: number;
  edge_count: number;
}

export interface NativeValidateRequest {
  workflow: NativeWorkflowDefinition;
}
