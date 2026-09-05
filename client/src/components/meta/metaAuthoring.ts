import { type WorkflowNodeKind } from "../../types/workflow";

export interface AuthoringDiagnostic {
  code?: string;
  message: string;
  severity?: "error" | "warning" | "info";
  path?: string;
}

export interface GraphPatchOperationV1 {
  op: string;
  [key: string]: unknown;
}

export interface GraphPatchEnvelopeV1 {
  protocol_version: 1;
  proposal_revision: number;
  expected_graph_checksum: string;
  expected_candidate_checksum: string;
  operations: GraphPatchOperationV1[];
}

export interface HeadlessAuthoringCompatibility {
  source_version?: 2 | 3;
  upgraded?: boolean;
  lossy?: boolean;
  warnings?: string[];
}

export interface HeadlessAuthoringProposalState {
  proposal_id: string;
  proposal_revision: number;
  authoring_protocol_version: string | number;
  ir_version: 2 | 3;
  can_author: boolean;
  graph_checksum: string;
  candidate_checksum: string;
  allowed_node_kinds: WorkflowNodeKind[];
  allowed_middleware_ids: string[];
  allowed_source_agent_ids: string[];
  allowed_knowledge_base_ids: string[];
  allowed_data_table_ids: string[];
  compiler_managed_node_kinds: WorkflowNodeKind[];
  compatibility: HeadlessAuthoringCompatibility;
  diagnostics: AuthoringDiagnostic[];
}

export interface GraphPatchPreview {
  preview_checksum: string;
  can_apply: boolean;
  diagnostics: AuthoringDiagnostic[];
  warnings: string[];
  diff: Record<string, unknown>;
  graph_ir_checksum?: string;
  candidate_checksum?: string;
  resource_snapshots: SafeResourceSnapshot[];
}

export interface SafeResourceSnapshot {
  node_ref: string;
  node_kind?: string;
  resource_id: string;
  resource_kind?: string;
  version_policy?: string;
  observed_version_id?: string;
  resolved_version_id?: string;
  schema_version?: number;
  resource_checksum?: string;
  schema_checksum?: string;
  warnings: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function numberValue(value: unknown, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function stringArray(value: unknown) {
  return Array.isArray(value)
    ? value
        .filter((item): item is string => typeof item === "string")
        .slice(0, 20)
    : [];
}

function optionalNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function abbreviatedChecksum(value: unknown) {
  const checksum = stringValue(value);
  return /^[a-f0-9]{16,}$/i.test(checksum) ? checksum.slice(0, 12) : undefined;
}

function normalizeSafeResourceSnapshot(
  value: unknown,
  node?: Record<string, unknown>,
): SafeResourceSnapshot | null {
  if (!isRecord(value)) return null;
  const resourceRef = isRecord(node?.resource_ref) ? node?.resource_ref : {};
  const resourceId = stringValue(
    value.resource_id ?? value.id ?? resourceRef.resource_id,
  );
  const nodeRef = stringValue(
    value.node_ref ?? value.ref ?? node?.ref,
  );
  if (!resourceId || !nodeRef) return null;
  return {
    node_ref: nodeRef.slice(0, 64),
    node_kind: stringValue(value.node_kind ?? node?.kind).slice(0, 80) || undefined,
    resource_id: resourceId.slice(0, 200),
    resource_kind: stringValue(value.resource_kind ?? value.kind) || undefined,
    version_policy:
      stringValue(value.version_policy ?? value.resolution_policy) || undefined,
    observed_version_id:
      stringValue(value.observed_version_id ?? value.observed_version) || undefined,
    resolved_version_id:
      stringValue(value.resolved_version_id ?? value.version_id) || undefined,
    schema_version: optionalNumber(
      value.schema_version ?? value.pinned_schema_version,
    ),
    resource_checksum: abbreviatedChecksum(
      value.resource_checksum ?? value.snapshot_checksum,
    ),
    schema_checksum: abbreviatedChecksum(value.schema_checksum),
    warnings: stringArray(value.warnings),
  };
}

export function normalizeSafeResourceSnapshots(value: unknown): SafeResourceSnapshot[] {
  if (!isRecord(value)) return [];
  const graph = isRecord(value.graph_ir) ? value.graph_ir : value;
  const direct = Array.isArray(graph.resource_snapshots)
    ? graph.resource_snapshots
    : [];
  const fromDirect = direct.flatMap((item) => {
    const normalized = normalizeSafeResourceSnapshot(item);
    return normalized ? [normalized] : [];
  });
  const fromNodes = Array.isArray(graph.nodes)
    ? graph.nodes.flatMap((item) => {
        if (!isRecord(item)) return [];
        const snapshot = normalizeSafeResourceSnapshot(item.resource_snapshot, item);
        if (snapshot) return [snapshot];
        if (!isRecord(item.resource_ref)) return [];
        const unresolved = normalizeSafeResourceSnapshot(
          { ...item.resource_ref, node_ref: item.ref, node_kind: item.kind },
          item,
        );
        return unresolved ? [unresolved] : [];
      })
    : [];
  const unique = new Map<string, SafeResourceSnapshot>();
  [...fromDirect, ...fromNodes].slice(0, 64).forEach((item) => {
    unique.set(`${item.node_ref}:${item.resource_id}`, item);
  });
  return [...unique.values()];
}

export function normalizeAuthoringDiagnostics(value: unknown): AuthoringDiagnostic[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string") return [{ message: item }];
    if (!isRecord(item) || typeof item.message !== "string") return [];
    const severity = ["error", "warning", "info"].includes(String(item.severity))
      ? (item.severity as AuthoringDiagnostic["severity"])
      : undefined;
    return [{
      code: stringValue(item.code) || undefined,
      message: item.message,
      severity,
      path: stringValue(item.path) || undefined,
    }];
  });
}

