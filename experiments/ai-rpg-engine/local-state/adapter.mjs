import {readFile} from 'node:fs/promises';
import {SOURCE_HASH,ADAPTER,hash,need} from './definition.mjs';
export const DELTA_INSTRUCTION=`【本地状态模式】
宿主保存并渲染【自身状态】【关系网络】，不要生成这两个面板或重复完整状态 JSON。
仅在回复末尾输出一份 <state_delta>{"operations":[]}</state_delta>。没有变化时 operations 为空数组。
只提出本轮发生的变化，每项必须有 reason；不得由正文、总结、记忆、Mod 或正则覆盖账本。
锚定资料只能通过用户明确改写调整。未知初值不能当作零。正文不报自行计算的余额、物品余量、关系及信任结果。
NPC 关系使用 <local_relation id="已有关系ID"></local_relation>，由宿主填入数值；新人物通过 addRelation 登记。
定性状态使用 set；数量使用 quantity/balance 的整数 delta；模型不得提供最终余额。
关系与信任事件类别及 delta 按原卡区间；程序累加、限制范围、展示实际变化。信任增速与深交难度继续由剧情判断。
增量协议：
set {field,value,reason}：仅定性 traits 字段；
mark {value,reason}：追加外貌印记；
addAbility {entry:{id,kind,name,description,progress,source},reason}；
progress {id,value,reason}；
addItem {entry:{id,kind,name,description,quantity,source,condition},reason}；
quantity {id,delta,reason}；balance {account,delta,reason}；
equip {slot,id或null,reason}；condition {id,value,reason}；
addRelation {id,name,reason}：新人物初值为零；
relation {id,axis,event,delta,reason}；
relationInfo {id,field,value,reason}；
faction {entry:{id,name,description},reason}。
所有操作必须有 type。档案只增不删，物品消耗到零后保留来源。不要新增历法、伤害、修炼或兑换公式。
`;
function removeSection(text,title){
 const pattern=new RegExp('<details[^>]*>\\s*<summary>'+title+'</summary>[\\s\\S]*?</details>','g');
 return text.replace(pattern,'');
}
export function stripPanels(text){return removeSection(removeSection(text,'🧍 自身状态'),'🔗 关系网络');}
export function effectiveSystem(original){
 let s=stripPanels(original);
 const start=s.indexOf('## ⚓ 金库机制'),end=s.indexOf('## ⚓ 记忆区铁律',start);
 need(start>=0&&end>start,'STATE_ADAPTER_ANCHOR');s=s.slice(0,start)+
 '## 本地档案留存\n自身能力、装备、物资、核心外貌由宿主保留；本轮只提交新增或变化，零数量不删除物品档案。\n\n'+s.slice(end);
 s=s.replace('**每次回复必须完整输出所有状态栏模块，无例外。**','未接管模块沿用原结构；自身状态和关系网络由宿主渲染，不进入模型输出。')
 .replace('4. 【世界信息】+【自身状态】+【交互角色】+【修真界风云】','4. 【世界信息】+【交互角色】+【修真界风云】（自身状态由宿主显示）')
 .replace('5. 【关系网络】','5. 【关系网络】由宿主显示')
 .replace(/关系：\{X\}\/100[^\r\n]*/g,'关系与信任：<local_relation id="关系ID"></local_relation>')
 .replace('状态栏格式：□与上轮一致 □模块齐全 □符号正确','未接管区块格式：□模块齐全 □符号正确');
 return s+'\n\n'+DELTA_INSTRUCTION;
}
export async function loadAdapter(){
 const raw=await readFile(new URL('../cultivation-card/author-system.txt',import.meta.url),'utf8');
 need(hash(raw)===SOURCE_HASH,'STATE_SOURCE_CHANGED');const system=effectiveSystem(raw);
 return {original:raw,system,binding:{adapter:ADAPTER,sourceHash:SOURCE_HASH,effectiveHash:hash(system)}};
}
