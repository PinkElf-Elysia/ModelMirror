import {NativeBridgeProvider} from '../../local-state/tools/bridge-provider.mjs';
import {hash,clone,need,keys} from '../contracts.mjs';
import {visibleText} from '../../harness/measurement.mjs';
export const MODEL='google/gemini-3.8-flash';
export const SLOTS={cultivation:{1:{id:1,cap:.60,max:32768,profile:'god-long-v1'}},lighthouse:{1:{id:2,cap:.10,max:8192,profile:'core-query-r3'},2:{id:3,cap:.20,max:32768,profile:'god-long-v1'}}};
export function reserve(payload,quote,slot){
 const inputBound=Buffer.byteLength(JSON.stringify(payload),'utf8')+1024;
 const reserveMicros=Math.ceil(inputBound*quote.inputUSD*1e6)+Math.ceil(slot.max*quote.outputUSD*1e6);
 need(reserveMicros<=Math.round(slot.cap*1e6),'CORE_REAL_CALL_BUDGET');return {inputBound,reserveMicros};
}
export class CoreRealProvider extends NativeBridgeProvider{
 constructor({records,url,serviceToken,selections,adapter,authorization,mode='real'}){
  need(['real','mock'].includes(mode)&&SLOTS[adapter],'CORE_REAL_ADAPTER');
  const address=new URL(url);need(address.protocol==='http:'&&address.hostname==='127.0.0.1'&&!address.username&&!address.password,'NATIVE_BRIDGE_URL');
  need(typeof serviceToken==='string'&&serviceToken.length>=32,'NATIVE_SERVICE_TOKEN');
  need(authorization.maximumCalls===3&&authorization.maximumUSD===.90&&Date.parse(authorization.expiresAt)>Date.now(),'CORE_REAL_AUTHORIZATION');
  need(authorization.pricing.inputUSD>0&&authorization.pricing.inputUSD<=.00000075&&authorization.pricing.outputUSD>0&&authorization.pricing.outputUSD<=.00000375&&authorization.pricing.quoteId,'CORE_REAL_PRICE');
  for(const slot of Object.values(SLOTS[adapter])){const s=selections[slot.profile];need(s?.model===MODEL&&s.available&&s.selectionId&&s.selectionRevision&&hash(s.parameters)===hash({temperature:.7,top_p:.8,max_tokens:slot.max}),'CORE_REAL_SELECTION');}
  super({records,url:address,serviceToken,authorization,fetcher:fetch});
  Object.assign(this,{adapter,selections,mode,key:'core-real-budget',kind:mode==='real'?'controlled-core-gemini':'controlled-core-mock'});
 }
 options(value={}){keys(value,['contextOptions']);return {contextOptions:clone(value.contextOptions||{}),fault:null,previewHash:null,variant:null};}
 preview(){throw Error('CORE_REAL_CANDIDATE_DISABLED');}
 close(){} // A single task owner closes the shared budget record.
 async request(path,body,signal=AbortSignal.timeout(245000)){
  const target=path.startsWith('../progress/')?'/api/rpg/v1/'+path.slice(3):'/api/rpg/v1/core/'+this.adapter+'/'+path+(path==='chat/completions'?'?profile='+this.parameterProfile:'');
  const response=await this.fetcher(new URL(target,this.url),{method:body?'POST':'GET',redirect:'error',headers:{Authorization:'Bearer '+this.serviceToken,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal});
  const raw=await response.text();need(Buffer.byteLength(raw)<=32*1024*1024&&!raw.includes(this.serviceToken),'NATIVE_RESPONSE');return {status:response.status,body:JSON.parse(raw)};
 }
 async generate(session,id,request,meta){
  const slot=SLOTS[this.adapter][meta.stage];
  if(!slot)return {status:'failed',error:'CORE_AUTHORIZED_STAGE_LIMIT',provider:this.kind};
  need(Date.now()<Date.parse(this.authorization.expiresAt),'NATIVE_AUTHORIZATION_EXPIRED');
  this.parameterProfile=slot.profile;this.selection=this.selections[slot.profile];
  const payload={sessionId:session,requestId:id,selectionId:this.selection.selectionId,selectionRevision:this.selection.selectionRevision,parameters:this.selection.parameters,...clone(request)};
  const before=await this.read(),fingerprint=hash({authorization:this.authorization,selections:this.selections,mode:this.mode});
  const data=before?.data||{fingerprint,calls:[],reservedMicros:0};need(data.fingerprint===fingerprint,'NATIVE_AUTHORIZATION_CHANGED');
  const old=data.calls.find(c=>c.id===id&&c.session===session);
  if(old){need(old.requestHash===hash(payload),'NATIVE_REQUEST_COLLISION');return old.result||{status:'unknown',error:'NATIVE_ALREADY_DISPATCHED'};}
  const bound=reserve(payload,this.authorization.pricing,slot);
  need(data.calls.length<3&&!data.calls.some(c=>c.slot===slot.id)&&data.reservedMicros+bound.reserveMicros<=900000,'CORE_REAL_TOTAL_BUDGET');
  data.calls.push({slot:slot.id,adapter:this.adapter,session,id,payload,requestHash:hash(payload),...bound,capUSD:slot.cap,result:null,response:null});data.reservedMicros+=bound.reserveMicros;
  await this.records.put(this.key,before?.hash||null,data);this.calls++;
  const began=performance.now(),controller=new AbortController();
  const timing={firstContentMs:null,proseFirstMs:null,completedMs:null,source:'host-progress-observation',pollIntervalMs:250};
  const observation=(async()=>{while(!controller.signal.aborted){try{const p=await this.request('../progress/'+session+'/'+id,undefined,AbortSignal.any([controller.signal,AbortSignal.timeout(3000)]));if(p.status===200&&visibleText(p.body.text)){timing.firstContentMs=performance.now()-began;break;}}catch{}await new Promise(r=>setTimeout(r,250));}})();
  let response,result;try{response=await this.request('chat/completions',payload);result=this.decode(response,payload);}catch(e){result={status:'unknown',error:e.code||'NATIVE_TRANSPORT_UNKNOWN'};}
  timing.completedMs=performance.now()-began;controller.abort();await observation;
  if(timing.firstContentMs===null&&visibleText(result.message?.content)){timing.firstContentMs=timing.completedMs;timing.source='buffered-bridge-completion';}
  result.timing=timing;
  const latest=await this.read(),saved=latest.data.calls.find(c=>c.id===id&&c.session===session);Object.assign(saved,{result,response:response||null});await this.records.put(this.key,latest.hash,latest.data);
  return result;
 }
 async lookup(session,id){const row=await this.read(),entry=row?.data.calls.find(c=>c.id===id&&c.session===session);if(!entry)return null;this.selection=this.selections[SLOTS[entry.adapter][entry.slot===3?2:1].profile];return super.lookup(session,id);}
}
