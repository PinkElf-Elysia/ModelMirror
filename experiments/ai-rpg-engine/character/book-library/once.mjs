// Branch state is private. Only the entry definition goes through card exchange.
import {copy,id,pending} from '../revisions/contracts.mjs';

export const oncePolicy='once-on-history/1';
const directive=/^@@dont_activate_after_match[ \t]*\r?\n/;
export function onceBody(content){
  const header=directive.exec(content);
  if(!header)return null;
  const body=content.slice(header[0].length);
  // Leave combinations, fallback chains and empty entries to the unsupported path.
  return body.trim()&&!/^\s*@@/m.test(body)?body:null;
}

export function forkOnce(parent,point,tip,enable){
  if(parent&&(tip||point>parent.enabledAfter)){
    return {enabledAfter:parent.enabledAfter,uses:copy(parent.uses.filter(use=>use.position<=point))};
  }
  return enable?{enabledAfter:point,uses:[]}:null;
}

// A pending claim is part of the prepared request, not part of the historical timeline.
export function onceView(row,branchId){
  const state=row.tracks[branchId].once;
  if(!state)return null;
  const entries=new Map();
  for(const use of state.uses){
    for(const key of use.keys)entries.set(key,{status:'once-used',messageId:use.messageId});
  }
  const request=pending(row.f);
  if(request?.branchId===branchId){
    for(const key of row.plans[request.id]?.onceKeys??[])entries.set(key,{status:'once-held'});
  }
  return entries;
}

export function prepareOnceBook(book,scope,actor,state){
  const rules=new Map();
  const entries=book.entries.map((entry,index)=>{
    const body=onceBody(entry.content);
    if(body===null)return entry;
    const key=id('once',actor,scope,index),previous=state.get(key);
    rules.set(index,{key,previous});
    return {...entry,content:body,enabled:entry.enabled&&!previous};
  });
  return {book:{...book,entries},rules};
}
