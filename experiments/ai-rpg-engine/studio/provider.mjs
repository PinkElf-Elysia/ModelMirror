import {readFile,mkdir,writeFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
export const sha=x=>createHash('sha256').update(x).digest('hex');
export const models=JSON.parse(await readFile(new URL('./models.json',import.meta.url),'utf8'));
const fail=code=>Object.assign(Error(code),{status:409,code});
export async function createProvider({directory,key,enabled=false,limit=1,fetcher=fetch}){
 if(!Number.isInteger(limit)||limit<0||limit>100)throw Error('INVALID_BUDGET');
 await mkdir(directory,{recursive:true});
 for(const card of Object.keys(models))await mkdir(join(directory,card),{recursive:true});
 async function status(card){const used=(await readdir(join(directory,card))).filter(n=>/^slot-\d+$/.test(n)).length;return {enabled:enabled&&!!key,used,limit,remaining:Math.max(0,limit-used),model:models[card].model,parameters:models[card].parameters};}
 async function generate(card,{messages,params,signal,sessionId,requestId}){
  if(!models[card]||!enabled||!key)throw fail('PROVIDER_DISABLED');
  if(!Array.isArray(messages)||messages.some(m=>!['system','user','assistant'].includes(m.role)||typeof m.content!=='string')||JSON.stringify(messages).length>1000000)throw fail('INVALID_MESSAGES');
  if(signal?.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');
  const config=models[card],effective={...config.parameters};
  if(card==='earth'&&params){for(const k of ['temperature','top_p','max_tokens'])if(params[k]!==undefined)effective[k]=params[k];}
  if(!Number.isFinite(effective.temperature)||effective.temperature<0||effective.temperature>2||!Number.isInteger(effective.max_tokens)||effective.max_tokens<1||effective.max_tokens>32768||(effective.top_p!==undefined&&(!Number.isFinite(effective.top_p)||effective.top_p<0||effective.top_p>1)))throw fail('INVALID_PARAMETERS');
  const payload={model:config.model,messages,...effective,stream:true,provider:{only:[config.providerSlug],order:[config.providerSlug],allow_fallbacks:false,require_parameters:true},transforms:[],stream_options:{include_usage:true}};
  const wire=JSON.stringify(payload);let folder;
  for(let i=1;i<=limit;i++){try{folder=join(directory,card,'slot-'+i);await mkdir(folder);break;}catch(e){if(e.code!=='EEXIST')throw e;folder=null;}}
  if(!folder)throw fail('BUDGET_EXHAUSTED');
  const save=(name,value)=>writeFile(join(folder,name),typeof value==='string'?value:JSON.stringify(value,null,2)+'\n',{flag:'wx',mode:0o600});
  const receipt={card,sessionId,requestId,at:new Date().toISOString(),status:'reserved',requestHash:sha(wire),requestedModel:config.model,parameters:effective,retries:0};
  await save('reservation.json',receipt);await save('request.json',wire);
  let raw='',sse='',pending='',done=false,finish=null,actualModel=null,provider=null,usage=null,httpStatus=null,error=null;
  function event(block){const data=block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart()).join('\n');if(!data)return;if(done)throw Error('EVENT_AFTER_DONE');if(data==='[DONE]'){done=true;return;}const x=JSON.parse(data);if(x.error)throw Error('PROVIDER_ERROR');actualModel=x.model||actualModel;provider=x.provider||provider;usage=x.usage||usage;const c=x.choices?.[0];if(typeof c?.delta?.content==='string')raw+=c.delta.content;if(c?.finish_reason)finish=c.finish_reason;}
  try{
   const combined=signal?AbortSignal.any([signal,AbortSignal.timeout(240000)]):AbortSignal.timeout(240000);
   const response=await fetcher('https://openrouter.ai/api/v1/chat/completions',{method:'POST',redirect:'error',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:wire,signal:combined});
   httpStatus=response.status;if(!response.ok||!response.headers.get('content-type')?.includes('text/event-stream'))throw Error('UPSTREAM_HTTP');
   const decoder=new TextDecoder();for await(const chunk of response.body){sse+=decoder.decode(chunk,{stream:true});if(sse.length>16000000)throw Error('RESPONSE_TOO_LARGE');}
   sse+=decoder.decode();pending=sse.replaceAll('\r\n','\n');const blocks=pending.split('\n\n');if(blocks.pop().trim())throw Error('INCOMPLETE_EVENT');for(const block of blocks)event(block);
   if(!done||finish!=='stop'||!raw.trim())throw Error('INCOMPLETE_OUTPUT');
   if(![config.model,config.model+'-20260902'].includes(actualModel)||provider!==config.provider)throw Error('ROUTE_MISMATCH');
   if(signal?.aborted)throw Error('CANCELLED');
  }catch(e){error=signal?.aborted?'CANCELLED':(['INCOMPLETE_OUTPUT','ROUTE_MISMATCH','UPSTREAM_HTTP','RESPONSE_TOO_LARGE','PROVIDER_ERROR','INCOMPLETE_EVENT'].includes(e.message)?e.message:'TRANSPORT_FAILED_OR_UNKNOWN');}
  if(raw.includes(key)||sse.includes(key)){raw='';sse='';error='CREDENTIAL_ECHO_BLOCKED';}
  await save('response.txt',raw);await save('response.sse',sse);await save('result.json',{...receipt,status:error?'failed_or_unknown':'complete',rawHash:sha(raw),sseHash:sha(sse),httpStatus,actualModel,provider,usage,finish,done,error,manual:'pending'});
  if(error)throw fail(error);return raw;
 }
 return {generate,status};
}
