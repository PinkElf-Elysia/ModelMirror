import {validateSceneRequest} from '../protocol.mjs';
import {decodeSSE} from '../../actions/transport.mjs';
import {QUERY} from '../projection.mjs';
import {need} from '../../contracts.mjs';
let raw='';for await(const chunk of process.stdin){raw+=chunk;need(Buffer.byteLength(raw)<=4*1024*1024,'WIRE_CAPACITY');}
const x=JSON.parse(raw);
if(x.kind==='request'){validateSceneRequest(x.request);console.log('{"ok":true}');}
else{
  need(x.kind==='sse','WIRE_KIND');const r=decodeSSE(x.sse,[QUERY]);let actual=null,usage=null;
  for(const line of x.sse.split(/\r?\n/)){
    if(!line.startsWith('data:')||line.slice(5).trim()==='[DONE]')continue;
    const e=JSON.parse(line.slice(5));if(e.model){need(e.model===x.model,'WIRE_MODEL');actual=e.model;}
    if(e.usage)usage=Object.fromEntries(Object.entries(e.usage).filter(([k,v])=>['prompt_tokens','completion_tokens','total_tokens'].includes(k)&&Number.isSafeInteger(v)&&v>=0));
  }
  console.log(JSON.stringify({actual,usage,message:r.message,finishReason:r.finishReason,raw:r.message.content??''}));
}
