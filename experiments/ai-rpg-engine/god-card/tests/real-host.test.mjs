import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {start} from '../../studio/server.mjs';
import {canonical,sha} from '../../plugins/catalog.mjs';
import {loadFrozen} from '../lib/assembly.mjs';
import {loadOutputSuffix} from '../lib/output-suffix.mjs';
import {serializeSetup} from '../lib/setup.mjs';
import {GOD_REAL_MODEL,GOD_REAL_PARAMETERS} from '../lib/real-profile.mjs';
const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b4-tests/',import.meta.url));
const setup=JSON.parse(await readFile(new URL('../../../../docs/ai-rpg-experiment/god-card/B4-ROLE.json',import.meta.url)));
const model={model:GOD_REAL_MODEL,name:'Gemini test',selectionId:'gemini',selectionRevision:'v1',available:true,parameters:GOD_REAL_PARAMETERS};
async function fixture(t,{limit=2,unknown=false}={}){
 await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'host-')),wires=[];
 const fetcher=async(url,opts)=>{
  assert.equal(new URL(url).searchParams.get('profile'),'god-long-v1');
  if(opts.method==='GET')return Response.json({models:[model,{model:'unconfigured/other'}]});
  const b=JSON.parse(opts.body);wires.push(b);if(unknown)throw Error('synthetic connection loss');
  const raw='<details><summary>虚构验收</summary>原始正文 '+wires.length+'</details>',sse='data: '+JSON.stringify({choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model.model,actualModel:model.model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const h=await start({port:0,directory,godCard:true,godPlugins:true,limit:0,godControl:{baseURL:'http://127.0.0.1:1/',serviceToken:'synthetic-not-a-credential-god-real-test',enabled:true,limit,fetcher}});
 t.after(async()=>{await h.plugins.close();await new Promise(r=>h.server.close(r));});
 const origin='http://127.0.0.1:'+h.server.address().port;
 async function api(path,data){const r=await fetch(origin+'/rpg-app/god/api/'+path,{method:data?'POST':'GET',headers:data?{Origin:origin,'Content-Type':'application/json'}:{},...(data?{body:JSON.stringify(data)}:{})});return {status:r.status,body:await r.json()};}
 async function request(id){const {body:p}=await api('sessions/'+id+'/memory-palace');return {operationId:randomUUID(),expectedSessionRevision:p.sessionRevision,expectedMemoryRevision:p.memoryRevision,expectedContextPolicyRevision:p.effectivePolicy.revision,input:p.totalTurns?'继续观察':serializeSetup(setup)};}
 return {h,wires,directory,api,request};
}
test('real God HTTP path binds exact opening, full follow-up, receipts, independent budget and no plugin dispatch',async t=>{
 const f=await fixture(t);const {body:status}=await f.api('status');assert.equal(status.providerEnabled,true);assert.equal(status.budget.remaining,2);assert.equal(status.limit,2);assert.equal(status.remaining,2);assert.equal(status.offlineBudget,undefined);
 const {body:s,status:created}=await f.api('sessions',{setup,mode:'real'});assert.equal(created,201);assert.equal(s.mode,'real');assert.equal(f.wires.length,0);
 assert.equal(s.characterText,serializeSetup(setup));assert.ok(!s.characterText.includes('其他角色'));
 assert.equal((await f.api('sessions',{setup,mode:'offline'})).status,400);
 assert.equal((await f.api('sessions',{setup,mode:'real',system:'untrusted'})).status,400);
 const first=await f.request(s.id),r1=await f.api('sessions/'+s.id+'/send',first);assert.equal(r1.status,200);assert.equal(r1.body.operation.status,'complete',JSON.stringify(r1.body));
 const append=text=>text+'\n\n'+loadOutputSuffix().text;
 assert.deepEqual(f.wires[0].messages,[{role:'system',content:loadFrozen().system},{role:'user',content:append(s.characterText)}]);assert.deepEqual(f.wires[0].parameters,GOD_REAL_PARAMETERS);
 await f.api('sessions/'+s.id+'/send',first);assert.equal(f.wires.length,1);
 const second=await f.request(s.id),r2=await f.api('sessions/'+s.id+'/send',second);assert.equal(r2.body.operation.status,'complete');
 const disk=await f.h.god.read(s.id);assert.equal(disk.turns.length,2);assert.equal(disk.mode,'real');assert.match(disk.turns[0].model.evidence.ref,/^god\/slot-1/);
 assert.deepEqual(f.wires[1].messages,[{role:'system',content:loadFrozen().system},...disk.history.slice(0,2),{role:'user',content:append(second.input)}]);
 assert.equal(disk.turns[0].input,s.characterText);assert.equal(disk.turns[1].input,second.input);
 assert.equal(disk.history[0].content,append(s.characterText));assert.equal(disk.history[2].content,append(second.input));
 const projected=(await f.api('sessions/'+s.id)).body;
 assert.deepEqual(projected.turns.map(t=>t.input),[s.characterText,second.input]);
 const r3=await f.api('sessions/'+s.id+'/send',await f.request(s.id));assert.notEqual(r3.body.operation?.status,'complete');assert.equal(f.wires.length,2);
 assert.equal((await f.api('status')).body.budget.remaining,0);
});
test('unknown God dispatch consumes one slot and repeated operation never calls provider again',async t=>{
 const f=await fixture(t,{unknown:true}),{body:s}=await f.api('sessions',{setup,mode:'real'}),input=await f.request(s.id);
 await f.api('sessions/'+s.id+'/send',input);await f.api('sessions/'+s.id+'/send',input);
 assert.equal(f.wires.length,1);assert.equal((await f.api('status')).body.budget.used,1);assert.equal((await f.h.god.read(s.id)).turns.length,0);
});
