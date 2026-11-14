import test from 'node:test';import assert from 'node:assert/strict';import {join} from 'node:path';
import {readFile,readdir} from 'node:fs/promises';import {sha256} from '../../authoring/contracts.mjs';
import {CharacterSessionHost} from '../session.mjs';import {CharacterFixtureProvider} from '../fixture-provider.mjs';import {LegacyConversationReader} from './legacy.mjs';import {fixture} from './test-support.mjs';
async function hashes(dir){const out={};async function scan(path,rel=''){for(const d of await readdir(path,{withFileTypes:true})){if(d.isDirectory())await scan(join(path,d.name),rel+d.name+'/');else out[rel+d.name]=sha256(await readFile(join(path,d.name)));}}await scan(dir);return out;}
for(const pending of [false,true])test('legacy projection is read-only, stable and '+(pending?'retains unknown without dispatch':'keeps opening/history/private memory distinct'),async t=>{
 const f=await fixture(t),directory=join(f.directory,'legacy'),provider=await CharacterFixtureProvider.open(join(directory,'provider'),{...(pending?{fault:async()=>{throw Error('lost fixture response');}}:{})});
 const host=await CharacterSessionHost.open({directory:join(directory,'sessions'),works:f.works,provider});t.after(async()=>{await host.close();await provider.close();});
 let s=await host.create({operationId:'legacy',reference:f.reference,openingIndex:0,player:{id:'player',name:'玩家'}});
 s=await host.editMemory(s.id,{operationId:'memory',expectedRevision:s.revision,change:{action:'create',value:{name:'private',content:'PRIVATE_LEGACY_MEMORY',enabled:true,match:'any',roles:['user'],keywords:['hello']}}});
 await host.send(s.id,{operationId:'one',expectedRevision:s.revision,input:'hello'});s=await host.wait(s.id);
 const reader=new LegacyConversationReader({host,works:f.works}),before=await hashes(directory),dispatches=provider.dispatchCount;
 const a=await reader.readOwner(s.id),b=await reader.readOwner(s.id);assert.deepEqual(a,b);assert.equal(a.readOnly,true);assert.deepEqual(a.settings,s.settings);assert.equal((await reader.readMember(s.id,a.members[1].id)).memoryEnabled,false);assert.equal(a.messages.length,pending?1:3);assert.equal(a.messages[0].source.kind,'legacy-opening');assert.ok(!JSON.stringify(a.messages).includes('EXAMPLE_NOT_HISTORY'));
 assert.equal(a.requestStates[0].state,pending?'unknown':'completed');assert.ok(!JSON.stringify(a.requestStates).includes('hello'));
 assert.equal((await reader.readMember(s.id,a.members[0].id)).format,'modelmirror.rpg.character-legacy-member-view/1');assert.equal((await reader.readMember(s.id,a.members[0].id)).privateRecords.length,0);assert.equal((await reader.readMember(s.id,a.members[1].id)).privateRecords.length,1);
 await assert.rejects(()=>reader.readMember(s.id,'member-'+'0'.repeat(64)),{code:'CONVERSATION_MEMBER_NOT_FOUND'});
 assert.equal(provider.dispatchCount,dispatches);assert.deepEqual(await hashes(directory),before);
});
test('unverified snapshot cannot substitute for trusted legacy host',()=>{assert.throws(()=>new LegacyConversationReader({host:{read:async()=>({})},works:{}}),{code:'CONVERSATION_HOST_REQUIRED'});});
