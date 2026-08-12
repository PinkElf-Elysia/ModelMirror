export type SkillTrustRiskLevel = "low" | "medium" | "high" | "critical";
export type SkillTrustStatus = "verified" | "conditional" | "blocked";
export type SkillTrustInstallPolicy = "allow" | "confirm" | "block";
export type SkillCompatibilityStatus = "portable" | "conditional" | "unsupported";
export type SkillTrustGateMode = "off" | "audit" | "enforce";

export interface SkillTrustReceiptSummary {
  receiptId: string;
  trustFingerprint: string;
  riskLevel: SkillTrustRiskLevel;
  trustStatus: SkillTrustStatus;
  installPolicy: SkillTrustInstallPolicy;
  compatibilityStatus: SkillCompatibilityStatus;
  routerEligible: boolean;
  summary: {
    fileCount: number;
    totalBytes: number;
    textFileCount: number;
    scriptCount: number;
    opaqueResourceCount: number;
  };
}

export interface SkillTrustFinding {
  code: string;
  severity: "info" | "warning" | "error" | "critical" | string;
  message: string;
  path?: string | null;
  line?: number | null;
}

export interface SkillTrustReceipt extends SkillTrustReceiptSummary {
  source:
    | {
        kind?: "catalog_git";
        repoUrl: string;
        subPath: string;
        verifiedCommit: string;
      }
    | {
        kind: "local_import";
        importId: string;
        importRevision: number;
        transportKind: "zip" | "folder";
        transportDigest: string;
      };
  directoryTreeSha?: string;
  contentTreeDigest?: string;
  packageDigest: string;
  scannerVersion: string;
  scripts: Array<{ path: string; language?: string; executable?: boolean }>;
  opaqueResources: Array<{ path: string; kind?: string; size?: number }>;
  license: string | null;
  allowedTools: string[];
  dependencies: string[];
  commands: string[];
  capabilities: Record<string, boolean>;
  findings: SkillTrustFinding[];
}

export interface InstalledSkillTrustFields {
  trust_state: string;
  trust_receipt_id?: string | null;
  trust_fingerprint?: string | null;
  trust_risk_level?: SkillTrustRiskLevel | null;
  trust_status?: SkillTrustStatus | "unknown" | null;
  trust_install_policy?: SkillTrustInstallPolicy | null;
  trust_compatibility_status?: SkillCompatibilityStatus | null;
  trust_package_digest?: string | null;
  trust_directory_tree_sha?: string | null;
  trust_verified_at?: number | null;
  trust_router_eligible: boolean;
  trust_activation_status: "ready" | "ack_required" | "blocked" | "not_applicable";
  trust_activation_allowed: boolean;
  trust_acknowledgement_required: boolean;
  trust_acknowledgement_satisfied: boolean;
  trust_reason_codes: string[];
}

export interface SkillTrustSummaryIndex {
  gateMode: SkillTrustGateMode;
  version: 1;
  scannerVersion: string;
  catalogFingerprint: string;
  trustIndexFingerprint: string;
  candidateReceipts: Record<string, string>;
  receipts: SkillTrustReceiptSummary[];
  fingerprint: string;
  sourceReceipts?: Record<string, string>;
}

let skillTrustIndexPromise: Promise<SkillTrustSummaryIndex> | undefined;
const skillTrustReceiptPromises = new Map<string, Promise<SkillTrustReceipt>>();

export async function readSkillTrustApiError(response: Response) {
  const fallback = `请求失败：${response.status}`;
  try {
    const payload = (await response.json()) as {
      detail?: string | { code?: string; message?: string };
      error?: string;
    };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
    if (payload.detail && typeof payload.detail === "object") {
      return payload.detail.message || payload.detail.code || fallback;
    }
    return payload.error || fallback;
  } catch {
    return fallback;
  }
}

