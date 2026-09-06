import { evaluatePluginReadiness } from "../src/index.mjs";
import {
  RUNTIME_FORMATS, RUNTIME_FORMAT_VERSION, canonicalJson,
  computeGenerationInputSha256, computeProposalSha256, validateCancelGenerationRequest,
  validateCommitTurnRequest, validateCreateSessionRequest, validateDiscardTurnRequest,
  validateGenerateTurnRequest, validateGenerationReceipt, validateModelProposal,
  validateRuntimeEvent, validateRuntimeResourceBindings, validateRuntimeSession,
  validateSetPluginAuthorizationRequest,
} from "./contracts.mjs";
import { recoverSession } from "./recovery.mjs";

const clone = (value) => structuredClone(value);
const diagnostic = (code, severity = "error") => Object.freeze({ phase: "runtime", severity, code, path: "" });
const ok = (value, diagnostics = []) => Object.freeze({ valid: true, diagnostics: Object.freeze(diagnostics), value });
const fail = (code, value) => Object.freeze({ valid: false, diagnostics: Object.freeze([diagnostic(code)]), ...(value === undefined ? {} : { value }) });
const unknownUsage = () => ({ input: null, output: null, total: null });
const emptyProposal = () => ({ narrative: "Admission validation", suggestedActions: [], informationModules: [], stateProposals: [], uncertainties: [] });
const safeId = (value) => typeof value === "string" && /^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$/u.test(value);
const safeSnapshot = (value, validator) => { try { const report = validator(value); return report.valid ? ok(clone(value)) : report; } catch { return fail("RUNTIME_INPUT_INVALID"); } };

