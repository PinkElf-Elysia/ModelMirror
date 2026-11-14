import {actionAdapters} from '../adapters.mjs';import {validateRequest,decodeSSE} from '../transport.mjs';import {need} from '../../contracts.mjs';
let raw='';for await(const chunk of process.stdin){raw+=chunk;need(Buffer.byteLength(raw)<=4*1024*1024,'WIRE_CAPACITY');}
try{
 const input=JSON.parse(raw),a=(await actionAdapters()).find(x=>x.id==='cultivation'),tools=a.tools.tools();
 if(input.kind==='request'){validateRequest(input.request,tools);console.log(JSON.stringify({ok:true}));}
 else{
  need(input.kind==='sse','WIRE_KIND');const result=decodeSSE(input.sse,tools);let actual=null,usage=null;
  for(const block of input.sse.replaceAll('\r\n','\n').split('\n\n')){const text=block.split('\n').filter(x=>x.startsWith('data:')).map(x=>x.slice(5).trimStart()).join('\n');if(!text||text==='[DONE]')continue;const frame=JSON.parse(text);if(frame.model){need(frame.model===input.model,'rpg_model_mismatch');actual=frame.model;}if(frame.usage)usage=Object.fromEntries(Object.entries(frame.usage).filter(([k,v])=>['prompt_tokens','completion_tokens','total_tokens'].includes(k)&&Number.isSafeInteger(v)&&v>=0));}
  console.log(JSON.stringify({raw:result.message.content||'',actual,usage,message:result.message,finishReason:result.finishReason}));
 }
}catch(e){console.log(JSON.stringify({error:e.code||'WIRE_INVALID'}));process.exitCode=1;}
