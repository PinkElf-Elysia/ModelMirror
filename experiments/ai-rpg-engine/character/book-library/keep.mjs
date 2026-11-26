import {id,visible} from '../revisions/contracts.mjs';
import {timingHeader} from './timing.mjs';
import {depthHeader} from './depth.mjs';

export const keepPolicy='keep-after-first-adopted-match/1';
export function keepHeader(content){
  if(!content.startsWith('@@'))return null;
  let offset=0,found=false,invalid=false;
  const others=[];
  while(content.startsWith('@@',offset)){
    const end=content.indexOf('\n',offset),next=end<0?content.length:end+1;
    const raw=content.slice(offset,next),line=raw.replace(/\r?\n$/,'');
    if(/^@@keep_activate_after_match(?:\s|$)/.test(line)){
      if(found||!/^@@keep_activate_after_match[ \t]*$/.test(line))invalid=true;
      found=true;
    }else others.push(raw);
    offset=next;
  }
  if(!found)return null;
  const body=content.slice(offset),remaining=others.join('')+body;
  if(invalid||!body.trim()||/^\s*@@/m.test(body)||/^@@dont_activate_after_match\b/m.test(remaining))return {invalid:true};
  if(others.length){
    const timing=timingHeader(remaining),depth=depthHeader(remaining);
    if(!(timing&&!timing.invalid)&&!Number.isInteger(depth?.depth))return {invalid:true};
  }
  return {remaining};
}

// Only adopted replies on this member's visible branch prefix can prove a previous match.
export function keepView(row,branchId,actor){
  const policy=row.tracks[branchId].keep;
  if(!policy)return null;
  const b=row.f.branches[branchId],prior=new Map();
  const eligible=new Set(b.messages.slice(policy.enabledAfter).map(m=>m.id));
  for(const m of visible(b,actor)){
    if(m.speakerId!==actor||!eligible.has(m.id))continue;
    const rid=m.generation?.requestId;
    if(!rid||row.f.requests[rid]?.status!=='completed')continue;
    for(const key of row.plans[rid]?.keepKeys??[])if(!prior.has(key))prior.set(key,m.id);
  }
  return prior;
}

export function prepareKeepBook(book,scope,actor,prior,scopes=null){
  const traces=new Map();
  const entries=book.entries.map((entry,index)=>{
    const rule=keepHeader(entry.content);
    if(!rule)return entry;
    if(rule.invalid){
      traces.set(index,{status:entry.enabled?'invalid-keep':'disabled'});
      return {...entry,enabled:false};
    }
    const keepKey=id('keep',actor,scopes?.[index]?.reference??scope,scopes?.[index]?.index??index),anchor=prior.get(keepKey);
    traces.set(index,{keepKey,...(anchor?{keepAnchor:anchor}:{})});
    return {...entry,content:rule.remaining,...(anchor?{constant:true,use_regex:false}:{})};
  });
  return {book:{...book,entries},traces};
}
