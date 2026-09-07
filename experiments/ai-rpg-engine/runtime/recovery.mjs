import { RUNTIME_FORMATS, RUNTIME_FORMAT_VERSION, validateRuntimeSession } from "./contracts.mjs";

const diagnostic = (phase, code) => Object.freeze({ phase, severity: "error", code, path: "" });
const failure = (phase, code) => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(phase, code)]) });
const success = (value) => Object.freeze({ valid: true, diagnostics: Object.freeze([]), value });

export function recoverSession(session, cardPackage, playerSetup, hash) {
  if (cardPackage === null || cardPackage === undefined || playerSetup === null || playerSetup === undefined || typeof hash !== "function") return failure("preflight", "RUNTIME_RECOVERY_ARGUMENT");
  const report = validateRuntimeSession(session, cardPackage, playerSetup, hash);
  if (!report.valid) return failure("reference", "RUNTIME_RECOVERY_SESSION_INVALID");
  if (session.revision >= Number.MAX_SAFE_INTEGER) return failure("policy", "RUNTIME_RECOVERY_REVISION_OVERFLOW");
  const value = structuredClone(session), nextRevision = value.revision + 1;
  value.revision = nextRevision;
  for (const generation of value.generations) {
    if (generation.status !== "active") continue;
    generation.status = "interrupted";
    generation.finishedRevision = nextRevision;
    generation.receipt = {
      format: RUNTIME_FORMATS.generationReceipt,
      formatVersion: RUNTIME_FORMAT_VERSION,
      sessionId: value.sessionId,
      cardPackageSha256: value.resources.cardPackage.sha256,
      playerSetupSha256: value.resources.playerSetup.sha256,
      generationId: generation.generationId,
      exchangeId: generation.exchangeId,
      revision: nextRevision,
      evidenceKind: generation.evidenceKind,
      status: "interrupted",
      outcome: "interrupted",
      requestedModel: generation.modelId,
      observedModel: null,
      serverReceipt: null,
      cancellation: { requested: Object.hasOwn(generation, "cancelRequestedRevision"), clientAborted: false, upstreamConfirmed: null },
      outputSha256: null,
      usage: { input: null, output: null, total: null },
      costUsd: null,
    };
  }
  const recovered = validateRuntimeSession(value, cardPackage, playerSetup, hash);
  return recovered.valid ? success(value) : failure("reference", "RUNTIME_RECOVERY_RESULT_INVALID");
}
