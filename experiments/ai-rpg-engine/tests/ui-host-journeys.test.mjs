import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRecordStore } from '../ui-host/storage.mjs';
import { createJourneyEngine } from '../ui-host/journeys.mjs';
import { createUiService } from '../ui-host/service.mjs';
import { createOfflineAdapter } from '../ui-host/mock.mjs';
import { loadBuiltinBundle, blankPlayer } from '../ui-host/setup.mjs';
const work = fileURLToPath(new URL('../.rpg04-work/', import.meta.url)), builtin = loadBuiltinBundle();
async function fixture(t) {
  await fs.mkdir(work, { recursive: true }); const root = await fs.mkdtemp(path.join(work, 'rpg05-journey-'));
  const store = await createRecordStore(path.join(root, 'records'));
  const control = { calls: 0, delay: 0, failed: false }, engines = [];
  const adapterFor = card => ({ evidenceKind: 'mock', async generate(...args) { control.calls++; if (control.failed) throw Error('private adapter error'); return createOfflineAdapter(card, { chunkDelay: control.delay }).generate(...args); } });
  async function open() { const engine = await createJourneyEngine({ store, root: path.join(root, 'runtime'), hostTemplate: builtin.hostTemplate, adapterFor }); engines.push(engine); return engine; }
  const engine = await open(), service = createUiService({ store, builtin, engine });
  const player = blankPlayer(builtin.cardPackage), scene = builtin.contextProfile.scenes[0];
  player.character = { name: '账本测试旅人', appearance: '朴素衣着', personality: '谨慎', preferences: [] };
  player.world.resourceRef = scene.worldRef; player.opening.openingRef = scene.openingRef;
  player.currentIdentity.resourceRef = builtin.cardPackage.resources.openings.find(item => item.id === scene.openingRef).identityRefs[0];
  await service.command('draft.save', { id: 'draft.test', expectedRevision: 0, playerSetup: player, sceneRef: scene.id, bundleId: 'builtin' });
  await service.command('journey.create', { id: 'journey.test', draftId: 'draft.test', expectedRevision: 1 });
  t.after(async () => { for (const item of engines) await item.close(); const actual = await fs.realpath(root); assert.equal(path.dirname(actual), await fs.realpath(work)); assert.ok(path.basename(actual).startsWith('rpg05-journey-')); await fs.rm(actual, { recursive: true }); });
  const read = () => service.command('journey.read', { id: 'journey.test' });
  const generate = async (operationId, text = '观察四周') => service.command('journey.generate', { id: 'journey.test', operationId, expectedRevision: (await read()).revision, text });
  return { store, root, control, engine, service, read, generate, open };
}
test('identical requests dispatch once across completion and restart; conflicting reuse fails', async t => {
  const f = await fixture(t), revision = (await f.read()).revision;
  const payload = { id: 'journey.test', operationId: 'op.once', expectedRevision: revision, text: '/查询 附近有什么' };
  await Promise.all([f.service.command('journey.generate', payload), f.service.command('journey.generate', payload)]);
  await f.engine.idle(); const first = await f.read();
  assert.equal(first.turnCount, 1); assert.equal(f.control.calls, 1); assert.equal(first.operation.status, 'committed');
  await f.service.command('journey.generate', payload); assert.equal(f.control.calls, 1);
  await assert.rejects(f.service.command('journey.generate', { ...payload, text: '不同输入' }), /OPERATION_ID_CONFLICT/u);
  await f.engine.close(); const restored = await f.open();
  await restored.command('journey.generate', payload); assert.equal(f.control.calls, 1);
  const text = JSON.stringify(first);
  for (const privateKey of ['worldbookEntries', 'hostTemplate', 'messages', 'stateProposals', 'acceptedStateFields', 'systemPrompt']) assert.equal(text.includes('"' + privateKey + '"'), false);
});
test('busy guard, cancellation and failed regeneration preserve the published head', async t => {
  const f = await fixture(t); await f.generate('op.first'); await f.engine.idle(); const first = await f.read();
  f.control.delay = 20;
  await f.service.command('journey.regenerate', { id: first.id, operationId: 'op.cancel', expectedRevision: first.revision });
  const busy = await f.read();
  await assert.rejects(f.service.command('journey.generate', { id: first.id, operationId: 'op.concurrent', expectedRevision: busy.revision, text: '并发' }), /JOURNEY_BUSY/u);
  await f.service.command('journey.cancel', { id: first.id, operationId: 'op.cancel' }); await f.engine.idle();
  assert.deepEqual((await f.read()).turns, first.turns);
  f.control.failed = true; await f.service.command('journey.regenerate', { id: first.id, operationId: 'op.fail', expectedRevision: (await f.read()).revision }); await f.engine.idle();
  const failed = await f.read(); assert.deepEqual(failed.turns, first.turns); assert.equal(failed.operation.status, 'failed');
  assert.equal(JSON.stringify(failed).includes('private adapter error'), false);
});
test('delete latest restores previous state without dispatch; regenerating retains receipts', async t => {
  const f = await fixture(t); await f.generate('op.one'); await f.engine.idle(); const first = await f.read();
  await f.generate('op.two', '/对话 你好'); await f.engine.idle(); let current = await f.read();
  await f.service.command('journey.regenerate', { id: current.id, operationId: 'op.newtwo', expectedRevision: current.revision }); await f.engine.idle(); current = await f.read();
  assert.equal(current.turnCount, 2); assert.equal(current.turns[0].id, first.turns[0].id);
  const calls = f.control.calls;
  await f.service.command('journey.delete-latest', { id: current.id, expectedRevision: current.revision, turnId: current.turns.at(-1).id });
  assert.deepEqual((await f.read()).turns, first.turns); assert.equal(f.control.calls, calls);
  const records = await f.service.command('journey.records', { id: current.id }); assert.equal(records.length, 3);
  await assert.rejects(f.service.command('journey.delete-latest', { id: current.id, expectedRevision: current.revision, turnId: current.turns.at(-1).id }), /REVISION_CONFLICT/u);
});
test('restart closes interrupted reservation including commit-before-publication window; never dispatches', async t => {
  const f = await fixture(t); await f.engine.close();
  let record = await f.store.read('journey', 'journey.test');
  await f.store.write('operation', 'op.crash', { journeyId: record.id, kind: 'generate', status: 'committed', requestHash: 'unpublished', previousHead: null, beforeHead: null, text: '未发布内容', updatedAt: new Date().toISOString(), sequence: 1 }, 0);
  await f.store.write('journey', record.id, { ...record.payload, activeOperationId: 'op.crash', lastOperationId: 'op.crash', status: 'generating' }, record.revision);
  const recovered = await f.open(); record = await f.store.read('journey', record.id);
  assert.equal(record.payload.activeOperationId, null); assert.equal(record.payload.status, 'interrupted');
  assert.equal((await recovered.read(record)).turnCount, 0); assert.equal(f.control.calls, 0);
  assert.equal((await f.store.read('operation', 'op.crash')).payload.status, 'interrupted');
});
