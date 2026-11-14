import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {join,resolve} from 'node:path';import {tmpdir} from 'node:os';
import {SessionRecords} from '../../../authoring/session-storage.mjs';
import {ContextRealProvider,reserve,SLOTS,MODEL} from './provider.mjs';
import {CoreHost} from '../../host.mjs';import {adapters} from '../composition.mjs';import {CONTINUITY} from '../adapters.mjs';

const pricing={inputUSD:.00000075,outputUSD:.00000375,quoteId:'test-only'};
const selections=Object.fromEntries(['god-long-v1','core-query-r3'].map(p=>[p,{model:MODEL,available:true,selectionId:'s-'+p,selectionRevision:'v',parameters:{temperature:.7,top_p:.8,max_tokens:p==='god-long-v1'?32768:8192}}]));
const authorization={id:'r4-test',maximumCalls:3,maximumUSD:.50,expiresAt:new Date(Date.now()+86400000).toISOString(),pricing};
test('three immutable slots reserve full output and reject old or excessive authorization',async()=>{
 assert.equal(SLOTS[1].cap,.30);assert.equal(SLOTS[2].cap,.10);assert.equal(SLOTS[3].max,8192);
 assert.ok(reserve({messages:'a'.repeat(131000)},pricing,SLOTS[1]).reserveMicros<300000);
 assert.throws(()=>reserve({messages:'a'.repeat(300000)},pricing,SLOTS[1]),/CALL_BUDGET/);
 assert.throws(()=>new ContextRealProvider({url:'http://127.0.0.1:18852',serviceToken:'x'.repeat(40),selections,adapter:'cultivation',slotId:1,authorization:{...authorization,maximumUSD:.9},mode:'mock'}),/AUTHORIZATION/);
});
test('duplicate requests, extra stages and occupied slots cannot spend again',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'r4-provider-')),records=await SessionRecords.open(directory,{scope:'r4-budget-test'});let posts=0;
 try{
 const make=slotId=>{const p=new ContextRealProvider({records,url:'http://127.0.0.1:18852',serviceToken:'x'.repeat(40),selections,adapter:SLOTS[slotId].adapter,slotId,authorization,mode:'mock'});p.fetcher=async(url,init)=>{if(init.method==='POST')posts++;return new Response('{}',{status:404});};return p;};
 const req={messages:[{role:'system',content:'s'},{role:'user',content:'u'}],tools:[],tool_choice:'none'},one=make(1);
 assert.equal((await one.generate('s','one',req,{stage:1})).status,'unknown');
 await one.generate('s','one',req,{stage:1});assert.equal(posts,1);
 assert.equal((await one.generate('s','extra',req,{stage:2})).status,'failed');assert.equal(posts,1);
 await assert.rejects(one.generate('s','other',req,{stage:1}),/TOTAL_BUDGET/);
 await assert.rejects(make(2).generate('s','auto',{...req,tool_choice:'auto'},{stage:1}),/DIRECT_ONLY/);
 await make(2).generate('l','two',req,{stage:1});await make(3).generate('l','three',req,{stage:1});
 assert.equal(posts,3);const row=await records.get('context-r4-budget',x=>x);assert.equal(row.data.calls.length,3);assert.ok(row.data.reservedMicros<500000);
 }finally{await records.close();assert.equal(resolve(directory).startsWith(resolve(tmpdir())+requireSlash(directory)),true);await rm(directory,{recursive:true,force:true});}
});
function requireSlash(p){return p.includes('\\')?'\\':'/';}
test('direct policy is frozen and lighthouse second turn contains the first committed prose',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'r4-direct-')),registry=await adapters(),adapter=registry.list.find(a=>a.id==='lighthouse'),requests=[];
 const provider={mode:'mock',kind:'fixture-direct',toolChoice:'none',options:v=>({contextOptions:v.contextOptions||{}}),close:async()=>{},lookup:async()=>null,
 generate:async(session,id,request,meta)=>{requests.push(request);return {status:'completed',finishReason:'stop',provider:'fixture-direct',usage:null,message:{role:'assistant',content:adapter.fixture(request,meta).text}};}};
 let host;try{
 host=await CoreHost.open({directory,adapter,offlinePreview:true,provider});const initial=adapter.initial();initial.duty=CONTINUITY[0].duty;
 let record=await host.create({operationId:'create',initial,confirmed:true,source:{kind:'new',id:'explicit-pending-promise',history:[]}});
 record=await host.turn(record.id,{operationId:'claim',revision:record.revision,input:CONTINUITY[1].input},{contextOptions:{selection:{mode:'scoped',refs:['scene:lighthouse']}}});
 assert.equal(record.operations.claim.status,'completed');assert.equal(record.state.duty,initial.duty);assert.equal(record.operations.claim.context.tool_choice,'none');
 const previousBody=record.turns[0].body;
 record=await host.turn(record.id,{operationId:'complete',revision:record.revision,input:CONTINUITY[3].input},{contextOptions:{selection:{mode:'scoped',refs:['scene:lighthouse']}}});
 assert.equal(record.operations.complete.status,'completed');assert.equal(record.state.duty,CONTINUITY[3].duty);assert.deepEqual(record.state.supplies,initial.supplies);
 assert.equal(requests.length,2);assert.ok(requests.every(r=>r.tool_choice==='none'));assert.ok(requests[1].messages.some(m=>m.role==='assistant'&&m.content===previousBody));
 }finally{if(host)await host.close();await rm(directory,{recursive:true,force:true});}
});
