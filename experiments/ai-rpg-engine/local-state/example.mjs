import {blankState,validateState} from './definition.mjs';
// Original adult fixture, not an extracted IP character or a real-model result.
export function exampleState(){
 const s=blankState();
 Object.assign(s.profile,{name:'林清',gender:'男',initialAge:'24',personality:'谨慎、愿意帮助同门',belief:'求道也要记得来路',root:'木水双灵根',constitution:'普通体质',appearance:'黑发束起，眉尾有一道旧疤，穿青色弟子服。'});
 Object.assign(s.traits,{age:'24',affiliation:'青云宗外门',clothing:'青色弟子服',realm:'练气初期',cultivationTime:'一年',breakthrough:'尚需积累',energy:'充盈',stamina:'精神充足',injury:'无',mental:'平静',goal:'完成今日的药圃杂务',practice:'基础吐纳法·入门',alchemy:'非炼丹师',crafting:'非炼器师'});
 Object.assign(s.balances,{low:20,mid:0,high:0,top:0,contribution:0});
 s.abilities=[{id:'breathing',kind:'功法',name:'基础吐纳法',description:'原创测试功法，仅作定性修习记录，无数值加成。',progress:'入门',source:'初始化确认的测试档案'}];
 s.items=[{id:'medicine',kind:'丹药',name:'养息丹',description:'原创测试丹药；功效由剧情定性描述，不绑定恢复公式。',quantity:5,source:'初始化确认的测试物资',condition:'完好'},{id:'bag',kind:'装备',name:'旧布囊',description:'装着随身物件的布囊。',quantity:1,source:'入门前自备',condition:'完好'}];
 s.equipment.storage='bag';
 s.relations=[{id:'zhou',name:'周禾',relation:0,trust:0,type:'同门',affiliation:'青云宗外门',group:'一般',impression:'刚在药圃见过面的成年同门。',lastContact:'今日·药圃'}];
 return validateState(s);
}
export const OPENING='林清走进药圃，周禾正扶着歪倒的竹架。屋檐下摆着药囊和登记簿，今日的杂务还没有开始。';
