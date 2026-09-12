import test from 'node:test';import assert from 'node:assert/strict';
import {createProtocolLedger,PROTOCOL_AUTHORIZATION,createReviewedProtocolLedger,REVIEWED_PROTOCOL_AUTHORIZATION} from '../ui-host/protocol-ledger.mjs';import {hashValue} from '../ui-host/setup.mjs';
async function fixture(){const rows=new Map(),store={async read(k,id){return rows.get(k+id)??null;},async list(k){return [...rows.entries()].filter(([key])=>key.startsWith(k)).map(([,v])=>v);},async write(k,id,p,revision){if((rows.get(k+id)?.revision??0)!==revision)throw Error('REVISION_CONFLICT');const r={id,revision:revision+1,payload:p,sha256:hashValue({p,revision})};rows.set(k+id,r);return r;}};
 const priorPolicies=[];for(const id of ['dispatch-policy','dispatch-continuation','dispatch-extension-slot5','dispatch-structured-slot7','dispatch-keyed-slot12'])priorPolicies.push({id,sha256:(await store.write('bundle',id,{neutral:id},0)).sha256});
 const priorRecords=[];for(let slot=1;slot<=16;slot++){const p={slot,kind:[1,4,8,9,13].includes(slot)?'certification':[11,12].includes(slot)?'minecraft':'gu',operationId:'old.'+slot,status:'completed',outcome:[3,5,7,12,16].includes(slot)?'failed':'succeeded'};await store.write('operation','dispatch.'+slot,p,0);priorRecords.push((await store.write('operation','dispatch.'+slot,p,1)).sha256);}
 const options={store,freezeSha256:'a'.repeat(64),protocolContinuation:{authorization:PROTOCOL_AUTHORIZATION,maxDispatches:24,priorRecords,priorPolicies}};return {rows,options,ledger:await createProtocolLedger(options)};}
const req=(kind,id,maxTokens=4096)=>({kind,operationId:id,maxTokens,requestSha256:'b'.repeat(64)}),out=outcome=>({outcome,dispatched:true,reportSha256:'c'.repeat(64)});
test('appends exactly eight slots after failed16 and preserves every old hash',async()=>{const f=await fixture();for(const [i,kind] of ['certification','gu','gu','gu','minecraft','minecraft','minecraft','cancellation'].entries()){const r=await f.ledger.reserve(req(kind,'keyed.'+i,i?4096:512));assert.equal(r.payload.slot,17+i);await f.ledger.complete(r,out(i===7?'cancelled':'succeeded'));}assert.deepEqual((await f.ledger.snapshot()).slice(0,16).map(r=>r.sha256),f.options.protocolContinuation.priorRecords);await assert.rejects(f.ledger.reserve(req('gu','extra')),/BUDGET_EXHAUSTED/);});
test('new failure and unconfirmed result stop across restart; no auto retry',async()=>{for(const outcome of ['failed','unknown']){const f=await fixture(),r=await f.ledger.reserve(req('certification','probe',512));await f.ledger.complete(r,out(outcome));const restored=await createProtocolLedger(f.options);await assert.rejects(restored.reserve(req('gu','next')),/BATCH_STOPPED/);}});
test('wrong budget, category, caps and old history edits are rejected',async()=>{const f=await fixture();await assert.rejects(f.ledger.reserve(req('minecraft','bad')),/SCOPE_MISMATCH/);await assert.rejects(f.ledger.reserve(req('certification','large',513)),/SCOPE_MISMATCH/);await assert.rejects(createProtocolLedger({...f.options,protocolContinuation:{...f.options.protocolContinuation,maxDispatches:25}}),/APPROVAL_INVALID/);f.rows.get('operationdispatch.16').sha256='d'.repeat(64);await assert.rejects(f.ledger.snapshot(),/HISTORY_DRIFT/);});
test('concurrent reservation allows one; changed freeze cannot reopen ledger',async()=>{const f=await fixture();const races=await Promise.allSettled([f.ledger.reserve(req('certification','one',512)),f.ledger.reserve(req('certification','two',512))]);assert.equal(races.filter(r=>r.status==='fulfilled').length,1);await assert.rejects(createProtocolLedger({...f.options,freezeSha256:'e'.repeat(64)}),/CORRUPT|DRIFT/);});


