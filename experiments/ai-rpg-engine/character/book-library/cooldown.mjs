import {id,visible} from '../revisions/contracts.mjs';

export const cooldownPolicy='member-adopted-replies/1';
export const cooldownField='modelmirror.cooldown';
export const hasCooldown=entry=>Object.hasOwn(entry.extensions??{},cooldownField);

// Progress follows this branch's visible, adopted send replies; wall time and attempts never advance it.
export function cooldownView(row,branchId,actor){
  const policy=row.tracks[branchId].cooldown;
  if(!policy)return null;
  const b=row.f.branches[branchId],eligible=new Set(b.messages.slice(policy.enabledAfter).map(m=>m.id));
  const last=new Map();
  let replies=0;
  for(const m of visible(b,actor)){
    if(m.speakerId!==actor||!eligible.has(m.id))continue;
    const rid=m.generation?.requestId,r=row.f.requests[rid];
    if(r?.kind!=='send'||r.status!=='completed'||r.targetId!==actor)continue;
    replies++;
    for(const key of row.plans[rid]?.cooldownKeys??[])last.set(key,{at:replies,messageId:m.id});
  }
  return {replies,last};
}

export function prepareCooldownBook(book,scope,actor,state,scopes=null){
  const traces=new Map();
  const entries=book.entries.map((entry,index)=>{
    if(!hasCooldown(entry))return entry;
    const rounds=entry.extensions[cooldownField];
    if(!Number.isSafeInteger(rounds)||rounds<0||rounds>1000){
      traces.set(index,{status:entry.enabled?'invalid-cooldown':'disabled'});
      return {...entry,enabled:false};
    }
    if(rounds===0)return entry;
    const cooldownKey=id('cooldown',actor,scopes?.[index]?.reference??scope,scopes?.[index]?.index??index),anchor=state.last.get(cooldownKey);
    const remaining=anchor?Math.max(0,rounds-(state.replies-anchor.at)):0;
    const blocked=entry.enabled&&remaining>0;
    traces.set(index,{cooldownKey,cooldown:{rounds,remaining,...(anchor?{messageId:anchor.messageId}:{})},...(blocked?{status:'cooldown-wait'}:{})});
    return blocked?{...entry,enabled:false}:entry;
  });
  return {book:{...book,entries},traces};
}
