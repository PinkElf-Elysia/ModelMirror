import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';import {startServer} from './server.mjs';
test('default off; isolated HTTP, origin, input limits and refresh',async t=>{
 await assert.rejects(startServer({port:0}),{code:'R7_PREVIEW_DISABLED'});const d=await mkdtemp(join(tmpdir(),'r7-http-')),app=await startServer({directory:d,port:0,offlinePreview:true});t.after(async()=>{await app.close();await rm(d,{recursive:true,force:true});});
 const post=(path,body,extra={})=>fetch(app.url+'api/runs'+path,{method:'POST',headers:{'Content-Type':'application/json','X-RPG-Task':'offline-preview',...extra},body:JSON.stringify(body)});
 assert.equal((await post('',{operationId:'bad'},{Origin:'https://outside.example'})).status,400);
 assert.equal((await post('',{operationId:'bad',supplierURL:'https://outside.example'})).status,400);
 let res=await post('',{operationId:'first'});assert.equal(res.status,200);const v=await res.json();assert.equal(v.budget.calls,0);
 assert.equal((await fetch(app.url+'api/runs/'+v.id)).status,200);assert.equal((await fetch(app.url+'api/runs')).status,200);
 assert.equal((await fetch(app.url+'../../package.json')).status,400);
 const second=await startServer({directory:join(d,'other'),port:0,offlinePreview:true});await second.close();
 await assert.rejects(startServer({directory:join(d,'collision'),port:app.server.address().port,offlinePreview:true}),{code:'EADDRINUSE'});
});
