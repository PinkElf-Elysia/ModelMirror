import test from 'node:test';import assert from 'node:assert/strict';import {reservation} from './contracts.mjs';import {fixture,offlineModel} from './test-support.mjs';
test('atomic creation: distinct same-template members, one opening, stable restart and exact duplicate',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id);assert.equal(s.members.length,3);assert.equal(new Set(s.members.map(m=>m.id)).size,3);assert.equal(s.messages.length,1);assert.equal(s.messages[0].content,'你好 测试玩家');await f.store.create(f.input);await f.reopen();assert.deepEqual(await f.store.read(f.id),s);await assert.rejects(f.store.create({...f.input,payload:{...f.input.payload,scene:'changed'}}),{code:'GROUP_OPERATION_COLLISION'});
});
test('group context replaces solo scene, preserves examples outside history, excludes sibling private data',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),a=s.members[1].id,b=s.members[2].id;
 await f.store.change(f.id,'private',{operationId:'private',expectedRevision:s.revision,payload:{ownerId:a,content:'ONLY_A'}});
 const pa=await f.store.preview(f.id,a),pb=await f.store.preview(f.id,b);assert.match(JSON.stringify(pa),/ONLY_A/);assert.doesNotMatch(JSON.stringify(pb),/ONLY_A|SOLO_SCENE/);assert.match(JSON.stringify(pb),/共享群场景/);assert.equal((await f.store.read(f.id)).messages.length,1);assert.equal(pb.messages.at(-1).role,'user');
});
test('prepared intent reserves once, freezes changes, task ceiling is shared by groups',async t=>{
 const f=await fixture(t,{authorization:{id:'one',mode:'fixture',calls:1,microUsd:1000000}}),s=await f.store.read(f.id),p={operationId:'send',expectedRevision:0,payload:{targetId:s.members[1].id,input:'中文\n多行'}};
 await f.store.prepare(f.id,p);await f.store.prepare(f.id,p);const next=await f.store.read(f.id);assert.equal(next.messages.length,2);assert.equal(next.budget.usedCalls,1);assert.equal(next.requests.length,1);
 await assert.rejects(f.store.change(f.id,'leave',{operationId:'leave',expectedRevision:next.revision,payload:{memberId:s.members[1].id}}),{code:'GROUP_REQUEST_PENDING'});
 const g=await f.store.create({...f.input,operationId:'other'}),gs=await f.store.read(g.id);await assert.rejects(f.store.prepare(g.id,{operationId:'other-send',expectedRevision:0,payload:{targetId:gs.members[1].id,input:'hi'}}),{code:'GROUP_TASK_BUDGET_EXHAUSTED'});await f.reopen();assert.equal((await f.store.read(f.id)).requests[0].status,'prepared');
});
test('unpriced models and mandatory-context overflow fail closed',()=>{assert.throws(()=>reservation([{content:'hi'}],{...offlineModel,price:null}),{code:'GROUP_PRICE_UNKNOWN'});assert.throws(()=>reservation([{content:'字'.repeat(20000)}],offlineModel),{code:'GROUP_CONTEXT_CAPACITY'});});
