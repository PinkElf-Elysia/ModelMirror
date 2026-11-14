import {SessionRecords} from '../../../authoring/session-storage.mjs';
import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {visibleText} from '../../../harness/measurement.mjs';
import {hash,clone,need} from '../../contracts.mjs';
import {reserve} from '../../actions/real/provider.mjs';
import {MODEL,SLOTS,validateGrant,policyFor} from './contract.mjs';
// This transport records receipts; only the common TaskLedger owns the allowance.
export class RealTransport{
 static async open({directory,...options}){const records=await SessionRecords.open(directory,{scope:'r7-live-transport',recoverDeadOwner:true});return new RealTransport({...options,records});}
 constructor({records,url,token,selections,authorization,mode,fetcher=fetch}){
  const u=new URL(url);need(['real','mock'].includes(mode)&&u.protocol==='http:'&&u.hostname==='127.0.0.1'&&['18896','18897'].includes(u.port)&&!u.username&&!u.password,'R7_BRIDGE');
  need(authorization.maximumCalls===3&&authorization.maximumUSD===.60&&authorization.requests.length===3&&token.length>=32,'R7_AUTHORIZATION');
  for(const slot of Object.values(SLOTS)){const s=selections[slot.profile];need(s?.available&&s.model===MODEL&&s.selectionId&&s.selectionRevision&&hash(s.parameters)===hash(policyFor(slot,authorization.pricing).model.parameters),'R7_SELECTION');}
  Object.assign(this,{records,url:u,token,selections,authorization,mode,fetcher,calls:0,active:0,maxActive:0});
 }
 async request(path,body,signal=AbortSignal.timeout(245000)){
  const r=await this.fetcher(new URL(path,this.url),{method:body?'POST':'GET',redirect:'error',signal,headers:{Authorization:'Bearer '+this.token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  const raw=await r.text();need(Buffer.byteLength(raw)<=32*1024*1024&&!raw.includes(this.token),'R7_RESPONSE');return {status:r.status,body:JSON.parse(raw)};
 }
 decode(response,payload,selection){const decoder=new NativeBridgeProvider({selection,authorization:this.authorization});const result=decoder.decode(response,payload);
  if(result.status==='failed'&&result.receipt?.dispatched===false)result.terminal=true;
  const reported=result.accounting?.reportedCostMicros,estimated=result.accounting?.estimatedCostMicros;
  if(result.status==='completed'&&(reported!=null||estimated!=null))result.billing={kind:this.mode==='mock'?'simulated':reported!=null?'provider':'estimate',microUSD:reported??estimated};
  return result;
 }
 budgetPayload(p){const g=this.authorization.requests.find(x=>x.taskId===p.taskId);need(g&&g.session===p.session,'R7_REAL_GRANT');const selection=this.selections[SLOTS[g.slot].profile];return {sessionId:p.session,requestId:p.id,selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:clone(selection.parameters),...clone(p.request)};}
 async generate(packet){
  need(Date.now()<Date.parse(this.authorization.expiresAt),'R7_AUTHORIZATION_EXPIRED');const g=this.authorization.requests.find(x=>x.taskId===packet.taskId);validateGrant(g,packet);need(g.inputHash===hash(packet.meta.input),'R7_INPUT_CHANGED');
  const slot=SLOTS[g.slot],selection=clone(this.selections[slot.profile]);need(hash(packet.model)===hash(policyFor(slot,this.authorization.pricing).model),'R7_MODEL_POLICY');
  const payload={sessionId:packet.session,requestId:packet.id,selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...clone(packet.request)};
  const bound=reserve(payload,this.authorization.pricing,slot);need(bound.reserveMicros<=packet.reserveMicroUSD,'R7_PAYLOAD_RESERVE');
  const key='request-'+slot.id,old=await this.records.get(key),fingerprint=hash({authorization:this.authorization,packet,payload});
  if(old){need(old.data.fingerprint===fingerprint,'R7_REAL_COLLISION');return old.data.result??{status:'unknown',error:'R7_ALREADY_DISPATCHED'};}
  const sentAt=Date.now();await this.records.put(key,null,{slot:slot.id,fingerprint,packet:clone(packet),payload,selection,bound,sentAt,result:null,response:null});
  this.calls++;this.active++;this.maxActive=Math.max(this.maxActive,this.active);const began=performance.now(),controller=new AbortController();
  const timing={firstContentMs:null,proseFirstMs:null,completedMs:null,source:'host-progress-poll',pollIntervalMs:250};
  const observation=(async()=>{while(!controller.signal.aborted){try{const r=await this.request('/api/rpg/v1/progress/'+packet.session+'/'+packet.id,undefined,AbortSignal.any([controller.signal,AbortSignal.timeout(3000)]));if(r.status===200&&typeof r.body.text==='string'&&visibleText(r.body.text)){timing.firstContentMs=performance.now()-began;break;}}catch{}await new Promise(ok=>setTimeout(ok,250));}})();
  let response,result;try{response=await this.request('/api/rpg/v1/'+slot.route+'/chat/completions?profile='+slot.profile,payload);result=this.decode(response,payload,selection);}catch(e){result={status:'unknown',error:e.code||'R7_TRANSPORT_UNKNOWN'};}
  finally{timing.completedMs=performance.now()-began;controller.abort();await observation;this.active--;}
  if(timing.firstContentMs===null&&typeof result.message?.content==='string'&&visibleText(result.message.content)){timing.bufferedContentMs=timing.completedMs;timing.source='buffered-completion-not-ttft';}
  if(result.finishReason==='stop'&&typeof result.message?.content==='string'&&visibleText(result.message.content)&&!/<(?:think|cot|state_delta|variable_panel)\b/i.test(result.message.content))timing.proseFirstMs=timing.firstContentMs;
  result.timing=timing;const e=await this.records.get(key);await this.records.put(key,e.hash,{...e.data,result,response:response??null,completedAt:Date.now()});return result;
 }
 async lookup(session,id){for(const slot of Object.values(SLOTS)){const key='request-'+slot.id,e=await this.records.get(key);if(e?.data.payload.sessionId!==session||e?.data.payload.requestId!==id)continue;
  if(e.data.result&&e.data.result.status!=='unknown')return e.data.result;
  const response=await this.request('/api/rpg/v1/'+slot.route+'/results/'+session+'/'+id);if(response.status!==200)return null;
  const result=this.decode({status:response.body.httpStatus,body:response.body.body},e.data.payload,e.data.selection);result.timing=e.data.result?.timing??null;await this.records.put(key,e.hash,{...e.data,result,recoveredResponse:response});return result;
 }return null;}
 async evidence(){return {mode:this.mode,calls:(await Promise.all([1,2,3].map(n=>this.records.get('request-'+n)))).filter(Boolean).map(e=>e.data),maxConcurrent:this.maxActive};}
 close(){return this.records.close();}
}
