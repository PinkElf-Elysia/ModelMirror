import Ajv2020 from "ajv/dist/2020.js";
import { PLUGIN_DATA_PROPOSAL_SCOPES, PLUGIN_DATA_READ_SCOPES, PLUGIN_PERMISSIONS, TURN_EXCHANGE_SCHEMA, validateCardPackage, validatePlayerSetup, validateTurnExchange } from "../src/index.mjs";

export const RUNTIME_FORMAT_VERSION = "0.1.0";
export const RUNTIME_FORMATS = Object.freeze({ session: "modelmirror.ai-rpg.runtime-session", event: "modelmirror.ai-rpg.runtime-event", generationReceipt: "modelmirror.ai-rpg.generation-receipt", turnCommit: "modelmirror.ai-rpg.turn-commit", pluginAuthorization: "modelmirror.ai-rpg.plugin-authorization" });
const ID_PATTERN = "^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$", VERSION_PATTERN = "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$", SHA_PATTERN = "^[A-Fa-f0-9]{64}$";
const id = { type: "string", pattern: ID_PATTERN }, version = { type: "string", pattern: VERSION_PATTERN }, sha = { type: "string", pattern: SHA_PATTERN };
const revision = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER }, positiveRevision = { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER };
const nullableString = (maximum) => ({ type: ["string", "null"], maxLength: maximum });
const strict = (required, properties, extra = {}) => ({ type: "object", additionalProperties: false, required, properties, ...extra });
function deepFreeze(value) { if (value && typeof value === "object" && !Object.isFrozen(value)) { Object.freeze(value); for (const child of Object.values(value)) deepFreeze(child); } return value; }
const schema = (value) => deepFreeze({ $schema: "https://json-schema.org/draft/2020-12/schema", ...value });
const resourcesSchema = strict(["cardPackage", "playerSetup"], { cardPackage: strict(["id", "version", "sha256"], { id, version, sha256: sha }), playerSetup: strict(["setupId", "sha256"], { setupId: id, sha256: sha }) });
const turnInputSchema = { oneOf: [...["action", "speech", "query"].map((kind) => strict(["kind", "text"], { kind: { const: kind }, text: { type: "string", minLength: 1, maxLength: 65536 } })), strict(["kind", "commandRef", "text"], { kind: { const: "command" }, commandRef: id, text: { type: "string", minLength: 1, maxLength: 65536 } })] };
const messageSchema = strict(["role", "content"], { role: { enum: ["system", "user", "assistant"] }, content: { type: "string", minLength: 1, maxLength: 65536 } });
const settingsSchema = strict(["temperature", "maxTokens"], { temperature: { type: "number", minimum: 0, maximum: 2 }, maxTokens: { type: "integer", minimum: 1, maximum: 32768 } });
const cancellationSchema = strict(["requested", "clientAborted", "upstreamConfirmed"], { requested: { type: "boolean" }, clientAborted: { type: "boolean" }, upstreamConfirmed: { type: ["boolean", "null"] } });
const usageSchema = strict(["input", "output", "total"], { input: { type: ["integer", "null"], minimum: 0 }, output: { type: ["integer", "null"], minimum: 0 }, total: { type: ["integer", "null"], minimum: 0 } });
const serverReceiptSchema = { oneOf: [{ type: "null" }, strict(["requested_model", "actual_model", "provider", "strategy", "engine", "reason_codes", "latency_ms", "ttft_ms", "tokens", "response_cost_usd", "cost_kind", "fallback_attempts", "cache_hit", "request_id", "version"], { requested_model: { type: "string", minLength: 1, maxLength: 512 }, actual_model: nullableString(512), provider: nullableString(256), strategy: nullableString(256), engine: nullableString(256), reason_codes: { type: "array", items: { type: "string", minLength: 1, maxLength: 256 }, maxItems: 64, uniqueItems: true }, latency_ms: { type: ["number", "null"], minimum: 0 }, ttft_ms: { type: ["number", "null"], minimum: 0 }, tokens: usageSchema, response_cost_usd: { type: ["number", "null"], minimum: 0 }, cost_kind: { enum: ["actual", "estimated", "unavailable", null] }, fallback_attempts: { type: "integer", minimum: 0 }, cache_hit: { type: ["boolean", "null"] }, request_id: nullableString(512), version: nullableString(64) })] };
const receiptProperties = { format: { const: RUNTIME_FORMATS.generationReceipt }, formatVersion: { const: RUNTIME_FORMAT_VERSION }, sessionId: id, cardPackageSha256: sha, playerSetupSha256: sha, generationId: id, exchangeId: id, revision, evidenceKind: { enum: ["mock", "real"] }, status: { enum: ["succeeded", "cancelled", "failed", "interrupted"] }, outcome: id, requestedModel: { type: "string", minLength: 1, maxLength: 512 }, observedModel: nullableString(512), serverReceipt: serverReceiptSchema, cancellation: cancellationSchema, outputSha256: { oneOf: [sha, { type: "null" }] }, usage: usageSchema, costUsd: { type: "null" } };
export const GENERATION_RECEIPT_SCHEMA = schema(strict(Object.keys(receiptProperties), receiptProperties));
const authorizationProperties = { format: { const: RUNTIME_FORMATS.pluginAuthorization }, formatVersion: { const: RUNTIME_FORMAT_VERSION }, sessionId: id, cardPackageSha256: sha, playerSetupSha256: sha, revision, evidenceKind: { enum: ["mock", "real"] }, action: { enum: ["authorize", "revoke"] }, pluginId: id, version, manifestSha256: sha, artifactSha256: sha, permissions: { type: "array", items: { enum: PLUGIN_PERMISSIONS }, uniqueItems: true, maxItems: PLUGIN_PERMISSIONS.length }, read: { type: "array", items: { enum: PLUGIN_DATA_READ_SCOPES }, uniqueItems: true, maxItems: PLUGIN_DATA_READ_SCOPES.length }, propose: { type: "array", items: { enum: PLUGIN_DATA_PROPOSAL_SCOPES }, uniqueItems: true, maxItems: PLUGIN_DATA_PROPOSAL_SCOPES.length }, settings: { type: "array", maxItems: 256, items: strict(["key", "value"], { key: id, value: { oneOf: [{ type: "boolean" }, { type: "integer" }, { type: "string", maxLength: 4096 }] } }) } };
export const PLUGIN_AUTHORIZATION_SCHEMA = schema(strict(Object.keys(authorizationProperties), authorizationProperties));
const stateItemSchema = strict(["fieldRef", "value"], { fieldRef: id, value: { oneOf: [{ type: "boolean" }, { type: "integer" }, { type: "string", maxLength: 4096 }] } });
const exchangeRef = { $ref: TURN_EXCHANGE_SCHEMA.$id };
const turnSchema = strict(["generationId", "exchange", "committedRevision", "acceptedStateFields"], { generationId: id, exchange: exchangeRef, committedRevision: positiveRevision, acceptedStateFields: { type: "array", items: id, uniqueItems: true, maxItems: 1024 } });
const generationSchema = strict(["generationId", "exchangeId", "inputSha256", "modelId", "evidenceKind", "status", "requestRevision", "startedRevision", "draftText"], { generationId: id, exchangeId: id, inputSha256: sha, modelId: { type: "string", minLength: 1, maxLength: 512 }, evidenceKind: { enum: ["mock", "real"] }, status: { enum: ["active", "pending", "committed", "discarded", "cancelled", "failed", "interrupted"] }, requestRevision: revision, startedRevision: positiveRevision, cancelRequestedRevision: positiveRevision, finishedRevision: positiveRevision, resolvedRevision: positiveRevision, draftText: { type: "string", maxLength: 1048576 }, exchange: exchangeRef, receipt: GENERATION_RECEIPT_SCHEMA });
export const RUNTIME_SESSION_SCHEMA = schema(strict(["format", "formatVersion", "sessionId", "resources", "revision", "state", "turns", "generations", "pending", "pluginAuthorizations"], { format: { const: RUNTIME_FORMATS.session }, formatVersion: { const: RUNTIME_FORMAT_VERSION }, sessionId: id, resources: resourcesSchema, revision, state: { type: "array", items: stateItemSchema, maxItems: 1024 }, turns: { type: "array", items: turnSchema, maxItems: 100000 }, generations: { type: "array", items: generationSchema, maxItems: 100000 }, pending: { oneOf: [{ type: "null" }, strict(["generationId", "exchangeId"], { generationId: id, exchangeId: id })] }, pluginAuthorizations: { type: "array", items: PLUGIN_AUTHORIZATION_SCHEMA, maxItems: 4096 } }));
export const CREATE_SESSION_REQUEST_SCHEMA = schema(strict(["sessionId", "cardPackage", "playerSetup"], { sessionId: id, cardPackage: { type: "object" }, playerSetup: { type: "object" }, pluginAuthorizations: { type: "array", items: PLUGIN_AUTHORIZATION_SCHEMA, maxItems: 256 } }));
export const SET_PLUGIN_AUTHORIZATION_REQUEST_SCHEMA = schema(strict(["sessionId", "expectedRevision", "authorization"], { sessionId: id, expectedRevision: revision, authorization: PLUGIN_AUTHORIZATION_SCHEMA }));
export const GENERATE_TURN_REQUEST_SCHEMA = schema(strict(["sessionId", "generationId", "exchangeId", "expectedRevision", "input", "messages", "modelId", "settings"], { sessionId: id, generationId: id, exchangeId: id, expectedRevision: revision, input: turnInputSchema, messages: { type: "array", items: messageSchema, minItems: 1, maxItems: 80 }, modelId: { type: "string", minLength: 1, maxLength: 512 }, settings: settingsSchema }));
const pendingRequestProperties = { sessionId: id, generationId: id, exchangeId: id, expectedRevision: revision };
export const COMMIT_TURN_REQUEST_SCHEMA = schema(strict(["format", "formatVersion", ...Object.keys(pendingRequestProperties), "acceptedStateFields"], { format: { const: RUNTIME_FORMATS.turnCommit }, formatVersion: { const: RUNTIME_FORMAT_VERSION }, ...pendingRequestProperties, acceptedStateFields: { type: "array", items: id, uniqueItems: true, maxItems: 1024 } }));
export const DISCARD_TURN_REQUEST_SCHEMA = schema(strict(Object.keys(pendingRequestProperties), pendingRequestProperties));
export const CANCEL_GENERATION_REQUEST_SCHEMA = schema(strict(["sessionId", "generationId", "expectedRevision"], { sessionId: id, generationId: id, expectedRevision: revision }));
const eventCommon = { format: { const: RUNTIME_FORMATS.event }, formatVersion: { const: RUNTIME_FORMAT_VERSION }, sessionId: id, cardPackageSha256: sha, playerSetupSha256: sha, generationId: id, exchangeId: id, revision, evidenceKind: { enum: ["mock", "real"] }, seq: { type: "integer", minimum: 0 } };
export const RUNTIME_EVENT_SCHEMA = schema({ oneOf: [strict([...Object.keys(eventCommon), "type", "text"], { ...eventCommon, type: { const: "draft" }, text: { type: "string", minLength: 1, maxLength: 65536 } }), strict([...Object.keys(eventCommon), "type", "status"], { ...eventCommon, type: { const: "status" }, status: { enum: ["active", "pending", "committed", "discarded", "cancelled", "failed", "interrupted"] } }), strict([...Object.keys(eventCommon), "type", "receipt"], { ...eventCommon, type: { const: "receipt" }, receipt: GENERATION_RECEIPT_SCHEMA })] });
export const RUNTIME_SCHEMAS = Object.freeze({ session: RUNTIME_SESSION_SCHEMA, event: RUNTIME_EVENT_SCHEMA, generationReceipt: GENERATION_RECEIPT_SCHEMA, pluginAuthorization: PLUGIN_AUTHORIZATION_SCHEMA, createSessionRequest: CREATE_SESSION_REQUEST_SCHEMA, setPluginAuthorizationRequest: SET_PLUGIN_AUTHORIZATION_REQUEST_SCHEMA, generateTurnRequest: GENERATE_TURN_REQUEST_SCHEMA, commitTurnRequest: COMMIT_TURN_REQUEST_SCHEMA, discardTurnRequest: DISCARD_TURN_REQUEST_SCHEMA, cancelGenerationRequest: CANCEL_GENERATION_REQUEST_SCHEMA });

