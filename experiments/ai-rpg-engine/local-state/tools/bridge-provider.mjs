import {SessionRecords} from '../../authoring/session-storage.mjs';
import {need,hash,clone} from '../definition.mjs';
import {usageFromSSE,normalizeUsage,accounting,visibleText} from '../../harness/measurement.mjs';
export class NativeBridgeProvider{
 static async open({directory,enabled=false,baseURL,serviceToken,selection,authorization,parameterProfile='default',fetcher=fetch}){
  need(enabled,'NATIVE_PROVIDER_DISABLED');
  const url=new URL(baseURL);need(url.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(url.hostname)&&!url.username&&!url.password,'NATIVE_BRIDGE_URL');
  need(typeof serviceToken==='string'&&serviceToken.length>=32,'NATIVE_SERVICE_TOKEN');
  need(selection?.selectionId&&selection.selectionRevision&&selection.model&&selection.parameters?.max_tokens>0,'NATIVE_SELECTION');
  need(authorization?.id&&Number.isInteger(authorization.maximumCalls)&&authorization.maximumCalls>0&&
   Number.isFinite(authorization.maximumUSD)&&authorization.maximumUSD>0&&Number.isFinite(authorization.perCallUSD)&&authorization.perCallUSD>0&&
   Number.isFinite(authorization.pricing?.inputUSD)&&authorization.pricing.inputUSD>=0&&Number.isFinite(authorization.pricing?.outputUSD)&&authorization.pricing.outputUSD>=0&&authorization.pricing.quoteId&&
   Number.isFinite(Date.parse(authorization.expiresAt)),'NATIVE_AUTHORIZATION');
  need(['default','god-long-v1'].includes(parameterProfile),'NATIVE_PROFILE');
  const records=await SessionRecords.open(directory,{scope:'rpg-native-bridge',recoverDeadOwner:true});
  return new NativeBridgeProvider({records,url,serviceToken,selection:{...selection,parameterProfile},authorization,parameterProfile,fetcher});
 }
 constructor(options){Object.assign(this,options);this.kind='controlled-native-bridge';this.calls=0;this.key='budget-'+hash(this.authorization.id).slice(0,40);}
 async close(){await this.records.close();}
 async read(){await this.records.reconcile(this.key);return this.records.get(this.key,x=>x);}
 async request(path,body,signal=AbortSignal.timeout(245000)){
  const response=await this.fetcher(new URL('/api/rpg/v1/local-state/'+path+(path==='chat/completions'?'?profile='+this.parameterProfile:''),this.url),{method:body?'POST':'GET',redirect:'error',headers:{Authorization:'Bearer '+this.serviceToken,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal});
  const raw=await response.text();need(Buffer.byteLength(raw)<=32*1024*1024&&!raw.includes(this.serviceToken),'NATIVE_RESPONSE');
  return {status:response.status,body:JSON.parse(raw)};
 }
 decode(value,payload){
  const b=value.body,r=b.receipt;
  if(!r)return {status:value.status===409&&b.detail==='rpg_native_tools_unavailable'?'failed':'unknown',error:b.detail||b.error||'NATIVE_RECEIPT_MISSING'};
  need(r.gateway==='rpg_scoped'&&r.sessionId===payload.sessionId&&r.requestId===payload.requestId&&r.selectionId===payload.selectionId&&r.selectionRevision===payload.selectionRevision&&r.requestedModel===this.selection.model&&hash(r.parameters)===hash(payload.parameters)&&r.requestHash===hash(payload)&&r.retries===0,'NATIVE_RECEIPT');
  need(r.rawHash===hash(b.raw)&&r.sseHash===hash(b.sse),'NATIVE_RECEIPT_HASH');
  if(r.status!=='complete')return {status:r.status==='failed'&&r.dispatched===false?'failed':'unknown',error:r.error,receipt:clone(r)};
  need(value.status===200&&r.dispatched===true&&r.messageHash===hash(b.message)&&(r.actualModel===null||r.actualModel===this.selection.model),'NATIVE_RECEIPT');
  const result={status:'completed',finishReason:b.finishReason,message:clone(b.message),provider:this.kind,usage:usageFromSSE(b.sse).usageFrames?usageFromSSE(b.sse):normalizeUsage(r.usage,'bridge-receipt'),receipt:clone(r)};
  result.accounting=accounting(result.usage,{currency:'USD',inputPerMillion:this.authorization.pricing.inputUSD*1e6,outputPerMillion:this.authorization.pricing.outputUSD*1e6,quoteId:this.authorization.pricing.quoteId});
  return result;
 }
 async generate(session,id,request){
  need(Date.now()<Date.parse(this.authorization.expiresAt),'NATIVE_AUTHORIZATION_EXPIRED');
  const payload={sessionId:session,requestId:id,selectionId:this.selection.selectionId,selectionRevision:this.selection.selectionRevision,parameters:this.selection.parameters,...clone(request)};
  const fingerprint=hash({authorization:this.authorization,selection:this.selection}),prior=await this.read();
  const data=prior?.data||{fingerprint,calls:[],reservedUSD:0};need(data.fingerprint===fingerprint,'NATIVE_AUTHORIZATION_CHANGED');
  const found=data.calls.find(c=>c.session===session&&c.id===id);
  if(found){need(found.requestHash===hash(payload),'NATIVE_REQUEST_COLLISION');return found.result||{status:'unknown',error:'NATIVE_ALREADY_DISPATCHED'};}
  // A deliberately conservative byte bound, not a tokenizer or a live price quote.
  const inputBound=Buffer.byteLength(JSON.stringify(payload),'utf8')+1024,pricing=this.authorization.pricing;
  const reserveUSD=inputBound*pricing.inputUSD+payload.parameters.max_tokens*pricing.outputUSD;
  need(reserveUSD<=this.authorization.perCallUSD&&data.calls.length<this.authorization.maximumCalls&&data.reservedUSD+reserveUSD<=this.authorization.maximumUSD,'NATIVE_BUDGET');
  const claim={session,id,payload,requestHash:hash(payload),reserveUSD,result:null,response:null};
  data.calls.push(claim);data.reservedUSD+=reserveUSD;await this.records.put(this.key,prior?.hash||null,data);
  this.calls++;const began=performance.now();let response,result;
  const controller=new AbortController(),timing={firstContentMs:null,proseFirstMs:null,completedMs:null,source:'host-progress-observation',pollIntervalMs:250};
  const observation=(async()=>{
   while(!controller.signal.aborted){
    try{
     const progress=await this.request('../progress/'+encodeURIComponent(session)+'/'+encodeURIComponent(id),undefined,AbortSignal.any([controller.signal,AbortSignal.timeout(4000)]));
     if(progress.status===200&&typeof progress.body.text==='string'&&visibleText(progress.body.text)){timing.firstContentMs=performance.now()-began;break;}
    }catch{if(controller.signal.aborted)break;}
    await new Promise(resolve=>{const done=()=>{clearTimeout(timer);controller.signal.removeEventListener('abort',done);resolve();};const timer=setTimeout(done,250);controller.signal.addEventListener('abort',done,{once:true});if(controller.signal.aborted)done();});
   }
  })();
  try{response=await this.request('chat/completions',payload);result=this.decode(response,payload);}
  catch(e){result={status:'unknown',error:e.code||'NATIVE_TRANSPORT_UNKNOWN'};}
  timing.completedMs=performance.now()-began;controller.abort();await observation;
  if(timing.firstContentMs===null&&visibleText(result.message?.content)){timing.firstContentMs=timing.completedMs;timing.source='buffered-bridge-completion';}
  result.timing=timing;
  const row=await this.read(),entry=row.data.calls.find(c=>c.id===id&&c.session===session);entry.result=result;entry.response=response||null;
  await this.records.put(this.key,row.hash,row.data);return result;
 }
 async lookup(session,id){
  const row=await this.read(),entry=row?.data.calls.find(c=>c.id===id&&c.session===session);
  if(!entry)return null;if(entry.result&&entry.result.status!=='unknown')return entry.result;
  let response;try{response=await this.request('results/'+encodeURIComponent(session)+'/'+encodeURIComponent(id));}catch{return null;}
  if(response.status!==200)return null;
  let result;try{result=this.decode({status:response.body.httpStatus,body:response.body.body},entry.payload);}catch{return null;}
  result.timing=entry.result?.timing||{firstContentMs:null,proseFirstMs:null,completedMs:null,source:'recovered-without-timing'};
  const fresh=await this.read(),saved=fresh.data.calls.find(c=>c.id===id&&c.session===session);saved.result=result;saved.response=response.body;
  await this.records.put(this.key,fresh.hash,fresh.data);return result;
 }
}
