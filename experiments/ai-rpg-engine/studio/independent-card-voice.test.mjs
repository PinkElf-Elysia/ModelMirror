import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,mkdir,writeFile,readFile,access} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {installBlueFish} from '../blue-fish-card/integration.mjs';
import {installAiGirls} from '../ai-girls-card/integration.mjs';
import {createArchiveHost as blueHost} from '../blue-fish-card/session-host.mjs';
import {createArchiveHost as girlsHost} from '../ai-girls-card/session-host.mjs';
import {systemPrompt as blueSystem} from '../blue-fish-card/assembly.mjs';
import {systemPrompt as girlsSystem} from '../ai-girls-card/assembly.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
const assetId='voice_'+'1'.repeat(32),model='qwen-audio-3.1-tts-flash',voice='library:'+assetId;
const cards=[['blue-fish',installBlueFish,blueHost,blueSystem,'4d94f34b3ff884326bb691942b9e8171ab7bb3d0857b4bfbd8687212ee4a229c'],['ai-girls',installAiGirls,girlsHost,girlsSystem,'1abc1cd4ef41cf8a680ae8a736ef750001db27b2224be3fac7592c04b6311b3e']];
const delay=()=>new Promise(r=>setTimeout(r,10));
async function fixture(card,install,speech){
 const directory=await mkdtemp(join(tmpdir(),'independent-voice-'));let server,origin;
 const boot=async()=>{server=createServer((req,res)=>{res.writeHead(404);res.end('{}');});await new Promise(r=>server.listen(0,'127.0.0.1',r));origin='http://127.0.0.1:'+server.address().port;install(server,{directory,speech});};
 const stop=async()=>{const done=new Promise(r=>server.close(r));server.closeAllConnections();await done;const lock=join(directory,'archive-chat',card,'offline','plugins','owner.lock');for(let i=0;i<100;i++){try{await access(lock);}catch(e){if(e.code==='ENOENT')return;throw e;}await delay();}throw Error('owner did not close');};
 const api=(path,data,originOverride)=>fetch(origin+'/rpg-app/'+card+'/chat/'+path,data===undefined?{}:{method:'POST',headers:{origin:originOverride||origin,'Content-Type':'application/json'},body:JSON.stringify(data)});
 const json=async(path,data,status=200)=>{const r=await api(path,data),v=await r.json();assert.equal(r.status,status,JSON.stringify(v));return v;};
 const grant=async(id,pluginId,action='enable')=>{let c=await json('api/plugins'),p=c.plugins.find(p=>p.id===pluginId);assert(p);const fields=()=>({operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision});if(!p.installed){await json('api/plugins/'+pluginId+'/install',fields());c=await json('api/plugins');}const g=await json('api/sessions/'+id+'/plugins/'+pluginId);return json('api/sessions/'+id+'/plugins/'+pluginId+'/'+action,{...fields(),sessionId:id,expectedSessionRevision:g.sessionRevision,...(action==='enable'?{permissions:p.permissions}:{})});};
 await boot();return {directory,api,json,grant,stop,boot};
}
for(const [card,install,host,system,previous] of cards){
 test(card+': optional voice host stays absent when disabled',async()=>{const f=await fixture(card,install,null);try{const c=await f.json('api/plugins');assert(!c.plugins.some(p=>['rpg.speech','rpg.voice-library'].includes(p.id)));}finally{await f.stop();}});
 test(card+': common voice, guarded synthesis, audio/history recovery and session isolation use shared services',async()=>{
  let available=true,calls=0;const requests=[];const wav=Buffer.alloc(48);wav.write('RIFF');wav.writeUInt32LE(40,4);wav.write('WAVE',8);
  const profile=()=>({model,voices:[voice],formats:['wav'],managed:true,library:true,labels:{[voice]:'Synthetic voice'},speeds:{[voice]:{min:1,max:1}}});
  const speech={catalog:async()=>({status:'online',profiles:[]}),profile:async()=>{if(!available)throw fail('SPEECH_CONTROL_BLOCKED',409);return profile();},voices:{request:async(path,data)=>{requests.push({path,write:data!==undefined});assert.equal(data,undefined,'no cloud mutation in this fixture');if(path==='catalog')return {available,models:[{id:model,available}]};if(path==='assets')return {assets:[{id:assetId,name:'Synthetic voice',model,state:'ready',usable:available}]};throw Error('unexpected request');}},generate:async()=>{calls++;return {bytes:wav,mediaType:'audio/wav',requestId:'offline-receipt'};}};
  const f=await fixture(card,install,speech);
  try{
   await mkdir(join(f.directory,'speech'));await writeFile(join(f.directory,'speech','common-voices.json'),JSON.stringify({version:1,voices:[{id:'elysia',assetId,model}]}));
   const a=await f.json('api/sessions',{opening:'neutral A'},201),b=await f.json('api/sessions',{opening:'neutral B'},201),path='api/sessions/'+a.id;
   const before=await f.json(path);assert.equal((await f.api(path+'/speech/models')).status,403);
   await f.grant(a.id,'rpg.speech');let catalog=await f.json(path+'/speech/models');assert.equal(catalog.commonVoices[0].available,true);assert.equal(catalog.commonVoices[0].voice,voice);
   assert.deepEqual((await f.json(path+'/speech/profile?model='+model)).voices,[voice]);
   const request={operationId:randomUUID(),text:'A neutral sentence.',model,voice,turn:'voice-preview',format:'wav',speed:1};
   assert.equal((await f.api(path+'/speech/jobs',request,'https://untrusted.invalid')).status,403);assert.equal(calls,0);
   await f.json(path+'/speech/jobs',request,202);
   let job;for(let i=0;i<100;i++){job=(await f.json(path+'/speech/jobs'))[0];if(job?.state==='complete')break;await delay();}assert.equal(job.state,'complete');assert.equal(calls,1);
   const audio=await f.api(path+'/speech/'+request.operationId);assert.equal(audio.headers.get('content-type'),'audio/wav');assert.deepEqual(Buffer.from(await audio.arrayBuffer()),wav);
   await f.json(path+'/speech/jobs',request);assert.equal(calls,1,'same-ID recovery must not repeat synthesis');
   await f.grant(a.id,'rpg.voice-library');await f.json(path+'/voice-library/bind',{expectedRevision:0,role:'Narrator',assetId});assert.equal((await f.json(path+'/voice-library/state')).bindings.roles[0].assetId,assetId);
   await f.grant(b.id,'rpg.speech');await f.grant(b.id,'rpg.voice-library');assert.deepEqual(await f.json('api/sessions/'+b.id+'/speech/jobs'),[]);assert.deepEqual((await f.json('api/sessions/'+b.id+'/voice-library/state')).bindings.roles,[]);
   available=false;assert.equal((await f.json(path+'/speech/models')).commonVoices[0].available,false);assert.equal((await f.api(path+'/speech/jobs',{...request,operationId:randomUUID()})).status,409);assert.equal(calls,1);
   await f.stop();await f.boot();assert.equal((await f.json(path+'/speech/jobs'))[0].state,'complete');assert.equal((await f.json(path+'/voice-library/state')).bindings.roles[0].assetId,assetId);assert.deepEqual(await f.json(path),before);
   await f.grant(a.id,'rpg.speech','disable');assert.equal((await f.api(path+'/speech/jobs',{...request,operationId:randomUUID()})).status,403);assert.equal((await f.api(path+'/speech/'+request.operationId)).status,200);
   const html=await f.api('');assert.match(html.headers.get('content-security-policy'),/media-src 'self' blob:/);assert(requests.every(r=>!r.write));assert.equal(calls,1);
  }finally{await f.stop();}
 });
 test(card+': exact pre-voice saves remain compatible; changed prompt/model/transport/setup stay rejected',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'pre-voice-compat-'));const open=()=>host(directory,null,{cardId:card,name:card,system:system(),pluginFeatures:{speech:true}});let h=await open();
  try{const s=await h.store.create({opening:'neutral'});s.runtime.descriptor.adapterHash=previous;s.runtime.hash=sha(canonical(s.runtime.descriptor));await h.store.write(s);const file=join(directory,s.id+'.json'),before=await readFile(file);await h.plugins.close();h=await open();assert.equal(h.store.status(await h.store.read(s.id)).compatible,true);assert.deepEqual(await readFile(file),before);
   for(const mutate of [x=>x.characterText+='changed',x=>x.runtime.descriptor.systemHash='0'.repeat(64),x=>x.runtime.descriptor.model.model='other',x=>x.runtime.descriptor.adapterHash='0'.repeat(64),x=>x.runtime.descriptor.transportArtifacts['unknown']='changed']){const x=structuredClone(s);mutate(x);x.runtime.hash=sha(canonical(x.runtime.descriptor));assert.equal(h.store.status(x).compatible,false);}
  }finally{await h.plugins.close();}
 });
}
