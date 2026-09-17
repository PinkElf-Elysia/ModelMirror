import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRecordStore } from '../ui-host/storage.mjs';
import { createDispatchLedger } from '../ui-host/dispatch-ledger.mjs';
const work = fileURLToPath(new URL('../.rpg04-work/', import.meta.url));
async function fixture(t, required = true) {
  const directory = await fs.mkdtemp(path.join(work, 'rpg05-ledger-'));
  t.after(async () => { const real = await fs.realpath(directory); assert.equal(path.dirname(real), await fs.realpath(work)); assert.ok(path.basename(real).startsWith('rpg05-ledger-')); await fs.rm(real, { recursive: true }); });
  const store = await createRecordStore(directory), options = { store, freezeSha256: 'a'.repeat(64), certificationRequired: required };
  return { store, options, ledger: await createDispatchLedger(options) };
}
const request = (kind, id = 'op.test', maxTokens = 4096) => ({ kind, operationId: id, maxTokens, requestSha256: 'b'.repeat(64) });
const result = outcome => ({ outcome, dispatched: true, reportSha256: 'c'.repeat(64) });
test('eight immutable slots retain budget, enforce allocation and reject replay', async t => {
  const { ledger } = await fixture(t), kinds = ['certification', 'gu', 'gu', 'gu', 'minecraft', 'minecraft', 'minecraft', 'cancellation'];
  for (const [i, kind] of kinds.entries()) {
    const r = await ledger.reserve(request(kind, 'op.' + i, i ? 4096 : 512));
    assert.equal(r.payload.slot, i + 1); await ledger.complete(r, result(i === 7 ? 'cancelled' : 'succeeded'));
    await assert.rejects(ledger.reserve(request(kind, 'op.' + i, i ? 4096 : 512)), /DISPATCH_REPLAY_FORBIDDEN/);
  }
  assert.equal((await ledger.snapshot()).length, 8);
  await assert.rejects(ledger.reserve(request('gu', 'op.ninth')), /DISPATCH_BATCH_STOPPED|DISPATCH_BUDGET_EXHAUSTED/);
});
test('unused certification slot cannot be reassigned; invalid kind/limit consumes nothing', async t => {
  const { ledger } = await fixture(t, false);
  await assert.rejects(ledger.reserve(request('certification', 'op.cert', 512)), /DISPATCH_SCOPE_MISMATCH/);
  await assert.rejects(ledger.reserve(request('gu', 'op.large', 4097)), /DISPATCH_SCOPE_MISMATCH/);
  assert.equal((await ledger.snapshot()).length, 0);
  const r = await ledger.reserve(request('gu')); assert.equal(r.payload.slot, 2);
});
test('concurrent reservations produce one winner; restart cannot replay an uncertain dispatch', async t => {
  const { ledger, options } = await fixture(t, false);
  const races = await Promise.allSettled([ledger.reserve(request('gu', 'op.one')), ledger.reserve(request('gu', 'op.two'))]);
  assert.equal(races.filter(r => r.status === 'fulfilled').length, 1);
  const reopened = await createDispatchLedger(options);
  assert.equal((await reopened.snapshot()).length, 1);
  await assert.rejects(reopened.reserve(request('gu', 'op.three')), /DISPATCH_BATCH_STOPPED/);
});
test('failure including undispatched preflight stops the batch and remains counted', async t => {
  const { ledger } = await fixture(t, false), r = await ledger.reserve(request('gu'));
  await ledger.complete(r, { ...result('failed'), dispatched: false });
  assert.equal((await ledger.snapshot()).length, 1);
  await assert.rejects(ledger.reserve(request('gu', 'op.next')), /DISPATCH_BATCH_STOPPED/);
  await assert.rejects(ledger.complete(r, result('succeeded')), /DISPATCH_COMPLETION_CONFLICT/);
});
test('policy/freeze mutation cannot reopen an existing ledger', async t => {
  const { options } = await fixture(t);
  await assert.rejects(createDispatchLedger({ ...options, freezeSha256: 'd'.repeat(64) }), /DISPATCH_POLICY_DRIFT/);
  await assert.rejects(createDispatchLedger({ ...options, certificationRequired: false }), /DISPATCH_POLICY_DRIFT/);
});

