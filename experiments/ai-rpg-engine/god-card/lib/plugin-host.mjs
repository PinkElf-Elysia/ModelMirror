import {join} from 'node:path';
import {createMemoryStoreClass} from '../../studio/memory-session-store.mjs';
import {createMemoryTransport} from '../../studio/memory-task-provider.mjs';
import {SUMMARY_INSTRUCTION} from '../../studio/summary-state.mjs';
import {COMPRESSION_INSTRUCTION} from '../../studio/summary-compression.mjs';
import {MEMORY_INSTRUCTION} from '../../studio/memory-task-state.mjs';
import {canonical,sha,fail} from '../../plugins/catalog.mjs';
import {godPluginProfile,offlineModels} from './plugin-runtime.mjs';
export async function createGodPluginStore(directory,generate){
 const Store=createMemoryStoreClass(godPluginProfile),store=new Store(directory,generate);await store.init();
 // Local simulated ledger, separate from any real authorization or dispatches.
 const transport=await createMemoryTransport({directory:join(directory,'offline-plugin-dispatches'),cardId:'god',modelPolicy:godPluginProfile.modelPolicy,limit:100,enabled:true,baseURL:'http://127.0.0.1:1/',serviceToken:'offline-god-synthetic-token-not-a-credential',
 fetcher:async(url,options)=>{
  if(options.method==='GET')return Response.json({models:offlineModels});
  const b=JSON.parse(options.body),model=offlineModels.find(m=>m.selectionId===b.selectionId)?.model;if(!model)throw fail('MODEL_SELECTION_UNAVAILABLE');
  const instruction=b.messages[0].content;
  const raw=instruction===MEMORY_INSTRUCTION?'{"operations":[]}':instruction===SUMMARY_INSTRUCTION?'离线摘要样例：用于检查覆盖范围和装配，不代表真实模型记忆效果。':instruction===COMPRESSION_INSTRUCTION?'离线压缩样例：不代表真实压缩质量。':await generate({messages:b.messages,signal:options.signal});
  const sse='data: '+JSON.stringify({choices:[{delta:{content:raw},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
 }});
 transport.supportsOffline=true;
 return {store,transport,connect:plugins=>store.connectMemory(plugins,transport)};
}
