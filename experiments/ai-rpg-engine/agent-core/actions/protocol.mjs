import {clone,need,bounded} from '../contracts.mjs';
export function parseResult(r,tools){
 need(r?.status==='completed','NATIVE_RESULT_UNKNOWN');const m=r.message;
 need(m?.role==='assistant'&&(m.content==null||typeof m.content==='string')&&Object.keys(m).every(k=>['role','content','tool_calls','extra_content','reasoning_details'].includes(k)),'NATIVE_MESSAGE');
 const meta=v=>need(v===undefined||(v&&typeof v==='object'&&!Array.isArray(v)&&JSON.stringify(v).length<=65536),'NATIVE_METADATA');meta(m.extra_content);
 if(m.reasoning_details!==undefined){need(Array.isArray(m.reasoning_details)&&m.reasoning_details.every(x=>x&&typeof x==='object'&&!Array.isArray(x)),'NATIVE_METADATA');bounded(m.reasoning_details,{bytes:65536,depth:12});}
 if(m.tool_calls?.length){
  need(r.finishReason==='tool_calls'&&m.tool_calls.length===1,'NATIVE_TOOL_LIMIT');const c=m.tool_calls[0];
  need(c&&Object.keys(c).every(k=>['id','type','function','extra_content'].includes(k))&&typeof c.id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(c.id)&&c.type==='function','NATIVE_CALL');meta(c.extra_content);
  need(c.function&&Object.keys(c.function).sort().join()==='arguments,name'&&tools.some(t=>t.function.name===c.function.name),'TOOL_NOT_ALLOWED');
  need(typeof c.function.arguments==='string'&&c.function.arguments.length<=8192,'NATIVE_ARGUMENTS');let args;
  try{args=JSON.parse(c.function.arguments);}catch{need(false,'NATIVE_ARGUMENTS');}
  return {kind:'tool',name:c.function.name,args,callId:c.id,message:clone(m)};
 }
 need(r.finishReason==='stop'&&typeof m.content==='string'&&m.content.trim()&&(!m.tool_calls||Array.isArray(m.tool_calls)),'NATIVE_EMPTY_OR_TRUNCATED');
 return {kind:'final',receipt:{status:'completed',finishReason:'stop',text:m.content,provider:r.provider,usage:r.usage??null}};
}
export function continuation(first,assistant,result,tools){
 return {...clone(first),messages:[...clone(first.messages),clone(assistant),{role:'tool',tool_call_id:assistant.tool_calls[0].id,content:JSON.stringify(result)}],tools:clone(tools),tool_choice:tools.length?'auto':'none'};
}
