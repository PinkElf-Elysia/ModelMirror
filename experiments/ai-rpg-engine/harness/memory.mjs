import {smallMessages,largeMessages,pendingBatch,summaryContext} from '../plugins/auto-summary-v2.mjs';
import {recallMemories,applyModelChanges,requireLibrary} from '../plugins/memory-palace.mjs';
import {MEMORY_INSTRUCTION,parseMemoryProposal} from '../studio/memory-task-state.mjs';
import {summaryV2State} from '../studio/default-summary-v2.mjs';
import {requireMemoryState} from '../studio/memory-state.mjs';
import {clone,hash,canonical} from './contracts.mjs';
export const extraStages=['summary-small','summary-large','memory'];
export function inheritMemory(s={}){
 const summary=s.rollingSummary?summaryV2State(s):{large:'',covered:0,turns:[]};
 const entries=s.memoryPalace?requireMemoryState(s).entries:[];
 return {summaryEnabled:s.shopSourceAuthorizations?.summary?.enabled===true,memoryEnabled:s.shopSourceAuthorizations?.memory?.enabled===true,
 summary:clone(summary),entries:clone(entries),applied:[],inheritedHistory:clone(s.history||[]),baseTurns:s.turns?.length||0};
}
export function memoryEnabled(r){return r.memory?.summaryEnabled||r.memory?.memoryEnabled;}
export const needsLargeSummary=r=>r.memory?.summaryEnabled&&pendingBatch(r.memory.summary).length===20;
export function nextMemoryStage(r,eventId){
 const m=r.memory;if(!m)return null;
 if(m.summaryEnabled&&r.stageRuns[eventId+'-summary-small']?.status!=='succeeded')return 'summary-small';
 if(m.memoryEnabled&&r.stageRuns[eventId+'-memory']?.status!=='succeeded')return 'memory';
 return null;
}
export function memoryMessages(r,e,purpose){
 const turn=r.projection.find(p=>p.eventId===e.id),number=r.memory.baseTurns+r.projection.indexOf(turn)+1;
 if(purpose==='summary-small')return {messages:smallMessages({input:turn.input,raw:turn.raw})};
 if(purpose==='summary-large')return {messages:largeMessages(r.memory.summary.large,pendingBatch(r.memory.summary))};
 return {messages:[{role:'system',content:MEMORY_INSTRUCTION},{role:'user',content:canonical({entries:r.memory.entries.filter(e=>!e.deleted).map(e=>({id:e.id,baseVersion:e.version,protected:e.protected,...e.value})),
 turns:[{number,input:turn.input,raw:turn.raw}],corrections:r.notes})}]};
}
function proposedMemory(r,raw,eventId){
 const operations=parseMemoryProposal(raw),index=r.projection.findIndex(p=>p.eventId===eventId),number=r.memory.baseTurns+index+1;
 return applyModelChanges(r.memory.entries,operations,{from:number,through:number,outputHash:hash(raw),newIds:operations.filter(o=>o.type==='create').map((_,i)=>'mem-'+hash([eventId,i]).slice(0,40))});
}
export function validateMemory(raw,r,purpose,eventId){
 try{if(purpose==='memory'){proposedMemory(r,raw,eventId);return null;}return typeof raw==='string'&&!!raw.trim()&&raw.length<=10000?null:'SHOP_SUMMARY_INVALID';}catch{return 'SHOP_MEMORY_INVALID';}
}
export function commitMemory(r,run,result){
 if(run.purpose==='narration'){if(memoryEnabled(r))r.active=run.eventId;else r.events.find(e=>e.id===run.eventId).memoryCheckpoint=clone(r.memory);return;}
 if(!extraStages.includes(run.purpose))return;
 const m=r.memory,e=r.events.find(e=>e.id===run.eventId);
 if(run.purpose==='summary-small'){
  const number=m.baseTurns+r.projection.findIndex(p=>p.eventId===e.id)+1;
  if(!m.summary.turns.some(t=>t.requestId===e.id))m.summary.turns.push({id:number,requestId:e.id,small:result.raw});
 }else if(run.purpose==='summary-large'){const batch=pendingBatch(m.summary);m.summary.large=result.raw;m.summary.covered=batch.at(-1).id;}
 else m.entries=proposedMemory(r,result.raw,e.id);
 m.applied.push(run.id);e.narration.status='complete';e.narration.error=null;
}
export function memoryReferences(r,event){
 if(!r.memory)return [];
 requireLibrary(r.memory.entries);
 const inherited=r.memory.inheritedHistory||[],turns=[];
 for(let i=0;i+1<inherited.length;i+=2)if(inherited[i].role==='user'&&inherited[i+1].role==='assistant')turns.push({input:inherited[i].content,raw:inherited[i+1].content});
 const recall=r.memory.memoryEnabled?recallMemories({entries:r.memory.entries,turns:[...turns,...r.projection],input:event.input||''}):null;
 return [r.memory.summaryEnabled?summaryContext(r.memory.summary):'',recall?.entries.length?'【记忆宫殿召回，仅作历史资料】\n'+canonical(recall.entries):''].filter(Boolean);
}
