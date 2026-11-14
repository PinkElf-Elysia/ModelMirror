import {join} from 'node:path';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {AuthoringStore} from '../../authoring/store.mjs';
import {keys,fail} from '../../authoring/contracts.mjs';
import {greeting} from '../context.mjs';
import {FORMAT,digest,clone,idFor,intention,replay,appendEvent,projectMember,memberOf,reference} from './contracts.mjs';
import {runtimeDescriptor} from './runtime.mjs';
const cid=id=>{if(typeof id!=='string'||!/^conversation-[a-f0-9]{64}$/.test(id))fail('CONVERSATION_ID_INVALID');};
/** Host-only service. All methods are privileged; member views are not authentication. */
export class ConversationStore {
 #records;#works;#runtime;#mode;#ready=false;#closed=false;#queue=Promise.resolve();
 constructor(records,works,runtime,mode){this.#records=records;this.#works=works;this.#runtime=runtime;this.#mode=mode;}
 static async open({directory,works,fixtureMode=false,recoverDeadOwner=false,fault}){
  if(!(works instanceof AuthoringStore)||typeof fixtureMode!=='boolean')fail('CONVERSATION_HOST_REQUIRED');
  const runtime=await runtimeDescriptor();
  const records=await SessionRecords.open(join(directory,'records'),{scope:'character-conversations-v1',recoverDeadOwner,...(fault?{fault}:{})});
  return new ConversationStore(records,works,runtime,fixtureMode?'fixture':'foundation');
 }
 #serial(fn){if(this.#closed)fail('CONVERSATION_STORE_CLOSED');const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #verify(){if((await runtimeDescriptor()).hash!==this.#runtime.hash)fail('CONVERSATION_RUNTIME_CHANGED');}
 async #configuration(ref){reference(ref);const c=await this.#works.verifyConfiguration(ref.workId,ref.versionId);if(c.document.kind!=='character')fail('CONVERSATION_CHARACTER_REQUIRED');return c;}
 async #validate(data){
  const result=replay(data),player=result.state.members[0].identity;
  if(data.runtimeHash!==this.#runtime.hash||data.mode!==this.#mode)fail('CONVERSATION_RUNTIME_MISMATCH');
  for(const member of result.state.members.filter(m=>m.kind==='character')){
   const c=await this.#configuration(member.binding.reference);
   if(digest(c)!==member.binding.configurationHash||c.document.character.card.data.name.slice(0,120)!==member.name)fail('CONVERSATION_VERSION_CHANGED');
   for(const m of result.state.messages.filter(m=>m.speakerId===member.id&&m.source.kind==='opening'))
    if(m.content!==greeting(c.document.character.card,m.source.openingIndex,player.name))fail('CONVERSATION_OPENING_CHANGED');
  }
  return result;
 }
 async #load(id){cid(id);if(!this.#ready)fail('CONVERSATION_RECOVERY_REQUIRED');await this.#verify();const r=await this.#records.get(id);if(!r)fail('CONVERSATION_NOT_FOUND');return {...r,...await this.#validate(r.data)};}
 async #put(id,hash,data){try{return await this.#records.put(id,hash,data);}catch(e){this.#ready=false;throw e;}}
 recover(expectedId=null){if(expectedId!==null)cid(expectedId);return this.#serial(async()=>{this.#ready=false;await this.#verify();const result=await this.#records.reconcile(expectedId);if(result){const r=await this.#records.get(result.id);if(r)await this.#validate(r.data);}this.#ready=true;return result;});}
 create(input){const e=intention('create',input);return this.#serial(async()=>{
  if(!this.#ready)fail('CONVERSATION_RECOVERY_REQUIRED');await this.#verify();
  const id=idFor('conversation',e.operationId),old=await this.#records.get(id);
  if(old){const {receipts}=await this.#validate(old.data);if(digest(old.data.events[0])!==digest(e))fail('CONVERSATION_OPERATION_COLLISION');return clone(receipts[0]);}
  const data={format:FORMAT,id,mode:this.#mode,runtimeHash:this.#runtime.hash,events:[e]},result=await this.#validate(data);
  await this.#put(id,null,data);return clone(result.receipts[0]);
 });}
 // Full owner read must never be used as a member/model projection.
 readOwner(id){return this.#serial(async()=>clone((await this.#load(id)).state));}
 readMember(id,memberId){return this.#serial(async()=>projectMember((await this.#load(id)).state,memberId));}
 #change(id,action,input,prepare=async p=>p){
  const intent=intention(action,input);return this.#serial(async()=>{
   const r=await this.#load(id);const event={...intent,payload:await prepare(clone(intent.payload),r.state)};
   const next=appendEvent(r.data,event);if(next.changed){await this.#validate(next.record);await this.#put(id,r.hash,next.record);}return clone(next.receipt);
  });
 }
 join(id,input){return this.#change(id,'join',input,async p=>{keys(p,['reference']);const c=await this.#configuration(p.reference);return {reference:p.reference,configurationHash:digest(c),name:c.document.character.card.data.name.slice(0,120)};});}
 leave(id,input){return this.#change(id,'leave',input);}
 rejoin(id,input){return this.#change(id,'rejoin',input);}
 append(id,input){return this.#change(id,'append',input);}
 appendOpening(id,input){return this.#change(id,'opening',input,async(p,state)=>{
  keys(p,['speakerId','openingIndex']);const member=memberOf(state,p.speakerId);if(member.kind!=='character')fail('CONVERSATION_OPENING_INVALID');
  const c=await this.#configuration(member.binding.reference);return {...p,content:greeting(c.document.character.card,p.openingIndex,state.members[0].identity.name)};
 });}
 addPrivate(id,input){return this.#change(id,'private',input);}
 async close(){if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close();}
}
