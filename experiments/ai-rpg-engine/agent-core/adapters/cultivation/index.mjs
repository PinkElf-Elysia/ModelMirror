import {clone,hash} from '../../contracts.mjs';
import {validateState} from '../../../local-state/definition.mjs';
import {candidate} from './rules.mjs';
import {loadToolsAdapter,assembleToolsContext,INSTRUCTION} from '../../../local-state/tools/adapter.mjs';
import {TOOL,validateQueries,queryLocalState} from '../../../local-state/tools/query.mjs';
import {validateConfig,displayBody,memoryInputs} from '../../../local-state/context.mjs';
import {renderPanels} from '../../../local-state/panels.mjs';
import {exampleState,OPENING} from '../../../local-state/example.mjs';
import {simulatedReply,SCENARIOS} from '../../../local-state/simulator.mjs';
export async function cultivationAdapter(){
 const legacy=await loadToolsAdapter();
 return {id:'cultivation',name:'超级修仙大世界',description:'原卡状态模式的公共核心适配；成年中性样例，原始提示词保持。',opening:OPENING,
 binding:{adapterId:'cultivation',adapterVersion:'3',stateSchemaVersion:'local-state/1',rulesVersion:'cultivation-rules/3',sourceHash:legacy.binding.sourceHash,effectiveContextHash:hash({system:legacy.system,instruction:INSTRUCTION,tool:TOOL}),manifestHash:'0'.repeat(64)},
 ownership:{profile:'confirmed-anchor',marks:'model-judgment; append-only',factions:'model-judgment',traits:'model-judgment',abilities:'model-judgment',items:'model-judgment; quantity:rule-calculation',balances:'rule-calculation',relations:'model-judgment; relation/trust:rule-calculation',equipment:'validated-reference'},
 initial:exampleState,validateState,validateConfig,assemble:assembleToolsContext,candidate,render:renderPanels,display:displayBody,memory:memoryInputs,
 query:{tool:clone(TOOL),validate:validateQueries,execute:queryLocalState},
 scenarios:[['query-use','先查询丹药，再服用两份养息丹'],['query','查看全部已登记丹药'],['ability','比较已学功法'],...SCENARIOS],
 fixture(request,{input,state,variant='calm'}){
  if(request.tool_choice==='auto'&&/查看|查询|比较/.test(input)){
   const queries=[/功法/.test(input)?{domain:'abilities',kind:'功法'}:/沉香/.test(input)?{domain:'items',name:'沉香'}:{domain:'items',kind:'丹药'}];
   return {query:{queries}};
  }
  if(request.tool_choice==='none'&&!input.includes('服用')){
   const result=JSON.parse(request.messages.at(-1).content),first=result.results[0];
   const description=first.entries.map(e=>e.name+(e.quantity===null?'（数量未设定）':e.quantity!==undefined?' × '+e.quantity: e.progress?'：'+e.progress:'')).join('、');
   return {text:(description?'你核对已登记的档案：'+description+'。':'当前登记范围内没有匹配条目；未登记信息仍不明确。')+'\n周禾将登记簿放回原处，等你决定下一步。\n<state_delta>{"operations":[]}</state_delta>'};
  }
  return {text:simulatedReply(input,state,{variant}).text};
 }};
}
