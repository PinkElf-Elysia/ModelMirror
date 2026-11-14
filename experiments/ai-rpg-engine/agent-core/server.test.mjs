import {request as httpRequest} from 'node:http';
import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {startServer} from './server.mjs';
test('preview defaults off; HTTP schema selection, native flow, recovery and origin checks',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'rpg-core-http-'));let app;t.after(async()=>{if(app)await app.close();await rm(dir,{recursive:true,force:true});});
 await assert.rejects(startServer({directory:dir,port:0}),{code:'CORE_PREVIEW_DISABLED'});
 app=await startServer({directory:dir,port:0,offlinePreview:true});
 const post=(path,body,headers={})=>fetch(app.url+path,{method:'POST',headers:{'Content-Type':'application/json','x-rpg-core':'offline-preview',...headers},body:JSON.stringify(body)});
 const page=await fetch(app.url);assert.equal(page.status,200);assert.match(page.headers.get('content-security-policy'),/connect-src 'self'/);assert.match(await page.text(),/离线模拟/);
 const bootstrap=await (await fetch(app.url+'api/bootstrap')).json();assert.equal(bootstrap.realCalls,0);assert.equal(bootstrap.adapters.length,2);
 for(const a of bootstrap.adapters){
  const base='api/'+a.id+'/sessions';let response=await post(base,{operationId:'op-http',initial:a.initial,confirmed:true});assert.equal(response.status,201);let r=await response.json();
  const url=base+'/'+r.id,input=a.id==='cultivation'?'先查询丹药，再服用两份养息丹':'先查询灯油，再给灯具添油两份';
  response=await post(url+'/turn',{operationId:'op-turn',revision:r.revision,input,options:{fault:'unknown'}});assert.equal(response.status,200);r=await response.json();assert.equal(r.operations[r.pending].status,'unknown');
  response=await post(url+'/recover',{});r=await response.json();assert.equal(r.pending,null);assert.equal(r.turns.length,1);
  const view=await (await fetch(app.url+url)).json();assert.ok(view.panels);assert.equal(view.simulated,true);
  const evidence=await (await fetch(app.url+url+'/evidence')).json();assert.equal(evidence.realCalls,0);assert.equal(evidence.record.operations['op-turn'].stages.length,2);
  assert.equal((await post(url+'/enabled',{operationId:'op-bad',revision:r.revision,enabled:false},{Origin:'https://example.org'})).status,400);
 }
 assert.equal((await fetch(app.url+'api/unknown/sessions')).status,400);
 const denied=await new Promise((ok,no)=>{const req=httpRequest(app.url+'api/bootstrap',{headers:{Host:'hostile.example'}},res=>{res.resume();res.on('end',()=>ok(res.statusCode));});req.on('error',no);req.end();});assert.equal(denied,400);
});
