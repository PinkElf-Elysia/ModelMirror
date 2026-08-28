export type CaseId = "success" | "task_error" | "long_running_cancel";
export type Phase = "queued" | "running" | "terminal";
export type Outcome = "success" | "task_error" | "cancelled" | "infrastructure_error";
export type EvidenceState = "pending" | "synced" | "failed";
export type Readiness = "ready" | "not_ready";
export type LiteraturePhase = "not_started" | "queued" | "running" | "terminal";
export type LiteratureOutcome = "completed" | "cancelled" | "failed" | "infrastructure_error";
export type IntegrityStatus = "pending" | "verified" | "failed";

export interface Run {
  runId: string;
  fixtureId: string;
  caseId: CaseId;
  tenantId: "local";
  projectId: "local";
  actorId: "local";
  phase: Phase;
  outcome: Outcome | null;
  inspectStatus: "started" | "success" | "error" | "cancelled" | null;
  cancelRequested: boolean;
  cancelApplied: boolean;
  evidenceState: EvidenceState;
  errorType: string | null;
  errorMessage: string | null;
  replayVerified: boolean;
  mlflowRunId: string | null;
  createdAt: string;
  startedAt: string | null;
  cancelRequestedAt: string | null;
  cancelAppliedAt: string | null;
  terminalAt: string | null;
  evidenceSyncedAt: string | null;
  updatedAt: string;
}

export interface RunList {
  items: Run[];
  nextCursor: string | null;
}

export interface RunSummary {
  total: number;
  phases: Record<Phase, number>;
  outcomes: Record<Outcome, number>;
  evidenceStates: Record<EvidenceState, number>;
  updatedAt: string | null;
}

export interface RunEvent {
  sequence: number;
  eventType: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface EventList {
  items: RunEvent[];
  nextSequence: number;
}

export interface SystemCheck {
  id: "controlLedger" | "worker" | "tracking" | "inspectView";
  status: Readiness;
  required: boolean;
}

export interface SystemStatus {
  status: "ready" | "degraded" | "not_ready";
  checks: SystemCheck[];
  checkedAt: string;
  literatureCapability?: {
    status: Readiness;
    serviceStatus: Readiness;
    sessionStatus: "locked" | "ready" | "expired";
    profileStatus: Readiness;
    modelBridgeStatus?: Readiness;
    username: string | null;
    scientificClaim: "none";
  };
}

export interface ModuleInfo {
  moduleId: string;
  moduleVersion: string;
  apiVersion: string;
  workerProtocolVersion: number;
  fixtures: CaseId[];
  runtimes: Record<string, string>;
  capabilities: Record<string, boolean>;
  capabilityClaims: {
    fixtureExecution: {
      enabled: true;
      claimLevel: "harness_only";
      packStatus: "fixture_only";
    };
    literatureResearch: {
      enabled: true;
      scientificClaim: "none";
      acceptanceState: "pending_live_acceptance";
      workflowSource: "local_deep_research";
    };
  };
  links: { mlflow: string; inspectView: string; localDeepResearch: string };
  limitations: string[];
}

export interface EvidenceArtifact {
  name: string;
  sizeBytes: number;
  sha256: string;
  downloadUrl: string;
}

export interface Evidence {
  runId: string;
  evidenceState: EvidenceState;
  integrityStatus: "pending" | "verified" | "failed";
  integrityError: string | null;
  verifiedAt: string;
  receipt: Record<string, unknown> | null;
  artifacts: EvidenceArtifact[];
  mlflow: Record<string, string | null>;
  outbox: Record<string, unknown> | null;
}

export interface LiteratureAttempt {
  runId: string;
  ldrResearchId: string | null;
  phase: LiteraturePhase;
  outcome: LiteratureOutcome | null;
  rawStatus: string | null;
  cancelRequestedAt: string | null;
  cancelAppliedAt: string | null;
  startedAt: string | null;
  terminalAt: string | null;
  syncedAt: string | null;
  errorType: string | null;
  errorMessage: string | null;
  integrityStatus: IntegrityStatus;
  createdAt: string;
  progress: number;
  latestLog: Record<string, unknown> | null;
}

export interface ResearchProject {
  schemaVersion: 1;
  projectId: string;
  title: string;
  researchQuestion: string;
  domain: "ai_agent";
  currentStage: "literature";
  stages: Record<string, "active" | "not_available">;
  literaturePhase: LiteraturePhase;
  literatureOutcome: LiteratureOutcome | null;
  activeRunId: string | null;
  completedRunId: string | null;
  collectionId: string | null;
  profileId: string;
  modelId: string | null;
  attempts: LiteratureAttempt[];
  createdAt: string;
  updatedAt: string;
}

export interface ProjectList {
  items: ResearchProject[];
  nextCursor: string | null;
}

export interface LiteratureSession {
  status: "locked" | "ready" | "expired";
  username: string | null;
}

export interface LiteratureSource {
  url: string;
  title: string;
  index: number | null;
}

export interface ProjectSources {
  projectId: string;
  literatureRunId: string;
  integrityStatus: "verified";
  sources: LiteratureSource[];
}

export interface ProjectReview {
  projectId: string;
  literatureRunId: string;
  integrityStatus: "verified";
  markdown: string;
}

export interface LibraryCollection {
  id: string;
  name?: string;
  description?: string | null;
  is_public?: boolean;
  agent_enabled?: boolean;
  document_count?: number;
  indexed_document_count?: number;
  embedding_model?: string | null;
}

export interface CollectionList {
  collections: LibraryCollection[];
}

export interface ZoteroStatus {
  config: {
    success?: boolean;
    enabled?: boolean;
    configured?: boolean;
    has_api_key?: boolean;
    library_type?: string;
    library_id?: string;
  };
  status: {
    success?: boolean;
    collections?: unknown[];
    progress?: unknown;
  };
}