/** Load third-party trust summaries only after a Skill experience requests them. */
export function loadSkillTrustSummaryIndex(force = false) {
  if (force) {
    skillTrustIndexPromise = undefined;
    skillTrustReceiptPromises.clear();
  }
  if (skillTrustIndexPromise) return skillTrustIndexPromise;
  const request = fetch("/api/skills/trust-index").then(async (response) => {
    if (!response.ok) throw new Error(await readSkillTrustApiError(response));
    const payload = (await response.json()) as {
      gateMode?: SkillTrustGateMode;
      index?: Omit<SkillTrustSummaryIndex, "gateMode"> | null;
      sourceReceipts?: Record<string, string>;
    };
    const gateMode = payload.gateMode ?? "enforce";
    if (!payload.index && gateMode !== "enforce") {
      return {
        gateMode,
        version: 1 as const,
        scannerVersion: "unavailable",
        catalogFingerprint: "",
        trustIndexFingerprint: "",
        candidateReceipts: {},
        receipts: [],
        fingerprint: "",
        sourceReceipts: {},
      };
    }
    if (
      !payload.index ||
      payload.index.version !== 1 ||
      !payload.index.fingerprint ||
      !payload.index.trustIndexFingerprint
    ) {
      throw new Error("Skill 信任摘要索引无效，本次不会提供安装操作。");
    }
    return {
      ...payload.index,
      gateMode,
      sourceReceipts: payload.sourceReceipts ?? {},
    };
  });
  const cachedRequest = request.catch((error) => {
    if (skillTrustIndexPromise === cachedRequest) skillTrustIndexPromise = undefined;
    throw error;
  });
  skillTrustIndexPromise = cachedRequest;
  return skillTrustIndexPromise;
}

export function loadSkillTrustReceipt(receiptId: string) {
  let request = skillTrustReceiptPromises.get(receiptId);
  if (!request) {
    const fetchRequest = fetch(`/api/skills/trust/${encodeURIComponent(receiptId)}`).then(
      async (response) => {
        if (!response.ok) throw new Error(await readSkillTrustApiError(response));
        const payload = (await response.json()) as { receipt?: SkillTrustReceipt };
        if (!payload.receipt || payload.receipt.receiptId !== receiptId) {
          throw new Error("Skill 信任凭据与目录不一致，请刷新后重试。");
        }
        return payload.receipt;
      },
    );
    request = fetchRequest.catch((error) => {
      if (skillTrustReceiptPromises.get(receiptId) === request) {
        skillTrustReceiptPromises.delete(receiptId);
      }
      throw error;
    });
    skillTrustReceiptPromises.set(receiptId, request);
  }
  return request;
}

export function trustSummaryForCandidate(
  index: SkillTrustSummaryIndex | null,
  candidateId: string,
) {
  if (!index) return null;
  const receiptId = index.candidateReceipts[candidateId];
  return receiptId
    ? index.receipts.find((receipt) => receipt.receiptId === receiptId) ?? null
    : null;
}

export const projectTrustCandidateId = (projectId: string) =>
  `catalog:project:${projectId}`;

export const memberTrustCandidateId = (memberId: string) =>
  `catalog:member:${memberId}`;

export function trustSummaryForSource(
  index: SkillTrustSummaryIndex | null,
  source: { repoUrl: string; subPath: string; verifiedCommit: string },
) {
  if (!index?.sourceReceipts) return null;
  const key = [
    source.repoUrl.trim().replace(/\.git$/i, "").toLocaleLowerCase("en"),
    source.subPath.trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, ""),
    source.verifiedCommit.trim().toLocaleLowerCase("en"),
  ].join("#");
  const receiptId = index.sourceReceipts[key];
  return receiptId
    ? index.receipts.find((receipt) => receipt.receiptId === receiptId) ?? null
    : null;
}

export function effectiveTrustInstallPolicy(
  gateMode: SkillTrustGateMode,
  summary: SkillTrustReceiptSummary | null,
): SkillTrustInstallPolicy {
  if (gateMode === "off" || gateMode === "audit") return "allow";
  return summary?.installPolicy ?? "block";
}
