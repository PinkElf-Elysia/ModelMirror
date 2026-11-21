import {randomUUID} from 'node:crypto';
import {assemble} from '../assembly.mjs';
import {date, places, newRun, opening} from '../../feature-exploration/ancient-map/world.mjs';
import {agentView, command, initialize, turn} from '../../feature-exploration/ancient-map/agent.mjs';
import {offlineNarrator, finalText} from './transport.mjs';

export const binding = 'ancient-liaodong/1';
export const prompts = {
  ask:'京师现在能确认宁远的情形吗？', report:'推进至关报抵京', draft:'请依据关报拟定宁远城防移交之旨。',
  decree:'确认这份旨意', receive:'推进至接防回执送达', handover:'请依据接防回执核对宁远交割。',
  settle:'确认这份交割', cancel:'撤回这份提案'
};
export function emptySession() {
  const id=randomUUID();
  return {binding,id,name:'古代人生 · 京师—辽东 · '+id.slice(0,6),revision:0,characterText:'',turns:[],requests:{},world:null,dispatches:[]};
}
// Facts and receipt of news have different clocks. Only delivered records enter
// the exploration adapter. An occurrence, by itself, is never model context.
function occurred(world, kind, text, source=null) {
  const fact={id:randomUUID(),kind,node:'宁远',text,occurredAt:world.clock,source,deliveries:[]};
  world.facts.push(fact);
  return fact;
}
export function knowledge(world,recipient,at=world.clock) {
  return world.facts.flatMap(f=>{
    const delivery=f.deliveries.find(d=>d.recipient===recipient && d.at<=at);
    return delivery ? [{id:f.id,kind:f.kind,text:f.text,source:f.source,occurredAt:f.occurredAt,receivedAt:delivery.at}] : [];
  });
}
function receive(fact,recipient,at){fact.deliveries.push({recipient,at});}
function startWorld(input) {
  const person=opening('scroll',input);
  if(person.role!=='court')throw Error('此切片请使用帝王之路的崇祯帝朱由检命书。');
  const world=Object.assign(initialize(newRun(person)),{clock:0,facts:[]});
  receive(occurred(world,'report','宁远军士因欠饷鼓噪，失城之说未核。'),'camp',0);
  return world;
}
function syncEvidence(world,action) {
  const fact=world.facts.find(f=>f.kind===(action==='report'?'report':'receipt'));
  command(world,action);
  const e=world.agent.events.at(-1);
  // Keep the original event identity through transmission and subsequent proposals.
  e.id=fact.id;
  receive(fact,'court',world.clock);
}
function projection(world) {
  const view=agentView(world);
  const known=knowledge(world,'court');
  const p=world.agent.pending;
  const controls=[prompts.ask];
  if(view.options.report)controls.push(prompts.report);
  if(view.options.receive)controls.push(prompts.receive);
  if(known.some(e=>e.kind==='report')&&!world.agent.events.some(e=>e.kind==='decree'))controls.push(prompts.draft);
  if(known.some(e=>e.kind==='receipt')&&world.control==='明')controls.push(prompts.handover);
  return {date,observer:world.person.name,locations:view.places,known,
    actions:p?[p.kind==='draft'?prompts.decree:prompts.settle,prompts.cancel]:controls};
}
export function publicSession(s) {
  return {id:s.id,name:s.name,mode:'offline',characterText:s.characterText,revision:s.revision,
    turns:s.turns,requests:s.requests,frontier:s.world?projection(s.world):null};
}
function deterministicAction(world,input) {
  const key=Object.keys(prompts).find(k=>prompts[k]===input.trim().replace(/[。！]$/,''));
  if(!['report','receive','decree','settle','cancel'].includes(key))return null;
  if(!projection(world).actions.includes(prompts[key]))throw Error('这项操作当前不可执行，请核对最新回合。');
  if(key==='report') {
    syncEvidence(world,'report');
    return '驿使将关报呈到御前。奏中只说宁远军士因欠饷鼓噪，未能坐实失城；京师的城防登记仍属明。';
  }
  if(key==='receive') {
    syncEvidence(world,'receive');
    return '接防文书送抵京师，所载双方与前旨相符。尚待你核对交割，宁远的控制登记仍属明。';
  }
  const p=world.agent.pending;
  command(world,key==='cancel'?'cancel':'approve');
  const e=world.agent.events.at(-1);
  const fact={...e,text:key==='cancel'?'提案已撤回':key==='decree'?'移交旨意已签发，控制登记未变。':'本档推演：宁远交割已确认，由明改登后金。',occurredAt:world.clock,deliveries:[]};
  receive(fact,'court',world.clock);world.facts.push(fact);
  if(key==='decree'){
    receive(occurred(world,'receipt','本档模拟：接防文书签收，待京师核对。',e.id),'camp',world.clock);
    return '你在拟旨上落印。旨意发出，尚须接防凭据，宁远仍登记在明廷名下。';
  }
  if(key==='settle')return '你核过接防回执，准予交割。簿上只改宁远一处：明 → 后金。北京、山海关、锦州的登记沿旧。';
  return p.kind==='draft'?'旨稿收回，未曾发出。':'交割提案已撤回，控制登记沿旧。';
}
function requestFor(s,input,packet) {
  const history=s.turns.flatMap(t=>[{role:'user',content:t.input},{role:'assistant',content:t.raw}]);
  const messages=assemble({input,history});
  messages[0].content+='\n本档京师—辽东四地控制登记、获报及交割以宿主工具回执为准。只叙述当前视角所得；状态栏这些字段由宿主绘制。拟旨与交割分别确认。\n'+packet.messages[0].content;
  return {...packet,messages:[...messages,...packet.messages.slice(2)]};
}
function escaped(text){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');}
export async function play(s,{input,revision,requestId},generate=offlineNarrator) {
  if(s.binding!==binding)throw Error('存档适配版本不匹配');
  if(typeof requestId!=='string'||!/^[a-f0-9-]{36}$/.test(requestId)||typeof input!=='string'||!input.trim()||input.length>12000)throw Error('无效回合');
  const old=Object.hasOwn(s.requests,requestId)&&s.requests[requestId];
  if(old){if(old.input!==input||old.revision!==revision)throw Error('同一操作编号内容不同');return;}
  if(revision!==s.revision)throw Error('回合版本已变更，请刷新');
  const record={requestId,stages:[]};
  async function dispatch(packet) {
    const request=requestFor(s,input,packet);
    const output=await generate(request);
    record.stages.push({functions:request.tools.map(t=>t.function.name),messages:request.messages.length,output});
    return output;
  }
  let raw;
  if(!s.world){
    s.world=startWorld(input);s.characterText=input;
    const out=await dispatch({messages:[{role:'system',content:'按命书进入京师。'},{role:'user',content:input}],tools:[],tool_choice:'none'});
    raw=finalText(out);
  }else{
    s.world.clock++;
    raw=deterministicAction(s.world,input);
    if(raw===null){
      const knownIds=new Set(knowledge(s.world,'court').map(e=>e.id));
      const scoped={...s.world,agent:{...s.world.agent,events:s.world.agent.events.filter(e=>knownIds.has(e.id))}};
      await turn(scoped,input,dispatch);
      s.world.agent.pending=scoped.agent.pending;
      raw=scoped.agent.turns.at(-1).body;
    }
  }
  s.turns.push({requestId,input,raw,html:'<p>'+escaped(raw)+'</p>',at:new Date().toISOString()});
  s.dispatches.push(record);
  s.requests[requestId]={status:'complete',input,revision};
  s.revision++;
}
