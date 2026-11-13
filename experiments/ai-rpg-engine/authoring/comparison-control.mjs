// Trusted S2S transport and remote quota port. Ordinary single-send transport is unchanged.
import {SessionRecords} from './session-storage.mjs';
import {keys,isId,isHash,canonical,parseJson,sha256,fail} from './contracts.mjs';
import {controlledPayload,controlEvidence,controlReceipt} from './controlled-receipt.mjs';
import {PARAMETER_CHOICE_FORMAT} from './model-control.mjs';
import {clone,digest,same,validatePlan,validateRequest,resultEnvelope,validateResult} from './comparison-contract.mjs';
const brand=new WeakMap(),token=Symbol(),FORMAT='modelmirror.rpg.comparison-reservation/1';
const remoteStates=['reserved','claimed','dispatching','unknown','completed','failed','cancelled','not_dispatched','released'];
const codeMap={rpg_comparison_budget_exhausted:'COMPARISON_BUDGET_EXHAUSTED',rpg_comparison_route_changed:'COMPARISON_CONTROL_CHANGED',rpg_comparison_routing_required:'COMPARISON_CONTROL_UNAVAILABLE'};
export const comparisonControlPort=v=>brand.get(v)??null;
export class ComparisonControl{
 #records;#origin;#credential;#timeout;#queue=Promise.resolve();#active=new Set();#closing=false;
 constructor(t,records,o){if(t!==token)fail('COMPARISON_CONTROL_REQUIRED');this.#records=records;this.#origin=o.origin;this.#credential=o.serviceToken;this.#timeout=o.timeoutMs;
  brand.set(this,Object.freeze(Object.fromEntries(['status','reserve','read','claim','settle','release','reconcile'].map(k=>[k,this[k].bind(this)]))));}
 static async open({directory,origin,serviceToken,timeoutMs=250000,recoverDeadOwner=false,storageFault}){
  if(typeof origin!=='string'||!/^http:\/\/(127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}$/.test(origin)||new URL(origin).origin!==origin||Number(new URL(origin).port)>65535)fail('CONTROL_ENDPOINT_INVALID');
  if(typeof serviceToken!=='string'||!/^[\x21-\x7e]{32,512}$/.test(serviceToken))fail('CONTROL_CREDENTIAL_REQUIRED');
  if(!Number.isSafeInteger(timeoutMs)||timeoutMs<20||timeoutMs>300000)fail('CONTROL_TIMEOUT_INVALID');
  return new ComparisonControl(token,await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'comparison-control'}),{origin,serviceToken,timeoutMs});
 }
 #serial(fn){const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #http(resource,body=null){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),resource==='tasks'?this.#timeout:Math.min(this.#timeout,15000));let response;
  try{
   response=await fetch(this.#origin+'/api/rpg/v1/authoring/'+resource,{method:body===null?'GET':'POST',redirect:'error',signal:controller.signal,
    headers:{authorization:'Bearer '+this.#credential,accept:'application/json',...(body===null?{}:{'content-type':'application/json'})},...(body===null?{}:{body:canonical(body)})});
   if(!/^application\/json(?:;|$)/i.test(response.headers.get('content-type')??''))fail('COMPARISON_CONTROL_UNKNOWN');
   const reader=response.body?.getReader();if(!reader)fail('COMPARISON_CONTROL_UNKNOWN');const chunks=[];let size=0;
   try{for(;;){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>32*1024*1024)fail('CONTROL_RESULT_CAPACITY');chunks.push(value);}}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
   const raw=new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks));if(raw.includes(this.#credential))fail('CONTROL_CREDENTIAL_ECHO');const value=parseJson(raw,32*1024*1024);
   if(!response.ok)fail(codeMap[value?.detail]??'COMPARISON_CONTROL_UNKNOWN');return value;
  }catch(e){if(['COMPARISON_BUDGET_EXHAUSTED','COMPARISON_CONTROL_CHANGED','COMPARISON_CONTROL_UNAVAILABLE','CONTROL_RESULT_CAPACITY','CONTROL_CREDENTIAL_ECHO'].includes(e.code))throw e;fail('COMPARISON_CONTROL_UNKNOWN');}
  finally{clearTimeout(timer);controller.abort();}
 }
 #record(s){keys(s,['originHash','plan','remotePlan']);validatePlan(s.plan);if(s.originHash!==sha256(this.#origin)||!same(s.remotePlan,this.#projection(s.plan,s.remotePlan.routeRevision)))fail('COMPARISON_CONTROL_IDENTITY_CHANGED');return s;}
 #projection(p,routeRevision){return {id:p.id,hash:p.hash,sessionId:p.intent.sessionId,operationId:p.intent.operationId,routeRevision,stages:p.stages.map(({id,name,phase,purpose})=>({id,name,phase,purpose}))};}
 async #plan(id){if(!isId(id))fail('COMPARISON_INPUT_INVALID');const r=await this.#records.get(id);if(!r)fail('COMPARISON_RESERVATION_MISSING');return this.#record(r.data);}
 #group(value,s){if(value===null)return null;keys(value,['plan','tasks']);if(!same(value.plan,s.remotePlan)||!Array.isArray(value.tasks)||value.tasks.length!==s.plan.stages.length)fail('COMPARISON_RESERVATION_CONFLICT');
  return {plan:clone(s.plan),tasks:value.tasks.map((t,i)=>{keys(t,['id','state','requestHash','resultHash','attemptId']);if(t.id!==s.plan.stages[i].id||!remoteStates.includes(t.state)||![t.requestHash,t.resultHash].every(x=>x===null||isHash(x))||!(t.attemptId===null||isId(t.attemptId)))fail('COMPARISON_RESERVATION_CONFLICT');return {id:t.id,state:t.state==='claimed'?'dispatching':t.state,requestHash:t.requestHash,resultHash:t.resultHash};})};}
 async status(){const v=await this.#http('comparison-budget');keys(v,['format','level','limit','used','reserved','remaining','revision','routeRevision']);if(v.format!==FORMAT||v.level!=='control'||!['limit','used','reserved','remaining','revision','routeRevision'].every(k=>Number.isSafeInteger(v[k])&&v[k]>=0)||v.remaining!==v.limit-v.used-v.reserved)fail('COMPARISON_CONTROL_INVALID');return v;}
 async reserve(value){const plan=clone(validatePlan(value));return this.#serial(async()=>{
  let r=await this.#records.get(plan.id);if(r){this.#record(r.data);if(!same(r.data.plan,plan))fail('COMPARISON_RESERVATION_CONFLICT');}
  else{const state=await this.status();r=await this.#records.put(plan.id,null,{originHash:sha256(this.#origin),plan,remotePlan:this.#projection(plan,state.routeRevision)});}
  return this.#group(await this.#http('comparison-reservations',r.data.remotePlan),r.data);
 });}
 async read(id){const r=await this.#serial(()=>this.#records.get(id));if(!r)return null;const saved=this.#record(r.data);return this.#group(await this.#http('comparison-reservations/'+id),saved);}
 async claim(batch,id,requestHash){if(!isId(id)||!isHash(requestHash))fail('COMPARISON_REQUEST_INVALID');await this.#plan(batch);const v=await this.#http('comparison-reservations/'+batch+'/claim',{stageId:id,requestHash});keys(v,['dispatch','state']);if(typeof v.dispatch!=='boolean'||!remoteStates.includes(v.state))fail('COMPARISON_CONTROL_INVALID');return v;}
 async release(batch,id){if(!isId(id))fail('COMPARISON_REQUEST_INVALID');await this.#plan(batch);return this.#http('comparison-reservations/'+batch+'/release',{stageId:id});}
 async settle(batch,id,requestHash,result){if(!isId(id)||!isHash(requestHash)||!isHash(result.hash)||!['completed','failed','cancelled','not_dispatched'].includes(result.status))fail('COMPARISON_RESULT_INVALID');await this.#plan(batch);return this.#http('comparison-reservations/'+batch+'/settle',{stageId:id,requestHash,status:result.status,resultHash:result.hash});}
 reconcile(){return this.#serial(()=>this.#records.reconcile());}
 async #payload(request){const {plan}=await this.#plan(request.batchId),stage=plan.stages.find(s=>s.id===request.id);if(!stage)fail('COMPARISON_STAGE_INVALID');validateRequest(request,plan,stage);
  if(request.modelChoice.format!==PARAMETER_CHOICE_FORMAT)fail('PARAMETER_CONTROL_V2_REQUIRED');
  return {...controlledPayload(request),format:'modelmirror.rpg.authoring-task/3',comparison:{batchId:plan.id,planHash:plan.hash,stageId:stage.id}};
 }
 #saved(record,request,payload){keys(record,['request','payload','result']);if(!same(record.request,request)||!same(record.payload,payload))fail('COMPARISON_REQUEST_CONFLICT');if(record.result)validateResult(record.result,request);return record;}
 async #accept(request,payload,value){
  if(value?.status==='absent'){keys(value,['status','sourceRequestHash']);if(value.sourceRequestHash!==digest(request))fail('CONTROL_RECEIPT_MISMATCH');return null;}
  if(value?.status==='unknown'&&!Object.hasOwn(value,'receipt')){keys(value,['status','requestHash']);if(!isHash(value.requestHash))fail('CONTROL_RECEIPT_MISMATCH');return null;}
  controlEvidence(value,request);const checked=controlReceipt(value,request),c=value.receipt;
  const result=resultEnvelope(request,{status:c.dispatched===false?'not_dispatched':value.status,raw:value.raw,receipt:{kind:'comparison-control',requestId:request.id,requestHash:digest(request),modelId:request.modelChoice.entry.modelId,parameters:clone(request.parameters),messagesHash:request.messagesHash,dispatched:c.dispatched,retries:0,controlReceipt:checked,evidence:clone(value)}});
  return this.#serial(async()=>{const id='transport-'+request.id,s=await this.#records.get(id);if(s)this.#saved(s.data,request,payload);if(s?.data.result&&s.data.result.status!=='unknown'&&!same(s.data.result,result))fail('COMPARISON_RESULT_CONFLICT');await this.#records.put(id,s?.hash??null,{request:clone(request),payload,result});return result;});
 }
 dispatch(value){if(this.#closing)fail('COMPARISON_CLOSED');const request=clone(value),pending=(async()=>{const payload=await this.#payload(request);
  await this.#serial(async()=>{const id='transport-'+request.id;if(await this.#records.get(id))fail('COMPARISON_REQUEST_ALREADY_CLAIMED');await this.#records.put(id,null,{request,payload,result:null});});
  const result=await this.#accept(request,payload,await this.#http('tasks',payload));if(!result)fail('COMPARISON_RESULT_UNKNOWN');return result;
 })();this.#active.add(pending);pending.then(()=>this.#active.delete(pending),()=>this.#active.delete(pending));return pending;}
 async lookup(value){const request=clone(value),payload=await this.#payload(request);await this.reconcile();const record=await this.#serial(()=>this.#records.get('transport-'+request.id));
  if(record){this.#saved(record.data,request,payload);if(record.data.result&&record.data.result.status!=='unknown')return clone(record.data.result);}
  return this.#accept(request,payload,await this.#http('tasks/'+request.sessionId+'/'+request.id+'?sourceRequestHash='+digest(request)));
 }
 async close(){this.#closing=true;await Promise.allSettled([...this.#active]);await this.#queue;await this.#records.close();}
}
