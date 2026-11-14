import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {SessionRecords} from '../../authoring/session-storage.mjs';import {CoreRealProvider,reserve,SLOTS,MODEL} from './provider.mjs';import {CoreHost} from '../host.mjs';
const pricing={inputUSD:.00000075,outputUSD:.00000375,quoteId:'test'};
const selections=Object.fromEntries(['god-long-v1','core-query-r3'].map(profile=>[profile,{model:MODEL,available:true,selectionId:'s-'+profile,selectionRevision:'v',parameters:{temperature:.7,top_p:.8,max_tokens:profile==='god-long-v1'?32768:8192}}]));
const authorization={id:'test',maximumCalls:3,maximumUSD:.90,expiresAt:new Date(Date.now()+86400000).toISOString(),pricing};
test('real admission covers prompt and maximum output without cache discounts',()=>{assert.ok(reserve({messages:'a'.repeat(540000)},pricing,SLOTS.cultivation[1]).reserveMicros<600000);assert.throws(()=>reserve({messages:'a'.repeat(800000)},pricing,SLOTS.cultivation[1]),/CALL_BUDGET/);assert.equal(SLOTS.lighthouse[1].max,8192);assert.equal(SLOTS.lighthouse[2].cap,.20);});
test('durable shared slots refuse redispatch, extra cultivation stage and cross-case reuse',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'core-r3-real-test-')),records=await SessionRecords.open(directory,{scope:'rpg-core-r3-real-budget'});let outbound=0;
 try{const make=adapter=>new CoreRealProvider({records,url:'http://127.0.0.1:18842',serviceToken:'test-token-'.repeat(5),selections,adapter,authorization,mode:'mock'});
 const a=make('cultivation'),b=make('lighthouse');for(const p of[a,b])p.fetcher=async(url,init)=>{if(init.method==='POST')outbound++;return new Response('{}',{status:404});};
 const request={messages:[{role:'system',content:'s'},{role:'user',content:'u'}],tools:[],tool_choice:'auto'};
 assert.equal((await a.generate('s','one',request,{stage:1})).status,'unknown');
 assert.equal((await a.generate('s','one',request,{stage:1})).status,'unknown');assert.equal(outbound,1);
 assert.equal((await a.generate('s','unexpected',request,{stage:2})).status,'failed');assert.equal(outbound,1);
 await assert.rejects(a.generate('s','different',request,{stage:1}),/TOTAL_BUDGET/);
 await b.generate('t','two',request,{stage:1});await b.generate('t','three',{...request,tool_choice:'none'},{stage:2});
 assert.equal(outbound,3);const row=await records.get('core-real-budget',x=>x);assert.equal(row.data.calls.length,3);assert.ok(row.data.reservedMicros<=900000);
 }finally{await records.close();assert.ok(directory.startsWith(join(tmpdir(),'core-r3-real-test-')));await rm(directory,{recursive:true,force:true});}
});
test('real provider cannot enter through the offline-preview flag',async()=>{await assert.rejects(CoreHost.open({offlinePreview:true,provider:{mode:'real'}}),/CORE_PREVIEW_DISABLED/);});
