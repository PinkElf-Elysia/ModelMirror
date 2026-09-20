import {createControlledProvider} from './controlled-provider.mjs';
import {createSummaryBudget} from './summary-budget.mjs';
import {fail} from '../plugins/catalog.mjs';
export async function createSummaryTransport(options){
 const budget=await createSummaryBudget(options);
 const control=await createControlledProvider({...options,reserveSlot:(lease,binding)=>budget.resolve(lease,binding)});
 return {budget,catalog:control.catalog,status:async()=>({...await budget.status(),enabled:(await control.status()).enabled}),
  async execute(task,signal,guard){
   let failure=null;
   try{await control.generate('earth',{messages:task.messages,params:task.selection.parameters,signal,sessionId:task.sessionId,requestId:task.requestId,selection:task.selection,purpose:task.purpose,lease:task.lease,
    beforeDispatch:async(start,requestHash)=>guard(async()=>{
     if(signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');await budget.dispatched(task.lease,task.requestId,requestHash);
     if(signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');return {response:start()};
    })});}catch(e){failure=e.code||'CONTROL_RESULT_UNKNOWN';}
   return {...await control.recoveredOutput(task.sessionId,task.requestId),failure,dispatched:await budget.wasDispatched(task.lease)};
  },
  async recover(task){return {...await control.recoveredOutput(task.sessionId,task.requestId),dispatched:await budget.wasDispatched(task.lease)};}
 };
}
