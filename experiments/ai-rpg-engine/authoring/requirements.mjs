import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
const digest=v=>sha256(canonical(v)),same=(a,b)=>canonical(a)===canonical(b),integer=n=>Number.isSafeInteger(n)&&n>=0;
export const REQUIREMENTS_LIMIT=8000,REQUIREMENTS_CHANGES=1000;
export function checkRequirementsText(text){if(typeof text!=='string'||!text.isWellFormed()||[...text].length>REQUIREMENTS_LIMIT)fail('REQUIREMENTS_TEXT_LIMIT');return text;}
export const emptyRequirements=(initialText)=>({revision:0,text:initialText??'',changes:[],...(initialText===undefined?{}:{initialText})});
export function requirementsIntent(input){
 canonical(input);keys(input,['sessionId','operationId','expectedRevision','expectedRequirementsRevision','expectedContextPolicyRevision','text']);
 if(!/^session-[a-f0-9]{64}$/.test(input.sessionId)||!isId(input.operationId)||!integer(input.expectedRevision)||!integer(input.expectedRequirementsRevision)||!isHash(input.expectedContextPolicyRevision))fail('REQUIREMENTS_INTENT_INVALID');
 checkRequirementsText(input.text);return structuredClone(input);
}
export function requirementsAt(state,sessionRevision){
 if(!state)return emptyRequirements();
 const changes=state.changes.filter(c=>c.intent.expectedRevision<sessionRevision);
 return {...emptyRequirements(state.initialText),revision:changes.length,text:changes.at(-1)?.intent.text??state.initialText??'',changes:structuredClone(changes)};
}
export function requirementsSnapshot(state){return {revision:state?.revision??0,text:state?.text??''};}
export function validateRequirements(state,sessionId,sessionRevision,initialText){
 if(!state){if(initialText!==undefined)fail('REQUIREMENTS_STATE_CORRUPT');return;}
 keys(state,['revision','text','changes',...(initialText===undefined?[]:['initialText'])]);
 if(!integer(state.revision)||!Array.isArray(state.changes)||state.revision!==state.changes.length||state.revision>REQUIREMENTS_CHANGES||state.initialText!==initialText)fail('REQUIREMENTS_STATE_CORRUPT');
 checkRequirementsText(state.text);if(initialText!==undefined)checkRequirementsText(initialText);
 let previous=-1;const ids=new Set();
 for(const [index,c] of state.changes.entries()){
  keys(c,['revision','intent','intentHash']);const i=requirementsIntent(c.intent);
  if(c.revision!==index+1||i.sessionId!==sessionId||i.expectedRequirementsRevision!==index||i.expectedRevision<=previous||i.expectedRevision>=sessionRevision||c.intentHash!==digest(i)||ids.has(i.operationId))fail('REQUIREMENTS_STATE_CORRUPT');
  ids.add(i.operationId);previous=i.expectedRevision;
 }
 if(!same(state,requirementsAt(state,sessionRevision)))fail('REQUIREMENTS_STATE_CORRUPT');
}
export function withRequirements(user,state){const text=state?.text??'';return text?user+'\n\n【持续要求】\n'+text+'\n【持续要求结束】':user;}
