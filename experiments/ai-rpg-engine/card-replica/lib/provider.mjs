import {open,readFile,writeFile,rename,mkdir,unlink} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {hash,providerParameters,activeSystem} from './assembly.mjs';
export const ROUTE='http://127.0.0.1:18305',MODEL='gpt-5.6-luna';
export const supported=['temperature','top_p','max_tokens'];
export function wirePayload(messages,params){
 if(!Array.isArray(messages)||messages.length>80)throw Error('History exceeds route admission; nothing was truncated');
 if(messages[0]?.role!=='system'||messages[0]?.content!==activeSystem||messages.slice(1).some((x,i)=>x.role!==(i%2?'assistant':'user')||typeof x.content!=='string'))throw Error('Untrusted message assembly');
 const effective=providerParameters(params,supported);
 return {payload:{model_id:MODEL,messages, ...effective.parameters,require_managed_route:true,gateway:'default',tool_mode:'none',output_mode:'none',compression:{mode:'off'}},effective};
}
async function editLedger(path,fn){
 const lock=await open(path+'.lock','wx');
 try{
  const ledger=JSON.parse(await readFile(path,'utf8'));
  if(ledger.model!==MODEL||ledger.route!==ROUTE||ledger.limit!==4||ledger.used!==ledger.entries.length)throw Error('Ledger scope mismatch');
  const result=fn(ledger),temp=path+'.'+randomUUID()+'.tmp';
  await writeFile(temp,JSON.stringify(ledger,null,2)+'\n');await rename(temp,path);return result;
 }finally{await lock.close();await unlink(path+'.lock');}
}
export async function reserve(path,{kind,requestHash,freezeHash}){
 return editLedger(path,l=>{
  if(l.entries.length>=l.limit)throw Error('Dispatch allowance exhausted');
  if(l.entries.some(e=>e.status==='pending'||e.status==='unknown'||e.status==='failed'||(e.kind==='generation'&&!e.userReviewed)))throw Error('Previous dispatch needs human review; no retry');
  if(!['generation','certification'].includes(kind))throw Error('Invalid dispatch kind');
  const entry={slot:l.entries.length+1,id:randomUUID(),kind,status:'pending',requestHash,freezeHash,at:new Date().toISOString(),userReviewed:false};
  l.entries.push(entry);l.used=l.entries.length;l.remaining=l.limit-l.used;return entry;
 });
}
export async function settle(path,id,result){
 return editLedger(path,l=>{const e=l.entries.find(x=>x.id===id);if(!e||e.status!=='pending')throw Error('Reservation not pending');Object.assign(e,result,{completedAt:new Date().toISOString()});return e;});
}
export async function qualification(fetchImpl=fetch){
 const r=await fetchImpl(ROUTE+'/api/models/provider-chat-control?model_id='+MODEL+'&capability=chat_text',{redirect:'error',signal:AbortSignal.timeout(10000)});
 if(!r.ok)throw Error('Qualification read failed');const q=await r.json();
 if(q.model_id!==MODEL||q.capability!=='chat_text'||!q.available||q.reason_code!=='qualified')throw Error('Exact model route is not qualified');
 return q;
}
// Transport metadata never enters messages. Failure/partial text is retained separately.
export async function receive(payload,{fetchImpl=fetch,signal,timeoutMs=300000}={}){
 const timed=AbortSignal.timeout(timeoutMs),combined=signal?AbortSignal.any([timed,signal]):timed;
 let raw='',buffer='',receipt=null,finishReason=null,done=false,httpStatus=null,observedModel=null,usage=null;
 const decoder=new TextDecoder(),chunks=[];let bytes=0,error=null;
 function event(block){
  let name='message';const data=[];
  for(const line of block.split('\n')){if(line.startsWith('event:'))name=line.slice(6).trim();else if(line.startsWith('data:'))data.push(line.slice(5).replace(/^ /,''));}
  if(!data.length)return;const value=data.join('\n');
  if(done)throw Error('EVENT_AFTER_DONE');
  if(value==='[DONE]'){done=true;return;}
  const obj=JSON.parse(value);
  if(name==='route_receipt'){if(receipt)throw Error('DUPLICATE_RECEIPT');receipt=obj;return;}
  if(name!=='message'||receipt)throw Error('EVENT_ORDER');
  if(obj.error)throw Error('UPSTREAM_ERROR');
  if(obj.model){observedModel=obj.model;if(obj.model!==MODEL)throw Error('MODEL_MISMATCH');}
  if(obj.usage)usage=obj.usage;
  if(obj.choices?.length===0)return;
  if(!Array.isArray(obj.choices)||obj.choices.length!==1||finishReason)throw Error('CHOICE_ORDER');
  const c=obj.choices[0];
  if(c.delta?.tool_calls||c.delta?.function_call)throw Error('UNEXPECTED_TOOL');
  if(typeof c.delta?.content==='string')raw+=c.delta.content;
  if(c.delta?.refusal)throw Error('MODEL_REFUSAL');
  if(c.finish_reason)finishReason=c.finish_reason;
 }
 try{
  const r=await fetchImpl(ROUTE+'/api/chat',{method:'POST',headers:{'content-type':'application/json',accept:'text/event-stream'},body:JSON.stringify(payload),redirect:'error',signal:combined});
  httpStatus=r.status;
  if(!r.ok||!r.headers.get('content-type')?.includes('text/event-stream'))throw Error('HTTP_OR_TYPE');
  for await(const part of r.body){chunks.push(Buffer.from(part));bytes+=part.length;if(bytes>16*1024*1024)throw Error('STREAM_SIZE');buffer+=decoder.decode(part,{stream:true});buffer=buffer.replace(/\r\n/g,'\n');let index;while((index=buffer.indexOf('\n\n'))>=0){event(buffer.slice(0,index));buffer=buffer.slice(index+2);}}
  buffer+=decoder.decode();if(buffer.trim())throw Error('INCOMPLETE_EVENT');
  if(!done||finishReason!=='stop'||!raw.trim()||!receipt||receipt.actual_model!==MODEL||receipt.requested_model!==MODEL||receipt.fallback_attempts!==0||receipt.engine!=='newapi'||!['newapi_preferred','newapi_required_default'].includes(receipt.strategy)||!receipt.reason_codes?.includes('qualified'))throw Error('INCOMPLETE_OR_ROUTE_MISMATCH');
 }catch(e){error=combined.aborted?'CANCELLED_OR_TIMEOUT':(['EVENT_AFTER_DONE','DUPLICATE_RECEIPT','EVENT_ORDER','UPSTREAM_ERROR','MODEL_MISMATCH','CHOICE_ORDER','UNEXPECTED_TOOL','MODEL_REFUSAL','HTTP_OR_TYPE','STREAM_SIZE','INCOMPLETE_EVENT','INCOMPLETE_OR_ROUTE_MISMATCH'].includes(e.message)?e.message:'TRANSPORT_UNKNOWN');}
 return {status:error?'unknown':'complete',raw,receipt,finishReason,done,httpStatus,observedModel,usage,error,stream:Buffer.concat(chunks)};
}
export function createProvider({ledgerPath,evidenceDirectory,freeze,fetchImpl=fetch}){
 return async({messages,params,signal,sessionId,requestId})=>{
  if(signal?.aborted)throw Error('Cancelled before dispatch');
  // Freeze exact operator-selected messages and effective parameters; browser cannot replace them.
  const {payload,effective}=wirePayload(messages,params),request=JSON.stringify(payload);
  if(hash(request)!==freeze.requestHash)throw Error('Request differs from reviewed local freeze');
  for(const f of freeze.files)if(hash(await readFile(f.path))!==f.sha256)throw Error('Frozen source drift');
  const q=await qualification(fetchImpl);
  if(signal?.aborted)throw Error('Cancelled before dispatch');
  await mkdir(evidenceDirectory,{recursive:true});
  const slot=await reserve(ledgerPath,{kind:'generation',requestHash:hash(request),freezeHash:hash(JSON.stringify(freeze))});
  const dir=join(evidenceDirectory,'slot-'+slot.slot);await mkdir(dir,{recursive:false});
  await writeFile(join(dir,'request.json'),request);
  await writeFile(join(dir,'freeze.json'),JSON.stringify(freeze,null,2));
  await writeFile(join(dir,'qualification.json'),JSON.stringify(q,null,2));
  const result=await receive(payload,{fetchImpl,signal});
  await writeFile(join(dir,'output.txt'),result.raw);
  await writeFile(join(dir,'stream.sse'),result.stream);
  const {stream,...safe}=result;
  await writeFile(join(dir,'result.json'),JSON.stringify({...safe,raw:undefined,rawHash:hash(result.raw),requestHash:hash(request),effective,sessionId,requestId},null,2));
  await settle(ledgerPath,slot.id,{status:result.status,rawHash:hash(result.raw),requestHash:hash(request),error:result.error,finishReason:result.finishReason,evidence:'slot-'+slot.slot,userReviewed:false});
  if(result.status!=='complete')throw Error('生成未完整确认；原文及回执已保留，等待人工核对，不重试');
  return result.raw;
 };
}
