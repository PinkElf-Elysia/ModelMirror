import {validateSceneRequest} from './protocol.mjs';
import {wireFixture} from '../assembly/fixture.mjs';
import {QUERY} from './projection.mjs';
import {simulatedWire,decodeSSE} from '../actions/transport.mjs';
// Explicit offline examples. No language understanding or provider integration is claimed.
export function sceneFixture(world){
 const original=wireFixture(world);
 return {async generate(session,id,request,meta){
  if(meta.purpose!=='scene-participant'){
   const raw=await original.generate(session,id,request,meta);
   if(raw.message?.content&&!raw.message.tool_calls){
    const marker='【已采用的场景对话】',system=request.messages[0].content,index=system.indexOf(marker);
    if(index>=0){const material=JSON.parse(system.slice(system.indexOf('\n',index)+1)),last=material.entries.at(-1);
     if(last)raw.message.content='你想起'+last.name+'刚才说过：“'+last.body+'” 这是一段已发生的对话，尚未核实的内容仍不改变登记。\n'+raw.message.content;}
    const wire=simulatedWire(raw);return {...raw,...decodeSSE(wire,request.tools),wire};
   }return raw;
  }
  validateSceneRequest(request);const f=meta.frame;let message,finishReason='stop';
  if(meta.stage===0&&/查询|多少|余量|库存/.test(meta.input)){
   finishReason='tool_calls';message={role:'assistant',content:null,tool_calls:[{id:'scene-query',type:'function',function:{name:'query_scene_state',arguments:JSON.stringify({domain:'resources'})},extra_content:{google:{thought_signature:'offline-r9-signature'}}}]};
   if(meta.fault==='foreign-tool')message.tool_calls[0].function.name='change_state';
  }else{
   const result=request.messages.at(-1).role==='tool'?JSON.parse(request.messages.at(-1).content):null;
   let body;
   if(result)body=result.status==='unavailable'?'我没有获准查看这份登记，余量我不知道。':result.entries.map(e=>e.name+'的登记余量是'+(e.quantity===null?'未设定':e.quantity)+'。').join('')+'这只涵盖获准登记的资源。';
   else if(/之前|刚才|处理/.test(meta.input))body=f.events.length?'我看到已确认的记录：'+f.events.at(-1).description+'。这不能证明除此之外的效果。':'我没有获准知道这次行动是否发生，也不知道未公开的账目。';
   else if(/记得|上次/.test(meta.input))body=f.dialogue.length?'上次你问“'+f.dialogue.at(-1).input+'”，我回答：“'+f.dialogue.at(-1).body+'”':'我们还没有已采用的交流记录。';
   else body=f.facts.length?'登记中的安排是“'+f.facts[0].value+'”。我们可以先把眼前的事说清楚。':'我只是来访者，先前的值守安排我并不了解。';
   if(meta.fault==='write')body+='<state_delta>{"operations":[]}</state_delta>';
   message={role:'assistant',content:body};
  }
  const raw={status:'completed',finishReason:meta.fault==='truncated'?'length':finishReason,message,provider:'offline-scene/9',usage:{kind:'simulated',inputTokens:null,outputTokens:null,costUSD:0}};
  if(['foreign-tool','truncated','write'].includes(meta.fault))return raw;
  const wire=simulatedWire(raw);return {...raw,...decodeSSE(wire,request.tools),wire};
 }};
}
