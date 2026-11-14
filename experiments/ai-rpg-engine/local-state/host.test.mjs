import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {LocalStateHost} from './host.mjs';import {exampleState} from './example.mjs';
async function setup(t){const dir=await mkdtemp(join(tmpdir(),'rpg-local-host-'));let host=await LocalStateHost.open({directory:dir,enabled:true});
 t.after(async()=>{await host.close();assert.ok(dir.startsWith(join(tmpdir(),'rpg-local-host-')));await rm(dir,{recursive:true,force:true});});
 const r=await host.create({operationId:'init',initial:exampleState(),confirmed:true});
 return {host,r,reopen:async()=>{await host.close();host=await LocalStateHost.open({directory:dir,enabled:true});return host;}};}
test('complete offline chat, raw evidence, local panels, continuation and refresh',async t=>{
 const {host,r,reopen}=await setup(t);let next=await host.turn(r.id,{operationId:'t1',revision:r.revision,input:'服用两份养息丹'});
 assert.equal(next.state.items[0].quantity,3);assert.equal(next.turns.length,1);
 next=await host.turn(r.id,{operationId:'t2',revision:next.revision,input:'帮周禾扶好竹架'});assert.equal(next.state.relations[0].relation,8);
 const view=await host.view(r.id);assert.ok(view.panels.includes('养息丹 × 3'));assert.ok(view.turns[1].html.includes('关系 8'));
 const proof=await host.evidence(r.id);assert.ok(proof.record.operations.t1.receipt.text.includes('<state_delta>'));
 assert.ok(proof.memoryTasks.summary[1].content.includes('已提交变化'));
 const again=await reopen();assert.equal((await again.view(r.id)).state.relations[0].relation,8);
});
test('unknown result recovers original persisted receipt across restart without dispatch',async t=>{
 const {host,r,reopen}=await setup(t);await host.turn(r.id,{operationId:'t1',revision:r.revision,input:'服用两份养息丹'},{fault:'unknown'});
 assert.equal((await host.store.read(r.id)).state.items[0].quantity,5);const again=await reopen();
 const next=await again.recover(r.id);assert.equal(next.state.items[0].quantity,3);assert.equal(again.provider.calls,0);
 await again.recover(r.id);assert.equal((await again.view(r.id)).turns.length,1);
});
test('two candidates preview without applying; one adoption and repeat cannot double consume',async t=>{
 const {host,r}=await setup(t),input='服用两份养息丹';
 const a=await host.preview(r.id,{input,variant:'calm'}),b=await host.preview(r.id,{input,variant:'warm'});
 assert.notEqual(a.body,b.body);assert.equal((await host.store.read(r.id)).state.items[0].quantity,5);assert.equal(host.provider.calls,0);
 const request={operationId:'adopt',revision:r.revision,input};
 await host.turn(r.id,request,{variant:'warm',previewHash:b.previewHash});await host.turn(r.id,request,{variant:'warm',previewHash:b.previewHash});
 assert.equal(host.provider.calls,1);assert.equal((await host.view(r.id)).state.items[0].quantity,3);
 const newer=await host.store.read(r.id);
 await assert.rejects(()=>host.turn(r.id,{...request,operationId:'other',revision:newer.revision},{variant:'calm',previewHash:a.previewHash}),{code:'STATE_STALE_CANDIDATE'});
});
test('truncation does not commit prose or state; disabling retains data and blocks continuation',async t=>{
 const {host,r}=await setup(t);let n=await host.turn(r.id,{operationId:'bad',revision:r.revision,input:'服用两份养息丹'},{fault:'truncated'});
 assert.equal(n.turns.length,0);assert.equal(n.state.items[0].quantity,5);assert.equal(n.operations.bad.status,'rejected');
 n=await host.store.discard(r.id,{operationId:'dismiss',revision:n.revision,pendingId:'bad',reason:'放弃离线截断样例'});
 n=await host.store.enabled(r.id,{operationId:'off',revision:n.revision,enabled:false});
 await assert.rejects(()=>host.turn(r.id,{operationId:'blocked',revision:n.revision,input:'继续'}),{code:'STATE_DISABLED'});
 assert.ok((await host.view(r.id)).panels.includes('养息丹 × 5'));
});

test('confirmed legacy branch preserves source prose without guessing old panel balances',async t=>{
 const {host}=await setup(t),initial=exampleState();initial.balances.low=null;
 const history=[{input:'此前行动',body:'此前的故事。<details><summary>🧍 自身状态</summary>灵石999</details>'}];
 const r=await host.create({operationId:'legacy',initial,confirmed:true,source:{kind:'confirmed-import',id:'old-card',history}});
 const v=await host.view(r.id);assert.equal(v.state.balances.low,null);assert.ok(v.inheritedTurns[0].html.includes('此前的故事'));assert.ok(!v.inheritedTurns[0].html.includes('999'));assert.ok(history[0].body.includes('999'));
 const n=await host.turn(r.id,{operationId:'continue-old',revision:r.revision,input:'与周禾聊聊接下来的打算'});assert.equal(n.state.balances.low,null);
});
