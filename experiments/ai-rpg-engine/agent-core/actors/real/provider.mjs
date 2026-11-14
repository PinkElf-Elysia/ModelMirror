import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {reserve,MODEL} from '../../actions/real/provider.mjs';
import {hash,clone,need} from '../../contracts.mjs';
import {visibleText} from '../../../harness/measurement.mjs';
import {validate} from './wire.mjs';
export {MODEL};
export const SLOTS={1:{id:1,cap:.15,max:8192,profile:'core-query-r3',choice:'none'},
 2:{id:2,cap:.15,max:8192,profile:'core-query-r3',choice:'auto'},
 3:{id:3,cap:.20,max:32768,profile:'god-long-v1',choice:'none'}};
export class ActorRealProvider extends NativeBridgeProvider{
 constructor({records,url,serviceToken,selections,authorization,mode='real',fetcher=fetch}){
  const address=new URL(url);need(address.protocol==='http:'&&address.hostname==='127.0.0.1'&&!address.username&&!address.password,'R6_BRIDGE_URL');
  need(['real','mock'].includes(mode)&&typeof serviceToken==='string'&&serviceToken.length>=32,'R6_PROVIDER');
  need(authorization.maximumCalls===3&&authorization.maximumUSD===.50&&Date.parse(authorization.expiresAt)>Date.now(),'R6_AUTHORIZATION');
  need(authorization.pricing.inputUSD>0&&authorization.pricing.inputUSD<=.00000075&&authorization.pricing.outputUSD>0&&authorization.pricing.outputUSD<=.00000375&&authorization.pricing.quoteId,'R6_PRICE');
  need(Object.keys(authorization.requests).length===3&&new Set(Object.values(authorization.requests).map(x=>x.slot)).size===3,'R6_SLOTS');
  for(const slot of Object.values(SLOTS)){const s=selections[slot.profile];need(s?.model===MODEL&&s.available&&s.selectionId&&s.selectionRevision&&hash(s.parameters)===hash({temperature:.7,top_p:.8,max_tokens:slot.max}),'R6_SELECTION');}
  super({records,url:address,serviceToken,authorization,fetcher});
  Object.assign(this,{selections,mode,key:'actors-r6-budget',kind:mode==='real'?'controlled-actor-gemini':'controlled-actor-mock'});
 }
 close(){} // Task owner closes the one budget record.
 async request(path,body,signal=AbortSignal.timeout(245000)){
  const target=path.startsWith('../progress/')?'/api/rpg/v1/'+path.slice(3):'/api/rpg/v1/actors/'+path+(path==='chat/completions'?'?profile='+this.parameterProfile:'');
  const response=await this.fetcher(new URL(target,this.url),{method:body?'POST':'GET',redirect:'error',headers:{Authorization:'Bearer '+this.serviceToken,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal});
  const raw=await response.text();need(Buffer.byteLength(raw)<=32*1024*1024&&!raw.includes(this.serviceToken),'R6_RESPONSE');
  return {status:response.status,body:JSON.parse(raw)};
 }
 async generate(session,id,request,meta){
  const grant=this.authorization.requests[id],slot=SLOTS[grant?.slot];
  need(slot&&grant.session===session&&grant.inputHash===hash(meta.input),'R6_REQUEST_NOT_AUTHORIZED');
  need(Date.now()<Date.parse(this.authorization.expiresAt),'R6_AUTHORIZATION_EXPIRED');
  validate(request);need(request.tool_choice===slot.choice,'R6_SLOT_CHOICE');
  this.parameterProfile=slot.profile;this.selection=this.selections[slot.profile];
  const payload={sessionId:session,requestId:id,selectionId:this.selection.selectionId,selectionRevision:this.selection.selectionRevision,parameters:this.selection.parameters,...clone(request)};
  const before=await this.read(),fingerprint=hash({authorization:this.authorization,selections:this.selections,mode:this.mode});
  const data=before?.data||{fingerprint,calls:[],reservedMicros:0};need(data.fingerprint===fingerprint,'R6_AUTHORIZATION_CHANGED');
  const old=data.calls.find(c=>c.id===id&&c.session===session);
  if(old){need(old.requestHash===hash(payload),'NATIVE_REQUEST_COLLISION');return old.result||{status:'unknown',error:'NATIVE_ALREADY_DISPATCHED'};}
  const bound=reserve(payload,this.authorization.pricing,slot);
  need(data.calls.length<3&&!data.calls.some(c=>c.slot===slot.id)&&data.reservedMicros+bound.reserveMicros<=500000,'R6_TOTAL_BUDGET');
  need(data.calls.every(c=>c.result?.status==='completed'),'R6_PREVIOUS_NOT_COMPLETE');
  data.calls.push({slot:slot.id,session,id,payload,requestHash:hash(payload),...bound,capUSD:slot.cap,result:null,response:null});data.reservedMicros+=bound.reserveMicros;
  await this.records.put(this.key,before?.hash||null,data);this.calls++;
  const began=performance.now(),controller=new AbortController();
  const timing={firstContentMs:null,proseFirstMs:null,completedMs:null,source:'host-progress-poll',pollIntervalMs:250};
  const observation=(async()=>{while(!controller.signal.aborted){
   try{const p=await this.request('../progress/'+session+'/'+id,undefined,AbortSignal.any([controller.signal,AbortSignal.timeout(3000)]));
    if(p.status===200&&typeof p.body.text==='string'&&visibleText(p.body.text)){timing.firstContentMs=performance.now()-began;break;}}
   catch{}await new Promise(r=>setTimeout(r,250));
  }})();
  let response,result;try{response=await this.request('chat/completions',payload);result=this.decode(response,payload);}catch(e){result={status:'unknown',error:e.code||'R6_TRANSPORT_UNKNOWN'};}
  timing.completedMs=performance.now()-began;controller.abort();await observation;
  if(timing.firstContentMs===null&&visibleText(result.message?.content)){timing.firstContentMs=timing.completedMs;timing.source='buffered-bridge-completion';}
  // Tool-stage text is not final prose; a completed plain-text phase can use
  // the sampled text time, explicitly not provider-precise TTFT.
  if(slot.choice==='none'&&result.finishReason==='stop'&&visibleText(result.message?.content)&&!/<(?:think|cot|state_delta|variable_panel)\b/i.test(result.message.content))timing.proseFirstMs=timing.firstContentMs;
  result.timing=timing;
  const latest=await this.read(),saved=latest.data.calls.find(c=>c.id===id&&c.session===session);Object.assign(saved,{result,response:response||null});
  await this.records.put(this.key,latest.hash,latest.data);return result;
 }
 async lookup(session,id){const row=await this.read(),entry=row?.data.calls.find(c=>c.id===id&&c.session===session);if(!entry)return null;
  this.parameterProfile=SLOTS[entry.slot].profile;this.selection=this.selections[this.parameterProfile];return super.lookup(session,id);}
}
