import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startMemoryWorkspace} from './application.mjs';
import {fakeControl} from '../../authoring/controlled-test-support.mjs';
import {characterDocument} from '../document.mjs';
import {blankDocument} from '../../authoring/contracts.mjs';

test('original single, R4a and R4b use one receipt guard and the same remaining calls',async t=>{
 const upstream=await fakeControl(t),choice=await upstream.choice();
 const model={id:choice.entry.modelId,label:choice.entry.name,choice,contextTokens:choice.entry.contextLength,
   outputTokens:choice.wireParameters.max_tokens,price:{revision:'test',inputMicroUsdPerMillion:1000000,outputMicroUsdPerMillion:1000000,overheadTokens:128}};
 const root=process.env.CHARACTER_R4B_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
 const directory=await mkdtemp(join(root,'workspace-'));
 const app=await startMemoryWorkspace({directory,upstream:upstream.origin,serviceToken:'synthetic-authoring-control-token-tests-only',
   models:[model],port:43775,authorization:{id:'mock-only',calls:3,microUsd:200000,initial:{calls:0,microUsd:0}}});
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
 const status=await app.context.guard.status();assert.equal(status.usedCalls,3);
 const latest=await app.workspace.single.read(single.id);
 await app.workspace.single.send(single.id,{operationId:'no-room',expectedRevision:latest.revision,input:'再聊'}).catch(e=>assert.match(e.message,/BUDGET/));
 await app.workspace.single.wait(single.id);assert.equal(upstream.calls.length,3);
});
