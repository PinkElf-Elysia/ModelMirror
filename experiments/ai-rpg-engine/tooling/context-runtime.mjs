import { compileContext } from "../context/compiler.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";
import { createRuntime } from "../runtime/core.mjs";
import { validateTurnExchange } from "../src/index.mjs";

const clone = (value) => structuredClone(value);
const diagnostic = (code, phase = "context-runtime") => Object.freeze({ phase, severity: "error", code, path: "" });
const fail = (code) => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(code)]) });
const ok = (value, diagnostics = []) => Object.freeze({ valid: true, diagnostics: Object.freeze(diagnostics), value });
const same = (left, right) => {
  const a = canonicalJson(left), b = canonicalJson(right);
  return a.valid && b.valid && a.value === b.value;
};
const digest = (value, hash) => {
  const encoded = canonicalJson(value);
  if (!encoded.valid) return null;
  try {
    const output = hash(encoded.value);
    return typeof output === "string" && /^[a-f0-9]{64}$/u.test(output) ? output : null;
  } catch { return null; }
};
const textDigest = (value, hash) => {
  if (typeof value !== "string") return null;
  try {
    const output = hash(value);
    return typeof output === "string" && /^[a-f0-9]{64}$/u.test(output) ? output : null;
  } catch { return null; }
};
const parseStrictJSON = (text) => {
  if (typeof text !== "string" || text.length > 1048576) return null;
  try {
    const value = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch { return null; }
};

/**
 * Offline admission bridge. It compiles trusted context immediately before dispatch,
 * while the frozen RPG03 runtime remains the only owner of session state changes.
 */
export function createPreparedRuntime({ store, modelAdapter, hash, hostTemplate } = {}) {
  if (!modelAdapter || typeof modelAdapter.generate !== "function" || !["mock", "real"].includes(modelAdapter.evidenceKind) || typeof hash !== "function" || !hostTemplate) return fail("CONTEXT_RUNTIME_PORT_INVALID");
  const admissions = new Map(), receipts = new Map();
  const wrappedAdapter = Object.freeze({
    evidenceKind: modelAdapter.evidenceKind,
    async generate(request, options) {
      const admission = admissions.get(request.generationId);
      if (!admission || admission.exchangeId !== request.exchangeId || !same(request, admission.prepared.request)) return fail("CONTEXT_RUNTIME_ADMISSION_MISSING");
      let report;
      try { report = await modelAdapter.generate(clone(request), options); }
      catch { report = null; }
      const rawText = typeof report?.value?.text === "string" ? report.value.text : "";
      const rawTextSha256 = textDigest(rawText, hash);
      const routeReceipt = report?.value?.serverReceipt === undefined ? null : clone(report.value.serverReceipt);
      const bridgeReceipt = Object.freeze({
        format: "modelmirror.ai-rpg.context-runtime-receipt", formatVersion: "0.1.0",
        evidenceKind: modelAdapter.evidenceKind, sessionId: request.sessionId,
        generationId: request.generationId, exchangeId: request.exchangeId,
        revision: admission.prepared.bindings.revision,
        preparedTurnSha256: admission.preparedSha256,
        contextReceiptSha256: digest(admission.prepared.receipt, hash),
        rawTurnExchangeSha256: rawTextSha256,
        routeReceipt,
      });
      receipts.set(request.generationId, bridgeReceipt);
      if (report?.valid !== true || report.value?.status !== "succeeded") return report ?? fail("CONTEXT_RUNTIME_ADAPTER_FAILED");
      const exchange = parseStrictJSON(rawText);
      if (!exchange || !validateTurnExchange(exchange, admission.cardPackage).valid ||
          exchange.exchangeId !== request.exchangeId || !same(exchange.cardPackageRef, { id: admission.cardPackage.package.id, version: admission.cardPackage.package.version }) ||
          !same(exchange.input, request.input)) {
        return { valid: false, diagnostics: [diagnostic("CONTEXT_RUNTIME_TURN_EXCHANGE_INVALID")], value: { ...clone(report.value), status: "failed", outcome: "turn_exchange_invalid", text: "" } };
      }
      return { valid: true, diagnostics: [], value: { ...clone(report.value), text: JSON.stringify(exchange.proposal) } };
    },
  });
  const created = createRuntime({ store, modelAdapter: wrappedAdapter, hash });
  if (!created.valid) return created;
  const runtime = created.value;
  const preparedRuntime = Object.freeze({
    createSession: runtime.createSession,
    readSession: runtime.readSession,
    resumeSession: runtime.resumeSession,
    commitTurn: runtime.commitTurn,
    discardTurn: runtime.discardTurn,
    cancelGeneration: runtime.cancelGeneration,
    setPluginAuthorization: runtime.setPluginAuthorization,
    getBridgeReceipt(generationId) { return receipts.has(generationId) ? ok(clone(receipts.get(generationId))) : fail("CONTEXT_RUNTIME_RECEIPT_MISSING"); },
    async generatePreparedTurn({ contextInput, prepared } = {}, options = {}) {
      if (!contextInput || typeof contextInput !== "object") return fail("CONTEXT_RUNTIME_INPUT_REQUIRED");
      const input = clone(contextInput);
      const compiled = compileContext(input, { hash, hostTemplate });
      if (!compiled.valid) return compiled;
      if (prepared !== undefined && !same(prepared, compiled.value)) return fail("CONTEXT_RUNTIME_PREPARED_DRIFT");
      const preparedSha256 = digest(compiled.value, hash);
      if (!preparedSha256) return fail("CONTEXT_RUNTIME_PREPARED_HASH_FAILED");
      const current = await runtime.readSession({ sessionId: input.sessionId ?? input.session?.sessionId, cardPackage: input.cardPackage, playerSetup: input.playerSetup });
      if (!current.valid) return current;
      const generationId = compiled.value.request.generationId;
      if (!same(current.value, input.session)) {
        const priorReceipt = receipts.get(generationId);
        const existing = current.value.generations.find((generation) => generation.generationId === generationId);
        if (!priorReceipt || priorReceipt.preparedTurnSha256 !== preparedSha256 || !existing || existing.exchangeId !== compiled.value.request.exchangeId || existing.inputSha256 !== compiled.value.receipt.generationInputSha256) return fail("CONTEXT_RUNTIME_SESSION_DRIFT");
        const repeated = await runtime.generateTurn(compiled.value.request, options);
        return repeated?.value ? (repeated.valid ? ok({ ...repeated.value, bridgeReceipt: clone(priorReceipt) }, repeated.diagnostics) : { ...repeated, value: { ...repeated.value, bridgeReceipt: clone(priorReceipt) } }) : repeated;
      }
      admissions.set(generationId, { exchangeId: compiled.value.request.exchangeId, prepared: clone(compiled.value), preparedSha256, cardPackage: clone(input.cardPackage) });
      try {
        const generated = await runtime.generateTurn(compiled.value.request, options);
        const bridgeReceipt = receipts.get(generationId);
        if (!bridgeReceipt || !generated?.value) return generated;
        return generated.valid ? ok({ ...generated.value, bridgeReceipt: clone(bridgeReceipt) }, generated.diagnostics) : { ...generated, value: { ...generated.value, bridgeReceipt: clone(bridgeReceipt) } };
      } finally { admissions.delete(generationId); }
    },
  });
  return ok(preparedRuntime);
}
