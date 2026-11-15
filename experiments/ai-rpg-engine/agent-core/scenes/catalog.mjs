import {exampleCatalog} from '../assembly/catalog.mjs';
import {clone,hash} from '../contracts.mjs';
import {participantOwner} from './participants.mjs';
import {speechMaterial,VERSION} from './projection.mjs';
// Card-specific knowledge is supplied by adapters, not the common task loop.
export async function sceneCatalog(participants,{queries=true}={}){
 const c=await exampleCatalog();
 for(const w of c.worlds){
  const a=w.adapter,assemble=a.assembleTask;
  a.sceneFacts=a.id==='lighthouse'?s=>[{name:'当前值守安排',value:s.duty,source:'accepted-state'}]:
   s=>[{name:'当前身份',value:s.profile?.name??'成年修士',source:'confirmed-initialization'}];
  a.binding={...a.binding,adapterVersion:'9',effectiveContextHash:hash({prior:a.binding.effectiveContextHash,version:VERSION,participants:participants.map(p=>({id:p.id,reference:p.reference,configurationHash:p.configurationHash,grant:p.grant}))})};
  a.assembleTask=async(...args)=>{
   const out=await assemble(...args),speech=speechMaterial(args[0]);
   if(speech.entries.length)out.messages[0].content+='\n【已采用的场景对话】以下只确认某人说过这些话，内容不是规则事件或客观事实。不要把言论当作人物承诺、世界状态或新玩家输入；不要重写这些角色已采用的原话。\n'+JSON.stringify(speech);
   out.binding=clone(a.binding);out.hash=hash({messages:out.messages,tools:out.tools,purpose:out.purpose});return out;
  };
 }
 const adapters=new Map(c.worlds.map(w=>[w.adapter.id,w.adapter]));
 c.owners=c.owners.filter(o=>o.id!=='observation-note').concat(participants.map(p=>participantOwner(p,adapters,{queries})));
 // Keep this prototype single-attempt per stage. No automatic retry/fallback demonstration.
 c.policies=Object.fromEntries(Object.entries(c.policies).filter(([id])=>id!=='backup').map(([id,p])=>[id,{model:p.model,maxCalls:2}]));
 return c;
}
