import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import{tmpdir}from'node:os';import{join,resolve}from'node:path';
import{start}from'../studio/server.mjs';import{initialAssets}from'./shop-rules.mjs';import{rules}from'./earth-adapter.mjs';
test('HTTP preview, branched activation, memory pipeline and legacy isolation',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'shop-http-'));
 const app=await start({port:0,directory,shop:true,enabled:false,limit:0});const base='http://127.0.0.1:'+app.server.address().port;
 t.after(async()=>{await app.shop.close();await new Promise(r=>app.server.close(r));await app.plugins?.close();assert.ok(resolve(directory).startsWith(resolve(tmpdir())+'\\shop-http-'));await rm(directory,{recursive:true,force:true});});
 async function req(path,body){const response=await fetch(base+path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json',Origin:base}:{},...(body?{body:JSON.stringify(body)}:{})});const data=await response.json();return {status:response.status,data};}
 assert.equal((await fetch(base+'/rpg-app/earth/?session=shop-start')).status,200);
 const original=await app.earth.create({characterText:'姓名：林川',world:'表世界',params:{},mode:'offline'}),frozen=JSON.stringify(await app.earth.read(original.id));
 let response=await req('/rpg-app/earth/api/shop/activate',{operationId:'open',sourceId:original.id,initial:initialAssets(rules),confirmed:true,context:{profiles:[],modIds:[],summaryEnabled:true,memoryEnabled:true}});
 assert.equal(response.status,201);let s=response.data;const path='/rpg-app/earth/api/shop/sessions/'+s.id;
 response=await req(path+'/purchase',{operationId:'buy',revision:s.revision,ruleVersion:rules.version,productId:'bread',quantity:2});assert.equal(response.status,200);
 await app.shop.process(s.id,'buy');s=(await req(path)).data;assert.equal(s.active,null);assert.equal(s.usage.calls,3);assert.equal(s.assets.balance,8800);assert.equal(s.memory.summary.turns.length,1);
 assert.equal(JSON.stringify(await app.earth.read(original.id)),frozen);
 assert.equal((await req('/rpg-app/earth/api/sessions/'+s.id+'/send',{requestId:'candidate',input:'重做购买'})).status,409);
 assert.equal((await req(path+'/export')).data.events.length,1);
 assert.equal((await req('/rpg-app/earth/api/sessions/'+original.id)).status,200);
 // Old setup and card material remain accepted after adding the optional shop.
 assert.equal(app.earth.status(await app.earth.read(original.id)).compatible,true);
});


test('shop prototype is unavailable unless the isolated host opts in',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'shop-http-disabled-'));
 const app=await start({port:0,directory,enabled:false,limit:0});
 t.after(async()=>{await new Promise(r=>app.server.close(r));await app.plugins?.close();assert.ok(resolve(directory).startsWith(resolve(tmpdir())+'\\shop-http-disabled-'));await rm(directory,{recursive:true,force:true});});
 assert.equal(app.shop,null);
 const base='http://127.0.0.1:'+app.server.address().port;
 assert.equal((await fetch(base+'/rpg-app/earth/api/shop/status')).status,404);
 assert.equal((await fetch(base+'/rpg-app/earth/api/shop/activate',{method:'POST',headers:{'Content-Type':'application/json',Origin:base},body:'{}'})).status,404);
});
