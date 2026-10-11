import {assembleHistoryRequest} from './history-assembly.mjs';
import {assembleSummaryRequest,requireContextEvidence,commitSummaryStory} from './summary-assembly.mjs';
import {assembleMemoryRequest,requireMemoryEvidence,commitMemoryStory,settleMemoryStory} from './memory-assembly.mjs';
import {canonical,fail} from '../plugins/catalog.mjs';
// Server-owned adapters only; no adapter code or history is accepted from clients.
export function createCardContext({cardId,assemble,state=null}){
 if(typeof cardId!=='string'||!/^[a-z][a-z0-9-]{0,63}$/.test(cardId)||typeof assemble!=='function')throw fail('CARD_CONTEXT_ADAPTER_INVALID');
 if(state&&state.cardId!==cardId)throw fail('CARD_STATE_BINDING_INVALID');
 const base=(session,input)=>{
  if(session.runtime?.descriptor?.cardId!==cardId||(session.cardId!==undefined&&session.cardId!==cardId))throw fail('CARD_CONTEXT_BINDING_INVALID');
  const result=assemble(structuredClone(session),input),m=result?.messages;
  if(!Array.isArray(m)||m.length!==session.history.length+2||m[0]?.role!=='system'||typeof m[0].content!=='string'||!m[0].content.trim()||
   canonical(m.slice(1,-1))!==canonical(session.history)||m.at(-1)?.role!=='user'||typeof result.current!=='string'||result.current!==m.at(-1).content||
   m.some(x=>!x||Object.keys(x).sort().join(',')!=='content,role'||typeof x.content!=='string'))throw fail('CARD_CONTEXT_ASSEMBLY_INVALID');
  return result;
 };
 return Object.freeze({
  history:(s,i,p)=>assembleHistoryRequest(s,i,p,base),
  summary:(s,i,p)=>assembleSummaryRequest(s,i,p,base,state),
  memory:(s,i,p)=>assembleMemoryRequest(s,i,p,base,state),
  verifySummary:s=>requireContextEvidence(s,base,state),
  verifyMemory:s=>requireMemoryEvidence(s,base,state),
  commitSummary:(s,t,r)=>commitSummaryStory(s,t,r,base,state),
  commitMemory:(s,t,r)=>commitMemoryStory(s,t,r,base,state),
  settleMemory:(s,op)=>{state?.guard(s);return settleMemoryStory(s,op,state);}
 });
}
