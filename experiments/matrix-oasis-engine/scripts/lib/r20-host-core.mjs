import { createServer } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { exportNpcAuthoritySession, submitNpcAuthorityIntent, verifyNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { selectNextNpcBehaviorCommand } from "@matrix-oasis/npc-behavior-runtime";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";

const MAX_BODY=32768;const states=new WeakMap();
export const R20_NPC_HOST="127.0.0.1";
export const R20_NPC_HOST_PORT=43120;
function frozen(value){if(!value||typeof value!=="object"||Object.isFrozen(value))return value;for(const child of Object.values(value))frozen(child);return Object.freeze(value)}
function response(statusCode,value){return frozen({statusCode,headers:frozen({"content-type":"application/json; charset=utf-8","cache-control":"no-store"}),body:canonicalizeJsonValue(value)})}
function sameToken(actual,expected){if(typeof actual!=="string"||!actual.startsWith("Bearer "))return false;const left=Buffer.from(actual.slice(7)),right=Buffer.from(expected);return left.length===right.length&&timingSafeEqual(left,right)}
function parseBody(request){if(request.method==="GET")return null;if(request.headers?.["content-type"]!=="application/json")throw new Error("content-type");if(typeof request.body!=="string"||Buffer.byteLength(request.body)>MAX_BODY)throw new Error("body");return JSON.parse(request.body)}
function exactObject(value,keys){return value&&typeof value==="object"&&!Array.isArray(value)&&Object.keys(value).sort().join("\0")===[...keys].sort().join("\0")}

export function createR20Coordinator({authoritySession,preparedBehavior,initialBehaviorState,sessionToken}){if(!authoritySession||!preparedBehavior||!initialBehaviorState||typeof sessionToken!=="string"||sessionToken.length<32)return null;const handle=Object.freeze(Object.create(null));states.set(handle,{authoritySession,preparedBehavior,behaviorState:structuredClone(initialBehaviorState),sessionToken,inFlight:null,frozen:false});return handle}
export function handleR20CoordinatorRequest(coordinator,request){
  try{
    const state=states.get(coordinator);if(!state)return response(500,{code:"R20_COORDINATOR_INVALID"});if(request?.remoteAddress!=="127.0.0.1")return response(403,{code:"R20_LOOPBACK_REQUIRED"});if(!sameToken(request.headers?.authorization,state.sessionToken))return response(401,{code:"R20_SESSION_TOKEN_INVALID"});const body=parseBody(request);
    if(request.method==="GET"&&request.url==="/v1/command"){
      if(state.frozen)return response(409,{code:"R20_TIMELINE_FROZEN"});if(state.inFlight)return response(200,{status:"command",command:state.inFlight.command});const exported=exportNpcAuthoritySession(state.authoritySession);if(!exported.ok)return response(500,{code:"R20_AUTHORITY_EXPORT_FAILED"});const selected=selectNextNpcBehaviorCommand({prepared:state.preparedBehavior,runtimeSnapshot:exported.runtimeSnapshot,runtimeInspection:exported.inspection,worldEventLedgerJson:exported.canonicalWorldEventLedgerJson,behaviorState:state.behaviorState});if(!selected.ok)return response(409,{code:selected.diagnostics[0].code});if(selected.status!=="command")return response(200,{status:selected.status});state.inFlight={command:selected.command,nextBehaviorState:selected.nextBehaviorState,phase:"moving",beforeSnapshotSha256:hashCanonicalValue(exported.runtimeSnapshot)};return response(200,{status:"command",command:selected.command});
    }
    if(request.method==="POST"&&request.url==="/v1/arrived"){
      if(!exactObject(body,["sequence","pathComplete","floorVerified","capsuleVerified","domainVerified","movementTicks","pathLengthMm"]))return response(400,{code:"R20_ARRIVAL_BODY_INVALID"});const flight=state.inFlight;if(!flight||flight.command.sequence!==body.sequence||flight.phase!=="moving")return response(409,{code:"R20_COMMAND_SEQUENCE_INVALID"});if(body.pathComplete!==true||body.floorVerified!==true||body.capsuleVerified!==true||body.domainVerified!==true||!Number.isSafeInteger(body.movementTicks)||body.movementTicks<0||body.movementTicks>1800||!Number.isSafeInteger(body.pathLengthMm)||body.pathLengthMm<0||body.pathLengthMm>100000)return response(409,{code:"R20_ARRIVAL_EVIDENCE_INVALID"});const result=submitNpcAuthorityIntent(state.authoritySession,flight.command.npcIntentJson);if(!result.ok){state.frozen=true;return response(409,{code:result.diagnostics[0].code})}const document=JSON.parse(result.canonicalAdjudicationResultJson);flight.phase="mirroring";flight.afterSnapshotSha256=document.afterSnapshotSha256;flight.adjudicationResult=document;flight.movementTicks=body.movementTicks;flight.pathLengthMm=body.pathLengthMm;return response(200,{status:"adjudicated",decision:document.decision,beforeSnapshotSha256:document.beforeSnapshotSha256,afterSnapshotSha256:document.afterSnapshotSha256});
    }
    if(request.method==="POST"&&request.url==="/v1/mirror"){
      if(!exactObject(body,["sequence","beforeSnapshotSha256","afterSnapshotSha256"]))return response(400,{code:"R20_MIRROR_BODY_INVALID"});const flight=state.inFlight;if(!flight||flight.command.sequence!==body.sequence||flight.phase!=="mirroring")return response(409,{code:"R20_COMMAND_SEQUENCE_INVALID"});if(body.beforeSnapshotSha256!==flight.beforeSnapshotSha256||body.afterSnapshotSha256!==flight.afterSnapshotSha256){state.frozen=true;return response(409,{code:"R20_RUNTIME_MIRROR_MISMATCH"})}state.behaviorState=flight.nextBehaviorState;state.inFlight=null;return response(200,{status:"committed",disposition:"returning"});
    }
    if(request.method==="POST"&&request.url==="/v1/verify"){
      if(!exactObject(body,[]))return response(400,{code:"R20_VERIFY_BODY_INVALID"});const verified=verifyNpcAuthoritySession(state.authoritySession);if(!verified.ok){state.frozen=true;return response(409,{code:"R20_AUTHORITY_VERIFY_FAILED"})}const report=JSON.parse(verified.canonicalWorldEventLedgerReplayReportJson);return response(200,{status:"verified",revision:report.verifiedEntries,headSha256:report.throughHeadSha256});
    }
    return response(404,{code:"R20_ROUTE_NOT_FOUND"});
  }catch{return response(400,{code:"R20_REQUEST_INVALID"})}
}

export function startR20LoopbackCoordinator({coordinator,port=R20_NPC_HOST_PORT}){if(port!==R20_NPC_HOST_PORT)return Promise.reject(new Error("R20_COORDINATOR_PORT_INVALID"));return new Promise((resolve,reject)=>{const server=createServer((request,out)=>{const chunks=[];let length=0;request.on("data",(chunk)=>{length+=chunk.length;if(length<=MAX_BODY)chunks.push(chunk)});request.on("end",()=>{const result=handleR20CoordinatorRequest(coordinator,{remoteAddress:request.socket.remoteAddress==="::ffff:127.0.0.1"?R20_NPC_HOST:request.socket.remoteAddress,method:request.method,url:request.url,headers:request.headers,body:length<=MAX_BODY?Buffer.concat(chunks).toString("utf8"):"x".repeat(MAX_BODY+1)});out.writeHead(result.statusCode,result.headers);out.end(result.body)})});server.once("error",reject);server.listen(R20_NPC_HOST_PORT,R20_NPC_HOST,()=>resolve(frozen({host:R20_NPC_HOST,port:R20_NPC_HOST_PORT,close:()=>new Promise((done)=>server.close(done))})))})}
