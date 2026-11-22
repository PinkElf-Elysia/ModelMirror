import {depthHeader} from './depth.mjs';
import {onceBody} from './once.mjs';

export const timingPolicy='visible-character-messages/1';
const timingLine=/^@@activate_only_(after|every)(?:[ \t]|$)/;

export function timingHeader(content){
  if(!content.startsWith('@@'))return null;
  const conditions={},other=[];
  let body=content,recognized=false,invalid=false;
  while(body.startsWith('@@')){
    const end=body.indexOf('\n'),raw=end<0?body:body.slice(0,end+1);
    const line=raw.trimEnd();
    body=end<0?'':body.slice(end+1);
    if(!timingLine.test(line)){other.push(raw);continue;}
    recognized=true;
    const match=/^@@activate_only_(after|every)[ \t]+(0|[1-9]\d*)$/.exec(line);
    if(!match){invalid=true;continue;}
    const [,kind,digits]=match,n=Number(digits);
    if(Object.hasOwn(conditions,kind)||!Number.isSafeInteger(n)||(kind==='every'&&n===0))invalid=true;
    conditions[kind]=n;
  }
  if(!recognized)return null;
  const remaining=other.join('')+body;
  if(!body.trim()||/^\s*@@/m.test(body)||invalid)return {invalid:true};
  if(other.length&&!Number.isInteger(depthHeader(remaining)?.depth)&&onceBody(remaining)===null)return {invalid:true};
  return {...conditions,remaining};
}

export function prepareTimedBook(book,count){
  const traces=new Map();
  const entries=book.entries.map((entry,index)=>{
    const rule=timingHeader(entry.content);
    if(!rule)return entry;
    if(rule.invalid){
      traces.set(index,{status:entry.enabled?'invalid-timing':'disabled'});
      return {...entry,enabled:false};
    }
    const {remaining,...conditions}=rule;
    const timing={count,...conditions};
    let status;
    if(!entry.enabled)status='disabled';
    else if(count<(rule.after??0))status='timing-delay';
    else if(rule.every&&count%rule.every!==0)status='timing-interval';
    traces.set(index,status?{timing,status}:{timing});
    return {...entry,content:remaining,enabled:entry.enabled&&!status};
  });
  return {book:{...book,entries},traces};
}
