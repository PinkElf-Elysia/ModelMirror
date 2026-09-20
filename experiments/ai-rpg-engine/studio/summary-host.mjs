import {COMPRESSION_INSTRUCTION} from './summary-compression.mjs';
import {join} from 'node:path';
import {createSummaryTransport} from './summary-task-provider.mjs';
import {models} from './provider.mjs';
import {SUMMARY_INSTRUCTION} from './summary-state.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
// Offline is an explicit local transport, with separate simulated slots and no network.
export async function summaryHostTransport({directory,control,limit,store,offlineGenerate}){
 const live=await createSummaryTransport({...control,directory:join(directory,'dispatches'),enabled:!!control?.enabled,limit});
 const item={model:models.earth.model,name:'离线样例（不调用模型）',selectionId:'offline-sample',selectionRevision:'offline-v1',available:true,parameters:models.earth.parameters};
 const offline=await createSummaryTransport({directory:join(directory,'offline-summary-dispatches'),enabled:true,limit:100,baseURL:'http://127.0.0.1:1/',serviceToken:'offline-internal-synthetic-token-not-a-credential',fetcher:async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[item]});
  const b=JSON.parse(options.body),raw=b.messages[0].content===SUMMARY_INSTRUCTION?'离线摘要样例：这是一份用于验证设置、覆盖范围和修订的固定文字，不代表模型的总结效果。':b.messages[0].content===COMPRESSION_INSTRUCTION?'离线二次压缩样例：保留尚未兑现的约定与未确认的事实，不代表真实压缩质量。':await offlineGenerate({signal:options.signal});
  const sse='data: '+JSON.stringify({model:item.model,choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:item.model,actualModel:item.model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
 }});
 const select=async id=>(await store.read(id)).mode==='offline'?offline:live;
 return {supportsOffline:true,async catalog({sessionId}={}){if(!sessionId)throw fail('SUMMARY_SESSION_REQUIRED');return (await select(sessionId)).catalog();},status:()=>live.status(),
  async execute(task,...args){return (await select(task.sessionId)).execute(task,...args);},async recover(task){return (await select(task.sessionId)).recover(task);},
  budget:{async reserve(group,id,purposes){return (await select(id)).budget.reserve(group,id,purposes);},async release(lease){return (await select(lease.sessionId)).budget.release(lease);},async recover(group){return await live.budget.recover(group)||await offline.budget.recover(group);}},
  offlineStatus:()=>offline.status()
 };
}
