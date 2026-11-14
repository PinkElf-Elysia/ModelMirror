import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {startServer} from './server.mjs';import {SAMPLES} from './provider.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/e5-tests/',import.meta.url));await mkdir(root,{recursive:true});
test('HTTP persists proposal only; rejects foreign origin, execution and unbounded commands',async()=>{
 await assert.rejects(startServer(),{code:'E5_PREVIEW_DISABLED'});
 const app=await startServer({offlinePreview:true,port:0,directory:await mkdtemp(root+'http-')});
 try{const first=await (await fetch(app.url+'api/view')).json();assert.equal(first.realCalls,0);
 const c={adapterId:'cultivation',operationId:'http-one',expectedHash:null,kind:'reply',text:SAMPLES.cultivation[0]},headers={'Content-Type':'application/json','X-RPG-E5':'plan-only'};
 let r=await fetch(app.url+'api/plan',{method:'POST',headers,body:JSON.stringify(c)});assert.equal(r.status,200);const v=await r.json();assert.equal(v.plan.steps.length,2);assert.deepEqual(v.source,first.source);
 r=await fetch(app.url+'api/plan',{method:'POST',headers:{...headers,Origin:'https://evil.invalid'},body:JSON.stringify(c)});assert.equal(r.status,400);
 assert.equal((await fetch(app.url+'api/execute',{method:'POST',headers,body:'{}'})).status,404);
 assert.equal((await fetch(app.url+'api/export')).status,400);
 r=await fetch(app.url+'api/plan',{method:'POST',headers,body:JSON.stringify({...c,operationId:'save',kind:'save',expectedHash:v.recordHash,text:null})});assert.equal(r.status,200);
 const out=await (await fetch(app.url+'api/export')).json();assert.equal(out.executionStatus,'not_executed');assert.equal(out.requiresExecutionValidation,true);
 const home=await fetch(app.url);assert.match(home.headers.get('content-security-policy'),/default-src 'self'/);assert.match(await home.text(),/整理模拟输入/);
 }finally{await app.close();}
});
