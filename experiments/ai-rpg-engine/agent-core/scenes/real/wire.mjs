import {validateSceneRequest} from '../protocol.mjs';
import {validateRequest,decodeSSE} from '../../actions/transport.mjs';
import {need} from '../../contracts.mjs';
export function validate(request){
 need(request?.tools?.length===0&&request.tool_choice==='none'&&!request.messages.some(m=>m.tool_calls||m.role==='tool'),'R9_TEXT_ONLY');
 if(request.messages.some(m=>m.role==='assistant'))validateSceneRequest(request);
 else validateRequest(request,[]);
}
let raw='';for await(const chunk of process.stdin){raw+=chunk;need(Buffer.byteLength(raw)<=4*1024*1024,'WIRE_CAPACITY');}
try{const input=JSON.parse(raw);
 if(input.kind==='request'){validate(input.request);console.log(JSON.stringify({ok:true}));}
 else{need(input.kind==='sse','WIRE_KIND');const result=decodeSSE(input.sse,[]);let actual=null,usage=null;
  for(const block of input.sse.replaceAll('\r\n','\n').split('\n\n')){const text=block.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n');if(!text||text==='[DONE]')continue;const f=JSON.parse(text);if(f.model){need(f.model===input.model,'rpg_model_mismatch');actual=f.model;}if(f.usage)usage=Object.fromEntries(Object.entries(f.usage).filter(([k,v])=>['prompt_tokens','completion_tokens','total_tokens'].includes(k)&&Number.isSafeInteger(v)&&v>=0));}
  console.log(JSON.stringify({raw:result.message.content||'',actual,usage,message:result.message,finishReason:result.finishReason}));
 }
}catch(e){console.log(JSON.stringify({error:e.code||'WIRE_INVALID'}));process.exitCode=1;}
