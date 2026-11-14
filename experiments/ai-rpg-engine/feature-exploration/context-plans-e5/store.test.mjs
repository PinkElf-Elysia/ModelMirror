import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openSources} from '../context-actions-e4/source.mjs';import {PlanStore} from './store.mjs';import {SAMPLES,Simulator} from './provider.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/e5-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(provider){const dir=await mkdtemp(root+'store-'),sources=await openSources(dir+'/sources'),store=await PlanStore.open(dir+'/plans',sources,provider?{provider}:{});return {dir,sources,store};}
const cmd=async(s,kind,text,operationId='op-'+crypto.randomUUID(),ops)=>{const v=await s.view('cultivation');const c={adapterId:'cultivation',kind,text,operationId,expectedHash:v.recordHash};if(ops)c.ops=ops;return {c,v:await s.command(c)};};
test('save, revise, reopen and export keep world unchanged and raw receipts',async()=>{
 const f=await fixture();let s=f.store;const before=(await s.view('cultivation')).source;
 try{let {v}=await cmd(s,'reply',SAMPLES.cultivation[0]);assert.equal(v.operations[Object.keys(v.operations)[0]].receipt.status,'completed');
 await cmd(s,'save',null);const out=await s.export('cultivation');assert.equal(out.format,'modelmirror.rpg.e5-plan/1');assert.equal(out.executionStatus,'not_executed');
 await s.close();s=await PlanStore.open(f.dir+'/plans',f.sources);assert.equal((await s.view('cultivation')).plan.saved.meaning,'save-plan-only');
 v=(await cmd(s,'reply','第二步改成两份')).v;assert.equal(v.plan.saved,null);assert.equal(v.history.at(-1).diff[0].stepId,'step-2');
 assert.deepEqual(v.source,before);assert.equal(v.realCalls,0);assert.equal(v.simulatedCalls,2);
 }finally{await s.close();}
});
test('duplicate operation and stale version never call provider twice',async()=>{
 const f=await fixture();try{const {c,v}=await cmd(f.store,'reply',SAMPLES.cultivation[0],'same');
 const again=await f.store.command(c);assert.equal(again.simulatedCalls,1);
 await assert.rejects(f.store.command({...c,text:'改成两份'}),{code:'E5_OPERATION_REUSED'});
 await assert.rejects(f.store.command({...c,operationId:'new'}),{code:'E5_CONFLICT'});assert.equal(f.store.provider.calls,1);
 }finally{await f.store.close();}
});
test('unknown receipt survives reopen; malformed batch leaves prior plan intact',async()=>{
 const f=await fixture({mode:'offline',async generate(){return {status:'unknown'};}});let s=f.store;
 try{const {v}=await cmd(s,'reply',SAMPLES.cultivation[0]);assert.ok(v.pending);await s.close();s=await PlanStore.open(f.dir+'/plans',f.sources);
 await assert.rejects(cmd(s,'clear',null),{code:'E5_REQUEST_UNKNOWN'});assert.equal((await s.reconcile('cultivation')).pending,v.pending);
 }finally{await s.close();}
 const g=await fixture();try{await cmd(g.store,'reply',SAMPLES.cultivation[0]);const steps=(await g.store.view('cultivation')).plan.steps;
 g.store.provider={mode:'offline',async generate(){return {status:'completed',raw:'```json\n{'};}};
 const {v}=await cmd(g.store,'reply','坏回复');assert.deepEqual(v.plan.steps,steps);assert.equal(v.pending,null);assert.equal(v.history.at(-1).text,'坏回复');
 }finally{await g.store.close();}
});
test('different scenes isolate drafts, explicit clear does not reuse step IDs',async()=>{
 const f=await fixture();try{await cmd(f.store,'reply',SAMPLES.cultivation[0]);assert.equal((await f.store.view('lighthouse')).plan,null);
 await cmd(f.store,'clear',null);const {v}=await cmd(f.store,'reply',SAMPLES.cultivation[0]);assert.equal(v.plan.steps[0].id,'step-3');
 }finally{await f.store.close();}
});
