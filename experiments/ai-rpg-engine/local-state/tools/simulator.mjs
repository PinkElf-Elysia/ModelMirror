import {SessionRecords} from '../../authoring/session-storage.mjs';
import {hash,need} from '../definition.mjs';
import {simulatedReply} from '../simulator.mjs';
export const SCENARIOS=[['query-medicine','查看全部已登记丹药'],['query-ability','比较已学功法'],['query-use','先查询丹药，再服用两份养息丹'],['empty','查看名为沉香的物资'],['talk','与周禾聊聊接下来的打算']];
export class NativeSimulator{
 static async open(directory){return new NativeSimulator(await SessionRecords.open(directory,{scope:'rpg-native-simulator',recoverDeadOwner:true}));}
 constructor(records){this.records=records;this.calls=0;this.kind='offline-native-simulator';this.selection={model:'deterministic-native-fixture/2'};}
 key(session,id){return 'native-'+hash([session,id]).slice(0,40);}
 async generate(session,id,request,{input,state,variant='calm',fault=null,stage}){
  const key=this.key(session,id),fingerprint=hash({request,input,state,variant,fault,stage}),old=await this.records.get(key,x=>x);
  if(old){need(old.data.requestHash===fingerprint,'NATIVE_REQUEST_COLLISION');return old.data.result;}
  this.calls++;await new Promise(r=>setTimeout(r,60));
  let result={status:'completed',finishReason:'stop',provider:this.kind,usage:{kind:'simulated',inputTokens:null,outputTokens:null,costUSD:0},timing:{firstContentMs:null,proseFirstMs:null,completedMs:60}};
  const asks=/查看|查询|比较/.test(input);
  if(request.tool_choice==='auto'&&asks){
   const queries=[/功法/.test(input)?{domain:'abilities',kind:'功法'}:/沉香/.test(input)?{domain:'items',name:'沉香'}:{domain:'items',kind:'丹药'}];
   result.finishReason='tool_calls';result.message={role:'assistant',content:null,tool_calls:[{id:'query-one',type:'function',function:{name:fault==='invalid'?'write_state':'query_local_state',arguments:JSON.stringify({queries})},extra_content:{google:{thought_signature:'offline-opaque-signature'}}}]};
  }else{
   let reply=simulatedReply(input,state,{variant});
   if(asks&&!input.includes('服用')){
    const output=JSON.parse(request.messages.at(-1).content),r=output.results[0];
    const description=r.entries.map(e=>e.name+(e.quantity===null?'（数量未设定）':e.quantity!==undefined?' × '+e.quantity:'：'+e.progress)).join('、');
    reply={...reply,text:(description?'你核对已登记的档案：'+description+'。':'你在当前登记范围内没有找到匹配条目；其他未登记信息仍不明确。')+'\n周禾把木牌放回桌边，等你决定接下来做什么。\n<state_delta>{"operations":[]}</state_delta>'};
   }
   result.message={role:'assistant',content:reply.text};
   if(fault==='truncated')result.finishReason='length';
  }
  if(fault==='failed'&&(stage===2||!asks)){result={status:'failed',error:'模拟明确失败',provider:this.kind};}
  if(fault==='repeat'&&stage===2){result.finishReason='tool_calls';result.message={role:'assistant',content:null,tool_calls:[{id:'again',type:'function',function:{name:'query_local_state',arguments:'{"queries":[{"domain":"items"}]}'}}]};}
  await this.records.put(key,null,{requestHash:fingerprint,result});
  if((fault==='unknown'&&(stage===2||!asks))||(fault==='unknown-first'&&stage===1))return {status:'unknown',error:'模拟传输丢失；核对保存的原结果'};
  return result;
 }
 async lookup(session,id){return (await this.records.get(this.key(session,id),x=>x))?.data.result||null;}
 async close(){await this.records.close();}
}
