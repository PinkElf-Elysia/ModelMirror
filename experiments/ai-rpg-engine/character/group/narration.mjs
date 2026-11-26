import {isId,keys} from '../../authoring/contracts.mjs';
import {text} from '../conversation/contracts.mjs';
import {active,browserView,check,idFor,memberView,pending,replay} from './contracts.mjs';

export const narrationFormat='modelmirror.rpg.group-narration/1';

export function replayNarrated(record, length=record.events.length, verifiedNotes) {
  const {narration,...base}=record;
  const state=replay({...base,events:base.events.slice(0,length)});
  if (!('narration' in record)) return state;
  if(verifiedNotes){
    state.notes=verifiedNotes.filter(note=>note.atRevision<length);
    return state;
  }
  keys(narration,['format','entries']);
  check(narration.format===narrationFormat && Array.isArray(narration.entries) && narration.entries.length<=1000,'NARRATION_FORMAT');
  const operations=new Set();
  let last=-1;
  state.notes=[];
  for (const entry of narration.entries) {
    keys(entry,['operationId','atRevision','content']);
    check(isId(entry.operationId) && !operations.has(entry.operationId) &&
      Number.isSafeInteger(entry.atRevision) && entry.atRevision>=0 && entry.atRevision>=last &&
      entry.atRevision<base.events.length,'NARRATION_RECORD');
    text(entry.content,2000);
    last=entry.atRevision; operations.add(entry.operationId);
    if (entry.atRevision>=length) continue;
    const at=entry.atRevision===state.revision ? state : replay({...base,events:base.events.slice(0,entry.atRevision+1)});
    check(!pending(at),'REQUEST_PENDING');
    state.notes.push({id:idFor('note',state.id,entry.operationId),after:at.messages.length,atRevision:entry.atRevision,
      audience:at.members.filter(active).map(m=>m.id),content:entry.content,
      source:{kind:'manual-narration',reference:entry.operationId,authorId:at.members[0].id}});
  }
  return state;
}

// Preserve ordinary message sequence numbers for the original participation filter.
// Insert only explicitly eligible notes; the synthetic narrator never enters the member roster.
export function narrationContext(state,targetId) {
  if (!state.notes?.length) return state;
  const visible=state.notes.filter(n=>n.audience.includes(targetId));
  if (!visible.length) return state;
  const narrator={id:idFor('narrator',state.id),kind:'narrator',name:'旁白（玩家手动记录）',intervals:[{from:1,until:null}]};
  const messages=[],owner=state.members.find(m=>m.id===targetId);
  let cursor=0;
  for(let i=0;i<=state.messages.length;i++){
    while(cursor<visible.length && visible[cursor].after===i){
      const {id,content,source}=visible[cursor++];
      messages.push({id,content,source,speakerId:narrator.id,sequence:owner.intervals.at(-1).from,
        visibility:{kind:'public'},addressedTo:[]});
    }
    if(i<state.messages.length)messages.push(state.messages[i]);
  }
  return {...state,members:[...state.members,narrator],messages};
}
export function narratedMemberView(state,targetId) {
  return memberView(narrationContext(state,targetId),targetId);
}
export function groupPageView(state) {
  const view=browserView(state);
  if (state.notes) {
    view.messages=narratedMemberView(state,state.members[0].id).messages;
    view.narrationRevision=state.notes.length;
  }
  return view;
}
