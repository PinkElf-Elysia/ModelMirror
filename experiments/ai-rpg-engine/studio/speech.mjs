import {mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha,fail,SPEECH_ID,VOICE_LIBRARY_ID} from '../plugins/catalog.mjs';
import {voiceLibraryService} from './voice-library.mjs';
import {atomicJson} from './branch-archive.mjs';

const uuid=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const maxBytes=25*1024*1024;
const formats={mp3:'audio/mpeg',wav:'audio/wav'};
export async function persistSpeechJob(path,job,writer=atomicJson){
 // Windows readers can briefly hold the destination during rename. Retry only
 // local persistence, never the provider request or audio generation.
 for(let attempt=0;;attempt++)try{return await writer(path,job);}catch(e){
  if(attempt>=3||!['EPERM','EACCES','EBUSY'].includes(e.code))throw e;
  await new Promise(resolve=>setTimeout(resolve,50*(attempt+1)));
 }
}
export function speedRange(model,voice){
 if(voice==='__prompt__'||['heygen/heygen-voice-1','heygen/heygen-voice-1-turbo'].includes(model))return {min:1,max:1};
 if(model.startsWith('elevenlabs/'))return ['elevenlabs/eleven-multilingual-v2','elevenlabs/eleven-flash-v2','elevenlabs/eleven-flash-v2.5'].includes(model)?{min:.7,max:1.2}:{min:1,max:1};
 return {min:.5,max:2};
}
export function speechService(baseURL,fetcher=fetch){
 const origin=new URL(baseURL);
 if(origin.protocol!=='http:'||!['localhost','127.0.0.1','[::1]','server'].includes(origin.hostname)||origin.username||origin.password||origin.pathname!=='/'||origin.search||origin.hash)throw Error('INVALID_SPEECH_SERVICE');
 const call=(path,init={})=>fetcher(new URL(path,origin),{redirect:'error',signal:AbortSignal.timeout(20000),...init});
 const voiceLibrary=voiceLibraryService(call);
 async function catalog(includeLibrary=false){
  const r=await call('/api/multimodal/audio/models');if(!r.ok)throw fail('SPEECH_CATALOG_UNAVAILABLE',503);
  const v=await r.json();if(!Array.isArray(v.profiles))throw fail('SPEECH_CATALOG_UNAVAILABLE',503);
  let library=[];if(includeLibrary)try{const c=await voiceLibrary.request('catalog');library=(c.models||[]).filter(m=>m.available).map(m=>({model_id:m.id,display_name:m.name+' · 我的音色',invocable:true,interaction_status:'ready',voices:[],output_formats:['wav']}));}catch{/* Preset TTS remains usable without the optional library. */}
  return {status:v.status,profiles:[...v.profiles.filter(p=>p.chat_modes?.includes('synthesize_speech')).map(p=>({model_id:p.model_id,display_name:p.display_name,invocable:p.invocable,interaction_status:p.interaction_status,voices:p.voices,output_formats:p.output_formats})),...library]};
 }
 async function profile(model){
  if(/^(qwen-audio-3\.[01]-tts-(plus|flash)|cosyvoice-v3(?:\.5)?-(plus|flash))$/.test(model))return voiceLibrary.profile(model);
  const params=new URLSearchParams({entry_id:'multimodal_speech',model_id:model,execution_shape:'audio_speech'});
  const r=await call('/api/models/provider-workload-control?'+params);if(!r.ok)throw fail('SPEECH_CONTROL_UNAVAILABLE',503);
  const control=await r.json();
  let voices,outputFormats,managed=false;
  if(control.feature_enabled===false||control.status==='legacy'){
   const p=(await catalog()).profiles.find(p=>p.model_id===model&&p.invocable&&p.interaction_status==='ready');
   if(!p)throw fail('SPEECH_MODEL_UNAVAILABLE',409);
   voices=p.voices;outputFormats=p.output_formats;
  }else if(control.available===true&&control.status==='managed_required'){
   managed=true;voices=[control.certified_voice];outputFormats=[control.certified_response_format];
  }else throw fail('SPEECH_CONTROL_BLOCKED',409);
  voices=(voices||[]).filter(v=>typeof v==='string'&&v.trim()&&v.length<=128);
  outputFormats=(outputFormats||[]).filter(f=>Object.hasOwn(formats,f));
  if(!voices.length||!outputFormats.length)throw fail('SPEECH_PROFILE_UNAVAILABLE',409);
  return {model,voices,formats:outputFormats,managed,speeds:Object.fromEntries(voices.map(v=>[v,speedRange(model,v)]))};
 }
 return {catalog,profile,voices:voiceLibrary,async generate(job){
  const assetId=job.voice.startsWith('library:')?job.voice.slice(8):null;
  const r=await call('/api/multimodal/speech',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':job.id},body:JSON.stringify({model_id:job.model,input:job.text,voice:job.voice,response_format:job.format,speed:job.speed,...(assetId?{voice_asset_id:assetId}:{})}),signal:AbortSignal.timeout(240000)});
  // Only explicit pre-dispatch validation errors are terminal. Transport/5xx remain unknown.
  if(!r.ok)throw fail([400,401,403,404,422].includes(r.status)?'SPEECH_REQUEST_REJECTED':'SPEECH_OUTCOME_UNKNOWN');
  const type=(r.headers.get('content-type')||'').split(';')[0];
  if(type!==formats[job.format]||Number(r.headers.get('content-length'))>maxBytes){await r.body?.cancel();throw fail('SPEECH_OUTCOME_UNKNOWN');}
  const chunks=[];let size=0;
  for await(const chunk of r.body){size+=chunk.length;if(size>maxBytes)throw fail('SPEECH_OUTCOME_UNKNOWN');chunks.push(chunk);}
  const bytes=Buffer.concat(chunks);
  const valid=job.format==='wav'?bytes.length>=44&&bytes.toString('ascii',0,4)==='RIFF'&&bytes.toString('ascii',8,12)==='WAVE':bytes.length>=3&&(bytes.toString('ascii',0,3)==='ID3'||bytes[0]===255&&(bytes[1]&224)===224);
  if(!valid)throw fail('SPEECH_OUTCOME_UNKNOWN');
  return {bytes,mediaType:type,requestId:r.headers.get('x-modelmirror-request-id')||null,actualModel:r.headers.get('x-modelmirror-actual-model')||job.model,costKind:['actual','estimated'].includes(r.headers.get('x-modelmirror-cost-kind'))?r.headers.get('x-modelmirror-cost-kind'):'unavailable'};
 }};
}

export function createSpeech(directory,service){
 const active=new Map();
 return async function handle({card,id,action,method,data,query=new URLSearchParams(),plugins,store,res,send}){
  if(!plugins||!store||!service)throw fail('SPEECH_HOST_UNAVAILABLE',503);
  const grant=await plugins.sessionStatus(id,SPEECH_ID);
  // Reading existing audio after disabling is allowed only for its existing installed grant.
  if(!grant.installed)throw fail('PLUGIN_NOT_AUTHORIZED',403);
  const folder=join(directory,card,id);
  const readJob=async key=>JSON.parse(await readFile(join(folder,key,'job.json'),'utf8'));
  const projected=j=>({...j,state:j.state==='pending'&&active.get(folder)!==j.id?'unknown':j.state});
  async function list(){
   let keys;try{keys=await readdir(folder);}catch(e){if(e.code==='ENOENT')return [];throw e;}
   const jobs=[];for(const key of keys.filter(k=>uuid.test(k))){try{jobs.push(projected(await readJob(key)));}catch(e){if(e.code!=='ENOENT')throw e;}}
   return jobs.sort((a,b)=>b.created.localeCompare(a.created));
  }
  if(method==='GET'&&action==='jobs')return send(200,await list());
  if(method==='GET'&&uuid.test(action)){
   const j=await readJob(action);if(j.state!=='complete'||!Object.values(formats).includes(j.mediaType))throw fail('SPEECH_NOT_READY',404);
   const bytes=await readFile(join(folder,action,'asset'));if(bytes.length!==j.bytes||sha(bytes)!==j.assetHash)throw fail('SPEECH_ASSET_DAMAGED',500);
   res.writeHead(200,{'Content-Type':j.mediaType,'Content-Length':bytes.length,'Content-Disposition':'inline; filename="speech.'+j.format+'"','Content-Security-Policy':"default-src 'none'; sandbox"});return res.end(bytes);
  }
  if(!grant.enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);
  if(method==='GET'&&action==='models')return send(200,await service.catalog(!!(await plugins.sessionStatus(id,VOICE_LIBRARY_ID)).enabled));
  if(method==='GET'&&action==='profile'){
   const model=query.get('model');if(!model||model.length>256)throw fail('SPEECH_INPUT_INVALID',400);
   return send(200,await service.profile(model));
  }
  if(method!=='POST'||action!=='jobs')throw fail('NOT_FOUND',404);
  const {operationId:key,text,model,turn,voice,format,speed}=data;
  if(!uuid.test(key)||typeof text!=='string'||!text.trim()||[...text].length>4000||typeof model!=='string'||!model||model.length>256||typeof turn!=='string'||!turn||turn.length>128||typeof voice!=='string'||!voice||voice.length>128||!Object.hasOwn(formats,format)||!Number.isFinite(speed)||Object.keys(data).sort().join(',')!=='format,model,operationId,speed,text,turn,voice')throw fail('SPEECH_INPUT_INVALID',400);
  const fingerprint=sha(canonical(data)),path=join(folder,key);
  async function existing(){const j=await readJob(key);if(j.fingerprint!==fingerprint)throw fail('SPEECH_OPERATION_CONFLICT');return projected(j);}
  try{return send(200,await existing());}catch(e){if(e.code!=='ENOENT')throw e;}
  const ticket=await plugins.invoke({sessionId:id,pluginId:SPEECH_ID,capability:'session.speech.generate',input:{}});
  const libraryVoice=voice.startsWith('library:');
  if(libraryVoice&&!(await plugins.sessionStatus(id,VOICE_LIBRARY_ID)).enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);
  const session=await store.read(id),anchor=session.turns.find(t=>t.requestId===turn);
  if(!anchor&&!(libraryVoice&&turn==='voice-preview'))throw fail('SPEECH_TURN_INVALID',400);
  const p=await service.profile(model),range=p.speeds[voice];
  if(!p.voices.includes(voice)||!p.formats.includes(format)||!range||speed<range.min||speed>range.max)throw fail('SPEECH_MODEL_OR_OPTIONS_CHANGED',409);
  const job={id:key,state:'pending',created:new Date().toISOString(),turn,turnHash:anchor?sha(anchor.raw):null,text,model,voice,voiceName:p.labels?.[voice]||null,format,speed,fingerprint};
  async function finish(){
   try{
    const result=await service.generate(job);await writeFile(join(path,'asset'),result.bytes,{flag:'wx',mode:0o600});
    const {bytes,...receipt}=result;Object.assign(job,{...receipt,state:'complete',bytes:bytes.length,assetHash:sha(bytes)});
   }catch(e){job.state=e.code==='SPEECH_REQUEST_REJECTED'?'failed':'unknown';job.error=job.state==='failed'?'SPEECH_REQUEST_REJECTED':'SPEECH_OUTCOME_UNKNOWN';}
   finally{try{await persistSpeechJob(join(path,'job.json'),job);}finally{active.delete(folder);}}
  }
  const result=await plugins.commitResult(ticket,async()=>{
   if(libraryVoice&&!(await plugins.sessionStatus(id,VOICE_LIBRARY_ID)).enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);
   if(active.has(folder))throw fail('SPEECH_BUSY');
   await mkdir(folder,{recursive:true});try{await mkdir(path);}catch(e){if(e.code==='EEXIST')return existing();throw e;}
   await atomicJson(join(path,'job.json'),job);active.set(folder,key);void finish().catch(()=>{});return {...job};
  });
  return send(202,result);
 };
}
