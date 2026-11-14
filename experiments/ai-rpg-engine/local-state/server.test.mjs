import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {startServer} from './server.mjs';
test('HTTP preview initializes, advances, refreshes, exports and forks in isolated records',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'rpg-state-http-'));const app=await startServer({directory:dir,port:0,enabled:true});
 t.after(async()=>{await app.close();assert.ok(dir.startsWith(join(tmpdir(),'rpg-state-http-')));await rm(dir,{recursive:true,force:true});});
 const call=async(path,data)=>{const r=await fetch(app.url+path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-Local-State':'preview'}:{},body:data?JSON.stringify(data):undefined});assert.ok(r.ok,await r.clone().text());return r.json();};
 const page=await fetch(app.url);assert.ok((await page.text()).includes('本地状态预览'));
 const boot=await call('api/bootstrap');let r=await call('api/sessions',{operationId:'initialize',initial:boot.state,confirmed:true});
 const url='api/sessions/'+r.id;
 r=await call(url+'/turn',{operationId:'consume',revision:r.revision,input:'服用两份养息丹'});
 const fresh=await call(url);assert.equal(fresh.state.items[0].quantity,3);assert.equal(fresh.realCalls,0);assert.ok(fresh.panels.includes('养息丹 × 3'));
 const proof=await call(url+'/evidence');assert.equal(proof.record.operations.consume.receipt.provider,'offline-simulator');
 const branch=await call(url+'/fork',{operationId:'fork',revision:r.revision,eventId:null});assert.equal(branch.state.items[0].quantity,5);assert.equal((await call(url)).state.items[0].quantity,3);
 const hostile=await fetch(app.url+url+'/recover',{method:'POST',headers:{'Content-Type':'application/json','Origin':'https://other.invalid','X-Local-State':'preview'},body:'{}'});assert.equal(hostile.status,400);
});
test('preview is disabled without explicit startup option',async()=>{
 await assert.rejects(()=>startServer({directory:join(tmpdir(),'not-created-state-preview'),port:0}),{code:'STATE_PREVIEW_DISABLED'});
});
