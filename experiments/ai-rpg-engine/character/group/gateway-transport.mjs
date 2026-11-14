// Independent transport namespace; preserves original durable no-replay behavior.
// Host-only fixed loopback adapter. Never installed in an author script/worker.
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {canonical,parseJson,keys,isId,sha256,fail} from '../../authoring/contracts.mjs';
import {CONTROLLED_MODE,controlledPayload,controlReceipt,controlEvidence,validateControlledReceipt} from './gateway-receipt.mjs';
const token=Symbol(),ports=new WeakMap(),digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b);
const maximum=32*1024*1024;
export const controlledProviderPort=p=>ports.get(p)??null;
export class ControlledProvider {
 #records;#origin;#token;#timeout;#closed=false;#queue=Promise.resolve();#calls=0;
 constructor(key,records,options){
  if(key!==token)fail('CONTROL_PROVIDER_REQUIRED');this.#records=records;this.#origin=options.origin;this.#token=options.serviceToken;this.#timeout=options.timeoutMs;
  ports.set(this,Object.freeze({mode:CONTROLLED_MODE,...Object.fromEntries(['preflight','ready','dispatch','lookup'].map(k=>[k,this[k].bind(this)]))}));Object.freeze(this);
 }
 static async open({directory,origin,serviceToken,timeoutMs=250000,recoverDeadOwner=false,storageFault}){
  if(typeof origin!=='string'||!/^http:\/\/(127\.0\.0\.1|\[::1\]):[1-9][0-9]{0,4}$/.test(origin))fail('CONTROL_ENDPOINT_INVALID');
  try{if(new URL(origin).origin!==origin||Number(origin.slice(origin.lastIndexOf(':')+1))>65535)fail('CONTROL_ENDPOINT_INVALID');}catch{fail('CONTROL_ENDPOINT_INVALID');}
  if(typeof serviceToken!=='string'||!/^[\x21-\x7e]{32,512}$/.test(serviceToken))fail('CONTROL_CREDENTIAL_REQUIRED');
  if(!Number.isSafeInteger(timeoutMs)||timeoutMs<20||timeoutMs>300000)fail('CONTROL_TIMEOUT_INVALID');
  const records=await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'group-controlled-transport'});
  return new ControlledProvider(token,records,{origin,serviceToken,timeoutMs});
 }
 get dispatchCount(){return this.#calls;}
 preflight(id){if(this.#closed)fail('CONTROL_PROVIDER_CLOSED');if(!isId(id))fail('CONTROL_REQUEST_INVALID');}
 #serial(fn){if(this.#closed)fail('CONTROL_PROVIDER_CLOSED');const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 #check(record,request){
  keys(record,['originHash','requestHash','payload','evidence','receipt']);
  if(record.originHash!==sha256(this.#origin)||record.requestHash!==digest(request)||!same(record.payload,controlledPayload(request)))fail('CONTROL_REQUEST_CONFLICT');
  if(record.evidence)controlEvidence(record.evidence,request);if(record.receipt)validateControlledReceipt(record.receipt,request);
  if(record.receipt&&!same(controlReceipt(record.evidence,request),record.receipt))fail('CONTROL_RECEIPT_MISMATCH');
  return record;
 }
 async ready(){
  if(this.#closed)fail('CONTROL_PROVIDER_CLOSED');let value;
  try{value=await this.#http('',null,'readiness');keys(value,['format','executionEnabled','dispatched']);
   if(value.format!=='modelmirror.rpg.authoring-readiness/1'||typeof value.executionEnabled!=='boolean'||value.dispatched!==false)fail('CONTROL_READINESS_UNAVAILABLE');
  }catch{fail('CONTROL_READINESS_UNAVAILABLE');}
  if(!value.executionEnabled)fail('CONTROL_EXECUTION_DISABLED');return value;
 }
 async #http(path,payload,resource='tasks'){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),resource==='readiness'?Math.min(this.#timeout,10000):this.#timeout);let response;
  try{
   response=await fetch(this.#origin+'/api/rpg/v1/authoring/'+resource+path,{method:payload?'POST':'GET',redirect:'error',signal:controller.signal,
    headers:{authorization:'Bearer '+this.#token,accept:'application/json',...(payload?{'content-type':'application/json'}:{})},...(payload?{body:canonical(payload)}:{})});
   if(!response.ok||!/^application\/json(?:;|$)/i.test(response.headers.get('content-type')??''))fail('CONTROL_RESULT_UNKNOWN');
   const reader=response.body?.getReader();if(!reader)fail('CONTROL_RESULT_UNKNOWN');const chunks=[];let size=0;
   try{while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>maximum)fail('CONTROL_RESULT_CAPACITY');chunks.push(value);}}
   finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
   const value=new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks));if(value.includes(this.#token))fail('CONTROL_CREDENTIAL_ECHO');return parseJson(value,maximum);
  }catch(e){if(['CONTROL_RESULT_CAPACITY','CONTROL_CREDENTIAL_ECHO'].includes(e.code))fail(e.code);fail('CONTROL_RESULT_UNKNOWN');}
  finally{clearTimeout(timer);controller.abort();if(response?.body&&!response.body.locked)await response.body.cancel().catch(()=>{});}
 }
 #empty(request){return {originHash:sha256(this.#origin),requestHash:digest(request),payload:controlledPayload(request),evidence:null,receipt:null};}
 async #accept(saved,request,value){
  if(value.status==='absent'){keys(value,['status','sourceRequestHash']);if(value.sourceRequestHash!==digest(request))fail('CONTROL_RECEIPT_MISMATCH');return null;}
  if(value.status==='unknown'&&!Object.hasOwn(value,'receipt')){keys(value,['status','requestHash']);if(!/^[a-f0-9]{64}$/.test(value.requestHash))fail('CONTROL_RECEIPT_MISMATCH');return null;}
  const receipt=controlReceipt(value,request),data=this.#check(saved?.data??this.#empty(request),request);
  data.evidence=structuredClone(value);data.receipt=receipt;
  await this.#records.put(request.id,saved?.hash??null,data);return receipt;
 }
 dispatch(request){request=structuredClone(request);const payload=controlledPayload(request);return this.#serial(async()=>{
  this.preflight(request.operationId);await this.#records.reconcile();if(await this.#records.get(request.id))fail('CONTROL_REQUEST_ALREADY_CLAIMED');
  const saved=await this.#records.put(request.id,null,this.#empty(request));this.#calls++;
  const value=await this.#http('',payload),receipt=await this.#accept(saved,request,value);if(!receipt)fail('CONTROL_RESULT_UNKNOWN');return receipt;
 });}
 lookup(request){request=structuredClone(request);controlledPayload(request);return this.#serial(async()=>{
  await this.#records.reconcile();const saved=await this.#records.get(request.id);if(saved&&this.#check(saved.data,request).receipt)return structuredClone(saved.data.receipt);
  const path='/'+request.sessionId+'/'+request.id+'?sourceRequestHash='+digest(request),value=await this.#http(path);
  return this.#accept(saved,request,value);
 });}
 async close(){await this.#queue;this.#closed=true;await this.#records.close();}
}
