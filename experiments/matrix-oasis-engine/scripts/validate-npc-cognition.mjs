import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  NPC_COGNITION_LIMITS,
  validateNpcCognitionCallPlanJson,
  validateNpcCognitionPolicyJson,
  validateNpcCognitionQualificationReportJson,
  validateNpcCognitionTraceJson,
  validateNpcCognitionTurnReceiptJson,
  validateNpcCognitionTurnRequestJson,
  validateNpcDialogueProposalJson,
} from "@matrix-oasis/npc-cognition-contracts";
import { decodeCanonicalR22Record, readStableR22File } from "./lib/r22-cli-core.mjs";

const scriptFile = fileURLToPath(import.meta.url);
const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
const VALIDATORS = Object.freeze({
  policy: [validateNpcCognitionPolicyJson, NPC_COGNITION_LIMITS.policyBytes], turn: [validateNpcCognitionTurnRequestJson, NPC_COGNITION_LIMITS.turnRequestBytes],
  plan: [validateNpcCognitionCallPlanJson, NPC_COGNITION_LIMITS.callPlanBytes], proposal: [validateNpcDialogueProposalJson, NPC_COGNITION_LIMITS.dialogueProposalBytes],
  receipt: [validateNpcCognitionTurnReceiptJson, NPC_COGNITION_LIMITS.turnReceiptBytes], trace: [validateNpcCognitionTraceJson, NPC_COGNITION_LIMITS.traceBytes],
  qualification: [validateNpcCognitionQualificationReportJson, NPC_COGNITION_LIMITS.qualificationReportBytes],
});

export async function runValidateNpcCognition(args, overrides = {}) {
  if (!Array.isArray(args) || args.length !== 4 || args[0] !== "--kind" || args[2] !== "--file" || !VALIDATORS[args[1]] || typeof args[3] !== "string" || !path.isAbsolute(args[3])) throw new Error("R22_CLI_ARGUMENT_INVALID");
  const [validator, maximum] = VALIDATORS[args[1]]; const text = decodeCanonicalR22Record(await readStableR22File(path.resolve(args[3]), maximum, overrides.temporaryRoot ?? temporaryRoot, overrides)).text;
  const report = validator(text); if (!report.valid) throw new Error(report.diagnostics[0]?.code ?? "NPC_COGNITION_INTERNAL_ERROR");
  return Object.freeze({ ok: true, kind: args[1] });
}

if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try { process.stdout.write(`${JSON.stringify(await runValidateNpcCognition(process.argv.slice(2)))}\n`); }
  catch { process.stderr.write("NPC_COGNITION_INTERNAL_ERROR\n"); process.exitCode = 2; }
}
