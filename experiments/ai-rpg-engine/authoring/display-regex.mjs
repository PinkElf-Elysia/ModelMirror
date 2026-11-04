import {Worker,isMainThread,parentPort,workerData} from 'node:worker_threads';
import {createHash} from 'node:crypto';

export const DISPLAY_REGEX_LIMITS=Object.freeze({rules:100,sourceBytes:2*1024*1024,matches:100000,timeoutMs:1000,workers:4});
const digest=value=>createHash('sha256').update(value).digest('hex');
const issue=(code,ruleId=null)=>Object.assign(new Error(code),{code,ruleId});
const text=value=>typeof value==='string'&&value.isWellFormed();
const scopes=new Set(['assistant','user','all']);
const fieldLimit=(value,maximum)=>text(value)&&value.length<=maximum*2&&[...value].length<=maximum;
export function validateDisplayRules(rules){
  if(!Array.isArray(rules)||rules.length>DISPLAY_REGEX_LIMITS.rules)throw issue('DISPLAY_REGEX_CONFIG');
  const ids=new Set();
  for(const rule of rules){
    if(!rule||typeof rule!=='object'||!text(rule.id)||!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$/.test(rule.id)||ids.has(rule.id)||!fieldLimit(rule.name,120)||typeof rule.enabled!=='boolean'||!fieldLimit(rule.pattern,4000)||!fieldLimit(rule.replacement,40000)||!text(rule.flags)||!/^[gimsu]*$/.test(rule.flags)||new Set(rule.flags).size!==rule.flags.length||(Object.hasOwn(rule,'scope')&&!scopes.has(rule.scope)))throw issue('DISPLAY_REGEX_CONFIG');
    if(Object.keys(rule).some(key=>!['id','name','enabled','pattern','flags','replacement','scope'].includes(key)))throw issue('DISPLAY_REGEX_CONFIG');
    ids.add(rule.id);
  }
}
// ECMAScript replacement tokens are expanded as data. No replacement functions
// or generated code are accepted. Append limits are checked before allocating
// the complete result, including empty matches and prefix/suffix substitutions.
function substitute(template,match,source,append){
  let cursor=0;
  for(let i=0;i<template.length;i++){
    if(template[i]!=='$'||i+1===template.length)continue;
    const next=template[i+1];let value,end=i+2;
    if(next==='$')value='$';
    else if(next==='&')value=match[0];
    else if(next==='`')value=source.slice(0,match.index);
    else if(next==="'")value=source.slice(match.index+match[0].length);
    else if(next==='<'&&match.groups!==undefined){const close=template.indexOf('>',i+2);if(close>=0){value=Object.hasOwn(match.groups,template.slice(i+2,close))?match.groups[template.slice(i+2,close)]??'':'';end=close+1;}}
    else if(/[0-9]/.test(next)){
      let n=Number(next);const second=template[i+2];
      if(second!==undefined&&/[0-9]/.test(second)&&Number(next+second)>0&&Number(next+second)<match.length){n=Number(next+second);end++;}
      if(n>0&&n<match.length)value=match[n]??'';
    }
    if(value===undefined)continue;
    append(template.slice(cursor,i));append(value);i=end-1;cursor=end;
  }
  append(template.slice(cursor));
}
function replaceBounded(source,rule){
  if(rule.pattern==='')throw issue('DISPLAY_REGEX_EMPTY',rule.id);
  let expression;try{expression=new RegExp(rule.pattern,rule.flags);}catch{throw issue('DISPLAY_REGEX_SYNTAX',rule.id);}
  const chunks=[];let bytes=0,cursor=0,matches=0,match;
  const append=value=>{bytes+=Buffer.byteLength(value);if(bytes>DISPLAY_REGEX_LIMITS.sourceBytes)throw issue('DISPLAY_REGEX_CAPACITY',rule.id);if(value)chunks.push(value);};
  while((match=expression.exec(source))!==null){
    if(++matches>DISPLAY_REGEX_LIMITS.matches)throw issue('DISPLAY_REGEX_MATCH_LIMIT',rule.id);
    append(source.slice(cursor,match.index));substitute(rule.replacement,match,source,append);cursor=match.index+match[0].length;
    if(!expression.global)break;
    if(match[0]===''){const p=expression.lastIndex;expression.lastIndex=p+(expression.unicode&&source.codePointAt(p)>0xffff?2:1);}
  }
  append(source.slice(cursor));return {display:chunks.join(''),matches};
}
if(!isMainThread&&workerData?.job==='display-regex'){
  let current=null;
  try{
    let display=workerData.source;
    for(const rule of workerData.rules){
      if(!rule.enabled||(rule.scope??'assistant')!=='all'&&(rule.scope??'assistant')!==workerData.role)continue;
      current=rule.id;parentPort.postMessage({kind:'start',id:current});
      const result=replaceBounded(display,rule),changed=result.display!==display;
      if(!result.display.isWellFormed())throw issue('DISPLAY_REGEX_UNICODE',rule.id);
      display=result.display;
      parentPort.postMessage({kind:'entry',entry:{id:rule.id,status:'applied',matches:result.matches,changed}});
    }
    parentPort.postMessage({kind:'result',display});
  }catch(error){parentPort.postMessage({kind:'failure',code:error.code||'DISPLAY_REGEX_RESOURCE_LIMIT',ruleId:error.ruleId||current});}
  parentPort.close();
}
let workers=0;
export async function projectDisplayRules(source,rules,{role='assistant',timeoutMs=DISPLAY_REGEX_LIMITS.timeoutMs}={}){
  validateDisplayRules(rules);
  if(!text(source)||Buffer.byteLength(source)>DISPLAY_REGEX_LIMITS.sourceBytes||!['assistant','user'].includes(role)||!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>2000)throw issue('DISPLAY_REGEX_INPUT');
  const report={role,sourceHash:digest(source),rulesHash:digest(JSON.stringify(rules.map(rule=>[rule.id,rule.name,rule.enabled,rule.pattern,rule.flags,rule.replacement,rule.scope??'assistant']))),applied:false,entries:rules.map(rule=>({id:rule.id,name:rule.name,status:!rule.enabled?'disabled':(rule.scope??'assistant')!=='all'&&(rule.scope??'assistant')!==role?'scope':'pending',matches:0,changed:false}))};
  const failed=(code,ruleId=null)=>({display:source,report:{...report,applied:false,rolledBack:true,entries:report.entries.map(entry=>entry.id===ruleId?{...entry,status:'failed'}:entry.status==='applied'?{...entry,status:'rolled-back'}:entry)},diagnostics:[{code,ruleId,detail:(ruleId?'规则 '+ruleId+'：':'')+'整批显示替换未生效，保留替换前的显示副本。'}]});
  if(!report.entries.some(entry=>entry.status==='pending'))return {display:source,report:{...report,applied:true},diagnostics:[]};
  if(workers>=DISPLAY_REGEX_LIMITS.workers)return failed('DISPLAY_REGEX_BUSY');
  workers++;
  return new Promise(resolve=>{
    let worker,timer,settled=false,current=null;
    const finish=async result=>{if(settled)return;settled=true;clearTimeout(timer);try{await worker?.terminate();}catch{}workers--;resolve(result);};
    try{
      worker=new Worker(new URL(import.meta.url),{workerData:{job:'display-regex',source,rules,role},env:{},execArgv:[],resourceLimits:{maxOldGenerationSizeMb:64,maxYoungGenerationSizeMb:16,stackSizeMb:4}});
      timer=setTimeout(()=>void finish(failed('DISPLAY_REGEX_TIMEOUT',current)),timeoutMs);
      worker.on('message',message=>{
        if(settled)return;
        if(message.kind==='start')current=message.id;
        else if(message.kind==='entry'){const i=report.entries.findIndex(entry=>entry.id===message.entry.id);if(i>=0)report.entries[i]={...report.entries[i],...message.entry};}
        else if(message.kind==='result')void finish({display:message.display,report:{...report,applied:true},diagnostics:[]});
        else if(message.kind==='failure')void finish(failed(message.code,message.ruleId));
      });
      worker.once('error',()=>void finish(failed('DISPLAY_REGEX_RESOURCE_LIMIT',current)));
      worker.once('exit',()=>{if(!settled)void finish(failed('DISPLAY_REGEX_RESOURCE_LIMIT',current));});
    }catch{void finish(failed('DISPLAY_REGEX_RESOURCE_LIMIT',current));}
  });
}
