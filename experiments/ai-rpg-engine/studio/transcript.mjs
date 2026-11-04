import {canonical,sha,fail,TRANSCRIPT_ID} from '../plugins/catalog.mjs';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import {parse,exportChunks,MAX_BYTES} from '../plugins/transcript.mjs';
import {initialModelState} from './model-runtime.mjs';
import {initialHistoryState} from './history-state.mjs';
import {initialSummaryState} from './summary-state.mjs';
import {initialMemoryState} from './memory-state.mjs';
import {initialMemoryTasks} from './memory-task-state.mjs';

export async function transcriptHttp({req,res,send,match,store,plugins}) {
  const [,sourceId,action]=match;
  const method=action==='export'?'GET':'POST';
  if(req.method!==method)return send(405,{error:'METHOD_NOT_ALLOWED'});
  if(!store||!plugins)throw fail('PLUGIN_HOST_UNAVAILABLE',503);
  const grant=await plugins.invoke({sessionId:sourceId,pluginId:TRANSCRIPT_ID,capability:'session.transcript.'+action});
  const source=await store.read(sourceId);
  if(action==='export'){
    const turns=[...(source.importedTurns||[]),...source.turns];
    if(!turns.length)throw fail('TRANSCRIPT_EMPTY',400);
    await plugins.validateResult(grant);
    res.writeHead(200,{'Content-Type':'text/plain; charset=utf-8','Content-Disposition':'attachment; filename="chat.txt"'});
    try{await pipeline(Readable.from(exportChunks(turns)),res);}catch(e){if(!res.destroyed)throw e;}
    return;
  }
  const key=req.headers['x-import-id'];
  if(typeof key!=='string'||!/^[a-f0-9-]{36}$/i.test(key))throw fail('TRANSCRIPT_IMPORT_ID',400);
  let name;try{name=decodeURIComponent(req.headers['x-session-name']||'').trim();}catch{throw fail('TRANSCRIPT_NAME',400);}
  if(!name||name.length>80||/[\x00-\x1f\x7f]/.test(name))throw fail('TRANSCRIPT_NAME',400);
  const chunks=[];let size=0;
  for await(const chunk of req){size+=chunk.length;if(size>MAX_BYTES)throw fail('TRANSCRIPT_TOO_LARGE',413);chunks.push(chunk);}
  const bytes=Buffer.concat(chunks),{turns,skipped}=parse(bytes);
  const id='import-'+sha(canonical([sourceId,key])),digest=sha(canonical([name,sha(bytes)]));
  const result=await store.exclusive(sourceId,async()=>{
    let prior;
    try{prior=await store.read(id);}catch(e){if(e.status!==404&&e.code!=='ENOENT')throw e;}
    if(prior){if(prior.transcriptDigest!==digest)throw fail('TRANSCRIPT_IMPORT_CONFLICT');return {id,count:turns.length,skipped};}
    await store.requireRuntime(source);
    const child={id,name,created:new Date().toISOString(),revision:0,mode:source.mode,characterText:source.characterText,
      world:source.world,params:source.params,runtime:source.runtime,history:[],turns:[],requests:{},transcriptDigest:digest};
    if(source.cardId)child.cardId=source.cardId;
    if(source.modelState)child.modelState=initialModelState(source.modelState.current);
    if(source.historyWindow)child.historyWindow=initialHistoryState();
    if(source.rollingSummary)child.rollingSummary=initialSummaryState();
    if(source.memoryPalace){child.memoryPalace=initialMemoryState();child.memoryTasks=initialMemoryTasks();}
    if(source.runtime.format>=3){child.importedTurns=turns;}
    else {
      for(const [i,turn] of turns.entries()){
        const requestId='import-'+i;
        const row={...turn,requestId,origin:'import',rawHash:sha(turn.raw),at:child.created};
        const record={status:'complete',origin:'import'};
        if(child.modelState){
          const selection=child.modelState.current;
          row.model={selection,selectionRevision:0,requestedModel:null,actualModel:null,mode:'import'};
          Object.assign(record,{selection,selectionRevision:0});
        }
        child.turns.push(row);child.requests[requestId]=record;
        child.history.push({role:'user',content:turn.input},{role:'assistant',content:turn.raw});
      }
    }
    await store.requireRuntime(child);
    await plugins.commitResult(grant,()=>store.write(child));
    return {id,count:turns.length,skipped};
  });
  send(201,result);
}
