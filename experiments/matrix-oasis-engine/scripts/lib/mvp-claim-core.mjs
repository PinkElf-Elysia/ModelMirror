import { readFileSync } from "node:fs";
import path from "node:path";

const PUBLIC_STATUS_DOCUMENTS = Object.freeze([
  "README.md",
  "docs/PRODUCT.md",
  "docs/V1_CRITICAL_PATH.md",
  "docs/ARCHITECTURE.md",
  "docs/KNOWN_LIMITATIONS.md",
]);

const PREMATURE_CLAIM_PATTERNS = Object.freeze([
  /R10通过即定义初版完成/u,
  /R10以Marble panorama与collider、Meshy道具\/静态人物、确定性Scene Pack和Creator本地宿主完成初版闭环/u,
  /初版(?:闭环)?(?:已经|已|现已)(?:完成|通过)/u,
  /(?:MATRIX_OASIS_)?MVP_(?:COMPLETE|READY)/u,
]);

export class MvpClaimError extends Error {
  constructor(code) {
    super(code);
    this.name = "MvpClaimError";
    this.code = code;
  }
}

function readUtf8(moduleRoot, relativePath) {
  return readFileSync(path.join(moduleRoot, ...relativePath.split("/")), "utf8");
}

export function checkMvpClaim({ moduleRoot }) {
  let policy;
  let status;
  try {
    policy = JSON.parse(readUtf8(moduleRoot, "module-boundary.json"));
    status = JSON.parse(readUtf8(moduleRoot, "docs/MVP_STATUS.json"));
  } catch {
    throw new MvpClaimError("MVP_CLAIM_POLICY_INVALID");
  }

  const claimPolicy = policy.mvpClaimPolicy;
  if (
    policy.activeRound !== "R16" ||
    claimPolicy?.blockingRound !== "R16" ||
    claimPolicy?.acceptanceRecord !== "docs/rounds/R15_ACCEPTANCE.md" ||
    claimPolicy?.machineStatus !== "docs/MVP_STATUS.json" ||
    claimPolicy?.completionMarker !== "MATRIX_OASIS_R12_MVP_READY"
  ) {
    throw new MvpClaimError("MVP_CLAIM_POLICY_INVALID");
  }

  if (
    status.schemaVersion !== 1 ||
    status.blockingRound !== "R16" ||
    status.acceptanceRecord !== claimPolicy.acceptanceRecord ||
    status.completionMarker !== claimPolicy.completionMarker ||
    status.status !== claimPolicy.status ||
    status.claimAllowed !== claimPolicy.claimAllowed
  ) {
    throw new MvpClaimError("MVP_CLAIM_STATUS_MISMATCH");
  }

  const acceptance = readUtf8(moduleRoot, claimPolicy.acceptanceRecord);
  if (!claimPolicy.claimAllowed) {
    if (
      claimPolicy.status !== "pending-creator-migration" ||
      !acceptance.includes("状态：R15验收通过；等待R16 Creator迁移")
    ) {
      throw new MvpClaimError("MVP_CLAIM_PREMATURE");
    }
    for (const relativePath of PUBLIC_STATUS_DOCUMENTS) {
      const document = readUtf8(moduleRoot, relativePath);
      if (PREMATURE_CLAIM_PATTERNS.some((pattern) => pattern.test(document))) {
        throw new MvpClaimError("MVP_CLAIM_PREMATURE");
      }
    }
  } else {
    throw new MvpClaimError("MVP_CLAIM_EVIDENCE_MISSING");
  }

  return Object.freeze({
    status: claimPolicy.status,
    claimAllowed: claimPolicy.claimAllowed,
    checkedDocuments: PUBLIC_STATUS_DOCUMENTS.length,
  });
}
