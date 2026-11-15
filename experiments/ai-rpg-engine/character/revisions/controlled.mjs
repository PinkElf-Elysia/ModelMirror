import {controlledPayload as singlePayload} from '../../authoring/controlled-receipt.mjs';
import {controlledPayload as groupPayload} from '../group/gateway-receipt.mjs';
import {ControlledProvider as SingleTransport} from '../../authoring/controlled-provider.mjs';
import {ControlledProvider as GroupTransport} from '../group/gateway-transport.mjs';
import {controlReceipt as singleReceipt} from '../../authoring/controlled-receipt.mjs';
import {controlReceipt as groupReceipt} from '../group/gateway-receipt.mjs';
import {digest,copy,check} from './contracts.mjs';
export function revisionWire(r){return {sessionId:r.sourceId,id:r.id,operationId:r.id,kind:'story',modelChoice:copy(r.model.choice),messages:copy(r.context),messagesHash:digest(r.context),messageContract:r.sourceKind==='group'?'modelmirror.rpg.character-group-messages/1':'modelmirror.rpg.character-messages/1',revisionBinding:{branchId:r.branchId,targetId:r.targetId,prefixHash:r.prefixHash,requestHash:r.hash}};}
export class RevisionControlled {
 constructor({single,group,control,guard}){check(single instanceof SingleTransport&&group instanceof GroupTransport&&control&&typeof control.assertCurrent==='function','CONTROL_PROVIDER_REQUIRED');this.single=single;this.group=group;this.control=control;this.mode='controlled';this.guard=guard;}
 async preflight(r){check(r.model.choice,'MODEL_REQUIRED');if(r.model.choice.format.endsWith('/2'))await this.control.parameterCatalog();else await this.control.refresh();await this.control.assertCurrent(r.model.choice);await this.control.validate(r.model.choice);}
 #transport(r){return r.sourceKind==='group'?this.group:this.single;}

 async closePrepared(r){check(this.guard,'CONTROLLED_CLOSE_REQUIRES_PROOF');const evidence=await this.guard.cancelUnclaimed((r.sourceKind==='group'?groupPayload:singlePayload)(revisionWire(r)));check(evidence.receipt.dispatched===false,'DISPATCH_UNCERTAIN');}
 async dispatch(r){return convertRevisionReceipt(r,await this.#transport(r).dispatch(revisionWire(r)));}
 async lookup(r){return convertRevisionReceipt(r,await this.#transport(r).lookup(revisionWire(r)));}
 validate(r,receipt){return validateRevisionReceipt(r,receipt);}
}
export function validateRevisionReceipt(r,receipt){const original=(r.sourceKind==='group'?groupReceipt:singleReceipt)(receipt.raw,revisionWire(r));check(original&&digest(convertRevisionReceipt(r,original))===digest(receipt),'RECEIPT_MISMATCH');}
export function convertRevisionReceipt(r,v){if(!v)return null;const c=v.control,completed=c.status==='completed'&&c.finishReason==='stop'&&v.result.text.trim().length>0;const raw={status:c.status,requestHash:c.requestHash,raw:v.result.text,sse:v.sse,receipt:copy(c)},body={requestId:r.id,requestHash:r.hash,modelId:r.model.id,targetId:r.targetId,status:completed?'completed':'failed',text:v.result.text,raw,dispatched:c.dispatched,costMicroUsd:c.dispatched?null:0};return {...body,hash:digest(body)};}
