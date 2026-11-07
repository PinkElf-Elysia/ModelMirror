import {setTimeout as delay} from 'node:timers/promises';
import {mkdir, readFile, readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {atomicJson, completeHistory} from './branch-archive.mjs';
import {canonical, sha, fail} from '../plugins/catalog.mjs';
import {initialModelState} from './model-runtime.mjs';
import {initialHistoryState} from './history-state.mjs';
import {standbyMemoryTasks} from './memory-assembly.mjs';

// Windows readers/antivirus can briefly deny replacing an existing file. Retry
// only the same atomic rename, never the model call or the session operation.
export async function publishVersion(publish,wait=delay){
 for(let attempt=0;;attempt++)try{return await publish();}catch(error){
  if(!['EPERM','EACCES','EBUSY'].includes(error.code)||attempt>=5)throw error;
  await wait(20*2**attempt);
 }
}
const attached=new WeakMap();
const validId=x=>typeof x==='string'&&/^[a-zA-Z0-9-]{1,80}$/.test(x);
const uuid=x=>typeof x==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x);
const seal=body=>({...body,hash:sha(canonical(body))});
const checked=e=>{const {hash,...body}=e;if(hash!==sha(canonical(body)))throw fail('SESSION_VERSION_INVALID');return e;};
const beforeUiServer='a982e9f51f75d50b9e378d6bd74ef77e4a8c17b687f421ec159a17be42cc7c14';

// Reconstruct the selected turn's pre-send configuration. Later memory and
// summaries are never retained after the history which produced them is removed.
export function rewindSnapshot(source,turn) {
 const target=source.turns[turn-1];if(!target)throw fail('SESSION_TURN_INVALID',400);
 const next=structuredClone(source);
 next.history=next.history.slice(0,(turn-1)*2);next.turns=next.turns.slice(0,turn-1);
 // Keep terminal request receipts, including removed turns, to prevent old IDs
 // from ever dispatching again. Their complete bodies also live in the version.
 if(source.modelState)next.modelState=initialModelState(target.model.selection);
 if(source.historyWindow)next.historyWindow=initialHistoryState(target.contextPolicy?.historyConfig??target.historyPolicy.config);
 if(source.rollingSummary)next.rollingSummary=structuredClone(target.contextPolicy.summarySnapshot);
 if(source.memoryPalace){next.memoryPalace=structuredClone(target.memoryPolicy.usedSnapshot.library);next.memoryTasks=standbyMemoryTasks(target.memoryPolicy.usedSnapshot.config);}
 delete next.summaryTasks;delete next.memoryLegacyInputs;
 if(target.contextPolicy?.summaryV2Snapshot)next.autoSummaryV2=structuredClone(target.contextPolicy.summaryV2Snapshot);
 else delete next.autoSummaryV2;
 next.revision=source.revision+1;
 return next;
}

