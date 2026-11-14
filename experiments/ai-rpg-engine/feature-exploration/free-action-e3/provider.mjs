import {need,bounded} from '../../agent-core/contracts.mjs';
export const SAMPLES=['给那人一些丹药','周禾','养息丹，两份；如果他不收就算了','改成一份，先问他需不需要','不是周禾','两份','就选他','算了','他已经答应了','用未登记的神通永久保护药圃'];
export function parseReply(raw){
 need(typeof raw==='string'&&raw.length<=12000,'REPLY_SIZE');let v;try{v=JSON.parse(raw);}catch{need(false,'REPLY_JSON');}
 bounded(v,{bytes:16000,depth:6});
 need(v&&Object.keys(v).sort().join()==='changes,questions,unsupported','REPLY_FIELDS');
 need(Array.isArray(v.changes)&&Array.isArray(v.questions)&&v.questions.length<=4&&v.questions.every(q=>typeof q==='string'&&q.trim()&&q.length<=300)&&typeof v.unsupported==='boolean','REPLY_SHAPE');
 return v;
}
// A finite transcript fixture. It is not an NLP implementation or a real provider.
export class MockProvider{
 calls=0;
 async generate(request){
  this.calls++;const text=request.input.trim(),v={changes:[],questions:[],unsupported:false};
  const put=(field,value,quote)=>v.changes.push({field,value,quote});
  const ref=(domain,name)=>request.objects[domain].find(x=>x.name===name)?.id;
  switch(text){
   case '给那人一些丹药':break;
   case '周禾':put('target',ref('relations','周禾')??'unknown','周禾');break;
   case '养息丹，两份；如果他不收就算了':
    put('item',ref('items','养息丹')??'unknown','养息丹');put('quantity',2,'两份');put('condition','如果他不收就算了','如果他不收就算了');break;
   case '改成一份，先问他需不需要':put('quantity',1,'一份');put('condition','先问他需不需要','先问他需不需要');break;
   case '不是周禾':put('target',null,'不是周禾');break;
   case '两份':
    if(request.carry?.intent.item)put('quantity',2,'两份');else v.questions=['请先明确哪一种丹药，再确定它的数量。'];break;
   case '就选他':
    if(request.carry?.intent.target)put('target',request.carry.intent.target.value,'他');else v.questions=['“他”尚未绑定，请点名或在界面选择对象。'];break;
   case '算了':v.questions=['你想取消整项计划，还是只撤掉某个条件？可以使用“取消计划”按钮明确选择。'];break;
   case '他已经答应了':put('condition',text,text);break;
   case '用未登记的神通永久保护药圃':v.unsupported=true;v.questions=['当前赠送原型不支持此目标，保留原文，不转换为物品消耗。'];break;
   default:v.questions=['离线模拟器不解释这段自由文本，请使用列出的样例或结构化编辑；真实模型尚未接入。'];
  }
  return {status:'completed',raw:JSON.stringify(v),provider:'offline-simulated',usage:null};
 }
}
