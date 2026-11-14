import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {setupHash,earthRuntime,runtimeStatus} from './runtime-binding.mjs';
import {memoryRuntimeStatus} from './memory-runtime.mjs';
import {createGodRealProfile} from '../god-card/lib/real-profile.mjs';
import {preserveOutputEditRuntime} from './output-edit-compatibility.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
const {runtimes}=JSON.parse(await readFile(new URL('./shared-runtime-fixtures.json',import.meta.url)));

for(const fixture of runtimes)test(`shared ${fixture.cardId} format ${fixture.format} ${fixture.hash.slice(0,12)} restores without rewriting or relaxing bindings`,async()=>{
 const session={cardId:fixture.cardId,mode:fixture.mode,characterText:'离线兼容验收角色',world:null,params:{}};
 session.runtime={format:fixture.format,hash:fixture.hash,descriptor:structuredClone(fixture.descriptor),setupHash:setupHash(session)};
 assert.equal(sha(canonical(session.runtime.descriptor)),fixture.hash);
 const original=fixture.cardId==='earth'?(fixture.format===1?s=>runtimeStatus(earthRuntime,s):memoryRuntimeStatus):(await createGodRealProfile({...fixture.descriptor.models[0],available:true})).status;
 const store={status:original};preserveOutputEditRuntime(store);
 const before=JSON.stringify(session);
 assert.deepEqual(store.status(session),{compatible:true,code:null,hash:fixture.hash});
 assert.equal(JSON.stringify(session),before,'compatibility must not migrate the stored descriptor');
 assert.equal(store.status({...session,characterText:'篡改角色'}).compatible,false);
 const broken=structuredClone(session);broken.runtime.hash='0'.repeat(64);
 assert.equal(store.status(broken).compatible,false);
 const mutate=fn=>{const s=structuredClone(session);fn(s.runtime.descriptor);s.runtime.hash=sha(canonical(s.runtime.descriptor));assert.equal(store.status(s).compatible,false);};
 mutate(d=>{const author=d.author||d.content.author;author.system='0'.repeat(64);});
 mutate(d=>{const a=d.hostArtifacts||d.artifacts;a[Object.keys(a)[0]]='0'.repeat(64);});
 mutate(d=>{const a=d.hostArtifacts||d.artifacts;a['unreviewed-host.mjs']='f'.repeat(64);});
 mutate(d=>{d.contextPolicy='unreviewed-context-policy';});
 if(fixture.cardId==='god')mutate(d=>{d.models[0].model='unreviewed/model';});
});
