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
// Pre-fix openings could persist even an unfinished protocol suffix. Reading
// them must neither replay a delta nor invalidate the immutable turn.
export function storyHistoryText(raw) {
  const at=raw.search(/<\/?map_delta\b/);
  return at===-1?raw:raw.slice(0,at).trimEnd();
}
// A short quote can identify a whole spoken sentence. Keep the surrounding
// speech (including its source and qualifications) in the registered fact.
function rumorEvidence(prose, quote) {
  const narrative=prose.split(/<details\b|<summary\b|^\s*(?:\x60{3}|【(?:世界动态|接下来|时间与环境)】)/im,1)[0]
    .replace(/<!--[\s\S]*?(?:-->|$)/g,'\n');
  if(!narrative.includes(quote))return null;
  const normalize=s=>s.trim().replace(/[。！!]$/u,'');
  const selected=normalize(quote);
  // Denial of receiving a report differs from reporting a negative fact.
  const unspoken=/(?:无人|没人|没有|并未|未曾|不曾|不敢)(?:向.{1,6}?)?(?:听说|听闻|收到|见到|来报|禀报|说|转述)|假如|倘若|如果|是否|可曾/u;
  const intendedSpeech=/(?:正要|本想|打算|准备|意欲|想要|差点)(?:开口)?(?:说道|回道|禀道|答道|转述|禀报|说)/u;
  const speech=/(?:^|(?<=[。！？!?\n”」]))[ \t]*([^\s。！？；，、：:“”「」<>]{1,30}(?:说道|回道|禀道|答道|转述|禀报|说))[:：]\s*(?:“([^”\n]{1,300})”|「([^」\n]{1,300})」)/gu;
  for(const match of narrative.matchAll(speech)){
    const [source,speaker,curly,corner]=match, words=curly??corner;
    const first=words.split(/[。！？!?]/u,1)[0];
    if(![normalize(source),normalize(words),first].includes(selected))continue;
    if(/[？?]/u.test(words) || intendedSpeech.test(speaker) || unspoken.test(speaker) || unspoken.test(first))continue;
    return {source,text:words+'（'+speaker+'）'};
  }
  // Also support a self-contained attributed report, e.g. 商人说鸡笼换了旗，尚待核实。
  const outsideSpeech=narrative.replace(/“[^”]*(?:”|$)|「[^」]*(?:」|$)/gu,' ');
  for(const unit of outsideSpeech.match(/[^。！？!?；\n]+[。！？!?]?/gu)??[]){
    const sentence=unit.trim();
    if(normalize(sentence)!==selected || !/^[\p{Script=Han}]{1,10}(?:说|转述|禀报)/u.test(sentence))continue;
    if(/[？?]/u.test(sentence) || intendedSpeech.test(sentence) || unspoken.test(sentence))continue;
    return {source:sentence,text:sentence};
  }
  return null;
}
export function inspectStoryDelta(raw) {
  if(!/<\/?map_delta/.test(raw))return {prose:raw};
  const match=/^([\s\S]+?)\s*<map_delta>([^<>]*)<\/map_delta>\s*$/.exec(raw);
  if(!match || !match[1].trim() || /<\/?map_delta/.test(match[1]))throw Error('地点增量格式无效');
  const prose=match[1].trimEnd();
  let delta;
  try{delta=JSON.parse(match[2]);}catch{return {prose,rejection:'invalid_json'};}
  if(!delta || Object.keys(delta).sort().join()!=='kind,node,quote' || delta.kind!=='rumor')
    return {prose,rejection:'invalid_rumor'};
  if(!lookup(delta.node))return {prose,rejection:'unknown_location'};
  if(typeof delta.quote!=='string' || !delta.quote.trim() || delta.quote.length>300 ||
     !delta.quote.includes(delta.node))
    return {prose,rejection:'unsupported_quote'};
  const evidence=rumorEvidence(prose,delta.quote);
  return evidence?{prose,delta,evidence}:{prose,rejection:'unsupported_quote'};
}
export function adoptStoryDelta(world,raw,requestId,onRejected) {
  const {prose,delta,evidence,rejection}=inspectStoryDelta(raw);
  if(rejection){
    if(!onRejected)throw Error('地点增量未采用：'+rejection);
    onRejected(rejection);
  }
  if(delta)record(world,delta.node,'rumor','未经核实的转述：'+evidence.text,{source:requestId,sourceText:evidence.source});
  return prose;
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
const native=(name,description,properties,required=Object.keys(properties))=>({type:'function',function:{name,description,parameters:{type:'object',properties,required,additionalProperties:false}}});
const nodeField={type:'string',enum:['台湾',...sites.map(r=>r[1])]};
const orderTool=native('propose_place_order','将玩家明确要求签发的地点事务列为旨稿，须玩家签发。询问、转述或否定时不要拟旨。',{node:{type:'string',enum:cases.map(c=>c.node)}});
const read=native('query_map','读取本档当前登记与京师已收到的文书。台湾返回三项。',{node:nodeField});
export const storyMapRead=native('query_map','查询所问地点的当前登记，以及京师已收到的、关于该地点的文书。node 填文书所涉或玩家询问的地点：在京师问大员填“大员”，问台湾各地填“台湾”（返回三项）。核对已送达的文书、消息来源或凭据时，use 选 documents，宿主直接呈卷。玩家要求推演剧情、解说守方或办理行动，且需要查图辅助时，use 选 scene，查询后继续叙事。未填 use 时直接呈卷。',{
  node:nodeField,use:{type:'string',enum:['documents','scene']}
},['node']);
const propose=native('propose_map_change','仅凭本轮查询返回的接收文书拟议该地点交割，等待玩家确认。',{node:{type:'string',enum:cases.map(c=>c.node)},source:{type:'string'}});
export const registerPair=Object.freeze([read.function.name,orderTool.function.name]);
export const registerTools=[read,atlasTool,orderTool,propose];
function mapQuery(w,args){
  if(Object.keys(args??{}).join()!=='node'||!nodeField.enum.includes(args.node))throw Error('地图地点无效');
  const rows=args.node==='台湾'?island:[lookup(args.node)];
  return {kind:'current_register',entries:rows.map(([,node,opening])=>({node,opening,holder:holder(w,node),evidence:delivered(w,node)}))};
}
const entryKinds={rumor:'未经核实的转述',order:'已签旨意',receipt:'已送达回执',settled:'已核准交割'};
function readPurpose(call,readTool){
  if(call.name!=='query_map'||readTool!==storyMapRead)return null;
  const use=Object.hasOwn(call.args??{},'use')?call.args.use:'documents';
  if(!['documents','scene'].includes(use))throw Error('地图读取用途无效');
  return use;
}
function courtRegisterRead(w,call,use){
  if(!use)return mapQuery(w,call.args);
  const args={...call.args};
  delete args.use;
  const result=mapQuery(w,args);
  return {...result,scope:{observer:'京师御前',asOf:w.clock,
    coverage:'仅含所列地点截至本刻送达御前的本档记录。传闻、旨意、接收回执和核准交割分别列示。无记录表示卷中无凭据，事由仍待核实。'}};
}
function presentRegister(result){
  const lines=['你请值房翻开御前登记簿，逐项核对截至第'+result.scope.asOf+'刻可查的记录。'];
  for(const row of result.entries){
    lines.push(row.node+'，簿上登记为'+row.holder+'。');
    if(!row.evidence.length)lines.push('御前尚无关于此地的新报。所问文书在本档无从核实。');
    for(const fact of row.evidence){
      const text=fact.text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
      lines.push('第'+fact.receivedAt+'刻 · '+entryKinds[fact.kind]+'｜'+text);
    }
    if(row.evidence.length&&!row.evidence.some(f=>f.kind==='receipt'))lines.push('以上登记中尚无接收回执。');
  }
  return lines.join('\n');
}
export async function registerTurn(w,input,dispatch,toolResults=[],readTool=read){
  // An explicit lookup remains available even when a model would choose the wrong task.
  const lookupCommand=readTool===storyMapRead && /^\/查卷\s+(.+)$/u.exec(input.trim());
  if(lookupCommand){
    const result=courtRegisterRead(w,{args:{node:lookupCommand[1].trim()}},'documents');
    toolResults.push({name:'query_map',use:'documents',origin:'player',result});
    return presentRegister(result);
  }
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
      // Older paired calls already express their action purpose via the proposal.
      const use=readPurpose(query,readTool);
      if(use && query.args?.use==='documents')throw Error('核卷不能同时拟旨');
      const result=courtRegisterRead(w,query,use);
      if(!result.entries.some(entry=>entry.node===c.node))throw Error('旨稿地点须在同批查询范围内');
      toolResults.push({tool_call_id:query.callId,name:query.name,result},
        {tool_call_id:first.callId,name:first.name,result:{kind:'draft',node:c.node}});
    }
    w.pending={node:c.node,kind:'order'};
    return '案头拟旨：'+c.order+'。待你签发。';
  }
  const use=readPurpose(first,readTool);
  const result=first.name==='query_map'?courtRegisterRead(w,first,use):queryAtlas(first.args,{islandOverview:true});
  if(use==='documents'){
    toolResults.push({tool_call_id:first.callId,name:first.name,use,result});
    return presentRegister(result);
  }
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
