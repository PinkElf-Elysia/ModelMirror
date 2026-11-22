import {randomUUID} from 'node:crypto';
import {parseResult,continuation} from '../../agent-core/actions/protocol.mjs';
import {atlasTool,queryAtlas} from './atlas-query.mjs';

const sites=[
  ['beijing','北京','明'],['shanhaiguan','山海关','明'],['ningyuan','宁远','明'],['jinzhou','锦州','明'],
  ['tayouan','大员','荷兰东印度公司'],['keelung','鸡笼','西班牙'],['taiwan-communities','台湾原住民各社','各社自治']
];
const island=sites.slice(4);
const cases=[
  {node:'大员',to:'明',order:'下旨经略台湾，命郑芝龙赴大员办理交接',deliver:'推进至大员捷报抵京',
   report:'郑芝龙呈报大员据点已受降，附当地接收文书。文书所涉止于大员。'},
  {node:'宁远',to:'后金',order:'签发宁远移交旨意',deliver:'推进至宁远接防回执抵京',
   report:'宁远呈到接防回执，载明明方交付、后金接收，待京师核准。'}
];
const sign='签发这份旨意';
const confirm='确认这份交割',cancel='撤回这份提案';
export const overview='台湾现在谁管？请分地说明。';
const label=c=>'核对'+c.node+'交割';
const lookup=site=>sites.find(row=>row[1]===site);
const holder=(world,node)=>world.facts.findLast(f=>f.node===node&&f.kind==='settled')?.after??lookup(node)[2];
const delivered=(world,node)=>world.facts.filter(f=>f.node===node&&f.receivedAt!=null&&f.receivedAt<=world.clock);
function record(world,node,kind,text,extra={}){
  const fact={id:randomUUID(),node,kind,text,occurredAt:world.clock,receivedAt:world.clock,...extra};
  world.facts.push(fact);
  return fact;
}
// Only a sourced, unverified report may be proposed in narrative output.
export function adoptStoryDelta(world,raw,requestId) {
  if(!raw.includes('<map_delta')&&!raw.includes('</map_delta'))return raw;
  const match=/^([\s\S]+?)\s*<map_delta>([^<>]+)<\/map_delta>\s*$/.exec(raw);
  if(!match || !match[1].trim() || /<\/?map_delta/.test(match[1]))throw Error('地点增量格式无效');
  const delta=JSON.parse(match[2]);
  if(!delta || Object.keys(delta).sort().join()!=='kind,node,quote' || delta.kind!=='rumor' ||
     !lookup(delta.node) || typeof delta.quote!=='string' || !delta.quote.trim() ||
     delta.quote.length>300 || !match[1].includes(delta.quote) || !delta.quote.includes(delta.node))
    throw Error('传闻须引用本轮正文中的同地原话');
  record(world,delta.node,'rumor','未经核实的转述：'+delta.quote,{source:requestId});
  return match[1].trimEnd();
}
export function startRegister(){return {clock:0,facts:[],pending:null};}
export function registerView(world){
  const locations=sites.map(([placeId,name,opening])=>({placeId,name,control:holder(world,name),sources:['开局登记：'+opening]}));
  const known=world.facts.filter(f=>f.receivedAt!=null&&f.receivedAt<=world.clock).map(f=>({...f,placeId:lookup(f.node)[0]}));
  const actions=[overview];
  if(world.pending)actions.push(world.pending.kind==='order'?sign:confirm,cancel);
  else for(const c of cases){
    const facts=delivered(world,c.node);
    if(!facts.some(f=>f.kind==='order'))actions.push(c.order);
    else if(!facts.some(f=>f.kind==='receipt'))actions.push(c.deliver);
    else if(!facts.some(f=>f.kind==='settled'))actions.push(label(c));
  }
  return {date:'崇祯元年七月开局 · 第 '+world.clock+' 刻',observer:'崇祯帝朱由检',locations,known,actions};
}
function playerAction(w,input){
  const text=input.trim().replace(/[。！]$/,'');
  if(!registerView(w).actions.includes(text)){
    if([sign,confirm,cancel,...cases.flatMap(c=>[c.deliver,c.order])].includes(text))throw Error('该操作当前不可执行');
    return null;
  }
  if(text===cancel){w.pending=null;return '你收回案头的提议，各地登记沿旧。';}
  if(text===sign){
    const c=cases.find(c=>c.node===w.pending.node);
    w.pending=null;
    return playerAction(w,c.order);
  }
  if(text===confirm){
    const {node,source}=w.pending;
    const receipt=delivered(w,node).find(f=>f.id===source&&f.kind==='receipt');
    if(!receipt||holder(w,node)!==receipt.before)throw Error('交割凭据失效');
    const body='你核准交割。'+node+'由'+receipt.before+'改登为'+receipt.after+'，其余各地沿旧。';
    record(w,node,'settled',body,{source,before:receipt.before,after:receipt.after});
    w.pending=null;
    return body;
  }
  for(const c of cases){
    if(text===c.order){
      record(w,c.node,'order',text+'。');
      return '你在旨上落印。'+c.node+'仍照旧册登记，等候当地回文。';
    }
    if(text===c.deliver){
      const order=delivered(w,c.node).find(f=>f.kind==='order');
      const occurredAt=w.clock;
      w.clock++; // The local receipt predates its arrival in the capital.
      const receipt=record(w,c.node,'receipt',c.report,{source:order.id,occurredAt,before:holder(w,c.node),after:c.to});
      return receipt.text+'京师尚未核准变更。';
    }
  }
  return null;
}
const native=(name,description,properties)=>({type:'function',function:{name,description,parameters:{type:'object',properties,required:Object.keys(properties),additionalProperties:false}}});
const nodeField={type:'string',enum:['台湾',...sites.map(r=>r[1])]};
const orderTool=native('propose_place_order','将玩家明确要求签发的地点事务列为旨稿，须玩家签发。询问、转述或否定时不要拟旨。',{node:{type:'string',enum:cases.map(c=>c.node)}});
const read=native('query_map','读取本档当前登记与京师已收到的文书。台湾返回三项。',{node:nodeField});
export const storyMapRead=native('query_map','查询所问地点的当前登记，以及京师已收到的、关于该地点的文书。node 填文书所涉或玩家询问的地点：在京师问大员填“大员”，问台湾各地填“台湾”（返回三项）。只在续写需要的地点事实缺失时查询；已附回执足以接着写时，直接续写。',{node:nodeField});
const propose=native('propose_map_change','仅凭本轮查询返回的接收文书拟议该地点交割，等待玩家确认。',{node:{type:'string',enum:cases.map(c=>c.node)},source:{type:'string'}});
export const registerPair=Object.freeze([read.function.name,orderTool.function.name]);
export const registerTools=[read,atlasTool,orderTool,propose];
function mapQuery(w,args){
  if(Object.keys(args??{}).join()!=='node'||!nodeField.enum.includes(args.node))throw Error('地图地点无效');
  const rows=args.node==='台湾'?island:[lookup(args.node)];
  return {kind:'current_register',entries:rows.map(([,node,opening])=>({node,opening,holder:holder(w,node),evidence:delivered(w,node)}))};
}
export async function registerTurn(w,input,dispatch,toolResults=[],readTool=read){
  const action=playerAction(w,input);
  if(action!==null)return action;
  let packet={messages:[{role:'system',content:'当前登记与开局地理分开。按京师当时收到的文书叙事。大员、鸡笼、岛内各社分别登记，旨意和报告不改变归属。'},{role:'user',content:input}],
    tools:w.pending?[readTool,atlasTool]:[readTool,atlasTool,orderTool],tool_choice:'auto'};
  const response=parseResult(await dispatch(packet),packet.tools,{allowedPair:registerPair});
  const first=response.kind==='pair'?response.calls[1]:response;
  if(first.kind==='final')return first.receipt.text;
  if(first.name==='propose_place_order'){
    const a=first.args;
    const c=cases.find(c=>c.node===a?.node);
    if(!c||Object.keys(a).join()!=='node'||!registerView(w).actions.includes(c.order))throw Error('该地当前不能签发这份旨意');
    if(response.kind==='pair'){
      const query=response.calls[0];
      const result=mapQuery(w,query.args);
      if(!result.entries.some(entry=>entry.node===c.node))throw Error('旨稿地点须在同批查询范围内');
      toolResults.push({tool_call_id:query.callId,name:query.name,result},
        {tool_call_id:first.callId,name:first.name,result:{kind:'draft',node:c.node}});
    }
    w.pending={node:c.node,kind:'order'};
    return '案头拟旨：'+c.order+'。待你签发。';
  }
  const result=first.name==='query_map'?mapQuery(w,first.args):queryAtlas(first.args,{islandOverview:true});
  const receipts=(result.entries??[]).flatMap(row=>row.evidence.filter(f=>f.kind==='receipt'&&row.holder===f.before));
  packet=continuation(packet,first.message,result,receipts.length&&!w.pending?[propose]:[]);
  const next=parseResult(await dispatch(packet),packet.tools);
  if(next.kind==='final')return next.receipt.text;
  const a=next.args;
  const receipt=receipts.find(f=>f.node===a?.node&&f.id===a?.source);
  if(!receipt||Object.keys(a).sort().join()!=='node,source')throw Error('提案须引用本轮查到的同地接收文书');
  w.pending={node:receipt.node,source:receipt.id};
  return receipt.node+'交割拟案：'+receipt.before+' → '+receipt.after+'。待你确认，现有登记沿旧。';
}
