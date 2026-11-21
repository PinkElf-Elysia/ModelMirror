import assert from 'node:assert/strict';
import {decodeSSE,validateRequest} from '../../../agent-core/actions/transport.mjs';
import {turn,initialize} from '../../../feature-exploration/ancient-map/agent.mjs';
import {newRun,opening} from '../../../feature-exploration/ancient-map/world.mjs';
import {assemble} from '../../assembly.mjs';
let registry;
await turn(initialize(newRun(opening('court'))),'查询',p=>{
 registry=p.tools;return {status:'completed',finishReason:'stop',message:{role:'assistant',content:'登记'}};
});
let raw='';for await(const s of process.stdin)raw+=s;
const x=JSON.parse(raw);
if(x.kind==='request'){
 const p=x.request;validateRequest(p,registry);
 assert.ok(p.messages[0].content.startsWith(assemble({input:'x'})[0].content));
 const text=p.messages.findLast(m=>m.role==='user').content;
 assert.ok(['京师现在能确认宁远的情形吗？','接着上回写：宁远现在归谁，京师凭什么这样登记？'].includes(text));
 const last=p.messages.at(-1);
 if(last.role==='tool'){
  assert.equal(p.messages.at(-2).tool_calls[0].function.name,'query_map');
  assert.equal(JSON.parse(last.content).name,'宁远');assert.deepEqual(p.tools,[registry[1]]);
 }else assert.deepEqual(p.tools,registry);
 console.log('{}');
}else{
 assert.equal(x.kind,'sse');const r=decodeSSE(x.sse,registry);
 let actual=null,usage=null;
 for(const l of x.sse.split('\n').filter(l=>l.startsWith('data:')&&!l.includes('[DONE]'))){
  const f=JSON.parse(l.slice(5));if(f.model){assert.equal(f.model,x.model);actual=f.model;}
  if(f.usage)usage=Object.fromEntries(Object.entries(f.usage).filter(([k])=>['prompt_tokens','completion_tokens','total_tokens'].includes(k)));
 }
 console.log(JSON.stringify({actual,usage,message:r.message,finishReason:r.finishReason,raw:r.message.content??''}));
}
