export type SkillLifecycleStateStatus =
  | "active"
  | "uninstalled"
  | "migration_blocked";

export interface SkillLifecycleEvent {
  event_id: string;
  kind: string;
  version_id: string | null;
  reason_code: string | null;
  actor_kind: "system_migration" | "local_console";
  created_at: number;
}

export interface SkillLifecycleState {
  skill_id: string;
  revision: number;
  status: SkillLifecycleStateStatus;
  current_version_id: string | null;
  recovery_version_id: string | null;
  protected_version_ids: string[];
  version_ids: string[];
  migration_code: string | null;
  events: SkillLifecycleEvent[];
  created_at: number;
  updated_at: number;
}

export interface SkillLifecycleVersion {
  version_id: string;
  skill_id: string;
  ordinal: number;
  package_digest: string;
  file_count: number;
  total_bytes: number;
  source_kind: "git" | "local_import" | "workspace_draft";
  source_id: string | null;
  source_revision: number | null;
  repo_url: string;
  sub_path: string;
  source_ref: string | null;
  trust_receipt_id: string | null;
  trust_fingerprint: string | null;
  trust_evidence_frozen: boolean;
  trust_risk_level: string | null;
  trust_status: string | null;
  trust_install_policy: string | null;
  trust_compatibility_status: string | null;
  trust_router_eligible: boolean;
  quality_required: boolean;
  quality_evidence_status: string;
  quality_status: string | null;
  quality_decision_id: string | null;
  quality_run_id: string | null;
  created_at: number;
}

export interface SkillLifecycleStatus {
  enabled: boolean;
  available: boolean;
  version: string;
  storeVersion: number;
  limits: {
    nonCurrentVersionsPerSkill: number;
    storageBytes: number;
    fileCount: number;
    fileBytes: number;
    packageBytes: number;
  };
  counts: {
    skills: number;
    versions: number;
    packages: number;
    quarantinedRecords: number;
    migrationBlocked: number;
  };
  storageBytes: number;
  pendingTransactions: number;
  errorCode: string | null;
}

export interface SkillLifecycleMigrationItem {
  skillId: string;
  sourceKind: string;
  outcome: "eligible" | "migrated" | "blocked" | "ignored";
  code: string | null;
  packageDigest?: string;
  lifecycleRevision?: number;
  versionId?: string | null;
}

export interface SkillLifecycleMigrationReport {
  version: string;
  applied: boolean;
  counts: {
    total: number;
    eligible: number;
    migrated: number;
    blocked: number;
    ignored: number;
  };
  items: SkillLifecycleMigrationItem[];
}

export interface SkillLifecycleStatesResponse {
  status: SkillLifecycleStatus;
  items: SkillLifecycleState[];
}

export interface SkillLifecycleVersionsResponse {
  state: SkillLifecycleState;
  versions: SkillLifecycleVersion[];
}

export class SkillLifecycleApiError extends Error {
  readonly code: string;

  constructor(message: string, code = "skill_lifecycle_request_failed") {
    super(message);
    this.name = "SkillLifecycleApiError";
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let code = "skill_lifecycle_request_failed";
    let message = `HTTP ${response.status}`;
    try {
      const payload = (await response.json()) as {
        detail?: string | { code?: string; message?: string };
      };
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (payload.detail) {
        code = payload.detail.code || code;
        message = payload.detail.message || message;
      }
    } catch {
      // Keep the bounded HTTP fallback and never expose an HTML error body.
    }
    throw new SkillLifecycleApiError(message, code);
  }
  return (await response.json()) as T;
}

export function loadSkillLifecycleStatus() {
  return request<SkillLifecycleStatus>("/api/skills/lifecycle/status");
}

export function loadSkillLifecycleStates() {
  return request<SkillLifecycleStatesResponse>("/api/skills/lifecycle/skills");
}

export function auditSkillLifecycleMigration() {
  return request<SkillLifecycleMigrationReport>("/api/skills/lifecycle/migration");
}

export function migrateSkillLifecycle() {
  return request<SkillLifecycleMigrationReport>("/api/skills/lifecycle/migration", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed: true }),
  });
}

export function loadSkillLifecycleVersions(skillId: string) {
  return request<SkillLifecycleVersionsResponse>(
    `/api/skills/${encodeURIComponent(skillId)}/versions`,
  );
}

export function rollbackSkillLifecycleVersion(options: {
  skillId: string;
  versionId: string;
  expectedStateRevision: number;
  expectedCurrentVersionId: string | null;
  expectedPackageDigest: string;
}) {
  return request<{ state: SkillLifecycleState; installed: { skill_id: string } }>(
    `/api/skills/${encodeURIComponent(options.skillId)}/versions/${encodeURIComponent(options.versionId)}/rollback`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_state_revision: options.expectedStateRevision,
        expected_current_version_id: options.expectedCurrentVersionId,
        expected_package_digest: options.expectedPackageDigest,
        confirmed: true,
      }),
    },
  );
}
