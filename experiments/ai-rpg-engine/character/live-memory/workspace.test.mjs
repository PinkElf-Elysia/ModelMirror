import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startLiveWorkspace} from './workspace.mjs';
import {fakeControl} from '../../authoring/controlled-test-support.mjs';
import {characterDocument} from '../document.mjs';
import {blankDocument} from '../../authoring/contracts.mjs';

test('original single, R4a, R4b and reviewed proposals consume one shared task allowance',async t=>{
 const upstream=await fakeControl(t),choice=await upstream.choice();
 const model={id:choice.entry.modelId,label:choice.entry.name,choice,contextTokens:choice.entry.contextLength,
   outputTokens:choice.wireParameters.max_tokens,price:{revision:'test',inputMicroUsdPerMillion:1000000,outputMicroUsdPerMillion:1000000,overheadTokens:128}};
 const root=process.env.CHARACTER_R4C_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
 const directory=await mkdtemp(join(root,'workspace-'));
 const app=await startLiveWorkspace({directory,upstream:upstream.origin,serviceToken:'synthetic-authoring-control-token-tests-only',
   models:[model],proposalLimit:{calls:1,microUsd:10000},port:0,authorization:{id:'mock-only',calls:4,microUsd:200000,initial:{calls:0,microUsd:0}}});
 t.after(()=>app.close());
 const doc=characterDocument(blankDocument());doc.character.card.data.name=doc.metadata.name='旅人';doc.character.card.data.first_mes='你好';
 const w=await app.studio.works.create({operationId:'work',expectedRevision:null,document:doc});
 const version=await app.studio.works.publish(w.workId,{operationId:'v',expectedRevision:0});
 const single=await app.workspace.single.create({operationId:'session',reference:{workId:w.workId,versionId:version.result.versionId},openingIndex:0,player:{id:null,name:'玩家'}});
 const ready=await app.workspace.single.configure(single.id,{expectedRevision:single.revision,settings:{memoryEnabled:false,historyTurns:null},
   selection:{catalogRevision:choice.catalogRevision,selectionId:choice.entry.selectionId,selectionRevision:choice.entry.selectionRevision,parameters:choice.parameters}});
 await app.workspace.single.send(single.id,{operationId:'first',expectedRevision:ready.revision,input:'你好'});await app.workspace.single.wait(single.id);
 async function generate(service,view,operationId){
   const w={operationId,expectedRevision:view.revision,payload:{branchId:view.branchId,targetId:view.members[1].id,kind:'send',input:'继续',settings:null}};
   const preview=await service.store.preview(view.id,w);await service.host.generate(view.id,{...w,previewHash:preview.previewHash});
 }
 const a=await app.longContext.store.importSource({operationId:'a',expectedRevision:null,reference:{kind:'single',sourceId:single.id}});
 await generate(app.longContext,a,'second');
 const b=await app.memory.store.importSource({operationId:'b',expectedRevision:null,reference:{kind:'context',familyId:a.id,branchId:a.branchId}});
 await generate(app.memory,b,'third');assert.equal(upstream.calls.length,3);
 const key=await app.desk.create({operationId:'live',familyId:b.id,branchId:b.branchId,expectedRevision:(await app.memory.store.read(b.id)).revision});
 const v=await app.desk.view(key);await app.desk.act(key,{operationId:'one-memory',actor:v.actor,action:'propose',payload:{},expectedRevision:v.revision});
 await Promise.all([...app.desk.jobs.values()]);
 const status=await app.context.guard.status();assert.equal(status.usedCalls,4);
 assert.equal((await app.desk.view(key)).requests[0].status,'invalid-proposal');
 const latest=await app.workspace.single.read(single.id);
 await app.workspace.single.send(single.id,{operationId:'no-room',expectedRevision:latest.revision,input:'再聊'}).catch(e=>assert.match(e.message,/BUDGET/));
 await app.workspace.single.wait(single.id);assert.equal(upstream.calls.length,4);
});
