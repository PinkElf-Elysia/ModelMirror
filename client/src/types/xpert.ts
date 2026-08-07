import {
  type WorkflowEdge,
  type WorkflowNodeData,
  type WorkflowNodeKind,
} from "./workflow";

export type XpertStatus = "draft" | "published" | "archived";

export interface PromptProfileBinding {
  profile_id: string;
  version_policy: "latest" | "pinned";
  pinned_version: number | null;
  enabled: boolean;
}

export interface ResolvedPromptProfile {
  profile_id: string;
  slug: string;
  version: number;
  name: string;
  description: string;
  aliases: string[];
  argument_hint: string;
  public_app_allowed: boolean;
  source: "direct" | "plugin";
  source_plugin_id?: string | null;
  source_plugin_version?: number | null;
}

export interface XpertFeatureConfig {
  opening: {
    enabled: boolean;
    message: string;
    questions: string[];
  };
  generated_questions: {
    enabled: boolean;
    model_id: string;
    count: number;
  };
  conversation_title: {
    enabled: boolean;
    model_id: string;
  };
  conversation_summary: {
    enabled: boolean;
    model_id: string;
    max_context_chars: number;
    trigger_ratio: number;
    keep_recent_messages: number;
    max_summary_chars: number;
  };
  memory_reply: {
    enabled: boolean;
    min_confidence: number;
  };
  file_upload: {
    enabled: boolean;
    max_files_per_run: number;
    allowed_extensions: string[];
  };
  text_to_speech: {
    enabled: boolean;
    model_id: string;
    voice: string;
    max_text_chars: number;
  };
  speech_to_text: {
    enabled: boolean;
    model_id: string;
  };
}

export interface XpertWorkflowDefinition {
  id: string;
  title: string;
  nodes: Array<{
    id: string;
    type?: WorkflowNodeKind | string | null;
    position?: { x: number; y: number } | null;
    data: WorkflowNodeData;
  }>;
  edges: WorkflowEdge[];
  source?: string;
  version?: string;
}

export interface XpertDraft {
  workflow: XpertWorkflowDefinition;
  input_variable: string;
  history_variable: string;
  output_variable: string;
  agent_config: {
    max_concurrency: number;
    recursion_limit: number;
  };
  features: XpertFeatureConfig;
  prompt_profiles: PromptProfileBinding[];
}

export interface XpertVersion {
  version: number;
  draft_revision: number;
  workflow: XpertWorkflowDefinition;
  input_variable: string;
  history_variable: string;
  output_variable: string;
  agent_config?: {
    max_concurrency: number;
    recursion_limit: number;
  } | null;
  features?: XpertFeatureConfig | null;
  prompt_profiles?: ResolvedPromptProfile[];
  release_notes: string;
  checksum: string;
  published_at: number;
}

export interface XpertDefinition {
  id: string;
  slug: string;
  name: string;
  description: string;
  tags: string[];
  starters: string[];
  status: XpertStatus;
  draft_revision: number;
  published_version: number | null;
  draft: XpertDraft;
  versions: XpertVersion[];
  created_at: number;
  updated_at: number;
}

export interface XpertSummary {
  id: string;
  slug: string;
  name: string;
  description: string;
  tags: string[];
  starters: string[];
  status: XpertStatus;
  draft_revision: number;
  published_version: number | null;
  version_count: number;
  created_at: number;
  updated_at: number;
}

export interface XpertValidationIssue {
  code: string;
  message: string;
  severity: "error" | "warning";
  node_id?: string | null;
  edge_id?: string | null;
}

export interface XpertValidationResult {
  valid: boolean;
  issues: XpertValidationIssue[];
  order: string[];
  node_count: number;
  edge_count: number;
}

export interface XpertListResponse {
  version: string;
  items: XpertSummary[];
  total: number;
}

export interface XpertConversationMessage {
  message_id?: string;
  role: "user" | "assistant";
  content: string;
  suggestions?: string[];
  version?: number | null;
  source_task_id?: string | null;
  source_run_id?: string | null;
  created_at?: number;
}

export interface XpertConversation {
  conversation_id: string;
  xpert_id: string;
  title: string;
  messages?: XpertConversationMessage[];
  message_count?: number;
  summary?: string;
  summary_through_message_id?: string | null;
  summary_revision?: number;
  summary_model_id?: string | null;
  summary_updated_at?: number | null;
  file_asset_ids: string[];
  archived: boolean;
  created_at: number;
  updated_at: number;
}

