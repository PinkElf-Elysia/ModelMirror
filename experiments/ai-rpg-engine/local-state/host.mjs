import {join} from 'node:path';
import {LocalStateStore} from './store.mjs';
import {OfflineProvider,simulatedReply} from './simulator.mjs';
import {loadAdapter,stripPanels} from './adapter.mjs';
import {assembleContext,validateConfig,displayBody,memoryInputs} from './context.mjs';
import {renderPanels} from './panels.mjs';
import {hash,need,clone} from './definition.mjs';
import {OPENING} from './example.mjs';
export class LocalStateHost{
 static async open({directory,enabled=false}){
  need(enabled,'STATE_PREVIEW_DISABLED');const adapter=await loadAdapter();
  const store=await LocalStateStore.open({directory:join(directory,'sessions'),binding:adapter.binding,recoverDeadOwner:true});
  try{return new LocalStateHost(store,await OfflineProvider.open(join(directory,'provider')));}catch(e){await store.close();throw e;}
 }
 constructor(store,provider){this.store=store;this.provider=provider;}
 async close(){await this.provider.close();await this.store.close();}
 async create(data){await validateConfig(data.config);return this.store.create(data);}
 async view(id){
  const r=await this.store.read(id),last=r.turns.at(-1),turns=[];
  for(const t of r.turns){const state=r.events.find(e=>e.id===t.id)?.after||r.state;turns.push({...t,html:await displayBody(t.body,state,r.config)});}
  const inheritedTurns=[];
  for(const t of r.source.history||[])inheritedTurns.push({input:t.input,html:await displayBody(stripPanels(t.body).replace(/<local_relation id="[^"]+"><\/local_relation>/g,''),r.initial,r.config)});
  return {...r,turns,inheritedTurns,opening:r.source.kind==='example'?OPENING.replace('林清',r.initial.profile.name||'你'):'已根据确认的档案建立适配分支。原会话保留；接下来将从这个检查点继续。',panels:await renderPanels(r.state,last?.changes||[]),realCalls:0,pendingOperation:r.pending?r.operations[r.pending]:null};
 }
 async preview(id,{input,variant='calm'}){
  const r=await this.store.read(id);need(!r.pending&&r.enabled,'STATE_TURN_PENDING');need(['calm','warm'].includes(variant),'STATE_VARIANT');
  const receipt=simulatedReply(input,r.state,{variant}),p=await this.store.previewCandidate(id,receipt);
  return {...p,html:await displayBody(p.body,p.state,r.config),previewHash:hash({stateRevision:r.stateRevision,input,variant,receipt}),variant,input,revision:r.revision};
 }
 async turn(id,request,{variant='calm',fault=null,previewHash=null}={}){
  need(['calm','warm'].includes(variant)&&[null,'truncated','failed','unknown'].includes(fault),'STATE_SIMULATION_OPTION');
  const r=await this.store.read(id),seen=r.operations[request.operationId];
  if(seen){need(seen.requestHash===hash(request),'STATE_OPERATION_COLLISION');return r;}
  if(previewHash){const receipt=simulatedReply(request.input,r.state,{variant});need(previewHash===hash({stateRevision:r.stateRevision,input:request.input,variant,receipt}),'STATE_STALE_CANDIDATE');}
  const context=await assembleContext(r,request.input);context.simulation={variant,fault};
  const start=await this.store.startTurn(id,request,context);if(!start.dispatch)return start.record;
  let receipt;
  try{receipt=await this.provider.generate(id,request.operationId,request.input,r.state,{variant,fault});}
  catch(e){await this.store.markFailure(id,request.operationId,{message:e.code||e.message});throw e;}
  if(fault==='unknown')return this.store.markFailure(id,request.operationId,{message:'模拟回执传输中断；恢复将核对原操作，不再次生成'});
  await this.store.receive(id,request.operationId,receipt);
  if(receipt.status==='failed')return this.store.read(id);
  return this.store.finish(id,request.operationId);
 }
 async recover(id){
  await this.store.reconcile(id);let r=await this.store.read(id);if(!r.pending)return r;
  const op=r.operations[r.pending];if(['failed','rejected'].includes(op.status))return r;
  if(!op.receipt||op.receipt.status==='unknown'){
   const receipt=await this.provider.lookup(id,r.pending);need(receipt,'STATE_RESULT_UNKNOWN');
   await this.store.receive(id,r.pending,receipt);
  }
  r=await this.store.read(id);if(r.operations[r.pending]?.receipt.status==='failed')return r;
  return this.store.finish(id,r.pending);
 }
 async evidence(id){
  const r=await this.store.read(id);return {kind:'offline-only',record:clone(r),memoryTasks:r.turns.length?await memoryInputs(r,r.turns.at(-1)):null};
 }
}
