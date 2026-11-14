import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';
import {AuthoringStore} from '../authoring/store.mjs';import {blankDocument} from '../authoring/contracts.mjs';import {characterDocument} from './document.mjs';import {CharacterFixtureProvider} from './fixture-provider.mjs';import {CharacterSessionHost} from './session.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/character-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t,{fault}={}){
 const dir=await mkdtemp(root+'session-'),works=await AuthoringStore.open(join(dir,'works')),doc=characterDocument(blankDocument());Object.assign(doc.character.card.data,{name:'测试夹具',first_mes:'你好 {{user}}',alternate_greetings:['另一开场'],description:'人物设定',mes_example:'示例不可成为历史'});doc.metadata.name='测试夹具';
 const c=await works.create({operationId:'work',expectedRevision:null,document:doc}),v=await works.publish(c.workId,{operationId:'publish',expectedRevision:0}),reference={workId:c.workId,versionId:v.result.versionId};
 let provider=await CharacterFixtureProvider.open(join(dir,'provider'),{fault}),host=await CharacterSessionHost.open({directory:join(dir,'sessions'),works,provider});
 t.after(async()=>{await host.close();await provider.close();await works.close();});
 return {dir,works,reference,doc,get host(){return host;},get provider(){return provider;},async reopen(){await host.close();await provider.close();provider=await CharacterFixtureProvider.open(join(dir,'provider'));host=await CharacterSessionHost.open({directory:join(dir,'sessions'),works,provider});}};
}
const create=(f,id,index=0)=>f.host.create({operationId:id,reference:f.reference,openingIndex:index,player:{id:null,name:'青禾'}});
test('initial greeting once, stream, two isolated sessions and template version pin',async t=>{
 const f=await fixture(t),a=await create(f,'one'),b=await create(f,'two',1);
 assert.equal(f.provider.dispatchCount,0);assert.deepEqual(a.messages,[{role:'assistant',content:'你好 青禾'}]);
 const deltas=[];const un=f.host.subscribe(a.id,x=>deltas.push(x));
 await f.host.send(a.id,{operationId:'send',expectedRevision:a.revision,input:'灯塔'});const done=await f.host.wait(a.id);un();
 assert.equal(done.turns,1);assert.ok(deltas.some(x=>x.type==='delta'));assert.equal(done.messages.length,3);assert.equal((await f.host.read(b.id)).messages.length,1);
 f.doc.character.card.data.first_mes='新开场';await f.works.save(f.reference.workId,{operationId:'edit',expectedRevision:1,document:f.doc});
 await f.reopen();const restored=await f.host.read(a.id);assert.equal(restored.messages[0].content,'你好 青禾');assert.equal(restored.turns,1);
 await f.host.send(a.id,{operationId:'send',expectedRevision:0,input:'灯塔'});assert.equal(f.provider.dispatchCount,0);
 const out=await f.works.exportConfiguration(f.reference.workId,f.reference.versionId);assert.ok(!JSON.stringify(out).includes('离线测试回复'));
});
test('unknown external result persists; recovery lookup does not dispatch',async t=>{
 const f=await fixture(t,{fault:async()=>{throw new Error('lost reply');}}),s=await create(f,'unknown');
 await f.host.send(s.id,{operationId:'one',expectedRevision:0,input:'你好'});const unknown=await f.host.wait(s.id);assert.equal(unknown.operations.one.state,'unknown');
 await assert.rejects(()=>f.host.send(s.id,{operationId:'two',expectedRevision:unknown.revision,input:'重试'}),{code:'CHARACTER_OPERATION_PENDING'});
 await f.reopen();const recovered=await f.host.recover(s.id,{operationId:'one'});assert.equal(recovered.turns,1);assert.equal(f.provider.dispatchCount,0);
});
test('private memory requires per-session enablement and never leaks to sibling',async t=>{
 const f=await fixture(t),a=await create(f,'a'),b=await create(f,'b');
 const value={name:'约定',content:'SESSION_A_SECRET',enabled:true,match:'any',roles:['user'],keywords:['灯塔']};
 let edited=await f.host.editMemory(a.id,{expectedRevision:0,operationId:'memory',change:{action:'create',value}});
 edited=await f.host.configure(a.id,{expectedRevision:edited.revision,settings:{memoryEnabled:true,historyTurns:null},selection:null});
 await f.host.send(a.id,{operationId:'talk',expectedRevision:edited.revision,input:'灯塔'});const done=await f.host.wait(a.id);assert.equal(done.operations.talk.trace.memoryCount,1);
 await f.host.send(b.id,{operationId:'talk',expectedRevision:0,input:'灯塔'});const other=await f.host.wait(b.id);assert.equal(other.operations.talk.trace.memoryCount,0);assert.equal(other.memories.length,0);assert.notEqual(done.memoryScope,other.memoryScope);
});
