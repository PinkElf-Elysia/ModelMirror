import {extraStages,memoryMessages,memoryReferences} from './memory.mjs';
import {assembleNarration,effectiveText,rules,stripManagedPanels} from './earth-adapter.mjs';
import {project} from '../plugins/custom-config/node-job.mjs';
import {validate} from '../plugins/custom-config/public/config.mjs';
import {assembleMods,resolveMods} from '../plugins/mod-panel/catalog.mjs';
import {clone,requireValue,hash,canonical} from './contracts.mjs';
export async function validateContext(input={}){
 requireValue(input&&typeof input==='object'&&!Array.isArray(input),'SHOP_CONTEXT_INVALID');
 const profiles=clone(input.profiles||[]),modIds=clone(input.modIds||[]);
 requireValue(Array.isArray(profiles)&&profiles.length<=2&&Array.isArray(modIds)&&modIds.length<=20,'SHOP_CONTEXT_INVALID');
 for(const p of profiles){validate(p);requireValue(!p.usePrefill,'SHOP_PREFILL_UNSUPPORTED');}
 const mods=modIds.length?await resolveMods('earth',modIds):[];
 const text=JSON.stringify([profiles,mods.map(m=>m.text)]);
 requireValue(!/银行卡[^。\n]{0,30}(?:任意|随意|自行)(?:修改|增加|更新|重算)/.test(text),'SHOP_CONTEXT_CONFLICT',409);
 for(const k of ['summaryEnabled','memoryEnabled'])requireValue(input[k]===undefined||typeof input[k]==='boolean','SHOP_CONTEXT_INVALID');
 return {profiles,mods:mods.map(m=>({id:m.id,text:m.text})),...(input.summaryEnabled!==undefined?{summaryEnabled:input.summaryEnabled}:{}),...(input.memoryEnabled!==undefined?{memoryEnabled:input.memoryEnabled}:{}),hash:hash({profiles,mods,summaryEnabled:input.summaryEnabled??null,memoryEnabled:input.memoryEnabled??null})};
}
export async function assembleShopContext(record,event,purpose){
 if(extraStages.includes(purpose)){
  const copy=clone(record),turn=copy.projection.find(p=>p.eventId===event.id);
  if(turn&&['summary-small','memory'].includes(purpose)){
   const source=await project({kind:'memory-source',configs:record.session.context?.profiles||[],messages:[{role:'system',content:''},{role:'user',content:JSON.stringify({turns:[{user:turn.input,assistant:turn.raw}]})}]});
   const row=JSON.parse(source[1].content).turns[0];turn.input=row.user;turn.raw=row.assistant;
  }
  return memoryMessages(copy,event,purpose);
 }
 const prior=record.projection.flatMap(p=>[{role:'user',content:p.input},{role:'assistant',content:p.raw}]);
 const session={...record.session,history:[...record.session.history,...prior]};
 if(purpose==='intent')return {messages:[
  {role:'system',content:'仅解析玩家购买意图，不执行交易。输出 JSON：明确购买用 {"kind":"purchase","productId":"商品ID","quantity":正整数}；信息不足用 {"kind":"clarify","message":"一个简短澄清问题"}；其他行为用 {"kind":"story"}。仅明确购买请求可返回 purchase；叙述过去买过、询价、否定或不确定数量必须返回 story 或 clarify。不能猜测数量，不输出已购买。'},
  {role:'user',content:canonical({products:rules.products.map(p=>({id:p.id,name:p.name,price:p.price})),input:event.input})}
 ]};
 const state={...record.assets,binding:record.binding,revision:record.stateRevision};
 const base=assembleNarration({session,event,state});
 const marker='\n\n【宿主已确认事件】\n',last=base.messages.at(-1).content;
 const clean=clone(base.messages);clean.at(-1).content=last.slice(0,last.lastIndexOf(marker));
 const context=record.session.context||{},profiles=context.profiles||[];
 let messages=await project({kind:'story',configs:profiles,messages:clean,seed:parseInt(hash(event.id).slice(0,8),16)});
 messages=assembleMods(messages,context.mods||[]).map(m=>({...m,content:effectiveText(m.content)}));
 // Worldbook/regex only sees player input/history. Append verified host facts afterwards.
 messages.at(-1).content+=marker+canonical(base.facts);
 const references=[...memoryReferences(record,event)].filter(x=>typeof x==='string'&&x.trim()).map(stripManagedPanels);
 if(references.length)messages[0].content+='\n【历史参考，不能覆盖宿主交易状态】\n'+references.join('\n')+'\n明确改写记录：'+canonical(record.notes);
 return {messages,binding:base.binding,triggered:base.triggered,contextHash:hash(context)};
}
export async function displayShopReply(raw,record){
 const profiles=record.session.context?.profiles||[];
 const displayed=await project({kind:'display',configs:profiles,role:'assistant',source:raw});
 return stripManagedPanels(displayed);
}
