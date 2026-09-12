import Ajv2020 from 'ajv/dist/2020.js';
import { randomUUID, createHash } from 'node:crypto';
import { PLAYER_SETUP_SCHEMA, validateCardPackage, validatePlayerSetup } from '../src/index.mjs';
import { validateContextProfile } from '../context/index.mjs';
import { canonicalJson } from '../runtime/contracts.mjs';
import { loadRpg04ApprovedContextCardFixture } from '../tooling/context-approved-card.mjs';
import { loadRpg04ApprovedHost } from '../tooling/context-host.mjs';
import { parseStrictJson } from '../tooling/source-input.mjs';

const clone = value => structuredClone(value);
const issue = (code, path = '') => ({ code, path, severity: 'error', phase: 'ui-setup' });
const result = diagnostics => ({ valid: diagnostics.length === 0, diagnostics });
export const hashText = text => createHash('sha256').update(text).digest('hex');
export function hashValue(value) {
  const encoded = canonicalJson(value);
  if (!encoded.valid) throw Error('JSON_CANONICAL_INVALID');
  return hashText(encoded.value);
}
const draftSchema = clone(PLAYER_SETUP_SCHEMA);
draftSchema.$id = 'https://modelmirror.local/schemas/ai-rpg/ui-draft/0.5.0';
// Only unfinished character text is relaxed for storage. Launch still uses the frozen schema.
for (const field of Object.values(draftSchema.properties.character.properties)) if (field.type === 'string') field.minLength = 0;
draftSchema.properties.world.oneOf[1].properties.resource.properties.displayName.minLength = 0;
draftSchema.properties.world.oneOf[1].properties.resource.properties.description.minLength = 0;
const draftValidator = new Ajv2020({ strict: true, allErrors: true }).compile(draftSchema);

