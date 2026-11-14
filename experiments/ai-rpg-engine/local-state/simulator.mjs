import {SessionRecords} from '../authoring/session-storage.mjs';
import {hash,need} from './definition.mjs';
export const SCENARIOS=[
 ['use-two','服用两份养息丹'],['use-rest','服用剩余三份养息丹'],['help','帮周禾扶好竹架'],['promise','如约归还周禾的工具'],
 ['earn','完成药圃杂务，领取八枚下品灵石和三点贡献'],['spend','花四枚下品灵石购买药圃材料'],
 ['learn','学习引露术'],['progress','练习基础吐纳法'],['equipment','领到新药囊并换上'],['tired','劳作后感到疲惫'],
 ['friction','与周禾争执'],['talk','与周禾聊聊接下来的打算']
];
export function simulatedReply(input,state,{variant='calm'}={}){
 const reason=input,operations=[];let body='周禾放下手中的工具，认真听你说完。他指向药圃旁尚未整理的竹架：“不如先看看这里，再商量下一步。”';
 const itemDelta=delta=>operations.push({type:'quantity',id:'medicine',delta,reason});
 if(input.includes('两份养息丹')){itemDelta(-2);body='你从布囊中取出两份养息丹服下，随后把药囊系紧。周禾将水壶推到你身边：“先歇一会儿，竹架不急着搬。”';}
 else if(input.includes('三份养息丹')){itemDelta(-3);body='你服下余下的养息丹，将空药袋折好收起。周禾看了一眼药袋，提醒你离开前去登记簿旁核对补给。';}
 else if(input.includes('扶好竹架')){operations.push({type:'relation',id:'zhou',axis:'relation',event:'help',delta:8,reason});body='你撑住倾斜的竹架，让周禾腾出手重新绑紧绳结。最后一根木桩稳住时，他舒了口气，主动给你让出阴凉处：“多谢。下回整理药圃，咱们可以搭个伴。”';}
 else if(input.includes('归还')){operations.push({type:'relation',id:'zhou',axis:'trust',event:'promise',delta:3,reason});body='你将借来的工具擦净后按约归还。周禾检查了一下刃口，点头把它收进木箱：“你记得这件事，我便放心了。”';}
 else if(input.includes('领取八枚')){operations.push({type:'balance',account:'low',delta:8,reason},{type:'balance',account:'contribution',delta:3,reason});body='登记杂务的师兄核过木牌，将应得的灵石交给你，又在贡献簿上添了一笔。周禾问你是否打算留下来学习辨认幼苗。';}
 else if(input.includes('花四枚')){operations.push({type:'balance',account:'low',delta:-4,reason});body='你付过药圃材料的钱，收好对方递来的凭据。周禾建议先把材料留在工具房，等午后一起搬运。';}
 else if(input.includes('引露术')){if(!state.abilities.some(a=>a.id==='dew'))operations.push({type:'addAbility',entry:{id:'dew',kind:'术法',name:'引露术',description:'原创测试术法，引导叶面露水；效果定性，不定义消耗公式。',progress:'初学',source:reason},reason});body='你按照示范调整指势，一滴露水终于顺着叶缘聚起。周禾没有催促，只请你记住这次成功时的手势，再试着保持片刻。';}
 else if(input.includes('练习基础')){operations.push({type:'progress',id:'breathing',value:'吐纳逐渐熟悉，仍在入门阶段',reason});body='你沿着熟悉的呼吸节奏练习吐纳，渐渐不再频繁停顿。这还称不上突破，却让你发现自己能更耐心地完成一整轮练习。';}
 else if(input.includes('新药囊')){if(!state.items.some(i=>i.id==='new-bag'))operations.push({type:'addItem',entry:{id:'new-bag',kind:'装备',name:'新药囊',description:'分格布药囊，无额外数值属性。',quantity:1,source:reason,condition:'完好'},reason});operations.push({type:'equip',slot:'storage',id:'new-bag',reason});body='你把常用物件移入分格的新药囊，将旧布囊叠好留下。周禾指出药囊侧边的小扣，示意你把登记木牌挂在那里。';}
 else if(input.includes('疲惫')){operations.push({type:'set',field:'stamina',value:'劳作后疲惫',reason},{type:'set',field:'mental',value:'需要歇息，但神志清楚',reason});body='你停下搬运，靠在树荫里缓了缓。周禾把下一捆竹竿搁在地上：“等你歇好再做，别硬撑。”';}
 else if(input.includes('争执')){operations.push({type:'relation',id:'zhou',axis:'relation',event:'friction',delta:-3,reason});body='你们对竹架该放在哪里意见相左。周禾语气变硬，却没有甩手离开，而是指着地上的水迹，让你看清他担心的地方。';}
 if(variant==='warm')body+='\n\n微风穿过药圃，叶片轻轻相碰。你们还有时间把接下来的事说清楚。';
 if(input.includes('周禾'))body+='\n<local_relation id="zhou"></local_relation>';
 return {status:'completed',finishReason:'stop',provider:'offline-simulator',model:'deterministic-fixture/1',text:body+'\n<state_delta>'+JSON.stringify({operations})+'</state_delta>',usage:{inputTokens:null,outputTokens:null,costUSD:0,kind:'simulated'}};
}
export class OfflineProvider{
 static async open(directory){return new OfflineProvider(await SessionRecords.open(directory,{scope:'rpg-local-state-simulator',recoverDeadOwner:true}));}
 constructor(records){this.records=records;this.calls=0;}
 key(session,operation){return 'sim-'+hash([session,operation]).slice(0,40);}
 async generate(session,operation,input,state,options={}){
  const key=this.key(session,operation),requestHash=hash({input,state,options}),old=await this.records.get(key,x=>x);
  if(old){need(old.data.requestHash===requestHash,'STATE_PROVIDER_COLLISION');return old.data.receipt;}
  const receipt=simulatedReply(input,state,options);
  if(options.fault==='truncated'){receipt.finishReason='length';receipt.text=receipt.text.slice(0,-8);}
  if(options.fault==='failed'){receipt.status='failed';receipt.text='';}
  this.calls++;await this.records.put(key,null,{requestHash,receipt});return receipt;
 }
 async lookup(session,operation){return (await this.records.get(this.key(session,operation),x=>x))?.data.receipt||null;}
 close(){return this.records.close();}
}
