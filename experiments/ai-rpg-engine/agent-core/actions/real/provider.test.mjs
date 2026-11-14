import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {ActionRealProvider,MODEL,SLOTS,reserve} from './provider.mjs';
const quote={inputUSD:.00000075,outputUSD:.00000375,quoteId:'test'};
test('fixed slot and total limits reserve maximum output without cache discounts',()=>{
 for(const slot of Object.values(SLOTS))assert.ok(reserve({messages:[{content:'x'.repeat(130000)}]},quote,slot).reserveMicros<=slot.cap*1e6);
 assert.throws(()=>reserve({messages:[{content:'x'.repeat(1000000)}]},quote,SLOTS[1]),{code:'CORE_REAL_CALL_BUDGET'});
});
test('durable duplicate and stage limits never replay a dispatched request',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'r5-provider-')),records=await SessionRecords.open(dir,{scope:'test'});
 t.after(async()=>{await records.close();await rm(dir,{recursive:true,force:true});});
 const selections={};for(const s of Object.values(SLOTS))selections[s.profile]={model:MODEL,available:true,selectionId:'selection',selectionRevision:'revision',parameters:{temperature:.7,top_p:.8,max_tokens:s.max}};
 const p=new ActionRealProvider({records,url:'http://127.0.0.1:18872',serviceToken:'x'.repeat(40),selections,mode:'mock',authorization:{id:'r5-test',maximumCalls:3,maximumUSD:.60,expiresAt:new Date(Date.now()+60000).toISOString(),pricing:quote}});
 let sent=0;p.request=async(path)=>{if(path==='chat/completions')sent++;return {status:200,body:{}}};p.decode=()=>({status:'unknown'});
 const req={messages:[{role:'system',content:'rules'},{role:'user',content:'input'}],tools:[],tool_choice:'auto'};
 const meta={purpose:'plan',stage:1};await p.generate('session','first',req,meta);await p.generate('session','first',req,meta);assert.equal(sent,1);
 await assert.rejects(p.generate('session','different',req,meta));
 assert.equal((await p.generate('session','third',req,{purpose:'plan',stage:3})).status,'failed');
 assert.equal(sent,1);assert.equal((await p.read()).data.calls.length,1);
});
