import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadBuiltinBundle, blankPlayer, freezeSetup, hashText } from '../ui-host/setup.mjs';
import { runUiTurn, acceptedStateFields } from '../ui-host/turn.mjs';
import { readOperationCheckpoint } from '../ui-host/checkpoints.mjs';
const work = fileURLToPath(new URL('../.rpg04-work/', import.meta.url)), bundle = loadBuiltinBundle();
async function fixture(t, world = 'world.gu') {
  await fs.mkdir(work, { recursive: true });
  const root = await fs.mkdtemp(path.join(work, 'rpg05-turn-'));
  t.after(async () => { const actual = await fs.realpath(root); assert.equal(path.dirname(actual), await fs.realpath(work)); assert.ok(path.basename(actual).startsWith('rpg05-turn-')); await fs.rm(actual, { recursive: true }); });
  const scene = bundle.contextProfile.scenes.find(item => item.worldRef === world), player = blankPlayer(bundle.cardPackage);
  player.character = { name: '中性离线旅人', appearance: '朴素衣着', personality: '谨慎', preferences: [] };
  player.world.resourceRef = world; player.opening.openingRef = scene.openingRef;
  player.currentIdentity.resourceRef = bundle.cardPackage.resources.openings.find(item => item.id === scene.openingRef).identityRefs[0];
  const frozen = freezeSetup(player, bundle, scene.id); assert.equal(frozen.valid, true);
  return { root, initial: frozen.value, sessionId: 'journey.test', hostTemplate: bundle.hostTemplate };
}
function adapter(handler) {
  const calls = [];
  return { evidenceKind: 'mock', calls, async generate(request, options) {
    calls.push(structuredClone(request));
    const exchange = { format: 'modelmirror.ai-rpg.turn-exchange', formatVersion: '0.1.0', exchangeId: request.exchangeId, cardPackageRef: { id: bundle.cardPackage.package.id, version: bundle.cardPackage.package.version }, input: request.input, proposal: { narrative: '离线 mock：你观察了身边的环境。', suggestedActions: [], informationModules: [], stateProposals: [], uncertainties: [] } };
    if (handler) await handler(exchange, options);
    return { valid: true, diagnostics: [], value: { status: 'succeeded', outcome: 'completed', dispatched: true, text: JSON.stringify(exchange), observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } } };
  } };
}
test('both worlds compile, dispatch mock once and internally commit; checkpoint and bridge hashes agree', async t => {
  for (const world of ['world.gu', 'world.minecraft']) {
    const f = await fixture(t, world), mock = adapter();
    const result = await runUiTurn({ ...f, adapter: mock, operationId: 'operation.first', input: { kind: 'action', text: '观察四周。' } });
    assert.equal(result.status, 'committed'); assert.equal(mock.calls.length, 1); assert.equal(result.session.turns.length, 1);
    assert.equal(result.bridgeReceipt.preparedTurnSha256, result.preparedSha256);
    assert.equal(result.bridgeReceipt.evidenceKind, 'mock');
    const restored = await readOperationCheckpoint({ ...f, operationId: 'operation.first', cardPackage: f.initial.cardPackage, playerSetup: f.initial.playerSetup });
    assert.deepEqual(restored, result.session);
    await assert.rejects(runUiTurn({ ...f, adapter: mock, operationId: 'operation.first', input: { kind: 'action', text: '观察四周。' } }));
    assert.equal(mock.calls.length, 1);
  }
});
test('next turn and replacement use exact parent checkpoint; old latest and failed attempt remain readable', async t => {
  const f = await fixture(t), mock = adapter();
  await runUiTurn({ ...f, adapter: mock, operationId: 'operation.one', input: { kind: 'action', text: '第一步' } });
  const before = await fs.readFile(path.join(f.root, 'operation.one', 'session-journey.test.json'));
  const second = await runUiTurn({ ...f, adapter: mock, parentOperationId: 'operation.one', operationId: 'operation.two', input: { kind: 'speech', text: '你好' } });
  const replacement = await runUiTurn({ ...f, adapter: mock, parentOperationId: 'operation.one', operationId: 'operation.replacement', input: { kind: 'speech', text: '你好' } });
  assert.equal(second.session.turns.length, 2); assert.equal(replacement.session.turns.length, 2);
  assert.equal(replacement.session.turns[0].generationId, second.session.turns[0].generationId);
  assert.notEqual(replacement.session.turns[1].generationId, second.session.turns[1].generationId);
  assert.equal(hashText(await fs.readFile(path.join(f.root, 'operation.one', 'session-journey.test.json'))), hashText(before));
  const invalid = adapter(exchange => { exchange.proposal.stateProposals = [{ fieldRef: 'state.unknown', proposedValue: true }]; });
  const failed = await runUiTurn({ ...f, adapter: invalid, parentOperationId: 'operation.two', operationId: 'operation.failed', input: { kind: 'action', text: '失败测试' } });
  assert.equal(failed.status, 'failed'); assert.equal(failed.session.turns.length, 2);
  assert.deepEqual((await readOperationCheckpoint({ ...f, operationId: 'operation.two', cardPackage: f.initial.cardPackage, playerSetup: f.initial.playerSetup })).turns, second.session.turns);
});
test('cancelled late success never commits; abort before start dispatches zero', async t => {
  const f = await fixture(t), controller = new AbortController();
  const mock = adapter(async (_exchange, options) => { controller.abort(); await new Promise(resolve => options.signal.aborted ? resolve() : options.signal.addEventListener('abort', resolve, { once: true })); });
  const result = await runUiTurn({ ...f, adapter: mock, operationId: 'operation.cancel', signal: controller.signal, input: { kind: 'action', text: '取消测试' } });
  assert.equal(result.status, 'cancelled'); assert.equal(result.session.turns.length, 0); assert.equal(result.session.pending, null);
  const none = adapter(); const early = await runUiTurn({ ...f, adapter: none, operationId: 'operation.early', signal: controller.signal, input: { kind: 'action', text: '不发送' } });
  assert.equal(early.status, 'cancelled'); assert.equal(none.calls.length, 0);
});
test('host refuses real adapters, resource drift and unsafe checkpoint IDs before dispatch', async t => {
  const f = await fixture(t), mock = adapter(), args = { ...f, adapter: mock, operationId: 'operation.test', input: { kind: 'query', text: '观察' } };
  await assert.rejects(runUiTurn({ ...args, adapter: { ...mock, evidenceKind: 'real' } }), /REAL_EXECUTION_NOT_AUTHORIZED/u);
  const changed = structuredClone(f.initial); changed.playerSetup.character.name = '漂移';
  await assert.rejects(runUiTurn({ ...args, initial: changed }), /INITIAL_BINDING_DRIFT/u);
  await assert.rejects(runUiTurn({ ...args, operationId: '../escape' }), /CHECKPOINT_ADDRESS_INVALID/u);
  assert.equal(mock.calls.length, 0);
});
test('state acceptance uses frozen full schema, declared rights and query prohibition', async t => {
  const f = await fixture(t);
  const field = f.initial.cardPackage.stateFields.find(item => item.modelMayPropose && item.valueType === 'shortText'); assert.ok(field);
  const mock = adapter(exchange => { exchange.proposal.stateProposals = [{ fieldRef: field.id, proposedValue: '离线状态记录' }]; });
  const accepted = await runUiTurn({ ...f, adapter: mock, operationId: 'operation.state', input: { kind: 'action', text: '观察' } });
  assert.equal(accepted.status, 'committed'); assert.equal(accepted.session.state.find(item => item.fieldRef === field.id).value, '离线状态记录');
  const bad = structuredClone(accepted.session.turns[0].exchange); bad.input.kind = 'query';
  assert.throws(() => acceptedStateFields(bad, f.initial.cardPackage), /STATE_EXCHANGE_INVALID/u);
  const queried = await runUiTurn({ ...f, adapter: adapter(), parentOperationId: 'operation.state', operationId: 'operation.query', input: { kind: 'query', text: '现状如何' } });
  assert.equal(queried.status, 'committed'); assert.deepEqual(queried.session.state, accepted.session.state);
});

