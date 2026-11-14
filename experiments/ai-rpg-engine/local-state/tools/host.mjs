import {join} from 'node:path';
import {LocalStateHost} from '../host.mjs';
import {LocalStateStore} from '../store.mjs';
import {hash,need,clone} from '../definition.mjs';
import {simulatedReply} from '../simulator.mjs';
import {loadToolsAdapter,assembleToolsContext,MODE} from './adapter.mjs';
import {NativeSimulator} from './simulator.mjs';
import {queryLocalState} from './query.mjs';
import {nativeResult,continueRequest,measureRequest} from './protocol.mjs';
export class ToolsHost extends LocalStateHost{
 static async open({directory,enabled=false,provider}){
  need(enabled,'STATE_PREVIEW_DISABLED');const adapter=await loadToolsAdapter();
  const store=await LocalStateStore.open({directory:join(directory,'sessions'),binding:adapter.binding,recoverDeadOwner:true});
  try{return new ToolsHost(store,provider||await NativeSimulator.open(join(directory,'provider')));}catch(e){await store.close();throw e;}
 }
 constructor(store,provider){super(store,provider);this.progress=new Map();this.mode=MODE;}
 async view(id){
  const view=await super.view(id);view.mode=MODE;view.progress=this.progress.get(id)||null;
  view.nativeQueries=Object.fromEntries(Object.entries(view.operations).filter(([,op])=>op.native).map(([id,op])=>[id,{
   query:op.native.query,stages:op.native.stages.map(s=>({id:s.id,status:s.status,measurement:s.measurement,usage:s.result?.usage??null,accounting:s.result?.accounting??null,timing:s.result?.timing??null})),
   totalModelRequests:op.native.stages.length,mode:this.provider.kind
  }]));
  // Opaque provider metadata remains in persistent records, not the page projection.
  for(const op of Object.values(view.operations))if(op.native){delete op.native;delete op.context;}
  if(view.pending)view.pendingOperation=view.operations[view.pending];
  return view;
 }
 async turn(id,request,{variant='calm',fault=null,previewHash=null,quantityOnly=null}={}){
  need(['calm','warm'].includes(variant)&&[null,'truncated','failed','unknown','unknown-first','invalid','repeat'].includes(fault),'STATE_SIMULATION_OPTION');
  const r=await this.store.read(id),old=r.operations[request.operationId];
  if(old){need(old.requestHash===hash(request)&&hash(old.context.statePolicy?.quantityOnly??null)===hash(quantityOnly),'STATE_OPERATION_COLLISION');return r;}
  let adopted=null;
  if(previewHash){
   need(this.provider.kind==='offline-native-simulator','NATIVE_CANDIDATE_UNAVAILABLE');
   adopted=simulatedReply(request.input,r.state,{variant});
   need(previewHash===hash({stateRevision:r.stateRevision,input:request.input,variant,receipt:adopted}),'STATE_STALE_CANDIDATE');
  }
  const context=await assembleToolsContext(r,request.input,{quantityOnly}),start=await this.store.startTurn(id,request,context);
  if(!start.dispatch)return start.record;
  await this.store.update(id,row=>{row.operations[request.operationId].native={state:clone(r.state),options:{variant,fault},selection:clone(this.provider.selection),stages:[],query:null};});
  if(adopted){await this.store.receive(id,request.operationId,adopted);return this.store.finish(id,request.operationId);}
  return this.advance(id,request.operationId,false);
 }
 async reject(id,operationId,error){
  return (await this.store.update(id,r=>{const op=r.operations[operationId];need(r.pending===operationId,'STATE_TURN_MISMATCH');op.status='rejected';op.error=error;})).record;
 }
 async advance(id,operationId,recover){
  try{
   for(let step=0;step<4;step++){
    let r=await this.store.read(id),op=r.operations[operationId];if(!r.pending)return r;
    need(r.pending===operationId&&op.native,'STATE_TURN_MISMATCH');
    need(op.stateRevision===r.stateRevision,'STATE_STALE_RESULT');
    if(['failed','rejected','discarded'].includes(op.status))return r;
    if(op.receipt)return this.store.finish(id,operationId);
    let stage=op.native.stages.at(-1);
    if(!stage||stage.status==='queried'){
     need(op.native.stages.length<2,'NATIVE_QUERY_LIMIT');
     const index=op.native.stages.length+1;
     const first={messages:op.context.messages,tools:op.context.tools,tool_choice:'auto'};
     const request=index===1?first:continueRequest(first,stage.result.message,op.native.query);
     stage={id:'native-'+hash([id,operationId,index]).slice(0,40),index,request,measurement:measureRequest(request),status:'ready',result:null};
     await this.store.update(id,row=>{row.operations[operationId].native.stages.push(stage);});
     recover=false;
    }
    let result=stage.result;
    if(!result||result.status==='unknown'){
     if(stage.status==='dispatched'||stage.status==='unknown'){
      if(!recover)return this.store.read(id);
      this.progress.set(id,'正在核对原请求');
      result=await this.provider.lookup(id,stage.id);
      if(!result||result.status==='unknown')return this.store.markFailure(id,operationId,{message:'STATE_RESULT_UNKNOWN'});
     }else{
      if(hash(this.provider.selection)!==hash(op.native.selection))return this.reject(id,operationId,'NATIVE_SELECTION_CHANGED');
      await this.store.update(id,row=>{row.operations[operationId].native.stages.at(-1).status='dispatched';});
      this.progress.set(id,stage.index===1?'正在生成或选择工具':'已查询状态，正在续写');
      try{result=await this.provider.generate(id,stage.id,stage.request,{input:op.input,state:op.native.state,...op.native.options,stage:stage.index,selection:op.native.selection});}
      catch(e){result={status:['NATIVE_BUDGET','NATIVE_AUTHORIZATION_EXPIRED','NATIVE_AUTHORIZATION_CHANGED'].includes(e.code)?'failed':'unknown',error:e.code||'NATIVE_TRANSPORT_UNKNOWN'};}
     }
     await this.store.update(id,row=>{const s=row.operations[operationId].native.stages.at(-1);s.result=clone(result);s.status=result.status;});
    }
    if(result.status!=='completed')return this.store.markFailure(id,operationId,{definite:result.status==='failed',message:result.error||'STATE_RESULT_UNKNOWN'});
    let parsed;
    try{parsed=nativeResult(result,{toolChoice:stage.request.tool_choice});}
    catch(e){return this.reject(id,operationId,e.code||e.message);}
    if(parsed.kind==='query'){
     this.progress.set(id,'正在查询本地状态');
     const output=queryLocalState(op.native.state,op.stateRevision,parsed.args);
     await this.store.update(id,row=>{const n=row.operations[operationId].native;n.query=output;n.stages.at(-1).status='queried';});
     continue;
    }
    await this.store.receive(id,operationId,parsed.receipt);
    return this.store.finish(id,operationId);
   }
   throw Object.assign(Error('NATIVE_QUERY_LIMIT'),{code:'NATIVE_QUERY_LIMIT'});
  }finally{this.progress.delete(id);}
 }
 async recover(id){
  await this.store.reconcile(id);const r=await this.store.read(id);
  if(!r.pending)return r;return this.advance(id,r.pending,true);
 }
 async evidence(id){
  const value=await super.evidence(id);return {...value,kind:this.provider.kind,mode:MODE,notice:'模拟阶段不能证明真实模型选择工具或成本收益；签名仅保存在运行记录。'};
 }
}
