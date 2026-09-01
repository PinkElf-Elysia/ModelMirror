import {createNpcAuthorityIncrementalState,createNpcAuthorityTimeline,exportNpcAuthorityIncrementalState,prepareNpcAuthority,submitNpcAuthorityIncrementalIntent,verifyNpcAuthorityIncrementalState} from "@matrix-oasis/npc-authority-runtime";

const sessions=new WeakMap();
function freeze(value){if(!value||typeof value!=="object"||Object.isFrozen(value))return value;for(const child of Object.values(value))freeze(child);return Object.freeze(value)}
function fail(code){return freeze({ok:false,diagnostics:[freeze({phase:"session",severity:"error",code,path:"",message:code})]})}
function strings(input,keys){if(!input||typeof input!=="object"||Array.isArray(input))return null;const out={};for(const key of keys){const descriptor=Object.getOwnPropertyDescriptor(input,key);if(!descriptor||!("value" in descriptor)||typeof descriptor.value!=="string")return null;out[key]=descriptor.value}return out}
async function prepared(input){return prepareNpcAuthority({runtimeGamePackJson:input.runtimeGamePackJson,runtimeReceiptJson:input.runtimeReceiptJson,policyJson:input.policyJson})}
function expose(handle,result){return freeze({ok:true,session:handle,runtimeSnapshot:result.runtimeSnapshot,inspection:result.inspection,canonicalWorldEventLedgerJson:result.canonicalWorldEventLedgerJson})}

export async function createNpcAuthoritySession(input){
  const values=strings(input,["runtimeGamePackJson","runtimeReceiptJson","policyJson","timelineId"]);if(!values)return fail("NPC_AUTHORITY_SESSION_INPUT_INVALID");
  const authority=await prepared(values);if(!authority.ok)return authority;const timeline=createNpcAuthorityTimeline(authority.prepared,{timelineId:values.timelineId,stepLimit:input.stepLimit});if(!timeline.ok)return timeline;const incremental=createNpcAuthorityIncrementalState({prepared:authority.prepared,worldEventLedgerJson:timeline.canonicalWorldEventLedgerJson});if(!incremental.ok)return incremental;const handle=Object.freeze(Object.create(null)),stepLimit=incremental.runtimeSnapshot.stepLimit;sessions.set(handle,{prepared:authority.prepared,state:incremental.state,stepLimit});return expose(handle,incremental);
}
export async function restoreNpcAuthoritySession(input){
  const values=strings(input,["runtimeGamePackJson","runtimeReceiptJson","policyJson","worldEventLedgerJson"]);if(!values)return fail("NPC_AUTHORITY_SESSION_INPUT_INVALID");
  const authority=await prepared(values);if(!authority.ok)return authority;const incremental=createNpcAuthorityIncrementalState({prepared:authority.prepared,worldEventLedgerJson:values.worldEventLedgerJson});if(!incremental.ok)return incremental;const handle=Object.freeze(Object.create(null)),stepLimit=incremental.runtimeSnapshot.stepLimit;sessions.set(handle,{prepared:authority.prepared,state:incremental.state,stepLimit});return expose(handle,incremental);
}
export function resetNpcAuthoritySession(session,timelineId){
  const value=sessions.get(session);if(!value||typeof timelineId!=="string"||timelineId.length<1)return fail("NPC_AUTHORITY_SESSION_INVALID");
  const timeline=createNpcAuthorityTimeline(value.prepared,{timelineId,stepLimit:value.stepLimit});if(!timeline.ok)return timeline;const incremental=createNpcAuthorityIncrementalState({prepared:value.prepared,worldEventLedgerJson:timeline.canonicalWorldEventLedgerJson});if(!incremental.ok)return incremental;const handle=Object.freeze(Object.create(null));sessions.set(handle,{prepared:value.prepared,state:incremental.state,stepLimit:value.stepLimit});return expose(handle,incremental);
}
export function submitNpcAuthorityIntent(session,npcIntentJson){const value=sessions.get(session);if(!value||typeof npcIntentJson!=="string")return fail("NPC_AUTHORITY_SESSION_INVALID");return submitNpcAuthorityIncrementalIntent({state:value.state,npcIntentJson})}
export function exportNpcAuthoritySession(session){const value=sessions.get(session);return value?exportNpcAuthorityIncrementalState(value.state):fail("NPC_AUTHORITY_SESSION_INVALID")}
export function verifyNpcAuthoritySession(session){const value=sessions.get(session);return value?verifyNpcAuthorityIncrementalState(value.state):fail("NPC_AUTHORITY_SESSION_INVALID")}
