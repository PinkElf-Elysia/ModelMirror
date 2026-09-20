import path from "node:path";
import { fileURLToPath } from "node:url";
import { canonicalText, parseR22Pairs } from "./lib/r22-cli-core.mjs";
import {
  describeR22OfficialToolUsageDiagnosticTransaction,
  createR22OfficialToolUsageDiagnosticTransaction,
  recoverR22OfficialToolUsageDiagnosticTransaction,
} from "./lib/r22-diagnostic-transaction.mjs";

const scriptFile = fileURLToPath(import.meta.url);
const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
const fields = Object.freeze({ "--cognition-run-root": "cognitionRunRoot", "--host-run-id": "hostRunId",
  "--session-manifest-sha256": "expectedSessionManifestSha256", "--host-budget-sha256": "expectedHostBudgetSha256", "--output": "output" });
const required = Object.values(fields);

// The only real-dispatch CLI requires the full, newly displayed disclosure SHA.
// There is deliberately no key value, endpoint, model, payload or executor
// argument. File selection is explicit, metadata-pinned and approval-bound.
export async function runR22ToolUsageDiagnostic(args) {
  if (arguments.length !== 1 || !Array.isArray(args) || !["plan", "execute", "recover"].includes(args[0])) throw new Error("R22_CLI_ARGUMENT_INVALID");
  const mode = args[0];
  const extra = mode === "execute" ? { "--approve-disclosure": "approval" } : mode === "recover" ? { "--transaction-sha256": "transaction" } : {};
  const hasCredentialFile = args.slice(1).some((value, index) => index % 2 === 0 && value === "--credential-file");
  const credential = mode !== "recover" && hasCredentialFile ? { "--credential-file": "credentialFile" } : {};
  const hasPrevious = args.slice(1).some((value, index) => index % 2 === 0 && value === "--continue-from-transaction");
  const additional = mode !== "recover" && hasPrevious ? { "--continue-from-transaction": "previousTransactionSha256" } : {};
  const hasBilling = args.slice(1).some((value, index) => index % 2 === 0 && value === "--billing-after-transaction");
  const billing = mode !== "recover" && hasBilling ? { "--billing-after-transaction": "billingAfterTransactionSha256" } : {};
  const hasProfile = args.slice(1).some((value, index) => index % 2 === 0 && value === "--capture-profile");
  const profile = hasProfile ? { "--capture-profile": "captureProfile" } : {};
  const parsed = parseR22Pairs(args.slice(1), { ...fields, ...extra, ...credential, ...additional, ...billing, ...profile },
    [...required, ...Object.values(extra), ...Object.values(credential), ...Object.values(additional), ...Object.values(billing), ...Object.values(profile)]);
  if (hasProfile && !["tool-usage", "billing"].includes(parsed.captureProfile)) throw new Error("R22_CLI_ARGUMENT_INVALID");
  if (hasBilling && (hasPrevious || !hasCredentialFile || parsed.captureProfile !== "billing")) throw new Error("R22_CLI_ARGUMENT_INVALID");
  const { approval, transaction, ...source } = parsed;
  const config = { temporaryRoot, ...source };
  if (mode === "recover") return recoverR22OfficialToolUsageDiagnosticTransaction(config, transaction);
  if (mode === "plan") return describeR22OfficialToolUsageDiagnosticTransaction(config);
  // The factory pins once and compares this approval before creating a claim.
  const operation = await createR22OfficialToolUsageDiagnosticTransaction({ ...config, expectedDisclosureSha256: approval });
  try {
    if (operation.disclosure.transactionSha256 !== approval) throw new Error("R22_APPROVAL_MISMATCH");
    const token = await operation.approve({ disclosureSha256: approval });
    return await operation.execute(token);
  } finally { await operation.close(); }
}

if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try { process.stdout.write(`${canonicalText(await runR22ToolUsageDiagnostic(process.argv.slice(2)))}\n`); }
  catch (error) {
    const code = ["R22_DIAGNOSTIC_CREDENTIAL_NOT_CONFIGURED", "R22_OFFICIAL_CREDENTIAL_FILE_UNAVAILABLE"].includes(error?.message)
      ? error.message : "R22_DIAGNOSTIC_CLI_FAILED";
    process.stderr.write(`${code}\n`); process.exitCode = 2;
  }
}
