// Native protocol retained from v2; schema validation is injected by a trusted adapter.
import {need,clone,hash} from './contracts.mjs';
export function nativeResult(result,{toolChoice='auto',query}={}){
 need(result?.status==='completed','NATIVE_RESULT_UNKNOWN');
 const m=result.message;need(m?.role==='assistant'&&(m.content==null||typeof m.content==='string'),'NATIVE_MESSAGE');
 need(Object.keys(m).every(k=>['role','content','tool_calls','extra_content'].includes(k)),'NATIVE_MESSAGE');
 const metadata=v=>need(v===undefined||(v&&typeof v==='object'&&!Array.isArray(v)&&JSON.stringify(v).length<=65536),'NATIVE_METADATA');
 metadata(m.extra_content);
 if(m.tool_calls?.length){
  need(toolChoice==='auto'&&result.finishReason==='tool_calls'&&m.tool_calls.length===1,'NATIVE_QUERY_LIMIT');
  const call=m.tool_calls[0];
  need(Object.keys(call).every(k=>['id','type','function','extra_content'].includes(k))&&typeof call.id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(call.id)&&call.type==='function','NATIVE_CALL');
  need(call.function?.name===query.tool.function.name&&Object.keys(call.function).sort().join() === 'arguments,name'&&typeof call.function.arguments==='string'&&call.function.arguments.length<=8192,'NATIVE_FUNCTION');
  metadata(call.extra_content);
  let args;try{args=JSON.parse(call.function.arguments);}catch{throw Object.assign(Error('NATIVE_ARGUMENTS'),{code:'NATIVE_ARGUMENTS'});}
  query.validate(args);return {kind:'query',message:clone(m),callId:call.id,args};
 }
 need(result.finishReason==='stop'&&m.content?.trim()&&(!m.tool_calls||Array.isArray(m.tool_calls)),'NATIVE_EMPTY_OR_TRUNCATED');
 return {kind:'final',receipt:{status:'completed',finishReason:'stop',text:m.content,provider:result.provider,usage:result.usage}};
}
export function continueRequest(first,assistant,output){
 return {...clone(first),messages:[...clone(first.messages),clone(assistant),{role:'tool',tool_call_id:assistant.tool_calls[0].id,content:JSON.stringify(output)}],tool_choice:'none'};
}
export function measureRequest(request){
 const bytes=s=>Buffer.byteLength(s,'utf8'),messages=request.messages;
 return {characters:messages.reduce((n,m)=>n+(m.content?.length||0),0),wireBytes:bytes(JSON.stringify(request)),
 systemBytes:bytes(messages.filter(m=>m.role==='system').map(m=>m.content).join('\n')),
 textHistoryAndInputBytes:bytes(messages.filter(m=>m.role!=='system'&&m.role!=='tool').map(m=>m.content||'').join('\n')),
 toolsBytes:bytes(JSON.stringify(request.tools||[])),toolResultBytes:bytes(messages.filter(m=>m.role==='tool').map(m=>m.content).join('\n')),
 inputHash:hash(request),tokens:null};
}
