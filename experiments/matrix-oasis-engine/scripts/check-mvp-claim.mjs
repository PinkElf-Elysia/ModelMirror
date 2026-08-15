import path from "node:path";
import { fileURLToPath } from "node:url";
import { checkMvpClaim, MvpClaimError } from "./lib/mvp-claim-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

try {
  if (process.argv.length !== 2) {
    throw new MvpClaimError("MVP_CLAIM_ARGUMENT_ERROR");
  }
  const result = checkMvpClaim({ moduleRoot });
  console.log(
    `MVP_CLAIM_OK status=${result.status} claimAllowed=${result.claimAllowed} checked=${result.checkedDocuments}`,
  );
} catch (error) {
  const code = error instanceof MvpClaimError
    ? error.code
    : "MVP_CLAIM_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = 1;
}
