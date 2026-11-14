import {ControlledProvider} from './gateway-transport.mjs';
import {validateModelChoice} from '../../authoring/model-control.mjs';
import {digest,idFor,check,pending,requestBody,validateReceipt,MESSAGE_CONTRACT} from './contracts.mjs';
export function wireRequest(r){
 const choice=r.model.choice;validateModelChoice(choice);
 check(r.model.outputTokens===(choice.parameters??choice.parameterProfile?.parameters)?.maxOutputTokens,'MODEL_OUTPUT_MISMATCH');
 return {sessionId:r.groupId,id:r.id,operationId:r.operationId,kind:'story',modelChoice:choice,messages:r.messages,messagesHash:r.messagesHash,messageContract:MESSAGE_CONTRACT,groupBinding:{targetId:r.targetId,version:r.binding,sourceHash:r.sourceHash}};
}
export class GroupGateway {
 #transport;constructor(transport){check(transport instanceof ControlledProvider,'TRANSPORT_REQUIRED');this.#transport=transport;}
 get mode(){return 'controlled';}
 async #convert(r,receipt){
  if(!receipt)return null;
  const c=receipt.control,completed=c.status==='completed'&&c.finishReason==='stop'&&receipt.result.text.trim().length>0;
  const result={format:'modelmirror.rpg.group-receipt/1',requestId:r.id,requestHash:digest(requestBody(r)),targetId:r.targetId,modelHash:digest(r.model),status:completed?'completed':'failed',dispatched:c.dispatched,actualModel:c.actualModel??c.requestedModel,content:completed?receipt.result.text:'',partial:completed?'':receipt.result.text,costMicroUsd:c.dispatched?null:0,evidenceHash:receipt.sha256};
  // No invoice is present in this gateway receipt: retain the conservative
  // reservation, rather than deriving a fictional settled USD cost from tokens.
  return validateReceipt(result,r);
 }
 async dispatch(r){return this.#convert(r,await this.#transport.dispatch(wireRequest(r)));}
 async lookup(r){return this.#convert(r,await this.#transport.lookup(wireRequest(r)));}
}
export class GroupHost {
 #store;#provider;
 constructor(store,provider){check(provider&&provider.mode===store.mode&&['fixture','controlled'].includes(provider.mode),'PROVIDER_MODE');this.#store=store;this.#provider=provider;}
 async #event(id,action,requestId,payload){const s=await this.#store.read(id);return this.#store[action](id,{operationId:idFor('op',requestId,action,s.revision),expectedRevision:s.revision,payload});}
 async #accept(id,r,e){
  if(e){validateReceipt(e,r);await this.#event(id,'adopt',r.id,{requestId:r.id,evidence:e});}
  else await this.#event(id,'markUnknown',r.id,{requestId:r.id});
  return this.#store.view(id);
 }
 async send(id,input){
  const result=await this.#store.prepare(id,input);if(result.duplicate)return this.#store.view(id);
  const r=(await this.#store.read(id)).requests.find(x=>x.id===result.requestId);
  await this.#event(id,'claim',r.id,{requestId:r.id});
  // Commit dispatch ownership before touching a provider. No retry on failure.
  let evidence=null;try{evidence=await this.#provider.dispatch(requestBody(r));}catch{}
  try{return await this.#accept(id,r,evidence);}catch(e){
   // A failed local commit must be reconciled before another write. Preserve
   // the transport's raw evidence and never redispatch to repair this failure.
   if(e.code==='GROUP_RECEIPT_MISMATCH'||e.code==='GROUP_RECEIPT_INVALID'||e.code==='GROUP_COST_BOUND_EXCEEDED')await this.#event(id,'markUnknown',r.id,{requestId:r.id});
   throw e;
  }
 }
 async recover(id,requestId){
  const s=await this.#store.read(id),r=s.requests.find(x=>x.id===requestId);check(!!r,'REQUEST_NOT_FOUND');
  if(['completed','failed'].includes(r.status))return this.#store.view(id);
  check(r.status!=='prepared'&&pending(s)?.id===r.id,'REQUEST_STATE');let evidence=null;
  try{evidence=await this.#provider.lookup(requestBody(r));}catch{}
  return this.#accept(id,r,evidence);
 }
}
