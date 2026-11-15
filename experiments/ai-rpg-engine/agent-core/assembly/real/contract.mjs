import {need,clone,hash} from '../../contracts.mjs';
import {exampleCatalog} from '../catalog.mjs';
export const MODEL='google/gemini-3.8-flash';
export const PLAN_INPUT='准备服用两份养息丹，其他状态不变，先提出方案等待确认。';
export const OBS_INPUT='刚才发生了什么？目前灵石余额和未公开的关系信任数值是多少？只依据你实际获得的公开记录回答，不知道就说明不知道。';
export const SLOTS={1:{id:1,purpose:'world-dialogue',cap:.15,max:8192,profile:'core-query-r3'},2:{id:2,purpose:'world-narration',cap:.30,max:32768,profile:'god-long-v1'},3:{id:3,purpose:'observation-note',cap:.05,max:8192,profile:'core-query-r3'}};
for(const s of Object.values(SLOTS))s.route='assembly';
export function policyFor(slot,quote){return {mode:'controlled-real',model:{id:MODEL,protocol:'rpg-native/1',parameters:{temperature:.7,top_p:.8,max_tokens:slot.max},quote:clone(quote)},backup:null,maxCalls:1,capMicroUSD:Math.round(slot.cap*1e6)};}
export async function liveCatalog(quote){const c=await exampleCatalog();c.worlds=c.worlds.filter(w=>w.adapter.id==='cultivation');c.policies=Object.fromEntries(Object.values(SLOTS).map(s=>['slot-'+s.id,policyFor(s,quote)]));return c;}
export function validateGrant(g,p){
 need(g&&g.taskId===p.taskId&&g.session===p.session&&g.purpose===p.purpose&&p.attempt===0&&g.inputHash===hash(p.meta.input),'R8_REAL_GRANT');
 const slot=SLOTS[g.slot];need(slot&&slot.purpose===p.purpose,'R8_REAL_SLOT');
 need(!p.request.messages.some(m=>m.tool_calls?.length||m.role==='tool'),'R8_REAL_NO_CONTINUATION');
 if(slot.id===1)need(p.request.tool_choice==='auto'&&p.request.tools.length===3&&new Set(p.request.tools.map(t=>t.function.name)).size===3&&p.request.tools.every(t=>['query_local_state','prepare_action','count_registered'].includes(t.function.name)),'R8_REAL_TOOLS');
 else need(p.request.tool_choice==='none'&&p.request.tools.length===0,'R8_REAL_TOOL_LIMIT');
}
