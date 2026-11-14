import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need,keys,bounded} from '../../agent-core/contracts.mjs';
import {empty,apply,project,save,refresh,exportPlan} from './plan.mjs';
import {Simulator,build,parse,SAMPLES} from './provider.mjs';
export class PlanStore{
 queue=Promise.resolve();
 static async open(directory,sources,{provider=new Simulator(),recoverDeadOwner=false}={}){
  need(provider.mode==='offline','E5_REAL_DISABLED');return new PlanStore(await SessionRecords.open(directory,{scope:'e5-plans',recoverDeadOwner}),sources,provider);
 }
 constructor(records,sources,provider){Object.assign(this,{records,sources,provider});}
 async read(id){need(this.sources.has(id),'E5_SOURCE');const source=this.sources.get(id),frame=await source.read(),key='e5-'+hash([id,frame.reference.session]).slice(0,40),row=await this.records.get(key);
  if(row)need(row.data.format==='e5-record/1','E5_RECORD');return {source,frame,key,row};}
 async view(id){const {source,frame,row}=await this.read(id),d=row?.data;
  return {adapterId:id,title:frame.title,subtitle:frame.subtitle,resources:frame.resources,people:frame.people,coverage:frame.coverage,source:frame.reference,
   sourceReady:frame.ready,recordHash:row?.hash??null,plan:project(source.adapter,frame,d?.current),pending:d?.pending??null,
   history:d?.history??[],operations:d?.operations??{},samples:SAMPLES[id],simulatedCalls:Object.values(d?.operations??{}).filter(x=>x.request).length,realCalls:0};}
 command(c){const p=this.queue.then(()=>this.run(c));this.queue=p.catch(()=>{});return p;}
 async run(c){
  bounded(c,{bytes:24000,depth:10});keys(c,['adapterId','operationId','expectedHash','kind','text','ops']);
  need(/^[a-zA-Z0-9-]{1,80}$/.test(c.operationId)&&typeof c.operationId==='string','E5_OPERATION');
  need(['reply','edit','save','refresh','clear'].includes(c.kind),'E5_COMMAND');
  const {source,frame,key,row}=await this.read(c.adapterId),old=row?.data,requestHash=hash({kind:c.kind,text:c.text??null,ops:c.ops??null});
  const prior=old?.operations[c.operationId];if(prior){need(prior.requestHash===requestHash,'E5_OPERATION_REUSED');return this.view(c.adapterId);}
  need(c.expectedHash===(row?.hash??null),'E5_CONFLICT');need(!old?.pending,'E5_REQUEST_UNKNOWN');
  const d=old?clone(old):{format:'e5-record/1',current:empty(frame),history:[],operations:{},pending:null};
  need(Object.keys(d.operations).length<80,'E5_RECORD_LIMIT');
  const before=clone(d.current);let expected=row?.hash??null;
  d.current.saved=null;d.operations[c.operationId]={requestHash,status:'completed'};
  if(c.kind==='reply'){
   need(frame.ready,'E5_SOURCE_BUSY');const request=build(frame,d.current,c.text);
   d.pending=c.operationId;Object.assign(d.operations[c.operationId],{status:'pending',request});
   expected=(await this.records.put(key,expected,d)).hash;
   let receipt;
   try{
    receipt=await this.provider.generate(request);d.operations[c.operationId].receipt=receipt;
    if(receipt.status==='unknown'){d.operations[c.operationId].status='unknown';}
    else{need(receipt.status==='completed','E5_REPLY_FAILED');const result=parse(receipt.raw);
     need(hash((await source.read()).reference)===hash(frame.reference),'E5_SOURCE_CHANGED');
     d.current=apply(source.adapter,frame,d.current,result,{text:c.text,turnId:c.operationId});d.pending=null;}
   }catch(e){Object.assign(d.operations[c.operationId],{status:receipt?'failed':'unknown',error:e.code||e.message});if(receipt)d.pending=null;}
  }else{
   if(c.kind==='edit')d.current=apply(source.adapter,frame,d.current,{ops:c.ops,question:null,limit:null},{text:c.text,turnId:c.operationId,kind:'user-edit'});
   if(c.kind==='save')d.current=save(source.adapter,frame,d.current);
   if(c.kind==='refresh')d.current=refresh(frame,d.current);
   if(c.kind==='clear'){d.current=empty(frame);d.current.nextId=before.nextId;d.current.revision=before.revision+1;}
   need(hash((await source.read()).reference)===hash(frame.reference),'E5_SOURCE_CHANGED');
  }
  const diff=[];for(const id of new Set([...before.steps,...d.current.steps].map(s=>s.id))){
   const a=before.steps.find(s=>s.id===id),b=d.current.steps.find(s=>s.id===id);
   if(hash(a??null)!==hash(b??null))diff.push({stepId:id,before:a??null,after:b??null});
  }
  d.history.push({operationId:c.operationId,kind:c.kind,text:c.text??c.kind,diff,orderBefore:before.steps.map(s=>s.id),orderAfter:d.current.steps.map(s=>s.id)});
  await this.records.put(key,expected,d);return this.view(c.adapterId);
 }
 async export(id){const {source,frame,row}=await this.read(id);need(row&&!row.data.pending,'E5_EXPORT_PENDING');return exportPlan(source.adapter,frame,row.data.current);}
 async reconcile(id){const {key}=await this.read(id);await this.records.reconcile(key);return this.view(id);}
 close(){return this.records.close();}
}
