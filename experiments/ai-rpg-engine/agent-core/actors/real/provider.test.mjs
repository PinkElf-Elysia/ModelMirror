import test from 'node:test';import assert from 'node:assert/strict';
import {ActorRealProvider,MODEL,SLOTS} from './provider.mjs';import {hash,clone} from '../../contracts.mjs';import {QUERY_TOOL} from '../observations.mjs';
function setup(){let row=null,posts=0;const records={async reconcile(){},async get(){return clone(row)},async put(k,old,data){assert.equal(old,row?.hash||null);row={hash:hash(data),data:clone(data)};return row}};
 const selections=Object.fromEntries(Object.values(SLOTS).map(s=>[s.profile,{model:MODEL,available:true,selectionId:s.profile,selectionRevision:'revision',parameters:{temperature:.7,top_p:.8,max_tokens:s.max}}]));
 const authorization={id:'unit',maximumCalls:3,maximumUSD:.50,expiresAt:new Date(Date.now()+60000).toISOString(),pricing:{inputUSD:.00000075,outputUSD:.00000375,quoteId:'quote'},requests:Object.fromEntries([1,2,3].map(slot=>['s'+slot,{slot,session:'session',inputHash:hash('question')}]))};
 const p=new ActorRealProvider({records,url:'http://127.0.0.1:18892',serviceToken:'x'.repeat(48),selections,authorization,mode:'mock'});
 p.request=async(path)=>{if(path.startsWith('../progress'))return {status:404,body:{}};posts++;return {status:200,body:{}}};p.decode=()=>({status:'completed',finishReason:'stop',message:{role:'assistant',content:'ok'}});
 return {p,posts:()=>posts,mutate(fn){fn(row.data);row.hash=hash(row.data)}};
}
const req=(slot=1)=>({messages:[{role:'system',content:'host'},{role:'assistant',content:'greeting'},{role:'user',content:'question'}],tools:slot===2?[QUERY_TOOL]:[],tool_choice:slot===2?'auto':'none'});
const meta={input:'question'};
test('authorized slots only; duplicate returns original without dispatch; changed request rejected',async()=>{const t=setup();await assert.rejects(()=>t.p.generate('session','foreign',req(),meta));await t.p.generate('session','s1',req(),meta);await t.p.generate('session','s1',req(),meta);assert.equal(t.posts(),1);const changed=req();changed.messages[2].content='different';await assert.rejects(()=>t.p.generate('session','s1',changed,meta));assert.equal(t.posts(),1)});
test('per-call byte reserve rejects oversized input before HTTP',async()=>{const t=setup(),r=req();r.messages[0].content='x'.repeat(170000);await assert.rejects(()=>t.p.generate('session','s1',r,meta),/CORE_REAL_CALL_BUDGET/);assert.equal(t.posts(),0)});
test('unknown first result prevents later slot and duplicate cannot retry',async()=>{const t=setup();t.p.decode=()=>({status:'unknown'});await t.p.generate('session','s1',req(),meta);await assert.rejects(()=>t.p.generate('session','s2',req(2),meta),/R6_PREVIOUS_NOT_COMPLETE/);await t.p.generate('session','s1',req(),meta);assert.equal(t.posts(),1)});
test('cumulative reserve cannot reset across requests',async()=>{const t=setup();await t.p.generate('session','s1',req(),meta);t.mutate(d=>d.reservedMicros=499999);await assert.rejects(()=>t.p.generate('session','s2',req(2),meta),/R6_TOTAL_BUDGET/);assert.equal(t.posts(),1)});
