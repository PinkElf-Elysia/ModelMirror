import { validateContextProfile } from "./contracts.mjs";
import { validateContextStructure } from "./schemas.mjs";

const fail = (code, path = "") => Object.freeze({ valid: false, diagnostics: Object.freeze([Object.freeze({ phase: "selection", severity: "error", code, path })]) });
const freeze = (value) => { if (value && typeof value === "object" && !Object.isFrozen(value)) { for (const child of Object.values(value)) freeze(child); Object.freeze(value); } return value; };
const inScope = (rule, worldRef) => rule.worldRefs.length === 0 || rule.worldRefs.includes(worldRef);

const ascii = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const MATCH_OCCURRENCE_LIMIT = 4096;

function matchedAliases(input, worldRef, entries) {
  const occurrences = [];
  for (const alias of input.profile.aliases) {
    if (alias.worldRef !== null && alias.worldRef !== worldRef || entries.get(alias.resourceRef)?.visibility === "host") continue;
    let start = input.input.text.indexOf(alias.text);
    while (start !== -1) {
      if (occurrences.length === MATCH_OCCURRENCE_LIMIT) return { limit: true };
      occurrences.push({ alias, start, end: start + alias.text.length }); start = input.input.text.indexOf(alias.text, start + 1);
    }
  }
  const winners = occurrences.filter((candidate) => !occurrences.some((other) => other.alias.kind === candidate.alias.kind && other.start <= candidate.start && other.end >= candidate.end && other.alias.text.length > candidate.alias.text.length));
  for (const winner of winners) {
    if (winners.some((other) => other !== winner && other.start === winner.start && other.end === winner.end && other.alias.kind === winner.alias.kind && other.alias.resourceRef !== winner.alias.resourceRef)) return { error: true };
  }
  return { refs: new Set(winners.map(({ alias }) => alias.resourceRef)) };
}

function stateMatches(conditions, state) {
  const values = new Map(state.map((item) => [item.fieldRef, item.value]));
  return conditions.every((condition) => values.has(condition.fieldRef) && (condition.operator === "eq" ? values.get(condition.fieldRef) === condition.value : values.get(condition.fieldRef) !== condition.value));
}

function triggerMatch(rule, input, aliasRefs) {
  const trigger = rule.trigger;
  const exactRefs = new Set([...input.resourceRefs, ...aliasRefs]);
  const scene = trigger.sceneRefs.length === 0 || trigger.sceneRefs.includes(input.sceneRef);
  const resources = trigger.resourceRefs.length === 0 || trigger.resourceRefs.some((ref) => exactRefs.has(ref));
  const all = trigger.allKeywords.every((keyword) => input.input.text.includes(keyword));
  const any = trigger.anyKeywords.length === 0 || trigger.anyKeywords.some((keyword) => input.input.text.includes(keyword));
  const not = trigger.notKeywords.every((keyword) => !input.input.text.includes(keyword));
  return { matched: scene && resources && all && any && not && stateMatches(trigger.stateConditions, input.session.state), scene: trigger.sceneRefs.includes(input.sceneRef), direct: trigger.resourceRefs.some((ref) => exactRefs.has(ref)) };
}

