import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need} from '../contracts.mjs';
import {parseResult} from '../actions/protocol.mjs';
import {decodeSSE,simulatedWire} from '../actions/transport.mjs';
import {QUERY_TOOL} from './observations.mjs';

// Offline only. This fixture reads exactly the actor-scoped frame, never owner state.
export class ActorSimulator{
 static async open(directory){return new ActorSimulator(await SessionRecords.open(directory,{scope:'rpg-actor-simulator-r6',recoverDeadOwner:true}));}
 constructor(records){this.records=records;this.mode='offline';this.calls=0;}
 key(session,id){return 'call-'+hash([session,id]).slice(0,40);}
 async generate(session,id,request,{frame,input,fault=null}){
  need(['auto','none'].includes(request.tool_choice)&&request.tools.every(t=>hash(t)===hash(QUERY_TOOL)),'ACTOR_TOOL_DECLARATION');
  need(request.messages.every(m=>['system','assistant','user','tool'].includes(m.role)),'ACTOR_MESSAGES');
  const key=this.key(session,id),requestHash=hash({request,frame,input,fault}),old=await this.records.get(key);
  if(old){need(old.data.requestHash===requestHash,'NATIVE_REQUEST_COLLISION');return clone(old.data.result);}
  this.calls++;let result;
  if(fault==='failed')result={status:'failed',error:'SIMULATED_FAILURE'};
  else{
   let message,finishReason='stop';
   if(request.tool_choice==='auto'&&/余量|还剩|登记/.test(input)){
    finishReason='tool_calls';message={role:'assistant',content:null,tool_calls:[{
     id:'call-inventory',type:'function',function:{name:'query_local_state',arguments:JSON.stringify({domain:'inventory',id:'medicine'})},
     extra_content:{google:{thought_signature:'offline-r6-signature'}}}]};
   }else{
    const tool=request.messages.findLast(m=>m.role==='tool'),observed=frame.events.length>0;
    let content;
    if(tool){const q=JSON.parse(tool.content);content=q.status==='matched'?'林舟核对了获准查看的登记：“养息丹还剩'+q.items[0].quantity+'份。这次没有再取用。”':'林舟摇了摇头：“我没有查阅你的丹药登记的权限，不能确定还剩多少。”';}
    else if(input.includes('已经赠给'))content='“这是你的转述，我还没有看到经过确认的赠与记录。”林舟没有把它记作已经收到的物品。';
    else if(input.includes('接下来'))content=observed?'“方才你已取用丹药，接下来想留在廊下歇息，还是先聊聊药圃？”林舟等你选择。':'“我们可以先聊聊药圃的安排。”林舟等你开口，没有臆测自己未见的经历。';
    else content=observed?'林舟放下手里的竹签：“方才我看见你服用了两份养息丹。药袋里还剩多少，我不能仅凭这件事判断。”':'林舟答道：“我没有这件事的观察记录，不能说自己亲眼见过。你愿意告诉我发生了什么吗？”';
    message={role:'assistant',content};
   }
   if(fault==='write')message={role:'assistant',content:'我改好了。<state_delta>{"operations":[]}</state_delta>'};
   if(fault==='foreign-tool'){finishReason='tool_calls';message={role:'assistant',content:null,tool_calls:[{id:'write',type:'function',function:{name:'prepare_action',arguments:'{}'}}]};}
   result={status:'completed',finishReason:fault==='truncated'?'length':finishReason,message,provider:'offline-actor-fixture/6',usage:{kind:'simulated',tokens:null,costUSD:0}};
   if(!['foreign-tool','truncated'].includes(fault)){
    const wire=simulatedWire(result);result={...result,...decodeSSE(wire,request.tools),wire};parseResult(result,request.tools);
   }
  }
  await this.records.put(key,null,{requestHash,request:clone(request),frame:clone(frame),result});
  return fault==='unknown'?{status:'unknown',error:'SIMULATED_RECEIPT_LOST'}:result;
 }
 async lookup(session,id){return (await this.records.get(this.key(session,id)))?.data.result??null;}
 close(){return this.records.close();}
}
