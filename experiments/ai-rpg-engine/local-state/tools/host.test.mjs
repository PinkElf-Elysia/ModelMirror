import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {ToolsHost} from './host.mjs';import {exampleState} from '../example.mjs';import {hash} from '../definition.mjs';
async function setup(t){
 const dir=await mkdtemp(join(tmpdir(),'rpg-native-'));let host=await ToolsHost.open({directory:dir,enabled:true});
 t.after(async()=>{await host.close();assert.ok(dir.startsWith(join(tmpdir(),'rpg-native-')));await rm(dir,{recursive:true,force:true});});
 const r=await host.create({operationId:'init',initial:exampleState(),confirmed:true});
 return {host,r,reopen:async()=>{await host.close();host=await ToolsHost.open({directory:dir,enabled:true});return host;}};
}
test('direct and native-query rounds, only final increment commits, no protocol in memory',async t=>{
 const {host,r}=await setup(t);let n=await host.turn(r.id,{operationId:'chat',revision:0,input:'聊聊接下来的打算'});
 assert.equal(host.provider.calls,1);assert.equal(n.operations.chat.native.stages.length,1);
 const request={operationId:'query',revision:n.revision,input:'先查询丹药，再服用两份养息丹'};
 n=await host.turn(r.id,request);assert.equal(host.provider.calls,3);assert.equal(n.state.items[0].quantity,3);assert.equal(n.turns.length,2);
 const stages=n.operations.query.native.stages;assert.equal(stages.length,2);assert.equal(stages[1].request.messages.at(-1).role,'tool');
 assert.equal(stages[1].request.messages.at(-2).tool_calls[0].extra_content.google.thought_signature,'offline-opaque-signature');
 assert.equal(n.operations.query.native.query.results[0].entries[0].quantity,5);
 await host.turn(r.id,request);assert.equal(host.provider.calls,3);
 const proof=await host.evidence(r.id);assert.ok(!JSON.stringify(proof.memoryTasks).includes('tool_calls'));
 const view=await host.view(r.id);assert.ok(!JSON.stringify(view).includes('opaque-signature'));assert.ok(view.panels.includes('养息丹 × 3'));
});
test('unknown final receipt recovers after restart with no replay',async t=>{
 const {host,r,reopen}=await setup(t);let n=await host.turn(r.id,{operationId:'q',revision:0,input:'先查询丹药，再服用两份养息丹'},{fault:'unknown'});
 assert.equal(n.state.items[0].quantity,5);assert.equal(n.operations.q.status,'unknown');assert.equal(host.provider.calls,2);
 const again=await reopen();n=await again.recover(r.id);assert.equal(n.state.items[0].quantity,3);assert.equal(again.provider.calls,0);
 await again.recover(r.id);assert.equal((await again.store.read(r.id)).turns.length,1);
});
test('unknown first result resumes at next planned stage, query-only preserves state',async t=>{
 const {host,r,reopen}=await setup(t);await host.turn(r.id,{operationId:'q',revision:0,input:'查看全部已登记丹药'},{fault:'unknown-first'});
 const again=await reopen(),n=await again.recover(r.id);assert.equal(again.provider.calls,1);assert.equal(hash(n.state),hash(r.state));assert.equal(n.turns.length,1);
});
for(const fault of ['failed','truncated','invalid','repeat'])test('failure without settlement or automatic retry: '+fault,async t=>{
 const {host,r}=await setup(t);let n=await host.turn(r.id,{operationId:'q',revision:0,input:'先查询丹药，再服用两份养息丹'},{fault});
 assert.equal(hash(n.state),hash(r.state));assert.equal(n.turns.length,0);const calls=host.provider.calls;await host.recover(r.id);assert.equal(host.provider.calls,calls);
 assert.ok(['failed','rejected'].includes(n.operations.q.status));
});
test('explicit rewrite blocks late result, fork and candidate adoption remain consistent',async t=>{
 const {host,r}=await setup(t),input='服用两份养息丹';
 const p=await host.preview(r.id,{input,variant:'warm'});assert.equal(host.provider.calls,0);
 let n=await host.turn(r.id,{operationId:'adopt',revision:0,input},{variant:'warm',previewHash:p.previewHash});
 assert.equal(n.state.items[0].quantity,3);assert.equal(host.provider.calls,0);assert.equal(n.turns[0].body,p.body);
 const child=await host.store.fork(r.id,{operationId:'fork',revision:n.revision,eventId:null});assert.equal(child.state.items[0].quantity,5);
 n=await host.turn(r.id,{operationId:'unknown',revision:n.revision,input:'查看全部已登记丹药'},{fault:'unknown'});
 await host.store.update(r.id,row=>{row.stateRevision++;});
 await assert.rejects(()=>host.recover(r.id),{code:'STATE_STALE_RESULT'});
});

test('frozen selection cannot switch silently and pre-dispatch budget failure is definite',async t=>{
 const {host,r}=await setup(t);const original=host.provider.generate.bind(host.provider);
 host.provider.generate=async(...args)=>{if(args.at(-1).stage===2)throw Object.assign(Error('budget'),{code:'NATIVE_BUDGET'});return original(...args);};
 let n=await host.turn(r.id,{operationId:'q',revision:0,input:'查看全部已登记丹药'});
 assert.equal(n.operations.q.status,'failed');assert.equal(n.turns.length,0);assert.equal(host.provider.calls,1);
 n=await host.store.discard(r.id,{operationId:'discard',revision:n.revision,pendingId:'q',reason:'预算不足，结束未完成回合'});
 host.provider.generate=original;
 n=await host.turn(r.id,{operationId:'second',revision:n.revision,input:'查看全部已登记丹药'},{fault:'unknown-first'});
 host.provider.selection={model:'changed'};
 n=await host.recover(r.id);assert.equal(n.operations.second.status,'rejected');assert.equal(n.operations.second.error,'NATIVE_SELECTION_CHANGED');
});

test('direct unknown result also recovers once without a second generation',async t=>{
 const {host,r,reopen}=await setup(t);let n=await host.turn(r.id,{operationId:'direct',revision:0,input:'服用两份养息丹'},{fault:'unknown'});
 assert.equal(n.operations.direct.status,'unknown');assert.equal(n.state.items[0].quantity,5);
 const again=await reopen();n=await again.recover(r.id);assert.equal(again.provider.calls,0);assert.equal(n.state.items[0].quantity,3);
});