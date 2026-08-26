import path from "node:path";
import { fileURLToPath } from "node:url";
import { checkV2Claim, V2ClaimError } from "./lib/v2-claim-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

try {
  if (process.argv.length !== 2) throw new V2ClaimError("V2_CLAIM_ARGUMENT_ERROR");
  const result = checkV2Claim({ moduleRoot });
  console.log(
    `V2_CLAIM_OK status=${result.status} claimAllowed=${result.claimAllowed} blockingRound=${result.blockingRound}`,
  );
} catch (error) {
  console.error(error instanceof V2ClaimError ? error.code : "V2_CLAIM_INTERNAL_ERROR");
  process.exitCode = 1;
}