async function stoppedHistory(t) {
  const f = await fixture(t);
  for (const [i, kind] of ['certification', 'gu', 'gu'].entries()) {
    const r = await f.ledger.reserve(request(kind, 'op.old.' + i, i ? 4096 : 64));
    await f.ledger.complete(r, result(i === 2 ? 'failed' : 'succeeded'));
  }
  const history = await f.ledger.snapshot();
  const continuation = { format: 'rpg05-authorized-continuation/1', authorization: 'user-approved-plus-two-after-slot3-failure', priorFreezeSha256: f.options.freezeSha256, priorRecords: history.map(r => r.sha256) };
  return { ...f, history, next: { ...f.options, freezeSha256: 'd'.repeat(64), continuation } };
}
test('explicit continuation preserves failed history and enforces seven remaining slots through numeric slot ten', async t => {
  const f = await stoppedHistory(t), before = await f.store.read('bundle', 'dispatch-policy');
  const ledger = await createDispatchLedger(f.next);
  for (const [i, kind] of ['certification', 'gu', 'gu', 'minecraft', 'minecraft', 'minecraft', 'cancellation'].entries()) {
    const r = await ledger.reserve(request(kind, 'op.new.' + i, i ? 4096 : 64));
    assert.equal(r.payload.slot, i + 4);
    await ledger.complete(r, result(i === 6 ? 'cancelled' : 'succeeded'));
  }
  const reopened = await createDispatchLedger(f.next), records = await reopened.snapshot();
  assert.equal(records.length, 10);
  assert.deepEqual(records.slice(0, 3), f.history);
  assert.deepEqual(await f.store.read('bundle', 'dispatch-policy'), before);
  await assert.rejects(reopened.reserve(request('gu', 'op.eleven')), /DISPATCH_BATCH_STOPPED|DISPATCH_BUDGET_EXHAUSTED/);
});
test('continuation cannot acknowledge different history or waive a new failure', async t => {
  const f = await stoppedHistory(t);
  await assert.rejects(createDispatchLedger({ ...f.next, continuation: { ...f.next.continuation, priorRecords: ['e'.repeat(64), ...f.next.continuation.priorRecords.slice(1)] } }), /DISPATCH_HISTORY_DRIFT/);
  const ledger = await createDispatchLedger(f.next);
  await assert.rejects(ledger.reserve(request('gu', 'op.skipcert')), /DISPATCH_SCOPE_MISMATCH/);
  const r = await ledger.reserve(request('certification', 'op.newcert', 64));
  await ledger.complete(r, result('failed'));
  await assert.rejects(ledger.reserve(request('gu', 'op.afterfailure')), /DISPATCH_BATCH_STOPPED/);
  assert.equal((await ledger.snapshot()).length, 4);
});

test('second authorized extension preserves all five records and both policies, caps eleven', async t => {
 const f=await stoppedHistory(t), first=await createDispatchLedger(f.next);
 for (const [i,kind] of ['certification','gu'].entries()) {const r=await first.reserve(request(kind,'op.mid.'+i,i?4096:64));await first.complete(r,result(i?'failed':'succeeded'));}
 const history=await first.snapshot(), policy=await f.store.read('bundle','dispatch-continuation');
 const next={...f.next,freezeSha256:'e'.repeat(64),continuation:{...f.next.continuation,extension:{authorization:'user-approved-plus-one-after-slot5-failure',priorFreezeSha256:f.next.freezeSha256,priorPolicySha256:policy.sha256,priorRecords:history.map(r=>r.sha256)}}};
 const ledger=await createDispatchLedger(next);
 for(const [i,kind] of ['gu','gu','minecraft','minecraft','minecraft','cancellation'].entries()){const r=await ledger.reserve(request(kind,'op.final.'+i));assert.equal(r.payload.slot,i+6);await ledger.complete(r,result(i===5?'cancelled':'succeeded'));}
 assert.deepEqual((await ledger.snapshot()).slice(0,5),history);assert.deepEqual(await f.store.read('bundle','dispatch-continuation'),policy);
 assert.equal((await (await createDispatchLedger(next)).snapshot()).length,11);
 await assert.rejects(ledger.reserve(request('gu','op.extra')),/DISPATCH_BATCH_STOPPED|DISPATCH_BUDGET_EXHAUSTED/);
});