export function normalizeHeadlessProposalState(
  payload: unknown,
): HeadlessAuthoringProposalState | null {
  if (!isRecord(payload)) return null;
  const compatibility = isRecord(payload.compatibility) ? payload.compatibility : {};
  const scope = isRecord(payload.scope) ? payload.scope : {};
  const authorizedScope = isRecord(payload.authorized_scope)
    ? payload.authorized_scope
    : scope;
  const graphChecksum = stringValue(
    payload.graph_checksum ?? payload.graph_ir_checksum,
  );
  const candidateChecksum = stringValue(
    payload.candidate_checksum ??
      payload.compiled_workflow_checksum ??
      payload.compiled_candidate_checksum ??
      payload.authoring_candidate_checksum,
  );
  const requestedIrVersion = numberValue(payload.ir_version);
  const irVersion = requestedIrVersion === 2 || requestedIrVersion === 3
    ? requestedIrVersion
    : payload.graph_ir && payload.authoring_protocol_version != null
      ? 3
      : 0;
  const proposalRevision = numberValue(
    payload.proposal_revision ?? payload.revision,
  );
  const allowedKinds = stringArray(
    payload.allowed_node_kinds ?? authorizedScope.allowed_node_kinds,
  ) as WorkflowNodeKind[];
  const lossy = compatibility.lossy === true;
  const explicitCanAuthor =
    payload.can_author ?? payload.can_edit ?? payload.can_apply ?? payload.headless_apply_allowed;
  const protocol = payload.authoring_protocol_version ?? payload.protocol_version;
  if (
    typeof payload.proposal_id !== "string" ||
    proposalRevision < 1 ||
    (irVersion !== 2 && irVersion !== 3) ||
    protocol == null
  ) {
    return null;
  }
  return {
    proposal_id: payload.proposal_id,
    proposal_revision: proposalRevision,
    authoring_protocol_version:
      typeof protocol === "string" || typeof protocol === "number" ? protocol : 1,
    ir_version: irVersion,
    can_author:
      typeof explicitCanAuthor === "boolean"
        ? explicitCanAuthor
        : irVersion === 3 && !lossy,
    graph_checksum: graphChecksum,
    candidate_checksum: candidateChecksum,
    allowed_node_kinds: allowedKinds,
    allowed_middleware_ids: stringArray(authorizedScope.middleware_ids),
    allowed_source_agent_ids: stringArray(authorizedScope.agent_ids),
    allowed_knowledge_base_ids: stringArray(authorizedScope.knowledge_base_ids),
    allowed_data_table_ids: stringArray(authorizedScope.data_table_ids),
    compiler_managed_node_kinds: (
      stringArray(payload.compiler_managed_node_kinds).length
        ? stringArray(payload.compiler_managed_node_kinds)
        : ["input", "output"]
    ) as WorkflowNodeKind[],
    compatibility: {
      source_version:
        compatibility.source_version === 2 || compatibility.source_version === 3
          ? compatibility.source_version
          : undefined,
      upgraded: compatibility.upgraded === true,
      lossy,
      warnings: stringArray(compatibility.warnings),
    },
    diagnostics: normalizeAuthoringDiagnostics(payload.diagnostics),
  };
}

