import {QUERY_TOOL} from '../observations.mjs';
import {parseResult} from '../../actions/protocol.mjs';
import {decodeSSE} from '../../actions/transport.mjs';
import {need,hash,keys,clone} from '../../contracts.mjs';
import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
export function validate(request){
 keys(request,['messages','tools','tool_choice']);need(Array.isArray(request.tools)&&request.tools.length<=1,'R6_TOOLS');
 for(const t of request.tools)need(hash(t)===hash(QUERY_TOOL),'R6_TOOL_DECLARATION');
 need(request.tool_choice===(request.tools.length?'auto':'none'),'R6_TOOL_CHOICE');
 need(Array.isArray(request.messages)&&request.messages.length>=3&&request.messages.length<=100,'R6_MESSAGES');
 need(request.messages[0].role==='system'&&Buffer.byteLength(JSON.stringify(request))<=192*1024,'R6_CAPACITY');
 let waiting=null,calls=0;
 for(const m of request.messages){
  if(waiting){need(m.role==='tool'&&m.tool_call_id===waiting&&typeof m.content==='string'&&Buffer.byteLength(m.content)<=65536,'R6_TOOL_PAIR');keys(m,['role','tool_call_id','content']);waiting=null;continue;}
  need(['user','assistant','system'].includes(m.role),'R6_MESSAGE_ROLE');
  if(m.tool_calls){parseResult({status:'completed',finishReason:'tool_calls',message:m},[QUERY_TOOL]);need(++calls<=1,'R6_TOOL_BATCH');waiting=m.tool_calls[0].id;}
  else{keys(m,['role','content']);need(typeof m.content==='string','R6_MESSAGE_CONTENT');}
 }
 need(!waiting,'R6_TOOL_PAIR');return clone(request);
}
export function parseWire(input){
 if(input.kind==='request'){validate(input.request);return {ok:true};}
 need(input.kind==='sse','R6_WIRE_KIND');const r=decodeSSE(input.sse,[QUERY_TOOL]);let actual=null,usage=null;
 for(const block of input.sse.replaceAll('\r\n','\n').split('\n\n')){
  const text=block.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n');if(!text||text==='[DONE]')continue;
  const frame=JSON.parse(text);if(frame.model){need(frame.model===input.model,'rpg_model_mismatch');actual=frame.model;}
  if(frame.usage)usage=Object.fromEntries(Object.entries(frame.usage).filter(([k,v])=>['prompt_tokens','completion_tokens','total_tokens'].includes(k)&&Number.isSafeInteger(v)&&v>=0));
 }
 return {raw:r.message.content||'',actual,usage,message:r.message,finishReason:r.finishReason};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 let raw='';for await(const chunk of process.stdin){raw+=chunk;need(Buffer.byteLength(raw)<=4*1024*1024,'R6_WIRE_CAPACITY');}
 try{console.log(JSON.stringify(parseWire(JSON.parse(raw))));}catch(e){console.log(JSON.stringify({error:e.code||'R6_WIRE_INVALID'}));process.exitCode=1;}
}
