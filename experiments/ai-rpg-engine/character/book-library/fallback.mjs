import {keepHeader} from './keep.mjs';
import {depthHeader} from './depth.mjs';
import {timingHeader} from './timing.mjs';
import {onceBody} from './once.mjs';

export const fallbackPolicy='ordered-decorator-fallback/1';
export const hasFallback=content=>/^@@@/m.test(content);

function capability(name,line,policies){
  const probe=line+'\nx';
  switch(name){
    case 'keep_activate_after_match':
      if(!policies.keep)return 'unknown'; // Preserve the exact diagnostics of older branches.
      return keepHeader(probe)?.remaining?'available':'invalid';
    case 'scan_depth':
      if(depthHeader(probe)?.depth===undefined)return 'invalid';
      return policies.depth?'available':'off';
    case 'activate_only_after':
    case 'activate_only_every':{
      const timing=timingHeader(probe);
      if(!timing||timing.invalid)return 'invalid';
      return policies.timing?'available':'off';
    }
    case 'dont_activate_after_match':
      if(onceBody(probe)===null)return 'invalid';
      return policies.once?'available':'off';
    default:return 'unknown';
  }
}

// Resolve capability before matching. A selected gate's outcome can never advance this chain.
export function resolveFallback(content,policies){
  if(!hasFallback(content))return null;
  const groups=[];
  let offset=0,group;
  while(content.startsWith('@@',offset)){
    const end=content.indexOf('\n',offset);
    if(end<0||end-offset>512)return {status:'invalid-fallback'};
    const line=content.slice(offset,end).trimEnd();
    const match=/^(@{2,3})([a-z][a-z0-9_]*)(?:[ \t]+.*)?$/.exec(line);
    if(!match)return {status:'invalid-fallback'};
    if(match[1]==='@@'){
      if(groups.length===16)return {status:'fallback-limit'};
      group=[];groups.push(group);
    }
    if(!group)return {status:'invalid-fallback'};
    if(group.length===9)return {status:'fallback-limit'};
    group.push({name:match[2],line:line.slice(match[1].length-2)});
    offset=end+1;
  }
  const body=content.slice(offset);
  if(!body.trim()||/^\s*@@/m.test(body))return {status:'invalid-fallback'};
  const headers=new Map(),fallback=[];
  for(const chain of groups){
    const candidates=[];
    let selected=null;
    for(const option of chain){
      const reason=capability(option.name,option.line,policies);
      candidates.push({name:option.name,reason});
      if(reason!=='available')continue;
      selected=candidates.length-1;
      if(!headers.has(option.name))headers.set(option.name,option.line);
      break;
    }
    fallback.push({candidates,selected});
    if(selected===null)return {status:'fallback-unavailable',fallback};
  }
  return {content:[...headers.values(),body].join('\n'),fallback};
}

export function prepareFallbackBook(book,policies){
  const traces=new Map();
  const entries=book.entries.map((entry,index)=>{
    const result=resolveFallback(entry.content,policies);
    if(!result)return entry;
    const {content,...trace}=result;
    if(!entry.enabled)trace.status='disabled';
    traces.set(index,trace);
    return content===undefined?{...entry,enabled:false}:{...entry,content};
  });
  return {book:{...book,entries},traces};
}
