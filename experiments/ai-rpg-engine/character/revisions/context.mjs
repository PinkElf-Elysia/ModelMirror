import {assembleIdentity} from '../daily/identity.mjs';
import {assembleGroup} from '../group/context.mjs';
import {reservation} from '../group/contracts.mjs';
import {digest,id,copy,check,text,branch,at,boundary,prefixHash,derivedView} from './contracts.mjs';
import {fixtureModel} from './sources.mjs';
export function prepare(f,operationId,p,overrideModel=null){
 const b=branch(f,p.branchId);check(['send','regenerate'].includes(p.kind),'REQUEST_KIND');
 let prefix,context,model,targetId,originalMessageId=null,slotId,inputMessage=null,viewState;
 if(p.kind==='regenerate'){
  const n=at(b,p.messageId),m=b.messages[n];check(m.generation,'REGENERATION_UNAVAILABLE');prefix=copy(b.messages.slice(0,n));targetId=m.speakerId;originalMessageId=m.id;slotId=m.slotId;
  // Use the exact verified pre-generation messages, never the replacement or suffix.
  context=copy(m.generation.context);model=copy(m.generation.model);viewState=copy(m.boundary);
 }else{
  targetId=p.targetId;prefix=copy(b.messages);viewState=copy(boundary(b));const m=viewState.members.find(m=>m.id===targetId&&m.kind==='character');check(m&&m.intervals.at(-1).until===null,'MEMBER_INACTIVE');model=copy(m.model??fixtureModel());
  if(p.input!==null){text(p.input);inputMessage={id:id('message',f.id,operationId,'input'),revisionId:id('revision',f.id,operationId,'input'),speakerId:viewState.members[0].id,content:p.input,visibility:{kind:'public'},addressedTo:[targetId],provenance:{kind:'player-input'},generation:null,slotId:null,boundary:copy(viewState)};prefix.push(inputMessage);}
  const temporary={...copy(b),messages:prefix},card=f.source.cards[targetId]?.document.character.card;check(card,'VERSION_MISSING');
  const privateRecords=derivedView(temporary,targetId).filter(d=>d.status==='valid').map(d=>({id:d.id,ownerId:d.ownerId,content:d.content}));
  if(f.source.kind==='group')context=assembleGroup({...viewState,messages:prefix.map((m,i)=>({...m,sequence:i+1,source:m.provenance})),privateRecords},targetId,card).messages;
  else {const first=prefix[0],history=prefix.slice(1).map(m=>({role:m.speakerId===targetId?'assistant':'user',content:m.content}));context=assembleIdentity({card,player:viewState.members[0].name,opening:first?.content??'',history,input:'',memory:privateRecords.map(d=>({name:'本分支确认资料',content:d.content})),maxTokens:model.contextTokens-model.outputTokens},viewState.members[0].identity).messages;}
  slotId=id('slot',f.id,operationId);
 }
 if(overrideModel)model=copy(overrideModel);
 check(model,'MODEL_UNAVAILABLE');
 const r={sourceKind:f.source.kind,sourceId:f.source.id,...(overrideModel?{overrideModel:copy(overrideModel)}:{}),id:id('request',f.id,operationId),branchId:b.id,targetId,slotId,prefix,prefixHash:prefixHash(prefix),context,contextHash:digest(context),model,reservation:reservation(context,model),kind:p.kind,originalMessageId,inputMessage,boundary:viewState};
 r.hash=digest(r);return r;
}
