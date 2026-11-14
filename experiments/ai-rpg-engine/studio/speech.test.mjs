import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {speechService,speedRange} from './speech.mjs';

const wav=Buffer.alloc(48);wav.write('RIFF');wav.writeUInt32LE(40,4);wav.write('WAVE',8);
const profile={model_id:'fixture/tts',display_name:'测试音色',invocable:true,interaction_status:'ready',chat_modes:['synthesize_speech'],voices:['voice-a','voice-b'],output_formats:['wav']};

test('speech validates control and voice/speed contracts before dispatch',async()=>{
 assert.throws(()=>speechService('https://example.com'),/INVALID/);
 assert.deepEqual(speedRange('elevenlabs/eleven-v4','a'),{min:1,max:1});
 assert.deepEqual(speedRange('elevenlabs/eleven-flash-v2','a'),{min:.7,max:1.2});
 assert.deepEqual(speedRange('any','__prompt__'),{min:1,max:1});
 let control={feature_enabled:false,status:'legacy'},calls=0;
 const service=speechService('http://server:8000',async url=>{calls++;return Response.json(url.pathname.includes('provider-workload')?control:{profiles:[profile]});});
 assert.deepEqual((await service.profile(profile.model_id)).voices,profile.voices);
 control={feature_enabled:true,status:'managed_required',available:true,certified_voice:'certified',certified_response_format:'mp3'};
 assert.deepEqual((await service.profile(profile.model_id)).voices,['certified']);
 control={...control,certified_voice:null};await assert.rejects(service.profile(profile.model_id),/SPEECH_PROFILE/);
 control={...control,status:'degraded_required',available:false};await assert.rejects(service.profile(profile.model_id),/SPEECH_CONTROL_BLOCKED/);
 assert.equal(calls,5);
});

test('manual speech: grants, durable audio, same-ID recovery, unknown outcomes and unchanged story',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'rpg-speech-'));let calls=0,failCall=false,release;
 const gate=new Promise(r=>{release=r;});
 const service=speechService('http://127.0.0.1:8000',async(url,options)=>{
  if(url.pathname.includes('provider-workload'))return Response.json({feature_enabled:false,status:'legacy'});
  if(url.pathname.endsWith('/models'))return Response.json({status:'online',profiles:[profile]});
  calls++;assert.equal(url.pathname,'/api/multimodal/speech');const body=JSON.parse(options.body);
  assert.equal(body.input,'黄昏里，钟楼响了。');assert.equal(body.voice,'voice-a');assert.equal(body.speed,1);assert(options.headers['Idempotency-Key']);assert(!body.messages);
  if(failCall)throw Error('transport lost');await gate;
  return new Response(wav,{headers:{'content-type':'audio/wav','x-modelmirror-request-id':'receipt-test','x-modelmirror-actual-model':profile.model_id}});
 });
 let host,origin;
 const boot=async()=>{host=await start({directory,port:0,speech:service});origin='http://127.0.0.1:'+host.server.address().port;};
 const stop=async()=>{await new Promise(r=>host.server.close(r));await host.plugins.close();};
 await boot();const api=async(path,body)=>fetch(origin+'/rpg-app/'+path,body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
 try{
  const s=await (await api('earth/api/sessions',{characterText:'姓名：测试',mode:'offline',world:'表世界',params:{}})).json();
  const path='earth/api/sessions/'+s.id,base=path+'/speech/';const turn=randomUUID();
  await api(path+'/send',{requestId:turn,input:'看看钟楼',revision:s.revision});const parent=await host.earth.read(s.id);
  const request={operationId:randomUUID(),turn,text:'黄昏里，钟楼响了。',model:profile.model_id,voice:'voice-a',format:'wav',speed:1};
  assert.equal((await api(base+'jobs',request)).status,403);
  const catalog=await (await api('api/plugins')).json(),p=catalog.plugins.find(p=>p.id==='rpg.speech');assert(p);
  const binding={pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256};
  const rev=await (await api('api/plugins/'+p.id+'/install',{...binding,operationId:randomUUID(),expectedRegistryRevision:catalog.revision})).json();
  assert((await api(path+'/plugins/'+p.id+'/enable',{...binding,operationId:randomUUID(),expectedRegistryRevision:rev.revision,sessionId:s.id,expectedSessionRevision:parent.revision,permissions:p.permissions})).ok);
  assert.equal((await api(base+'models')).status,200);
  assert.equal((await api(base+'profile?model='+encodeURIComponent(profile.model_id))).status,200);
  assert.equal((await api(base+'jobs',{...request,voice:'invented'})).status,409);
  assert.equal((await api(base+'jobs',{...request,turn:randomUUID()})).status,400);
  assert.equal((await api(base+'jobs',{...request,speed:3})).status,409);
  assert.equal((await api(base+'jobs',{...request,text:'x'.repeat(4001)})).status,400);assert.equal(calls,0);
  const dispatched=await api(base+'jobs',request);assert.equal(dispatched.status,202,await dispatched.text());
  assert.equal((await api(base+'jobs',request)).status,200);
  assert.equal((await api(base+'jobs',{...request,text:'changed'})).status,409);
  assert.equal((await api(base+'jobs',{...request,operationId:randomUUID()})).status,409);assert.equal(calls,1);release();
  async function settled(key){for(let i=0;i<100;i++){const j=(await (await api(base+'jobs')).json()).find(r=>r.id===key);if(j&&j.state!=='pending')return j;await new Promise(r=>setTimeout(r,10));}throw Error('unfinished fixture');}
  const job=await settled(request.operationId);assert.equal(job.state,'complete');assert.equal(job.requestId,'receipt-test');
  assert.deepEqual(Buffer.from(await (await api(base+job.id)).arrayBuffer()),wav);
  failCall=true;const unknown={...request,operationId:randomUUID()};await api(base+'jobs',unknown);assert.equal((await settled(unknown.operationId)).state,'unknown');
  await stop();await boot();assert.equal((await (await api(base+'jobs',unknown)).json()).state,'unknown');assert.equal(calls,2);
  const record=join(directory,'speech','earth',s.id,unknown.operationId,'job.json');const saved=JSON.parse(await readFile(record));await writeFile(record,JSON.stringify({...saved,state:'pending'}));
  assert.equal((await settled(unknown.operationId)).state,'unknown');assert.equal(calls,2);
  assert.deepEqual(await host.earth.read(s.id),parent);
  const registry=await (await api('api/plugins')).json();
  assert.equal((await api(path+'/plugins/'+p.id+'/disable',{...binding,operationId:randomUUID(),expectedRegistryRevision:registry.revision,sessionId:s.id,expectedSessionRevision:parent.revision})).status,200);
  assert.equal((await api(base+'jobs',{...request,operationId:randomUUID()})).status,403);
  assert.deepEqual(Buffer.from(await (await api(base+job.id)).arrayBuffer()),wav);
  await writeFile(join(directory,'speech','earth',s.id,job.id,'asset'),Buffer.from('damaged'));assert.equal((await api(base+job.id)).status,500);
  const installed=await (await api('api/plugins')).json();
  assert.equal((await api('api/plugins/'+p.id+'/uninstall',{...binding,operationId:randomUUID(),expectedRegistryRevision:installed.revision})).status,200);
  assert.equal((await api(base+'jobs')).status,403);
  assert.equal((await api(base+job.id)).status,403);
  assert.equal(JSON.parse(await readFile(record)).state,'pending');assert.equal(calls,2);

 }finally{release();await stop();}
});
