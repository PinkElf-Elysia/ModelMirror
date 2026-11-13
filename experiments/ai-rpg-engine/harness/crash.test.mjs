import test from 'node:test';import assert from 'node:assert/strict';import{mkdtemp,rm}from'node:fs/promises';import{tmpdir}from'node:os';import{join,resolve}from'node:path';import{execFile}from'node:child_process';import{promisify}from'node:util';
import{ShopStore}from'./shop-store.mjs';import{rules,binding}from'./earth-adapter.mjs';import{initialAssets}from'./shop-rules.mjs';
test('abrupt process exit after publication reconciles durable marker with no second debit',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'shop-crash-'));let store=await ShopStore.open({directory,rules,binding});
 try{
  const r=await store.activate({operationId:'open',source:{id:'original',world:'表世界',characterText:'林川',history:[]},initial:initialAssets(rules),confirmed:true});
  const request={operationId:'buy',revision:r.revision,ruleVersion:rules.version,productId:'bread',quantity:2};await store.close();
  const code="import{ShopStore}from'./harness/shop-store.mjs';import{rules,binding}from'./harness/earth-adapter.mjs';const s=await ShopStore.open({directory:process.argv[1],rules,binding,fault:async at=>{if(at==='afterCommit')process.exit(73);}});await s.purchase(process.argv[2],JSON.parse(process.argv[3]));";
  await assert.rejects(promisify(execFile)(process.execPath,['--input-type=module','-e',code,directory,r.id,JSON.stringify(request)]),e=>e.code===73);
  store=await ShopStore.open({directory,rules,binding,recoverDeadOwner:true});
  assert.equal((await store.reconcile(r.id)).status,'committed');
  const result=await store.purchase(r.id,request);assert.equal(result.replayed,true);assert.equal(result.record.assets.balance,8800);assert.equal(result.record.events.length,1);
 }finally{await store.close();assert.ok(resolve(directory).startsWith(resolve(tmpdir())+'\\shop-crash-'));await rm(directory,{recursive:true,force:true});}
});
