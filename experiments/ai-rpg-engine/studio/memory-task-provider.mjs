import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha} from '../plugins/catalog.mjs';
import {createControlledProvider} from './controlled-provider.mjs';
import {createMemoryBudget} from './memory-budget.mjs';
import {fail} from '../plugins/catalog.mjs';
export async function createMemoryTransport(options){
 const budget=await createMemoryBudget(options);
 const control=await createControlledProvider({...options,reserveSlot:(lease,binding)=>budget.resolve(lease,binding)});
 async function recovered(task){
  const result=await control.recoveredOutput(task.sessionId,task.requestId),dispatched=await budget.wasDispatched(task.lease);
  let cancelledKnown=false;
  // Only inspect a slot already bound by the budget and controlled evidence.
  // This distinguishes a validated complete receipt discarded after cancel from
  // a dropped connection. It grants no right to publish that output.
  if(result?.evidence&&/^earth\/slot-\d+\/result\.json$/.test(result.evidence.ref)){
   const saved=JSON.parse(await readFile(join(options.directory,...result.evidence.ref.split('/')),'utf8')),r=saved.receipt;
   cancelledKnown=saved.error==='CANCELLED'&&saved.sessionId===task.sessionId&&saved.requestId===task.requestId&&r?.status==='complete'&&r.dispatched===true&&r.requestedModel===task.selection.model&&(r.actualModel===null||r.actualModel===task.selection.model)&&r.selectionId===task.selection.selectionId&&r.selectionRevision===task.selection.selectionRevision&&canonical(r.parameters)===canonical(task.selection.parameters)&&r.requestHash===saved.controlRequestHash&&r.rawHash===sha(result.raw)&&r.sseHash===saved.sseHash&&r.retries===0;
  }
  return {...result,dispatched,cancelledKnown};
 }
 return {budget,catalog:control.catalog,status:async()=>({...await budget.status(),enabled:(await control.status()).enabled}),
  async execute(task,signal,guard){
   let failure=null;
   try{await control.generate('earth',{messages:task.messages,params:task.selection.parameters,signal,sessionId:task.sessionId,requestId:task.requestId,selection:task.selection,purpose:task.purpose,lease:task.lease,
    beforeDispatch:async(start,requestHash)=>guard(async()=>{
     if(signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');await budget.dispatched(task.lease,task.requestId,requestHash);
     if(signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');return {response:start()};
    })});}catch(e){failure=e.code||'CONTROL_RESULT_UNKNOWN';}
   return {...await recovered(task),failure};
  },
  async recover(task){return recovered(task);}
 };
}
