import {readFile} from 'node:fs/promises';
import {hash,need,clone,validateBinding} from '../../agent-core/contracts.mjs';
import {validateState,SOURCE_HASH} from '../../local-state/definition.mjs';
import {queryLocalState} from '../../local-state/tools/query.mjs';
export const BASELINE='e40f238cc71eeff4dcda267d4e07d46e18f915e1';
export const ARCHIVE_HASH='2f7104d5f41f21f8591b02a59434e8f147693f7bf560708c90648e245c939e77';
export const ARCHIVE_SESSION='core-c5c081a240f2f4d80f6382aa61e786e3924ce354';
export function project(record,provenance,sessionId){
 need(record?.id===sessionId&&record.format==='modelmirror.rpg.agent-core-session/3','SOURCE_ID');
 validateBinding(record.binding);validateState(record.state);
 need(record.config&&Object.keys(record.config).every(k=>['profiles','mods'].includes(k))&&['profiles','mods'].every(k=>Array.isArray(record.config[k])&&record.config[k].length===0),'SOURCE_CUSTOMIZATION_UNSUPPORTED');
 need(record.binding.adapterId==='cultivation'&&record.binding.adapterVersion==='4'&&record.binding.sourceHash===SOURCE_HASH,'SOURCE_BINDING');
 need(Number.isSafeInteger(record.revision)&&Number.isSafeInteger(record.stateRevision),'SOURCE_VERSION');
 need(typeof record.enabled==='boolean'&&(record.pending===null||typeof record.pending==='string'),'SOURCE_PENDING');
 return clone({session:record.id,branch:record.id,version:record.stateRevision,revision:record.revision,
  binding:record.binding,state:record.state,pending:record.pending,enabled:record.enabled,
  provenance,historyReference:record.turns?.at(-1)?{eventId:record.turns.at(-1).eventId,body:record.turns.at(-1).body,nature:'saved-narrative-not-authoritative'}:null});
}
export function assertReady(world){need(!world.pending,'SOURCE_TURN_PENDING');need(world.enabled,'SOURCE_DISABLED');}
export function queries(world){
 return [queryLocalState(world.state,world.version,{queries:[{domain:'items'},{domain:'abilities'},{domain:'relations'}]}),
 queryLocalState(world.state,world.version,{queries:[{domain:'traits'},{domain:'balances'},{domain:'equipment'}]})];
}
export function archiveSource(file){
 return {kind:'saved-session-copy',async read(){
  const raw=await readFile(file,'utf8');need(hash(raw)===ARCHIVE_HASH,'SOURCE_ARCHIVE_CHANGED');
  return project(JSON.parse(raw),{kind:'saved-session-copy',baseline:BASELINE,hash:ARCHIVE_HASH,
   label:'R4 已验收实测会话的只读副本（历史检查点，不追踪主线当前进度）'},ARCHIVE_SESSION);
 }};
}
// Configuration is supplied by the local launcher, never by HTTP input. No writes, redirects or cookies.
export function httpSource(url,sessionId){
 const u=new URL(url);need(u.protocol==='http:'&&u.hostname==='127.0.0.1'&&u.port&&!u.username&&!u.password&&!u.search&&!u.hash,'SOURCE_URL');
 need(u.pathname==='/api/cultivation/sessions/'+sessionId,'SOURCE_URL');
 return {kind:'live-view',async read(){
  const res=await fetch(u,{method:'GET',redirect:'error',signal:AbortSignal.timeout(5000)});
  need(res.ok,'SOURCE_UNAVAILABLE');const raw=await res.text();need(Buffer.byteLength(raw)<16*1024*1024,'SOURCE_CAPACITY');
  return project(JSON.parse(raw),{kind:'live-view',endpoint:u.href,label:'独立适配会话实时只读视图'},sessionId);
 }};
}
