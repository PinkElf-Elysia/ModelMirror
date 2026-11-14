import {clone,hash,need} from '../../agent-core/contracts.mjs';
import {assemblePrepared,material} from '../../agent-core/context/assemble.mjs';
import {parseReply as parseChanges,MockProvider as GiftMock} from '../free-action-e3/provider.mjs';
export const SAMPLES={
 cultivation:['我想服用两份养息丹','我想服用一些丹药','两份','改成一份','改为赠送','周禾','养息丹，两份；如果他不收就算了','不是周禾','他已经答应了','改为服用','用未登记的神通永久保护药圃'],
 lighthouse:['我想添两份灯油','我想添一些灯油','两份','改成一份','添油以后保证永不熄灭']
};
export const CONTRACT='只解释玩家意图，不执行、不叙述已发生的行动。返回JSON {action:登记行动ID或null,changes:[{field:item|quantity|target|condition,value:值或null,quote:当前玩家原文}],questions:[],unsupported:false}。action为null表示不切换；切换行动会清除旧参数，不能沿用不适用的对象或条件。gift仅修仙意图，无执行规则。只使用显式ID和数量，未知先询问，不默认唯一对象或数量1。玩家陈述仅为condition，不证明人物在场或同意。condition.value非null时必须逐字摘录当前玩家输入中的连续原文，保留人称、措辞和标点，不做代词消解、概括或改写；quote也必须逐字摘录当前输入。例如玩家写“他已经答应了”，condition.value和quote应为“他已经答应了”，不得改成“周禾已经答应”。撤回条件时value为null。禁止state_delta、工具调用和全量面板。';
export function build(frame,draft,text){
 need(typeof text==='string'&&text.trim()&&text.length<=2000,'E4_INPUT');
 const scope={session:frame.record.id,stateRevision:frame.record.stateRevision},facts={resources:frame.resources,people:frame.people,coverage:frame.coverage};
 const carry=draft?{action:draft.action,intent:draft.intent,questions:draft.questions,worldConditions:draft.worldConditions,status:draft.status}:null;
 const payload={capabilities:frame.capabilities.filter(c=>c.kind!=='read'),facts,currentDraft:carry,playerText:text};
 const materials=[
 material(scope,'e4-contract','output-contract','instruction',CONTRACT,{kind:'e4-proposal-contract',hash:hash(CONTRACT)},{required:true}),
 material(scope,'e4-facts','state','confirmed',JSON.stringify(facts),{kind:'r5-readonly-projection',reference:frame.reference},{required:true}),
 material(scope,'e4-capabilities','tool-contract','instruction',JSON.stringify(payload.capabilities),{kind:'r5-registry-projection'},{required:true})];
 const request=assemblePrepared({scope,materials,selection:{mode:'full',refs:[]},bindings:{},messages:[{role:'system',content:''},{role:'user',content:JSON.stringify({currentDraft:carry,playerText:text})}],replacePrefix:''});
 return {...request,input:text,adapterId:frame.adapterId,capabilities:payload.capabilities,objects:{items:frame.resources,relations:frame.people},carry,provider:'offline-fixture',tools:[],tool_choice:'none',realCalls:0};
}
export function parse(raw){
 need(typeof raw==='string','E4_REPLY_JSON');
 // Accept one complete JSON fence only. Never extract JSON from surrounding prose.
 // Keep the original receipt untouched; this local string is only the parser input.
 let text=raw.trim();
 if(text.startsWith('```')){
  const fence=text.match(/^```json[ \t]*\r?\n([\s\S]*?)\r?\n```$/);
  need(fence,'E4_REPLY_JSON');text=fence[1];
 }
 let v;try{v=JSON.parse(text);}catch{need(false,'E4_REPLY_JSON');}
 need(v&&Object.keys(v).sort().join()==='action,changes,questions,unsupported','E4_REPLY_FIELDS');
 const {action,...rest}=v;need(action===null||typeof action==='string','E4_REPLY_ACTION');return {action,...parseChanges(JSON.stringify(rest))};
}
// Finite examples, not a natural-language model. Unknown inputs remain questions.
export class Simulator{
 calls=0;
 async generate(request){
  this.calls++;const t=request.input.trim(),v={action:null,changes:[],questions:[],unsupported:false};
  const put=(field,value,quote)=>v.changes.push({field,value,quote});
  const resource=request.objects.items[0],rule=request.capabilities.find(c=>c.kind==='rule');
  if(t==='我想服用两份养息丹'&&request.adapterId==='cultivation'||t==='我想添两份灯油'&&request.adapterId==='lighthouse'){
   v.action=rule.id;put('item',resource.id,resource.name);put('quantity',2,'两份');
  }else if(t==='我想服用一些丹药'&&request.adapterId==='cultivation'){v.action=rule.id;}
  else if(t==='我想添一些灯油'&&request.adapterId==='lighthouse'){v.action=rule.id;put('item',resource.id,resource.name);}
  else if(t==='改为赠送'&&request.adapterId==='cultivation')v.action='gift';
  else if(t==='改为服用'&&request.adapterId==='cultivation')v.action=rule.id;
  else if(['改成一份','两份'].includes(t)&&request.carry?.action){
   if(request.carry.intent.item)put('quantity',t==='两份'?2:1,t==='两份'?'两份':'一份');
   else v.questions=['请先明确物资，再确定它的数量。'];
  }else if(request.carry?.action==='gift'){
   const r=await new GiftMock().generate({...request,carry:{intent:request.carry.intent}});
   Object.assign(v,parseChanges(r.raw));
  }else if(t==='用未登记的神通永久保护药圃'||t==='添油以后保证永不熄灭'){v.unsupported=true;v.questions=['现有规则不能承诺这一目标，请另拟方案；不会忽略额外条件。'];}
  else v.questions=['离线模拟器未覆盖这段文字。请使用列出的样例或直接编辑；没有发起真实模型调用。'];
  return {status:'completed',raw:JSON.stringify(v),provider:'offline-fixture',usage:null};
 }
}
