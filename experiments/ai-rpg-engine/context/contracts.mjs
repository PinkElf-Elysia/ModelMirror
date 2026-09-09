import { validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { canonicalJson, validateRuntimeSession, validateGenerateTurnRequest, computeGenerationInputSha256 } from "../runtime/contracts.mjs";
import { validateContextStructure } from "./schemas.mjs";
const fail = (code, path = "", phase = "reference") => Object.freeze({ valid: false, diagnostics: Object.freeze([Object.freeze({ phase, severity: "error", code, path })]) });
const ok = () => Object.freeze({ valid: true, diagnostics: Object.freeze([]) });
const same = (a, b) => { const x = canonicalJson(a), y = canonicalJson(b); return x.valid && y.valid && x.value === y.value; };
function digest(value, hash) { if (typeof hash !== "function") return null; const c = canonicalJson(value); if (!c.valid) return null; try { const result = hash(c.value); return typeof result === "string" && /^[a-f0-9]{64}$/u.test(result) ? result : null; } catch { return null; } }
const kinds = { world: "worlds", identity: "identities", talent: "talents", item: "items", background: "backgrounds", worldbook: "worldbookEntries" };
const inWorld = (resource, world) => resource && (!resource.worldRefs || resource.worldRefs.length === 0 || resource.worldRefs.includes(world)) && (!resource.worldRef || resource.worldRef === world);
function fieldValue(field, value) { if (!field) return false; if (field.valueType === "boolean") return typeof value === "boolean"; if (field.valueType === "integer") return Number.isSafeInteger(value) && (field.minimum === undefined || value >= field.minimum) && (field.maximum === undefined || value <= field.maximum); if (field.valueType === "shortText") return typeof value === "string" && value.length <= field.maxLength; return field.valueType === "enum" && field.choices.includes(value); }
export function validateContextProfile(profile, card) {
  const shape = validateContextStructure("profile", profile); if (!shape.valid) return shape;
  if (!validateCardPackage(card).valid) return fail("CONTEXT_CARD_INVALID");
  if (profile.cardPackage.id !== card.package.id || profile.cardPackage.version !== card.package.version) return fail("CONTEXT_CARD_VERSION", "/cardPackage");
  if (profile.budget.loreLimit > profile.budget.inputLimit || profile.budget.historyLimit > profile.budget.inputLimit) return fail("CONTEXT_BUDGET_LIMIT", "/budget", "policy");
  const resources = new Map(Object.values(card.resources).flat().map((r) => [r.id, r]));
  const worlds = new Set(card.resources.worlds.map((r) => r.id));
  const scenes = new Map(), rules = new Map();
  for (const scene of profile.scenes) {
    if (scenes.has(scene.id)) return fail("CONTEXT_SCENE_DUPLICATE", "/scenes"); scenes.set(scene.id, scene);
    const opening = card.resources.openings.find((r) => r.id === scene.openingRef);
    if (!worlds.has(scene.worldRef) || !opening || opening.worldRef !== scene.worldRef || scene.requiredResourceRefs.some((r) => !inWorld(resources.get(r), scene.worldRef))) return fail("CONTEXT_SCENE_REFERENCE", "/scenes");
  }
  const aliases = new Map();
  for (const alias of profile.aliases) {
    const resource = card.resources[kinds[alias.kind]].find((r) => r.id === alias.resourceRef);
    if (!resource || alias.worldRef !== null && (!worlds.has(alias.worldRef) || !inWorld(resource, alias.worldRef)) || alias.worldRef === null && resource.worldRefs?.length || alias.kind === "world" && alias.worldRef !== null && alias.resourceRef !== alias.worldRef) return fail("CONTEXT_ALIAS_REFERENCE", "/aliases");
    const key = JSON.stringify([alias.kind, alias.worldRef, alias.text]); if (aliases.has(key)) return fail("CONTEXT_ALIAS_AMBIGUOUS", "/aliases"); aliases.set(key, alias.resourceRef);
    if (alias.worldRef !== null && aliases.has(JSON.stringify([alias.kind, null, alias.text])) && aliases.get(JSON.stringify([alias.kind, null, alias.text])) !== alias.resourceRef) return fail("CONTEXT_ALIAS_AMBIGUOUS", "/aliases");
  }
  for (const alias of profile.aliases) if (alias.worldRef === null && profile.aliases.some((other) => other.kind === alias.kind && other.text === alias.text && other.resourceRef !== alias.resourceRef)) return fail("CONTEXT_ALIAS_AMBIGUOUS", "/aliases");
  for (const rule of profile.loreRules) {
    if (rules.has(rule.entryRef)) return fail("CONTEXT_LORE_DUPLICATE", "/loreRules"); rules.set(rule.entryRef, rule);
    const entry = card.resources.worldbookEntries.find((r) => r.id === rule.entryRef);
    if (!entry || rule.worldRefs.some((w) => !worlds.has(w) || !inWorld(entry, w)) || entry.worldRefs.length && (rule.worldRefs.length === 0 || rule.worldRefs.some((w) => !entry.worldRefs.includes(w)))) return fail("CONTEXT_LORE_REFERENCE", "/loreRules");
    if (entry.visibility === "host" && rule.required) return fail("CONTEXT_HOST_REQUIRED", "/loreRules", "policy");
    if (rule.trigger.sceneRefs.some((r) => !scenes.has(r) || rule.worldRefs.length && !rule.worldRefs.includes(scenes.get(r).worldRef)) || rule.trigger.resourceRefs.some((r) => !resources.has(r))) return fail("CONTEXT_TRIGGER_REFERENCE", "/loreRules");
    for (const condition of rule.trigger.stateConditions) if (!fieldValue(card.stateFields.find((f) => f.id === condition.fieldRef), condition.value)) return fail("CONTEXT_STATE_CONDITION", "/loreRules");
  }
  for (const rule of rules.values()) {
    for (const ref of rule.replaces) if (ref === rule.entryRef || !rules.has(ref) || rule.conflictGroup === null || rules.get(ref).conflictGroup !== rule.conflictGroup) return fail("CONTEXT_REPLACEMENT_REFERENCE", "/loreRules");
  }
  for (const rule of rules.values()) {
    const seen = new Set(), queue = [...rule.replaces]; while (queue.length) { const next = queue.pop(); if (next === rule.entryRef) return fail("CONTEXT_REPLACEMENT_CYCLE", "/loreRules"); if (!seen.has(next)) { seen.add(next); queue.push(...rules.get(next).replaces); } }
  }
  return ok();
}
export function validateContextInput(input, { hash } = {}) {
  const shape = validateContextStructure("input", input); if (!shape.valid) return shape;
  const p = validateContextProfile(input.profile, input.cardPackage); if (!p.valid) return p;
  if (!validatePlayerSetup(input.playerSetup, input.cardPackage).valid || !validateRuntimeSession(input.session, input.cardPackage, input.playerSetup, hash).valid || typeof hash !== "function") return fail("CONTEXT_SESSION_INVALID");
  const cardHash = digest(input.cardPackage, hash); if (!cardHash || cardHash !== input.profile.cardPackage.sha256) return fail("CONTEXT_CARD_HASH", "/profile/cardPackage");
  if (input.expectedRevision !== input.session.revision) return fail("CONTEXT_REVISION", "/expectedRevision");
  if (input.session.pending !== null || input.session.generations.some((g) => g.status === "active")) return fail("CONTEXT_SESSION_BUSY", "/session", "policy");
  const scene = input.profile.scenes.find((s) => s.id === input.sceneRef);
  if (!scene || scene.openingRef !== input.playerSetup.opening.openingRef || input.playerSetup.world.source === "package" && scene.worldRef !== input.playerSetup.world.resourceRef) return fail("CONTEXT_INPUT_SCENE", "/sceneRef");
  const resources = new Map(Object.values(input.cardPackage.resources).flat().map((r) => [r.id, r]));
  if (input.resourceRefs.some((ref) => !inWorld(resources.get(ref), scene.worldRef))) return fail("CONTEXT_INPUT_RESOURCE", "/resourceRefs");
  if (input.input.kind === "command" && !input.cardPackage.resources.commands.some((r) => r.id === input.input.commandRef)) return fail("CONTEXT_INPUT_COMMAND", "/input");
  if (input.settings.maxTokens > input.profile.budget.outputLimit) return fail("CONTEXT_OUTPUT_LIMIT", "/settings", "policy");
  return ok();
}
export function validateContextReceipt(receipt) {
  const shape = validateContextStructure("receipt", receipt); if (!shape.valid) return shape;
  const m = receipt.measurement;
  if (m.method === "utf8_bytes_with_overhead" ? m.accuracy !== "estimate" || m.counterRef !== null : m.counterRef === null) return fail("CONTEXT_MEASUREMENT_METHOD", "/measurement", "policy");
  const total = m.required + m.lore + m.history + m.overhead;
  if (!Number.isSafeInteger(total) || total !== m.input || m.input > m.inputLimit) return fail("CONTEXT_MEASUREMENT_TOTAL", "/measurement", "policy");
  if (new Set(receipt.loreDecisions.map((r) => r.entryRef)).size !== receipt.loreDecisions.length) return fail("CONTEXT_RECEIPT_DUPLICATE", "/loreDecisions");
  return ok();
}
export function validatePreparedTurn(prepared, bindings) {
  const shape = validateContextStructure("preparedTurn", prepared); if (!shape.valid) return shape;
  if (!bindings || typeof bindings !== "object") return fail("CONTEXT_BINDINGS_REQUIRED");
  const { input, hash, hostTemplate } = bindings;
  const validated = validateContextInput(input, { hash }); if (!validated.valid) return validated;
  if (!canonicalJson(hostTemplate).valid || !same(hostTemplate, input.profile.hostTemplate)) return fail("CONTEXT_TEMPLATE_BINDING");
  const expected = { sessionId: input.session.sessionId, revision: input.session.revision, generationId: input.generationId, exchangeId: input.exchangeId, cardPackageSha256: digest(input.cardPackage, hash), playerSetupSha256: digest(input.playerSetup, hash), profileSha256: digest(input.profile, hash), hostTemplateSha256: hostTemplate.sha256 };
  if (Object.values(expected).includes(null) || !same(prepared.bindings, expected) || !same(prepared.receipt.bindings, expected)) return fail("CONTEXT_PREPARED_BINDING", "/bindings");
  const request = prepared.request;
  if (!validateGenerateTurnRequest(request).valid || request.sessionId !== expected.sessionId || request.expectedRevision !== expected.revision || request.generationId !== expected.generationId || request.exchangeId !== expected.exchangeId || request.modelId !== input.modelId || !same(request.input, input.input) || !same(request.settings, input.settings)) return fail("CONTEXT_REQUEST_BINDING", "/request");
  const receipt = validateContextReceipt(prepared.receipt); if (!receipt.valid) return receipt;
  const generationHash = computeGenerationInputSha256(request, input.session, hash);
  if (!generationHash.valid || generationHash.value !== prepared.receipt.generationInputSha256 || digest(request.messages, hash) !== prepared.receipt.messagesSha256) return fail("CONTEXT_MESSAGES_HASH", "/receipt");
  const m = prepared.receipt.measurement, budget = input.profile.budget;
  if (m.inputLimit !== budget.inputLimit || m.outputLimit !== input.settings.maxTokens || m.lore > budget.loreLimit || m.history > budget.historyLimit) return fail("CONTEXT_RECEIPT_BUDGET", "/receipt/measurement");
  const visible = input.cardPackage.resources.worldbookEntries.filter((r) => r.visibility !== "host");
  for (const d of prepared.receipt.loreDecisions) { const entry = visible.find((r) => r.id === d.entryRef); if (!entry || !same([...d.sourceRefs].sort(), [...entry.sourceRefs].sort())) return fail("CONTEXT_RECEIPT_VISIBILITY", "/receipt/loreDecisions"); }
  const publicSources = new Set(Object.entries(input.cardPackage.resources).flatMap(([kind, entries]) => (kind === "worldbookEntries" ? entries.filter((r) => r.visibility !== "host") : entries).flatMap((r) => r.sourceRefs)));
  if (prepared.receipt.sourceRefs.some((r) => !publicSources.has(r))) return fail("CONTEXT_RECEIPT_SOURCE", "/receipt/sourceRefs");
  const committed = input.session.turns.map((t) => t.exchange.exchangeId), included = prepared.receipt.history.includedExchangeIds;
  if (included.length + prepared.receipt.history.excludedTurnCount !== committed.length || !same(included, committed.slice(committed.length - included.length)) || committed.length > 0 && included.length === 0) return fail("CONTEXT_HISTORY_BINDING", "/receipt/history");
  return ok();
}
