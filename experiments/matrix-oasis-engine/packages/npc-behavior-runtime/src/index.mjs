import { createHash } from "node:crypto";
import { validateNpcAuthorityPolicyJson, validateWorldEventLedgerJson } from "@matrix-oasis/npc-authority-contracts";
import { validateNpcBehaviorPolicyJson, validateNpcEntityBindingJson } from "@matrix-oasis/npc-behavior-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const motion=Object.freeze({physicsTicksPerSecond:60,bodyRadiusMm:350,bodyHeightMm:1800,floorSnapMm:200,speedMmPerTick:50,turnMilliDegreesPerTick:3000,arrivalToleranceMm:100,movementTickLimit:1800,maximumPathLengthMm:100000});
export const NPC_BEHAVIOR_MOTION_PROFILE=motion;
const hash=(text)=>`sha256:${createHash("sha256").update(text,"utf8").digest("hex")}`;
const preparedBehaviors=new WeakMap();
function freeze(value){if(!value||typeof value!=="object"||Object.isFrozen(value))return value;for(const child of Object.values(value))freeze(child);return Object.freeze(value)}
function fail(code,path=""){return freeze({ok:false,diagnostics:[freeze({phase:"synthesis",severity:"error",code,path,message:code})]})}
function capture(input,keys){if(!input||typeof input!=="object"||Array.isArray(input))return null;const output={};for(const key of keys){const descriptor=Object.getOwnPropertyDescriptor(input,key);if(!descriptor||!("value" in descriptor)||typeof descriptor.value!=="string")return null;output[key]=descriptor.value}return output}
function parseCanonical(text){const value=JSON.parse(text);if(canonicalizeJsonValue(value)!==text)throw new Error("canonical");return value}

