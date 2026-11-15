import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';import {startServer} from './server.mjs';
test('preview defaults off; same-origin create, free input, refresh and port collision',async t=>{
 await assert.rejects(startServer({port:0}),/ASSEMBLY_OFFLINE_ONLY/);const dir=await mkdtemp(join(tmpdir(),'r8-http-'));const app=await startServer({directory:dir,port:0,offlinePreview:true});t.after(async()=>{await app.close();await rm(dir,{recursive:true,force:true});});
 const post=(p,data,h={})=>fetch(app.url+'api/runs'+p,{method:'POST',headers:{'Content-Type':'application/json','X-RPG-Assembly':'offline-preview',...h},body:JSON.stringify(data)});
 assert.equal((await post('',{operationId:'x',adapterId:'lighthouse'},{Origin:'https://outside.example'})).status,400);
 assert.equal((await post('',{operationId:'x',adapterId:'lighthouse',url:'https://outside.example'})).status,400);
 const r=await(await post('',{operationId:'start',adapterId:'lighthouse'})).json();assert.equal(r.budget.calls,0);
 const v=await(await post('/'+r.id+'/submit',{operationId:'talk',ownerId:'world-dialogue',input:'并非固定按钮文本'})).json();assert.equal(v.tasks[0].adoption,'completed');
 assert.equal((await(await fetch(app.url+'api/runs/'+r.id)).json()).budget.calls,1);assert.equal((await fetch(app.url)).status,200);
 assert.equal((await fetch(app.url+'../bootstrap.mjs')).status,400);
 await assert.rejects(startServer({directory:join(dir,'other'),port:app.server.address().port,offlinePreview:true}),{code:'EADDRINUSE'});
});
