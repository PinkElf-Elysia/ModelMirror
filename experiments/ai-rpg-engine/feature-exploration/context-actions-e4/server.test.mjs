import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {startServer} from './server.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/e4-tests/',import.meta.url));await mkdir(root,{recursive:true});
test('isolated HTTP complete plan and query; no execution/foreign-origin/source-write route',async t=>{
 const directory=await mkdtemp(root+'http-'),app=await startServer({port:0,directory,offlinePreview:true});t.after(()=>app.close());
 let v=await (await fetch(app.url+'api/view')).json();const before=v.source.recordHash;
 assert.equal((await fetch(app.url)).status,200);assert.equal((await fetch(app.url+'api/inspect')).status,200);
 const cmd={adapterId:'cultivation',kind:'reply',operationId:'http-1',expectedHash:v.recordHash,text:'我想服用两份养息丹'};
 const send=body=>fetch(app.url+'api/draft',{method:'POST',headers:{'content-type':'application/json','x-rpg-e4':'proposal-only'},body:JSON.stringify(body)});
 v=await (await send(cmd)).json();assert.equal(v.draft.current.preflight.expected[0].after,3);
 v=await (await send({...cmd,kind:'accept',operationId:'http-2',expectedHash:v.recordHash})).json();assert.equal(v.draft.current.status,'accepted');
 const out=await (await fetch(app.url+'api/export')).json();assert.equal(out.executionStatus,'not_executed');
 assert.equal((await fetch(app.url+'api/execute',{method:'POST'})).status,404);
 assert.equal((await fetch(app.url+'api/view',{headers:{origin:'https://elsewhere.example'}})).status,400);
 assert.equal((await send({...cmd,kind:'execute',operationId:'x',expectedHash:v.recordHash})).status,400);
 assert.equal((await (await fetch(app.url+'api/view')).json()).source.recordHash,before);
});
