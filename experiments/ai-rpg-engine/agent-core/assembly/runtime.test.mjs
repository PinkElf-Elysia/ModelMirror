import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {openPreview} from './bootstrap.mjs';import {exampleCatalog} from './catalog.mjs';import {observationOwner,worldOwner} from './owners.mjs';import {clone,hash} from '../contracts.mjs';
async function setup(t,options={}){const dir=await mkdtemp(join(tmpdir(),'r8-runtime-'));let rt=await openPreview(dir,{offlinePreview:true,delayMs:1,...options});
 t.after(async()=>{await rt.close();await rm(dir,{recursive:true,force:true});});return {get rt(){return rt;},async reopen(){await rt.close();rt=await openPreview(dir,{offlinePreview:true,delayMs:1,...options});}};}
const submit=(rt,r,operationId,input,extra={})=>rt.submit(r.id,{operationId,input,ownerId:'world-dialogue',...extra});
async function confirm(rt,v){const [proposalId,row]=Object.entries(v.world.proposals).at(-1);return rt.confirm(v.id,{operationId:'execute-'+proposalId,revision:v.world.revision,proposalId,proposalHash:row.proposal.hash});}

for(const adapterId of ['cultivation','lighthouse'])test(adapterId+' direct, native count, proposal, confirmation, prose, next turn and restart',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start',adapterId}),before=clone(r.world.state);
 let v=await submit(f.rt,r,'talk','你好，今天有什么安排？');assert.equal(v.tasks[0].stages.length,1);assert.deepEqual(v.world.state,before);
 v=await submit(f.rt,r,'count','统计登记物资');assert.equal(v.tasks[1].stages.length,2);assert.equal(v.queries.at(-1).name,'count_registered');assert.deepEqual(v.world.state,before);
 const toolMessage=v.tasks[1].stages[1].request.messages.find(m=>m.tool_calls);assert.equal(toolMessage.tool_calls[0].extra_content.google.thought_signature,'offline-r8-signature');
 v=await submit(f.rt,r,'plan','先查询，再拟定使用两份的方案');assert.equal(v.tasks.at(-1).adoption,'proposed');assert.deepEqual(v.world.state,before);
 const proposed=v;v=await confirm(f.rt,v);assert.equal(v.world.resources[0].quantity,r.world.resources[0].quantity-2);
 const after=hash(v.world.state);v=await confirm(f.rt,proposed);assert.equal(hash(v.world.state),after);assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
 v=await f.rt.narrate(r.id);assert.equal(v.tasks.at(-1).adoption,'completed');assert.equal(hash(v.world.state),after);
 v=await submit(f.rt,r,'after','查看登记物资');assert.equal(hash(v.world.state),after);const calls=v.budget.calls;
 await f.reopen();v=await f.rt.view(r.id);assert.equal(v.budget.calls,calls);assert.equal(hash(v.world.state),after);
});
test('unknown request survives restart, lookup does not dispatch again',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start',adapterId:'cultivation'});
 let v=await submit(f.rt,r,'talk','你好',{fault:'unknown'});assert.equal(v.tasks[0].adoption,'unknown');const calls=v.budget.calls;
 await assert.rejects(submit(f.rt,r,'blocked','你好'),/RECOVERY/);await f.reopen();v=await f.rt.recover(r.id,v.tasks[0].id);
 assert.equal(v.tasks[0].adoption,'completed');assert.equal(v.tasks[0].error,null);assert.equal(v.budget.calls,calls);assert.equal(v.world.turns.length,1);
});
test('new task owner is independently adopted with no state or secret access',async t=>{
 const c=await exampleCatalog();c.owners.push(observationOwner('review-note'));const f=await setup(t,{catalog:c}),r=await f.rt.create({operationId:'start',adapterId:'lighthouse'});
 let v=await f.rt.submit(r.id,{operationId:'note',ownerId:'review-note',input:'物资和秘密是什么？'}),task=v.tasks[0];
 assert.equal(task.adoption,'awaiting-adoption');assert.equal(task.stages.length,1);assert.equal(task.observation.supplies,undefined);assert.equal(task.stages[0].request.tools.length,0);
 assert.deepEqual(v.world.state,r.world.state);assert.equal(v.world.turns.length,0);
 v=await f.rt.adopt(r.id,task.id);assert.equal(v.tasks[0].adoption,'completed');v=await f.rt.adopt(r.id,task.id);assert.equal(v.budget.calls,1);assert.equal(v.world.turns.length,0);
});
test('checkpoints cut exact history, clear proposals, preserve allowance and survive restart',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start',adapterId:'lighthouse'});
 await submit(f.rt,r,'one','第一段');await f.rt.checkpoint(r.id,{operationId:'checkpoint-one'});
 await submit(f.rt,r,'two','第二段');await submit(f.rt,r,'plan','拟定使用两份的方案');
 let child=await f.rt.fork(r.id,{operationId:'branch',checkpointId:'checkpoint-one'});assert.equal(child.world.turns.length,1);assert.equal(child.world.turns[0].input,'第一段');assert.deepEqual(child.world.proposals,{});assert.equal(child.tasks.length,0);assert.equal(child.budget.calls,3);
 await f.reopen();child=await f.rt.view(child.id);assert.equal(child.world.turns.length,1);assert.equal(child.budget.calls,3);
 const original=await f.rt.view(r.id);assert.equal(original.world.turns.length,2);
 await assert.rejects(f.rt.fork(r.id,{operationId:'branch',checkpointId:'not-found'}));
});
test('disabled capabilities do not reappear on continuation, saved state remains readable',async t=>{
 const f=await setup(t),r=await f.rt.create({operationId:'start',adapterId:'cultivation'});
 await f.rt.capabilities(r.id,{operationId:'disable',revision:r.world.revision,names:['query_local_state']});
 let v=await submit(f.rt,r,'query','先查询，再拟定使用两份的方案');assert.equal(v.tasks[0].stages[1].request.tools.length,0);assert.deepEqual(v.world.state,r.world.state);assert.equal(Object.keys(v.world.proposals).length,0);
 await f.reopen();v=await f.rt.view(r.id);assert.equal(v.tools.filter(x=>x.enabled).length,1);
});
test('registered entrypoints replace fixed purpose names without changing executor',async t=>{
 const c=await exampleCatalog();c.owners=[worldOwner('story','turn'),worldOwner('receipt-story','narrate')];c.entrypoints={dialogue:'story',narration:'receipt-story'};
 const f=await setup(t,{catalog:c}),r=await f.rt.create({operationId:'start',adapterId:'lighthouse'});
 let v=await f.rt.submit(r.id,{operationId:'plan',ownerId:'story',input:'拟定使用两份的方案'});v=await confirm(f.rt,v);v=await f.rt.narrate(r.id);
 assert.equal(v.tasks.at(-1).purpose,'receipt-story');assert.equal(v.tasks.at(-1).adoption,'completed');
});
