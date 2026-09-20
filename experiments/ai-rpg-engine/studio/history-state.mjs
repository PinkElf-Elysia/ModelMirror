import {DEFAULT_CONFIG,validConfig,POLICY_VERSION} from '../plugins/history-window.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
export const operationId=x=>typeof x==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9-]{0,79}$/.test(x);
export const revision=x=>Number.isSafeInteger(x)&&x>=0;
export const exact=(x,keys)=>x&&typeof x==='object'&&!Array.isArray(x)&&Object.keys(x).sort().join(',')===keys.slice().sort().join(',');
export const initialHistoryState=(config=DEFAULT_CONFIG)=>{if(!validConfig(config))throw fail('HISTORY_CONFIGURATION_INVALID',400);return {policyVersion:POLICY_VERSION,revision:0,config:{...config},pending:null,operations:{}};};
export function requireHistoryState(s){
 const h=s.historyWindow;
 if(!exact(h,['policyVersion','revision','config','pending','operations'])||h.policyVersion!==POLICY_VERSION||!revision(h.revision)||!validConfig(h.config)||!h.operations||typeof h.operations!=='object'||Array.isArray(h.operations)||(h.pending!==null&&!operationId(h.pending)))throw fail('HISTORY_STATE_INVALID');
 const completed=[];
 for(const [id,o] of Object.entries(h.operations)){
  if(!operationId(id)||!exact(o,['hash','input','status','configRevision'])||!validSave(o.input)||o.input.operationId!==id||o.hash!==sha(canonical(o.input))||!['pending','complete','not-applied'].includes(o.status)||!revision(o.configRevision))throw fail('HISTORY_STATE_INVALID');
  if(o.status==='pending'&&(h.pending!==id||o.configRevision!==h.revision||o.input.expectedConfigRevision!==h.revision||o.input.expectedSessionRevision!==s.revision))throw fail('HISTORY_STATE_INVALID');
  if(o.status==='complete'){if(o.configRevision<1||o.configRevision>h.revision||o.input.expectedConfigRevision!==o.configRevision-1)throw fail('HISTORY_STATE_INVALID');completed.push(o);}
 }
 if(h.pending!==null&&h.operations[h.pending]?.status!=='pending')throw fail('HISTORY_STATE_INVALID');
 completed.sort((a,b)=>a.configRevision-b.configRevision);
 if(completed.length!==h.revision||completed.some((o,i)=>o.configRevision!==i+1)||completed.length&&canonical(h.config)!==canonical(configFrom(completed.at(-1).input)))throw fail('HISTORY_STATE_INVALID');
 const origin=s.provenance?s.turns?.[s.provenance.turn-1]?.historyPolicy?.config:DEFAULT_CONFIG;
 if(h.revision===0&&canonical(h.config)!==canonical(origin))throw fail('HISTORY_STATE_INVALID');
 return h;
}
export const configFrom=x=>({turns:x.turns,includeInitialCharacter:x.includeInitialCharacter});
export const validSave=x=>exact(x,['operationId','expectedSessionRevision','expectedConfigRevision','turns','includeInitialCharacter'])&&operationId(x.operationId)&&revision(x.expectedSessionRevision)&&revision(x.expectedConfigRevision)&&validConfig(configFrom(x));
export function effectiveHistoryPolicy(s,authorization){
 const h=requireHistoryState(s);
 if(!exact(authorization,['enabled','revision'])||typeof authorization.enabled!=='boolean'||! /^[a-f0-9]{64}$/.test(authorization.revision))throw fail('HISTORY_AUTHORIZATION_UNKNOWN');
 const value={policyVersion:POLICY_VERSION,configRevision:h.revision,config:{...h.config},enabled:authorization.enabled,authorizationRevision:authorization.revision,runtimeHash:s.runtime.hash};
 return {...value,revision:sha(canonical(value))};
}

export function requireHistoryEvidence(s){
 for(let i=0;i<s.turns.length;i++){
  const t=s.turns[i],p=t.historyPolicy;
  const origins=s.requests?.[t.requestId]||s.provenance?.requestOrigins?.find(o=>o.requestId===t.requestId)?.record||s.source?.requestOrigins?.find(o=>o.requestId===t.requestId)?.record;
  if(!p||p.policyVersion!==POLICY_VERSION||!validConfig(p.config)||typeof p.enabled!=='boolean'||!revision(p.configRevision)||p.totalTurns!==i||!Array.isArray(p.selectedTurns)||!Array.isArray(p.selectedRequestIds)||p.initialSnapshotAdded!==(p.enabled&&p.config.includeInitialCharacter&&i>p.config.turns)||!['requestHash','policyRevision','authorizationRevision','runtimeHash'].every(k=>typeof p[k]==='string'&&/^[a-f0-9]{64}$/.test(p[k]))||!origins||origins.status!=='complete'||origins.assemblyHash!==p.requestHash||canonical(origins.historyPolicy)!==canonical(p))throw fail('HISTORY_TURN_BINDING_INVALID');
  const start=p.enabled?Math.max(0,i-p.config.turns):0,turns=Array.from({length:i-start},(_,n)=>start+n+1);
  if(canonical(p.selectedTurns)!==canonical(turns)||canonical(p.selectedRequestIds)!==canonical(turns.map(n=>s.turns[n-1].requestId)))throw fail('HISTORY_TURN_BINDING_INVALID');
 }
}
