// Internal data contract, never a provider wire contract. No I/O or model execution.
import {canonical,sha256,keys,isId,isHash,fail} from '../../authoring/contracts.mjs';
export const FORMAT='modelmirror.rpg.character-conversation/1';
export const VIEW='modelmirror.rpg.character-member-view/1';
export const LIMITS=Object.freeze({events:1000,members:32,text:200000});
export const digest=value=>sha256(canonical(value));
export const clone=structuredClone;
export const idFor=(kind,...parts)=>kind+'-'+digest(parts);
const id=(value,kind)=>typeof value==='string'&&new RegExp('^'+kind+'-[a-f0-9]{64}$').test(value);
const check=(ok,code)=>{if(!ok)fail('CONVERSATION_'+code);};
export function text(value,maximum=LIMITS.text){check(typeof value==='string'&&value.isWellFormed()&&value.trim().length>0&&value.length<=maximum,'TEXT_INVALID');}
export function player(value){keys(value,['id','name']);check(value.id===null||typeof value.id==='string'&&value.id.length<=200,'PLAYER_INVALID');text(value.name,120);}
export function reference(value){keys(value,['workId','versionId']);check(id(value.workId,'work')&&id(value.versionId,'version'),'VERSION_REQUIRED');}
export function memberOf(state,memberId){const m=state.members.find(m=>m.id===memberId);check(!!m,'MEMBER_NOT_FOUND');return m;}
export const active=m=>m.intervals.at(-1).until===null;
export const participated=(m,sequence)=>m.intervals.some(i=>sequence>=i.from&&(i.until===null||sequence<i.until));
export function intention(action,input){canonical(input);keys(input,['operationId','expectedRevision','payload']);check(isId(input.operationId),'OPERATION_INVALID');return {action,...clone(input)};}
function listMembers(state,values){check(Array.isArray(values)&&new Set(values).size===values.length,'RECIPIENT_INVALID');for(const v of values)memberOf(state,v);}
function apply(state,event,index){
 keys(event,['action','operationId','expectedRevision','payload']);check(isId(event.operationId),'OPERATION_INVALID');
 check(event.expectedRevision===(index===0?null:index-1),'REVISION_CONFLICT');const p=event.payload;
 const receipt={conversationId:state.id,revision:index};
 if(index===0){
  check(event.action==='create','CREATE_REQUIRED');keys(p,['player']);player(p.player);
  check(state.id===idFor('conversation',event.operationId),'ID_INVALID');
  const memberId=idFor('member',state.id,event.operationId,'player');
  state.members.push({id:memberId,kind:'player',name:p.player.name,identity:clone(p.player),binding:null,intervals:[{from:1,until:null}]});receipt.memberId=memberId;return receipt;
 }
 if(event.action==='join'){
  keys(p,['reference','configurationHash','name']);reference(p.reference);check(isHash(p.configurationHash),'BINDING_INVALID');text(p.name,120);check(state.members.length<LIMITS.members,'MEMBER_LIMIT');
  const memberId=idFor('member',state.id,event.operationId,'character');
  state.members.push({id:memberId,kind:'character',name:p.name,identity:null,binding:{reference:clone(p.reference),configurationHash:p.configurationHash,worldInstanceRef:null},intervals:[{from:state.messages.length+1,until:null}]});receipt.memberId=memberId;
 }else if(event.action==='leave'||event.action==='rejoin'){
  keys(p,['memberId']);const m=memberOf(state,p.memberId);check(m.kind==='character','PLAYER_FIXED');
  check(active(m)===(event.action==='leave'),'PRESENCE_CONFLICT');
  if(event.action==='leave')m.intervals.at(-1).until=state.messages.length+1;else m.intervals.push({from:state.messages.length+1,until:null});receipt.memberId=m.id;
 }else if(event.action==='append'||event.action==='opening'){
  keys(p,event.action==='opening'?['speakerId','content','openingIndex']:['speakerId','content','visibility','addressedTo','source']);
  const m=memberOf(state,p.speakerId);check(active(m),'MEMBER_INACTIVE');text(p.content);
  let visibility,addressedTo,source;
  if(event.action==='opening'){
   check(m.kind==='character'&&Number.isSafeInteger(p.openingIndex)&&p.openingIndex>=0,'OPENING_INVALID');
   check(!state.messages.some(x=>x.speakerId===m.id),'OPENING_EXISTS');
   visibility={kind:'public'};addressedTo=[];source={kind:'opening',openingIndex:p.openingIndex};
  }else{
   visibility=clone(p.visibility);check(['public','members'].includes(visibility?.kind),'VISIBILITY_INVALID');keys(visibility,visibility.kind==='public'?['kind']:['kind','memberIds']);
   if(visibility.kind==='members'){listMembers(state,visibility.memberIds);check(visibility.memberIds.length>0&&visibility.memberIds.includes(m.id),'RECIPIENT_INVALID');}
   listMembers(state,p.addressedTo);addressedTo=clone(p.addressedTo);
   source=clone(p.source);keys(source,['kind','reference']);check(source.reference===null||isId(source.reference),'SOURCE_INVALID');
   check(source.kind==='player-input'&&m.kind==='player'||source.kind==='fixture'&&state.mode==='fixture','SOURCE_UNSUPPORTED');
  }
  const messageId=idFor('message',state.id,event.operationId);
  state.messages.push({id:messageId,sequence:state.messages.length+1,speakerId:m.id,content:p.content,visibility,addressedTo,source});receipt.messageId=messageId;
 }else if(event.action==='private'){
  keys(p,['ownerId','content','source']);const m=memberOf(state,p.ownerId);check(active(m),'MEMBER_INACTIVE');text(p.content);keys(p.source,['kind','reference']);check(p.source.kind==='manual'&&(p.source.reference===null||isId(p.source.reference)),'SOURCE_UNSUPPORTED');
  const privateId=idFor('private',state.id,event.operationId);state.privateRecords.push({id:privateId,...clone(p)});receipt.privateId=privateId;
 }else check(false,'ACTION_UNSUPPORTED');
 return receipt;
}
export function replay(record){
 canonical(record);keys(record,['format','id','mode','runtimeHash','events']);check(record.format===FORMAT,'FORMAT_UNSUPPORTED');check(id(record.id,'conversation'),'ID_INVALID');check(['foundation','fixture'].includes(record.mode)&&isHash(record.runtimeHash),'RUNTIME_INVALID');
 check(Array.isArray(record.events)&&record.events.length>=1&&record.events.length<=LIMITS.events,'EVENT_LIMIT');
 const state={format:FORMAT,id:record.id,revision:record.events.length-1,worldInstanceRef:null,mode:record.mode,members:[],messages:[],privateRecords:[]};const seen=new Set(),receipts=[];
 for(const [i,e] of record.events.entries()){keys(e,['action','operationId','expectedRevision','payload']);check(!seen.has(e.operationId),'OPERATION_COLLISION');seen.add(e.operationId);receipts.push(apply(state,e,i));}
 return {state,receipts};
}
export function appendEvent(record,event){
 const current=replay(record),prior=record.events.findIndex(e=>e.operationId===event.operationId);
 if(prior>=0){check(canonical(record.events[prior])===canonical(event),'OPERATION_COLLISION');return {record:clone(record),receipt:current.receipts[prior],changed:false};}
 const next={...clone(record),events:[...clone(record.events),clone(event)]},result=replay(next);
 return {record:next,receipt:result.receipts.at(-1),changed:true};
}
export function projectMember(state,memberId){
 const owner=memberOf(state,memberId);check(active(owner),'MEMBER_INACTIVE');
 const messages=state.messages.filter(m=>participated(owner,m.sequence)&&(m.visibility.kind==='public'||m.visibility.memberIds.includes(memberId)));
 // Deliberately omit other bindings, private records, operation logs, revision and hidden message counts.
 const {intervals,...own}=owner;const visibleIds=new Set(messages.flatMap(m=>[m.speakerId,...m.addressedTo]));
 return clone({format:VIEW,conversationId:state.id,member:own,
  participants:state.members.filter(m=>m.id===memberId||active(m)||visibleIds.has(m.id)).map(m=>({id:m.id,kind:m.kind,name:m.name})),
  messages:messages.map(({sequence,visibility,...m})=>m),privateRecords:state.privateRecords.filter(p=>p.ownerId===memberId)});
}
