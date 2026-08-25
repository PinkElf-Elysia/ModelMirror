export type CaseId = "success" | "task_error" | "long_running_cancel";
export type Phase = "queued" | "running" | "terminal";
export type Outcome = "success" | "task_error" | "cancelled" | "infrastructure_error";
export type EvidenceState = "pending" | "synced" | "failed";
export type Readiness = "ready" | "not_ready";

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
}

export interface ModuleInfo {
  moduleId: string;
  moduleVersion: string;
  apiVersion: string;
  workerProtocolVersion: number;
  claimLevel: "harness_only";
  packStatus: "fixture_only";
  fixtures: CaseId[];
  runtimes: Record<string, string>;
  capabilities: Record<string, boolean>;
  links: { mlflow: string; inspectView: string };
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
