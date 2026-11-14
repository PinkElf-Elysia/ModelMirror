import {join} from 'node:path';
import {clone,hash,need,keys} from './contracts.mjs';
import {CoreStore} from './store.mjs';
import {nativeResult,continueRequest,measureRequest} from './query-protocol.mjs';
export class CoreHost{
 static async open({directory,adapter,offlinePreview=false,controlledReal=false,fault,provider}){
  need(provider?.mode==='real'?controlledReal===true:offlinePreview===true,'CORE_PREVIEW_DISABLED');need(provider&&typeof provider.generate==='function'&&typeof provider.lookup==='function','CORE_PROVIDER_REQUIRED');
  const store=await CoreStore.open({directory:join(directory,'sessions'),adapter,fault,recoverDeadOwner:true});
  try{return new CoreHost(store,provider);}
  catch(e){await store.close();throw e;}
 }
 constructor(store,provider){this.store=store;this.adapter=store.adapter;this.provider=provider;this.locks=new Map();this.progress=new Map();}
 async close(){await this.provider.close();await this.store.close();}
 async exclusive(id,fn){const previous=this.locks.get(id)||Promise.resolve();const task=previous.catch(()=>{}).then(fn);this.locks.set(id,task);try{return await task;}finally{if(this.locks.get(id)===task)this.locks.delete(id);}}
 create(request){return this.store.create(request);}
 normalize(options={}){return this.provider.options(options);}
 turn(id,request,options={}){
  return this.exclusive(id,async()=>{
   const opts=this.normalize(options),r=await this.store.read(id),old=r.operations[request.operationId];
   if(old){need(old.requestHash===hash({request,options:opts}),'STATE_OPERATION_COLLISION');return r;}
   need(r.revision===request.revision&&!r.pending,'STATE_STALE');
   const context=await this.adapter.assemble(r,request.input,opts.contextOptions);
   need(hash(context.tools)===hash([this.adapter.query.tool])&&context.tool_choice==='auto','CORE_TOOL_DECLARATION');
   context.binding=clone(this.adapter.binding);
   let adopted;
   if(opts.previewHash){
    need(opts.fault===null,'CORE_CANDIDATE_FAULT');
    const p=await this.previewInternal(r,request.input,opts.variant,opts.contextOptions);
    need(p.previewHash===opts.previewHash,'STATE_STALE_CANDIDATE');adopted=p.receipt;
   }
   await this.store.start(id,request,context,opts,adopted??null);
   if(adopted)return this.store.finish(id,request.operationId);
   return this.advance(id,request.operationId,false);
  });
 }
 async previewInternal(r,input,variant,contextOptions){
  const context=await this.adapter.assemble(r,input,contextOptions);
  // Offline candidates are final-text fixtures, never model calls or tool executions.
  const output=await this.provider.preview({messages:context.messages,tools:context.tools,tool_choice:'preview'},{input,state:clone(r.state),variant});
  need(typeof output.text==='string','CORE_PREVIEW_FINAL');
  const receipt={status:'completed',finishReason:'stop',text:output.text,provider:'offline-candidate'};
  const proposal=this.adapter.candidate(r.state,receipt,context.statePolicy);
  return {...proposal,receipt,stateRevision:r.stateRevision,previewHash:hash({stateRevision:r.stateRevision,input,variant,contextOptions,receipt})};
 }
 preview(id,input,options={}){return this.exclusive(id,async()=>{const r=await this.store.read(id);need(!r.pending,'STATE_TURN_PENDING');const o=this.normalize(options);return this.previewInternal(r,input,o.variant,o.contextOptions);});}
 async advance(id,operationId,recover){
  try{
   for(let step=0;step<5;step++){
    let r=await this.store.read(id),op=r.operations[operationId];
    if(!r.pending)return r;
    need(r.pending===operationId&&op.stateRevision===r.stateRevision,'STATE_STALE_RESULT');
    if(['failed','rejected','discarded'].includes(op.status))return r;
    if(op.receipt)return this.store.finish(id,operationId);
    let stage=op.stages.at(-1);
    if(!stage||stage.status==='queried'){
     need(op.stages.length<2,'NATIVE_QUERY_LIMIT');const index=op.stages.length+1;
     const first={messages:op.context.messages,tools:op.context.tools,tool_choice:'auto'};
     const request=index===1?clone(first):continueRequest(first,stage.result.message,op.query);
     stage={id:'stage-'+hash([id,operationId,index]).slice(0,40),index,request,measurement:measureRequest(request),status:'ready',result:null};
     await this.store.update(id,row=>{row.operations[operationId].stages.push(stage);});recover=false;
    }
    let result=stage.result;
    if(!result||result.status==='unknown'){
     if(['dispatched','unknown'].includes(stage.status)){
      if(!recover)return this.store.read(id);
      this.progress.set(id,'正在核对原请求');result=await this.provider.lookup(id,stage.id);
      if(!result||result.status==='unknown')return this.store.mark(id,operationId,'unknown','STATE_RESULT_UNKNOWN');
     }else{
      need(stage.status==='ready','CORE_STAGE');
      await this.store.update(id,row=>{row.operations[operationId].stages.at(-1).status='dispatched';});
      this.progress.set(id,stage.index===1?(this.provider.mode==='real'?'生成／选择工具':'模拟生成／选择工具'):'正在查询本地状态并续写');
      try{result=await this.provider.generate(id,stage.id,clone(stage.request),{input:op.input,state:clone(op.frozenState),options:op.options,stage:stage.index});}
      catch(e){result={status:'unknown',error:e.code||'PROVIDER_RESULT_UNKNOWN'};}
     }
     await this.store.update(id,row=>{const s=row.operations[operationId].stages.at(-1);s.result=clone(result);s.status=result.status;});
    }
    if(result.status!=='completed')return this.store.mark(id,operationId,result.status==='failed'?'failed':'unknown',result.error||'STATE_RESULT_UNKNOWN');
    let parsed;try{parsed=nativeResult(result,{toolChoice:stage.request.tool_choice,query:this.adapter.query});}
    catch(e){return this.store.mark(id,operationId,'rejected',e.code||'NATIVE_INVALID');}
    if(parsed.kind==='query'){
     // Query receives a clone; it cannot mutate the frozen state or authoritative record.
     let output;try{output=await this.adapter.query.execute(clone(op.frozenState),op.stateRevision,parsed.args);}
     catch(e){return this.store.mark(id,operationId,'rejected',e.code||'CORE_QUERY_FAILED');}
     need(output.stateRevision===op.stateRevision,'CORE_QUERY_REVISION');
     await this.store.update(id,row=>{const o=row.operations[operationId];need(o.stateRevision===row.stateRevision,'STATE_STALE_RESULT');o.query=clone(output);o.stages.at(-1).status='queried';});
     continue;
    }
    await this.store.receive(id,operationId,parsed.receipt);return this.store.finish(id,operationId);
   }
   throw Object.assign(Error('CORE_STEP_LIMIT'),{code:'CORE_STEP_LIMIT'});
  }finally{this.progress.delete(id);}
 }
 recover(id){return this.exclusive(id,async()=>{await this.store.reconcile(id);const r=await this.store.read(id);return r.pending?this.advance(id,r.pending,true):r;});}
 action(id,name,request){return this.exclusive(id,()=>{need(['override','enabled','discard','fork'].includes(name),'CORE_ACTION');return this.store[name](id,request);});}
 async view(id){
  const r=await this.store.read(id),view=clone(r);view.adapter={id:this.adapter.id,name:this.adapter.name};view.progress=this.progress.get(id)||null;view.simulated=this.provider.mode!=='real';
  try{
   view.panels=await this.adapter.render(r.state,r.events.at(-1)?.changes||[]);
   view.messages=await Promise.all(r.turns.map(async t=>({...t,html:await this.adapter.display(t.body,r.events.find(e=>e.id===t.id).after,r.config)})));
  }catch(e){view.panels='';view.messages=r.turns.map(t=>({...t,html:null}));view.projectionError=e.code||'CORE_PROJECTION_FAILED';}
  for(const op of Object.values(view.operations)){delete op.context;delete op.frozenState;
   if(op.receipt)op.receipt={status:op.receipt.status,provider:op.receipt.provider};
   if(op.stages)op.stages=op.stages.map(s=>({id:s.id,index:s.index,status:s.status,measurement:s.measurement,usage:s.result?.usage??null,timing:s.result?.timing??null}));
  }
  return view;
 }
 async evidence(id){const r=await this.store.read(id);return {record:r,memoryInputs:await Promise.all(r.turns.map(t=>this.adapter.memory(r,t))),provider:this.provider.kind,realCalls:this.provider.mode==='real'?this.provider.calls:0};}
}
