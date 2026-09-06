import { canonicalJson } from "../runtime/contracts.mjs";

const OPERATIONS = Object.freeze(["create", "read", "resume", "generate", "cancel", "commit", "discard"]);
const ID = /^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$/u;
const SHA = /^[a-f0-9]{64}$/u, PHASES = new Set(["cli", "schema", "reference", "policy", "preflight", "runtime", "storage", "transport", "readiness"]), STATUSES = new Set(["active", "pending", "committed", "discarded", "cancelled", "failed", "interrupted"]);
const diagnostic = (code) => Object.freeze({ phase: "cli", severity: "error", code, path: "" });
const fail = (code) => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(code)]), value: null });
const ok = (value) => Object.freeze({ valid: true, diagnostics: Object.freeze([]), value: Object.freeze(value) });
function safeDiagnostics(items) { return Object.freeze((Array.isArray(items) ? items : []).slice(0, 64).map((item) => Object.freeze({ phase: PHASES.has(item?.phase) ? item.phase : "runtime", severity: item?.severity === "warning" ? "warning" : "error", code: /^[A-Z0-9_]{1,128}$/u.test(item?.code) ? item.code : "RUNTIME_FAILURE", path: "" }))); }
function clone(value) { const report = canonicalJson(value); return report.valid ? JSON.parse(report.value) : null; }
const safeId = (value) => typeof value === "string" && ID.test(value) ? value : null, safeSha = (value) => typeof value === "string" && SHA.test(value) ? value : null, safeInteger = (value) => Number.isSafeInteger(value) && value >= 0 ? value : null;
function safeUsage(value) { return value && typeof value === "object" && !Array.isArray(value) && ["input", "output", "total"].every((key) => value[key] === null || safeInteger(value[key]) !== null) ? Object.freeze({ input: value.input, output: value.output, total: value.total }) : null; }
function eventSummary(event) { const type = ["draft", "status", "receipt"].includes(event?.type) ? event.type : null; return Object.freeze({ kind: "event", type, seq: safeInteger(event?.seq), sessionId: safeId(event?.sessionId), generationId: safeId(event?.generationId), exchangeId: safeId(event?.exchangeId), revision: safeInteger(event?.revision), cardPackageSha256: safeSha(event?.cardPackageSha256), playerSetupSha256: safeSha(event?.playerSetupSha256), ...(type === "draft" ? { textLength: typeof event.text === "string" ? event.text.length : null } : {}), ...(type === "status" ? { status: STATUSES.has(event.status) ? event.status : null } : {}) }); }
function resultSummary(value) {
  const session = value?.session ?? (value?.format === "modelmirror.ai-rpg.runtime-session" ? value : null), generation = value?.generation ?? null;
  return Object.freeze({
    sessionId: safeId(session?.sessionId ?? value?.sessionId),
    generationId: safeId(generation?.generationId),
    exchangeId: safeId(generation?.exchangeId),
    revision: safeInteger(session?.revision),
    status: STATUSES.has(generation?.status) ? generation.status : null,
    outcome: safeId(value?.outcome ?? generation?.receipt?.outcome),
    pending: session?.pending === null ? false : Boolean(session?.pending),
    turnCount: Array.isArray(session?.turns) ? safeInteger(session.turns.length) : null,
    cardPackageSha256: safeSha(session?.resources?.cardPackage?.sha256),
    playerSetupSha256: safeSha(session?.resources?.playerSetup?.sha256),
    usage: safeUsage(generation?.receipt?.usage),
  });
}
function commandShape(value) { return value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).sort().join(",") === "input,operation,requestId" && ID.test(value.requestId) && OPERATIONS.includes(value.operation) && value.input && typeof value.input === "object" && !Array.isArray(value.input); }
export function safeCommandEnvelope(command, result) { return Object.freeze({ requestId: safeId(command?.requestId), operation: OPERATIONS.includes(command?.operation) ? command.operation : null, valid: result?.valid === true, diagnostics: safeDiagnostics(result?.diagnostics), value: result?.value && typeof result.value === "object" ? result.value : null }); }

export function createDeveloperDriver({ runtime, cardPackage, playerSetup } = {}) {
  if (!runtime || typeof runtime !== "object" || !cardPackage || !playerSetup || clone(cardPackage) === null || clone(playerSetup) === null) return fail("RUNTIME_CLI_DRIVER_CONFIG_INVALID");
  const resources = Object.freeze({ cardPackage: clone(cardPackage), playerSetup: clone(playerSetup) });
  const seen = new Map();
  const driver = Object.freeze({
    async runCommand(command, { onEvent } = {}) {
      const snapshot = clone(command);
      if (!commandShape(snapshot) || onEvent !== undefined && typeof onEvent !== "function") return fail("RUNTIME_CLI_COMMAND_INVALID");
      const canonical = canonicalJson(snapshot).value, prior = seen.get(snapshot.requestId);
      if (prior !== undefined) return fail(prior === canonical ? "RUNTIME_CLI_REQUEST_DUPLICATE" : "RUNTIME_CLI_REQUEST_CONFLICT");
      seen.set(snapshot.requestId, canonical);
      const resourceInput = { sessionId: snapshot.input.sessionId, cardPackage: resources.cardPackage, playerSetup: resources.playerSetup };
      if (["create", "read", "resume"].includes(snapshot.operation) && Object.keys(snapshot.input).join(",") !== "sessionId") return fail("RUNTIME_CLI_COMMAND_INPUT_INVALID");
      let promise;
      try {
        if (snapshot.operation === "create") promise = runtime.createSession(resourceInput);
        else if (snapshot.operation === "read") promise = runtime.readSession(resourceInput);
        else if (snapshot.operation === "resume") promise = runtime.resumeSession(resourceInput);
        else if (snapshot.operation === "generate") promise = runtime.generateTurn(snapshot.input, { onEvent: onEvent ? (event) => { try { onEvent(eventSummary(event)); } catch {} } : undefined });
        else if (snapshot.operation === "cancel") promise = runtime.cancelGeneration(snapshot.input);
        else if (snapshot.operation === "commit") promise = runtime.commitTurn(snapshot.input);
        else promise = runtime.discardTurn(snapshot.input);
        const result = await promise;
        return Object.freeze({ valid: result?.valid === true, diagnostics: safeDiagnostics(result?.diagnostics), value: result?.value ? resultSummary(result.value) : null });
      } catch { return fail("RUNTIME_CLI_RUNTIME_FAILED"); }
    },
  });
  return ok(driver);
}
