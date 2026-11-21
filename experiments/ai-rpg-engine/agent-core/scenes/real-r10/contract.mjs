import {need, hash} from '../../contracts.mjs';
import {QUERY} from '../projection.mjs';
export {MODEL,policyFor} from '../real/contract.mjs';

export const ports=['18925','18926'], limit=5,total=.50;
export const inputs=[
  '把 2 份养息丹送给周禾，给他服用，我自己不服用。',
  '我决定自己服用 2 份养息丹。',
  '请查一下，现在登记的养息丹还剩多少？'
];
const owners=['world-dialogue','world-dialogue','world-narration','participant-keeper','participant-keeper'];
export const SLOTS=Object.fromEntries([.10,.10,.20,.05,.05].map((cap,i)=>[i+1,{
  id:i+1,cap,purpose:owners[i],max:i===2?32768:8192,
  profile:i===2?'god-long-v1':'core-query-r3',route:i<3?'assembly':'scene'
}]));
export function validateGrant(g,p){
  need(g&&g.taskId===p.taskId&&g.session===p.session&&p.attempt===0&&g.inputHash===hash(p.meta.input),'R10_GRANT');
  const n=g.slot,r=p.request;
  need(p.purpose===SLOTS[n].purpose,'R10_PURPOSE');
  if(n<=2){
    need(p.meta.input===inputs[n-1]&&r.tool_choice==='auto'&&r.tools.length===3&&
      ['prepare_action','query_local_state','count_registered'].every(k=>r.tools.some(t=>t.function.name===k)),'R10_NATIVE_SELECTION');
  }else if(n===4)need(r.tool_choice==='auto'&&hash(r.tools)===hash([QUERY])&&p.meta.input===inputs[2],'R10_OBSERVATION');
  else need(r.tool_choice==='none'&&!r.tools.length,'R10_FINAL');
  if(n===5){
    const [assistant,tool]=r.messages.slice(-2);
    need(assistant.tool_calls?.[0]?.function.name==='query_scene_state'&&tool.role==='tool'&&tool.tool_call_id===assistant.tool_calls[0].id,'R10_QUERY_PAIR');
    const result=JSON.parse(tool.content);
    need(result.status==='available'&&result.entries.some(x=>x.id==='medicine'&&x.quantity===3),'R10_QUERY_SOURCE');
  }else need(!r.messages.some(m=>m.tool_calls?.length||m.role==='tool'),'R10_SINGLE_STAGE');
}
