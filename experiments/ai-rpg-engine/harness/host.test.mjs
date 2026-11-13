import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';
import {createShopHost} from './host.mjs';import {createMockProvider} from './mock-provider.mjs';import {initialAssets} from './shop-rules.mjs';import {rules} from './earth-adapter.mjs';
async function setup(t,mode='success',sourceLoader=async()=>{throw Error('not found');}){
 const directory=await mkdtemp(join(tmpdir(),'shop-host-')),provider=createMockProvider({mode,delay:0});
 const host=await createShopHost({directory,provider,sourceLoader});
 t.after(async()=>{await host.close();assert.ok(resolve(directory).startsWith(resolve(tmpdir())+'\\shop-host-'));await rm(directory,{recursive:true,force:true});});
 async function request(path,body){let result;await host.handle({pathname:'/rpg-app/earth/api/shop'+path,method:body===undefined?'GET':'POST',data:body,query:new URLSearchParams(),send:(status,data)=>{result={status,data};}});return result.data;}
 const s=await request('/activate',{operationId:'open',sourceId:'example',initial:initialAssets(rules),confirmed:true,context:{profiles:[],modIds:[]}});
 return {host,provider,request,s,path:'/sessions/'+s.id};
}
test('actual plugin authorization, host purchase, narration, replay and next turn',async t=>{
 const {host,provider,request,s,path}=await setup(t);
 const body={operationId:'buy',revision:s.revision,ruleVersion:rules.version,productId:'bread',quantity:2};
 let r=await request(path+'/purchase',body);assert.equal(r.assets.balance,8800);
 await host.process(s.id,'buy');r=await request(path);assert.equal(r.active,null);assert.equal(r.events[0].narration.status,'complete');
 assert.equal(provider.calls,1);assert.equal((await request(path+'/purchase',body)).assets.balance,8800);
 r=await request(path+'/input',{operationId:'next',revision:r.revision,input:'问店主附近有什么去处'});
 await host.process(s.id,'next');r=await request(path);assert.equal(r.active,null);assert.equal(r.assets.inventory.bread,2);assert.equal(provider.calls,2);
 assert.equal((await request(path+'/reconcile',{})).assets.balance,8800);
});
test('free text parsed before single atomic settlement',async t=>{
 const {host,provider,request,s,path}=await setup(t);
 await request(path+'/input',{operationId:'buy',revision:s.revision,input:'我想买两个面包'});
 await host.process(s.id,'buy');const r=await request(path);
 assert.equal(r.assets.balance,8800);assert.equal(r.assets.stock.bread,0);assert.equal(provider.calls,2);
 assert.equal(r.events.length,1);assert.equal(r.active,null);
});
test('failed narration retry only, receipt end, pause and checkpoint',async t=>{
 const {host,provider,request,s,path}=await setup(t,'fail-all');
 await request(path+'/purchase',{operationId:'buy',revision:s.revision,ruleVersion:rules.version,productId:'bread',quantity:2});await host.process(s.id,'buy');
 let r=await request(path);assert.equal(r.events[0].narration.status,'failed');
 r=await request(path+'/retry',{operationId:'retry',revision:r.revision,eventId:'buy',purpose:'narration'});await host.process(s.id,'buy');
 r=await request(path);assert.equal(provider.calls,2);assert.equal(r.assets.balance,8800);assert.equal(r.events.length,1);
 r=await request(path+'/skip',{operationId:'skip',revision:r.revision,eventId:'buy'});
 const child=await request(path+'/fork',{operationId:'fork',revision:r.revision,eventId:'buy'});assert.equal(child.assets.balance,8800);
 r=await request(path+'/enabled',{operationId:'pause',revision:r.revision,enabled:false});assert.equal(r.enabled,false);
 await assert.rejects(request(path+'/purchase',{operationId:'other',revision:r.revision,ruleVersion:rules.version,productId:'water',quantity:1}));
});
test('unknown narration resolves original receipt, never a second transaction',async t=>{
 const {host,provider,request,s,path}=await setup(t,'unknown');
 await request(path+'/purchase',{operationId:'buy',revision:s.revision,ruleVersion:rules.version,productId:'water',quantity:1});await host.process(s.id,'buy');
 let r=await request(path);assert.equal(r.events[0].narration.status,'unknown');
 await assert.rejects(request(path+'/skip',{operationId:'skip',revision:r.revision,eventId:'buy'}));
 r=await request(path+'/recover',{eventId:'buy'});assert.equal(r.active,null);assert.equal(provider.calls,1);assert.equal(r.assets.balance,9700);
});

