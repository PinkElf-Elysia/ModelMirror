import {cultivationSource,QUALITY} from '../../agent-core/context/cultivation-source.mjs';
import {assemblePrepared,material} from '../../agent-core/context/assemble.mjs';
import {queries,assertReady} from '../free-action-e2/source.mjs';
import {hash,need,clone} from '../../agent-core/contracts.mjs';
export const CONTRACT=`本次仅修订一份赠送意图草稿，禁止执行、推进剧情、写入状态或替人物同意。
返回唯一 JSON：{"changes":[{"field":"target|item|quantity|condition","value":"值或null","quote":"本次用户原文中的直接引文"}],"questions":[],"unsupported":false}。
changes 仅含本次真正修改项。target/item 为登记的原 ID；quantity 为正整数；condition 保留玩家原话。
不重复返回全部意图；省略项由宿主保留。明确否定旧对象时用null清除；语义不明先 questions，不猜取消整项计划。
对象必须由玩家点名或已经明确绑定，不能默认唯一登记人物。数量缺失不填1；“一些”保持未明确。
“周禾”“两份”可作为对当前问题的回答；存在多个指向时询问。给出精确引文，数量引文如“两份”。
人物是否在场/愿意接受仍是未知世界条件；玩家声称已同意也仅存为带来源的condition，不能成为世界事实。
不输出步骤、人物关系描述、容器位置、状态增量、推理过程。无法支持的新目标用unsupported及questions说明，不能偷偷改为赠送。
模型解释是待玩家核对的候选，绝不构成执行授权。`;
export async function createBuilder(){
 const adapter=await cultivationSource(),keep=new Set(['rule-3','rule-4','rule-5','rule-6','rule-7','rule-17','rule-18']);
 return (world,current,input)=>{
  assertReady(world);need(typeof input==='string'&&input.trim()&&input.length<=2000,'INPUT_TEXT');
  const scope={session:world.session,stateRevision:world.version},all=adapter.materials(scope);
  const materials=all.filter(m=>keep.has(m.id)||m.purpose==='world-reference');
  materials.push(material(scope,'proposal-behavior','global-rule','instruction',QUALITY.split('\n').slice(0,2).join('\n'),{path:'agent-core/context/cultivation-source.mjs',hash:hash(QUALITY)},{required:true}));
  materials.push(material(scope,'current-state','state','confirmed',JSON.stringify({profile:world.state.profile,queries:queries(world)}),{kind:'local-state',hash:hash(world.state)},{required:true}));
  materials.push(material(scope,'e3-contract','output-contract','instruction',CONTRACT,{path:'feature-exploration/free-action-e3/context.mjs',hash:hash(CONTRACT)},{required:true}));
  const carry=current?{intent:current.intent,questions:current.questions,worldConditions:current.worldConditions,status:current.status}:null;
  const request=assemblePrepared({scope,materials,selection:{mode:'scoped',refs:['scene:qingyun']},bindings:adapter.bindings,replacePrefix:'',
   messages:[{role:'system',content:''},{role:'user',content:JSON.stringify({currentProposal:carry,reply:input})}]});
  return {...request,adapter:'cultivation-clarification/3',input,carry:clone(carry),objects:{items:world.state.items.map(({id,name})=>({id,name})),relations:world.state.relations.map(({id,name})=>({id,name}))},
   provider:'offline-simulated',realCalls:0};
 };
}