export interface XpertFileAsset {
  asset_id: string;
  artifact_id: string;
  xpert_id: string;
  conversation_id: string;
  filename: string;
  size_bytes: number;
  extension: string;
  mime_type: string;
  status: "ready" | "archived";
  character_count: number;
  extracted_truncated: boolean;
  created_at: number;
  archived_at: number | null;
}

export type XpertFileMemoryType = "user" | "feedback" | "project" | "reference";

export interface XpertMemoryUsage {
  recall_count: number;
  detail_read_count: number;
  explicit_write_count: number;
  candidate_count: number;
  correction_count: number;
  usefulness_score: number;
  last_recalled_at: number | null;
  last_detail_read_at: number | null;
}

export interface XpertMemoryRecord {
  memory_id: string;
  xpert_id: string;
  scope: "conversation" | "xpert";
  conversation_id: string | null;
  content: string;
  tags: string[];
  source_type: string;
  source_id: string | null;
  status: "active" | "archived";
  created_at: number;
  updated_at: number;
  type?: XpertFileMemoryType;
  memory_type?: XpertFileMemoryType;
  title?: string;
  summary?: string;
  revision?: number;
  canonical_ref?: string;
  source_refs?: string[];
  confidence?: number | null;
  usage?: XpertMemoryUsage;
}

export interface XpertMemoryCandidate {
  candidate_id: string;
  xpert_id: string;
  scope: "conversation" | "xpert";
  conversation_id: string | null;
  content: string;
  tags: string[];
  source_run_id: string | null;
  status: "pending" | "approved" | "rejected" | "conflict";
  created_at: number;
  decided_at: number | null;
  memory_id: string | null;
  revision?: number;
  action?: "create" | "update";
  type?: XpertFileMemoryType;
  memory_type?: XpertFileMemoryType;
  title?: string;
  summary?: string;
  target_memory_id?: string | null;
  base_revision?: number | null;
  source_refs?: string[];
  confidence?: number | null;
  error?: string | null;
}

export interface XpertFileMemoryIndex {
  version: string;
  active_count: number;
  archived_count: number;
  type_counts: Record<XpertFileMemoryType, number>;
  signal_count: number;
  index_revision: number;
  updated_at: number | null;
  content: string;
}

export interface XpertFileMemorySignal {
  signal_id: string;
  xpert_id: string;
  memory_id: string | null;
  signal_type: string;
  conversation_id: string | null;
  query_hash: string | null;
  source_ref: string | null;
  metadata: Record<string, unknown>;
  created_at: number;
}

export interface XpertAppPolicy {
  allow_tools: boolean;
  allow_handoffs: boolean;
  allow_xpert_memory: boolean;
  allow_knowledge_read: boolean;
  allow_datax_read: boolean;
}

export interface XpertAppLimits {
  requests_per_minute: number;
  requests_per_day: number;
  max_concurrency: number;
}

export interface XpertAppDeployment {
  revision: number;
  version: number;
  release_notes: string;
  deployed_at: number;
}

export interface XpertAppApiKey {
  key_id: string;
  name: string;
  prefix: string;
  limits: XpertAppLimits;
  usage_day: string;
  requests_today: number;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
  expires_at: number | null;
}

export interface XpertAppDefinition {
  app_id: string;
  xpert_id: string;
  slug: string;
  name: string;
  description: string;
  starters: string[];
  status: "draft" | "active" | "disabled";
  visibility: "unlisted";
  pinned_version: number | null;
  deployment_revision: number;
  policy: XpertAppPolicy;
  limits: XpertAppLimits;
  share_token_prefix: string;
  share_usage_day: string;
  share_requests_today: number;
  share_last_used_at: number | null;
  api_keys: XpertAppApiKey[];
  deployments: XpertAppDeployment[];
  created_at: number;
  updated_at: number;
}

export interface XpertAppManifest {
  object: "xpert.app";
  slug: string;
  name: string;
  description: string;
  starters: string[];
  version: number;
  deployment_revision: number;
  visibility: "unlisted";
  commands?: Array<{
    alias: string;
    name: string;
    description: string;
    argument_hint: string;
  }>;
}
