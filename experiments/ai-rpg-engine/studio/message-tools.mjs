import {canonical, sha, fail, MESSAGE_TOOLS_ID} from '../plugins/catalog.mjs';
import {initialModelState} from './model-runtime.mjs';
import {initialHistoryState} from './history-state.mjs';
import {standbyMemoryTasks} from './memory-assembly.mjs';
import {completeHistory} from './branch-archive.mjs';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// First-turn branches have no completed turn to archive. Restore only the
// configuration used by that first send, never today's memory or later tasks.
function openingSession(parent, operation, id) {
  const first = parent.turns[0];
  const child = {id, name:operation.name, revision:0, created:new Date().toISOString(),
    runtime:parent.runtime, characterText:parent.characterText, world:parent.world, params:parent.params,
    mode:parent.mode, history:[], turns:[], requests:{}, messageOrigin:operation};
  if (parent.importedTurns) child.importedTurns=structuredClone(parent.importedTurns);
  if (parent.cardId) child.cardId = parent.cardId;
  if (parent.modelState) child.modelState = initialModelState(first.model.selection);
  if (parent.historyWindow) child.historyWindow = initialHistoryState(first.contextPolicy?.historyConfig ?? first.historyPolicy.config);
  if (parent.rollingSummary) child.rollingSummary = structuredClone(first.contextPolicy.summarySnapshot);
  if (parent.memoryPalace) {
    const snapshot = first.memoryPolicy.usedSnapshot;
    child.memoryPalace = structuredClone(snapshot.library);
    child.memoryTasks = standbyMemoryTasks(snapshot.config);
  }
  return child;
}

export async function forkMessage(store, plugins, sessionId, data) {
  if (!data || Object.keys(data).sort().join(',') !== 'operationId,revision,turn' ||
      !uuid.test(data.operationId) || !Number.isSafeInteger(data.turn) || data.turn < 1 ||
      !Number.isSafeInteger(data.revision)) throw fail('MESSAGE_FORK_INVALID',400);
  return store.exclusive(sessionId, async () => {
    const source = await store.read(sessionId);
    const operation = {sessionId, operationId:data.operationId, expectedSessionRevision:data.revision,
      turn:data.turn-1, name:(source.name.slice(0,55)+' · 第 '+data.turn+' 回合重写')};
    const id = 'rewrite-'+sha(canonical([sessionId,data.operationId]));
    if (operation.turn) {
      const prior = await store.archive.find(operation);
      if (prior) return {id:prior.session.id};
    } else {
      let existing;
      try { existing = await store.read(id); } catch (e) { if (e.status!==404 && e.code!=='ENOENT') throw e; }
      if (existing) {
        if (canonical(existing.messageOrigin)!==canonical(operation)) throw fail('MESSAGE_FORK_CONFLICT');
        return {id};
      }
    }
    await store.requireRuntime(source);
    if (source.revision !== data.revision) throw fail('MESSAGE_HISTORY_CHANGED');
    completeHistory(source);
    const ticket = await plugins.invoke({pluginId:MESSAGE_TOOLS_ID,sessionId,capability:'session.message.fork',input:{turn:data.turn}});
    if (operation.turn) {
      const envelope = store.archive.prepare(source,operation);
      await store.archive.publish(envelope, publish=>plugins.commitResult(ticket,publish));
      return {id:envelope.session.id};
    }
    const child = openingSession(source,operation,id);
    if(store.prepareOpeningFork)await store.prepareOpeningFork(source,child);
    await store.requireRuntime(child);
    await plugins.commitResult(ticket,()=>store.write(child));
    return {id};
  });
}

export async function storyProgress(store,id,operationId,control,fixedProgress) {
  if (!uuid.test(operationId)) throw fail('MESSAGE_REQUEST_INVALID',400);
  const session = await store.read(id);
  if(session.mode!=='real')return {supported:false,text:''};
  if(!session.modelState || session.modelState.current.kind!=='controlled'){
    return {supported:!!fixedProgress,text:fixedProgress?.get(JSON.stringify([id,operationId]))??''};
  }
  if(!control?.enabled)return {supported:false,text:''};
  // Only the story task for this browser operation can be observed. Never expose
  // a plugin's private model output, even when it shares the same operation.
  const operation = session.memoryTasks?.operations?.[operationId] ?? session.summaryTasks?.operations?.[operationId];
  const requestId = operation?.tasks.find(t=>t.purpose==='story' && t.status==='pending')?.requestId ??
    (session.requests?.[operationId]?.status==='pending' ? operationId : null);
  if (!requestId) return {supported:true,text:''};
  const url = new URL('/api/rpg/v1/progress/'+encodeURIComponent(id)+'/'+encodeURIComponent(requestId),control.baseURL);
  if (url.protocol!=='http:' || !['server','127.0.0.1','localhost','[::1]'].includes(url.hostname)) throw fail('INVALID_CONTROL_ENDPOINT');
  const result = await (control.fetcher??fetch)(url,{headers:{Authorization:'Bearer '+control.serviceToken},redirect:'error',signal:AbortSignal.timeout(4000)});
  if(!result.ok)throw fail('MESSAGE_PROGRESS_UNAVAILABLE',503);
  const value=await result.json();
  if(typeof value.text!=='string'||value.text.length>524288||value.text.includes(control.serviceToken))throw fail('MESSAGE_PROGRESS_INVALID');
  return {supported:true,text:value.text};
}
