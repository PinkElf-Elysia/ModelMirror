import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRecordStore } from '../ui-host/storage.mjs';
import { createManualLedger, MANUAL_AUTHORIZATION, MANUAL_SOURCE_AUTHORIZATION, MANUAL_SOURCE_VERSION } from '../ui-host/manual-access.mjs';
const work=fileURLToPath(new URL('../.rpg04-work/',import.meta.url));
const oldFreeze='a'.repeat(64),newFreeze='d'.repeat(64);
const request=id=>({kind:'manual',operationId:id,requestSha256:'b'.repeat(64),maxTokens:4096});
const result=outcome=>({outcome,dispatched:true,reportSha256:'c'.repeat(64)});
const policyIds=['dispatch-policy','dispatch-continuation','dispatch-extension-slot5','dispatch-structured-slot7','dispatch-keyed-slot12','dispatch-protocol-slot16','dispatch-reviewed-slot18'];
async function fixture(t,outcomes=[]) {
 const root=await fs.mkdtemp(path.join(work,'rpg05-source-cutover-'));
 t.after(async()=>{const actual=await fs.realpath(root);assert.equal(path.dirname(actual),await fs.realpath(work));assert.ok(path.basename(actual).startsWith('rpg05-source-cutover-'));await fs.rm(actual,{recursive:true});});
 const store=await createRecordStore(path.join(root,'manual')),automaticStore=await createRecordStore(path.join(root,'automatic'));
 const approval={authorization:MANUAL_AUTHORIZATION,operator:'user',maxDispatches:5,automaticConsumed:26,automaticRetry:false,automaticRecords:[],automaticPolicies:[]};
 for(let slot=1;slot<=26;slot++) {const p={slot,status:'completed',outcome:'succeeded'};await automaticStore.write('operation','dispatch.'+slot,p,0);approval.automaticRecords.push((await automaticStore.write('operation','dispatch.'+slot,p,1)).sha256);}
 for(const id of policyIds) approval.automaticPolicies.push({id,sha256:(await automaticStore.write('bundle',id,{historical:id},0)).sha256});
 const oldOptions={store,automaticStore,approval,freezeSha256:oldFreeze},old=await createManualLedger(oldOptions);
 for(const [i,outcome] of outcomes.entries()){const r=await old.reserve(request('gen.old.'+i));if(outcome!=='reserved')await old.complete(r,result(outcome));}
 const prior=await old.snapshot(),policy=await store.read('bundle','manual-policy');
 const sourceBinding={format:'rpg05-manual-source-binding/1',authorization:MANUAL_SOURCE_AUTHORIZATION,version:MANUAL_SOURCE_VERSION,priorFreezeSha256:oldFreeze,priorPolicySha256:policy.sha256,priorRecords:prior.map(r=>r.sha256)};
 return {root,store,automaticStore,prior,policy,oldOptions,old,options:{...oldOptions,freezeSha256:newFreeze,sourceBinding}};
}
test('cutover appends one binding, preserves consumed slots and stops at the same total five after restart',async t=>{
 const f=await fixture(t,['succeeded','cancelled']),oldBytes=await fs.readFile(path.join(f.root,'manual','operation-dispatch.1-00000002.json'));
 let ledger=await createManualLedger(f.options);assert.equal((await ledger.snapshot()).length,2);
 const binding=await f.store.read('bundle','manual-source-binding.closeout-1');assert.equal(binding.revision,1);
 for(const [i,outcome] of ['failed','succeeded','cancelled'].entries()){
  ledger=await createManualLedger(f.options);const r=await ledger.reserve(request('gen.new.'+i));assert.equal(r.payload.slot,3+i);assert.equal(r.payload.cumulativeSlot,29+i);assert.equal(r.payload.freezeSha256,newFreeze);await ledger.complete(r,result(outcome));
 }
 await assert.rejects((await createManualLedger(f.options)).reserve(request('gen.six')),/BUDGET_EXHAUSTED/);
 assert.equal((await f.store.read('bundle','manual-policy')).sha256,f.policy.sha256);assert.equal((await f.store.read('bundle','manual-source-binding.closeout-1')).sha256,binding.sha256);
 assert.deepEqual(await fs.readFile(path.join(f.root,'manual','operation-dispatch.1-00000002.json')),oldBytes);
 assert.deepEqual((await ledger.snapshot()).slice(0,2).map(r=>r.sha256),f.options.sourceBinding.priorRecords);
 assert.deepEqual((await f.automaticStore.list('operation')).sort((a,b)=>a.payload.slot-b.payload.slot).map(r=>r.sha256),f.options.approval.automaticRecords);
 await assert.rejects(createManualLedger(f.oldOptions),/SOURCE_VERSION_REQUIRED/);
 await assert.rejects(f.old.reserve(request('gen.old.runtime')),/SOURCE_VERSION_REQUIRED/);
});
test('zero-consumption cutover restarts without consuming and unknown result still seals the allowance',async t=>{
 const f=await fixture(t),first=await createManualLedger(f.options),second=await createManualLedger(f.options);
 assert.equal((await second.snapshot()).length,0);const reserved=await first.reserve(request('gen.unknown'));
 await first.complete(reserved,result('unknown'));await assert.rejects((await createManualLedger(f.options)).reserve(request('gen.next')),/UNRESOLVED/);
 await assert.rejects(first.complete(reserved,result('succeeded')),/COMPLETION_CONFLICT/);
});
test('a copied authorization cannot initialize a fresh empty cutover ledger',async t=>{
 const f=await fixture(t,['succeeded']);const empty=await createRecordStore(path.join(f.root,'empty'));
 await assert.rejects(createManualLedger({...f.options,store:empty}),/PRIOR_POLICY_REQUIRED/);assert.equal((await empty.list('bundle')).length,0);
});
test('prior unresolved dispatches cannot be hidden by a source cutover',async t=>{
 for(const outcome of ['reserved','unknown'])await t.test(outcome,async t=>{
  const f=await fixture(t,[outcome]);await assert.rejects(createManualLedger(f.options),/PRIOR_RECORDS_DRIFT/);assert.equal(await f.store.read('bundle','manual-source-binding.closeout-1'),null);
 });
});
test('cutover rejects altered authority, source identity, policy and prefix before writing a binding',async t=>{
 const f=await fixture(t,['succeeded']);
 const mutations=[b=>b.authorization='agent',b=>b.version='other',b=>b.priorFreezeSha256=newFreeze,b=>b.priorPolicySha256='e'.repeat(64),b=>b.priorRecords=[],b=>b.priorRecords=['f'.repeat(64)],b=>b.priorRecords=Array(6).fill('f'.repeat(64))];
 for(const [i,mutate] of mutations.entries())await t.test('mutation '+i,async()=>{
  const sourceBinding=structuredClone(f.options.sourceBinding);mutate(sourceBinding);await assert.rejects(createManualLedger({...f.options,sourceBinding}),/MANUAL_/);
  assert.equal(await f.store.read('bundle','manual-source-binding.closeout-1'),null);assert.equal((await f.old.snapshot()).length,1);
 });
 await assert.rejects(createManualLedger({...f.options,approval:{...f.options.approval,maxDispatches:6}}),/APPROVAL_INVALID/);
});
test('source binding revisions or another target freeze cannot silently redefine a completed cutover',async t=>{
 const f=await fixture(t),ledger=await createManualLedger(f.options);
 await assert.rejects(createManualLedger({...f.options,freezeSha256:'e'.repeat(64)}),/SOURCE_BINDING_DRIFT/);
 const bound=await f.store.read('bundle','manual-source-binding.closeout-1');await f.store.write('bundle',bound.id,{...bound.payload,freezeSha256:'f'.repeat(64)},1);
 await assert.rejects(ledger.reserve(request('gen.tamper')),/SOURCE_BINDING_DRIFT/);await assert.rejects(createManualLedger(f.options),/SOURCE_BINDING_DRIFT/);
});
test('new cutover implementation keeps concurrent reservations exclusive and rejects an old operation',async t=>{
 const f=await fixture(t,['succeeded']),a=await createManualLedger(f.options),b=await createManualLedger(f.options);
 await assert.rejects(a.reserve(request('gen.old.0')),/REPLAY_FORBIDDEN/);
 const attempts=await Promise.allSettled([a.reserve(request('gen.a')),b.reserve(request('gen.b'))]);assert.equal(attempts.filter(v=>v.status==='fulfilled').length,1);
 const r=attempts.find(v=>v.status==='fulfilled').value;assert.equal(r.payload.slot,2);await a.complete(r,result('succeeded'));
 assert.equal((await (await createManualLedger(f.options)).snapshot()).length,2);
});

