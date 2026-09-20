import path from "node:path";
import { fileURLToPath } from "node:url";
import { NPC_COGNITION_ENDPOINT, NPC_COGNITION_MODEL, validateNpcCognitionCallPlanJson } from "@matrix-oasis/npc-cognition-contracts";
import { canonicalText, decodeCanonicalR22Record, readStableR22File, sha256 } from "./lib/r22-cli-core.mjs";

const scriptFile = fileURLToPath(import.meta.url); const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
export async function createR22CallDisclosure(args, overrides = {}) {
  if (!Array.isArray(args) || args.length !== 4 || args[0] !== "--call-plan" || args[2] !== "--payload" || !path.isAbsolute(args[1]) || !path.isAbsolute(args[3])) throw new Error("R22_CLI_ARGUMENT_INVALID");
  const root = overrides.temporaryRoot ?? temporaryRoot;
  const planText = decodeCanonicalR22Record(await readStableR22File(path.resolve(args[1]), 64 * 1024, root, overrides)).text;
  const payloadText = decodeCanonicalR22Record(await readStableR22File(path.resolve(args[3]), 32 * 1024, root, overrides)).text;
  const report = validateNpcCognitionCallPlanJson(planText); if (!report.valid) throw new Error("R22_CALL_PLAN_INVALID");
  const plan = JSON.parse(planText); if (plan.providerPayloadSha256 !== sha256(payloadText) || plan.endpoint !== NPC_COGNITION_ENDPOINT || plan.model !== NPC_COGNITION_MODEL) throw new Error("R22_APPROVAL_MISMATCH");
  return Object.freeze({ endpoint: plan.endpoint, model: plan.model, maximumRequests: 1, maximumCostMicrousd: plan.maxCostMicrousd, retention: plan.retention, approvalHash: plan.approval.hash, outgoingPayload: JSON.parse(payloadText), disclosureSha256: sha256(canonicalText({ callPlanSha256: sha256(planText), payloadSha256: sha256(payloadText), approvalHash: plan.approval.hash })) });
}
if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try { process.stdout.write(`${canonicalText(await createR22CallDisclosure(process.argv.slice(2)))}\n`); }
  catch { process.stderr.write("NPC_COGNITION_INTERNAL_ERROR\n"); process.exitCode = 2; }
}
