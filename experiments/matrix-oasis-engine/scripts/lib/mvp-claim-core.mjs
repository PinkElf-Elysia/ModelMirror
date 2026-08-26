import { readFileSync } from "node:fs";
import path from "node:path";

const PUBLIC_STATUS_DOCUMENTS = Object.freeze([
  "README.md",
  "docs/PRODUCT.md",
  "docs/V1_CRITICAL_PATH.md",
  "docs/ARCHITECTURE.md",
  "docs/KNOWN_LIMITATIONS.md",
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
    !/^R(?:1[6-9]|[2-9][0-9]+)$/.test(policy.activeRound) ||
    claimPolicy?.blockingRound !== null ||
    claimPolicy?.acceptanceRecord !== "docs/rounds/R16_ACCEPTANCE.md" ||
    claimPolicy?.machineStatus !== "docs/MVP_STATUS.json" ||
    claimPolicy?.completionMarker !== "MATRIX_OASIS_R16_CREATOR_MVP_READY"
  ) {
    throw new MvpClaimError("MVP_CLAIM_POLICY_INVALID");
  }

  if (
    status.schemaVersion !== 1 ||
    status.blockingRound !== null ||
    status.acceptanceRecord !== claimPolicy.acceptanceRecord ||
    status.completionMarker !== claimPolicy.completionMarker ||
    status.status !== claimPolicy.status ||
    status.claimAllowed !== claimPolicy.claimAllowed
  ) {
    throw new MvpClaimError("MVP_CLAIM_STATUS_MISMATCH");
  }

  const acceptance = readUtf8(moduleRoot, claimPolicy.acceptanceRecord);
  if (!claimPolicy.claimAllowed || claimPolicy.status !== "r16-qualified") {
    throw new MvpClaimError("MVP_CLAIM_PREMATURE");
  }
  for (const marker of [
    "状态：R16双真实案例人工验收通过；MVP声明门解除",
    "60b63d9a3bd8d36592314ad6c444e8873edd189071a6f5e13664881e8f6c96ad",
    "fda3dc97079ec02a40f1c0e5df48897e07996b60a8e9a10d57c363d40570c572",
    "供应商请求数为零",
  ]) {
    if (!acceptance.includes(marker)) throw new MvpClaimError("MVP_CLAIM_EVIDENCE_MISSING");
  }
  const readme = readUtf8(moduleRoot, "README.md");
  const product = readUtf8(moduleRoot, "docs/PRODUCT.md");
  const criticalPath = readUtf8(moduleRoot, "docs/V1_CRITICAL_PATH.md");
  if (
    !readme.includes(claimPolicy.completionMarker) ||
    !product.includes("初版闭环完成") ||
    !criticalPath.includes("R16双真实案例人工验收通过")
  ) {
    throw new MvpClaimError("MVP_CLAIM_EVIDENCE_MISSING");
  }

  return Object.freeze({
    status: claimPolicy.status,
    claimAllowed: claimPolicy.claimAllowed,
    checkedDocuments: PUBLIC_STATUS_DOCUMENTS.length,
  });
}