test('cancel after pending or observer failure discards without publication', async t => {
  for (const mode of ['cancel', 'observer']) {
    const f = await fixture(t), controller = new AbortController();
    const result = await runUiTurn({ ...f, adapter: adapter(), operationId: 'operation.pending', signal: controller.signal, input: { kind: 'action', text: '边界测试' }, onEvent(event) {
      if (event.type === 'status' && event.status === 'pending') { if (mode === 'cancel') controller.abort(); else throw Error('observer failed'); }
    } });
    assert.equal(result.status, mode === 'cancel' ? 'cancelled' : 'failed');
    assert.equal(result.session.pending, null); assert.equal(result.session.turns.length, 0);
    assert.equal(result.session.generations.at(-1).status, 'discarded');
  }
});

test('model-proposed player saves are rejected without changing formal history or silently dropping the candidate',async t=>{
 const f=await fixture(t),first=await runUiTurn({...f,adapter:adapter(),operationId:'operation.before-save',input:{kind:'action',text:'Observe the road.'}});
 const address=path.join(f.root,'operation.before-save','session-journey.test.json'),before=await fs.readFile(address);
 const model=adapter(exchange=>{exchange.proposal.stateProposals=[{fieldRef:'state.rpg04.memory.saves',proposedValue:'Slot 1: unsolicited save'}];});
 await assert.rejects(runUiTurn({...f,adapter:model,parentOperationId:'operation.before-save',operationId:'operation.unsolicited-save',input:{kind:'action',text:'Look at the empty road.'}}),/STATE_PLAYER_SAVE_AUTHORIZATION_REQUIRED/);
 assert.equal(model.calls.length,1);assert.deepEqual(await fs.readFile(address),before);
 const rejected=await readOperationCheckpoint({...f,operationId:'operation.unsolicited-save',cardPackage:f.initial.cardPackage,playerSetup:f.initial.playerSetup});
 assert.deepEqual(rejected.turns,first.session.turns);assert.deepEqual(rejected.state,first.session.state);
 assert.ok(rejected.pending);assert.equal(rejected.generations.at(-1).exchange.proposal.stateProposals[0].proposedValue,'Slot 1: unsolicited save');
});
