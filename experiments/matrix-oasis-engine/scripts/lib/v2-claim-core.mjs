import { readFileSync } from "node:fs";
import path from "node:path";

export class V2ClaimError extends Error {
  constructor(code) {
    super(code);
    this.name = "V2ClaimError";
    this.code = code;
  }
}

export function checkV2Claim({ moduleRoot }) {
  let boundary;
  let status;
  try {
    boundary = JSON.parse(readFileSync(path.join(moduleRoot, "module-boundary.json"), "utf8"));
    status = JSON.parse(readFileSync(path.join(moduleRoot, "docs", "V2_STATUS.json"), "utf8"));
  } catch {
    throw new V2ClaimError("V2_CLAIM_POLICY_INVALID");
  }
  const policy = boundary.v2ClaimPolicy;
  if (
    boundary.activeRound !== "R20" ||
    policy?.status !== "r20-implementation-in-progress" ||
    policy?.machineStatus !== "docs/V2_STATUS.json" ||
    policy?.qualificationProfile !== "matrix-oasis.deterministic-npc-bridge/1" ||
    policy?.blockingRound !== "R25" ||
    policy?.claimAllowed !== false
  ) {
    throw new V2ClaimError("V2_CLAIM_POLICY_INVALID");
  }
  if (
    status.schemaVersion !== 1 ||
    status.status !== policy.status ||
    status.claimAllowed !== false ||
    status.blockingRound !== policy.blockingRound ||
    status.qualificationProfile !== policy.qualificationProfile
  ) {
    throw new V2ClaimError("V2_CLAIM_STATUS_MISMATCH");
  }
  return Object.freeze({
    status: status.status,
    claimAllowed: false,
    blockingRound: status.blockingRound,
  });
}
