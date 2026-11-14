import {test} from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {SessionRecords} from '../../../authoring/session-storage.mjs';
import {actionAdapters} from '../adapters.mjs';import {savedProposal,replayThenNarrate} from './recovery.mjs';
import {ActionRealProvider,MODEL,SLOTS} from './provider.mjs';
test('recovery authorization cannot dispatch planning or a second narrative',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'r5-recovery-')),records=await SessionRecords.open(directory,{scope:'test'});
 t.after(async()=>{await records.close();await rm(directory,{recursive:true,force:true});});
 const s=SLOTS[3],selections={[s.profile]:{model:MODEL,available:true,selectionId:'selection',selectionRevision:'revision',parameters:{temperature:.7,top_p:.8,max_tokens:s.max}}};
 const p=new ActionRealProvider({records,url:'http://127.0.0.1:18872',serviceToken:'x'.repeat(40),selections,mode:'mock',authorization:{id:'recovery-test',kind:'saved-proposal-narration',maximumCalls:1,maximumUSD:.30,expiresAt:new Date(Date.now()+60000).toISOString(),pricing:{inputUSD:.00000075,outputUSD:.00000375,quoteId:'test'}}});
 let sent=0;p.request=async path=>{if(path==='chat/completions')sent++;return {status:200,body:{}}};p.decode=()=>({status:'unknown'});
 const req={messages:[{role:'user',content:'narration'}],tools:[],tool_choice:'none'};
 assert.equal((await p.generate('s','plan',{...req,tool_choice:'auto'},{purpose:'plan',stage:1})).status,'failed');assert.equal(sent,0);
 await p.generate('s','narrate',req,{purpose:'narration',stage:1});
 await p.generate('s','narrate',req,{purpose:'narration',stage:1});assert.equal(sent,1);
 await assert.rejects(p.generate('s','second',req,{purpose:'narration',stage:1}));
 assert.equal(sent,1);assert.ok((await p.read()).data.reservedMicros<=300000);
});
test('saved response replay never dispatches provider and is single use',async()=>{
 const adapter=(await actionAdapters()).find(a=>a.id==='cultivation'),result={status:'completed',message:{role:'assistant',tool_calls:[]}};
 const saved={input:'input',initial:adapter.initial(),tools:adapter.tools.tools(),result,source:{}};
 let sent=0;const wrapper=replayThenNarrate({mode:'mock',calls:0,options:x=>x,generate:async()=>{sent++;return {status:'completed'};}},saved);
 const req={tools:saved.tools,tool_choice:'auto'},meta={purpose:'plan',stage:1,input:saved.input,state:saved.initial};
 assert.equal((await wrapper.generate('s','p',req,meta)).provider,'saved-original-native-response');assert.equal(sent,0);
 await assert.rejects(wrapper.generate('s','p',req,meta));
 await wrapper.generate('s','n',{tool_choice:'none'},{purpose:'narration'});assert.equal(sent,1);
});