export function normalizeGraphPatchEnvelope(
  payload: unknown,
): GraphPatchEnvelopeV1 | null {
  if (!isRecord(payload)) return null;
  const source = isRecord(payload.patch)
    ? payload.patch
    : isRecord(payload.envelope)
      ? payload.envelope
      : payload;
  if (
    source.protocol_version != null &&
    numberValue(source.protocol_version) !== 1
  ) {
    return null;
  }
  if (!Array.isArray(source.operations)) return null;
  const operations = source.operations;
  if (
    operations.some(
      (item) => !isRecord(item) || typeof item.op !== "string" || !item.op.trim(),
    )
  ) {
    return null;
  }
  const revision = numberValue(source.proposal_revision);
  const graphChecksum = stringValue(
    source.expected_graph_checksum ?? source.graph_checksum,
  );
  const candidateChecksum = stringValue(
    source.expected_candidate_checksum ??
      source.expected_compiled_workflow_checksum ??
      source.compiled_workflow_checksum ??
      source.compiled_candidate_checksum,
  );
  if (
    revision < 1 ||
    operations.length > 64 ||
    !graphChecksum ||
    !candidateChecksum
  ) {
    return null;
  }
  return {
    protocol_version: 1,
    proposal_revision: revision,
    expected_graph_checksum: graphChecksum,
    expected_candidate_checksum: candidateChecksum,
    operations: operations as GraphPatchOperationV1[],
  };
}

export function canUseTypedHeadlessAuthoring(
  state: HeadlessAuthoringProposalState,
) {
  const losslessV2Upgrade =
    state.ir_version === 2 && state.compatibility.upgraded === true;
  return (
    state.can_author &&
    !state.compatibility.lossy &&
    (state.ir_version === 3 || losslessV2Upgrade) &&
    Boolean(state.graph_checksum) &&
    Boolean(state.candidate_checksum)
  );
}

export function headlessStateMode(
  state: HeadlessAuthoringProposalState,
): "headless" | "unavailable" {
  return canUseTypedHeadlessAuthoring(state) ? "headless" : "unavailable";
}

export function normalizeGraphPatchPreview(payload: unknown): GraphPatchPreview | null {
  if (!isRecord(payload) || typeof payload.preview_checksum !== "string") return null;
  return {
    preview_checksum: payload.preview_checksum,
    can_apply: payload.can_apply === true,
    diagnostics: normalizeAuthoringDiagnostics(payload.diagnostics),
    warnings: stringArray(payload.warnings),
    diff: isRecord(payload.diff) ? payload.diff : {},
    graph_ir_checksum:
      stringValue(payload.graph_ir_checksum ?? payload.graph_checksum) || undefined,
    candidate_checksum:
      stringValue(
        payload.candidate_checksum ??
          payload.compiled_workflow_checksum ??
          payload.compiled_candidate_checksum,
      ) || undefined,
    resource_snapshots: normalizeSafeResourceSnapshots(payload),
  };
}

export function buildMetadataPatch(
  state: HeadlessAuthoringProposalState,
  metadata: {
    name: string;
    description: string;
    tags: string[];
    starters: string[];
  },
): GraphPatchEnvelopeV1 {
  return {
    protocol_version: 1,
    proposal_revision: state.proposal_revision,
    expected_graph_checksum: state.graph_checksum,
    expected_candidate_checksum: state.candidate_checksum,
    operations: [{ op: "set_xpert_metadata", ...metadata }],
  };
}

export function authoringDiffSummary(diff: Record<string, unknown>): string[] {
  const preferredKeys = [
    "summary",
    "operation_count",
    "operation_types",
    "graph_changed",
    "candidate_changed",
    "nodes_added",
    "nodes_removed",
    "nodes_updated",
    "edges_added",
    "edges_removed",
    "bindings_changed",
    "node_resources_changed",
    "resource_refs_changed",
    "resource_snapshots_changed",
    "error_outcomes_changed",
    "metadata_changed",
    "layout_changed",
  ];
  const lines: string[] = [];
  for (const key of preferredKeys) {
    const value = diff[key];
    if (typeof value === "string" && value.trim()) lines.push(value.trim());
    else if (typeof value === "number" && value > 0) lines.push(`${key}: ${value}`);
    else if (Array.isArray(value) && value.length > 0) lines.push(`${key}: ${value.length}`);
    else if (isRecord(value) && Object.keys(value).length > 0) {
      lines.push(`${key}: ${Object.keys(value).length}`);
    }
    else if (value === true) lines.push(key);
  }
  return lines.length ? lines : ["服务端未返回可展示的结构差异摘要。"];
}

export function authoringOperationSummary(operation: GraphPatchOperationV1): string {
  const op = stringValue(operation.op) || "unknown";
  if (op === "set_node_resource" || op === "clear_node_resource") {
    const nodeRef = stringValue(operation.node_ref ?? operation.ref);
    const resourceId = stringValue(operation.resource_id);
    return [op, nodeRef, resourceId ? `resource ${resourceId}` : ""]
      .filter(Boolean)
      .join(" · ");
  }
  if (op === "connect_control" || op === "disconnect_control") {
    const source = stringValue(operation.source_ref);
    const outcome = stringValue(operation.outcome_ref) || "success";
    const target = stringValue(operation.target_ref);
    return `${op} · ${source}:${outcome} -> ${target}`;
  }
  return op;
}
