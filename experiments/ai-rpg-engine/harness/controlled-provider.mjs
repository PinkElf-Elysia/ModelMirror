import {clone,hash,canonical,requireValue,integer} from './contracts.mjs';
import {accounting,normalizeUsage} from './measurement.mjs';
// Adapter over the existing reviewed S2S transport. Caller supplies an explicitly
// authorized frozen catalog/configuration; this module never reads credentials.
export function createHarnessControlledProvider({transport,models,authorized=false,measurementRun=null}){
 requireValue(transport&&typeof transport.generate==='function'&&typeof transport.recoveredOutput==='function','SHOP_TRANSPORT_INVALID');
 requireValue(Array.isArray(models)&&models.length>0&&models.length<=20&&models.every(m=>typeof m.id==='string'&&m.selection&&m.parameters&&integer(m.upperBoundMicros)),'SHOP_MODEL_CONFIGURATION_INVALID');
 const frozen=clone(models);
 async function lookup(request){
  const out=await transport.recoveredOutput(request.sessionId,request.id);if(!out)return {status:'unknown'};
  const e=out.evidence?.record;
  const payload={sessionId:request.sessionId,requestId:request.id,selectionId:request.model.selection.selectionId,selectionRevision:request.model.selection.selectionRevision,messages:request.messages,parameters:request.model.parameters};
  if(!e||e.requestId!==request.id||e.sessionId!==request.sessionId||e.requestedModel!==request.model.selection.model||
    e.selectionId!==request.model.selection.selectionId||e.selectionRevision!==request.model.selection.selectionRevision||canonical(e.parameters)!==canonical(request.model.parameters)||e.rawHash!==hashRaw(out.raw)||e.requestHash!==hashRaw(JSON.stringify(payload)))return {status:'unknown'};
  const usage=out.measurement?.usage||normalizeUsage(null),costs=accounting(usage,request.model.pricing);
  const measurement={accounting:costs,timing:out.measurement?.timing||null};
  const base={requestId:request.id,messagesHash:request.messagesHash,actualModel:e.actualModel||request.model.selection.model,terminal:true,
   costMicros:costs.reportedCostMicros,usage,measurement,evidence:out.evidence};
  if(e.status==='complete'&&out.raw?.trim())return {...base,status:'succeeded',raw:out.raw};
  if(out.failureKnown===true)return {...base,status:'failed',safeToRetry:true,code:out.failureCode||'SHOP_PROVIDER_FAILED'};
  return {status:'unknown'};
 }
 return {mode:'controlled',timeoutMs:250000,
  catalog:async()=>frozen.map(m=>({...clone(m),available:authorized===true})),
  async validate(m){requireValue(authorized===true,'SHOP_REAL_CALL_NOT_AUTHORIZED');requireValue(frozen.some(x=>x.id===m.id&&canonical(x)===canonical(Object.fromEntries(Object.entries(m).filter(([k])=>k!=='available')))),'SHOP_SELECTION_CHANGED');},
  async dispatch(request){
   await this.validate(request.model);requireValue(request.messagesHash===hash(request.messages),'SHOP_INPUT_CHANGED');
   if(measurementRun){const prior=await measurementRun.budget.reserve(measurementRun.testId,request);if(prior.replayed){const recovered=await lookup(request);await measurementRun.budget.settle(measurementRun.testId,request,recovered);return recovered;}}
   try{await transport.generate('earth',{sessionId:request.sessionId,requestId:request.id,messages:request.messages,selection:request.model.selection,params:request.model.parameters,purpose:request.purpose});}catch{/* Only verified persisted evidence may classify failure. */}
   const result=await lookup(request);if(measurementRun)await measurementRun.budget.settle(measurementRun.testId,request,result);return result;
  },async lookup(request){const result=await lookup(request);if(measurementRun)await measurementRun.budget.settle(measurementRun.testId,request,result);return result;}
 };
}
import {createHash} from 'node:crypto';
const hashRaw=s=>createHash('sha256').update(s||'').digest('hex');
