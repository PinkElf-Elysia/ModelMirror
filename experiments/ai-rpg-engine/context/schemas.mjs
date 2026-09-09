import Ajv2020 from "ajv/dist/2020.js";
import { CARD_PACKAGE_SCHEMA, PLAYER_SETUP_SCHEMA, TURN_EXCHANGE_SCHEMA } from "../src/index.mjs";
import { RUNTIME_SESSION_SCHEMA, GENERATE_TURN_REQUEST_SCHEMA, canonicalJson } from "../runtime/contracts.mjs";

export const CONTEXT_FORMAT_VERSION = "0.1.0";
export const CONTEXT_FORMATS = Object.freeze({ profile: "modelmirror.ai-rpg.context-profile", input: "modelmirror.ai-rpg.context-input", preparedTurn: "modelmirror.ai-rpg.prepared-turn", receipt: "modelmirror.ai-rpg.context-receipt" });
const id = { type: "string", pattern: "^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$" };
const sha = { type: "string", pattern: "^[a-f0-9]{64}$" };
const count = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER };
const text = (maxLength) => ({ type: "string", minLength: 1, maxLength });
const list = (items, maxItems = 1024) => ({ type: "array", items, maxItems });
const ids = { ...list(id), uniqueItems: true };
const obj = (properties) => ({ type: "object", additionalProperties: false, required: Object.keys(properties), properties });
const value = { oneOf: [{ type: "boolean" }, { type: "integer" }, text(4096)] };
const version = { type: "string", pattern: "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\\+[0-9A-Za-z.-]+)?$" };
const cardBinding = obj({ id, version, sha256: sha });
const profileBinding = obj({ id, version, sha256: sha });
const templateBinding = obj({ id, version, sha256: sha });
const budget = obj({ inputLimit: { type: "integer", minimum: 1, maximum: 262144 }, loreLimit: count, historyLimit: count, outputLimit: { type: "integer", minimum: 1, maximum: 4096 } });
const conditionValue = { oneOf: [{ type: "boolean" }, { type: "integer" }, { type: "string", maxLength: 4096 }] };
const condition = obj({ fieldRef: id, operator: { enum: ["eq", "ne"] }, value: conditionValue });
const trigger = obj({ sceneRefs: ids, resourceRefs: ids, allKeywords: { ...list(text(256), 128), uniqueItems: true }, anyKeywords: { ...list(text(256), 128), uniqueItems: true }, notKeywords: { ...list(text(256), 128), uniqueItems: true }, stateConditions: list(condition, 128) });
const rule = obj({ entryRef: id, worldRefs: ids, required: { type: "boolean" }, priority: { type: "integer", minimum: -100000, maximum: 100000 }, trigger, conflictGroup: { oneOf: [id, { type: "null" }] }, replaces: ids });
const alias = obj({ text: text(256), kind: { enum: ["world", "identity", "talent", "item", "background", "worldbook"] }, worldRef: { oneOf: [id, { type: "null" }] }, resourceRef: id });
const bindings = obj({ sessionId: id, revision: count, generationId: id, exchangeId: id, cardPackageSha256: sha, playerSetupSha256: sha, profileSha256: sha, hostTemplateSha256: sha });
const decision = obj({ entryRef: id, disposition: { enum: ["included", "not_matched", "out_of_scope", "budget_excluded", "replaced"] }, sourceRefs: ids });
const measurement = obj({ method: { enum: ["utf8_bytes_with_overhead", "trusted_token_counter"] }, accuracy: { enum: ["estimate", "exact"] }, counterRef: { oneOf: [id, { type: "null" }] }, input: count, required: count, lore: count, history: count, overhead: count, inputLimit: { type: "integer", minimum: 1, maximum: 262144 }, outputLimit: { type: "integer", minimum: 1, maximum: 4096 } });
function freeze(value) { if (value && typeof value === "object" && !Object.isFrozen(value)) { for (const child of Object.values(value)) freeze(child); Object.freeze(value); } return value; }
function document(format, properties) { return freeze({ $schema: "https://json-schema.org/draft/2020-12/schema", ...obj({ format: { const: format }, formatVersion: { const: CONTEXT_FORMAT_VERSION }, ...properties }) }); }
export const CONTEXT_PROFILE_SCHEMA = document(CONTEXT_FORMATS.profile, { profile: obj({ id, version }), cardPackage: cardBinding, hostTemplate: templateBinding, budget, scenes: list(obj({ id, worldRef: id, openingRef: id, requiredResourceRefs: ids })), aliases: list(alias, 4096), loreRules: list(rule, 16384) });
export const CONTEXT_INPUT_SCHEMA = document(CONTEXT_FORMATS.input, { cardPackage: CARD_PACKAGE_SCHEMA, playerSetup: PLAYER_SETUP_SCHEMA, session: RUNTIME_SESSION_SCHEMA, profile: CONTEXT_PROFILE_SCHEMA, sceneRef: id, resourceRefs: ids, generationId: id, exchangeId: id, expectedRevision: count, input: GENERATE_TURN_REQUEST_SCHEMA.properties.input, modelId: text(512), settings: obj({ temperature: { type: "number", minimum: 0, maximum: 2 }, maxTokens: { type: "integer", minimum: 1, maximum: 4096 } }) });
export const CONTEXT_RECEIPT_SCHEMA = document(CONTEXT_FORMATS.receipt, { bindings, evidenceKind: { enum: ["offline", "mock", "real"] }, compilerVersion: version, messagesSha256: sha, generationInputSha256: sha, measurement, loreDecisions: list(decision, 16384), history: obj({ includedExchangeIds: ids, excludedTurnCount: count }), sourceRefs: ids });
export const PREPARED_TURN_SCHEMA = document(CONTEXT_FORMATS.preparedTurn, { bindings, request: GENERATE_TURN_REQUEST_SCHEMA, receipt: CONTEXT_RECEIPT_SCHEMA });
export const CONTEXT_SCHEMAS = Object.freeze({ profile: CONTEXT_PROFILE_SCHEMA, input: CONTEXT_INPUT_SCHEMA, preparedTurn: PREPARED_TURN_SCHEMA, receipt: CONTEXT_RECEIPT_SCHEMA });
const ajv = new Ajv2020({ strict: true, allErrors: false });
ajv.addSchema(TURN_EXCHANGE_SCHEMA);
const validators = new Map(Object.entries(CONTEXT_SCHEMAS).map(([name, schema]) => [name, ajv.compile(schema)]));
function report(code = null, phase = "schema") { return Object.freeze({ valid: code === null, diagnostics: Object.freeze(code === null ? [] : [Object.freeze({ phase, severity: "error", code, path: "" })]) }); }
// Structural check only: does not authenticate bindings, select lore, prepare messages or authorize dispatch.
export function validateContextStructure(kind, input) {
  if (typeof kind !== "string" || !validators.has(kind)) return report("CONTEXT_STRUCTURE_KIND", "preflight");
  try {
    if (!canonicalJson(input).valid) return report("CONTEXT_JSON_INVALID", "preflight");
    return validators.get(kind)(input) ? report() : report("CONTEXT_" + kind.toUpperCase() + "_SCHEMA");
  } catch { return report("CONTEXT_JSON_INVALID", "preflight"); }
}
