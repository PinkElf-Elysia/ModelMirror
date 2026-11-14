import {clone,need,keys,bounded} from '../../agent-core/contracts.mjs';
export const CONTRACT='仅整理玩家计划，不执行、不叙述已发生。最多三个有序步骤，每步最多一个原文条件；不自行补步骤、嵌套条件、循环或并行。返回 JSON {ops:[],question:null,limit:null}。ops仅add/revise/remove，使用给定行动ID、对象ID、稳定步骤ID。add:{type,action,changes:[{field,value,quote}],condition:null或{kind:prerequisite|player-claim,value,quote},quote}；revise增加stepId，action可null，不修改条件时省略condition；remove:{type,stepId,quote}。每个quote逐字来自当前输入，condition.value必须等于quote并逐字保留原文标点。玩家陈述不是世界事实。歧义时ops为空并只问一个关键问题，不预填对象/数量。超限或不支持时ops为空，limit说明原因，不截取或偷换行动。原有步骤顺序不可通过删除再添加偷偷改写，不输出世界状态或工具调用。'+'\n条件边界：单步条件只是挂在某一步上的原文备注，不是要你执行、核实或展开条件分支。若条件不满足仅不做该步、没有另一项替代行动，仍可记录为该步的 prerequisite；不要仅因出现“如果”“否则”“不做”或条件尚待未来核实就把整份计划标为超限。例如“给甲一份药；如果他不收，就不送。”只记录一个赠送步骤及原文条件，不增加“不送”步骤，不推断甲已接受，也不改写条件为正向表达。真正要求条件成立与否分别采取不同动作（如“同意就送药，否则卖药”）、嵌套条件、循环或并行流程才属于本轮不支持的控制流，ops为空并用limit说明，不能截取可支持部分。若不清楚条件属于哪一步，ops为空、question只问一个归属问题，不自行绑定。玩家说“已经答应”等只可记录为 player-claim，不能变成条件已满足；每步仍最多一个条件，值与quote逐字保留原文和标点，条件不能携带子步骤或执行指令。'+'\n提案槽位与规则参数分开：capabilities.parameters描述宿主规则参数，不是你输出的changes结构。行动ID只写在步骤op.action；add必须给出当前capabilities中已声明的action，revise保留行动时action为null；不得把action或actionId放入changes。changes.field的白名单按最终行动确定：use_medicine、fuel_lamp仅item和quantity，每次最多2项；gift仅target、item、quantity，每次最多3项；同一field不得重复。item填写facts.resources登记ID，target填写facts.people登记ID，quantity为玩家明确提供的正安全整数；quote逐字取自当前输入，数量quote仅引用明确数量短语。缺失或有歧义的槽位保持未明确，不猜测；修订只输出实际变化的槽位，未列出的沿用，明确撤回槽位才用value:null。每条changes只能含field、value、quote三个键。禁止将itemId、targetId、actionId、parameters或其他规则参数名用作提案字段；即使capabilities.parameters.required列出它们，也不能照抄。规则示例中的itemId与提案item不是输出别名，宿主不会自动改名或删除错误字段；错误提案将整批拒绝。E5条件只放在步骤op.condition:{kind,value,quote}，不放入changes；无条件为null，revise未修改条件时省略，明确撤回条件用null。remove仅含type、stepId、quote，不附带changes或规则执行参数。使用或赠送都只是意图，不输出规则执行请求。'+'\n操作级引文不可省略：每条add、revise、remove都必须有自己的op.quote，它是与type、stepId、action、changes、condition并列的操作级字段。revise完整结构为{type:"revise",stepId,action,changes,quote,condition?}；action不变时为null，changes无槽位变化时为[]，condition仅在修改或撤回条件时提供。op.quote必须是当前玩家输入中逐字存在的非空连续原文，用来标明本次修改要求；即使只修改条件或changes为空，revise也必须包含op.quote。changes[i].quote只证明该槽位值的来源；condition.quote只证明条件/玩家陈述的来源，并须与condition.value完全相同；二者都不能替代op.quote。三层quote分别校验，不会互相继承，也不会由宿主自动补写。不要因已有数量quote或条件quote而省略操作级quote；缺失时整批拒绝，不部分采用。';
export const SAMPLES={
 cultivation:['先服用两份养息丹，再给周禾一份养息丹；如果他不收，就不送。','先服用一些养息丹，再给周禾一份养息丹。','第二步改成两份','改成两份','取消赠送','先不要服药','第二步的条件改为：他已经答应了。','先服用两份养息丹，再服用四份养息丹。','先服药，再送药，再休息，再修炼。'],
 lighthouse:['先添两份灯油，再添一份灯油。','先添两份灯油，再添一份灯油，最后再添一份灯油。','第二步改成两份','改成两份','取消第二步','添油以后保证永不熄灭']
};
export function build(frame,plan,text){
 need(typeof text==='string'&&text.trim()&&text.length<=2000,'E5_INPUT');
 return {mode:'offline-fixture',input:text,source:clone(frame.reference),adapterId:frame.adapterId,
  messages:[{role:'system',content:CONTRACT},{role:'user',content:JSON.stringify({facts:{resources:frame.resources,people:frame.people,coverage:frame.coverage},capabilities:frame.capabilities.filter(c=>c.kind!=='read'),plan:plan?{steps:plan.steps.map(s=>({id:s.id,action:s.atomic.action,intent:s.atomic.intent,condition:s.condition})),question:plan.question}:null,playerText:text})}],
  carry:clone(plan),tools:[],tool_choice:'none'};
}
export function parse(raw){
 need(typeof raw==='string','E5_JSON');let text=raw.trim();
 if(text.startsWith('```')){const m=text.match(/^```json[ \t]*\r?\n([\s\S]*?)\r?\n```$/);need(m,'E5_JSON');text=m[1];}
 let v;try{v=JSON.parse(text);}catch{need(false,'E5_JSON');}
 bounded(v,{bytes:20000,depth:9});keys(v,['ops','question','limit']);
 need(Object.keys(v).length===3&&Array.isArray(v.ops)&&v.ops.length<=3,'E5_REPLY');
 need(v.ops.every(o=>['add','revise','remove'].includes(o?.type)),'E5_MODEL_OPERATION');
 return v;
}
// Deliberately finite fixture interpreter. No network and no claim of general NLU.
export class Simulator{
 mode='offline';calls=0;
 async generate(r){
  this.calls++;const t=r.input.trim(),steps=r.carry?.steps??[],v={ops:[],question:null,limit:null};
  const c=(field,value,quote)=>({field,value,quote});
  const add=(action,quantity,quote)=>({type:'add',action,changes:[c('item',r.adapterId==='cultivation'?'medicine':'lamp-oil',r.adapterId==='cultivation'?'养息丹':'灯油'),...(quantity?[c('quantity',quantity,quantity===1?'一份':quantity===2?'两份':'四份')]:[])],condition:null,quote});
  const change=(s,n,q)=>{if(!s){v.question='没有对应步骤，请先提出计划。';return;}v.ops=[{type:'revise',stepId:s.id,action:null,changes:[c('quantity',n,q)],quote:t}];};
  if(t==='先服用两份养息丹，再给周禾一份养息丹；如果他不收，就不送。'||t==='先服用一些养息丹，再给周禾一份养息丹。'){
   if(steps.length)v.question='已有计划。请先清空计划，再使用完整开局样例。';
   else{const gift=add('gift',1,'给周禾一份养息丹');gift.changes.push(c('target','zhou','周禾'));
    if(t.includes('如果'))gift.condition={kind:'prerequisite',value:'如果他不收，就不送。',quote:'如果他不收，就不送。'};
    v.ops=[add('use_medicine',t.includes('一些')?null:2,t.includes('一些')?'服用一些养息丹':'服用两份养息丹'),gift];}
  }else if(t==='先服用两份养息丹，再服用四份养息丹。'||t==='先添两份灯油，再添一份灯油。'){
   if(steps.length)v.question='已有计划。请先清空计划，再使用完整开局样例。';
   else v.ops=[add(r.adapterId==='cultivation'?'use_medicine':'fuel_lamp',2,t.includes('养息丹')?'服用两份养息丹':'添两份灯油'),add(r.adapterId==='cultivation'?'use_medicine':'fuel_lamp',r.adapterId==='cultivation'?4:1,t.includes('养息丹')?'服用四份养息丹':'添一份灯油')];
  }else if(t==='先添两份灯油，再添一份灯油，最后再添一份灯油。'){
   if(steps.length)v.question='已有计划。请先清空计划，再使用完整开局样例。';
   else v.ops=[add('fuel_lamp',2,'添两份灯油'),add('fuel_lamp',1,'添一份灯油'),add('fuel_lamp',1,'最后再添一份灯油')];
  }else if(/^第[一二三]步改成[一两]份$/.test(t)){
   const index={一:0,二:1,三:2}[t[1]],q=t.includes('两份')?'两份':'一份';change(steps[index],q==='两份'?2:1,q);
  }else if(t==='改成两份'||t==='两份'){
   const missing=steps.filter(s=>!s.atomic.intent.quantity);
   if(t==='两份'&&missing.length===1)change(missing[0],2,'两份');
   else if(steps.length===1)change(steps[0],2,'两份');
   else v.question='你想把哪一步改成两份？请写“第一步改成两份”或“第二步改成两份”。';
  }else if(['取消赠送','先不要服药','取消第二步'].includes(t)){
   const matches=t==='取消第二步'?(steps[1]?[steps[1]]:[]):steps.filter(s=>s.atomic.action===(t==='取消赠送'?'gift':'use_medicine'));
   if(matches.length===1)v.ops=[{type:'remove',stepId:matches[0].id,quote:t}];else v.question='请明确要取消哪一步。';
  }else if(t==='第二步的条件改为：他已经答应了。'){
   if(steps[1])v.ops=[{type:'revise',stepId:steps[1].id,action:null,changes:[],condition:{kind:'player-claim',value:'他已经答应了。',quote:'他已经答应了。'},quote:t}];else v.question='没有第二步，请先提出计划。';
  }else if(t==='先服药，再送药，再休息，再修炼。')v.limit='首版最多三个步骤；本次未截取或采用任何步骤。';
  else if(t.includes('否则')||t.includes('循环')||t.includes('同时'))v.limit='首版不支持嵌套分支、循环或并行行动；本次未拆分。';
  else if(t==='添油以后保证永不熄灭')v.limit='现有规则不能承诺永不熄灭，不会将目标偷换为普通添油。';
  else v.question='离线模拟器未覆盖这段文字。请使用样例，或在步骤内明确修改；没有调用真实模型。';
  return {status:'completed',raw:JSON.stringify(v),provider:'offline-fixture',usage:null};
 }
}
