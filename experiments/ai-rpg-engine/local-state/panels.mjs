import {renderPanelTemplateBounded} from '../authoring/variable-template.mjs';
import {validateState,relationshipLabel,PROFILE,TRAITS,ACCOUNTS,SLOTS} from './definition.mjs';
export const LABELS={name:'姓名',gender:'性别',initialAge:'初始年龄',personality:'性格',belief:'信念',root:'灵根',constitution:'特殊体质',appearance:'核心外貌',age:'当前年龄',affiliation:'所属',reputation:'声望',clothing:'当前着装',realm:'境界',lifespan:'寿命上限',cultivationTime:'已修炼时长',breakthrough:'突破进度',awakening:'觉醒程度',goldenCore:'金丹',nascentSoul:'元婴',awareness:'神识',practice:'功法层次',energyForm:'灵力形态',requirements:'突破条件',alchemy:'炼丹等级',crafting:'炼器等级',furnace:'丹炉／炉鼎',energy:'灵力',stamina:'体力',injury:'伤势',mental:'精神',goal:'当前目标',innerThought:'内心',low:'下品灵石',mid:'中品灵石',high:'上品灵石',top:'极品灵石',contribution:'宗门贡献点',natal:'本命法宝',external:'外物法宝',defense:'防御法宝',storage:'储物装备'};
export const value=x=>x===null?'未设定':String(x);
export function relationText(r){return r.name+' · 关系 '+r.relation+'（'+relationshipLabel(r.relation)+'） · 信任 '+r.trust+'（'+relationshipLabel(r.trust,'trust')+'）';}
export function panelText(state,changes=[]){
 validateState(state);const added=new Set(changes.filter(c=>c.before===null).map(c=>c.path));
 const rows=(keys,obj)=>keys.map(k=>LABELS[k]+'：'+value(obj[k])).join('\n');
 const ability=state.abilities.map(a=>(added.has('abilities.'+a.id)?'[NEW] ':'')+a.kind+' · '+a.name+'｜'+a.progress+'\n'+a.description+'\n来源：'+a.source).join('\n\n')||'尚未登记';
 const inventory=state.items.map(i=>(added.has('items.'+i.id)?'[NEW] ':'')+i.kind+' · '+i.name+' × '+value(i.quantity)+(i.quantity===0?'（已耗尽，档案保留）':'')+'\n'+i.description+'｜'+i.condition+'\n来源：'+i.source).join('\n\n')||'尚未登记';
 const equipment=SLOTS.map(k=>{const v=state.equipment[k];return LABELS[k]+'：'+(v===null?'未设定':state.items.find(i=>i.id===v)?.name);}).join('\n');
 return {self:[
 {title:'自身档案 · 锚定',body:rows(PROFILE.filter(k=>k!=='appearance'),state.profile)},
 {title:'外貌档案 · 金库',body:'核心外貌：'+value(state.profile.appearance)+'\n追加印记：'+(state.marks.join('；')||'无')+'\n当前着装：'+value(state.traits.clothing)},
 {title:'修炼与动态状态',body:rows(TRAITS.filter(k=>k!=='clothing'),state.traits)},
 {title:'能力档案 · 金库',body:ability},{title:'装备栏',body:equipment},{title:'物资与丹药 · 金库',body:inventory},
 {title:'财产',body:rows(ACCOUNTS,state.balances)}
 ],relations:['核心','一般','休眠'].map(group=>({title:group+'关系',body:state.relations.filter(r=>r.group===group).map(r=>relationText(r)+'\n'+r.type+'｜'+r.affiliation+'\n印象：'+r.impression+'\n最近：'+r.lastContact).join('\n\n')||'尚未登记'})).concat([{title:'势力声望',body:state.factions.map(f=>f.name+'：'+f.description).join('\n')||'未设定'}])};
}
export async function renderPanels(state,changes=[]){
 const data=panelText(state,changes);
 return renderPanelTemplateBounded('<details open class="state-panel"><summary>🧍 自身状态</summary>{{#each self}}<section><h3>{{title}}</h3><pre>{{body}}</pre></section>{{/each}}</details><details open class="state-panel"><summary>🔗 关系网络</summary>{{#each relations}}<section><h3>{{title}}</h3><pre>{{body}}</pre></section>{{/each}}</details>',data);
}
export function referencePanels(state){
 const d=panelText(state);return '<details><summary>🧍 自身状态</summary>\n'+d.self.map(s=>s.title+'\n'+s.body).join('\n')+'</details><details><summary>🔗 关系网络</summary>\n'+d.relations.map(s=>s.title+'\n'+s.body).join('\n')+'</details>';
}
