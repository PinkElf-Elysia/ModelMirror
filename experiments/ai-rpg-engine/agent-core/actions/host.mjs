import {CoreHost} from '../host.mjs';
import {ActionStore} from './store.mjs';
import {clone,hash,need} from '../contracts.mjs';
import {measureRequest} from '../query-protocol.mjs';
import {parseResult,continuation} from './protocol.mjs';
import {join} from 'node:path';
export class ActionHost extends CoreHost{
 static async open({directory,adapter,provider,offlinePreview=false,controlledReal=false,fault}){
  need(provider.mode==='real'?controlledReal===true:offlinePreview===true&&['offline','mock'].includes(provider.mode),'R5_REAL_DISABLED');
  return new ActionHost(await ActionStore.open({directory:join(directory,'sessions'),adapter,fault,recoverDeadOwner:true}),provider);
 }
 turn(id,request,options={}){return this.exclusive(id,async()=>{
  const opts=this.normalize(options),r=await this.store.read(id),old=r.operations[request.operationId];
  if(old){need(old.requestHash===hash({request,options:opts}),'STATE_OPERATION_COLLISION');return r;}
  need(!r.pending&&r.revision===request.revision,'STATE_STALE');
  const context=await this.adapter.assembleTask(r,request.input);
  await this.store.start(id,request,context,opts);return this.advance(id,request.operationId,false);
 });}
 propose(id,request){return this.exclusive(id,()=>this.store.propose(id,request));}
 cancel(id,request){return this.exclusive(id,()=>this.store.cancel(id,request));}
 execute(id,request){return this.exclusive(id,()=>this.store.execute(id,request));}
 // Explicit invocation. A confirmed failure can make one new narration attempt; unknown never can.
 narrate(id,options={}){return this.exclusive(id,async()=>{
  const r=await this.store.read(id),op=r.operations[r.pending];need(op?.purpose==='action-narration','ACTION_NARRATION');
  need(op.status!=='unknown'&&op.stages.at(-1)?.status!=='dispatched','STATE_RESULT_UNKNOWN');
  if(['failed','rejected'].includes(op.status)){
   await this.store.update(id,row=>{const x=row.operations[row.pending];x.previousAttempts??=[];x.previousAttempts.push(clone(x.stages));x.stages=[];x.status='pending';x.receipt=null;x.options=this.normalize(options);});
  }else if(!op.stages.length)await this.store.update(id,row=>{row.operations[row.pending].options=this.normalize(options);});
  return this.advance(id,r.pending,false);
 });}
 async advance(id,opId,recover){
  try{
   for(let step=0;step<4;step++){
    let r=await this.store.read(id),op=r.operations[opId];
    if(!r.pending||['failed','rejected','discarded'].includes(op?.status))return r;
    need(r.pending===opId&&op.stateRevision===r.stateRevision,'STATE_STALE_RESULT');
    const narrative=op.purpose==='action-narration',purpose=narrative?'narration':'plan';
    if(!op.context){
     const context=await this.adapter.assembleTask(r,op.input,purpose,r.operations[op.actionId].receipt);
     await this.store.update(id,row=>{row.operations[opId].context=context;});continue;
    }
    let stage=op.stages.at(-1);
    if(stage?.status==='prepared')return this.store.acceptProposal(id,opId,op.toolResults.at(-1).output);
    if(!stage||stage.status==='queried'){
     need(op.stages.length<(narrative?1:2),'TOOL_STAGE_LIMIT');
     const first={messages:op.context.messages,tools:op.context.tools,tool_choice:op.context.tool_choice};
     const req=stage?continuation(first,stage.result.message,op.query,this.adapter.tools.tools(['prepare_action'])):first;
     stage={id:'stage-'+hash([id,opId,op.previousAttempts?.length??0,op.stages.length]).slice(0,40),index:op.stages.length+1,request:req,measurement:measureRequest(req),status:'ready',result:null};
     await this.store.update(id,row=>{row.operations[opId].stages.push(stage);});
    }
    let result=stage.result;
    if(!result||result.status==='unknown'){
     if(['dispatched','unknown'].includes(stage.status)){
      if(!recover)return r;
      result=await this.provider.lookup(id,stage.id);
      if(!result||result.status==='unknown')return this.store.mark(id,opId,'unknown','STATE_RESULT_UNKNOWN');
     }else{
      need(stage.status==='ready','CORE_STAGE');
      await this.store.update(id,row=>{row.operations[opId].stages.at(-1).status='dispatched';});
      this.progress.set(id,narrative?'已执行，正在生成结果叙事':'正在查询或准备行动方案');
      try{result=await this.provider.generate(id,stage.id,clone(stage.request),{purpose,input:op.input,state:clone(op.frozenState),stage:stage.index,options:op.options});}
      catch(e){result={status:'unknown',error:e.code||'PROVIDER_RESULT_UNKNOWN'};}
     }
     await this.store.update(id,row=>{const s=row.operations[opId].stages.at(-1);s.result=clone(result);s.status=result.status;});
    }
    if(result.status!=='completed')return this.store.mark(id,opId,result.status==='failed'?'failed':'unknown',result.error||'STATE_RESULT_UNKNOWN');
    let parsed;try{parsed=parseResult(result,stage.request.tools);}
    catch(e){return this.store.mark(id,opId,'rejected',e.code||'NATIVE_INVALID');}
    if(parsed.kind==='tool'){
     let done;try{done=this.adapter.tools.execute(parsed.name,parsed.args,{...r,state:op.frozenState},{allowed:stage.request.tools.map(t=>t.function.name),counts:op.toolCounts||{}});}
     catch(e){return this.store.mark(id,opId,'rejected',e.code||'TOOL_FAILED');}
     await this.store.update(id,row=>{const o=row.operations[opId];o.toolCounts??={};o.toolCounts[parsed.name]=(o.toolCounts[parsed.name]||0)+1;o.toolResults??=[];o.toolResults.push({callId:parsed.callId,name:parsed.name,...clone(done)});o.query=done.output;o.stages.at(-1).status=done.effect==='proposal'?'prepared':'queried';});
     if(done.effect==='proposal')return this.store.acceptProposal(id,opId,done.output);
     continue;
    }
    if(narrative){
     try{return await this.store.finishNarrative(id,opId,parsed.receipt);}
     catch(e){if(['ACTION_NARRATION_WRITE','STATE_REPLY_INCOMPLETE'].includes(e.code))return this.store.mark(id,opId,'rejected',e.code);throw e;}
    }
    await this.store.receive(id,opId,parsed.receipt);return this.store.finish(id,opId);
   }
   throw Object.assign(Error('TOOL_STAGE_LIMIT'),{code:'TOOL_STAGE_LIMIT'});
  }finally{this.progress.delete(id);}
 }
 async view(id){
  const v=await super.view(id);v.actionRule={id:this.adapter.rule.id,label:this.adapter.rule.label,version:this.adapter.rule.version};
  v.resources=clone(this.adapter.resources(v.state));v.toolRegistry=this.adapter.tools.entries.map(({name,effect,maxCalls,maxBytes,version})=>({name,effect,maxCalls,maxBytes,version}));
  v.actionReceipts=v.events.filter(e=>e.kind==='action').map(e=>e.receipt);
  v.metrics={kind:this.provider.mode,realCalls:this.provider.mode==='real'?this.provider.calls:0,stages:Object.values(v.operations).reduce((n,o)=>n+(o.stages?.length||0)+(o.previousAttempts||[]).reduce((s,a)=>s+a.length,0),0),tokens:null,costUSD:this.provider.mode==='real'?null:0};
  return v;
 }
}
