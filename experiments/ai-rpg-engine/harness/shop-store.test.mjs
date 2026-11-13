import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';import {join} from 'node:path';
import {ShopStore} from './shop-store.mjs';import {rules,binding} from './earth-adapter.mjs';
import {initialAssets,purchaseResult} from './shop-rules.mjs';
const source={id:'legacy-1',world:'表世界',characterText:'林川',history:[],revision:0};
async function setup(t,{balance=10000,fault}={}){
 const directory=await mkdtemp(join(tmpdir(),'rpg-shop-'));let store=await ShopStore.open({directory,rules,binding,fault});
 t.after(async()=>{await store.close();assert.ok(directory.startsWith(join(tmpdir(),'rpg-shop-')));await rm(directory,{recursive:true,force:true});});
 const activate={operationId:'activate-1',source,initial:initialAssets(rules,balance),confirmed:true};
 const r=await store.activate(activate);
 return {store,r,activate,directory,reopen:async()=>{await store.close();store=await ShopStore.open({directory,rules,binding});return store;}};
}
const buy=(r,more={})=>({operationId:'buy-1',revision:r.revision,ruleVersion:rules.version,productId:'bread',quantity:2,...more});
test('buy atomically settles and duplicate returns same receipt',async t=>{
 const {store,r}=await setup(t);const req=buy(r),a=await store.purchase(r.id,req),b=await store.purchase(r.id,req);
 assert.equal(a.record.assets.balance,8800);assert.equal(a.record.assets.stock.bread,0);assert.equal(a.record.assets.inventory.bread,2);
 assert.equal(a.record.events.length,1);assert.equal(a.record.active,req.operationId);assert.equal(b.replayed,true);assert.deepEqual(a.receipt,b.receipt);
 await assert.rejects(store.purchase(r.id,{...req,quantity:1}),/SHOP_OPERATION_COLLISION/);
});
test('insufficient balance and stock do not mutate assets',async t=>{
 const {store,r}=await setup(t,{balance:500});const a=await store.purchase(r.id,buy(r));
 assert.equal(a.receipt.reason,'SHOP_BALANCE_INSUFFICIENT');assert.deepEqual(a.record.assets,r.assets);assert.equal(a.record.active,null);
 const b=await store.purchase(r.id,buy(a.record,{operationId:'buy-2',quantity:3}));assert.equal(b.receipt.reason,'SHOP_STOCK_INSUFFICIENT');
});
test('invalid numbers, unknown products, client price and overflow rejected',async t=>{
 const {store,r}=await setup(t);
 for(const quantity of [0,-1,1.5,Number.MAX_SAFE_INTEGER,Infinity,'2'])await assert.rejects(store.purchase(r.id,buy(r,{quantity})));
 await assert.rejects(store.purchase(r.id,buy(r,{productId:'absent'})),/SHOP_PRODUCT_UNKNOWN/);
 await assert.rejects(store.purchase(r.id,{...buy(r),price:1}),/SHOP_PURCHASE_INVALID/);
 const a=initialAssets(rules);a.inventory.bread=Number.MAX_SAFE_INTEGER;assert.throws(()=>purchaseResult(a,rules,{productId:'bread',quantity:1}),/OVERFLOW/);
});
test('stale state and rules rejected; active narration blocks new transaction',async t=>{
 const {store,r}=await setup(t);
 await assert.rejects(store.purchase(r.id,buy(r,{revision:99})),/SHOP_STATE_STALE/);
 await assert.rejects(store.purchase(r.id,buy(r,{ruleVersion:'other'})),/SHOP_RULE_STALE/);
 const a=await store.purchase(r.id,buy(r));await assert.rejects(store.purchase(r.id,buy(a.record,{operationId:'buy-2'})),/SHOP_TURN_PENDING/);
});
test('concurrent different requests cannot both settle same revision',async t=>{
 const {store,r}=await setup(t);const out=await Promise.allSettled([store.purchase(r.id,buy(r)),store.purchase(r.id,buy(r,{operationId:'buy-2',quantity:1}))]);
 assert.equal(out.filter(x=>x.status==='fulfilled').length,1);assert.equal((await store.read(r.id)).events.length,1);
});
test('commit-before/after fault reconciles without double charge',async t=>{
 for(const point of ['beforeCommit','afterCommit']){
  let armed=false;const s=await setup(t,{fault:async p=>{if(armed&&p===point){armed=false;throw Error('simulated crash');}}});
  armed=true;await assert.rejects(s.store.purchase(s.r.id,buy(s.r)),/OFFLINE_WRITE_/);
  await s.store.reconcile(s.r.id);const before=await s.store.read(s.r.id);
  assert.equal(before.assets.balance,point==='afterCommit'?8800:10000);
  const a=await s.store.purchase(s.r.id,buy(s.r));assert.equal(a.record.assets.balance,8800);assert.equal(a.record.events.length,1);
 }
});
test('restart retains operation and pending narration',async t=>{
 const s=await setup(t);await s.store.purchase(s.r.id,buy(s.r));const store=await s.reopen();
 assert.equal((await store.read(s.r.id)).active,'buy-1');assert.equal((await store.purchase(s.r.id,buy(s.r))).replayed,true);
});
test('override is separate traceable operation; disabled state retains assets',async t=>{
 const {store,r}=await setup(t);
 const a=await store.override(r.id,{operationId:'edit-1',revision:0,assets:{...r.assets,balance:2500},reason:'用户明确修订开局',relatedEventId:null});
 assert.equal(a.receipt.before.balance,10000);assert.equal(a.receipt.after.balance,2500);
 const b=await store.enabled(r.id,{operationId:'disable-1',revision:a.record.revision,enabled:false});
 assert.equal(b.record.assets.balance,2500);await assert.rejects(store.purchase(r.id,buy(b.record)),/SHOP_DISABLED/);
});
test('only explicit failed narration can end receipt-only; unknown cannot be bypassed',async t=>{
 const {store,r}=await setup(t);await store.purchase(r.id,buy(r));
 let s=await store.update(r.id,x=>{x.events[0].narration.status='unknown';});
 await assert.rejects(store.endReceiptOnly(r.id,{operationId:'skip-1',revision:s.revision,eventId:'buy-1'}),/NOT_FAILED/);
 s=await store.update(r.id,x=>{x.events[0].narration.status='failed';});
 const a=await store.endReceiptOnly(r.id,{operationId:'skip-1',revision:s.revision,eventId:'buy-1'});assert.equal(a.record.active,null);assert.equal(a.record.assets.balance,8800);
});
test('activation is idempotent and requires confirmed initial state',async t=>{
 const {store,r,activate}=await setup(t);assert.equal((await store.activate(activate)).id,r.id);assert.equal(source.history.length,0);
 await assert.rejects(store.activate({...activate,initial:initialAssets(rules,1)}),/SHOP_OPERATION_COLLISION/);
 await assert.rejects(store.activate({...activate,operationId:'activate-2',confirmed:false}),/CONFIRMATION/);
});
test('checkpoint fork preserves assets at selected event without affecting parent',async t=>{
 const {store,r}=await setup(t);
 let a=await store.override(r.id,{operationId:'edit-1',revision:0,assets:{...r.assets,balance:8000},reason:'第一处改写',relatedEventId:null});
 await store.override(r.id,{operationId:'edit-2',revision:a.record.revision,assets:{...r.assets,balance:6000},reason:'第二处改写',relatedEventId:'edit-1'});
 const child=await store.fork(r.id,{operationId:'fork-1',eventId:'edit-1'});assert.equal(child.assets.balance,8000);
 assert.equal((await store.read(r.id)).assets.balance,6000);assert.equal(child.events.length,1);assert.equal((await store.fork(r.id,{operationId:'fork-1',eventId:'edit-1'})).id,child.id);
});
