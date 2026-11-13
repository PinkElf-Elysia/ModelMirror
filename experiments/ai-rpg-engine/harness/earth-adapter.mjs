import {readFileSync} from 'node:fs';
import {parseFragment,serialize} from 'parse5';
import {activeSystem,loadFrozen,matchWorldbook} from '../card-replica/lib/assembly.mjs';
import {canonical,hash,requireValue,fail,clone,money} from './contracts.mjs';
export const rules=JSON.parse(readFileSync(new URL('./earth-shop.json',import.meta.url),'utf8'));
export const ADAPTER_VERSION='earth-shop-adapter/2';
const {prompts,manifest}=loadFrozen();
const bank='<div class="finance-item"><span class="finance-label">银行卡余额</span><span class="finance-val">{XXXXX元}</span></div>';
const bag='<div class="inv-category"><strong>包内：</strong>{背包/公文包/手提袋中的物品}</div>';
const updates='必须更新状态栏所有动态信息';
export const patches=[
 {field:'system',from:bank,to:'<!-- 银行卡余额由宿主面板展示，此处不生成 -->',reason:'银行卡交易字段由宿主维护'},
 {field:'system',from:bag,to:'<div class="inv-category"><strong>其他包内物品：</strong>{仅描述非商店受管物品，不列饮用水、面包、笔记本的数量}</div>',reason:'受管物品单独由宿主展示，其他物品仍由原卡描述'},
 {field:'prefix',from:updates,to:'仅更新未被宿主接管的状态栏信息；银行卡余额及饮用水、面包、笔记本数量不由模型计算或输出',reason:'移除全部动态字段由模型重算的冲突'}
];
function replaceExact(text,patch){requireValue(text.split(patch.from).length===2,'SHOP_ADAPTER_SOURCE_DRIFT',409);return text.replace(patch.from,patch.to);}
const adapted={...prompts,system:activeSystem};
for(const p of patches)adapted[p.field]=replaceExact(adapted[p.field],p);
// A committed purchase is already adjudicated. Reuse only the source's surface-world
// and writing rules here, not its engine protocol, hidden reasoning or full UI template.
function sourceSection(start,end){requireValue(activeSystem.split(start).length===2&&activeSystem.split(end).length===2,'SHOP_ADAPTER_SOURCE_DRIFT',409);const a=activeSystem.indexOf(start),b=activeSystem.indexOf(end,a);requireValue(b>a,'SHOP_ADAPTER_SOURCE_DRIFT',409);return activeSystem.slice(a,b).trim();}
const purchaseStyle=[sourceSection('# 世界观核心','## 回复规则'),sourceSection('## 叙事风格','# 输出格式 （必须严格遵守）')].join('\n\n');
const purchaseOutput='本次只叙述已经结算的购买，不再决定成交或推演其他游戏机制。直接输出完整现场正文，人物反应通过行为和对话呈现；不输出思维链、隐藏注释、检查清单、状态 JSON 或全量面板。保留原卡第三人称与正文篇幅要求。正文使用 <div class="main-text">，最后给出五条贴合场景、不会自行执行交易的行动选项（action-item）。金额与物品变动由宿主回执展示，不重复抄写面板。';
export const purchaseNarrationPolicy=Object.freeze({version:'settled-purchase/1',sourceSections:['# 世界观核心 → ## 回复规则','## 叙事风格 → # 输出格式'],system:purchaseStyle+'\n\n'+purchaseOutput});
export const binding=Object.freeze({adapterVersion:ADAPTER_VERSION,cardId:'earth',sourceHash:hash(prompts),manifestHash:hash(manifest),ruleVersion:rules.version,ruleHash:hash(rules),adaptedHash:hash({adapted,purchaseNarrationPolicy}),ownedFields:['bank.balance','inventory.water','inventory.bread','inventory.notebook','shop.stock']});
export function assertBinding(b){requireValue(canonical(b)===canonical(binding),'SHOP_ADAPTER_BINDING_CHANGED',409);}
function nodeText(n){return n.nodeName==='#text'?n.value:(n.childNodes||[]).map(nodeText).join('');}
// Display/context copies only. Raw historical replies remain untouched.
export function stripManagedPanels(raw){
 const root=parseFragment(raw);
 const walk=n=>{n.childNodes=(n.childNodes||[]).filter(c=>{
  const cl=c.attrs?.find(a=>a.name==='class')?.value||'',t=nodeText(c);
  if(cl.split(/\s+/).includes('finance-item')&&t.includes('银行卡余额'))return false;
  if(cl.split(/\s+/).includes('inv-category')&&rules.products.some(p=>t.includes(p.name))){
   const redact=node=>{if(node.nodeName==='#text')node.value=node.value.replace(/(?:[一二两三四五六七八九十百\d]+\s*(?:个|瓶|本|袋|份)?\s*)?(?:饮用水|面包|笔记本)(?:\s*[x×:：]?\s*[一二两三四五六七八九十百\d]+(?:个|瓶|本|袋|份)?)?/g,'');for(const x of node.childNodes||[])redact(x);};redact(c);
  }
  return true;
 });for(const c of n.childNodes)walk(c);};
 walk(root);return serialize(root);
}
export function effectiveText(text){let v=text;for(const p of patches)if(v.includes(p.from))v=v.split(p.from).join(p.to);return v;}
export function relevantState(state,event){
 const product=event?.productId?rules.products.find(p=>p.id===event.productId):null;
 return {account:rules.account.label,balance:money(state.balance,rules.currency),
  inventory:Object.fromEntries((product?[product]:rules.products).map(p=>[p.name,state.inventory[p.id]])),
  ...(product?{stock:{[product.name]:state.stock[product.id]}}:{}),stateRevision:state.revision};
}
export function assembleNarration({session,event,state,additions=[]}){
 assertBinding(state.binding);
 requireValue(session.world==='表世界','SHOP_WORLD_UNSUPPORTED');
 requireValue(Array.isArray(session.history)&&session.history.every(m=>['user','assistant'].includes(m.role)&&typeof m.content==='string'),'SHOP_HISTORY_INVALID');
 const triggered=matchWorldbook({world:session.world,body:session.history.at(-1)?.content||'',enabledIds:session.enabledRuleIds||[]});
 const history=session.history.map(m=>({role:m.role,content:m.role==='assistant'?stripManagedPanels(m.content):effectiveText(m.content)}));
 const input=event.input||('购买'+event.quantity+'个'+(rules.products.find(p=>p.id===event.productId)?.name||'商品'));
 const settledPurchase=event.outcome==='committed'&&!!event.productId;
 const current=settledPurchase?((history.length?'':session.characterText?session.characterText+'\n\n':'')+input+triggered.map(x=>'\n'+x.text).join('')):history.length?adapted.prefix+'\n'+input+'\n'+adapted.suffix+triggered.map(x=>'\n'+x.text).join(''):session.characterText?session.characterText+'\n\n'+input:input;
 requireValue(additions.every(x=>typeof x==='string'),'SHOP_CONTEXT_INVALID');
 const facts={eventId:event.id,outcome:event.outcome,reason:event.reason||null,changes:event.changes||null,state:relevantState(state,event)};
 const contract='本会话启用商店交易适配。宿主事件是银行卡余额与受管物品数量的唯一依据。不得新增扣款、收入、发货、退货或改变这些字段。叙述已经确认的结果、人物反应和后续情节；不要复述计算过程、账本 JSON 或重复交易面板。未适配的经济变更只提出方案，不视为已结算。仅宿主明确改写事件可改变已确认结果。其他世界设定、叙事文风与未接管信息沿用原卡。';
 const messages=[{role:'system',content:(settledPurchase?purchaseNarrationPolicy.system:adapted.system)+'\n\n'+contract},...history,{role:'user',content:current+additions.map(x=>'\n'+effectiveText(x)).join('')+'\n\n【宿主已确认事件】\n'+canonical(facts)}];
 return {messages,current,triggered:triggered.map(x=>({id:x.id,sha256:x.sha256})),binding:clone(binding),messagesHash:hash(messages),facts};
}
export function narrationIssue(raw,state){
 if(typeof raw!=='string'||!raw.trim())return 'SHOP_NARRATION_EMPTY';
 // Explicit currency assertions can be checked without a second model call.
 for(const m of raw.matchAll(/银行卡余额(?:为|是|剩余|[：:])\s*(\d+(?:\.\d{1,2})?)\s*元/g)){
  if(Math.round(Number(m[1])*100)!==state.balance)return 'SHOP_NARRATION_STATE_CONFLICT';
 }
 return null;
}
export function adaptationReport(){return {binding,patches:clone(patches),purchaseNarration:{version:purchaseNarrationPolicy.version,sourceSections:purchaseNarrationPolicy.sourceSections,scope:'committed purchases only; original card and ordinary story assembly unchanged'},preserved:['world','style','perspective','length','worldbook-trigger-source','ordinary-story-first/continuation-prefix-semantics'],limitations:['不验证任意隐喻或自然语言中的全部事实','未接管字段沿用旧模式']};}
