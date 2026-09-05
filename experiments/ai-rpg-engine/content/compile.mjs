import { validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { parsePlayerText } from "./player.mjs";
import { diagnostic, inspectPlainJson, sortDiagnostics, validateCompileInputSchema, validateContentIndexSchema, validateConversionReceiptSchema } from "./schemas.mjs";

const AUTHORED_SOURCE = "source.authored-rpg02";
const collections = { world: "worlds", identity: "identities", talent: "talents" };
const outputKinds = { worlds: "world", identities: "identity", talents: "talent", items: "item", backgrounds: "background", styles: "style", worldbookEntries: "worldbookEntry", openings: "opening", informationModules: "informationModule" };

function finish(diagnostics, value) {
  const sorted = sortDiagnostics(diagnostics);
  return sorted.length ? Object.freeze({ valid: false, diagnostics: sorted }) : Object.freeze({ valid: true, diagnostics: sorted, value });
}
function schemaDiagnostics(validator, phase, code) {
  return (validator.errors ?? []).map((error) => diagnostic(phase, code, error.instancePath || ""));
}
function pushUnique(map, key, path, diagnostics, code) {
  if (map.has(key)) diagnostics.push(diagnostic("mapping", code, path, map.get(key))); else map.set(key, path);
}
function extensionsFor(records) {
  const values = {};
  for (const record of records) values[record.stableId] = { locator: record.locator, dataSha256: record.dataSha256, hashVerification: "tooling_required", ...(record.kind === "world" ? { boss: record.data.boss } : {}), ...(record.kind === "talent" ? { color: record.data.color, cost: record.data.cost, sourceType: record.data.type } : {}) };
  return { "modelmirror.source": values, "modelmirror.rank-provenance": "configured", "modelmirror.item-binding": "selected_identity_only" };
}

export function compileContent(input) {
  const diagnostics = [...inspectPlainJson(input, { maxDepth: 48, maxNodes: 20000 })];
  if (diagnostics.length) return finish(diagnostics);
  if (!validateCompileInputSchema(input)) return finish(schemaDiagnostics(validateCompileInputSchema, "input", "COMPILE_INPUT_SCHEMA"));
  const sourceIds = new Map(), recordIds = new Map(), mappings = new Map(), locators = new Map();
  input.sources.forEach((source, index) => pushUnique(sourceIds, source.id, "/sources/" + index + "/id", diagnostics, "SOURCE_ID_DUPLICATE"));
  input.stableIdMap.forEach((mapping, index) => { pushUnique(mappings, mapping.id, "/stableIdMap/" + index + "/id", diagnostics, "STABLE_ID_DUPLICATE"); pushUnique(locators, mapping.kind + "\u0000" + mapping.sourceLocator, "/stableIdMap/" + index + "/sourceLocator", diagnostics, "SOURCE_LOCATOR_DUPLICATE"); });
  input.records.forEach((record, index) => {
    pushUnique(recordIds, record.stableId, "/records/" + index + "/stableId", diagnostics, "RECORD_ID_DUPLICATE");
    const mapping = input.stableIdMap.find((entry) => entry.id === record.stableId);
    if (!mapping || mapping.kind !== record.kind || mapping.sourceLocator !== record.locator || mapping.expectedDataSha256.toLowerCase() !== record.dataSha256.toLowerCase() || JSON.stringify(mapping.aliases) !== JSON.stringify(record.aliases)) diagnostics.push(diagnostic("mapping", "SOURCE_MAPPING_DRIFT", "/records/" + index));
    if (!sourceIds.has(record.sourceRef)) diagnostics.push(diagnostic("reference", "SOURCE_REF_MISSING", "/records/" + index + "/sourceRef"));
  });
  const authoredSource = input.sources.find((source) => source.id === AUTHORED_SOURCE);
  if (!authoredSource) diagnostics.push(diagnostic("reference", "AUTHORED_SOURCE_MISSING", "/sources"));
  else if (authoredSource.kind !== "authored") diagnostics.push(diagnostic("mapping", "AUTHORED_SOURCE_KIND", "/sources"));
  input.stableIdMap.forEach((mapping, index) => { if (!recordIds.has(mapping.id)) diagnostics.push(diagnostic("mapping", "STABLE_ID_RECORD_MISSING", "/stableIdMap/" + index + "/id")); });

  for (const record of input.records) {
    const mapping = mappings.has(record.stableId) ? input.stableIdMap.find((entry) => entry.id === record.stableId) : null;
    if (record.kind === "world") { if (mapping?.worldScope !== null || record.worldName !== record.data.name) diagnostics.push(diagnostic("mapping", "SOURCE_WORLD_SCOPE_DRIFT", "/stableIdMap")); }
    else if (record.worldName === null) { if (mapping?.worldScope !== null) diagnostics.push(diagnostic("mapping", "SOURCE_WORLD_SCOPE_DRIFT", "/stableIdMap")); }
    else {
      const world = input.records.find((entry) => entry.kind === "world" && entry.stableId === mapping?.worldScope);
      if (!world || world.data.name !== record.worldName) diagnostics.push(diagnostic("mapping", "SOURCE_WORLD_SCOPE_DRIFT", "/stableIdMap"));
    }
  }

  const aliases = [];
  input.stableIdMap.forEach((mapping, index) => mapping.aliases.forEach((alias, offset) => aliases.push({ kind: mapping.kind, scope: mapping.worldScope, alias, id: mapping.id, path: "/stableIdMap/" + index + "/aliases/" + offset })));
  function resolve(kind, reference, scope, path) {
    if (mappings.has(reference)) { const mapping = input.stableIdMap.find((entry) => entry.id === reference); if (mapping.kind !== kind) diagnostics.push(diagnostic("reference", "ALIAS_KIND_MISMATCH", path)); else if (mapping.worldScope !== null && mapping.worldScope !== scope) diagnostics.push(diagnostic("reference", "ALIAS_SCOPE_MISMATCH", path)); else return mapping.id; return null; }
    const sameName = aliases.filter((entry) => entry.kind === kind && entry.alias === reference), eligible = sameName.filter((entry) => entry.scope === null || entry.scope === scope);
    if (eligible.length === 1) return eligible[0].id;
    diagnostics.push(diagnostic("reference", eligible.length > 1 ? "ALIAS_AMBIGUOUS" : sameName.length ? "ALIAS_SCOPE_MISMATCH" : "ALIAS_MISSING", path)); return null;
  }
  if (diagnostics.length) return finish(diagnostics);

  const ranks = new Map();
  input.configuredRanks.forEach((entry, index) => {
    if (ranks.has(entry.identityRef)) diagnostics.push(diagnostic("mapping", "CONFIGURED_RANK_DUPLICATE", "/configuredRanks/" + index + "/identityRef"));
    const record = input.records.find((value) => value.stableId === entry.identityRef);
    if (!record) diagnostics.push(diagnostic("reference", "CONFIGURED_RANK_IDENTITY_MISSING", "/configuredRanks/" + index + "/identityRef"));
    else if (record.kind !== "identity") diagnostics.push(diagnostic("reference", "CONFIGURED_RANK_KIND_MISMATCH", "/configuredRanks/" + index + "/identityRef"));
    else ranks.set(entry.identityRef, entry.rankLabel);
  });
  const resources = { worlds: [], identities: [], talents: [], items: [], backgrounds: [], styles: [], worldbookEntries: [], openings: [], informationModules: [], commands: [] };
  for (const record of input.records) {
    const sourceRefs = [record.sourceRef];
    if (record.kind === "world") resources.worlds.push({ id: record.stableId, displayName: record.data.name, sourceRefs, description: record.data.desc });
    else if (record.kind === "identity") {
      const worldRef = mappings.has(record.stableId) ? input.stableIdMap.find((entry) => entry.id === record.stableId).worldScope : null;
      if (!ranks.has(record.stableId)) diagnostics.push(diagnostic("reference", "CONFIGURED_RANK_MISSING", "/configuredRanks"));
      resources.identities.push({ id: record.stableId, displayName: record.data.name, sourceRefs, description: record.data.name, rankLabel: ranks.get(record.stableId) ?? "", worldRefs: worldRef ? [worldRef] : [] });
    } else {
      const worldScope = mappings.has(record.stableId) ? input.stableIdMap.find((entry) => entry.id === record.stableId).worldScope : null;
      const worldRefs = worldScope === null ? [] : [worldScope];
      resources.talents.push({ id: record.stableId, displayName: record.data.name, sourceRefs, description: record.data.desc, tierLabel: record.data.color, worldRefs });
    }
  }
  for (const item of input.items) {
    const identity = input.records.find((entry) => entry.stableId === item.identityRef);
    if (!identity) diagnostics.push(diagnostic("reference", "ITEM_IDENTITY_MISSING", "/items"));
    else if (identity.kind !== "identity") diagnostics.push(diagnostic("reference", "ITEM_IDENTITY_KIND_MISMATCH", "/items"));
    else {
      const supplies = Array.isArray(identity.data.items) ? identity.data.items.join(", ") : identity.data.items;
      if (item.sourceRef !== identity.sourceRef || item.description !== supplies) diagnostics.push(diagnostic("mapping", "ITEM_SOURCE_BINDING_DRIFT", "/items"));
    }
    resources.items.push({ id: item.id, displayName: item.displayName, sourceRefs: [item.sourceRef], description: item.description });
  }
  for (const value of input.authored.backgrounds) resources.backgrounds.push({ id: value.id, displayName: value.displayName, sourceRefs: [AUTHORED_SOURCE], description: value.description });
  for (const value of input.authored.styles) resources.styles.push({ id: value.id, displayName: value.displayName, sourceRefs: [AUTHORED_SOURCE], instruction: value.instruction });
  for (const value of input.authored.worldbookEntries) resources.worldbookEntries.push({ ...structuredClone(value), sourceRefs: [AUTHORED_SOURCE] });
  for (const value of input.authored.informationModules) resources.informationModules.push({ ...structuredClone(value), sourceRefs: [AUTHORED_SOURCE] });
  for (const value of input.authored.openings) resources.openings.push({ ...structuredClone(value), sourceRefs: [AUTHORED_SOURCE], identityRefs: value.identityRefs.map((ref, index) => resolve("identity", ref, value.worldRef, "/authored/openings/identityRefs/" + index)).filter(Boolean), talentRefs: value.talentRefs.map((ref, index) => resolve("talent", ref, value.worldRef, "/authored/openings/talentRefs/" + index)).filter(Boolean) });
  if (diagnostics.length) return finish(diagnostics);

  const cardPackage = { format: "modelmirror.ai-rpg.card-package", formatVersion: "0.1.0", package: structuredClone(input.package), provenance: { rights: [structuredClone(input.rights)], sources: input.sources.map(({ hashStatus, hashConvention, ...source }) => source) }, resources, defaults: structuredClone(input.defaults), stateFields: [], requiredPlugins: [], recommendedPlugins: [], extensions: extensionsFor(input.records) };
  const cardValidation = validateCardPackage(cardPackage);
  if (!cardValidation.valid) return finish(cardValidation.diagnostics.map((entry) => diagnostic("card", "CARD_PACKAGE_INVALID", entry.path, entry.relatedPath)));
  const entries = [];
  for (const [collection, values] of Object.entries(resources)) if (outputKinds[collection]) for (const value of values) entries.push({ id: value.id, kind: outputKinds[collection], sourceRefs: value.sourceRefs });
  const contentIndex = { format: "modelmirror.ai-rpg.content-index", formatVersion: "0.1.0", packageRef: input.package.id, entries };
  const conversionReceipt = { format: "modelmirror.ai-rpg.conversion-receipt", formatVersion: "0.1.0", packageRef: input.package.id, sourceRecordCount: input.records.length, resourceCount: entries.length, recordDataHashConvention: input.recordDataHashConvention, sourceEvidence: input.sources.map((source) => ({ sourceRef: source.id, reference: source.reference, sha256: source.sha256, hashConvention: source.hashConvention })), hashVerification: "tooling_required", losses: [], warnings: ["Identity rankLabel is configured and is not claimed as an extracted identity literal.", "Identity item resources are not granted until a player setup selects one identity."] };
  if (!validateContentIndexSchema(contentIndex) || !validateConversionReceiptSchema(conversionReceipt)) return finish([diagnostic("output", "AUXILIARY_OUTPUT_INVALID", "")]);
  let playerSetup;
  if (input.player !== undefined) {
    const parsed = parsePlayerText(input.player.text);
    if (!parsed.valid) return finish(parsed.diagnostics);
    const draft = parsed.value;
    const worldId = resolve("world", draft.world.name, null, "/player/text/world");
    const identityId = worldId ? resolve("identity", draft.identity.name, worldId, "/player/text/identity") : null;
    const worldRecord = input.records.find((entry) => entry.stableId === worldId), identityRecord = input.records.find((entry) => entry.stableId === identityId);
    if (worldRecord && (worldRecord.data.desc !== draft.world.description || worldRecord.data.boss !== draft.world.boss)) diagnostics.push(diagnostic("player", "PLAYER_WORLD_SOURCE_CONFLICT", "/player/text/world"));
    if (identityRecord) { const supplies = Array.isArray(identityRecord.data.items) ? identityRecord.data.items.join(", ") : identityRecord.data.items; if (supplies !== draft.identity.items) diagnostics.push(diagnostic("player", "PLAYER_IDENTITY_ITEMS_CONFLICT", "/player/text/identity/items")); }
    if (identityId && ranks.get(identityId) !== draft.identity.rankLabel) diagnostics.push(diagnostic("player", "PLAYER_IDENTITY_RANK_CONFLICT", "/player/text/identity/rank"));
    const talentIds = [];
    for (let index = 0; index < draft.talents.length; index++) {
      const talent = draft.talents[index], scope = talent.worldName === "通用" ? worldId : resolve("world", talent.worldName, null, "/player/text/talents/" + index + "/world");
      if (scope !== worldId) { diagnostics.push(diagnostic("player", "PLAYER_TALENT_WORLD_CONFLICT", "/player/text/talents/" + index)); continue; }
      const talentId = resolve("talent", talent.name, worldId, "/player/text/talents/" + index + "/name"), record = input.records.find((entry) => entry.stableId === talentId);
      if (record && (talent.worldName === "通用") !== (record.worldName === null)) diagnostics.push(diagnostic("player", "PLAYER_TALENT_SCOPE_LABEL_CONFLICT", "/player/text/talents/" + index + "/world"));
      else if (record?.worldName !== null && record.worldName !== talent.worldName) diagnostics.push(diagnostic("player", "PLAYER_TALENT_SCOPE_LABEL_CONFLICT", "/player/text/talents/" + index + "/world"));
      if (record && (record.data.desc !== talent.description || record.data.color !== talent.tierLabel)) diagnostics.push(diagnostic("player", "PLAYER_TALENT_SOURCE_CONFLICT", "/player/text/talents/" + index));
      if (talentId) talentIds.push(talentId);
    }
    const boundTalents = new Set(); talentIds.forEach((idValue, index) => { if (boundTalents.has(idValue)) diagnostics.push(diagnostic("player", "PLAYER_TALENT_BINDING_DUPLICATE", "/player/text/talents/" + index)); boundTalents.add(idValue); });
    const opening = resources.openings.find((entry) => entry.id === input.player.openingRef);
    if (!opening) diagnostics.push(diagnostic("player", "PLAYER_OPENING_MISSING", "/player/openingRef"));
    else {
      if (opening.worldRef !== worldId) diagnostics.push(diagnostic("player", "PLAYER_OPENING_WORLD_CONFLICT", "/player/openingRef"));
      if (!opening.identityRefs.includes(identityId)) diagnostics.push(diagnostic("player", "PLAYER_OPENING_IDENTITY_CONFLICT", "/player/openingRef"));
      talentIds.forEach((idValue, index) => { if (!opening.talentRefs.includes(idValue)) diagnostics.push(diagnostic("player", "PLAYER_OPENING_TALENT_CONFLICT", "/player/text/talents/" + index)); });
    }
    const activationMap = new Map();
    input.player.activations.forEach((entry, index) => { if (activationMap.has(entry.talentRef)) diagnostics.push(diagnostic("player", "PLAYER_ACTIVATION_DUPLICATE", "/player/activations/" + index)); else activationMap.set(entry.talentRef, entry.active); });
    talentIds.forEach((idValue) => { if (!activationMap.has(idValue)) diagnostics.push(diagnostic("player", "PLAYER_ACTIVATION_MISSING", "/player/activations")); });
    for (const idValue of activationMap.keys()) if (!talentIds.includes(idValue)) diagnostics.push(diagnostic("player", "PLAYER_ACTIVATION_EXTRA", "/player/activations"));
    const backgroundIds = new Set(resources.backgrounds.map((entry) => entry.id));
    input.player.backgroundRefs.forEach((idValue, index) => { if (!backgroundIds.has(idValue)) diagnostics.push(diagnostic("player", "PLAYER_BACKGROUND_MISSING", "/player/backgroundRefs/" + index)); });
    const kits = input.items.filter((entry) => entry.identityRef === identityId), kit = kits[0];
    if (kits.length === 0) diagnostics.push(diagnostic("player", "PLAYER_IDENTITY_KIT_MISSING", "/player/text/identity"));
    else if (kits.length > 1) diagnostics.push(diagnostic("player", "PLAYER_IDENTITY_KIT_AMBIGUOUS", "/player/text/identity"));
    if (diagnostics.length) return finish(diagnostics);
    playerSetup = {
      format: "modelmirror.ai-rpg.player-setup", formatVersion: "0.1.0", setupId: input.player.setupId,
      cardPackageRef: { id: input.package.id, version: input.package.version },
      character: { name: draft.character.name, gender: draft.character.gender, age: draft.character.age, appearance: draft.character.appearance, personality: draft.character.personality, preferences: draft.character.xpText.split("、"), notes: "XP：" + draft.character.xpText + "\n其他：" + draft.character.otherText },
      opening: { mode: draft.openingMode, openingRef: input.player.openingRef }, world: { source: "package", resourceRef: worldId }, currentIdentity: { source: "package", resourceRef: identityId },
      inherentBackgrounds: [{ source: "custom", resource: { id: "background.player-inherent", kind: "background", displayName: draft.character.otherText, description: draft.character.otherText } }, ...input.player.backgroundRefs.map((resourceRef) => ({ source: "package", resourceRef }))],
      possessions: [{ resource: { source: "package", resourceRef: kit.id }, quantity: 1 }],
      talents: talentIds.map((resourceRef) => ({ resource: { source: "package", resourceRef }, owned: true, active: activationMap.get(resourceRef) })),
      characterPower: { status: "unspecified" }, runtimePermissions: []
    };
    const validation = validatePlayerSetup(playerSetup, cardPackage);
    if (!validation.valid) return finish(validation.diagnostics.map((entry) => diagnostic("player", "PLAYER_SETUP_INVALID", entry.path, entry.relatedPath)));
  }
  return finish([], Object.freeze({ cardPackage, contentIndex, conversionReceipt, ...(playerSetup ? { playerSetup } : {}) }));
}
