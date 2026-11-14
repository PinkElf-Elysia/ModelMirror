import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {startToolsServer} from './server.mjs';
test('native preview round-trip uses real local queries and displays final state',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'native-http-')),app=await startToolsServer({directory:dir,port:0,enabled:true});
 t.after(async()=>{await app.close();assert.ok(dir.startsWith(join(tmpdir(),'native-http-')));await rm(dir,{recursive:true,force:true});});
 const call=async(path,data)=>{const res=await fetch(app.url+path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-Local-State':'preview'}:{},body:data?JSON.stringify(data):undefined});assert.equal(res.status,200===res.status?200:201,await res.clone().text());return res.json();};
 const boot=await call('api/bootstrap');assert.equal(boot.mode,'cultivation-local-state-tools/2');assert.match(boot.scenarios[0][1],/查看/);
 let r=await call('api/sessions',{operationId:'init',confirmed:true,initial:boot.state});
 r=await call('api/sessions/'+r.id+'/turn',{operationId:'query',revision:0,input:'先查询丹药，再服用两份养息丹'});
 const view=await call('api/sessions/'+r.id);assert.equal(view.nativeQueries.query.totalModelRequests,2);assert.equal(view.state.items[0].quantity,3);
 assert.equal(view.nativeQueries.query.query.results[0].entries[0].quantity,5);
 assert.ok(!JSON.stringify(view).includes('thought_signature'));
 assert.equal((await call('api/sessions/'+r.id+'/progress')).text,null);
 const appJS=await (await fetch(app.url+'app.mjs')).text();assert.match(appJS,/本轮查询依据/);
});