export function validateDraft(playerSetup) {
  if (!draftValidator(playerSetup)) return result(draftValidator.errors.map(error => issue('DRAFT_SHAPE_INVALID', error.instancePath)));
  if (!canonicalJson(playerSetup).valid) return result([issue('DRAFT_JSON_INVALID')]);
  return result([]);
}
export function blankPlayer(cardPackage) {
  return {
    format: 'modelmirror.ai-rpg.player-setup', formatVersion: '0.1.0', setupId: 'setup.' + randomUUID(),
    cardPackageRef: { id: cardPackage.package.id, version: cardPackage.package.version },
    character: { name: '', appearance: '', personality: '', preferences: [] },
    opening: { mode: '全新攻略者', openingRef: 'opening.unselected' },
    world: { source: 'package', resourceRef: 'world.unselected' },
    currentIdentity: { source: 'package', resourceRef: 'identity.unselected' },
    inherentBackgrounds: [], possessions: [], talents: [], characterPower: { status: 'unspecified' }, runtimePermissions: [],
  };
}
export function loadBuiltinBundle() {
  const source = loadRpg04ApprovedContextCardFixture(), host = loadRpg04ApprovedHost();
  if (!source.valid || !host.valid || host.value.activation.ready !== true) throw Error('BUILTIN_SOURCE_UNAVAILABLE');
  const cardPackage = clone(source.value.cardPackage), contextProfile = clone(source.value.contextProfile);
  contextProfile.hostTemplate = clone(host.value.binding);
  return { cardPackage, contextProfile, hostTemplate: clone(host.value.hostTemplate) };
}
export function publicCatalog(bundle) {
  const card = bundle.cardPackage;
  const kinds = ['worlds', 'identities', 'talents', 'items', 'backgrounds'];
  return {
    card: { id: card.package.id, version: card.package.version, displayName: card.package.displayName },
    resources: Object.fromEntries(kinds.map(kind => [kind, card.resources[kind].map(resource => ({
      id: resource.id, displayName: resource.displayName, description: resource.description,
      ...(resource.worldRefs ? { worldRefs: clone(resource.worldRefs) } : {}),
      ...(resource.rankLabel ? { rankLabel: resource.rankLabel } : {}),
      ...(resource.tierLabel ? { tierLabel: resource.tierLabel } : {}),
    }))])),
    scenes: bundle.contextProfile.scenes.map(scene => {
      const opening = card.resources.openings.find(value => value.id === scene.openingRef);
      return { id: scene.id, worldRef: scene.worldRef, openingRef: scene.openingRef, displayName: opening.displayName, identityRefs: clone(opening.identityRefs), talentRefs: clone(opening.talentRefs), itemRefs: clone(opening.itemRefs), backgroundRefs: clone(opening.backgroundRefs) };
    }),
    modes: [
      { id: '全新攻略者', available: true },
      { id: '继承', available: false, reason: '缺少兼容的继承配置' },
      { id: '沙盒（体验者）', available: false, reason: '缺少兼容的体验开局配置' },
    ],
  };
}
export function validateReady(playerSetup, bundle, sceneRef) {
  const draft = validateDraft(playerSetup);
  if (!draft.valid) return draft;
  const card = validateCardPackage(bundle.cardPackage), profile = validateContextProfile(bundle.contextProfile, bundle.cardPackage);
  if (!card.valid || !profile.valid) return result([issue('BUNDLE_INVALID')]);
  const diagnostics = [...validatePlayerSetup(playerSetup, bundle.cardPackage).diagnostics];
  if (bundle.contextProfile.cardPackage.sha256 !== hashValue(bundle.cardPackage)) diagnostics.push(issue('CARD_HASH_MISMATCH'));
  if (bundle.contextProfile.hostTemplate.sha256 !== hashValue(bundle.hostTemplate)) diagnostics.push(issue('HOST_BINDING_MISMATCH'));
  if (playerSetup.opening.mode !== '全新攻略者') diagnostics.push(issue('MODE_CONFIGURATION_MISSING', '/opening/mode'));
  const scene = bundle.contextProfile.scenes.find(value => value.id === sceneRef);
  const opening = bundle.cardPackage.resources.openings.find(value => value.id === playerSetup.opening.openingRef);
  if (!scene || !opening || scene.openingRef !== opening.id) diagnostics.push(issue('SCENE_CONFIGURATION_MISSING', '/opening/openingRef'));
  else {
    const world = playerSetup.world;
    if (world.source === 'package' ? scene.worldRef !== world.resourceRef : !bundle.cardPackage.resources.worlds.some(value => value.id === scene.worldRef && value.id === world.resource.id && value.displayName === world.resource.displayName && value.description === world.resource.description)) diagnostics.push(issue('WORLD_CONFIGURATION_MISMATCH', '/world'));
    const groups = [
      ['currentIdentity', [playerSetup.currentIdentity], opening.identityRefs],
      ['talents', playerSetup.talents.map(value => value.resource), opening.talentRefs],
      ['possessions', playerSetup.possessions.map(value => value.resource), opening.itemRefs],
      ['inherentBackgrounds', playerSetup.inherentBackgrounds, opening.backgroundRefs],
    ];
    for (const [field, choices, allowed] of groups) for (const choice of choices) {
      if (choice.source === 'package' && !allowed.includes(choice.resourceRef)) diagnostics.push(issue('OPENING_RESOURCE_MISMATCH', '/' + field));
    }
  }
  return result(diagnostics);
}
export function freezeSetup(playerSetup, bundle, sceneRef) {
  const check = validateReady(playerSetup, bundle, sceneRef);
  if (!check.valid) return check;
  return { ...check, value: {
    playerSetup: clone(playerSetup), sceneRef,
    cardPackage: clone(bundle.cardPackage), contextProfile: clone(bundle.contextProfile),
    bindings: { playerSetupSha256: hashValue(playerSetup), cardPackageSha256: hashValue(bundle.cardPackage), contextProfileSha256: hashValue(bundle.contextProfile), hostTemplateSha256: hashValue(bundle.hostTemplate) },
  } };
}
// This export deliberately excludes built-in card contents, lore and host messages.
export function validateWorldDraft(value, playerSetup) {
  if (value === undefined) return result([]);
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== 4 || !['resourceId', 'displayName', 'introduction', 'reference'].every(key => typeof value[key] === 'string') || !/^world[.][a-z0-9._-]{1,90}$/u.test(value.resourceId) || value.displayName.length > 256 || value.introduction.length + value.reference.length > 1000000) return result([issue('WORLD_DRAFT_INVALID')]);
  const expected = value.introduction + (value.reference ? '\n\n代表性强者或力量上限：' + value.reference : '');
  if (playerSetup.world.source === 'custom' && (playerSetup.world.resource.id !== value.resourceId || playerSetup.world.resource.displayName !== value.displayName || playerSetup.world.resource.description !== expected)) return result([issue('WORLD_DRAFT_BINDING_MISMATCH')]);
  return result([]);
}
export function exportPlayerDraft(playerSetup, sceneRef, worldDraft) {
  if (!validateDraft(playerSetup).valid || !validateWorldDraft(worldDraft, playerSetup).valid || typeof sceneRef !== 'string') throw Error('DRAFT_INVALID');
  return JSON.stringify({ format: 'modelmirror.ai-rpg.ui-import', formatVersion: '0.5.0', playerSetup: clone(playerSetup), sceneRef, ...(worldDraft ? { worldDraft: clone(worldDraft) } : {}) }, null, 2);
}
export function validateImport(text, builtin) {
  if (typeof text !== 'string' || Buffer.byteLength(text) > 1048576) return result([issue('IMPORT_SIZE_INVALID')]);
  const parsed = parseStrictJson(text);
  if (!parsed.valid) return result([issue('IMPORT_JSON_INVALID')]);
  const value = parsed.value;
  const envelope = value?.format === 'modelmirror.ai-rpg.player-setup' ? { playerSetup: value } : value;
  if (!envelope || typeof envelope !== 'object' || Array.isArray(envelope) || Object.keys(envelope).some(key => !['format', 'formatVersion', 'playerSetup', 'sceneRef', 'cardPackage', 'contextProfile', 'worldDraft'].includes(key)) || envelope.format && (envelope.format !== 'modelmirror.ai-rpg.ui-import' || envelope.formatVersion !== '0.5.0')) return result([issue('IMPORT_ENVELOPE_INVALID')]);
  const player = validateDraft(envelope.playerSetup);
  if (!player.valid) return player;
  const worldDraftCheck = validateWorldDraft(envelope.worldDraft, envelope.playerSetup);
  if (!worldDraftCheck.valid) return worldDraftCheck;
  const bundle = envelope.cardPackage ? { cardPackage: clone(envelope.cardPackage), contextProfile: clone(envelope.contextProfile), hostTemplate: builtin.hostTemplate } : clone(builtin);
  if (!validateCardPackage(bundle.cardPackage).valid) return result([issue('IMPORT_CARD_INVALID')]);
  // Uploaded profile is data, never a new trusted host or model address.
  if (envelope.contextProfile && !envelope.cardPackage) bundle.contextProfile = clone(envelope.contextProfile);
  if (!bundle.contextProfile || !validateContextProfile(bundle.contextProfile, bundle.cardPackage).valid || hashValue(bundle.contextProfile.hostTemplate) !== hashValue(builtin.contextProfile.hostTemplate)) return result([issue('IMPORT_CONTEXT_MISSING_OR_UNTRUSTED')]);
  const sceneRef = envelope.sceneRef ?? bundle.contextProfile.scenes.find(scene => scene.openingRef === envelope.playerSetup.opening.openingRef)?.id ?? '';
  const ready = validateReady(envelope.playerSetup, bundle, sceneRef);
  return { valid: true, diagnostics: [], value: { playerSetup: clone(envelope.playerSetup), sceneRef, bundle, ready, ...(envelope.worldDraft ? { worldDraft: clone(envelope.worldDraft) } : {}) } };
}
