import test from 'node:test';import assert from 'node:assert/strict';import {join} from 'node:path';
import {fixture} from '../group/test-support.mjs';import {CharacterSessionHost} from '../session.mjs';import {CharacterFixtureProvider} from '../fixture-provider.mjs';import {DailyWorkspace} from '../daily/workspace.mjs';import {GroupHost} from '../group/host.mjs';import {GroupFixtureProvider} from '../group/fixture-provider.mjs';import {LegacySources} from './sources.mjs';import {RevisionStore} from './store.mjs';import {RevisionFixture,RevisionHost} from './host.mjs';import {digest} from './contracts.mjs';
test('verified real single/group snapshots round-trip into revisions without mutating original',async t=>{
 const f=await fixture(t),w=await DailyWorkspace.open(f.directory),p=await CharacterFixtureProvider.open(join(f.directory,'single-provider')),single=await CharacterSessionHost.open({directory:join(f.directory,'single'),works:f.works,provider:p});w.single=single;w.group=f.store;w.works=f.works;
 const gp=await GroupFixtureProvider.open(join(f.directory,'group-provider')),gh=new GroupHost(f.store,gp);
 const sources=new LegacySources({workspace:w,singleDirectory:join(f.directory,'single'),groupDirectory:join(f.directory,'groups')}),store=await RevisionStore.open({directory:join(f.directory,'revisions'),sources}),rp=await RevisionFixture.open(join(f.directory,'revision-provider')),rh=new RevisionHost(store,rp);
 t.after(async()=>{await store.close();await rp.close();await single.close();await p.close();await gp.close();await w.close();});
 let s=await single.create({operationId:'new',reference:f.reference,openingIndex:0,player:{id:null,name:'玩家'}});await single.send(s.id,{operationId:'send',expectedRevision:0,input:'原始玩家消息'});await single.wait(s.id);
 const g=await f.store.read(f.id);await gh.send(f.id,{operationId:'send',expectedRevision:g.revision,payload:{targetId:g.members[1].id,input:'群聊消息'}});
 for(const [kind,sid] of [['single',s.id],['group',f.id]]){
  const before=await sources.read(kind,sid),original=digest(before);assert.equal(before.messages.length,3);assert.equal(before.messages[2].generation.context.length>0,true);
  const v=await store.importSource({operationId:kind,expectedRevision:null,kind,sourceId:sid});const r=await rh.generate(v.id,{operationId:'regen',expectedRevision:0,payload:{kind:'regenerate',branchId:v.rootId,messageId:v.messages[2].id}});assert.equal(r.candidates.length,1);assert.doesNotMatch(r.candidates[0].content,/hostInstruction|targetId/);assert.equal(r.messages[2].content,v.messages[2].content);assert.equal(digest(await sources.read(kind,sid)),original);
 }
});
