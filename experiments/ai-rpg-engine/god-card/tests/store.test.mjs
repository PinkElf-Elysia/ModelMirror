import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {createGodStore} from '../lib/adapter.mjs';
import {newSetup,serializeSetup} from '../lib/setup.mjs';
import {loadFrozen} from '../lib/assembly.mjs';
const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b2-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(generate=async()=> ' raw\n\t<details><summary>原文</summary>资料</details>'){
 const dir=await mkdtemp(join(root,'store-'));const store=createGodStore(dir,generate);await store.init();
 const setup=newSetup();setup.user.姓名='虚构旅人';setup.era='汉朝';setup.opening='观察人间';
 return {store,dir,s:await store.create({setup,mode:'offline'}),setup};
}
test('shared store dispatches the complete opening once and keeps continuation literal',async()=>{
 const wires=[];const {store,s,setup,dir}=await fixture(async a=>{wires.push(structuredClone(a.messages));return '原文\n\t';});
 const first={input:serializeSetup(setup),requestId:'first',revision:0};
 const one=await store.send(s.id,first);await store.send(s.id,first);
 const two=await store.send(s.id,{input:' 后续\n输入 ',requestId:'second',revision:1});
 assert.deepEqual(wires,[[{role:'system',content:loadFrozen().system},{role:'user',content:first.input}],[{role:'system',content:loadFrozen().system},...one.history,{role:'user',content:' 后续\n输入 '}]]);
 assert.equal(two.world,null);assert.deepEqual(two.params,{});assert.equal(two.runtime.descriptor.cardId,'god');
 const reopened=createGodStore(dir,()=>{throw Error('no replay');});await reopened.init();assert.deepEqual(await reopened.read(s.id),two);
});
test('browser cannot supply system/history/world/parameters or real mode; first input bound to snapshot',async()=>{
 const {store,s}=await fixture();
 for(const data of [{setup:newSetup(),mode:'real'},{setup:newSetup(),mode:'offline',world:'表世界'},{setup:newSetup(),mode:'offline',params:{temperature:0}}])await assert.rejects(store.create(data));
 await assert.rejects(store.send(s.id,{input:'arbitrary',requestId:'first',revision:0}),/GOD_OPENING_SNAPSHOT_REQUIRED/);
 await assert.rejects(store.send(s.id,{input:s.characterText,requestId:'first',revision:0,system:'injected'}),/GOD_SEND_FIELDS_INVALID/);
 assert.equal((await store.read(s.id)).history.length,0);
});
test('shared locking, cancellation and pending recovery never append late or replay unknown output',async()=>{
 let release,begin;const began=new Promise(r=>begin=r);
 const {store,s,dir}=await fixture(async()=>{begin();return new Promise(r=>release=r);});
 const running=store.send(s.id,{input:s.characterText,requestId:'one',revision:0});await began;
 await assert.rejects(store.send(s.id,{input:s.characterText,requestId:'two',revision:0}),/SESSION_BUSY/);
 store.cancel(s.id);release('late');const end=await running;assert.equal(end.history.length,0);assert.equal(end.requests.one.status,'cancelled');
 end.requests.unknown={status:'pending'};await store.write(end);
 const recovered=createGodStore(dir,()=>{throw Error('unexpected replay');});await recovered.init();
 await assert.rejects(recovered.send(s.id,{input:s.characterText,requestId:'new',revision:0}),/未确认/);
});
test('runtime mismatch keeps history readable but refuses new dispatch',async()=>{
 const {store,s}=await fixture();s.runtime.hash='0'.repeat(64);await store.write(s);
 assert.equal((await store.read(s.id)).id,s.id);
 await assert.rejects(store.send(s.id,{input:s.characterText,requestId:'one',revision:0}),/RUNTIME_BINDING_INVALID/);
});

test('unknown generator result and failed final publication remain pending across restart',async()=>{
 let calls=0;const f=await fixture(async()=>{calls++;throw Error('unknown transport result');});
 const req={input:f.s.characterText,requestId:'unknown',revision:0};
 const unknown=await f.store.send(f.s.id,req);assert.equal(unknown.requests.unknown.status,'pending');assert.equal(unknown.history.length,0);
 const reopened=createGodStore(f.dir,()=>{throw Error('must not dispatch');});await reopened.init();
 await reopened.send(f.s.id,req);await assert.rejects(reopened.send(f.s.id,{...req,requestId:'replacement'}),/未确认/);assert.equal(calls,1);
 const g=await fixture(async()=> 'completed-but-not-published');
 const write=g.store.write.bind(g.store);let writes=0;
 g.store.write=async s=>{if(++writes===2)throw Error('simulated final disk failure');return write(s);};
 await assert.rejects(g.store.send(g.s.id,{input:g.s.characterText,requestId:'disk',revision:0}),/disk failure/);
 const disk=await g.store.read(g.s.id);assert.equal(disk.requests.disk.status,'pending');assert.equal(disk.history.length,0);
});
