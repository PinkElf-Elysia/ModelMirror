import {AsyncLocalStorage} from 'node:async_hooks';
import {keys,isHash} from '../../authoring/contracts.mjs';
import {check,digest,idFor} from './contracts.mjs';

const uint=n=>Number.isSafeInteger(n)&&n>=0;

// Quote the full admitted input window; no estimate of a later speaker's text is required.
function ceiling(model){
  const price=model.price;
  check(price&&uint(model.contextTokens)&&uint(model.outputTokens)&&model.contextTokens>model.outputTokens,'PRICE_UNKNOWN');
  const input=BigInt(model.contextTokens-model.outputTokens),out=BigInt(model.outputTokens);
  const total=input*BigInt(price.inputMicroUsdPerMillion)+out*BigInt(price.outputMicroUsdPerMillion);
  const bound=Number((total+999999n)/1000000n);
  check(uint(bound),'PRICE_UNKNOWN');return bound;
}

// Host-owned admission for an existing controlled workbench. It owns no provider or balance.
export class RoundControl {
  #step=new AsyncLocalStorage();
  constructor(app,policy,priceEvidence){
    keys(policy,['authorizationId','maxCalls','maxMicroUsd','certifiedModels']);
    check(app.groupStore.mode==='controlled'&&policy.authorizationId===app.context.authorization.id,'ROUND_AUTHORIZATION');
    check(uint(policy.maxCalls)&&policy.maxCalls>=1&&policy.maxCalls<=3&&uint(policy.maxMicroUsd)&&policy.maxMicroUsd>0,'ROUND_LIMIT');
    check(Array.isArray(policy.certifiedModels)&&policy.certifiedModels.length&&policy.certifiedModels.every(isHash),'ROUND_MODEL');
    check(typeof priceEvidence==='function','PRICE_UNKNOWN');this.priceEvidence=priceEvidence;
    this.policy=structuredClone(policy);this.app=app;
  }
  quote(group,targets){
    const steps=targets.map(memberId=>{
      const model=group.members.find(m=>m.id===memberId)?.model;
      check(model&&this.policy.certifiedModels.includes(digest(model))&&model.choice?.entry.modelId===model.id&&model.contextTokens===model.choice.entry.contextLength&&model.outputTokens===model.choice.wireParameters?.max_tokens,'ROUND_MODEL');
      return {memberId,modelId:model.id,modelHash:digest(model),priceRevision:model.price?.revision??null,microUsd:ceiling(model)};
    });
    const microUsd=steps.reduce((n,s)=>n+s.microUsd,0);
    check(uint(microUsd)&&steps.length<=this.policy.maxCalls&&microUsd<=this.policy.maxMicroUsd,'ROUND_LIMIT');
    const estimate={policyHash:digest(this.policy),calls:steps.length,microUsd,steps};
    return {...estimate,hash:digest({groupId:group.id,estimate})};
  }
  async available(group,steps){
    const guard=this.app.context.guard,remaining=await guard.status();
    check(remaining.authorizationId===this.policy.authorizationId&&await guard.registered(group.id),'ROUND_AUTHORIZATION');
    check(!remaining.pending.some(p=>p.sessionId===group.id),'REQUEST_PENDING');
    for(const step of steps){
      let proof;
      try{proof=await this.priceEvidence(step.modelId);}catch{check(false,'PRICE_UNKNOWN');}
      check(proof?.modelHash===step.modelHash&&proof.priceRevision===step.priceRevision&&proof.includesReasoning===true&&
        Number.isSafeInteger(proof.validUntil)&&proof.validUntil>Date.now(),'PRICE_UNKNOWN');
    }
    const cost=steps.reduce((n,s)=>n+s.microUsd,0);
    check(group.budget.usedCalls+steps.length<=group.budget.calls&&group.budget.usedMicroUsd+cost<=group.budget.microUsd,'BUDGET_EXHAUSTED');
    check(remaining.usedCalls+steps.length<=remaining.calls&&remaining.usedMicroUsd+cost<=remaining.microUsd,'TASK_BUDGET_EXHAUSTED');
  }
  attach(){
    const store=this.app.groupStore,prepare=store.prepare,claim=store.claim;
    const getStep=()=>this.#step.getStore();
    store.prepare=async(groupId,input)=>{
      const ticket=getStep();
      if(ticket){
        check(ticket.groupId===groupId&&ticket.operationId===input.operationId,'ROUND_BINDING');
        const group=await store.read(groupId);
        const model=group.members.find(m=>m.id===input.payload.targetId)?.model;
        check(model&&digest(model)===ticket.step.modelHash,'ROUND_MODEL');
        check(!ticket.cancelled(),'ROUND_STOPPING');
        // These checks do not generate text or certify a new model.
        try{
          await this.app.context.group.ready();
          await this.app.context.parameters.validate(model);
        }catch{check(false,'ROUND_ROUTE_CHANGED');}
        await this.available(group,[ticket.step]);
        check(!ticket.cancelled(),'ROUND_STOPPING');
      }
      return prepare.call(store,groupId,input);
    };
    store.claim=async(groupId,input)=>{
      const ticket=getStep();
      const own=ticket&&ticket.groupId===groupId&&input.payload.requestId===idFor('request',groupId,ticket.operationId);
      if(own&&ticket.cancelled()){
        // No claim has occurred. Only this original prepared intent can be closed.
        await store.closePrepared(groupId,{...input,operationId:idFor('cancel',input.payload.requestId)});
        check(false,'ROUND_STOPPING');
      }
      const result=await claim.call(store,groupId,input);
      // Same durable dispatch boundary used by the manual host. Stop can now
      // finish while that one provider call is still waiting for its receipt.
      if(own)ticket.entered();
      return result;
    };
    this.detach=()=>{store.prepare=prepare;store.claim=claim;};
  }
  send(groupId,input,step,entered,cancelled){
    return this.#step.run({groupId,operationId:input.operationId,step,entered,cancelled},()=>this.app.groupHost.send(groupId,input));
  }
}
