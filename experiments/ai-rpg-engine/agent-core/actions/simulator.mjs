import {validateRequest,decodeSSE,simulatedWire} from './transport.mjs';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need,keys} from '../contracts.mjs';
export class ActionSimulator{
 static async open(directory,adapter){return new ActionSimulator(await SessionRecords.open(directory,{scope:'rpg-actions-simulator',recoverDeadOwner:true}),adapter);}
 constructor(records,adapter){Object.assign(this,{records,adapter,calls:0,mode:'offline',kind:'offline-actions/5'});}
 options(o={}){keys(o,['fault']);need([undefined,null,'failed','unknown','truncated','invalid','write','double','bad-json'].includes(o.fault),'CORE_OPTIONS');return {fault:o.fault??null};}
 key(s,id){return 'sim-'+hash([s,id]).slice(0,40);}
 async generate(session,id,request,meta){
  validateRequest(request,this.adapter.tools.tools());
  const key=this.key(session,id),requestHash=hash({request,meta}),old=await this.records.get(key,x=>x);
  if(old){need(old.data.requestHash===requestHash,'NATIVE_REQUEST_COLLISION');return clone(old.data.result);}
  const start=performance.now();this.calls++;
  const {purpose,input,state}=meta,a=this.adapter,resource=a.resources(state)[0];let message,finishReason='stop';
  const call=(name,args)=>{finishReason='tool_calls';return {role:'assistant',content:null,tool_calls:[{id:'call-'+meta.stage,type:'function',function:{name,arguments:JSON.stringify(args)},extra_content:{google:{thought_signature:'offline-signature'}}}]};};
  if(purpose==='narration')message={role:'assistant',content:a.id==='cultivation'?'你按刚才确认的用量服下养息丹，将剩余丹药收好。周禾停下手里的活，问你接下来是否仍去药圃。你还可以选择先歇一会儿，再作安排。':'你按确认的用量给灯具添油，把登记簿放回桌边。灯光照着窗沿，你听了听海风，继续留意值夜的动静。'};
  else if(input==='先查询，再拟定使用两份的方案'&&meta.stage===1||input==='查看登记物资'&&meta.stage===1)
   message=call('query_local_state',{queries:[{domain:a.id==='cultivation'?'items':'supplies'}]});
  else if(['先查询，再拟定使用两份的方案','拟定使用两份的方案'].includes(input))
   message=call('prepare_action',{actionId:a.rule.id,itemId:resource.id,quantity:2});
  else message={role:'assistant',content:input==='查看登记物资'?'你核对了已登记物资，尚未取用任何东西。\n<state_delta>{"operations":[]}</state_delta>':input==='聊聊今日安排'?'你把今天的安排想了想，准备听听同伴的意见，再决定下一步。\n<state_delta>{"operations":[]}</state_delta>':'当前是离线固定样例，不会理解任意自由输入。请选择页面列出的交流、查询或提案样例。\n<state_delta>{"operations":[]}</state_delta>'};
  const fault=meta.options?.fault;
  if(fault==='invalid'&&message.tool_calls)message.tool_calls[0].function.name='write_state';
  if(fault==='bad-json'&&message.tool_calls)message.tool_calls[0].function.arguments='{';
  if(fault==='double'&&message.tool_calls)message.tool_calls.push(clone(message.tool_calls[0]));
  if(fault==='write'&&purpose==='narration')message.content+='\n<state_delta>{"operations":[]}</state_delta>';
  let result={status:'completed',finishReason:fault==='truncated'?'length':finishReason,message,provider:this.kind,
   usage:{kind:'simulated',inputTokens:null,outputTokens:null,costUSD:0},timing:{proseFirstMs:null,firstContentMs:null,completedMs:performance.now()-start}};
  if(result.status==='completed'&&!["invalid","bad-json","double","truncated"].includes(fault)){
   const wire=simulatedWire(result),decoded=decodeSSE(wire,request.tools);result.message=decoded.message;result.wire=wire;
  }
  if(fault==='failed')result={status:'failed',error:'SIMULATED_FAILURE'};
  await this.records.put(key,null,{requestHash,request:clone(request),result});
  return fault==='unknown'?{status:'unknown',error:'SIMULATED_RECEIPT_LOST'}:result;
 }
 async lookup(session,id){return (await this.records.get(this.key(session,id),x=>x))?.data.result??null;}
 async preview(){throw Object.assign(Error('R5_PREVIEW_DISABLED'),{code:'R5_PREVIEW_DISABLED'});}
 close(){return this.records.close();}
}
