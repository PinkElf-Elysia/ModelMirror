import { randomUUID } from 'node:crypto';
import { loadBuiltinBundle, publicCatalog, blankPlayer, validateDraft, validateReady, freezeSetup, validateImport, exportPlayerDraft, validateWorldDraft, hashValue } from './setup.mjs';
const fail = (code, status = 400) => Object.assign(new Error(code), { code, status });
function shape(value, allowed, required = allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !allowed.includes(key)) || required.some(key => !Object.hasOwn(value, key))) throw fail('COMMAND_PAYLOAD_INVALID');
}
const summary = record => ({
  id: record.id, revision: record.revision,
  title: record.payload.title ?? record.payload.playerSetup?.character.name ?? '未命名角色',
  characterName: record.payload.playerSetup?.character.name ?? record.payload.initial?.playerSetup.character.name ?? '',
  updatedAt: record.payload.updatedAt, turnCount: record.payload.turnCount ?? 0, status: record.payload.status ?? 'draft',
});
export function createUiService({ store, builtin = loadBuiltinBundle(), engine = null } = {}) {
  if (!store?.read || !store?.write || !store?.list) throw fail('STORE_REQUIRED');
  async function bundleFor(id) {
    if (id === 'builtin') return builtin;
    const stored = await store.read('bundle', id);
    if (!stored) throw fail('BUNDLE_NOT_FOUND', 404);
    return { ...structuredClone(stored.payload), hostTemplate: structuredClone(builtin.hostTemplate) };
  }
  async function draftFor(id) {
    const stored = await store.read('draft', id);
    if (!stored) throw fail('DRAFT_NOT_FOUND', 404);
    return stored;
  }
  async function draftView(record) {
    const bundle = await bundleFor(record.payload.bundleId);
    return { id: record.id, revision: record.revision, ...structuredClone(record.payload), catalog: publicCatalog(bundle), ready: validateReady(record.payload.playerSetup, bundle, record.payload.sceneRef) };
  }
  function journeyView(record) {
    if (engine) return engine.read(record);
    return { ...summary(record), playerSetup: structuredClone(record.payload.initial.playerSetup), sceneRef: record.payload.initial.sceneRef, status: record.payload.status, execution: 'disabled', turns: [], bindings: structuredClone(record.payload.initial.bindings) };
  }
  return Object.freeze({
    async bootstrap() {
      return { journeys: (await store.list('journey')).map(summary), drafts: (await store.list('draft')).map(summary), capabilities: ['drafts', 'imports', 'creation', ...(engine ? ['mock-play'] : [])], catalog: publicCatalog(builtin), blankPlayer: blankPlayer(builtin.cardPackage) };
    },
    async command(command, payload) {
      if (command === 'draft.save') {
        shape(payload, ['id', 'expectedRevision', 'playerSetup', 'sceneRef', 'bundleId', 'worldDraft'], ['id', 'expectedRevision', 'playerSetup', 'sceneRef', 'bundleId']);
        if (!validateWorldDraft(payload.worldDraft, payload.playerSetup).valid) throw fail('WORLD_DRAFT_INVALID');
        if (!validateDraft(payload.playerSetup).valid || typeof payload.sceneRef !== 'string' || payload.sceneRef.length > 128) throw fail('DRAFT_INVALID');
        const bundle = await bundleFor(payload.bundleId);
        if (hashValue(payload.playerSetup.cardPackageRef) !== hashValue({ id: bundle.cardPackage.package.id, version: bundle.cardPackage.package.version })) throw fail('DRAFT_CARD_MISMATCH');
        return draftView(await store.write('draft', payload.id, { playerSetup: structuredClone(payload.playerSetup), sceneRef: payload.sceneRef, bundleId: payload.bundleId, ...(payload.worldDraft ? { worldDraft: structuredClone(payload.worldDraft) } : {}), updatedAt: new Date().toISOString() }, payload.expectedRevision));
      }
      if (command === 'draft.read') {
        shape(payload, ['id']);
        return draftView(await draftFor(payload.id));
      }
      if (command === 'draft.export') {
        shape(payload, ['id']);
        const record = await draftFor(payload.id);
        return { text: exportPlayerDraft(record.payload.playerSetup, record.payload.sceneRef, record.payload.worldDraft) };
      }
      if (command === 'import.validate') {
        shape(payload, ['text']);
        const imported = validateImport(payload.text, builtin);
        if (!imported.valid) return imported;
        const { bundle, playerSetup, sceneRef, ready, worldDraft } = imported.value;
        let bundleId = 'builtin';
        if (hashValue(bundle.cardPackage) !== hashValue(builtin.cardPackage) || hashValue(bundle.contextProfile) !== hashValue(builtin.contextProfile)) {
          const content = { cardPackage: bundle.cardPackage, contextProfile: bundle.contextProfile };
          bundleId = 'bundle.' + hashValue(content);
          if (!(await store.read('bundle', bundleId))) {
            try { await store.write('bundle', bundleId, content, 0); }
            catch (cause) { if (cause.code !== 'REVISION_CONFLICT' || hashValue((await store.read('bundle', bundleId))?.payload) !== hashValue(content)) throw cause; }
          }
        }
        return { valid: true, diagnostics: [], value: { id: 'draft.' + randomUUID(), revision: 0, playerSetup, sceneRef, bundleId, catalog: publicCatalog(bundle), ready, ...(worldDraft ? { worldDraft } : {}) } };
      }
      if (command === 'journey.create') {
        shape(payload, ['id', 'draftId', 'expectedRevision']);
        const previous = await store.read('journey', payload.id);
        if (previous) {
          if (previous.payload.sourceDraft.id !== payload.draftId || previous.payload.sourceDraft.revision !== payload.expectedRevision) throw fail('JOURNEY_ID_CONFLICT', 409);
          return journeyView(previous);
        }
        const draft = await draftFor(payload.draftId);
        if (draft.revision !== payload.expectedRevision) throw fail('REVISION_CONFLICT', 409);
        const bundle = await bundleFor(draft.payload.bundleId);
        const frozen = freezeSetup(draft.payload.playerSetup, bundle, draft.payload.sceneRef);
        if (!frozen.valid) throw fail('SETUP_NOT_READY', 422);
        const world = publicCatalog(bundle).resources.worlds.find(item => item.id === bundle.contextProfile.scenes.find(item => item.id === draft.payload.sceneRef)?.worldRef);
        const record = await store.write('journey', payload.id, {
          title: draft.payload.playerSetup.character.name + ' · ' + (world?.displayName ?? '自定义世界'),
          sourceDraft: { id: draft.id, revision: draft.revision }, initial: frozen.value,
          bundleId: draft.payload.bundleId, updatedAt: new Date().toISOString(), status: 'created', turnCount: 0,
        }, 0);
        return journeyView(record);
      }
      if (command === 'journey.read') {
        shape(payload, ['id']);
        const record = await store.read('journey', payload.id);
        if (!record) throw fail('JOURNEY_NOT_FOUND', 404);
        return journeyView(record);
      }
      if (engine && ['journey.generate', 'journey.cancel', 'journey.regenerate', 'journey.delete-latest', 'journey.records'].includes(command)) return engine.command(command, payload);
      throw fail('COMMAND_NOT_AVAILABLE', 409);
    },
  });
}
