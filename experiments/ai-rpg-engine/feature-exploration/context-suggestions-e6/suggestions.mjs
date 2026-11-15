import {clone,need} from '../../agent-core/contracts.mjs';
import {stamp} from './source.mjs';
export const EXAMPLES=['我想向周禾表达谢意，但尽量保留自己的丹药，有哪些办法？','我想向他表达谢意。','想感谢周禾，但不送丹药。'];
export const CONTRACT='根据受限情境和玩家目标给出最多三个不同的备选建议，必要时只问一个关键问题。不得替玩家设目标、替NPC同意或把公开叙述当事实。建议不是已发生行动，不组成执行步骤。对象仅引用登记ID；缺失数量不补写为玩家决定。标明依据、未知和能力边界。';
export function build(frame,goal,answer=''){
 need(typeof goal==='string'&&goal.trim()&&goal.length<=1500&&typeof answer==='string'&&answer.length<=500,'E6_INPUT');
 return {mode:'offline-fixture',source:clone(frame.source),messages:[{role:'system',content:CONTRACT},
 {role:'user',content:JSON.stringify({context:frame,goal,clarification:answer,playerStatementStatus:'unverified'})}]};
}
export function suggest(frame,goal,answer=''){
 const request=build(frame,goal,answer),text=goal+' '+answer;
 const base={mode:'offline-fixture',source:stamp(frame),goal,answer,question:null,notice:null,options:[],request};
 if(!frame.ready)return {...base,notice:'来源有待处理回合，目前只保留你的想法。'};
 if(!/谢|感谢|致谢/.test(text))return {...base,notice:'离线样例仅演示致谢目标；你仍可原样保存自己的想法。'};
 if(!/周禾/.test(text))return {...base,question:'你希望向谁表达谢意？本场景只提供了周禾的登记信息。'};
 if(!frame.facts.people.some(x=>x.id==='zhou'))return {...base,notice:'本次情境没有周禾的登记信息，请补充来源。'};
 base.options=[
 {id:'ask',kind:'conversation',title:'先问问他需要什么',text:'我想先询问周禾需要什么，再决定怎样致谢。',reason:'先了解需要，可以避免立即消耗物资。',evidence:['people:zhou'],unknown:'不知道周禾是否在场、是否可联系，也不知道他的答复。',support:'仅交流意图；本轮不模拟NPC回应'},
 {id:'thanks',kind:'conversation',title:'先表达感谢，保留丹药',text:'我想向周禾表达感谢，暂时保留自己的丹药。',reason:'直接回应致谢目标，同时保留现有物资。',evidence:['people:zhou'],unknown:'不保证对方接受，也不预估关系变化。',support:'仅交流意图；尚未发生'}];
 const item=frame.facts.resources.find(x=>x.id==='medicine');
 if(item&&item.quantity>0&&!/不送|不赠|不要送|全部保留/.test(text))base.options.push({
 id:'gift',kind:'gift',title:'考虑赠送少量丹药',text:'我想给周禾养息丹，数量由我确认。',itemId:item.id,targetId:'zhou',
 reason:'登记中有养息丹；可以考虑赠送，但数量由你决定。',evidence:['items:'+item.id,'people:zhou'],
 unknown:'赠送条件和对方意愿未知；没有登记的赠送执行规则。',support:'仅赠送意图；不支持执行'});
 base.capabilityNote=frame.capabilities.some(x=>x.name==='query_local_state'&&x.effect==='read')?'当前开放登记物资查询；开放查询不等于赠送可执行。':'当前未开放登记物资查询；此处只是宿主已提供的情境快照。';
 return base;
}
