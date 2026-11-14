import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {startServer} from './server.mjs';
test('loopback HTTP pipeline: bootstrap, propose, confirm, receipt, narration, refresh, duplicate and origin guard',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'r5-http-')),app=await startServer({directory:dir,port:0,offlinePreview:true});
 t.after(async()=>{await app.close();await rm(dir,{recursive:true,force:true});});
 const send=async(path,data,headers={})=>fetch(app.url+path,{method:'POST',headers:{'Content-Type':'application/json','X-Rpg-Actions':'offline-preview',...headers},body:JSON.stringify(data)});
 const boot=await (await fetch(app.url+'api/bootstrap')).json(),a=boot.adapters.find(a=>a.id==='lighthouse'),root='api/lighthouse/sessions';
 let r=await(await send(root,{operationId:'open',initial:a.initial,confirmed:true})).json(),path=root+'/'+r.id;
 const read=async()=> (await fetch(app.url+path)).json();
 r=await(await send(path+'/propose',{operationId:'proposal',revision:r.revision,action:{actionId:'fuel_lamp',itemId:'lamp-oil',quantity:2}})).json();
 let v=await read();assert.equal(v.state.supplies[0].quantity,5);
 const req={operationId:'execute',revision:r.revision,proposalId:'proposal',proposalHash:v.proposals.proposal.proposal.hash};
 assert.equal((await send(path+'/execute',req,{Origin:'https://outside.example'})).status,400);
 assert.equal((await send(path+'/execute',req)).status,200);v=await read();assert.equal(v.state.supplies[0].quantity,3);assert.ok(v.pending);assert.equal(v.turns.length,0);
 assert.equal((await send(path+'/execute',req)).status,200);assert.equal((await read()).actionReceipts.length,1);
 assert.equal((await send(path+'/narrate',{})).status,200);v=await read();assert.equal(v.pending,null);assert.equal(v.turns.length,1);
 assert.equal((await fetch(app.url)).status,200);assert.equal((await fetch(app.url+path+'/evidence')).status,200);
});
