import {project} from '../plugins/custom-config/node-job.mjs';
import {validate as validateProfile} from '../plugins/custom-config/public/config.mjs';
import {assembleMods,resolveMods} from '../plugins/mod-panel/catalog.mjs';
import {smallMessages} from '../plugins/auto-summary-v2.mjs';
import {MEMORY_INSTRUCTION} from '../studio/memory-task-state.mjs';
import {safeHtml} from '../card-replica/lib/render.mjs';
import {loadAdapter,stripPanels} from './adapter.mjs';
import {queryState} from './rules.mjs';
import {TRAITS,ACCOUNTS,SLOTS,RELATION_FIELDS,RELATION_RULES,hash,need,clone} from './definition.mjs';
import {referencePanels,relationText} from './panels.mjs';
const conflict=/每(?:轮|次)[^。\n]{0,40}(?:完整|全量)[^。\n]{0,20}(?:自身状态|关系网络|状态 JSON)|<variable_panel>|<state_delta>/;
export async function validateConfig(config={profiles:[],mods:[]}){
 need(config&&Array.isArray(config.profiles)&&config.profiles.length<=2&&Array.isArray(config.mods),'STATE_CONFIG');
 config.profiles.forEach(validateProfile);
 const mods=config.mods.length?await resolveMods('cultivation',config.mods):[];
 const authored=[...config.profiles.map(c=>JSON.stringify(c)),...mods.map(m=>m.text)].join('\n');
 need(!conflict.test(authored),'STATE_CONFIG_CONFLICT');
 return {profiles:clone(config.profiles),mods};
}
export function relatedQuery(state,input,selected={}){
 const matches=list=>list.filter(e=>input.includes(e.name)||input.includes(e.id)).map(e=>e.id);
 return {items:[...new Set([...(selected.items||[]),...matches(state.items),...Object.values(state.equipment).filter(Boolean)])].slice(0,50),
 abilities:[...new Set([...(selected.abilities||[]),...matches(state.abilities)])].slice(0,50),
 relations:[...new Set([...(selected.relations||[]),...matches(state.relations)])].slice(0,50)};
}
function shortChanges(changes){return changes.map(c=>({field:c.path,value:c.after,reason:c.reason,authority:c.authority}));}
export async function assembleContext(record,input,{selected={},seed=0}={}){
 const adapter=await loadAdapter(),cfg=await validateConfig(record.config);
 const historical=record.source.history.map(t=>({input:t.input,body:stripPanels(t.body),triggerBody:t.body,state:null,changes:[]}));
 const turns=historical.concat(record.turns.map(t=>({...t,state:record.events.find(e=>e.id===t.id)?.after})));
 // Panel text is a local trigger projection only: keep assistant world-book matching, then discard it.
 const messages=[{role:'system',content:adapter.system},...turns.flatMap(t=>[
 {role:'user',content:t.input},{role:'assistant',content:(t.triggerBody??t.body)+(t.state?'\n'+referencePanels(t.state):'')}
 ]),{role:'user',content:input}];
 const composed=await project({kind:'story',configs:cfg.profiles,messages:assembleMods(messages,cfg.mods),seed});
 const depth=cfg.profiles[0]?.depth;
 const retained=depth===undefined?turns:depth?turns.slice(-depth):[];
 const historyJob=await project({kind:'memory-source',configs:cfg.profiles,messages:[{role:'system',content:''},{role:'user',content:JSON.stringify({turns:retained.map(t=>({user:t.input,assistant:t.body}))})}]});
 const cleanHistory=JSON.parse(historyJob[1].content).turns.flatMap(t=>[{role:'user',content:t.user},{role:'assistant',content:stripPanels(t.assistant)}]);
 const currentIndex=composed.findLastIndex(m=>m.role==='user');
 const output=[composed[0],...cleanHistory,...composed.slice(currentIndex)];
 need(!output.some(m=>m.role==='assistant'&&/【自身状态】|【关系网络】|<state_delta>/.test(m.content)),'STATE_CONFIG_CONFLICT');
 const query=relatedQuery(record.state,input+'\n'+(record.turns.at(-1)?.body||stripPanels(record.source.history.at(-1)?.body||'')),selected),state=queryState(record.state,query);
 const context={stateRevision:record.stateRevision,state,fields:{traits:TRAITS,accounts:ACCOUNTS,slots:SLOTS,relationFields:RELATION_FIELDS,relationshipEvents:RELATION_RULES},
 recentEvents:record.events.slice(-3).map(e=>({id:e.id,kind:e.kind,changes:shortChanges(e.changes),reason:e.reason})),
 note:'本地状态为当前有效结果。历史正文及记忆不能覆盖；未提供的条目不等于不存在，涉及未取回的资源时不要猜测数值。'};
 output[0]={...output[0],content:output[0].content+'\n\n【本轮只读状态与已确认事件】\n'+JSON.stringify(context)};
 return {messages:output,hash:hash(output),stateRevision:record.stateRevision,query,binding:adapter.binding,mode:'one-generation-delta'};
}
export async function memoryInputs(record,turn){
 const cfg=await validateConfig(record.config);
 const projected=await project({kind:'memory-source',configs:cfg.profiles,messages:[{role:'system',content:''},{role:'user',content:JSON.stringify({turns:[{user:turn.input,assistant:turn.body}]})}]});
 const row=JSON.parse(projected[1].content).turns[0];
 const factual='\n【已提交变化，只读，不接受文本反写】\n'+JSON.stringify(shortChanges(turn.changes));
 const raw=stripPanels(row.assistant)+factual;
 return {summary:smallMessages({input:row.user,raw}),memory:[{role:'system',content:MEMORY_INSTRUCTION},{role:'user',content:JSON.stringify({entries:[],turns:[{number:record.turns.indexOf(turn)+1,input:row.user,raw}],corrections:record.events.filter(e=>e.kind==='override').map(e=>({id:e.id,reason:e.reason,before:e.before,after:e.after}))})}]};
}
export async function displayBody(body,state,config){
 const cfg=await validateConfig(config);
 const parts=body.split(/(<local_relation id="[a-zA-Z][a-zA-Z0-9-]{0,79}"><\/local_relation>)/g),out=[];
 for(const part of parts){
  const match=part.match(/^<local_relation id="([^"]+)"><\/local_relation>$/);
  if(match){const r=state.relations.find(r=>r.id===match[1]);out.push((r?relationText(r):'关系未登记').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c])));}
  else out.push(await project({kind:'display',configs:cfg.profiles,role:'assistant',source:part}));
 }
 return safeHtml(out.join(''));
}