export function synthesizeNpcBehaviorPolicy(input){
  try{
    const captured=capture(input,["authorityPolicyJson"]);if(!captured)return fail("NPC_BEHAVIOR_SYNTHESIS_INPUT_INVALID");
    if(!validateNpcAuthorityPolicyJson(captured.authorityPolicyJson).valid)return fail("NPC_BEHAVIOR_SYNTHESIS_AUTHORITY_POLICY_INVALID");
    const authority=parseCanonical(captured.authorityPolicyJson);
    const policy={format:"matrix-oasis.npc-behavior-policy",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",authorityPolicySha256:hash(captured.authorityPolicyJson),actors:authority.actorGrants.map((actor)=>({actorEntityId:actor.actorEntityId,rules:actor.grants.map((grant)=>({nodeId:grant.nodeId,actionId:grant.actionId,executionLimit:1,minimumRevisionGap:0}))})),motion:{...motion}};
    const canonicalNpcBehaviorPolicyJson=canonicalizeJsonValue(policy);const validation=validateNpcBehaviorPolicyJson(canonicalNpcBehaviorPolicyJson);if(!validation.valid)return fail("NPC_BEHAVIOR_SYNTHESIS_OUTPUT_INVALID");
    return freeze({ok:true,npcBehaviorPolicy:policy,canonicalNpcBehaviorPolicyJson});
  }catch{return fail("NPC_BEHAVIOR_SYNTHESIS_INTERNAL_ERROR")}
}

export function synthesizeNpcEntityBindings(input){
  try{
    const keys=["sceneBlueprintJson","scenePackJson","assetBundleJson","spatialSolutionJson","spatialVerificationJson","authorityPolicyJson"];
    const captured=capture(input,keys);if(!captured)return fail("NPC_ENTITY_BINDING_SYNTHESIS_INPUT_INVALID");
    if(!validateNpcAuthorityPolicyJson(captured.authorityPolicyJson).valid)return fail("NPC_ENTITY_BINDING_AUTHORITY_POLICY_INVALID");
    const blueprint=parseCanonical(captured.sceneBlueprintJson),scenePack=parseCanonical(captured.scenePackJson),assetBundle=parseCanonical(captured.assetBundleJson),solution=parseCanonical(captured.spatialSolutionJson);parseCanonical(captured.spatialVerificationJson);const authority=parseCanonical(captured.authorityPolicyJson);
    const grants=new Set(authority.actorGrants.map(({actorEntityId})=>actorEntityId));
    const briefs=new Map(blueprint.assetBriefs.map((brief)=>[brief.id,brief]));
    const solved=new Map(solution.placements.map((placement)=>[placement.placementId,placement]));
    const bindings=[];
    for(const placement of blueprint.placements){const brief=briefs.get(placement.assetBriefId);if(brief?.kind!=="character-placeholder"||!placement.entityId||!grants.has(placement.entityId))continue;const selected=solved.get(placement.id);if(!selected||selected.anchorKind!=="floor")return fail("NPC_ENTITY_BINDING_SPATIAL_PLACEMENT_MISSING",`/placements/${placement.id}`);const visibleNodeIds=blueprint.nodeBindings.filter((node)=>node.visiblePlacementIds.includes(placement.id)).map((node)=>node.nodeId);if(!visibleNodeIds.length)return fail("NPC_ENTITY_BINDING_CHARACTER_INVISIBLE",`/placements/${placement.id}`);bindings.push({actorEntityId:placement.entityId,assetBriefId:brief.id,placementId:placement.id,runtimeEntityId:placement.entityId,homeFloorAnchorId:selected.anchorId,homePositionMm:{x:selected.positionMm[0],y:selected.positionMm[1],z:selected.positionMm[2]},visibleNodeIds});}
    if(bindings.length>6)return fail("NPC_ENTITY_BINDING_REAL_ACTOR_LIMIT");
    const packPlacements=new Set((scenePack.placements??[]).map((placement)=>placement.id??placement.placementId));
    if(bindings.some((binding)=>!packPlacements.has(binding.placementId)))return fail("NPC_ENTITY_BINDING_SCENE_PACK_MISMATCH");
    const materialized=new Set((assetBundle.blueprint?.assetBriefs??[]).filter((brief)=>brief.kind==="character-placeholder").map((brief)=>brief.id));
    if(bindings.some((binding)=>!materialized.has(binding.assetBriefId)))return fail("NPC_ENTITY_BINDING_ASSET_BUNDLE_MISMATCH");
    const document={format:"matrix-oasis.npc-entity-binding",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",identities:{sceneBlueprintSha256:hash(captured.sceneBlueprintJson),scenePackSha256:hash(captured.scenePackJson),assetBundleSha256:hash(captured.assetBundleJson),spatialSolutionSha256:hash(captured.spatialSolutionJson),spatialVerificationSha256:hash(captured.spatialVerificationJson),authorityPolicySha256:hash(captured.authorityPolicyJson)},bindings};
    const canonicalNpcEntityBindingJson=canonicalizeJsonValue(document);if(!validateNpcEntityBindingJson(canonicalNpcEntityBindingJson).valid)return fail("NPC_ENTITY_BINDING_SYNTHESIS_OUTPUT_INVALID");
    return freeze({ok:true,npcEntityBindings:document,canonicalNpcEntityBindingJson});
  }catch{return fail("NPC_ENTITY_BINDING_SYNTHESIS_INTERNAL_ERROR")}
}

export function prepareDeterministicNpcBehavior(input){
  try{
    const captured=capture(input,["behaviorPolicyJson","entityBindingJson","authorityPolicyJson"]);if(!captured)return fail("NPC_BEHAVIOR_PREPARE_INPUT_INVALID");
    if(!validateNpcBehaviorPolicyJson(captured.behaviorPolicyJson).valid||!validateNpcEntityBindingJson(captured.entityBindingJson).valid||!validateNpcAuthorityPolicyJson(captured.authorityPolicyJson).valid)return fail("NPC_BEHAVIOR_PREPARE_CONTRACT_INVALID");
    const policy=parseCanonical(captured.behaviorPolicyJson),bindings=parseCanonical(captured.entityBindingJson),authority=parseCanonical(captured.authorityPolicyJson);
    if(policy.authorityPolicySha256!==hash(captured.authorityPolicyJson)||bindings.identities.authorityPolicySha256!==hash(captured.authorityPolicyJson))return fail("NPC_BEHAVIOR_PREPARE_AUTHORITY_IDENTITY_MISMATCH");
    const grants=new Map(authority.actorGrants.map((actor)=>[actor.actorEntityId,new Set(actor.grants.map((grant)=>`${grant.nodeId}\0${grant.actionId}`))]));
    const bindingMap=new Map(bindings.bindings.map((binding)=>[binding.actorEntityId,binding]));
    for(const actor of policy.actors){if(!bindingMap.has(actor.actorEntityId))return fail("NPC_BEHAVIOR_PREPARE_ACTOR_UNBOUND");if(actor.rules.some((rule)=>!grants.get(actor.actorEntityId)?.has(`${rule.nodeId}\0${rule.actionId}`)))return fail("NPC_BEHAVIOR_PREPARE_RULE_UNAUTHORIZED")}
    const handle=Object.freeze(Object.create(null));preparedBehaviors.set(handle,Object.freeze({policy,bindings,bindingMap}));return freeze({ok:true,prepared:handle,initialState:freeze({nextSequence:1,executions:[]})});
  }catch{return fail("NPC_BEHAVIOR_PREPARE_INTERNAL_ERROR")}
}

function executionMap(state){const map=new Map();for(const value of state.executions??[]){if(!Number.isSafeInteger(value.ruleIndex)||!Number.isSafeInteger(value.count)||!Number.isSafeInteger(value.lastRevision))return null;const key=`${value.actorEntityId}\0${value.ruleIndex}`;if(map.has(key))return null;map.set(key,value)}return map}
export function selectNextNpcBehaviorCommand(input){
  try{
    const data=preparedBehaviors.get(input?.prepared);if(!data)return fail("NPC_BEHAVIOR_PREPARED_INVALID");
    const {runtimeSnapshot,runtimeInspection,worldEventLedgerJson,behaviorState}=input;
    if(!runtimeSnapshot||!runtimeInspection||!behaviorState||!Number.isSafeInteger(behaviorState.nextSequence)||behaviorState.nextSequence<1)return fail("NPC_BEHAVIOR_STATE_INVALID");
    const ledgerReport=validateWorldEventLedgerJson(worldEventLedgerJson);if(!ledgerReport.valid)return freeze({ok:false,diagnostics:ledgerReport.diagnostics});const ledger=JSON.parse(worldEventLedgerJson);
    if(runtimeInspection.status==="ended")return freeze({ok:true,status:"ended",nextBehaviorState:freeze(structuredClone(behaviorState))});
    const currentNodeId=runtimeInspection.location?.id;const available=new Set((runtimeInspection.actions??[]).filter((action)=>action.available).map((action)=>action.id));const executions=executionMap(behaviorState);if(!executions)return fail("NPC_BEHAVIOR_STATE_INVALID");
    for(const actor of data.policy.actors){const binding=data.bindingMap.get(actor.actorEntityId);if(!binding.visibleNodeIds.includes(currentNodeId))continue;for(let ruleIndex=0;ruleIndex<actor.rules.length;ruleIndex+=1){const rule=actor.rules[ruleIndex];if(rule.nodeId!==currentNodeId||!available.has(rule.actionId))continue;const key=`${actor.actorEntityId}\0${ruleIndex}`,prior=executions.get(key)??{actorEntityId:actor.actorEntityId,ruleIndex,count:0,lastRevision:0};if(prior.count>=rule.executionLimit||ledger.revision-prior.lastRevision<rule.minimumRevisionGap)continue;const sequence=behaviorState.nextSequence;const identity=hash(canonicalizeJsonValue({timelineId:ledger.timeline.id,sequence,actorEntityId:actor.actorEntityId,ruleIndex})).slice(7,23);const intent={format:"matrix-oasis.npc-intent",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",id:`intent-${String(sequence).padStart(6,"0")}-${identity}`,actorEntityId:actor.actorEntityId,timelineId:ledger.timeline.id,nodeId:rule.nodeId,actionId:rule.actionId,observed:{revision:ledger.revision,headSha256:ledger.headSha256,runtimeSnapshotSha256:hash(canonicalizeJsonValue(runtimeSnapshot))}};const nextExecutions=[...executions.values().filter((value)=>!(value.actorEntityId===actor.actorEntityId&&value.ruleIndex===ruleIndex)),{actorEntityId:actor.actorEntityId,ruleIndex,count:prior.count+1,lastRevision:ledger.revision+1}].sort((a,b)=>data.policy.actors.findIndex((value)=>value.actorEntityId===a.actorEntityId)-data.policy.actors.findIndex((value)=>value.actorEntityId===b.actorEntityId)||a.ruleIndex-b.ruleIndex);const command={sequence,actorEntityId:actor.actorEntityId,ruleIndex,nodeId:rule.nodeId,actionId:rule.actionId,intentId:intent.id,npcIntentJson:canonicalizeJsonValue(intent)};return freeze({ok:true,status:"command",command,nextBehaviorState:{nextSequence:sequence+1,executions:nextExecutions}})}}
    return freeze({ok:true,status:"quiescent",nextBehaviorState:freeze(structuredClone(behaviorState))});
  }catch{return fail("NPC_BEHAVIOR_SELECT_INTERNAL_ERROR")}
}