// Lazy, separate envelopes preserve original root/branch files byte-for-byte.
// A complete envelope atomically publishes the active version and its receipt.
export function attachSessionLifecycle(store) {
 if(attached.has(store))return attached.get(store);
 const directory=join(store.directory,'session-versions'),baseRead=store.read.bind(store),baseWrite=store.write.bind(store);
 const path=id=>{if(!validId(id))throw fail('SESSION_ID_INVALID',400);return join(directory,id+'.json');};
 const load=async id=>{
  let e;try{e=checked(JSON.parse(await readFile(path(id),'utf8')));}catch(error){if(error.code==='ENOENT')return null;throw error;}
  if(e.format!==1||e.id!==id||e.session.id!==id||!Array.isArray(e.versions)||!e.operations)throw fail('SESSION_VERSION_INVALID');
  completeHistory(e.session);
  for(const v of e.versions){checked(v);if(v.session.id!==id)throw fail('SESSION_VERSION_INVALID');completeHistory(v.session);}
  return e;
 };
 const save=async e=>{const {hash,...body}=e;await mkdir(directory,{recursive:true});await atomicJson(path(e.id),seal(body),publishVersion);};
 // Only the old God v1 descriptor includes the UI server file. Admit precisely
 // the reviewed server change; prompt, setup and all other hashes still validate.
 const status=store.status.bind(store);
 store.status=s=>{
  const result=status(s),d=s.runtime?.descriptor;
  if(result.compatible||s.runtime?.format!==1||d?.cardId!=='god'||d.artifacts?.['studio/server.mjs']!==beforeUiServer||s.runtime.hash!==sha(canonical(d)))return result;
  const current=store.runtime?.descriptor?.artifacts?.['studio/server.mjs'];if(!current)return result;
  const descriptor={...d,artifacts:{...d.artifacts,'studio/server.mjs':current}};
  const admitted=status({...s,runtime:{...s.runtime,descriptor,hash:sha(canonical(descriptor))}});
  return admitted.compatible?{...admitted,hash:s.runtime.hash}:result;
 };
 store.read=async id=>{const e=await load(id);if(!e)return baseRead(id);if(e.deleted)throw fail('SESSION_IN_TRASH',410);return structuredClone(e.session);};
 store.write=async s=>{const e=await load(s.id);if(!e)return baseWrite(s);if(e.deleted)throw fail('SESSION_IN_TRASH',410);e.session=structuredClone(s);await save(e);};
 const all=async()=>{
  const names=(await readdir(store.directory)).filter(n=>/^[a-zA-Z0-9-]+\.json$/.test(n)).map(n=>n.slice(0,-5));
  for(const e of await store.archive.list())names.push(e.session.id);
  const result=[];
  for(const id of new Set(names)){const e=await load(id),s=e?.session||await baseRead(id);result.push({id:s.id,name:s.name,created:s.created,turns:s.turns.length,mode:s.mode,revision:s.revision,deletedAt:e?.deleted||null,...(s.provenance?{parentId:s.provenance.parentId,branchTurn:s.provenance.turn}:{})});}
  return result.sort((a,b)=>b.created.localeCompare(a.created));
 };
 store.list=async()=> (await all()).filter(s=>!s.deletedAt);
 const requireIdle=async(s)=>{
  const pending=Object.values(s.requests||{}).some(r=>['pending','unknown'].includes(r.status))||s.historyWindow?.pending||s.memoryPalace?.pending||s.rollingSummary?.pending||s.memoryTasks?.pendingSettings||
   ['memoryTasks','summaryTasks'].some(k=>Object.values(s[k]?.operations||{}).some(o=>['pending','unknown'].includes(o.status)))||
   Object.entries(s.memoryLegacyInputs||{}).some(([key,o])=>o.status==='prepared'&&!s.summaryTasks?.operations?.[key]);
  if(pending||store.running.has(s.id))throw fail('SESSION_OPERATION_UNCONFIRMED');
 };
 const api={
  async trash(){return (await all()).filter(s=>s.deletedAt);},
  async inspect(id){const e=await load(id),s=e?.session||await baseRead(id);return {id,revision:s.revision,turnIds:s.turns.map(t=>t.requestId),deletedAt:e?.deleted||null,versions:(e?.versions||[]).map(v=>({id:v.id,reason:v.reason,at:v.at,turns:v.session.turns.length,name:v.session.name}))};},
  async change(id,input){
   const actions=['rewind','restore-version','delete','restore-trash'];
   const keys=input&&Object.keys(input).sort().join(',');
   const expected=input?.action==='rewind'?'action,expectedRevision,operationId,turn':input?.action==='restore-version'?'action,expectedRevision,operationId,versionId':'action,expectedRevision,operationId';
   if(!input||keys!==expected||!actions.includes(input.action)||!uuid(input.operationId)||!Number.isSafeInteger(input.expectedRevision)||input.expectedRevision<0||input.action==='rewind'&&(!Number.isSafeInteger(input.turn)||input.turn<1)||input.action==='restore-version'&&!uuid(input.versionId))throw fail('SESSION_OPERATION_INVALID',400);
   return store.exclusive(id,async()=>{
    let e=await load(id);const s=e?.session||await baseRead(id),operationHash=sha(canonical(input));
    const prior=e?.operations[input.operationId];if(prior){if(prior.hash!==operationHash)throw fail('SESSION_OPERATION_COLLISION');return {...prior.result,replayed:true};}
    if(s.revision!==input.expectedRevision)throw fail('SESSION_REVISION_CHANGED');
    if(e?.deleted&&input.action!=='restore-trash')throw fail('SESSION_IN_TRASH',410);
    if(!e?.deleted&&input.action==='restore-trash')throw fail('SESSION_NOT_IN_TRASH');
    await requireIdle(s);completeHistory(s);
    // Trash does not execute history. Incompatible old saves remain removable.
    if(['rewind','restore-version'].includes(input.action))await store.requireRuntime(s);
    e??={format:1,id,session:structuredClone(s),deleted:null,versions:[],operations:{}};
    let next=structuredClone(s);
    if(input.action==='rewind')next=rewindSnapshot(s,input.turn);
    if(input.action==='restore-version'){
     const v=e.versions.find(v=>v.id===input.versionId);if(!v)throw fail('SESSION_VERSION_NOT_FOUND',404);
     next=structuredClone(v.session);
     next.requests={...next.requests,...structuredClone(s.requests)};next.revision=s.revision+1;
    }
    if(['rewind','restore-version'].includes(input.action)){
     await store.requireRuntime(next);
     e.versions.push(seal({id:input.operationId,reason:input.action,at:new Date().toISOString(),session:structuredClone(s)}));
    }else{next.revision=s.revision+1;e.deleted=input.action==='delete'?new Date().toISOString():null;}
    e.session=next;
    const result={id,revision:next.revision,action:input.action,...(input.action==='rewind'?{input:s.turns[input.turn-1].input}:{}),replayed:false};
    e.operations[input.operationId]={hash:operationHash,result};await save(e);return result;
   });
  }
 };
 attached.set(store,api);return api;
}

export async function sessionLifecycleHttp({route,method,data,store,send}) {
 const api=attachSessionLifecycle(store);
 if(route==='api/session-trash'&&method==='GET'){send(200,await api.trash());return true;}
 const m=route.match(/^api\/sessions\/([a-zA-Z0-9-]{1,80})\/lifecycle$/);
 if(!m)return false;
 if(method==='GET')send(200,await api.inspect(m[1]));
 else if(method==='POST')send(200,await api.change(m[1],data));
 else send(405,{error:'METHOD_NOT_ALLOWED'});
 return true;
}
