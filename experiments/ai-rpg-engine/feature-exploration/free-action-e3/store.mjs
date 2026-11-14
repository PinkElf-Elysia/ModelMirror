import {randomUUID} from 'node:crypto';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {hash,clone,need,bounded} from '../../agent-core/contracts.mjs';
import {sourceRef} from '../free-action/proposal.mjs';
import {assertReady} from '../free-action-e2/source.mjs';
import {proposal,accept,exportProposal} from './intent.mjs';
import {parseReply} from './provider.mjs';
export class DraftStore{
 queue=Promise.resolve();
 constructor(records,source,builder,provider){Object.assign(this,{records,source,builder,provider});}
 static async open(directory,options){return new DraftStore(await SessionRecords.open(directory,{scope:'e3-free-action',recoverDeadOwner:options.recoverDeadOwner??false}),options.source,options.builder,options.provider);}
 key(w){return 'e3-'+hash(w.session).slice(0,40);}
 async read(){const world=await this.source.read(),row=await this.records.get(this.key(world));if(row)need(row.data.format==='modelmirror.rpg.e3-draft/1','DRAFT_FORMAT');return {world,row};}
 async view(){const {world,row}=await this.read(),draft=row?.data??null;return {world,recordHash:row?.hash??null,draft,stale:!!draft?.current&&hash(draft.current.source)!==hash(sourceRef(world)),realCalls:0,simulatedCalls:Object.values(draft?.operations??{}).filter(op=>op.request).length};}
 command(cmd){const next=this.queue.then(()=>this.run(cmd));this.queue=next.catch(()=>{});return next;}
 async run(cmd){
  bounded(cmd,{bytes:8000,depth:6});
  need(typeof cmd.operationId==='string'&&/^[a-zA-Z0-9-]{1,80}$/.test(cmd.operationId),'OPERATION_ID');
  need(['reply','edit','accept','revoke','cancel','new','refresh'].includes(cmd.kind),'COMMAND');
  const {world,row}=await this.read();assertReady(world);
  const key=this.key(world),requestHash=hash({kind:cmd.kind,text:cmd.text??null,changes:cmd.changes??null}),old=row?.data;
  const prior=old?.operations[cmd.operationId];
  if(prior){need(prior.requestHash===requestHash,'OPERATION_REUSED');return this.view();}
  need((row?.hash??null)===cmd.expectedHash,'DRAFT_CONFLICT');need(!old?.pending,'REQUEST_UNKNOWN');
  need(!old||Object.keys(old.operations).length<100,'DRAFT_LIMIT');
  if(old?.current?.unsupported)need(['new','cancel','refresh'].includes(cmd.kind),'UNSUPPORTED_PLAN');
  if(old?.current?.status==='cancelled')need(cmd.kind==='new','DRAFT_CANCELLED');
  const at=new Date().toISOString(),turnId='turn-'+randomUUID();
  let draft=old?clone(old):{format:'modelmirror.rpg.e3-draft/1',current:null,revisions:[],turns:[],operations:{},pending:null};
  need(draft.turns.length<30,'DRAFT_LIMIT');
  if(!['reply','edit','new'].includes(cmd.kind))need(draft.current,'DRAFT_MISSING');
  if(['edit','accept'].includes(cmd.kind)&&draft.current)need(hash(draft.current.source)===hash(sourceRef(world)),'SOURCE_STALE');
  if(cmd.kind==='reply'){
   const request=this.builder(world,draft.current,cmd.text);
   if(draft.current?.status==='accepted'){draft.revisions.push(clone(draft.current));draft.current={...draft.current,status:'review',confirmation:null};}
   draft.pending=cmd.operationId;draft.operations[cmd.operationId]={requestHash,status:'pending',request,at};
   const pending=await this.records.put(key,row?.hash??null,draft);
   try{
    const receipt=await this.provider.generate(request);
    draft.operations[cmd.operationId].receipt=receipt;
    if(receipt.status==='unknown'){draft.operations[cmd.operationId].status='unknown';await this.records.put(key,pending.hash,draft);return this.view();}
    need(receipt.status==='completed','PROPOSAL_INCOMPLETE');
    const result=parseReply(receipt.raw),after=await this.source.read();
    need(hash(sourceRef(after))===hash(sourceRef(world)),'SOURCE_STALE');
    const next=proposal(world,draft.current,result.changes,{...result,id:'p-'+randomUUID(),turnId,text:cmd.text});
    if(draft.current)draft.revisions.push(draft.current);
    draft.current=next;draft.turns.push({id:turnId,kind:'reply',text:cmd.text,revision:next.revision,at});
    draft.operations[cmd.operationId].status='completed';draft.pending=null;
   }catch(e){
    draft.operations[cmd.operationId].status=draft.operations[cmd.operationId].receipt?'failed':'unknown';
    draft.operations[cmd.operationId].error=e.code||e.message;
    if(draft.operations[cmd.operationId].status==='failed')draft.pending=null;
   }
   await this.records.put(key,pending.hash,draft);return this.view();
  }
  const previous=clone(draft.current);
  if(cmd.kind==='edit'){
   need(Array.isArray(cmd.changes)&&cmd.changes.length>0,'EDIT_EMPTY');
   draft.current=proposal(world,previous,cmd.changes.map(c=>({field:c.field,value:c.value,quote:'界面明确修改'})),{id:'p-'+randomUUID(),turnId,kind:'user-edit',
    unsupported:previous?.unsupported??false,questions:(previous?.questions??[]).filter(q=>q.field===null).map(q=>q.text)});
  }else if(cmd.kind==='new'){
   draft.current=proposal(world,null,[],{id:'p-'+randomUUID(),turnId,kind:'user-edit'});
  }else if(cmd.kind==='refresh'){
   draft.current=proposal(world,previous,[],{turnId,kind:'source-refresh',unsupported:previous.unsupported,questions:previous.questions.filter(q=>q.field===null).map(q=>q.text)});
  }else if(cmd.kind==='accept')draft.current=accept(previous,world);
  else if(cmd.kind==='revoke'){need(previous.status==='accepted','DRAFT_STATUS');draft.current={...previous,status:'review',confirmation:null};}
  else draft.current={...previous,status:'cancelled',confirmation:null};
  if(previous)draft.revisions.push(previous);
  draft.turns.push({id:turnId,kind:cmd.kind,text:cmd.kind==='edit'?'界面明确修改':cmd.kind,revision:draft.current.revision,at});
  draft.operations[cmd.operationId]={requestHash,status:'completed',at};
  need(hash(sourceRef(await this.source.read()))===hash(sourceRef(world)),'SOURCE_STALE');
  await this.records.put(key,row?.hash??null,draft);return this.view();
 }
 async export(){const {world,row}=await this.read();assertReady(world);need(row&&!row.data.pending,'DRAFT_NOT_READY');return exportProposal(row.data.current,world);}
 async reconcile(){const world=await this.source.read();await this.records.reconcile(this.key(world));return this.view();}
 close(){return this.records.close();}
}