/** Ports are supplied by the trusted host; this module never opens files or dispatches HTTP itself. */
export function createRuntime({ store, modelAdapter, hash, pluginHost = null } = {}) {
  if (!store || typeof store.read !== "function" || typeof store.write !== "function" || !modelAdapter || typeof modelAdapter.generate !== "function" || !["mock", "real"].includes(modelAdapter.evidenceKind) || typeof hash !== "function" || pluginHost !== null && ["readiness", "checkAuthorization", "disable"].some((method) => typeof pluginHost[method] !== "function")) return fail("RUNTIME_PORT_INVALID");
  const evidenceKind = modelAdapter.evidenceKind, sessions = new Map();
  let queue = Promise.resolve(), active = null;
  function serial(operation) {
    const result = queue.then(operation).catch(() => fail("RUNTIME_OPERATION_FAILED"));
    queue = result.then(() => undefined);
    return result;
  }
  function readiness(entry) {
    try {
      const latest = new Map(entry.session.pluginAuthorizations.map((record) => [record.pluginId, record]));
      if ([...latest.values()].some((record) => record.action === "authorize" && record.evidenceKind !== evidenceKind)) return fail("RUNTIME_PLUGIN_EVIDENCE_MISMATCH");
      const report = pluginHost === null ? evaluatePluginReadiness(entry.cardPackage, []) : pluginHost.readiness(clone(entry.cardPackage), clone(entry.session));
      if (!report || report.ready !== true) return fail("RUNTIME_REQUIRED_PLUGIN_UNAVAILABLE");
      const diagnostics = (report.diagnostics ?? []).map((item) => diagnostic(/^PLUGIN_[A-Z_]+$/u.test(item.code) ? item.code : "RUNTIME_PLUGIN_DEGRADED", "warning"));
      return ok(null, diagnostics);
    } catch { return fail("RUNTIME_PLUGIN_HOST_FAILED"); }
  }
  function event(token, type, extra) {
    if (!token.onEvent) return;
    const session = token.entry.session;
    const value = { format: RUNTIME_FORMATS.event, formatVersion: RUNTIME_FORMAT_VERSION, sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, generationId: token.generationId, exchangeId: token.exchangeId, revision: session.revision, evidenceKind, seq: token.seq++, type, ...extra };
    if (!validateRuntimeEvent(value).valid) { token.observerFailed = true; return; }
    try {
      const result = token.onEvent(clone(value));
      if (result && typeof result.then === "function") Promise.resolve(result).catch(() => { token.observerFailed = true; });
    } catch { token.observerFailed = true; }
  }
  async function save(entry, next, expectedRevision) {
    if (!validateRuntimeSession(next, entry.cardPackage, entry.playerSetup, hash).valid) return fail("RUNTIME_CHECKPOINT_INVALID");
    let result;
    try { result = await store.write(clone(next), { expectedRevision, cardPackage: clone(entry.cardPackage), playerSetup: clone(entry.playerSetup) }); } catch { result = null; }
    if (!result?.valid || !validateRuntimeSession(result.value, entry.cardPackage, entry.playerSetup, hash).valid || canonicalJson(result.value).value !== canonicalJson(next).value) {
      entry.faulted = true;
      if (active?.entry === entry) active.controller.abort();
      return fail("RUNTIME_STORE_WRITE_FAILED");
    }
    entry.session = clone(next); entry.faulted = false;
    return ok(clone(next));
  }
  function current(sessionId) {
    const entry = sessions.get(sessionId);
    return !entry ? fail("RUNTIME_SESSION_NOT_OPEN") : entry.faulted ? fail("RUNTIME_SESSION_FAULTED") : ok(entry);
  }
  function incrementable(session, count = 1) { return Number.isSafeInteger(session.revision + count); }
  function observation(entry, generation) {
    const value = { session: clone(entry.session), generation: clone(generation) };
    return ["failed", "cancelled", "interrupted"].includes(generation.status) ? fail("RUNTIME_GENERATION_" + generation.status.toUpperCase(), value) : ok(value);
  }
  function receipt(session, generation, result, status, outcome, proposalSha256 = null) {
    return {
      format: RUNTIME_FORMATS.generationReceipt, formatVersion: RUNTIME_FORMAT_VERSION,
      sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256,
      generationId: generation.generationId, exchangeId: generation.exchangeId, revision: session.revision, evidenceKind: generation.evidenceKind,
      status, outcome, requestedModel: generation.modelId, observedModel: result.observedModel ?? null,
      serverReceipt: result.serverReceipt ?? null,
      cancellation: result.cancellation ?? { requested: false, clientAborted: false, upstreamConfirmed: null },
      outputSha256: proposalSha256, usage: result.usage ?? unknownUsage(), costUsd: null,
    };
  }
  function safeAdapterResult(report, token) {
    const encoded = canonicalJson(report?.value);
    if (!encoded.valid) return null;
    const value = JSON.parse(encoded.value);
    if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
    if (!["succeeded", "cancelled", "failed"].includes(value.status) || typeof value.text !== "string" || value.text.length > 1048576 || !safeId(value.outcome) || value.status === "succeeded" && report.valid !== true) return null;
    if (value.cancellation && typeof value.cancellation === "object" && !Array.isArray(value.cancellation) && token.cancelRequested) value.cancellation.requested = true;
    return value;
  }
  async function finishGeneration(token, adapterReport) {
    return serial(async () => {
      const entry = token.entry;
      try {
        if (active !== token || entry.faulted) return fail("RUNTIME_SESSION_FAULTED");
        const original = entry.session, next = clone(original), generation = next.generations.find((item) => item.generationId === token.generationId);
        if (!generation || generation.status !== "active" || !incrementable(next)) return fail("RUNTIME_GENERATION_STATE_CONFLICT");
        next.revision += 1;
        const fallback = { status: "failed", outcome: token.streamFailure ? "draft_limit" : "adapter_failed", text: token.draft, observedModel: null, serverReceipt: null, cancellation: { requested: token.cancelRequested, clientAborted: false, upstreamConfirmed: null }, usage: unknownUsage() };
        let result = safeAdapterResult(adapterReport, token) ?? fallback, status = result.status, outcome = result.outcome, exchange = null, outputHash = null;
        if (token.streamFailure) { result = fallback; status = "failed"; outcome = "draft_limit"; }
        if (token.cancelRequested && status === "succeeded") {
          status = "cancelled"; outcome = "cancelled_late_result";
          result.cancellation = { requested: true, clientAborted: result.cancellation?.clientAborted === true, upstreamConfirmed: result.cancellation?.upstreamConfirmed ?? null };
        }
        if (status === "succeeded") {
          let parsed; try { parsed = JSON.parse(result.text); } catch { parsed = null; }
          const validated = validateModelProposal(parsed, token.exchangeId, token.request.input, entry.cardPackage);
          const output = validated.valid ? computeProposalSha256(parsed, hash) : null;
          if (!validated.valid || !output?.valid) { status = "failed"; outcome = "proposal_invalid"; }
          else { exchange = validated.value; outputHash = output.value; }
        }
        let completedReceipt = receipt(next, generation, result, status, outcome, outputHash);
        if (!validateGenerationReceipt(completedReceipt).valid) {
          result = fallback; status = "failed"; outcome = "adapter_result_invalid"; exchange = null; outputHash = null;
          completedReceipt = receipt(next, generation, result, status, outcome);
        }
        generation.finishedRevision = next.revision;
        generation.receipt = completedReceipt;
        generation.status = status === "succeeded" ? "pending" : status;
        generation.draftText = exchange ? "" : result.text;
        if (exchange) { generation.exchange = exchange; next.pending = { generationId: token.generationId, exchangeId: token.exchangeId }; }
        const written = await save(entry, next, original.revision);
        if (!written.valid) return written;
        event(token, "status", { status: generation.status });
        event(token, "receipt", { receipt: completedReceipt });
        const observed = observation(entry, generation);
        return token.observerFailed && observed.valid ? ok(observed.value, [diagnostic("RUNTIME_EVENT_OBSERVER_FAILED", "warning")]) : observed;
      } finally { if (active === token) active = null; }
    });
  }
  async function runGeneration(token) {
    event(token, "status", { status: "active" });
    let result;
    try {
      result = await modelAdapter.generate(clone(token.request), {
        signal: token.controller.signal,
        onText: (part) => {
          if (active !== token || token.controller.signal.aborted) return;
          if (typeof part !== "string" || part.length + token.draft.length > 1048576 || token.chunks++ >= 4096) {
            token.streamFailure = true; token.controller.abort(); throw new Error("RUNTIME_DRAFT_LIMIT");
          }
          token.draft += part;
          for (let offset = 0; offset < part.length;) {
            let end = Math.min(offset + 65536, part.length);
            if (end < part.length && /[\uD800-\uDBFF]/u.test(part[end - 1])) end -= 1;
            event(token, "draft", { text: part.slice(offset, end) }); offset = end;
          }
        },
      });
    } catch { result = null; }
    token.resultReady = true;
    return finishGeneration(token, result);
  }
  function openSession(value, mode) {
    const snap = safeSnapshot(value, validateCreateSessionRequest);
    if (!snap.valid) return Promise.resolve(fail("RUNTIME_RESOURCE_REQUEST_INVALID"));
    if (mode !== "create" && Object.hasOwn(snap.value, "pluginAuthorizations")) return Promise.resolve(fail("RUNTIME_READ_AUTHORIZATION_INJECTION"));
    return serial(async () => {
      const request = snap.value, prior = sessions.get(request.sessionId);
      if (active !== null && active.entry === prior) {
        if (mode === "resume") return fail("RUNTIME_RECOVERY_WHILE_ACTIVE");
        if (prior.faulted) return fail("RUNTIME_SESSION_FAULTED");
      }
      if (prior && !validateRuntimeResourceBindings(request.cardPackage, request.playerSetup, prior.session.resources, hash).valid) return fail("RUNTIME_RESOURCE_BINDING_MISMATCH");
      if (mode === "read" && prior && !prior.faulted) return ok(clone(prior.session));
      let stored;
      try { stored = await store.read(request.sessionId, { cardPackage: request.cardPackage, playerSetup: request.playerSetup }); } catch { return fail("RUNTIME_STORE_READ_FAILED"); }
      if (!stored?.valid) return fail("RUNTIME_STORE_READ_FAILED");
      if (mode === "create" && stored.value !== null) return fail("RUNTIME_SESSION_EXISTS");
      if (mode !== "create" && stored.value === null) return fail("RUNTIME_SESSION_MISSING");
      let session;
      if (mode === "create") {
        const card = computeProposalSha256(request.cardPackage, hash), player = computeProposalSha256(request.playerSetup, hash);
        if (!card.valid || !player.valid) return fail("RUNTIME_RESOURCE_HASH_FAILED");
        session = {
          format: RUNTIME_FORMATS.session, formatVersion: RUNTIME_FORMAT_VERSION, sessionId: request.sessionId, revision: 0,
          resources: { cardPackage: { id: request.cardPackage.package.id, version: request.cardPackage.package.version, sha256: card.value }, playerSetup: { setupId: request.playerSetup.setupId, sha256: player.value } },
          state: request.cardPackage.stateFields.map((field) => ({ fieldRef: field.id, value: field.initialValue })), turns: [], generations: [], pending: null, pluginAuthorizations: clone(request.pluginAuthorizations ?? []),
        };
        for (const record of session.pluginAuthorizations) {
          if (!pluginHost || record.cardPackageSha256 !== card.value || record.playerSetupSha256 !== player.value || record.evidenceKind !== evidenceKind) return fail("RUNTIME_INITIAL_AUTHORIZATION_BINDING");
          try { if (pluginHost.checkAuthorization(clone(record))?.valid !== true) return fail("RUNTIME_INITIAL_AUTHORIZATION_REJECTED"); } catch { return fail("RUNTIME_PLUGIN_HOST_FAILED"); }
        }
      } else {
        if (!validateRuntimeSession(stored.value, request.cardPackage, request.playerSetup, hash).valid) return fail("RUNTIME_STORED_SESSION_INVALID");
        session = clone(stored.value);
      }
      if (mode === "read" && prior?.faulted) return ok(clone(session), [diagnostic("RUNTIME_SESSION_FAULTED", "warning")]);
      const entry = { session, cardPackage: request.cardPackage, playerSetup: request.playerSetup, faulted: false }, ready = mode === "read" ? ok(null) : readiness(entry);
      if (!ready.valid) return ready;
      if (mode === "resume") {
        const recovered = recoverSession(session, request.cardPackage, request.playerSetup, hash);
        if (!recovered.valid) return fail("RUNTIME_RECOVERY_INVALID");
        const saved = await save(entry, recovered.value, session.revision);
        if (!saved.valid) return saved;
      } else if (mode === "create") {
        const saved = await save(entry, session, null);
        if (!saved.valid) return saved;
      }
      sessions.set(request.sessionId, entry);
      return ok(clone(entry.session), ready.diagnostics);
    });
  }
  function resolvePending(value, commit) {
    const snap = safeSnapshot(value, commit ? validateCommitTurnRequest : validateDiscardTurnRequest);
    if (!snap.valid) return Promise.resolve(fail("RUNTIME_TURN_REQUEST_INVALID"));
    return serial(async () => {
      const request = snap.value, found = current(request.sessionId); if (!found.valid) return found;
      const entry = found.value, session = entry.session;
      if (session.revision !== request.expectedRevision) return fail("RUNTIME_REVISION_CONFLICT");
      if (!session.pending || session.pending.generationId !== request.generationId || session.pending.exchangeId !== request.exchangeId) return fail("RUNTIME_PENDING_CONFLICT");
      if (!incrementable(session)) return fail("RUNTIME_REVISION_OVERFLOW");
      const next = clone(session), generation = next.generations.find((item) => item.generationId === request.generationId);
      const accepted = new Set(commit ? request.acceptedStateFields : []), proposals = generation.exchange.proposal.stateProposals;
      if (generation.exchange.input.kind === "query" && accepted.size || [...accepted].some((field) => !proposals.some((proposal) => proposal.fieldRef === field))) return fail("RUNTIME_STATE_SELECTION_INVALID");
      next.revision += 1; generation.status = commit ? "committed" : "discarded"; generation.resolvedRevision = next.revision; next.pending = null;
      if (commit) {
        next.turns.push({ generationId: generation.generationId, exchange: clone(generation.exchange), committedRevision: next.revision, acceptedStateFields: [...request.acceptedStateFields] });
        for (const proposal of proposals) if (accepted.has(proposal.fieldRef)) next.state.find((field) => field.fieldRef === proposal.fieldRef).value = proposal.proposedValue;
      }
      return save(entry, next, session.revision);
    });
  }
  const runtime = Object.freeze({
    createSession: (request) => openSession(request, "create"),
    readSession: (request) => openSession(request, "read"),
    resumeSession: (request) => openSession(request, "resume"),
    commitTurn: (request) => resolvePending(request, true),
    discardTurn: (request) => resolvePending(request, false),
    setPluginAuthorization(value) {
      const snap = safeSnapshot(value, validateSetPluginAuthorizationRequest);
      if (!snap.valid) return Promise.resolve(fail("RUNTIME_AUTHORIZATION_REQUEST_INVALID"));
      return serial(async () => {
        const request = snap.value, found = current(request.sessionId); if (!found.valid) return found;
        const entry = found.value, session = entry.session, record = request.authorization;
        if (request.expectedRevision !== session.revision) return fail("RUNTIME_REVISION_CONFLICT");
        if (!pluginHost) return fail("RUNTIME_PLUGIN_HOST_UNAVAILABLE");
        if (active?.entry === entry || session.generations.some((generation) => generation.status === "active")) return fail("RUNTIME_AUTHORIZATION_WHILE_ACTIVE");
        if (!incrementable(session)) return fail("RUNTIME_REVISION_OVERFLOW");
        if (record.sessionId !== session.sessionId || record.revision !== session.revision + 1 || record.cardPackageSha256 !== session.resources.cardPackage.sha256 || record.playerSetupSha256 !== session.resources.playerSetup.sha256 || record.evidenceKind !== evidenceKind) return fail("RUNTIME_AUTHORIZATION_BINDING");
        if (record.action === "revoke") {
          const previous = session.pluginAuthorizations.findLast((item) => item.pluginId === record.pluginId);
          if (!previous || previous.action !== "authorize" || ["version", "manifestSha256", "artifactSha256", "evidenceKind"].some((key) => record[key] !== previous[key])) return fail("RUNTIME_REVOCATION_BINDING");
        } else {
          try { if (pluginHost.checkAuthorization(clone(record))?.valid !== true) return fail("RUNTIME_AUTHORIZATION_REJECTED"); } catch { return fail("RUNTIME_PLUGIN_HOST_FAILED"); }
        }
        const next = clone(session); next.revision += 1; next.pluginAuthorizations.push(clone(record));
        const saved = await save(entry, next, session.revision); if (!saved.valid) return saved;
        try {
          if (pluginHost.disable({ sessionId: session.sessionId, pluginId: record.pluginId })?.valid !== true) throw new Error("RUNTIME_PLUGIN_DISABLE_FAILED");
        } catch { entry.faulted = true; return fail("RUNTIME_PLUGIN_DISABLE_FAILED"); }
        return saved;
      });
    },
    async generateTurn(value, { onEvent } = {}) {
      const snap = safeSnapshot(value, validateGenerateTurnRequest);
      if (!snap.valid || onEvent !== undefined && typeof onEvent !== "function") return fail("RUNTIME_GENERATE_REQUEST_INVALID");
      const admitted = await serial(async () => {
        const request = snap.value, found = current(request.sessionId); if (!found.valid) return found;
        const entry = found.value, session = entry.session, inputHash = computeGenerationInputSha256(request, session, hash);
        if (!inputHash.valid) return fail("RUNTIME_INPUT_HASH_FAILED");
        const previous = session.generations.find((generation) => generation.generationId === request.generationId);
        if (previous) return previous.inputSha256 === inputHash.value ? { existing: observation(entry, previous) } : fail("RUNTIME_GENERATION_ID_CONFLICT");
        if (active) return fail("RUNTIME_INSTANCE_BUSY");
        if (session.pending !== null || session.generations.some((generation) => generation.status === "active")) return fail("RUNTIME_SESSION_UNRESOLVED");
        if (request.expectedRevision !== session.revision) return fail("RUNTIME_REVISION_CONFLICT");
        if (session.generations.some((generation) => generation.exchangeId === request.exchangeId)) return fail("RUNTIME_EXCHANGE_ID_CONFLICT");
        if (!incrementable(session, 2)) return fail("RUNTIME_REVISION_OVERFLOW");
        if (!validateModelProposal(emptyProposal(), request.exchangeId, request.input, entry.cardPackage).valid) return fail("RUNTIME_TURN_INPUT_INVALID");
        const ready = readiness(entry); if (!ready.valid) return ready;
        const next = clone(session); next.revision += 1;
        next.generations.push({ generationId: request.generationId, exchangeId: request.exchangeId, inputSha256: inputHash.value, modelId: request.modelId, evidenceKind, status: "active", requestRevision: session.revision, startedRevision: next.revision, draftText: "" });
        const saved = await save(entry, next, session.revision); if (!saved.valid) return saved;
        const token = { entry, request, generationId: request.generationId, exchangeId: request.exchangeId, controller: new AbortController(), onEvent, seq: 0, chunks: 0, draft: "", cancelRequested: false, resultReady: false, streamFailure: false, observerFailed: false };
        active = token; return { token };
      });
      if (admitted.existing) return admitted.existing;
      return admitted.token ? runGeneration(admitted.token) : admitted;
    },
    cancelGeneration(value) {
      const snap = safeSnapshot(value, validateCancelGenerationRequest);
      if (!snap.valid) return Promise.resolve(fail("RUNTIME_CANCEL_REQUEST_INVALID"));
      return serial(async () => {
        const request = snap.value, found = current(request.sessionId); if (!found.valid) return found;
        const entry = found.value, session = entry.session;
        if (request.expectedRevision !== session.revision) return fail("RUNTIME_REVISION_CONFLICT");
        const generation = session.generations.find((item) => item.generationId === request.generationId);
        if (!generation) return fail("RUNTIME_GENERATION_MISSING");
        const token = active?.entry === entry && active.generationId === request.generationId ? active : null;
        if (generation.status !== "active" || token?.resultReady) return ok({ session: clone(session), generation: clone(generation), cancellation: { requested: true, clientAborted: false, upstreamConfirmed: null }, outcome: "completed_before_cancel" });
        if (!token) return fail("RUNTIME_GENERATION_NEEDS_RECOVERY");
        if (!incrementable(session, 2)) return fail("RUNTIME_REVISION_OVERFLOW");
        const next = clone(session); next.revision += 1;
        const cancelling = next.generations.find((item) => item.generationId === request.generationId);
        cancelling.draftText = token.draft;
        cancelling.cancelRequestedRevision ??= next.revision;
        const saved = await save(entry, next, session.revision); if (!saved.valid) return saved;
        token.cancelRequested = true; token.controller.abort();
        return ok({ session: clone(entry.session), generation: clone(entry.session.generations.find((item) => item.generationId === request.generationId)), cancellation: { requested: true, clientAborted: false, upstreamConfirmed: null }, outcome: "cancel_requested" });
      });
    },
  });
  return ok(runtime);
}
