import test from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {fixture} from '../group/test-support.mjs';
import {DailyWorkspace} from './workspace.mjs';
import {CharacterSessionHost} from '../session.mjs';
import {CharacterFixtureProvider} from '../fixture-provider.mjs';
import {placeholder} from '../png.mjs';
const op=(operationId,expectedRevision,payload)=>({operationId,expectedRevision,payload});

test('profile edits and avatar removal affect new sessions only; restart preserves both snapshots without generation',async t=>{
 const f=await fixture(t);let w=await DailyWorkspace.open(f.directory);const provider=await CharacterFixtureProvider.open(join(f.directory,'snapshot-fixture'));
 const options={directory:join(f.directory,'snapshot-single'),works:f.works,provider};let single=await CharacterSessionHost.open(options);
 t.after(async()=>{await single.close();await provider.close();await w.close();});
 const bytes=placeholder().toString('base64'),asset=await w.store.putAvatar(op('avatar',null,{mediaType:'image/png',base64:bytes}));
 const p=await w.store.saveProfile(op('identity',null,{id:null,name:'初始旅人',description:'旧身份公开描述',avatar:asset.id}));
 const player=await w.resolvePlayer({profileId:p.profileId,revision:0}),s1=await single.create({operationId:'old-single',reference:f.reference,openingIndex:0,player}),g1=await f.store.create({...f.input,operationId:'old-group',payload:{...f.input.payload,player}});
 const oldSingle=await single.read(s1.id),oldGroup=await f.store.read(g1.id);
 const p2=await w.store.saveProfile(op('edit',0,{id:p.profileId,name:'修订旅人',description:'新身份公开描述',avatar:null}));
 const next=await w.resolvePlayer({profileId:p.profileId,revision:1}),s2=await single.create({operationId:'new-single',reference:f.reference,openingIndex:0,player:next}),g2=await f.store.create({...f.input,operationId:'new-group',payload:{...f.input.payload,player:next}});
 await single.close();await w.close();await f.reopen();w=await DailyWorkspace.open(f.directory);single=await CharacterSessionHost.open(options);
 assert.deepEqual(await single.read(s1.id),oldSingle);assert.deepEqual(await f.store.read(g1.id),oldGroup);
 assert.deepEqual((await single.read(s2.id)).binding.player,p2);assert.deepEqual((await f.store.read(g2.id)).members[0].identity,p2);
 assert.equal((await w.store.snapshot(p.profileId,0)).avatar,asset.id);assert.equal((await w.store.avatar(asset.id)).base64,bytes);
 const oldContext=await f.store.preview(g1.id,oldGroup.members[1].id),newGroup=await f.store.read(g2.id),newContext=await f.store.preview(g2.id,newGroup.members[1].id);
 assert.match(JSON.stringify(oldContext.messages),/旧身份公开描述/);assert.doesNotMatch(JSON.stringify(oldContext.messages),/新身份公开描述/);assert.match(JSON.stringify(newContext.messages),/新身份公开描述/);
 assert.doesNotMatch(JSON.stringify(oldContext.messages),new RegExp(asset.id));assert.equal(oldSingle.turns,0);assert.deepEqual((await single.read(s2.id)).operations,{});assert.deepEqual(oldGroup.requests,[]);assert.deepEqual(newGroup.requests,[]);
 w.single=single;w.group=f.store;w.works=f.works;for(const [kind,id] of [['single',s1.id],['group',g1.id]]){const exported=await w.export(kind,id,'md');assert.doesNotMatch(exported.text,/旧身份公开描述|新身份公开描述|profileRevision|base64/);assert.ok(!exported.text.includes(asset.id));}
});
