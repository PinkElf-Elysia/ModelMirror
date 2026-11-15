import {sceneCatalog} from '../catalog.mjs';
import {need,clone,hash} from '../../contracts.mjs';
export const MODEL='google/gemini-3.8-flash';
export const INPUTS={2:'你看到了刚才怎样处理灯油？还有什么需要核实？只依据你获准看到的记录回应，不猜测未提供的余量。',3:'你刚才亲眼看到了处理过程吗？知道现在灯油剩多少吗？不知道请直接说明。'};
export const SLOTS={1:{id:1,purpose:'world-narration',cap:.15,max:32768,profile:'god-long-v1',route:'scene'},2:{id:2,purpose:'participant-keeper',cap:.10,max:8192,profile:'core-query-r3',route:'scene'},3:{id:3,purpose:'participant-visitor',cap:.05,max:8192,profile:'core-query-r3',route:'scene'}};
export function policyFor(slot,quote){return {mode:'controlled-real',model:{id:MODEL,protocol:'rpg-native/1',parameters:{temperature:.7,top_p:.8,max_tokens:slot.max},quote:clone(quote)},backup:null,maxCalls:1,capMicroUSD:Math.round(slot.cap*1e6)};}
export async function liveCatalog(quote,participants){
 const c=await sceneCatalog(participants,{queries:false});c.worlds=c.worlds.filter(w=>w.adapter.id==='lighthouse');
 c.policies=Object.fromEntries(Object.values(SLOTS).map(s=>['slot-'+s.id,policyFor(s,quote)]));return c;
}
export function validateGrant(g,p){
 need(g&&g.taskId===p.taskId&&g.session===p.session&&g.purpose===p.purpose&&p.attempt===0&&g.inputHash===hash(p.meta.input),'R9_REAL_GRANT');
 const slot=SLOTS[g.slot];need(slot&&slot.purpose===p.purpose,'R9_REAL_SLOT');
 need(!p.request.messages.some(m=>m.tool_calls?.length||m.role==='tool'),'R9_REAL_NO_CONTINUATION');
 need(p.request.tool_choice==='none'&&p.request.tools.length===0,'R9_REAL_TOOL_LIMIT');
}
