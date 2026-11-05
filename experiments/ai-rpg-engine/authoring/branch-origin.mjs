import {checkRequirementsText} from './requirements.mjs';
import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {MAX_SUMMARY_CHARACTERS} from '../plugins/rolling-summary.mjs';
import {validConfig} from '../plugins/history-window.mjs';
import {requireLibrary} from '../plugins/memory-palace.mjs';

export const BRANCH_ORIGIN_FORMAT='modelmirror.rpg.authored-branch-origin/1';
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),same=(a,b)=>canonical(a)===canonical(b);
const integer=n=>Number.isSafeInteger(n)&&n>=0;
// No archive, grant, request or executable task is projected into the child.
// The owning host must additionally resolve checkpointId and compare this value
// with its verified checkpoint. A hash is consistency evidence, not authority.
export function branchOrigin(checkpoint){
 const body={format:BRANCH_ORIGIN_FORMAT,checkpointId:checkpoint.id,checkpointHash:checkpoint.sha256,
  parentSessionId:checkpoint.boundary.sessionId,turn:checkpoint.boundary.turn,inheritance:clone(checkpoint.inheritance)};
 const value={...body,sha256:digest(body)};validateBranchOrigin(value);return value;
}
export function validateBranchOrigin(value){
 canonical(value);keys(value,['format','checkpointId','checkpointHash','parentSessionId','turn','inheritance','sha256']);
 const {sha256:hash,...body}=value,s=value.inheritance;
 if(value.format!==BRANCH_ORIGIN_FORMAT||!/^branch-[a-f0-9]{64}$/.test(value.checkpointId)||!isHash(value.checkpointHash)||
  !/^session-[a-f0-9]{64}$/.test(value.parentSessionId)||!integer(value.turn)||value.turn<1||value.turn>1000||hash!==digest(body))fail('BRANCH_ORIGIN_INVALID');
 keys(s,['binding','allowanceId','presetId','examples','turns','settings','summary','memory']);
 keys(s.binding,['format','workId','versionId','runtimeHash','openingHash','resourceHash','openingText']);
 if(!isId(s.allowanceId)||!Array.isArray(s.turns)||s.turns.length!==value.turn||!Array.isArray(s.examples))fail('BRANCH_ORIGIN_INVALID');
 for(const [i,t]of s.turns.entries()){
  keys(t,['id','number','user','userMessage','assistant','rawHash','messagesHash',...(Object.hasOwn(t,'variablePanel')?['variablePanel']:[])]);
  if(t.id!=='turn-'+(i+1)||t.number!==i+1||['user','userMessage','assistant'].some(k=>typeof t[k]!=='string'||!t[k].isWellFormed())||t.rawHash!==sha256(t.assistant)||!isHash(t.messagesHash))fail('BRANCH_ORIGIN_INVALID');
 }
 keys(s.settings,['history','modelSuggestions',...(Object.hasOwn(s.settings,'requirements')?['requirements']:[])]);if(Object.hasOwn(s.settings,'requirements'))checkRequirementsText(s.settings.requirements);if(!validConfig(s.settings.history))fail('BRANCH_ORIGIN_INVALID');
 keys(s.summary,['text','coveredThrough','sourceHash','versionId']);keys(s.memory,['entries','processedThrough','sourceHash','versionId']);
 if(!integer(s.summary.coveredThrough)||s.summary.coveredThrough>value.turn-1||
   (s.summary.coveredThrough===0?s.summary.text!==null:typeof s.summary.text!=='string'||!s.summary.text.trim()||[...s.summary.text].length>MAX_SUMMARY_CHARACTERS)||
   !integer(s.memory.processedThrough)||s.memory.processedThrough>value.turn||!isHash(s.summary.sourceHash)||!isHash(s.memory.sourceHash)||
   ![s.summary.versionId,s.memory.versionId].every(v=>v===null||isHash(v)))fail('BRANCH_ORIGIN_INVALID');
 requireLibrary(s.memory.entries);return value;
}
export function originTurns(origin){
 validateBranchOrigin(origin);
 return origin.inheritance.turns.map(t=>({...clone(t),complete:true,
  operationId:'inherited-turn-'+digest({checkpoint:origin.checkpointId,turn:t.number}),
  requestId:'inherited-request-'+digest({checkpoint:origin.checkpointId,turn:t.number})}));
}
export function checkSessionOrigin(session){
 if(!Object.hasOwn(session,'origin'))return null;
 const origin=validateBranchOrigin(session.origin),s=origin.inheritance,b=session.binding;
 if(session.id===origin.parentSessionId||session.allowanceId!==s.allowanceId||
  ['workId','versionId','runtimeHash','openingHash','openingText'].some(k=>b[k]!==s.binding[k])||
  !same(session.turns.slice(0,origin.turn),originTurns(origin)))fail('BRANCH_ORIGIN_MISMATCH');
 return origin;
}
export const summaryOriginRevision=s=>s.origin?.inheritance.summary.text===null||!s.origin?0:1;
export const memoryOriginRevision=s=>s.origin?1:0;
export function originVersion(session,kind,sourceHash){
 const origin=checkSessionOrigin(session);if(!origin)return null;
 const source=origin.inheritance[kind],operationId='inherited-'+kind+'-'+origin.sha256;
 if(kind==='summary'&&source.text===null)return null;
 const body=kind==='summary'?{kind:'inherited',operationId,previousVersionId:null,coveredThrough:source.coveredThrough,sourceHash,
  effectiveText:source.text,result:null,edit:null}:{kind:'inherited',operationId,previousVersionId:null,processedThrough:source.processedThrough,
  sourceHash,entriesHash:digest(source.entries),result:null,edit:null};
 return {id:digest(body),...body};
}
