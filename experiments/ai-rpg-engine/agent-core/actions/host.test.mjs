import {empty} from '../../plugins/custom-config/public/config.mjs';
import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {openRuntime} from './composition.mjs';import {operationId} from '../contracts.mjs';
async function fixture(t,which='lighthouse'){
 const dir=await mkdtemp(join(tmpdir(),'r5-host-'));let rt=await openRuntime(dir,{offlinePreview:true}),h=rt.get(which);
 t.after(async()=>{await rt.close();await rm(dir,{recursive:true,force:true});});const r=await h.create({operationId:operationId(),initial:h.adapter.initial(),confirmed:true});
 return {r,get h(){return h;},async reopen(){await rt.close();rt=await openRuntime(dir,{offlinePreview:true});h=rt.get(which);}};
}
for(const which of ['lighthouse','cultivation'])test(which+' query → prepare → confirm → execution → narration → next turn',async t=>{
 const f=await fixture(t,which),h=f.h;let r=await h.turn(f.r.id,{operationId:'plan-one',revision:0,input:'先查询，再拟定使用两份的方案'});
 assert.equal(r.operations['plan-one'].stages.length,2);assert.equal(h.adapter.resources(r.state)[0].quantity,5);assert.equal(r.turns.length,0);
 assert.ok(r.proposals['plan-one']);const request={operationId:'exec-one',revision:r.revision,proposalId:'plan-one',proposalHash:r.proposals['plan-one'].proposal.hash};
 r=await h.execute(r.id,request);assert.equal(h.adapter.resources(r.state)[0].quantity,3);
 const state=structuredClone(r.state);r=await h.narrate(r.id);assert.deepEqual(r.state,state);assert.equal(r.turns.length,1);
 assert.equal(h.provider.calls,3);r=await h.turn(r.id,{operationId:'talk',revision:r.revision,input:'聊聊今日安排'});assert.equal(h.provider.calls,4);assert.deepEqual(r.state,state);
 await f.reopen();assert.deepEqual((await f.h.view(r.id)).state,state);assert.equal(f.h.provider.calls,0);
});
test('only query takes two calls, ordinary text one, malformed tool never changes state',async t=>{
 const f=await fixture(t),h=f.h;let r=await h.turn(f.r.id,{operationId:'query',revision:0,input:'查看登记物资'});
 assert.equal(h.provider.calls,2);assert.deepEqual(r.state,r.initial);
 r=await h.turn(r.id,{operationId:'invalid',revision:r.revision,input:'拟定使用两份的方案'},{fault:'invalid'});
 assert.equal(r.operations.invalid.status,'rejected');assert.deepEqual(r.state,r.initial);
});
test('narration failure, unknown recovery and explicit retry never execute twice',async t=>{
 const f=await fixture(t),h=f.h;let r=await h.execute(f.r.id,{operationId:'exec-one',revision:0,action:{actionId:'fuel_lamp',itemId:'lamp-oil',quantity:2}});
 r=await h.narrate(r.id,{fault:'failed'});assert.equal(r.operations[r.pending].status,'failed');assert.equal(r.state.supplies[0].quantity,3);
 r=await h.narrate(r.id,{fault:'unknown'});const calls=h.provider.calls;assert.equal(r.operations[r.pending].status,'unknown');
 await assert.rejects(h.narrate(r.id),{code:'STATE_RESULT_UNKNOWN'});await f.reopen();r=await f.h.recover(r.id);
 assert.equal(r.pending,null);assert.equal(r.events.length,1);assert.equal(r.turns.length,1);assert.equal(r.state.supplies[0].quantity,3);assert.equal(f.h.provider.calls,0);assert.equal(calls,2);
});
test('post execution delta refused; receipt can be retained and turn ended',async t=>{
 const f=await fixture(t),h=f.h;let r=await h.execute(f.r.id,{operationId:'exec-one',revision:0,action:{actionId:'fuel_lamp',itemId:'lamp-oil',quantity:2}});
 r=await h.narrate(r.id,{fault:'write'});assert.equal(r.operations[r.pending].error,'ACTION_NARRATION_WRITE');
 r=await h.action(r.id,'discard',{operationId:'keep-receipt',revision:r.revision,pendingId:r.pending,reason:'只保留回执'});
 assert.equal(r.pending,null);assert.equal(r.events.length,1);assert.equal(r.state.supplies[0].quantity,3);
});

test('R5 retains real plugin assembly and committed-only memory across action narration',async t=>{
 const f=await fixture(t,'cultivation'),h=f.h,c=empty();c.prefix='青竹';c.suffix='收尾';
 c.worldbook.entries=[{id:'prior',name:'prior',categoryId:null,keywords:['基础吐纳法'],body:'来自本地历史面板的原有触发',depth:1,order:0,mode:'regular',enabled:true,regex:false,chance:100,contents:[],roles:['assistant'],targets:['system'],rule:'or'}];
 c.rules=[{id:'mask',name:'显示名',pattern:'养息丹',replacement:'药丸',scope:'assistant',enabled:true,renderOnly:false,flags:'g'}];
 let r=await h.create({operationId:operationId(),initial:h.adapter.initial(),confirmed:true,config:{profiles:[c],mods:[]}});
 const initial=structuredClone(r.state);
 r=await h.turn(r.id,{operationId:'plugin-plan',revision:r.revision,input:'先查询，再拟定使用两份的方案'});
 const stages=r.operations['plugin-plan'].stages;assert.deepEqual(stages[1].request.messages.slice(0,stages[0].request.messages.length),stages[0].request.messages);
 r=await h.execute(r.id,{operationId:'plugin-exec',revision:r.revision,proposalId:'plugin-plan',proposalHash:r.proposals['plugin-plan'].proposal.hash});
 r=await h.narrate(r.id);const other=structuredClone(r.state);other.items[0].quantity=initial.items[0].quantity;assert.deepEqual(other,initial);
 const next=await h.adapter.assembleTask(r,'聊聊今日安排');assert.match(next.messages[0].content,/来自本地历史面板的原有触发/);
 const history=JSON.stringify(next.messages.filter(m=>m.role==='assistant'));assert.match(history,/药丸/);assert.doesNotMatch(history,/tool_call_id|state_delta|contextTrace/);
 const memory=JSON.stringify(await h.adapter.memory(r,r.turns[0]));assert.match(memory,/药丸/);assert.match(memory,/items.medicine.quantity/);assert.doesNotMatch(memory,/tool_call_id|prepare_action/);
});
test('ending failed narration retains confirmed action evidence in the next ordinary request',async t=>{
 const f=await fixture(t),h=f.h;let r=await h.execute(f.r.id,{operationId:'retained-action',revision:0,action:{actionId:'fuel_lamp',itemId:'lamp-oil',quantity:2}});
 r=await h.narrate(r.id,{fault:'failed'});
 r=await h.action(r.id,'discard',{operationId:'discard',revision:r.revision,pendingId:r.pending,reason:'只保留回执'});
 const context=await h.adapter.assembleTask(r,'聊聊今日安排');
 assert.ok(context.contextTrace.materials.some(m=>m.id==='unnarrated-actions'&&m.selected));assert.match(context.messages[0].content,/retained-action/);assert.equal(r.state.supplies[0].quantity,3);
});
