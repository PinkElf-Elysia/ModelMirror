import {readFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {VOICE_LIBRARY_ID,fail} from '../plugins/catalog.mjs';
import {atomicJson} from './branch-archive.mjs';

const idPattern=/^voice_[a-f0-9]{32}$/;
const uuid=/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const exact=(value,keys)=>value&&Object.keys(value).sort().join(',')===keys.sort().join(',');

export function voiceLibraryService(call){
 async function request(path,data){
  const r=await call('/api/multimodal/voice-library/'+path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),signal:AbortSignal.timeout(75000)});
  if(!r.ok){let v;try{v=await r.json();}catch{/* No response body is surfaced. */}throw fail(/^voice_[a-z_]+$/.test(v?.detail?.code)?v.detail.code:'VOICE_SERVICE_UNAVAILABLE',r.status);}
  return r.json();
 }
 return {request,async profile(model){
  const [catalog,result]=await Promise.all([request('catalog'),request('assets')]);
  if(!catalog.available||!catalog.models.some(m=>m.id===model&&m.available))throw fail('VOICE_CONTROL_BLOCKED');
  const assets=result.assets.filter(a=>a.model===model&&a.state==='ready'&&a.usable!==false);
  const voices=assets.map(a=>'library:'+a.id);
  return {model,voices,formats:['wav'],managed:true,library:true,labels:Object.fromEntries(assets.map(a=>['library:'+a.id,a.name])),speeds:Object.fromEntries(voices.map(v=>[v,{min:1,max:1}]))};
 }};
}

export function createVoiceLibrary(directory,service){
 return async function handle({card,id,action,method,data,plugins,store,send}){
  if(!service||!plugins||!store)throw fail('VOICE_SERVICE_UNAVAILABLE',503);
  const grant=await plugins.sessionStatus(id,VOICE_LIBRARY_ID);
  if(!grant.installed)throw fail('PLUGIN_NOT_AUTHORIZED',403);
  await store.read(id);
  const folder=join(directory,card,id),path=join(folder,'bindings.json');
  const bindings=async()=>{try{return JSON.parse(await readFile(path,'utf8'));}catch(e){if(e.code==='ENOENT')return {revision:0,roles:[]};throw e;}};
  if(method==='GET'&&action==='state'){
   const [catalog,assets,bound]=await Promise.all([service.request('catalog'),service.request('assets'),bindings()]);
   return send(200,{...catalog,...assets,bindings:bound});
  }
  if(method==='GET'&&action.startsWith('operation-')&&uuid.test(action.slice(10)))return send(200,await service.request('operations/'+action.slice(10)));
  if(method!=='POST'||!grant.enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);
  const ticket=await plugins.invoke({sessionId:id,pluginId:VOICE_LIBRARY_ID,capability:'session.voice.manage',input:{}});
  if(action==='acknowledge'){
   if(!exact(data,['operationId','confirmed'])||!uuid.test(data.operationId)||data.confirmed!==true)throw fail('VOICE_INPUT_INVALID',422);
   return send(200,await plugins.commitResult(ticket,()=>service.request('operations/'+data.operationId+'/acknowledge',{confirmed:true})));
  }
  if(action==='bind'){
   if(!exact(data,['expectedRevision','role','assetId'])||!Number.isSafeInteger(data.expectedRevision)||typeof data.role!=='string'||!data.role.trim()||data.role.length>40||(data.assetId!==null&&!idPattern.test(data.assetId)))throw fail('VOICE_INPUT_INVALID',422);
   if(data.assetId){const {assets}=await service.request('assets');if(!assets.some(a=>a.id===data.assetId&&a.state==='ready'))throw fail('VOICE_NOT_READY');}
   return send(200,await plugins.commitResult(ticket,async()=>{
    const old=await bindings();if(old.revision!==data.expectedRevision)throw fail('VOICE_BINDING_CHANGED');
    const roles=old.roles.filter(r=>r.role!==data.role.trim());if(data.assetId)roles.push({role:data.role.trim(),assetId:data.assetId});
    if(roles.length>40)throw fail('VOICE_ROLE_LIMIT',422);
    const next={revision:old.revision+1,roles};await mkdir(folder,{recursive:true});await atomicJson(path,next);return next;
   }));
  }
  if(action==='create'){
   if(!exact(data,['operationId','model','name','audio','consent'])||!uuid.test(data.operationId)||typeof data.audio!=='string'||data.audio.length>14000000||data.consent!==true)throw fail('VOICE_INPUT_INVALID',422);
   return send(200,await plugins.commitResult(ticket,()=>service.request('create',data)));
  }
  if(['query','delete','rename'].includes(action)&&idPattern.test(data?.assetId)){
   const {assetId,...payload}=data;
   if(action==='rename'?!exact(payload,['name']):(!exact(payload,['operationId'])||!uuid.test(payload.operationId)))throw fail('VOICE_INPUT_INVALID',422);
   return send(200,await plugins.commitResult(ticket,()=>service.request(assetId+'/'+action,payload)));
  }
  throw fail('VOICE_INPUT_INVALID',422);
 };
}
