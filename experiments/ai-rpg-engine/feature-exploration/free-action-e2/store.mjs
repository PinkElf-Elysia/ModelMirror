import {randomUUID} from 'node:crypto';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {need,hash,clone} from '../../agent-core/contracts.mjs';
import {confirm,handoff,sourceRef,digest} from '../free-action/proposal.mjs';
import {assertReady} from './source.mjs';
import {validateProposal} from './model.mjs';
const FORMAT='modelmirror.rpg.e2-draft/1';
export class ProposalStore{
 constructor(records,source,builder,provider){Object.assign(this,{records,source,builder,provider});}
 static async open(directory,{source,builder,provider,recoverDeadOwner=false}){
  return new ProposalStore(await SessionRecords.open(directory,{scope:'e2-free-action',recoverDeadOwner}),source,builder,provider);
 }
 key(world){return 'e2-'+hash(world.session).slice(0,40);}
 async view(){
  const world=await this.source.read();
  const row=await this.records.get(this.key(world),r=>need(r.format===FORMAT&&r.current,'DRAFT_CORRUPT'));
  const stale=!!row&&digest(row.data.current.source)!==digest(sourceRef(world));
  return {world,recordHash:row?.hash??null,draft:row?.data??null,stale};
 }
 async revise(input,expectedHash){
  const before=await this.view();assertReady(before.world);need(before.recordHash===expectedHash,'DRAFT_CONFLICT');
  const request=this.builder(before.world,input),receipt=await this.provider.generate(request);
  need(receipt.status==='completed','PROPOSAL_INCOMPLETE');
  const current=validateProposal(before.world,request,receipt.raw,{id:before.draft?.current.id??randomUUID(),revision:(before.draft?.current.revision??0)+1});
  const after=await this.source.read();assertReady(after);need(digest(sourceRef(after))===digest(sourceRef(before.world)),'SOURCE_STALE');
  const record={format:FORMAT,current,request,receipt,revisions:[...(before.draft?.revisions??[]),...(before.draft?[{current:before.draft.current,request:before.draft.request,receipt:before.draft.receipt}]:[])],
   audit:[...(before.draft?.audit??[]),{kind:'revised',revision:current.revision,at:new Date().toISOString()}]};
  need(record.revisions.length<=30,'DRAFT_LIMIT');await this.records.put(this.key(after),expectedHash,record);return this.view();
 }
 async quantity(quantity,expectedHash,proposalHash){
  const {world,recordHash,draft}=await this.view();assertReady(world);
  need(draft&&recordHash===expectedHash&&draft.current.hash===proposalHash,'DRAFT_CONFLICT');
  need(digest(draft.current.source)===digest(sourceRef(world)),'SOURCE_STALE');
  need(Number.isSafeInteger(quantity)&&quantity>0&&draft.current.requirements.length===1,'PROPOSAL_QUANTITY');
  const original=draft.receipt.raw,edited=JSON.parse(original);
  edited.requirements[0].quantity=quantity;
  const current=validateProposal(world,draft.request,JSON.stringify(edited),{id:draft.current.id,revision:draft.current.revision+1,quantityOverride:{itemId:edited.requirements[0].itemId,quantity}});
  current.originalGoal=draft.current.originalGoal??draft.current.goal;
  current.goal='修订方案用量：'+world.state.items.find(i=>i.id===edited.requirements[0].itemId).name+' '+quantity+'份';
  current.localEdit={kind:'quantity-only',quantity,originalProviderReplyHash:hash(original),note:'User quantity edit; no provider invocation.'};
  current.hash=hash({...current,hash:null});
  need(draft.revisions.length<30,'DRAFT_LIMIT');
  await this.records.put(this.key(world),recordHash,{...draft,current,revisions:[...draft.revisions,{current:draft.current,request:draft.request,receipt:draft.receipt}],audit:[...draft.audit,{kind:'quantity-only',quantity,at:new Date().toISOString()}]});
  return this.view();
 }
 async action(command,expectedHash,proposalHash){
  const {world,recordHash,draft}=await this.view();need(draft,'DRAFT_MISSING');
  need(['confirm','revoke','cancel'].includes(command),'COMMAND_UNSUPPORTED');
  if(command==='confirm'){assertReady(world);if(draft.current.status==='awaiting_execution'&&proposalHash===draft.current.hash){confirm(draft.current,world,proposalHash,'unused');return this.view();}}
  need(recordHash===expectedHash&&draft.current.hash===proposalHash,'DRAFT_CONFLICT');
  let current;
  if(command==='confirm')current=confirm(draft.current,world,proposalHash,new Date().toISOString());
  else{need(command==='revoke'?draft.current.status==='awaiting_execution':draft.current.status!=='awaiting_execution','DRAFT_STATUS');
   current={...clone(draft.current),status:command==='revoke'?'revoked':'cancelled',confirmation:null};}
  await this.records.put(this.key(world),recordHash,{...draft,current,audit:[...draft.audit,{kind:command,at:new Date().toISOString()}]});
  return this.view();
 }
 async export(){const {world,draft}=await this.view();assertReady(world);need(draft,'DRAFT_MISSING');
  return {...handoff(draft.current,world),format:'modelmirror.rpg.e2-handoff/1',notice:'仅提案交接，未执行；执行前须重新核对状态、权限、规则与用户意图。'};}
 async reconcile(){const world=await this.source.read();await this.records.reconcile(this.key(world));return this.view();}
 close(){return this.records.close();}
}
