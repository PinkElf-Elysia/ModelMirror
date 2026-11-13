import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {ShopStore} from './shop-store.mjs';import {initialAssets} from './shop-rules.mjs';import {rules,binding,assembleNarration,narrationIssue} from './earth-adapter.mjs';
import {StageRunner} from './stage-runner.mjs';import {createMockProvider} from './mock-provider.mjs';
async function setup(t,mode,backup=true){
 const directory=await mkdtemp(join(tmpdir(),'shop-stage-')),store=await ShopStore.open({directory,rules,binding});
 t.after(async()=>{await store.close();assert.ok(directory.startsWith(join(tmpdir(),'shop-stage-')));await rm(directory,{recursive:true,force:true});});
 let r=await store.activate({operationId:'open',source:{id:'original',world:'表世界',characterText:'林川',history:[]},initial:initialAssets(rules),confirmed:true});
 const provider=createMockProvider({mode}),runner=new StageRunner({store,provider,assemble:(r,e)=>assembleNarration({session:r.session,event:e,state:{...r.assets,binding:r.binding,revision:r.stateRevision}}),validate:(raw,r)=>narrationIssue(raw,r.assets)});
 r=(await runner.configure(r.id,{operationId:'cfg',revision:r.revision,purpose:'narration',primary:'mock-narrator',backup:backup?'mock-fast':null,cap:0})).record;
 r=(await store.purchase(r.id,{operationId:'buy',revision:r.revision,ruleVersion:rules.version,productId:'bread',quantity:2})).record;
 return {store,r,runner,provider};
}
test('one direct trade uses one narration call and duplicate run is read-only',async t=>{
 const {store,r,runner,provider}=await setup(t,'success');const a=await runner.run(r.id,'buy');assert.equal(a.assets.balance,8800);assert.equal(a.active,null);assert.equal(a.projection.length,1);
 await runner.run(r.id,'buy');assert.equal(provider.calls,1);assert.equal((await store.read(r.id)).usage.calls,1);
});
test('confirmed failed primary switches once without repeating trade',async t=>{
 const {r,runner,provider}=await setup(t,'fail-primary');const a=await runner.run(r.id,'buy');assert.equal(provider.calls,2);assert.equal(a.events.length,1);assert.equal(a.assets.balance,8800);assert.equal(a.events[0].narration.switched,true);
});
test('unknown does not switch, lookup original completes with no new dispatch',async t=>{
 const {r,runner,provider}=await setup(t,'unknown');let a=await runner.run(r.id,'buy');assert.equal(a.active,'buy');assert.equal(a.events[0].narration.status,'unknown');assert.equal(provider.calls,1);
 a=await runner.run(r.id,'buy','narration',true);assert.equal(a.active,null);assert.equal(provider.calls,1);
});
test('both models fail and receipt-only releases turn, preserving settled assets',async t=>{
 const {r,runner,store,provider}=await setup(t,'fail-all');const a=await runner.run(r.id,'buy');assert.equal(provider.calls,2);assert.equal(a.events[0].narration.status,'failed');
 const b=await store.endReceiptOnly(r.id,{operationId:'skip',revision:a.revision,eventId:'buy'});assert.equal(b.record.active,null);assert.equal(b.record.assets.balance,8800);
});
test('no backup means no automatic extra call; concurrent starts share one call',async t=>{
 const {r,runner,provider}=await setup(t,'fail-all',false);await Promise.all([runner.run(r.id,'buy'),runner.run(r.id,'buy')]);assert.equal(provider.calls,1);
});
test('wrong receipt identity is unknown and cannot authorize fallback',async t=>{
 const {r,runner,provider}=await setup(t,'success');const dispatch=provider.dispatch;provider.dispatch=async q=>({...await dispatch(q),requestId:'wrong'});
 const a=await runner.run(r.id,'buy');assert.equal(a.events[0].narration.status,'unknown');assert.equal(provider.calls,1);
});
test('cost cap checked before dispatch',async t=>{
 const {r,runner,provider}=await setup(t,'success');const catalog=provider.catalog;provider.catalog=async()=> (await catalog()).map(x=>({...x,upperBoundMicros:5}));
 const a=await runner.run(r.id,'buy');assert.equal(provider.calls,0);assert.equal(a.events[0].narration.error,'SHOP_COST_LIMIT');assert.equal(a.assets.balance,8800);
});

test('manual retry cannot reset accumulated stage spending',async t=>{
 const {r,runner,store,provider}=await setup(t,'fail-all',false);
 const catalog=provider.catalog,dispatch=provider.dispatch;
 provider.catalog=async()=>(await catalog()).map(x=>({...x,upperBoundMicros:5}));
 provider.dispatch=async q=>({...await dispatch(q),costMicros:5});
 await store.update(r.id,n=>{n.stages.narration.cap=5;});
 let a=await runner.run(r.id,'buy');assert.equal(a.usage.settled,5);
 await runner.retry(r.id,{operationId:'retry',revision:a.revision,eventId:'buy',purpose:'narration'});
 a=await runner.run(r.id,'buy');assert.equal(provider.calls,1);assert.equal(a.usage.settled,5);assert.equal(a.stageRuns['buy-narration'].error,'SHOP_COST_LIMIT');
});

test('unknown calls cannot be retried or discarded',async t=>{
 const {r,runner,store}=await setup(t,'unknown');
 const a=await runner.run(r.id,'buy');
 await assert.rejects(runner.retry(r.id,{operationId:'retry',revision:a.revision,eventId:'buy',purpose:'narration'}),/SHOP_RETRY_NOT_ALLOWED/);
 await assert.rejects(store.endReceiptOnly(r.id,{operationId:'skip',revision:a.revision,eventId:'buy'}),/SHOP_NARRATION_NOT_FAILED/);
});
