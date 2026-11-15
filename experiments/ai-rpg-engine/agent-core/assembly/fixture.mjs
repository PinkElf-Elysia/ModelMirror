import {validateRequest,simulatedWire,decodeSSE} from '../actions/transport.mjs';
// Offline pattern fixture only. It is not a language understanding implementation.
export function wireFixture(world){return {async generate(session,id,request,meta){
 const a=world.adapter;validateRequest(request,a.tools.tools(),{maxTools:16});
 let finishReason='stop',message;const available=n=>request.tools.some(t=>t.function.name===n);
 const call=(name,args)=>{finishReason='tool_calls';return {role:'assistant',content:null,tool_calls:[{id:'call-'+meta.stage,type:'function',function:{name,arguments:JSON.stringify(args)},extra_content:{google:{thought_signature:'offline-r8-signature'}}}]};};
 const result=request.messages.at(-1).role==='tool'?JSON.parse(request.messages.at(-1).content):null;
 const input=meta.input??'',resource=meta.state?a.resources(meta.state)[0]:null;
 const query=a.query.tool.function.parameters.properties.queries.items.properties.domain.enum[0];
 if(meta.purpose==='observation'){
  const seen=JSON.parse(request.messages.at(-1).content).observation;
  message={role:'assistant',content:'我只能依据已公开的记录：'+(seen.prose.at(-1)?.body??'目前没有可见的已完成剧情。')+' 未提供的库存与私人记忆，我并不知情。'};
 }else if(meta.purpose==='narration'){
  message={role:'assistant',content:'你完成了刚才确认的行动，把余下物资收好。片刻停顿后，你重新考虑接下来的安排。此次结果以已提交回执为准。'};
 }else if(input==='准备交接班'&&a.id==='lighthouse'){
  message={role:'assistant',content:a.fixture(request,{input,state:meta.state}).text};
 }else if(result&&/统计|合计/.test(input))message={role:'assistant',content:'你核对了'+result.category+'的登记：已知数量合计 '+result.knownTotal+'，统计范围为已登记条目。'+(result.unknown.length?'另有 '+result.unknown.length+' 个条目的数量未设定，不计入已知合计。':'')+'没有取用任何物资。\n<state_delta>{"operations":[]}</state_delta>'};
 else if(/统计|合计/.test(input)&&available('count_registered'))message=call('count_registered',{category:world.category});
 else if(/先查询/.test(input)&&!result&&available('query_local_state'))message=call('query_local_state',{queries:[{domain:query}]});
 else if(/方案|服用|使用|添油/.test(input)&&available('prepare_action')){
  const match=input.match(/(?:使用|服用|消耗|添油)\s*(\d+)/);message=call('prepare_action',{actionId:a.rule.id,itemId:resource.id,quantity:match?Number(match[1]):2});
 }else if(/查看|查询|多少/.test(input)&&!result&&available('query_local_state'))message=call('query_local_state',{queries:[{domain:query}]});
 else message={role:'assistant',content:(result?'你核对了登记记录，暂时没有取用物资。':'你停下手边的事，整理下一步安排。当前为离线模拟；其他自由输入只作无状态变化的交流回显，不代表真实模型理解。')+'\n<state_delta>{"operations":[]}</state_delta>'};
 if(meta.fault==='foreign-tool'&&message.tool_calls)message.tool_calls[0].function.name='write_anything';
 if(meta.fault==='write'&&meta.purpose==='narration')message.content+='\n<state_delta>{"operations":[]}</state_delta>';
 const raw={status:'completed',finishReason:meta.fault==='truncated'?'length':finishReason,message,provider:'offline-assembly/8',usage:{kind:'simulated',inputTokens:null,outputTokens:null,costUSD:0}};
 if(['foreign-tool','truncated'].includes(meta.fault))return raw;
 const wire=simulatedWire(raw);return {...raw,...decodeSSE(wire,request.tools),wire};
 }};}