async function reviewedFixture() {
 const f=await fixture();
 for(const [i,kind] of ['certification','gu'].entries()) {const r=await f.ledger.reserve(req(kind,'protocol.'+i,i?4096:512));await f.ledger.complete(r,out('succeeded'));}
 const priorRecords=(await f.ledger.snapshot()).map(r=>r.sha256),priorPolicies=[...f.options.protocolContinuation.priorPolicies,{id:'dispatch-protocol-slot16',sha256:(await f.options.store.read('bundle','dispatch-protocol-slot16')).sha256}];
 const options={store:f.options.store,freezeSha256:'e'.repeat(64),reviewedContinuation:{authorization:REVIEWED_PROTOCOL_AUTHORIZATION,maxDispatches:26,priorRecords,priorPolicies}};
 return {...f,reviewedOptions:options,reviewed:await createReviewedProtocolLedger(options)};
}
test('reviewed budget appends19-26 and retains18 outcomes plus all six old policies',async()=>{
 const f=await reviewedFixture(),prior=f.reviewedOptions.reviewedContinuation;
 for(const [i,kind] of ['certification','gu','gu','gu','minecraft','minecraft','minecraft','cancellation'].entries()) {const r=await f.reviewed.reserve(req(kind,'reviewed.'+i,i?4096:512));assert.equal(r.payload.slot,19+i);await f.reviewed.complete(r,out(i===7?'cancelled':'succeeded'));}
 assert.deepEqual((await f.reviewed.snapshot()).slice(0,18).map(r=>r.sha256),prior.priorRecords);
 for(const p of prior.priorPolicies) assert.equal((await f.options.store.read('bundle',p.id)).sha256,p.sha256);
 await assert.rejects(f.reviewed.reserve(req('gu','extra')),/BUDGET_EXHAUSTED/);
 assert.equal((await f.options.store.read('bundle','dispatch-reviewed-slot18')).payload.maxDispatches,26);
});
test('reviewed budget requires exact authorization,18 old hashes and six unique policies',async()=>{
 const f=await reviewedFixture();
 for(const mutate of [a=>a.maxDispatches=25,a=>a.maxDispatches=27,a=>a.authorization=PROTOCOL_AUTHORIZATION,a=>a.priorRecords.pop(),a=>a.priorPolicies.pop(),a=>a.priorPolicies[5]={...a.priorPolicies[0]}]) {
  const approval=structuredClone(f.reviewedOptions.reviewedContinuation);mutate(approval);await assert.rejects(createReviewedProtocolLedger({...f.reviewedOptions,reviewedContinuation:approval}),/APPROVAL_INVALID/);
 }
 f.rows.get('operationdispatch.18').sha256='f'.repeat(64);await assert.rejects(f.reviewed.snapshot(),/HISTORY_DRIFT/);
});
test('reviewed qualification and scenario caps cannot be reallocated or replayed',async()=>{
 const f=await reviewedFixture();
 await assert.rejects(f.reviewed.reserve(req('gu','skip')),/SCOPE_MISMATCH/);
 await assert.rejects(f.reviewed.reserve(req('certification','oversized',513)),/SCOPE_MISMATCH/);
 await assert.rejects(f.reviewed.reserve(req('certification','old.1',512)),/REPLAY_FORBIDDEN/);
 const probe=await f.reviewed.reserve(req('certification','probe',512));await f.reviewed.complete(probe,out('succeeded'));
 await assert.rejects(f.reviewed.reserve(req('minecraft','skip-world')),/SCOPE_MISMATCH/);
 await assert.rejects(f.reviewed.reserve(req('gu','oversized-scene',4097)),/SCOPE_MISMATCH/);
});
test('reviewed failed,cancelled,unknown and unresolved reservations stop after restart',async()=>{
 for(const outcome of ['failed','cancelled','unknown',null]) {const f=await reviewedFixture(),r=await f.reviewed.reserve(req('certification','probe',512));if(outcome)await f.reviewed.complete(r,out(outcome));const restored=await createReviewedProtocolLedger(f.reviewedOptions);await assert.rejects(restored.reserve(req('gu','next')),/BATCH_STOPPED/);}
});
test('reviewed CAS permits one concurrent dispatch and rejects freeze or old policy drift',async()=>{
 const f=await reviewedFixture(),results=await Promise.allSettled([f.reviewed.reserve(req('certification','one',512)),f.reviewed.reserve(req('certification','two',512))]);
 assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
 await assert.rejects(createReviewedProtocolLedger({...f.reviewedOptions,freezeSha256:'f'.repeat(64)}),/CORRUPT|DRIFT/);
 f.rows.get('bundledispatch-protocol-slot16').sha256='f'.repeat(64);await assert.rejects(f.reviewed.snapshot(),/HISTORY_DRIFT/);
});
