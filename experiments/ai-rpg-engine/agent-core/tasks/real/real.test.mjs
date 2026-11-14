import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {TaskLedger} from '../ledger.mjs';import {TaskExecutor} from '../executor.mjs';import {totals,policy} from '../policy.mjs';import {policyFor,SLOTS,validateGrant} from './contract.mjs';
const quote={inputUSD:.00000075,outputUSD:.00000375,quoteId:'verified-test'},req={messages:[{role:'user',content:'fixture'}],tools:[],tool_choice:'none'};
async function ledger(t){const root=fileURLToPath(new URL('../../../.rpg03-work/r7-validation/',import.meta.url));await mkdir(root,{recursive:true});const l=await TaskLedger.open(await mkdtemp(root+'real-unit-'),{calls:3,microUSD:600000});await l.change(r=>r.accountingMode='real');t.after(()=>l.close());return l;}
test('real strategy is explicit and cannot configure retry, backup or excessive quote',()=>{
 assert.throws(()=>new TaskExecutor({}, {mode:'real'}),/R7_REAL_DISABLED/);
 assert.equal(policy(policyFor(SLOTS[1],quote)).model.parameters.max_tokens,32768);
 assert.throws(()=>policy({...policyFor(SLOTS[1],quote),maxCalls:2}),/R7_REAL_POLICY/);
 assert.throws(()=>policy(policyFor(SLOTS[1],{...quote,inputUSD:.1})),/R7_REAL_PRICE/);
 assert.throws(()=>validateGrant({taskId:'a',session:'s',purpose:'actor-response'},{taskId:'b',session:'s',purpose:'actor-response',attempt:0,request:req}),/R7_REAL_GRANT/);
});
test('shared executor keeps real timing and one-attempt policy; real costs are not simulated',async t=>{
 const l=await ledger(t);await l.begin({id:'task',session:'session',purpose:'actor-response',sourceHash:'frozen',ownerRef:{}},policyFor(SLOTS[2],quote));let calls=0;
 const result={status:'completed',finishReason:'stop',message:{role:'assistant',content:'reply'},billing:{kind:'provider',microUSD:500},timing:{proseFirstMs:123,source:'host-progress-poll'}};
 const tr={mode:'mock',budgetPayload:p=>({...p.request,parameters:p.model.parameters,sessionId:p.session,requestId:p.id}),generate:async p=>{calls++;assert.equal(p.model.id,'google/gemini-3.8-flash');return result;}};
 const e=new TaskExecutor(l,tr,{controlledReal:true});await e.using('task',()=>e.generate('session','stage',req,{}));await e.using('task',()=>e.generate('session','stage',req,{}));
 const r=await l.read();assert.equal(calls,1);assert.equal(r.tasks.task.stages[0].attempts[0].timing.proseFirstMs,123);assert.equal(totals(r).actualUSD,.0005);assert.equal(totals(r).simulatedKnownMicroUSD,0);
});
test('unknown real result keeps reservation and is not reported as zero cost',async t=>{
 const l=await ledger(t);await l.begin({id:'task',session:'session',purpose:'actor-response',sourceHash:'frozen',ownerRef:{}},policyFor(SLOTS[2],quote));let calls=0;
 const e=new TaskExecutor(l,{mode:'mock',generate:async()=>{calls++;return {status:'unknown'};},lookup:async()=>null},{controlledReal:true});
 await e.using('task',()=>e.generate('session','stage',req,{}));await e.lookup('session','stage');assert.equal(calls,1);const sum=totals(await l.read());assert.equal(sum.actualUSD,null);assert.ok(sum.reservedUnknownMicroUSD>0);
});
