import { createHash } from "node:crypto";
import { validateNpcAuthorityPolicyJson } from "@matrix-oasis/npc-authority-contracts";
import { validateNpcBehaviorPolicyJson, validateNpcEntityBindingJson } from "@matrix-oasis/npc-behavior-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const motion=Object.freeze({physicsTicksPerSecond:60,bodyRadiusMm:350,bodyHeightMm:1800,floorSnapMm:200,speedMmPerTick:50,turnMilliDegreesPerTick:3000,arrivalToleranceMm:100,movementTickLimit:1800,maximumPathLengthMm:100000});
export const NPC_BEHAVIOR_MOTION_PROFILE=motion;
const hash=(text)=>`sha256:${createHash("sha256").update(text,"utf8").digest("hex")}`;
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
