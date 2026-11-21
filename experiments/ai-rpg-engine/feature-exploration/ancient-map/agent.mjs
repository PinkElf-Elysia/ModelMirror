import {randomUUID} from 'node:crypto';
import {parseResult, continuation} from '../../agent-core/actions/protocol.mjs';
import {places,project} from './world.mjs';

export const examples={query:'我现在能确认宁远的情况吗？',rumor:'把听来的失城消息记为传闻。',draft:'先拟宁远城防移交之旨。',settle:'请依据收到的接防回执核对交割。'};
function tool(name,description,properties){return {type:'function',function:{name,description,parameters:{type:'object',properties,required:Object.keys(properties),additionalProperties:false}}};}
const node={type:'string',enum:places};
const query=tool('query_map','按消息涉及的地点查询地图记录。角色视角和可见范围由宿主绑定。',{
 node:{...node,description:'玩家所问消息涉及的地点。当问句同时提到消息接收地和所询问的地点时，填写所询问的地点。'}
});
const propose=tool('propose_map_change','登记传闻、拟旨或凭已收到的回执提出交割。引用查询返回的依据编号。',{node,kind:{type:'string',enum:['rumor','draft','handover']},source:{type:'string'}});
export function initialize(s){s.agent={turns:[],events:[],pending:null};return s;}
function event(s,kind,info){const e={id:randomUUID(),kind,node:'宁远',...info};s.agent.events.push(e);return e;}
const find=(s,k)=>s.agent.events.find(e=>e.kind===k);
function visible(s,n){
 const p=project(s).places.find(p=>p.name===n);
 const evidence=[];
 if(n==='宁远'){
  if(s.person.role==='soldier')evidence.push({id:'camp-rumor',text:'同袍口信，未经核实'});
  if(find(s,'report'))evidence.push({id:find(s,'report').id,text:'已送达关报，报告营变，未证实失城'});
  if(find(s,'receipt'))evidence.push({id:find(s,'receipt').id,text:'本档模拟接防凭据：宁远由明交付后金'});
 }
 return {...p,evidence};
}
export function agentView(s){
 const v=project(s);v.options={};v.agent=s.agent;
 if(s.person.role==='court'){
  if(!find(s,'report'))v.options.report='推进至关报抵京';
  if(find(s,'decree')&&!find(s,'receipt'))v.options.receive='推进至接防回执送达';
 }
 if(s.agent.pending){v.options={approve:'确认这份提案',cancel:'撤回这份提案'};}
 return v;
}
export function command(s,a){
 if(!Object.hasOwn(agentView(s).options,a))throw Error('当前不可执行');
 if(a==='report'){s.step=2;event(s,'report',{text:'关报已到，记载营变。'});}
 if(a==='receive')event(s,'receipt',{source:find(s,'decree').id,text:'模拟交割文书已签收，待核对。'});
 if(a==='approve'||a==='cancel'){
  const p=s.agent.pending;s.agent.pending=null;
  const kind=a==='cancel'?'cancelled':p.kind==='draft'?'decree':'settled';
  const e=event(s,kind,{source:p.source,proposal:p.id});
  if(kind==='settled'){if(s.control!=='明'||find(s,'receipt')?.id!==p.source)throw Error('交割凭据失效');e.before=s.control;e.after='后金';s.control=e.after;}
 }
}
function execute(s,call,turnId,seen){
 const a=call.args;if(!a||!places.includes(a.node))throw Error('地图节点无效');
 if(call.name==='query_map'){if(Object.keys(a).length!==1)throw Error('查询字段无效');const v=visible(s,a.node);for(const e of v.evidence)seen.add(e.id);return v;}
 if(Object.keys(a).sort().join()!=='kind,node,source'||a.node!=='宁远'||!seen.has(a.source))throw Error('提案缺少本轮可见依据');
 if(a.kind==='rumor'&&s.person.role==='soldier'&&a.source==='camp-rumor')return event(s,'rumor',{source:a.source,turnId,text:'已登记传闻，归属未变'});
 if(s.person.role!=='court'||s.control!=='明')throw Error('没有这项权限');
 const valid=a.kind==='draft'&&!find(s,'decree')&&find(s,'report')?.id===a.source||a.kind==='handover'&&find(s,'receipt')?.id===a.source;
 if(!valid)throw Error('依据不足以支持提案');
 return s.agent.pending={id:randomUUID(),turnId,...a,status:'待玩家确认'};
}
// Deterministic replacement for the narrative provider. The host only consumes native messages.
export async function fixture(packet){
 const input=packet.messages.findLast(m=>m.role==='user').content;
 const last=packet.messages.at(-1);
 const call=(name,args)=>({status:'completed',finishReason:'tool_calls',message:{role:'assistant',content:null,tool_calls:[{type:'function',id:'c-'+randomUUID(),function:{name,arguments:JSON.stringify(args)}}]}});
 const final=text=>({status:'completed',finishReason:'stop',message:{role:'assistant',content:text}});
 if(last.role==='user')return Object.values(examples).includes(input)?call('query_map',{node:'宁远'}):final('本轮只记下你的话。');
 const result=JSON.parse(last.content);
 if(result.evidence){
  const type=Object.keys(examples).find(k=>examples[k]===input);
  const source=result.evidence.find(e=>type==='rumor'?e.id==='camp-rumor':type==='draft'?e.text.startsWith('已送达'):type==='settle'?e.text.startsWith('本档模拟'):false);
  if(source)return call('propose_map_change',{node:'宁远',kind:type==='settle'?'handover':type,source:source.id});
  return final('依目前所见：'+result.sources.join('；')+'。登记控制方为'+result.control+'。');
 }
 return final(result.text||'提案已呈上，等候确认。');
}
// This instruction applies only to final narration after the host recorded a rumor.
export function rumorNarration(s){
 return '你是'+s.person.name+'的叙事者。逐项依据本轮查询与登记回执作答：开局登记只说明最后登记的控制方，亲见仅限查询中标作亲见的内容，口信继续标为未经核实。不得补造现场观察或佐证，也不得把未提及的情况断言为正常。回执“已登记传闻，归属未变”表示工具登记已经完成；直接告知玩家该结果。不得称为草案、待登记或待确认，不再询问是否确认。';
}
export function mapContinuation(s,first,call,result,nextTools){
 const packet=continuation(first,call.message,result,nextTools);
 if(s.person.role==='court'&&call.name==='query_map'&&result.evidence.length===0){
  packet.messages[0].content+='\n此次查询的证据列表为空，只能说明“本次可见记录中没有新报”。按来源说明最后登记的控制方，当前实情仍未知。不得补写已查阅或查遍内阁、兵部等机构的文卷，不得断言御前未收到任何奏报。';
 }
 return packet;
}

export async function turn(s,input,generate=fixture){
 if(typeof input!=='string'||!input.trim()||input.length>1200||s.agent.pending)throw Error('请先处理当前提案或检查输入');
 const id=randomUUID(),seen=new Set(),trace=[];
 let packet={messages:[{role:'system',content:'你是'+s.person.name+'的叙事者。只依据此角色可见信息。需要状态先查询；传闻、拟旨、凭据交割分别处理。仅提案，等待玩家确认。'+s.log.slice(-2).join('\n')},{role:'user',content:input}],tools:[query,propose],tool_choice:'auto'};
 for(let stage=0;stage<3;stage++){
  const out=parseResult(await generate(packet),packet.tools);
  if(out.kind==='final'){s.agent.turns.push({id,input,trace,body:out.receipt.text});s.log.push(out.receipt.text);return;}
  const result=execute(s,out,id,seen);trace.push({message:out.message,result:structuredClone(result)});
  packet=mapContinuation(s,packet,out,result,stage===0&&out.name==='query_map'?[propose]:[]);
  if(out.name==='propose_map_change'&&result.kind==='rumor'){
   packet.messages[0].content=rumorNarration(s);
  }
 }
 throw Error('叙事未完成');
}
