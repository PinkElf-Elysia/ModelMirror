import {randomUUID} from 'node:crypto';
import {VersionedSessionStore} from './versioned-store.mjs';
import {bindRuntime} from './runtime-binding.mjs';
import {atomicJson,branchId} from './branch-archive.mjs';
import {sha,fail} from '../plugins/catalog.mjs';
const validId=x=>typeof x==='string'&&/^[a-zA-Z0-9-]{1,80}$/.test(x);
// A new adapter-aware dispatch boundary. Legacy versioned code stays byte-frozen.
// Persistence, runtime verification, session locks and branch archives are inherited.
export class CardSessionStore extends VersionedSessionStore {
 constructor(directory,generate,runtime,adapter){super(directory,generate,null,runtime);this.adapter=adapter;}
 async create(input){
  const configuration=this.adapter.create(input);
  if(configuration.mode!=='offline')throw fail('PROVIDER_DISABLED',403);
  await this.runtime.verify();
  const s={...configuration,id:randomUUID(),created:new Date().toISOString(),revision:0,history:[],turns:[],requests:{}};
  s.runtime=bindRuntime(this.runtime,s);await this.write(s);return s;
 }
 async write(s){return branchId(s.id)?super.write(s):atomicJson(this.path(s.id),s);}
 async send(id,request){
  this.adapter.validateSend(request);
  const {input,requestId,revision}=structuredClone(request);
  if(typeof input!=='string'||!input.trim()||input.length>20000||!validId(requestId)||!Number.isSafeInteger(revision)||revision<0)throw fail('CARD_REQUEST_INVALID',400);
  return this.exclusive(id,async()=>{
   const s=await this.read(id);await this.requireRuntime(s);
   if(s.provenance?.requestOrigins?.some(o=>o.requestId===requestId))throw fail('BRANCH_INHERITED_REQUEST_ID');
   const prior=s.requests[requestId];
   if(prior){if(prior.inputHash!==sha(input))throw fail('CARD_REQUEST_COLLISION');return s;}
   if(Object.values(s.requests).some(r=>r.status==='pending'))throw fail('存在未确认请求，请先核对处理状态');
   if(s.revision!==revision)throw fail('CARD_SESSION_CONFLICT');
   if(s.mode!=='offline')throw fail('PROVIDER_DISABLED',403);
   const a=this.adapter.assemble(s,input),controller=new AbortController();
   this.running.set(id,controller);
   try{
    s.requests[requestId]={status:'pending',inputHash:sha(input),assemblyHash:sha(JSON.stringify(a.messages)),at:new Date().toISOString()};
    await this.write(s);
    try{
     const raw=await this.generate({messages:a.messages,params:s.params,signal:controller.signal,sessionId:id,requestId});
     if(controller.signal.aborted)s.requests[requestId].status='cancelled';
     else{
      if(typeof raw!=='string'||!raw.trim())throw fail('CARD_EMPTY_OUTPUT');
      s.history.push({role:'user',content:a.current},{role:'assistant',content:raw});
      s.turns.push({requestId,input,raw,rawHash:sha(raw),at:new Date().toISOString()});
      s.requests[requestId].status='complete';s.revision++;
     }
    }catch{
     // Without a trustworthy terminal receipt, keep unknown results pending.
     s.requests[requestId].status=controller.signal.aborted?'cancelled':'pending';
     s.requests[requestId].error=controller.signal.aborted?'已取消':'结果未确认；不会自动重试';
    }
    await this.write(s);return s;
   }finally{this.running.delete(id);}
  });
 }
}
