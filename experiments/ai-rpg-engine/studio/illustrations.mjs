import {mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {dirname,join} from 'node:path';
import {canonical,sha,fail,ILLUSTRATIONS_ID} from '../plugins/catalog.mjs';
import {atomicJson} from './branch-archive.mjs';
import {publishVersion} from './session-lifecycle.mjs';

const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const media=new Set(['image/png','image/jpeg','image/webp','image/svg+xml']);
const options=['aspect_ratio','resolution','quality','output_format','background'];
const maxBytes=25*1024*1024;
export function imageService(baseURL,fetcher=fetch){
  const origin=new URL(baseURL);
  if(origin.protocol!=='http:'||!['localhost','127.0.0.1','[::1]','server'].includes(origin.hostname)||
     origin.username||origin.password||origin.pathname!=='/'||origin.search||origin.hash)throw Error('INVALID_IMAGE_SERVICE');
  const call=(path,init)=>fetcher(new URL('/api/multimodal/image/'+path,origin),{redirect:'error',...init});
  return {
    async catalog(){
      const r=await call('models',{signal:AbortSignal.timeout(20000)});
      if(!r.ok)throw fail('IMAGE_CATALOG_UNAVAILABLE',503);
      const v=await r.json();
      return {status:v.status,profiles:v.profiles.filter(p=>p.invocable&&p.operation==='generate_image'&&p.interaction_status==='ready'&&
          !(p.supported_parameters?.input_references?.min>0)&&!(p.supported_parameters?.n?.min>1))
        .map(({model_id,display_name,pricing,supported_parameters})=>({model_id,display_name,pricing,
          options:Object.fromEntries(options.filter(k=>supported_parameters?.[k]?.type==='enum'&&supported_parameters[k].values.length).map(k=>[k,supported_parameters[k].values]))}))};
    },
    async generate(job){
      const form=new FormData();
      for(const [k,v] of Object.entries({model_id:job.model,prompt:job.prompt,n:'1',...job.options}))form.set(k,v);
      const r=await call('generations',{method:'POST',headers:{'Idempotency-Key':job.id},body:form,signal:AbortSignal.timeout(240000)});
      if(!r.ok){throw fail([400,422].includes(r.status)?'IMAGE_REQUEST_REJECTED':'IMAGE_OUTCOME_UNKNOWN');}
      return r.json();
    }
  };
}

export function createIllustrations(directory,service,writer=null){
  const active=new Set();
  return async function handle({card,id,action,method,data,plugins,store,res,send}){
    if(!plugins||!store||!service)throw fail('IMAGE_HOST_UNAVAILABLE',503);
    const grant=await plugins.sessionStatus(id,ILLUSTRATIONS_ID);
    if(!grant.enabled)throw fail('PLUGIN_NOT_AUTHORIZED',403);
    const folder=join(directory,card,id);
    const readJob=async key=>JSON.parse(await readFile(join(folder,key,'job.json'),'utf8'));
    async function list(){
      let keys;try{keys=await readdir(folder);}catch(e){if(e.code==='ENOENT')return [];throw e;}
      const jobs=[];
      for(const key of keys.filter(k=>uuid.test(k))){
        let j;try{j=await readJob(key);}catch(e){if(e.code==='ENOENT')continue;throw e;}
        if(j.state==='pending'&&!active.has(join(folder,key)))j.state='unknown';
        jobs.push(j);
      }
      return jobs.sort((a,b)=>a.created.localeCompare(b.created));
    }
    if(method==='GET'&&action==='models')return send(200,{...await service.catalog(),writer:writer?await writer.status(card):null});
    if(method==='GET'&&action==='jobs')return send(200,await list());
    if(method==='GET'&&uuid.test(action)){
      const j=await readJob(action);
      if(j.state!=='complete'||!media.has(j.mediaType))throw fail('IMAGE_NOT_READY',404);
      const bytes=await readFile(join(folder,action,'asset'));
      if(bytes.length!==j.bytes||sha(bytes)!==j.assetHash)throw fail('IMAGE_ASSET_DAMAGED',500);
      res.writeHead(200,{'Content-Type':j.mediaType,'Content-Length':bytes.length,'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'"});return res.end(bytes);
    }
    if(method==='POST'&&action==='rewrite'){
      if(!writer)throw fail('REWRITE_DISABLED',409);
      const {turn,operationId,source,focus}=data;
      if(!uuid.test(operationId)||typeof turn!=='string'||!turn||turn.length>128||
         !['回合剧情','关键画面特写','选区'].includes(focus)||typeof source!=='string'||!source.trim()||
         [...source].length>16000||Object.keys(data).sort().join(',')!=='focus,operationId,source,turn')throw fail('REWRITE_INPUT_INVALID',400);
      if(!(await store.read(id)).turns.some(t=>t.requestId===turn))throw fail('IMAGE_TURN_INVALID',400);
      const ticket=await plugins.invoke({sessionId:id,pluginId:ILLUSTRATIONS_ID,capability:'session.illustration.rewrite',input:{}});
      const lock=join(folder,'rewrite');
      if(active.has(lock))throw fail('REWRITE_BUSY');
      active.add(lock);
      try{
        const prompt=await writer.rewrite(card,{sessionId:id,requestId:operationId,source:source.trim(),focus,
          beforeDispatch:start=>plugins.commitResult(ticket,()=>({response:start()}))});
        return send(200,{prompt});
      }finally{active.delete(lock);}
    }
    if(method!=='POST'||action!=='jobs')throw fail('NOT_FOUND',404);
    const {operationId:key,prompt,model,turn,options:params}=data;
    if(!uuid.test(key)||typeof prompt!=='string'||!prompt.trim()||[...prompt].length>4000||typeof model!=='string'||model.length>256||
       !(turn===null||typeof turn==='string'&&turn.length<=128)||!params||typeof params!=='object'||Array.isArray(params)||
       Object.keys(data).sort().join(',')!=='model,operationId,options,prompt,turn')throw fail('IMAGE_INPUT_INVALID',400);
    const fingerprint=sha(canonical(data)),path=join(folder,key);
    async function existing(){
      const j=await readJob(key);if(j.fingerprint!==fingerprint)throw fail('IMAGE_OPERATION_CONFLICT');
      return {...j,state:j.state==='pending'&&!active.has(path)?'unknown':j.state};
    }
    try{return send(200,await existing());}catch(e){if(e.code!=='ENOENT')throw e;}
    const ticket=await plugins.invoke({sessionId:id,pluginId:ILLUSTRATIONS_ID,capability:'session.illustration.generate',input:{}});
    const session=await store.read(id);
    const anchor=turn===null?null:session.turns.find(t=>t.requestId===turn);
    if(turn!==null&&!anchor)throw fail('IMAGE_TURN_INVALID',400);
    const profile=(await service.catalog()).profiles.find(p=>p.model_id===model);
    if(!profile||Object.entries(params).some(([k,v])=>!options.includes(k)||typeof v!=='string'||!profile.options[k]?.includes(v)))throw fail('IMAGE_MODEL_OR_OPTIONS_CHANGED',409);
    const job={id:key,state:'pending',created:new Date().toISOString(),turn,turnHash:anchor?sha(anchor.raw):null,prompt,model,options:params,fingerprint};
    async function finish(){
      try{
        const result=await service.generate(job),item=result.images?.[0];
        if(result.requested_model!==model||!result.request_id||result.images?.length!==1||!media.has(item?.media_type)||!Number.isSafeInteger(item.output_bytes)||item.output_bytes<1||item.output_bytes>maxBytes)throw Error('IMAGE_RESULT_INVALID');
        const prefix='data:'+item.media_type+';base64,';
        if(typeof item.data_url!=='string'||!item.data_url.startsWith(prefix)||item.data_url.length-prefix.length!==4*Math.ceil(item.output_bytes/3))throw Error('IMAGE_RESULT_INVALID');
        const bytes=Buffer.from(item.data_url.slice(prefix.length),'base64');
        if(bytes.length!==item.output_bytes)throw Error('IMAGE_RESULT_INVALID');
        await writeFile(join(path,'asset'),bytes,{flag:'wx',mode:0o600});
        Object.assign(job,{state:'complete',mediaType:item.media_type,bytes:bytes.length,assetHash:sha(bytes),actualModel:result.actual_model,requestId:result.request_id,costUsd:result.usage?.cost_usd??null,costKind:result.usage?.cost_kind??'unavailable'});
      }catch(e){job.state=e.code==='IMAGE_REQUEST_REJECTED'?'failed':'unknown';job.error=job.state==='failed'?'IMAGE_REQUEST_REJECTED':'IMAGE_OUTCOME_UNKNOWN';}
      // Retry only publication of the same durable record on transient file locks.
      // Keep the job active until publication settles; never repeat generate().
      finally{try{await atomicJson(join(path,'job.json'),job,publishVersion);}finally{active.delete(path);}}
    }
    const result=await plugins.commitResult(ticket,async()=>{
      if(active.has(path))return existing();
      if([...active].some(p=>dirname(p)===folder))throw fail('IMAGE_BUSY');
      await mkdir(folder,{recursive:true});
      try{await mkdir(path);}catch(e){if(e.code==='EEXIST')return existing();throw e;}
      await atomicJson(join(path,'job.json'),job,publishVersion);active.add(path);
      void finish().catch(()=>{});return {...job};
    });
    return send(202,result);
  };
}
