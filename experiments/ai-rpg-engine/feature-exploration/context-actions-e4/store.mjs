import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need,keys,bounded} from '../../agent-core/contracts.mjs';
import {revise,refresh,accept,handoff} from './draft.mjs';
import {build,parse,Simulator,SAMPLES} from './provider.mjs';
import {query} from './source.mjs';
export class DraftStore{
 queue=Promise.resolve();
 static async open(directory,sources,{provider=new Simulator(),recoverDeadOwner=false}={}){
  return new DraftStore(await SessionRecords.open(directory,{scope:'e4-context-drafts',recoverDeadOwner}),sources,provider);
 }
 constructor(records,sources,provider){Object.assign(this,{records,sources,provider});}
 source(id){need(this.sources.has(id),'E4_SOURCE');return this.sources.get(id);}
 key(f){return 'e4-'+hash([f.adapterId,f.reference.session]).slice(0,40);}
 async read(id){const source=this.source(id),frame=await source.read(),row=await this.records.get(this.key(frame));
  if(row)need(row.data.format==='modelmirror.rpg.e4-record/1','E4_RECORD');
  return {source,frame,row};
 }
 async view(id){
  const {frame,row}=await this.read(id),d=row?.data??null;
  return {mode:'offline-fixture',adapterId:id,title:frame.title,subtitle:frame.subtitle,source:frame.reference,sourceReady:frame.ready,
   sourceLabel:'本任务独立 R5 成年测试会话快照',resources:frame.resources,people:frame.people,capabilities:frame.capabilities,coverage:frame.coverage,
   recordHash:row?.hash??null,draft:d,stale:!!d?.current&&hash(d.current.source)!==hash(frame.reference),
   realCalls:0,simulatedCalls:Object.values(d?.operations??{}).filter(x=>x.request).length,samples:SAMPLES[id]};
 }
 async inspect(id){const {source,frame}=await this.read(id);return {source:frame.reference,result:query(source.adapter,frame)};}
 command(cmd){const p=this.queue.then(()=>this.run(cmd));this.queue=p.catch(()=>{});return p;}
 async run(cmd){
  bounded(cmd,{bytes:16000,depth:8});keys(cmd,['adapterId','kind','operationId','expectedHash','action','changes','text']);
  need(typeof cmd.operationId==='string'&&/^[a-zA-Z0-9-]{1,80}$/.test(cmd.operationId),'E4_OPERATION');
  need(['select','edit','reply','accept','revoke','cancel','refresh'].includes(cmd.kind),'E4_COMMAND');
  const {source,frame,row}=await this.read(cmd.adapterId),a=source.adapter,key=this.key(frame),old=row?.data;
  const requestHash=hash({kind:cmd.kind,action:cmd.action??null,changes:cmd.changes??null,text:cmd.text??null});
  const prior=old?.operations[cmd.operationId];if(prior){need(prior.requestHash===requestHash,'E4_OPERATION_REUSED');return this.view(cmd.adapterId);}
  need((row?.hash??null)===cmd.expectedHash,'E4_DRAFT_CONFLICT');need(!old?.pending,'E4_REQUEST_UNKNOWN');
  const d=old?clone(old):{format:'modelmirror.rpg.e4-record/1',current:null,history:[],operations:{},pending:null};
  need(Object.keys(d.operations).length<80,'E4_DRAFT_LIMIT');
  if(d.current?.status==='cancelled')need(cmd.kind==='select','E4_CANCELLED');
  const previous=clone(d.current),at=new Date().toISOString();let expected=row?.hash??null;
  if(cmd.kind==='reply'){
   need(frame.ready,'E4_SOURCE_BUSY');
   const request=build(frame,d.current,cmd.text);
   if(d.current?.status==='accepted')d.current={...d.current,status:d.current.confirmation.priorStatus,confirmation:null};
   d.pending=cmd.operationId;d.operations[cmd.operationId]={requestHash,status:'pending',request,at};
   expected=(await this.records.put(key,expected,d)).hash;
   let receipt=null;
   try{
    receipt=await this.provider.generate(request);d.operations[cmd.operationId].receipt=receipt;
    if(receipt.status==='unknown'){d.operations[cmd.operationId].status='unknown';}
    else{
     need(receipt.status==='completed','E4_REPLY_FAILED');
     const result=parse(receipt.raw),after=await source.read();need(hash(after.reference)===hash(frame.reference),'E4_SOURCE_CHANGED');
     d.current=revise(a,frame,d.current,{...result,text:cmd.text,turnId:cmd.operationId});
     d.pending=null;d.operations[cmd.operationId].status='completed';
    }
   }catch(e){d.operations[cmd.operationId].status=receipt?'failed':'unknown';d.operations[cmd.operationId].error=e.code||e.message;if(receipt)d.pending=null;}
  }else{
   if(cmd.kind==='select'){
    need(typeof cmd.action==='string','E4_ACTION');d.current=revise(a,frame,null,{action:cmd.action,changes:cmd.changes??[],kind:'user-edit',turnId:cmd.operationId,text:'界面明确选择'});
    d.current.revision=(previous?.revision??0)+1;
   }else if(cmd.kind==='edit'){
    need(d.current,'E4_DRAFT_MISSING');d.current=revise(a,frame,d.current,{changes:cmd.changes,kind:'user-edit',turnId:cmd.operationId,text:'界面明确修改',unsupported:d.current.unsupported,
     questions:d.current.modelQuestions});
   }else if(cmd.kind==='refresh')d.current=refresh(a,frame,d.current);
   else if(cmd.kind==='accept')d.current=accept(a,frame,d.current);
   else if(cmd.kind==='revoke'){need(d.current?.status==='accepted','E4_NOT_ACCEPTED');d.current={...d.current,status:d.current.confirmation.priorStatus,confirmation:null};}
   else{need(d.current,'E4_DRAFT_MISSING');d.current={...d.current,status:'cancelled',confirmation:null,preflight:null};}
   need(hash((await source.read()).reference)===hash(frame.reference),'E4_SOURCE_CHANGED');
   d.operations[cmd.operationId]={requestHash,status:'completed',at};
  }
  const diff=['action','item','quantity','target','condition'].flatMap(field=>{
   const before=field==='action'?previous?.action??null:previous?.intent[field]?.value??null;
   const after=field==='action'?d.current?.action??null:d.current?.intent[field]?.value??null;
   return hash(before)===hash(after)?[]:[{field,before,after}];
  });
  d.history.push({operationId:cmd.operationId,kind:cmd.kind,text:cmd.text??cmd.kind,at,diff,before:previous,after:clone(d.current)});
  await this.records.put(key,expected,d);return this.view(cmd.adapterId);
 }
 async export(id){const {source,frame,row}=await this.read(id);need(row&&!row.data.pending,'E4_EXPORT_PENDING');return handoff(source.adapter,frame,row.data.current);}
 async reconcile(id){const {frame}=await this.read(id);await this.records.reconcile(this.key(frame));return this.view(id);}
 close(){return this.records.close();}
}
