import test from 'node:test';
import assert from 'node:assert/strict';
import { createUiService } from '../ui-host/service.mjs';
import { loadBuiltinBundle, blankPlayer } from '../ui-host/setup.mjs';
const builtin = loadBuiltinBundle();
function setup() {
  const entries = new Map();
  const store = {
    async read(kind, id) { return structuredClone(entries.get(kind + ':' + id) ?? null); },
    async list(kind) { return structuredClone([...entries.values()].filter(value => value.kind === kind)); },
    async write(kind, id, payload, expectedRevision) {
      const key = kind + ':' + id, previous = entries.get(key);
      if ((previous?.revision ?? 0) !== expectedRevision) throw Object.assign(Error('REVISION_CONFLICT'), { code: 'REVISION_CONFLICT' });
      const record = { kind, id, revision: expectedRevision + 1, payload: structuredClone(payload) };
      entries.set(key, record); return structuredClone(record);
    },
  };
  const service = createUiService({ store, builtin });
  const player = blankPlayer(builtin.cardPackage);
  const scene = builtin.contextProfile.scenes[0], opening = builtin.cardPackage.resources.openings.find(value => value.id === scene.openingRef);
  player.character = { name: '中性旅人', appearance: '朴素衣着', personality: '谨慎', preferences: [] };
  player.opening.openingRef = opening.id;
  player.world.resourceRef = scene.worldRef;
  player.currentIdentity.resourceRef = opening.identityRefs[0];
  const payload = { id: 'draft.test', expectedRevision: 0, playerSetup: player, sceneRef: scene.id, bundleId: 'builtin' };
  return { service, payload };
}
test('save, read, export and import remain exact and return only public projections', async () => {
  const { service, payload } = setup();
  const saved = await service.command('draft.save', payload);
  assert.equal(saved.revision, 1);
  assert.equal(saved.ready.valid, true);
  assert.deepEqual((await service.command('draft.read', { id: payload.id })).playerSetup, payload.playerSetup);
  const exported = await service.command('draft.export', { id: payload.id });
  const imported = await service.command('import.validate', exported);
  assert.equal(imported.valid, true);
  assert.deepEqual(imported.value.playerSetup, payload.playerSetup);
  const bootstrap = await service.bootstrap();
  assert.equal(bootstrap.drafts.length, 1);
  const browserText = JSON.stringify([saved, bootstrap, imported]);
  assert.equal(browserText.includes('"worldbookEntries"'), false);
  assert.equal(browserText.includes('"hostTemplate"'), false);
});
test('create freezes initial config, duplicate click is idempotent, editing draft creates no history', async () => {
  const { service, payload } = setup();
  await service.command('draft.save', payload);
  const request = { id: 'journey.test', draftId: payload.id, expectedRevision: 1 };
  const first = await service.command('journey.create', request);
  assert.deepEqual(await service.command('journey.create', request), first);
  payload.expectedRevision = 1;
  payload.playerSetup.character.name = '另一位旅人';
  await service.command('draft.save', payload);
  const read = await service.command('journey.read', { id: 'journey.test' });
  assert.equal(read.playerSetup.character.name, '中性旅人');
  assert.equal(read.turns.length, 0);
  assert.equal((await service.bootstrap()).journeys.length, 1);
  await assert.rejects(service.command('journey.create', { ...request, expectedRevision: 2 }), /JOURNEY_ID_CONFLICT/u);
});
test('invalid launch, arbitrary command and extra model parameters fail closed', async () => {
  const { service, payload } = setup();
  payload.playerSetup.character.name = '';
  await service.command('draft.save', payload);
  await assert.rejects(service.command('journey.create', { id: 'journey.test', draftId: payload.id, expectedRevision: 1 }), /SETUP_NOT_READY/u);
  await assert.rejects(service.command('draft.read', { id: payload.id, modelAddress: 'https://example.org' }), /COMMAND_PAYLOAD_INVALID/u);
  await assert.rejects(service.command('journey.generate', { id: 'journey.test' }), /COMMAND_NOT_AVAILABLE/u);
  assert.equal((await service.bootstrap()).journeys.length, 0);
});