import { startManualHost } from '../ui-host/manual-start.mjs';
import { createJourneyEngine } from '../ui-host/journeys.mjs';
import { loadBuiltinBundle } from '../ui-host/setup.mjs';
async function ownerFixture(f) {
 const store=await createRecordStore(path.join(f.root,'journeys'));
 return ()=>createJourneyEngine({store,root:path.join(f.root,'runtime'),hostTemplate:loadBuiltinBundle().hostTemplate,evidenceKind:'mock',adapterFor:()=>{throw Error('NO_TEST_GENERATION');}});
}
test('qualification fails before acquiring ownership or appending a source binding',async()=>{
 const calls=[];await assert.rejects(startManualHost({qualify:async()=>{calls.push('qualify');throw Error('unqualified');},acquireOwner:async()=>calls.push('owner'),bindSource:async()=>calls.push('bind'),listen:async()=>calls.push('listen')}),/unqualified/);
 assert.deepEqual(calls,['qualify']);
});
test('an existing runtime owner blocks migration before the source-binding callback',async t=>{
 const f=await fixture(t),open=await ownerFixture(f),oldOwner=await open();let bound=false;
 try {await assert.rejects(startManualHost({qualify:async()=>{},acquireOwner:open,bindSource:async()=>{bound=true;return createManualLedger(f.options);},listen:async()=>{throw Error('unexpected');}}),/OWNER_UNAVAILABLE/);assert.equal(bound,false);assert.equal(await f.store.read('bundle','manual-source-binding.closeout-1'),null);}
 finally {await oldOwner.close();}
});
test('binding failure releases the actual runtime owner and preserves the original policy',async t=>{
 const f=await fixture(t),open=await ownerFixture(f);
 await assert.rejects(startManualHost({qualify:async()=>{},acquireOwner:open,bindSource:()=>createManualLedger({...f.options,sourceBinding:{...f.options.sourceBinding,priorPolicySha256:'f'.repeat(64)}}),listen:async()=>{throw Error('unexpected');}}),/PRIOR_POLICY_REQUIRED/);
 const recovered=await open();await recovered.close();assert.equal(await f.store.read('bundle','manual-source-binding.closeout-1'),null);assert.equal((await f.store.read('bundle','manual-policy')).sha256,f.policy.sha256);
});
test('listen failure keeps the appended binding and permits only same-version startup with unchanged usage',async t=>{
 const f=await fixture(t,['succeeded']),open=await ownerFixture(f);
 const steps={qualify:async()=>{},acquireOwner:open,bindSource:()=>createManualLedger(f.options),listen:async()=>{throw Error('address in use');}};
 await assert.rejects(startManualHost(steps),/address in use/);const binding=await f.store.read('bundle','manual-source-binding.closeout-1');assert.equal(binding.revision,1);
 const restarted=await startManualHost({...steps,listen:async()=>({testOnly:true})});
 try {assert.equal((await restarted.access.snapshot()).length,1);assert.equal((await f.store.read('bundle',binding.id)).sha256,binding.sha256);await assert.rejects(open(),/OWNER_UNAVAILABLE/);}
 finally {await restarted.engine.close();}
 await assert.rejects(createManualLedger(f.oldOptions),/SOURCE_VERSION_REQUIRED/);
});
