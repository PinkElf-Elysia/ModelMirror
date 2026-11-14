import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {join} from 'node:path';import {fileURLToPath} from 'node:url';
import {fakeControl,syntheticToken} from '../authoring/controlled-test-support.mjs';import {ControlledProvider} from '../authoring/controlled-provider.mjs';import {AuthoringStore} from '../authoring/store.mjs';import {blankDocument} from '../authoring/contracts.mjs';import {characterDocument} from './document.mjs';import {CharacterSessionHost} from './session.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/character-tests/',import.meta.url));await mkdir(root,{recursive:true});
test('existing controlled provider, explicit model, bounded allowance and receipt-only recovery (local fake)',async t=>{
 const dir=await mkdtemp(root+'control-'),control=await fakeControl(t,{dropAfterStored:true}),works=await AuthoringStore.open(join(dir,'works'));t.after(()=>works.close());
 const d=characterDocument(blankDocument());Object.assign(d.character.card.data,{name:'受控测试夹具',first_mes:'你好',system_prompt:'角色提示',post_history_instructions:'历史之后'});d.metadata.name='受控测试夹具';
 const w=await works.create({operationId:'w',expectedRevision:null,document:d}),v=await works.publish(w.workId,{operationId:'p',expectedRevision:0});
 const provider=await ControlledProvider.open({directory:join(dir,'transport'),origin:control.origin,serviceToken:syntheticToken});t.after(()=>provider.close());
 const host=await CharacterSessionHost.open({directory:join(dir,'sessions'),works,provider,control:control.modelControl,limit:1});t.after(()=>host.close());
 let s=await host.create({operationId:'s',reference:{workId:w.workId,versionId:v.result.versionId},openingIndex:0,player:{id:'player-a',name:'玩家'}});
 const chosen=await control.choice();
 s=await host.configure(s.id,{expectedRevision:s.revision,settings:{memoryEnabled:false,historyTurns:null},selection:{catalogRevision:chosen.catalogRevision,selectionId:chosen.entry.selectionId,selectionRevision:chosen.entry.selectionRevision,parameters:chosen.parameters}});
 await host.send(s.id,{operationId:'one',expectedRevision:s.revision,input:'第一条消息'});s=await host.wait(s.id);assert.equal(s.operations.one.state,'unknown');
 s=await host.recover(s.id,{operationId:'one'});assert.equal(s.turns,1);assert.equal(control.calls.length,1);assert.equal(control.calls[0].messages[1].role,'assistant');assert.equal(control.calls[0].messages.at(-1).content,'历史之后');
 await assert.rejects(()=>host.send(s.id,{operationId:'two',expectedRevision:s.revision,input:'第二条消息'}),{code:'JOINT_BUDGET_EXHAUSTED'});assert.equal(control.calls.length,1);
});