const ajv = new Ajv2020({ allErrors: true, strict: true });
ajv.addSchema(TURN_EXCHANGE_SCHEMA);
const validators = new Map(Object.entries(RUNTIME_SCHEMAS).map(([name, schema]) => [name, ajv.compile(schema)]));
const diagnostic = (phase, code, path = "") => Object.freeze({ phase, severity: "error", code, path });
const finish = (items) => Object.freeze({ valid: items.length === 0, diagnostics: Object.freeze([...new Map(items.map((entry) => [entry.phase + "\u0000" + entry.code, entry])).values()].sort((a, b) => (a.phase + a.code).localeCompare(b.phase + b.code))) });
function inspectJson(value, limits = {}) {
  const maxDepth = limits.maxDepth ?? 48, maxNodes = limits.maxNodes ?? 50000, seen = new Set(), diagnostics = []; let nodes = 0, stopped = false;
  function visit(current, depth) {
    if (stopped) return;
    nodes += 1; if (nodes > maxNodes) { diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_NODE_LIMIT")); stopped = true; return; }
    if (depth > maxDepth) { diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_DEPTH_LIMIT")); stopped = true; return; }
    if (current === null || typeof current === "string" || typeof current === "boolean") return;
    if (typeof current === "number") { if (!Number.isFinite(current)) diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_NUMBER")); return; }
    if (typeof current !== "object") { diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_VALUE")); return; }
    if (seen.has(current)) { diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_CYCLE")); return; } seen.add(current);
    const prototype = Object.getPrototypeOf(current);
    if (Array.isArray(current) ? prototype !== Array.prototype : prototype !== Object.prototype && prototype !== null) diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_PROTOTYPE"));
    if (Array.isArray(current) && current.length > maxNodes - nodes) { diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_NODE_LIMIT")); stopped = true; seen.delete(current); return; }
    if (Object.getOwnPropertySymbols(current).length) diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_SYMBOL"));
    const names = Object.getOwnPropertyNames(current).sort();
    if (Array.isArray(current)) { const actual = names.filter((key) => key !== "length"); let shapeValid = actual.length === current.length; for (let index = 0; shapeValid && index < current.length; index += 1) shapeValid = Object.hasOwn(current, index); if (!shapeValid) diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_ARRAY_SHAPE")); }
    for (const key of names) { if (stopped) break; if (Array.isArray(current) && key === "length") continue; const descriptor = Object.getOwnPropertyDescriptor(current, key); if (!descriptor || "get" in descriptor || "set" in descriptor) { diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_ACCESSOR")); continue; } if (!descriptor.enumerable) { diagnostics.push(diagnostic("preflight", "RUNTIME_JSON_NON_ENUMERABLE")); continue; } visit(descriptor.value, depth + 1); }
    seen.delete(current);
  }
  visit(value, 0); return finish(diagnostics);
}
export function canonicalJson(value, limits) {
  const report = inspectJson(value, limits); if (!report.valid) return report;
  function encode(current) { if (current === null || typeof current !== "object") return JSON.stringify(current); if (Array.isArray(current)) return "[" + current.map(encode).join(",") + "]"; return "{" + Object.keys(current).sort().map((key) => JSON.stringify(key) + ":" + encode(Object.getOwnPropertyDescriptor(current, key).value)).join(",") + "}"; }
  return Object.freeze({ valid: true, diagnostics: Object.freeze([]), value: encode(value) });
}
function validateNamed(name, value) { const preflight = inspectJson(value); if (!preflight.valid) return preflight; return validators.get(name)(value) ? finish([]) : finish([diagnostic("schema", "RUNTIME_" + name.toUpperCase() + "_SCHEMA")]); }
export function validateCreateSessionRequest(value) {
  const report = validateNamed("createSessionRequest", value); if (!report.valid) return report;
  if (!validateCardPackage(value.cardPackage).valid || !validatePlayerSetup(value.playerSetup, value.cardPackage).valid) return finish([diagnostic("reference", "RUNTIME_CREATE_RESOURCES_INVALID")]);
  const seen = new Set();
  for (const record of value.pluginAuthorizations ?? []) {
    if (!validatePluginAuthorization(record).valid || record.action !== "authorize" || record.revision !== 0 || record.sessionId !== value.sessionId || seen.has(record.pluginId)) return finish([diagnostic("reference", "RUNTIME_INITIAL_AUTHORIZATION_INVALID")]);
    seen.add(record.pluginId);
  }
  return report;
}
export function validateSetPluginAuthorizationRequest(value) { const report = validateNamed("setPluginAuthorizationRequest", value); return report.valid ? validatePluginAuthorization(value.authorization) : report; }
export function validateGenerateTurnRequest(value) { const report = validateNamed("generateTurnRequest", value); if (!report.valid) return report; const total = value.messages.reduce((sum, message) => sum + message.content.length, 0); return total <= 262144 ? report : finish([diagnostic("policy", "RUNTIME_MESSAGES_TOTAL_LIMIT")]); }
export const validateCommitTurnRequest = (value) => validateNamed("commitTurnRequest", value);
export const validateDiscardTurnRequest = (value) => validateNamed("discardTurnRequest", value);
export const validateCancelGenerationRequest = (value) => validateNamed("cancelGenerationRequest", value);
export function validateGenerationReceipt(value, proposal = null, hash = null) {
  const report = validateNamed("generationReceipt", value); if (!report.valid) return report; const errors = [];
  if (value.status === "succeeded" && (value.outputSha256 === null || value.cancellation.clientAborted || value.cancellation.upstreamConfirmed === true)) errors.push(diagnostic("policy", "RUNTIME_RECEIPT_SUCCESS_INCONSISTENT"));
  if (value.status !== "succeeded" && value.outputSha256 !== null) errors.push(diagnostic("policy", "RUNTIME_RECEIPT_FAILURE_HAS_OUTPUT"));
  if (value.status === "cancelled" && !value.cancellation.requested) errors.push(diagnostic("policy", "RUNTIME_RECEIPT_CANCEL_INCONSISTENT"));
  if (value.status === "succeeded" && value.observedModel !== null && value.observedModel !== value.requestedModel) errors.push(diagnostic("reference", "RUNTIME_RECEIPT_OBSERVED_MODEL_MISMATCH"));
  if (value.serverReceipt !== null && value.serverReceipt.requested_model !== value.requestedModel) errors.push(diagnostic("reference", "RUNTIME_RECEIPT_REQUESTED_MODEL_MISMATCH"));
  if (proposal !== null || hash !== null) { const output = proposal === null || hash === null ? null : canonicalSha256(proposal, hash); if (!output?.valid || value.status === "succeeded" && (value.outputSha256 === null || output.value !== value.outputSha256.toLowerCase())) errors.push(diagnostic("reference", "RUNTIME_RECEIPT_OUTPUT_HASH_MISMATCH")); }
  return finish(errors);
}
export function validateRuntimeEvent(value) { const report = validateNamed("event", value); if (!report.valid) return report; if (value.type === "receipt") { const receipt = validateGenerationReceipt(value.receipt); if (!receipt.valid) return receipt; if (value.sessionId !== value.receipt.sessionId || value.generationId !== value.receipt.generationId || value.exchangeId !== value.receipt.exchangeId || value.revision !== value.receipt.revision || value.evidenceKind !== value.receipt.evidenceKind || value.cardPackageSha256 !== value.receipt.cardPackageSha256 || value.playerSetupSha256 !== value.receipt.playerSetupSha256) return finish([diagnostic("reference", "RUNTIME_EVENT_RECEIPT_BINDING")]); } return finish([]); }
export function validatePluginAuthorization(value) {
  const report = validateNamed("pluginAuthorization", value); if (!report.valid) return report;
  if (new Set(value.settings.map((entry) => entry.key)).size !== value.settings.length) return finish([diagnostic("reference", "RUNTIME_AUTHORIZATION_SETTING_DUPLICATE")]);
  if (value.action === "revoke" && [value.permissions, value.read, value.propose, value.settings].some((items) => items.length)) return finish([diagnostic("policy", "RUNTIME_REVOCATION_NONEMPTY")]);
  return report;
}
function valueMatches(field, value) { if (field.valueType === "boolean") return typeof value === "boolean"; if (field.valueType === "integer") return Number.isInteger(value) && (field.minimum === undefined || value >= field.minimum) && (field.maximum === undefined || value <= field.maximum); if (field.valueType === "shortText") return typeof value === "string" && value.length <= field.maxLength; return field.valueType === "enum" && typeof value === "string" && field.choices.includes(value); }
export function validateRuntimeSession(value, cardPackage = null, playerSetup = null, hash = null) {
  const structure = validateNamed("session", value); if (!structure.valid) return structure;
  const errors = [], generations = new Map(), exchanges = new Set(), turnsByGeneration = new Map();
  for (const generation of value.generations) {
    if (generations.has(generation.generationId)) errors.push(diagnostic("reference", "RUNTIME_GENERATION_ID_DUPLICATE")); else generations.set(generation.generationId, generation);
    if (exchanges.has(generation.exchangeId)) errors.push(diagnostic("reference", "RUNTIME_EXCHANGE_ID_DUPLICATE")); else exchanges.add(generation.exchangeId);
    if (generation.startedRevision !== generation.requestRevision + 1 || generation.startedRevision > value.revision) errors.push(diagnostic("reference", "RUNTIME_GENERATION_REVISION"));
    const terminal = generation.status !== "active", hasFinished = Object.hasOwn(generation, "finishedRevision"), resolved = ["committed", "discarded"].includes(generation.status), hasResolved = Object.hasOwn(generation, "resolvedRevision");
    if (terminal !== hasFinished || hasFinished && (generation.finishedRevision <= generation.startedRevision || generation.finishedRevision > value.revision)) errors.push(diagnostic("reference", "RUNTIME_GENERATION_FINISH_REVISION"));
    if (resolved !== hasResolved || hasResolved && (generation.resolvedRevision <= generation.finishedRevision || generation.resolvedRevision > value.revision)) errors.push(diagnostic("reference", "RUNTIME_GENERATION_RESOLVED_REVISION"));
    if (Object.hasOwn(generation, "cancelRequestedRevision") && (generation.cancelRequestedRevision <= generation.startedRevision || generation.cancelRequestedRevision > value.revision || hasFinished && generation.cancelRequestedRevision >= generation.finishedRevision || ["pending", "committed", "discarded"].includes(generation.status) || generation.receipt && generation.receipt.cancellation.requested !== true)) errors.push(diagnostic("reference", "RUNTIME_GENERATION_CANCEL_REVISION"));
    const hasExchange = Object.hasOwn(generation, "exchange"), hasReceipt = Object.hasOwn(generation, "receipt");
    if (hasExchange && generation.exchange.exchangeId !== generation.exchangeId) errors.push(diagnostic("reference", "RUNTIME_GENERATION_EXCHANGE_BINDING"));
    if (["pending", "committed", "discarded"].includes(generation.status) && (!hasExchange || !hasReceipt || generation.draftText !== "" || generation.receipt?.status !== "succeeded")) errors.push(diagnostic("policy", "RUNTIME_GENERATION_SUCCESS_SHAPE"));
    if (["cancelled", "failed", "interrupted"].includes(generation.status) && (hasExchange || !hasReceipt)) errors.push(diagnostic("policy", "RUNTIME_GENERATION_FAILURE_SHAPE"));
    if (generation.status === "active" && (hasExchange || hasReceipt || hasFinished)) errors.push(diagnostic("policy", "RUNTIME_GENERATION_ACTIVE_SHAPE"));
    if (["cancelled", "failed", "interrupted"].includes(generation.status) && hasReceipt && generation.receipt.status !== generation.status) errors.push(diagnostic("policy", "RUNTIME_GENERATION_FAILURE_STATUS"));
    if (hasReceipt) { const verifyOutput = hasExchange && typeof hash === "function"; const receipt = validateGenerationReceipt(generation.receipt, verifyOutput ? generation.exchange.proposal : null, verifyOutput ? hash : null); if (!receipt.valid) errors.push(...receipt.diagnostics); if (generation.receipt.sessionId !== value.sessionId || generation.receipt.generationId !== generation.generationId || generation.receipt.exchangeId !== generation.exchangeId || generation.receipt.cardPackageSha256 !== value.resources.cardPackage.sha256 || generation.receipt.playerSetupSha256 !== value.resources.playerSetup.sha256 || generation.receipt.revision !== generation.finishedRevision || generation.receipt.evidenceKind !== generation.evidenceKind || generation.receipt.requestedModel !== generation.modelId) errors.push(diagnostic("reference", "RUNTIME_GENERATION_RECEIPT_BINDING")); }
  }
  const activeCount = value.generations.filter((entry) => entry.status === "active").length;
  if (activeCount > 1) errors.push(diagnostic("policy", "RUNTIME_ACTIVE_GENERATION_MULTIPLE"));
  if (activeCount && value.pending !== null) errors.push(diagnostic("policy", "RUNTIME_ACTIVE_WITH_PENDING"));
  if (value.pending !== null) { const generation = generations.get(value.pending.generationId); if (!generation || generation.exchangeId !== value.pending.exchangeId || generation.status !== "pending") errors.push(diagnostic("reference", "RUNTIME_PENDING_REFERENCE")); }
  if (value.generations.filter((entry) => entry.status === "pending").length !== (value.pending === null ? 0 : 1)) errors.push(diagnostic("reference", "RUNTIME_PENDING_CARDINALITY"));
  let lastRevision = 0;
  for (const turn of value.turns) {
    if (turnsByGeneration.has(turn.generationId)) errors.push(diagnostic("reference", "RUNTIME_TURN_GENERATION_DUPLICATE")); else turnsByGeneration.set(turn.generationId, turn);
    const generation = generations.get(turn.generationId), generationExchange = generation?.exchange ? canonicalJson(generation.exchange) : null, turnExchange = canonicalJson(turn.exchange);
    if (!generation || generation.status !== "committed" || generation.exchangeId !== turn.exchange.exchangeId || !generationExchange?.valid || !turnExchange.valid || generationExchange.value !== turnExchange.value || turn.committedRevision !== generation.resolvedRevision) errors.push(diagnostic("reference", "RUNTIME_TURN_GENERATION_BINDING"));
    if (turn.committedRevision <= lastRevision || turn.committedRevision > value.revision) errors.push(diagnostic("reference", "RUNTIME_TURN_REVISION_ORDER")); lastRevision = turn.committedRevision;
    const proposed = new Set((turn.exchange.proposal?.stateProposals ?? []).map((entry) => entry.fieldRef)); if (turn.acceptedStateFields.some((fieldRef) => !proposed.has(fieldRef)) || turn.exchange.input?.kind === "query" && turn.acceptedStateFields.length) errors.push(diagnostic("policy", "RUNTIME_TURN_ACCEPTED_STATE_INVALID"));
  }
  for (const generation of value.generations) { const count = value.turns.filter((turn) => turn.generationId === generation.generationId).length; if (generation.status === "committed" ? count !== 1 : count !== 0) errors.push(diagnostic("reference", "RUNTIME_GENERATION_TURN_CARDINALITY")); }
  const latestAuthorizations = new Map(); let lastAuthorizationRevision = -1;
  for (const authorization of value.pluginAuthorizations) {
    if (!validatePluginAuthorization(authorization).valid || authorization.sessionId !== value.sessionId || authorization.cardPackageSha256 !== value.resources.cardPackage.sha256 || authorization.playerSetupSha256 !== value.resources.playerSetup.sha256 || authorization.revision > value.revision) errors.push(diagnostic("reference", "RUNTIME_PLUGIN_AUTHORIZATION_BINDING"));
    if (authorization.revision < lastAuthorizationRevision || latestAuthorizations.has(authorization.pluginId) && authorization.revision <= latestAuthorizations.get(authorization.pluginId).revision || authorization.revision === 0 && authorization.action !== "authorize") errors.push(diagnostic("reference", "RUNTIME_PLUGIN_AUTHORIZATION_ORDER"));
    const previous = latestAuthorizations.get(authorization.pluginId);
    if (authorization.action === "revoke" && (!previous || previous.action !== "authorize" || ["version", "manifestSha256", "artifactSha256", "evidenceKind"].some((key) => authorization[key] !== previous[key]))) errors.push(diagnostic("reference", "RUNTIME_REVOCATION_BINDING"));
    latestAuthorizations.set(authorization.pluginId, authorization); lastAuthorizationRevision = authorization.revision;
  }
  if (cardPackage !== null || playerSetup !== null) {
    if (cardPackage === null || playerSetup === null || !validateCardPackage(cardPackage).valid || !validatePlayerSetup(playerSetup, cardPackage).valid) errors.push(diagnostic("reference", "RUNTIME_RESOURCE_INVALID"));
    else {
      if (value.resources.cardPackage.id !== cardPackage.package.id || value.resources.cardPackage.version !== cardPackage.package.version || value.resources.playerSetup.setupId !== playerSetup.setupId) errors.push(diagnostic("reference", "RUNTIME_RESOURCE_REFERENCE"));
      const fields = new Map(cardPackage.stateFields.map((field) => [field.id, field])), stateRefs = new Set();
      for (const entry of value.state) { if (stateRefs.has(entry.fieldRef)) errors.push(diagnostic("reference", "RUNTIME_STATE_FIELD_DUPLICATE")); stateRefs.add(entry.fieldRef); const field = fields.get(entry.fieldRef); if (!field || !valueMatches(field, entry.value)) errors.push(diagnostic("policy", "RUNTIME_STATE_VALUE_INVALID")); }
      if (stateRefs.size !== fields.size || [...fields.keys()].some((key) => !stateRefs.has(key))) errors.push(diagnostic("reference", "RUNTIME_STATE_FIELDS_INCOMPLETE"));
      const replay = new Map(cardPackage.stateFields.map((field) => [field.id, field.initialValue])); for (const turn of value.turns) { const accepted = new Set(turn.acceptedStateFields); for (const proposal of turn.exchange.proposal.stateProposals) if (accepted.has(proposal.fieldRef)) replay.set(proposal.fieldRef, proposal.proposedValue); }
      if (value.state.some((entry) => replay.get(entry.fieldRef) !== entry.value)) errors.push(diagnostic("reference", "RUNTIME_STATE_REPLAY_MISMATCH"));
      for (const generation of value.generations) if (generation.exchange && !validateTurnExchange(generation.exchange, cardPackage).valid) errors.push(diagnostic("reference", "RUNTIME_GENERATION_EXCHANGE_INVALID"));
      if (hash !== null && !validateRuntimeResourceBindings(cardPackage, playerSetup, value.resources, hash).valid) errors.push(diagnostic("reference", "RUNTIME_RESOURCE_HASH_MISMATCH"));
    }
  }
  return finish(errors);
}
function canonicalSha256(value, hash) {
  if (typeof hash !== "function") return finish([diagnostic("preflight", "RUNTIME_HASH_ARGUMENT")]); const canonical = canonicalJson(value); if (!canonical.valid) return canonical;
  let output; try { output = hash(canonical.value); } catch { return finish([diagnostic("preflight", "RUNTIME_HASH_FAILED")]); }
  if (output && typeof output.then === "function") return finish([diagnostic("preflight", "RUNTIME_HASH_ASYNC")]); if (typeof output !== "string" || !new RegExp(SHA_PATTERN, "u").test(output)) return finish([diagnostic("preflight", "RUNTIME_HASH_RESULT")]);
  return Object.freeze({ valid: true, diagnostics: Object.freeze([]), value: output.toLowerCase() });
}
export function validateRuntimeResourceBindings(cardPackage, playerSetup, resources, hash) {
  if (!inspectJson(resources).valid || !validateCardPackage(cardPackage).valid || !validatePlayerSetup(playerSetup, cardPackage).valid || resources?.cardPackage?.id !== cardPackage.package.id || resources?.cardPackage?.version !== cardPackage.package.version || resources?.playerSetup?.setupId !== playerSetup.setupId || typeof resources.cardPackage.sha256 !== "string" || typeof resources.playerSetup.sha256 !== "string") return finish([diagnostic("reference", "RUNTIME_RESOURCE_BINDING_INVALID")]);
  const card = canonicalSha256(cardPackage, hash), player = canonicalSha256(playerSetup, hash); if (!card.valid || !player.valid) return finish([diagnostic("preflight", "RUNTIME_RESOURCE_HASH_FAILED")]);
  return card.value === resources.cardPackage.sha256.toLowerCase() && player.value === resources.playerSetup.sha256.toLowerCase() ? finish([]) : finish([diagnostic("reference", "RUNTIME_RESOURCE_HASH_MISMATCH")]);
}
export const computeProposalSha256 = (proposal, hash) => canonicalSha256(proposal, hash);
export function validateModelProposal(proposal, exchangeId, input, cardPackage) {
  const preflight = inspectJson(proposal); if (!preflight.valid) return preflight; if (!validateCardPackage(cardPackage).valid) return finish([diagnostic("reference", "RUNTIME_MODEL_CARD_INVALID")]); const keys = proposal && typeof proposal === "object" && !Array.isArray(proposal) ? Object.keys(proposal).sort() : [];
  if (JSON.stringify(keys) !== JSON.stringify(["informationModules", "narrative", "stateProposals", "suggestedActions", "uncertainties"])) return finish([diagnostic("schema", "RUNTIME_MODEL_PROPOSAL_KEYS")]);
  const exchange = { format: "modelmirror.ai-rpg.turn-exchange", formatVersion: "0.1.0", exchangeId, cardPackageRef: { id: cardPackage.package.id, version: cardPackage.package.version }, input, proposal }; const report = validateTurnExchange(exchange, cardPackage);
  return report.valid ? Object.freeze({ valid: true, diagnostics: Object.freeze([]), value: exchange }) : finish([diagnostic("schema", "RUNTIME_MODEL_PROPOSAL_INVALID")]);
}
export function computeGenerationInputSha256(request, session, hash) {
  const requestReport = validateGenerateTurnRequest(request), sessionReport = validateRuntimeSession(session); if (!requestReport.valid || !sessionReport.valid || request.sessionId !== session.sessionId || typeof hash !== "function") return finish([diagnostic("preflight", "RUNTIME_INPUT_HASH_ARGUMENT")]);
  const canonical = canonicalJson({ sessionId: request.sessionId, resources: session.resources, exchangeId: request.exchangeId, input: request.input, messages: request.messages, modelId: request.modelId, settings: request.settings }); if (!canonical.valid) return canonical;
  return canonicalSha256(JSON.parse(canonical.value), hash);
}
