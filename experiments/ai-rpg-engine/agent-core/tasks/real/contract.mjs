import {need,clone} from '../../contracts.mjs';
export const MODEL='google/gemini-3.8-flash';
export const INPUT='刚才在廊下发生了什么？我的养息丹还剩多少？';
export const SLOTS={1:{id:1,purpose:'world-narration',cap:.30,max:32768,profile:'god-long-v1',route:'actions'},2:{id:2,purpose:'actor-response',actorId:'witness',cap:.15,max:8192,profile:'core-query-r3',route:'actors'},3:{id:3,purpose:'actor-response',actorId:'outsider',cap:.15,max:8192,profile:'core-query-r3',route:'actors'}};
export function policyFor(slot,quote){return {mode:'controlled-real',model:{id:MODEL,protocol:'rpg-native/1',parameters:{temperature:.7,top_p:.8,max_tokens:slot.max},quote:clone(quote)},backup:null,maxCalls:1,capMicroUSD:Math.round(slot.cap*1e6)};}
export function validateGrant(g,p){need(g&&g.taskId===p.taskId&&g.session===p.session&&g.purpose===p.purpose&&p.attempt===0,'R7_REAL_GRANT');need(p.request.tool_choice==='none'&&p.request.tools.length===0&&!p.request.messages.some(m=>m.tool_calls||m.role==='tool'),'R7_REAL_TOOL_LIMIT');}
