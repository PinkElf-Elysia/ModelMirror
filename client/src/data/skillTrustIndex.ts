export type SkillTrustRiskLevel = "low" | "medium" | "high" | "critical";
export type SkillTrustStatus = "verified" | "conditional" | "blocked";
export type SkillTrustInstallPolicy = "allow" | "confirm" | "block";
export type SkillCompatibilityStatus = "portable" | "conditional" | "unsupported";

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

export interface SkillTrustSummaryIndex {
  version: 1;
  scannerVersion: string;
  catalogFingerprint: string;
  trustIndexFingerprint: string;
  candidateReceipts: Record<string, string>;
  receipts: SkillTrustReceiptSummary[];
  fingerprint: string;
}

let skillTrustIndexPromise: Promise<SkillTrustSummaryIndex> | undefined;

/** Load third-party trust summaries only after a Skill experience requests them. */
export function loadSkillTrustSummaryIndex() {
  skillTrustIndexPromise ??= import("./skillTrustIndex.generated.json").then(
    (module) => module.default as SkillTrustSummaryIndex,
  );
  return skillTrustIndexPromise;
}
