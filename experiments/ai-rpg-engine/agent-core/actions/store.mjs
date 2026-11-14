import {CoreStore} from '../store.mjs';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need,keys,id} from '../contracts.mjs';
import {prepare,verifyProposal} from './rules.mjs';
export class ActionStore extends CoreStore{
 static async open({directory,adapter,fault,recoverDeadOwner=false}){
  return new ActionStore(await SessionRecords.open(directory,{scope:'rpg-actions-r5',fault,recoverDeadOwner}),adapter);
 }
 async propose(key,request){
  keys(request,['operationId','revision','action']);return this.action(key,request,'proposal',r=>{
   need(r.enabled&&!r.pending,'STATE_TURN_PENDING');
   const p=prepare(r,this.adapter.rule,request.action);
   // CoreStore.action appends the operation after this mutation.
   r.proposals??={};r.proposals[request.operationId]={proposal:p,status:'review'};
  });
 }
 async cancel(key,request){
  keys(request,['operationId','revision','proposalId','proposalHash']);
  return this.action(key,request,'cancel-proposal',r=>{
   const p=r.proposals?.[request.proposalId];need(p&&p.status==='review'&&p.proposal.hash===request.proposalHash,'ACTION_PROPOSAL');
   p.status='cancelled';
  });
 }
 async execute(key,request){
  keys(request,['operationId','revision','action','proposalId','proposalHash']);
  need(id(request.operationId)&&Number.isSafeInteger(request.revision),'STATE_INPUT');
  const fingerprint=hash({kind:'execute',request}),prior=await this.read(key),old=prior.operations[request.operationId];
  if(old){need(old.requestHash===fingerprint,'STATE_OPERATION_COLLISION');return prior;}
  return (await this.update(key,r=>{
   need(r.enabled&&!r.pending,'STATE_TURN_PENDING');need(r.revision===request.revision,'STATE_STALE');
   let p;
   if(request.proposalId!==undefined){
    need(request.action===undefined&&id(request.proposalId),'ACTION_INPUT');const row=r.proposals?.[request.proposalId];
    need(row&&row.status==='review'&&row.proposal.hash===request.proposalHash,'ACTION_PROPOSAL');p=row.proposal;
   }else{need(request.proposalHash===undefined&&request.action,'ACTION_INPUT');p=prepare(r,this.adapter.rule,request.action);}
   const out=verifyProposal(r,this.adapter.rule,p),before=clone(r.state),version=r.stateRevision;
   r.state=out.state;r.stateRevision++;
   const receipt={status:'executed',eventId:request.operationId,operationId:request.operationId,session:r.id,actor:'player',
    ruleVersion:this.adapter.rule.version,proposalHash:p.hash,sourceProposal:request.proposalId??null,beforeVersion:version,
    stateRevision:r.stateRevision,changes:clone(out.changes),result:out.result};
   r.events.push({id:request.operationId,kind:'action',operationId:request.operationId,stateRevision:r.stateRevision,before,after:clone(r.state),changes:clone(out.changes),receipt});
   const narrativeId='narrate-'+hash([key,request.operationId]).slice(0,32);
   r.operations[request.operationId]={kind:'execute',requestHash:fingerprint,status:'completed',receipt,narrativeId};
   if(request.proposalId)r.proposals[request.proposalId].status='executed';
   r.operations[narrativeId]={kind:'turn',purpose:'action-narration',status:'pending',stateRevision:r.stateRevision,actionId:request.operationId,
    input:out.result.label+' '+out.result.name+' × '+out.result.quantity,stages:[],receipt:null,context:null,frozenState:clone(r.state),options:{}};
   r.pending=narrativeId;
  })).record;
 }
 async finishNarrative(key,opId,receipt){
  return (await this.update(key,r=>{
   const op=r.operations[opId];need(r.pending===opId&&op.purpose==='action-narration'&&op.stateRevision===r.stateRevision,'STATE_STALE_RESULT');
   need(receipt?.status==='completed'&&receipt.finishReason==='stop'&&typeof receipt.text==='string'&&receipt.text.trim(),'STATE_REPLY_INCOMPLETE');
   need(!/<state_delta>|<variable_panel>/.test(receipt.text),'ACTION_NARRATION_WRITE');
   const event=r.events.find(e=>e.id===op.actionId);need(event,'ACTION_RECEIPT');
   r.turns.push({id:event.id,eventId:event.id,input:op.input,body:receipt.text,changes:clone(event.changes),stateRevision:event.stateRevision});
   op.receipt=clone(receipt);op.status='completed';r.pending=null;
  })).record;
 }
 async acceptProposal(key,opId,proposal){
  return (await this.update(key,r=>{
   const op=r.operations[opId];need(r.pending===opId&&op.stateRevision===r.stateRevision,'STATE_STALE_RESULT');
   verifyProposal(r,this.adapter.rule,proposal);r.proposals??={};r.proposals[opId]={proposal:clone(proposal),status:'review'};
   op.status='proposed';r.pending=null;
  })).record;
 }
}
