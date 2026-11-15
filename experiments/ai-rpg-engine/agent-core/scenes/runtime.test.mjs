import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir} from 'node:fs/promises';import {join} from 'node:path';
import {openScene} from './bootstrap.mjs';
const root=new URL('../../.rpg03-work/r9-tests/',import.meta.url);
async function setup(t){await mkdir(root,{recursive:true});const {fileURLToPath}=await import('node:url');const path=await mkdtemp(join(fileURLToPath(root),'scene-'));const app=await openScene(path,{offlinePreview:true,delayMs:0});t.after(()=>app.close());return {app,path};}
test('continuous scene: adoption, action, scoped query, world continuation and exact branch',async t=>{
 const {app}=await setup(t);let v=await app.create({operationId:'create',adapterId:'lighthouse'}),key=v.id,initial=structuredClone(v.world.state);
 v=await app.submit(key,{operationId:'talk',ownerId:'participant-keeper',input:'现在如何值守？'});
 assert.equal(v.tasks.at(-1).adoption,'awaiting-adoption');assert.equal(v.scene.entries.length,0);
 await assert.rejects(app.submit(key,{operationId:'blocked',ownerId:'world-dialogue',input:'继续'}),/RECOVERY_REQUIRED/);
 const task=v.tasks.at(-1).id;v=await app.adopt(key,task);assert.equal(v.scene.entries.length,1);assert.equal(v.timeline[0].kind,'speech');assert.deepEqual(v.world.state,initial);
 const rev=v.world.revision;v=await app.adopt(key,task);assert.equal(v.world.revision,rev);
 v=await app.checkpoint(key,{operationId:'before-action'});
 v=await app.submit(key,{operationId:'prepare',ownerId:'world-dialogue',input:'添油 2'});const proposal=v.world.proposals.prepare.proposal;
 v=await app.confirm(key,{operationId:'execute',revision:v.world.revision,proposalId:'prepare',proposalHash:proposal.hash});
 assert.equal(v.world.resources[0].quantity,3);
 v=await app.narrate(key,{});assert.match(v.world.turns.at(-1).body,/沈砚/);
 v=await app.submit(key,{operationId:'ask-again',ownerId:'participant-keeper',input:'查询灯油余量多少？'});
 assert.equal(v.tasks.at(-1).stages.length,2);assert.match(v.tasks.at(-1).body,/3/);
 const full=await app.ledger.read(),last=full.tasks[v.tasks.at(-1).id];
 assert.equal(last.stages[1].request.messages.at(-2).tool_calls[0].extra_content.google.thought_signature,'offline-r9-signature');
 v=await app.adopt(key,v.tasks.at(-1).id);
 v=await app.submit(key,{operationId:'outsider',ownerId:'participant-visitor',input:'刚才处理好了吗？'});
 assert.match(v.tasks.at(-1).body,/不知道/);v=await app.dismiss(key,v.tasks.at(-1).id);
 const calls=v.budget.calls;const child=await app.fork(key,{operationId:'fork',checkpointId:'before-action'});
 assert.equal(child.world.resources[0].quantity,5);assert.equal(child.scene.entries.length,1);assert.equal(child.budget.calls,calls);
 assert.notEqual(child.scene.participants[0].instanceId,v.scene.participants[0].instanceId);assert.equal((await app.view(key)).scene.entries.length,2);
});
test('same registered participant consumer works with cultivation without state writes',async t=>{
 const {app}=await setup(t);let v=await app.create({operationId:'cultivate',adapterId:'cultivation'}),before=structuredClone(v.world.state);
 v=await app.submit(v.id,{operationId:'query',ownerId:'participant-keeper',input:'查询库存多少？'});
 assert.equal(v.tasks.at(-1).stages.length,2);v=await app.adopt(v.id,v.tasks.at(-1).id);assert.deepEqual(v.world.state,before);
});