export function selectContextLore(input) {
  const shape = validateContextStructure("input", input);
  if (!shape.valid) return shape;
  const hostRefs = new Set(input.cardPackage.resources.worldbookEntries.filter((entry) => entry.visibility === "host").map((entry) => entry.id));
  const publicProfile = { ...input.profile, aliases: input.profile.aliases.filter((alias) => !hostRefs.has(alias.resourceRef)) };
  const profile = validateContextProfile(publicProfile, input.cardPackage);
  if (!profile.valid) return profile;
  const scene = input.profile.scenes.find((candidate) => candidate.id === input.sceneRef);
  if (!scene) return fail("CONTEXT_SELECTION_SCENE", "/sceneRef");
  const allResources = new Map(Object.values(input.cardPackage.resources).flat().map((resource) => [resource.id, resource]));
  for (const ref of scene.requiredResourceRefs) {
    const resource = allResources.get(ref);
    if (!resource || resource.worldRefs?.length && !resource.worldRefs.includes(scene.worldRef) || resource.worldRef && resource.worldRef !== scene.worldRef) return fail("CONTEXT_SELECTION_REQUIRED_SCOPE", "/profile/scenes");
  }
  const entries = new Map(input.cardPackage.resources.worldbookEntries.map((entry) => [entry.id, entry]));
  const aliases = matchedAliases(input, scene.worldRef, entries);
  if (aliases.limit) return fail("CONTEXT_SELECTION_MATCH_LIMIT", "/input/text");
  if (aliases.error) return fail("CONTEXT_SELECTION_ALIAS_AMBIGUOUS", "/input/text");

  const requiredRefs = new Set(scene.requiredResourceRefs.filter((ref) => entries.has(ref)));
  const candidates = [];
  for (const rule of input.profile.loreRules) {
    const entry = entries.get(rule.entryRef);
    if (entry.visibility === "host") continue;
    const scope = inScope(rule, scene.worldRef) && (entry.worldRefs.length === 0 || entry.worldRefs.includes(scene.worldRef));
    const hit = triggerMatch(rule, input, aliases.refs);
    const required = rule.required || requiredRefs.has(rule.entryRef);
    if (required && !scope) return fail("CONTEXT_SELECTION_REQUIRED_SCOPE", "/profile/loreRules");
    if (scope && (required || hit.matched)) candidates.push({ rule, entry, hit, required });
  }
  for (const ref of requiredRefs) if (!candidates.some((candidate) => candidate.rule.entryRef === ref)) return fail("CONTEXT_SELECTION_REQUIRED_UNRESOLVED", "/profile/scenes");

  const selected = new Map(candidates.map((candidate) => [candidate.rule.entryRef, candidate]));
  for (const candidate of candidates) for (const replaced of candidate.rule.replaces) selected.delete(replaced);
  const groups = new Map();
  for (const candidate of selected.values()) if (candidate.rule.conflictGroup !== null) {
    const members = groups.get(candidate.rule.conflictGroup) ?? [];
    members.push(candidate); groups.set(candidate.rule.conflictGroup, members);
  }
  if ([...groups.values()].some((members) => members.length > 1)) return fail("CONTEXT_SELECTION_CONFLICT", "/profile/loreRules");
  for (const candidate of candidates) if (candidate.required && !selected.has(candidate.rule.entryRef)) return fail("CONTEXT_SELECTION_REQUIRED_CONFLICT", "/profile/loreRules");

  const rank = (candidate) => [candidate.hit.scene ? 1 : 0, candidate.hit.direct ? 1 : 0, candidate.rule.worldRefs.length ? 1 : 0, candidate.rule.priority];
  const ordered = [...selected.values()].sort((a, b) => { const ar = rank(a), br = rank(b); for (let i = 0; i < ar.length; i++) if (ar[i] !== br[i]) return br[i] - ar[i]; return ascii(a.rule.entryRef, b.rule.entryRef); });
  const selectedRefs = new Set(ordered.map((candidate) => candidate.rule.entryRef));
  const replacedRefs = new Set(candidates.flatMap((candidate) => candidate.rule.replaces));
  const loreDecisions = input.profile.loreRules.flatMap((rule) => {
    const entry = entries.get(rule.entryRef);
    if (entry.visibility === "host") return [];
    let disposition = "not_matched";
    if (!inScope(rule, scene.worldRef) || entry.worldRefs.length && !entry.worldRefs.includes(scene.worldRef)) disposition = "out_of_scope";
    else if (selectedRefs.has(rule.entryRef)) disposition = "included";
    else if (replacedRefs.has(rule.entryRef)) disposition = "replaced";
    return [{ entryRef: entry.id, disposition, sourceRefs: [...entry.sourceRefs].sort() }];
  }).sort((a, b) => ascii(a.entryRef, b.entryRef));
  const sourceRefs = [...new Set(ordered.flatMap(({ entry }) => entry.sourceRefs))].sort(ascii);
  return freeze({ valid: true, diagnostics: [], value: { selectedEntries: ordered.map(({ entry }) => structuredClone(entry)), loreDecisions, sourceRefs } });
}
