import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {speechService,persistSpeechJob} from './speech.mjs';

test('temporary local file sharing contention retries persistence only',async()=>{
 let attempts=0;const job={state:'complete'};
 await persistSpeechJob('fixture',job,async(path,value)=>{assert.equal(value,job);if(++attempts<3)throw Object.assign(Error('sharing'),{code:'EPERM'});});
 assert.equal(attempts,3);
 attempts=0;await assert.rejects(persistSpeechJob('fixture',job,async()=>{attempts++;throw Object.assign(Error('disk full'),{code:'ENOSPC'});}));assert.equal(attempts,1);
});

test('voice plugin grants, role persistence, TTS preview/history, delete and unchanged story',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'voice-library-'));let assets=[],calls=0;
 const model='qwen-audio-3.1-tts-plus',assetId='voice_'+'a'.repeat(32);
 const wav=Buffer.alloc(48);wav.write('RIFF');wav.writeUInt32LE(40,4);wav.write('WAVE',8);
 const service=speechService('http://server:8000',async(url,options)=>{
  const path=url.pathname;
  if(path.endsWith('/audio/models'))return Response.json({profiles:[]});
  if(path.endsWith('/voice-library/catalog'))return Response.json({available:true,remaining:10,models:[{id:model,name:model,available:true}]});
  if(path.endsWith('/voice-library/assets'))return Response.json({assets});
  if(path.endsWith('/voice-library/create')){calls++;assets=[{id:assetId,name:'旁白样例',model,state:'processing',version:1,sample:{seconds:12},created:new Date().toISOString()}];return Response.json({asset:assets[0]});}
  if(path.endsWith('/query')){calls++;assets[0].state='ready';return Response.json({asset:assets[0]});}
  if(path.endsWith('/delete')){calls++;assets[0].state='deleted';return Response.json({asset:assets[0]});}
  if(path==='/api/multimodal/speech'){
   calls++;const body=JSON.parse(options.body);assert.equal(body.voice_asset_id,assetId);assert.equal(body.voice,'library:'+assetId);assert.equal(body.model_id,model);assert(!body.messages);
   return new Response(wav,{headers:{'content-type':'audio/wav'}});
  }
  throw Error('Unexpected fixture request '+path);
 });
 let host,origin;
 const boot=async()=>{host=await start({directory,port:0,speech:service});origin='http://127.0.0.1:'+host.server.address().port;};
 const stop=async()=>{await new Promise(r=>host.server.close(r));await host.plugins.close();};
 await boot();
 const api=(path,body)=>fetch(origin+'/rpg-app/'+path,body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 try{
  const session=await (await api('earth/api/sessions',{characterText:'姓名：测试',mode:'offline',world:'表世界',params:{}})).json();
  const prefix='earth/api/sessions/'+session.id,root=prefix+'/voice-library/';
  assert.match((await api(root+'state')).headers.get('content-security-policy'),/media-src 'self' blob:/);
  assert.equal((await api(root+'state')).status,403);
  async function enable(pluginId){
   const catalog=await (await api('api/plugins')).json(),p=catalog.plugins.find(p=>p.id===pluginId);assert(p);
   const binding={pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256};
   const install=await api('api/plugins/'+pluginId+'/install',{...binding,operationId:randomUUID(),expectedRegistryRevision:catalog.revision});assert.equal(install.status,200);const installed=await install.json();
   const current=await host.earth.read(session.id);
   const r=await api(prefix+'/plugins/'+pluginId+'/enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:installed.revision,sessionId:session.id,expectedSessionRevision:current.revision,permissions:p.permissions});assert.equal(r.status,200,await r.text());
   return binding;
  }
  const voiceBinding=await enable('rpg.voice-library');await enable('rpg.speech');
  const before=await host.earth.read(session.id);
  const created=await api(root+'create',{operationId:randomUUID(),model,name:'旁白样例',audio:'fixture',consent:true});assert.equal(created.status,200);
  assert.equal((await api(root+'bind',{expectedRevision:0,role:'旁白',assetId})).status,409);
  assert.equal((await api(root+'query',{operationId:randomUUID(),assetId})).status,200);
  assert.equal((await api(root+'bind',{expectedRevision:0,role:'旁白',assetId})).status,200);
  assert.equal((await api(root+'bind',{expectedRevision:0,role:'另一个角色',assetId})).status,409);
  const request={operationId:randomUUID(),turn:'voice-preview',text:'今天阳光很好。',model,voice:'library:'+assetId,format:'wav',speed:1};
  assert.deepEqual((await service.profile(model)).voices,['library:'+assetId]);
  const dispatched=await api(prefix+'/speech/jobs',request);assert.equal(dispatched.status,202,await dispatched.text());
  let job;
  for(let i=0;i<100;i++){job=(await (await api(prefix+'/speech/jobs')).json())[0];if(job.state==='complete')break;await new Promise(r=>setTimeout(r,10));}
  assert.equal(job.state,'complete');assert.equal(job.turn,'voice-preview');assert.equal(calls,3);
  assert.deepEqual(Buffer.from(await (await api(prefix+'/speech/'+job.id)).arrayBuffer()),wav);
  await stop();await boot();
  assert.equal((await (await api(root+'state')).json()).bindings.roles[0].assetId,assetId);
  assert.equal((await api(prefix+'/speech/jobs',request)).status,200);assert.equal(calls,3);
  assert.equal((await api(root+'delete',{operationId:randomUUID(),assetId})).status,200);
  assert.equal((await api(prefix+'/speech/jobs',{...request,operationId:randomUUID()})).status,409);
  assert.deepEqual(await host.earth.read(session.id),before);
  const catalog=await (await api('api/plugins')).json();
  assert.equal((await api(prefix+'/plugins/rpg.voice-library/disable',{...voiceBinding,operationId:randomUUID(),expectedRegistryRevision:catalog.revision,sessionId:session.id,expectedSessionRevision:before.revision})).status,200);
  assert.equal((await api(root+'state')).status,200);
  assert.equal((await api(root+'query',{operationId:randomUUID(),assetId})).status,403);
  assert.equal((await api(prefix+'/speech/'+job.id)).status,200);
 }finally{await stop();}
});
