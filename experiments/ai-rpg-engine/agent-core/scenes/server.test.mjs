import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir} from 'node:fs/promises';import {join} from 'node:path';import {fileURLToPath} from 'node:url';
import {startServer} from './server.mjs';
test('offline HTTP entry supports full scene, refresh and scoped writes; no real route',async t=>{
 const root=fileURLToPath(new URL('../../.rpg03-work/r9-tests/',import.meta.url));await mkdir(root,{recursive:true});
 const app=await startServer({directory:await mkdtemp(join(root,'http-')),port:0,offlinePreview:true,delayMs:0});t.after(()=>app.close());
 const get=path=>fetch(new URL(path,app.url)),post=(path,data,extra={})=>fetch(new URL(path,app.url),{method:'POST',headers:{'content-type':'application/json','x-rpg-scene':'offline-preview',...extra},body:JSON.stringify(data)});
 assert.equal((await get('/')).status,200);assert.match(await (await get('/app.mjs')).text(),/awaiting-adoption/);
 assert.equal((await post('/api/runs',{operationId:'bad',adapterId:'lighthouse'},{origin:'https://foreign.invalid'})).status,400);
 assert.equal((await post('/api/runs',{operationId:'start',adapterId:'lighthouse',real:true})).status,400);
 let res=await post('/api/runs',{operationId:'start',adapterId:'lighthouse'});assert.equal(res.status,200);let v=await res.json(),prefix='/api/runs/'+v.id;
 v=await (await post(prefix+'/submit',{operationId:'talk',ownerId:'participant-keeper',input:'你好'})).json();assert.equal(v.tasks[0].adoption,'awaiting-adoption');
 v=await (await post(prefix+'/adopt',{taskId:v.tasks[0].id})).json();assert.equal(v.scene.entries.length,1);
 const fresh=await (await get(prefix)).json();assert.equal(fresh.scene.entries.length,1);assert.equal(fresh.budget.calls,1);
 assert.equal((await get('/api/real-call')).status,400);assert.equal((await post(prefix+'/capabilities',{})).status,400);
 await assert.rejects(startServer({port:0,offlinePreview:false}),/OFFLINE/);
});