test('lost activation response and old toggle replay cannot re-enable a paused branch',async t=>{
 const {request,s,path}=await setup(t);
 const activation={operationId:'open',sourceId:'example',initial:initialAssets(rules),confirmed:true,context:{profiles:[],modIds:[]}};
 assert.equal((await request('/activate',activation)).id,s.id);
 const pause={operationId:'pause',revision:s.revision,enabled:false};let r=await request(path+'/enabled',pause);
 assert.equal((await request('/activate',activation)).enabled,false);
 const resume={operationId:'resume',revision:r.revision,enabled:true};r=await request(path+'/enabled',resume);
 assert.equal((await request(path+'/enabled',pause)).enabled,true);
});

test('closed purchase grammar uses no parser and transcript preview never writes ledger',async t=>{
 const {host,provider,request,s,path}=await setup(t);
 await request(path+'/input',{operationId:'direct',revision:s.revision,input:'买两个面包'});await host.process(s.id,'direct');
 const r=await request(path);assert.equal(provider.calls,1);assert.equal(r.assets.balance,8800);
 const transcript=await request(path+'/export'),before=await host.store.read(s.id);
 const preview=await request('/preview-transcript',transcript);assert.equal(preview.readonly,true);assert.equal(preview.turns.length,1);
 assert.deepEqual(await host.store.read(s.id),before);
});

test('twenty small summaries merge before next narration, then update only the new turn',async t=>{
 const {host,provider,request,s,path}=await setup(t);
 const purposes=[],dispatch=provider.dispatch;provider.dispatch=async q=>{purposes.push(q.purpose);return dispatch(q);};
 await host.store.update(s.id,r=>{
  r.memory.summaryEnabled=true;r.memory.memoryEnabled=true;r.memory.baseTurns=20;
  r.memory.summary.turns=Array.from({length:20},(_,i)=>({id:i+1,requestId:'old-'+i,small:'曾在街角散步 '+i}));
 });
 const r=await request(path);
 await request(path+'/purchase',{operationId:'buy',revision:r.revision,ruleVersion:rules.version,productId:'bread',quantity:2});
 await host.process(s.id,'buy');const done=await host.store.read(s.id);
 assert.deepEqual(purposes,['summary-large','narration','summary-small','memory']);
 assert.equal(done.memory.summary.covered,20);assert.equal(done.memory.summary.turns.at(-1).id,21);
 assert.equal(done.assets.balance,8800);assert.equal(done.active,null);assert.equal(provider.calls,4);
 assert.match(done.stageRuns['buy-narration'].messages[0].content,/总结|摘要/);
});

test('failed memory retry never repeats narration or settlement',async t=>{
 const {host,provider,request,s,path}=await setup(t);
 await host.store.update(s.id,r=>{r.memory.memoryEnabled=true;});
 const original=provider.dispatch;let failed=false;
 provider.dispatch=async q=>{const out=await original(q);if(q.purpose==='memory'&&!failed){failed=true;return {...out,status:'failed',safeToRetry:true,code:'MEMORY_FIXTURE_FAILURE'};}return out;};
 let r=await request(path);
 await request(path+'/purchase',{operationId:'buy',revision:r.revision,ruleVersion:rules.version,productId:'bread',quantity:2});await host.process(s.id,'buy');
 r=await request(path);assert.equal(r.events[0].narration.status,'failed');assert.equal(r.events[0].pendingStage,'memory');
 await request(path+'/retry',{operationId:'retry-memory',revision:r.revision,eventId:'buy',purpose:'memory'});await host.process(s.id,'buy');
 r=await request(path);assert.equal(r.active,null);assert.equal(provider.calls,3);assert.equal(r.events.length,1);assert.equal(r.assets.balance,8800);
});
