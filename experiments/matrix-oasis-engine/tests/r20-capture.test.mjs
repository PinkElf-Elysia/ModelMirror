import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { lstat, mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { createNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { prepareDeterministicNpcBehavior, synthesizeNpcBehaviorPolicy } from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  R20_CAPTURE_ALLOWED_FILES,
  runR20Capture,
  verifyR20Capture,
} from "../scripts/capture-r20.mjs";
import { buildR20BridgeArtifacts } from "../scripts/lib/r20-cli-core.mjs";
import { createR20Coordinator, exportR20Coordinator, handleR20CoordinatorRequestAsync } from "../scripts/lib/r20-host-core.mjs";
import { deriveR20QualificationCoverageRequirement, evaluateR20QualificationCoverage } from "../scripts/lib/r20-qualification-coverage.mjs";

const temporaryRoot=path.resolve(path.parse(fileURLToPath(import.meta.url)).root,"tmp");
const sha=value=>`sha256:${createHash("sha256").update(value).digest("hex")}`;
const fake=character=>`sha256:${character.repeat(64)}`;
const canonical=value=>canonicalizeJsonValue(value);
const authoring=canonical({format:"matrix-oasis.authoring-game-pack",formatVersion:"0.1.0",id:"r20-capture-coverage",contentVersion:"1",language:"en",title:"Capture coverage",summary:"One action ending coverage fixture.",entryNodeId:"node-start",entities:[{id:"actor-unit",label:"Actor"},{id:"control-unit",label:"Control"}],variables:[],cues:[],nodes:[{id:"node-start",title:"Start",text:"Finish.",entityIds:["actor-unit","control-unit"],entryCueIds:[],actions:[{id:"action-initialize",label:"Finish",entityIds:["control-unit"],effects:[],target:{kind:"ending",id:"ending-pass"}}]}],endings:[{id:"ending-pass",title:"Pass",text:"Done.",cueIds:[]}]});
const compiled=await compileAuthoringGamePackJson(authoring),runtimeGamePackJson=compiled.canonicalJson,runtimeReceiptJson=canonical(compiled.receipt),coverage=deriveR20QualificationCoverageRequirement(runtimeGamePackJson),sessionToken="c".repeat(64);
const authorityPolicyJson=canonical({format:"matrix-oasis.npc-authority-policy",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",id:"r20-capture-policy",contentVersion:"1",runtime:{format:compiled.runtimePack.format,formatVersion:compiled.runtimePack.formatVersion,id:compiled.runtimePack.source.id,contentVersion:compiled.runtimePack.source.contentVersion,sourceSha256:`sha256:${compiled.runtimePack.source.canonicalSha256}`,artifactSha256:`sha256:${compiled.receipt.artifact.sha256}`,receiptSha256:hashCanonicalValue(compiled.receipt)},actorGrants:[{actorEntityId:"actor-unit",grants:[{nodeId:"node-start",actionId:"action-initialize"}]}]});
const behavior=synthesizeNpcBehaviorPolicy({authorityPolicyJson});
const coordinatorRequest=(method,url,body)=>({remoteAddress:"127.0.0.1",method,url,headers:{authorization:`Bearer ${sessionToken}`,...(method==="POST"?{"content-type":"application/json"}:{})},...(body===undefined?{}:{body:canonical(body)})});

async function absent(file){try{await lstat(file);return false}catch(error){if(error?.code==="ENOENT")return true;throw error}}

async function fixture(t,label,options={}){
  const token=randomBytes(10).toString("hex"),npcRunRoot=path.join(temporaryRoot,`r20-capture-${label}-${token}-npc`),output=path.join(temporaryRoot,`r20-capture-${label}-${token}-output`),timelineId=`timeline-${label}`,godotBinary={sha256:fake("b"),byteLength:4096};
  const behaviorPolicyJson=behavior.canonicalNpcBehaviorPolicyJson,entityBindingJson=canonical({format:"matrix-oasis.npc-entity-binding",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",identities:{sceneBlueprintSha256:fake("1"),scenePackSha256:fake("2"),assetBundleSha256:fake("3"),spatialSolutionSha256:fake("4"),spatialVerificationSha256:fake("5"),authorityPolicySha256:behavior.npcBehaviorPolicy.authorityPolicySha256},bindings:[{actorEntityId:"actor-unit",assetBriefId:"brief-one",placementId:"placement-one",runtimeEntityId:"actor-unit",homeFloorAnchorId:"floor-one",homePositionMm:{x:0,y:0,z:0},visibleNodeIds:["node-start"]}]}),entityBindingSha256=sha(entityBindingJson);
  const authoritySession=await createNpcAuthoritySession({runtimeGamePackJson,runtimeReceiptJson,policyJson:authorityPolicyJson,timelineId}),prepared=prepareDeterministicNpcBehavior({behaviorPolicyJson,entityBindingJson,authorityPolicyJson}),coordinator=createR20Coordinator({authoritySession:authoritySession.session,preparedBehavior:prepared.prepared,initialBehaviorState:prepared.initialState,entityBindingSha256,sessionToken,onCommit:()=>{}});
  const command=JSON.parse((await handleR20CoordinatorRequestAsync(coordinator,coordinatorRequest("GET","/v1/command"))).body).command,arrival={sequence:command.sequence,pathComplete:true,floorVerified:true,capsuleVerified:true,domainVerified:true,movementTicks:3,pathLengthMm:150},verdict=JSON.parse((await handleR20CoordinatorRequestAsync(coordinator,coordinatorRequest("POST","/v1/arrived",arrival))).body);
  await handleR20CoordinatorRequestAsync(coordinator,coordinatorRequest("POST","/v1/mirror",{sequence:command.sequence,beforeSnapshotSha256:verdict.beforeSnapshotSha256,afterSnapshotSha256:verdict.afterSnapshotSha256}));
  const snapshot=exportR20Coordinator(coordinator),godotEvents=snapshot.commands.flatMap(item=>[{sequence:item.sequence,actorEntityId:item.actorEntityId,actionId:item.actionId,state:"arrived",arrivalEvidence:item.arrivalEvidence},{sequence:item.sequence,actorEntityId:item.actorEntityId,actionId:item.actionId,state:"mirrored",decision:item.state,beforeSnapshotSha256:item.mirrorEvidence.beforeSnapshotSha256,afterSnapshotSha256:item.mirrorEvidence.afterSnapshotSha256}]),godotTraceJson=canonical({traceVersion:1,entityBindingSha256,renderer:"forward_plus",navigationSynchronized:true,eventCount:godotEvents.length,eventsSha256:hashCanonicalValue(godotEvents),performance:{sampleCount:300,medianFrameMicros:10_000,medianFpsMilli:100_000}}),artifacts=buildR20BridgeArtifacts({snapshot,behaviorPolicyJson,entityBindingJson,godotTraceJson}),ledgerJson=snapshot.authority.canonicalWorldEventLedgerJson,ledger=JSON.parse(ledgerJson),traceJson=artifacts.canonicalBehaviorTraceJson,bridgeReportJson=artifacts.canonicalBridgeReportJson,revision=ledger.revision,headSha256=ledger.headSha256;
  const implementationManifestJson=canonical({format:"matrix-oasis.r20-implementation-manifest",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",files:[{path:"scripts/qualify-r20-npc-bridge.mjs",byteLength:1,sha256:fake("c")}]});
  const implementation={manifestJson:implementationManifestJson,sha256:sha(implementationManifestJson)};
  const authorityManifestJson=canonical({format:"matrix-oasis.npc-authority-manifest",formatVersion:"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",timelineId,qualificationCoverage:coverage.requirement,identities:{behaviorPolicySha256:sha(behaviorPolicyJson),entityBindingSha256,implementationSha256:implementation.sha256,godotBinarySha256:godotBinary.sha256,runtimePackSha256:coverage.requirement.runtimePackSha256}}),manifestSha256=sha(authorityManifestJson),manifestId=manifestSha256.slice(7);
  const checkpointJson=canonical({format:"matrix-oasis.npc-authority-checkpoint",formatVersion:"0.1.0",timelineId,manifestSha256,revision,headSha256,commit:"00001-fixture",godotTraceSha256:sha(godotTraceJson),bridgeReportSha256:sha(bridgeReportJson)}),processLogUtf8="R20 fixture qualified\n",runtimeProjectManifestJson=canonical({format:"matrix-oasis.r20-runtime-project-manifest",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",files:[{path:"project.godot",byteLength:1,sha256:fake("d")} ]});
  const evaluatedCoverage=evaluateR20QualificationCoverage({requirement:coverage.requirement,worldEventLedgerJson:ledgerJson,behaviorTraceJson:traceJson}).evidence,qualificationCoverage=options.transformCoverage?options.transformCoverage(structuredClone(evaluatedCoverage)):evaluatedCoverage,receiptValue={format:"matrix-oasis.r20-qualification-receipt",formatVersion:options.receiptVersion??"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",manifestSha256,timelineId,revision,headSha256,godotTraceSha256:sha(godotTraceJson),bridgeReportSha256:sha(bridgeReportJson),godotVersion:"4.6.3",renderer:"forward_plus",processExitCode:0,processLogSha256:sha(Buffer.from(processLogUtf8)),implementationSha256:implementation.sha256,godotBinarySha256:godotBinary.sha256,runtimeProjectSha256:sha(runtimeProjectManifestJson)};if(receiptValue.formatVersion==="0.2.0")receiptValue.qualificationCoverage=qualificationCoverage;const receiptJson=canonical(receiptValue),qualificationReceiptSha256=sha(receiptJson);
  const currentValue={format:"matrix-oasis.npc-current",formatVersion:"0.1.0",manifestSha256,timelineId,revision,headSha256,qualificationReceiptSha256},currentJson=canonical(currentValue),activationJson=canonical({format:"matrix-oasis.r20-qualification-activation",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",manifestSha256,timelineId,qualificationReceiptSha256,previousCurrentSha256:null}),activatedJson=canonical({format:"matrix-oasis.r20-qualification-activated",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",manifestSha256,timelineId,qualificationReceiptSha256,currentSha256:sha(currentJson)});
  const evidenceJson=canonical({format:"matrix-oasis.r20-qualification-evidence",formatVersion:"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",processLogUtf8,processLogSha256:sha(Buffer.from(processLogUtf8)),runtimeProjectManifestJson,runtimeProjectSha256:sha(runtimeProjectManifestJson),qualificationReceiptJson:receiptJson,qualificationReceiptSha256,activationJson,activatedJson,runtimeGamePackJson,runtimeGamePackSha256:sha(runtimeGamePackJson),runtimeReceiptJson,runtimeReceiptSha256:sha(runtimeReceiptJson),authorityPolicyJson,authorityPolicySha256:sha(authorityPolicyJson)});
  const timelineRoot=path.join(npcRunRoot,"timelines",manifestId);await mkdir(timelineRoot,{recursive:true});
  const files={"authority-manifest.json":authorityManifestJson,"behavior-policy.json":behaviorPolicyJson,"entity-bindings.json":entityBindingJson,"checkpoint.json":checkpointJson,"world-event-ledger.json":ledgerJson,"behavior-trace.json":traceJson,"godot-trace.json":godotTraceJson,"bridge-report.json":bridgeReportJson,"qualification-evidence.json":evidenceJson};
  await Promise.all(Object.entries(files).map(([name,value])=>writeFile(path.join(timelineRoot,name),value,{flag:"wx"})));await writeFile(path.join(npcRunRoot,"npc-current.json"),currentJson,{flag:"wx"});
  const audit={ok:true,current:currentValue,pendingCurrent:null,timelines:[{manifestId,timelineId,commitCount:1,revision,headSha256,qualified:true,status:"qualified",implementationSha256:implementation.sha256,godotBinarySha256:godotBinary.sha256,qualificationReceiptSha256}]};
  let leaseActive=false;const events=[];
  const operations={
    async acquireWriterLease(){assert.equal(leaseActive,false);leaseActive=true;events.push("acquire");return Object.freeze({fixture:true})},
    async releaseWriterLease(){assert.equal(leaseActive,true);events.push("release");leaseActive=false},
    async auditTimelineStore(){assert.equal(leaseActive,true);events.push("audit");return audit},
    async calculateImplementationIdentity(){return implementation},
    async calculateGodotBinaryIdentity(){return godotBinary},
  };
  t.after(async()=>{await rm(npcRunRoot,{recursive:true,force:true});await rm(output,{recursive:true,force:true})});
  return {npcRunRoot,output,timelineRoot,currentValue,implementation,godotBinary,audit,events,operations,isLeaseActive:()=>leaseActive};
}

function argumentsFor(value){return ["--npc-run-root",value.npcRunRoot,"--output",value.output]}

async function rewriteSelfConsistentCapture(root,mutate){
  const documents=Object.create(null);
  for(const name of R20_CAPTURE_ALLOWED_FILES)documents[name]=JSON.parse(await readFile(path.join(root,name),"utf8"));
  mutate(documents);
  const authority=documents["authority-manifest.json"],policyJson=canonical(documents["behavior-policy.json"]),bindingJson=canonical(documents["entity-bindings.json"]),policySha256=sha(policyJson),bindingSha256=sha(bindingJson),trace=documents["behavior-trace.json"],ledger=documents["world-event-ledger.json"],ledgerJson=canonical(ledger);
  authority.identities.behaviorPolicySha256=policySha256;authority.identities.entityBindingSha256=bindingSha256;
  trace.behaviorPolicySha256=policySha256;trace.entityBindingSha256=bindingSha256;trace.finalRevision=ledger.revision;trace.finalHeadSha256=ledger.headSha256;
  for(const command of trace.commands){
    command.mirrorEvidence.entityBindingSha256=bindingSha256;
    command.mirrorEvidence.commandSha256=hashCanonicalValue({sequence:command.sequence,actorEntityId:command.actorEntityId,ruleIndex:command.ruleIndex,intentId:command.intentId,nodeId:command.nodeId,actionId:command.actionId});
  }
  const traceJson=canonical(trace),godotTrace=documents["godot-trace.json"];
  if(godotTrace&&typeof godotTrace==="object"&&!Array.isArray(godotTrace)&&Object.hasOwn(godotTrace,"entityBindingSha256"))godotTrace.entityBindingSha256=bindingSha256;
  const godotTraceJson=canonical(godotTrace),bridge=documents["bridge-report.json"];
  if(bridge&&typeof bridge==="object"&&!Array.isArray(bridge)){
    if(Object.hasOwn(bridge,"worldEventLedgerSha256"))bridge.worldEventLedgerSha256=sha(ledgerJson);
    if(Object.hasOwn(bridge,"behaviorTraceSha256"))bridge.behaviorTraceSha256=sha(traceJson);
  }
  const bridgeReportJson=canonical(bridge),authorityManifestJson=canonical(authority),manifestSha256=sha(authorityManifestJson),checkpoint=documents["checkpoint.json"];
  checkpoint.manifestSha256=manifestSha256;checkpoint.revision=ledger.revision;checkpoint.headSha256=ledger.headSha256;checkpoint.godotTraceSha256=sha(godotTraceJson);checkpoint.bridgeReportSha256=sha(bridgeReportJson);
  const evidence=documents["qualification-evidence.json"],receipt=JSON.parse(evidence.qualificationReceiptJson),activation=JSON.parse(evidence.activationJson),activated=JSON.parse(evidence.activatedJson);
  receipt.manifestSha256=manifestSha256;receipt.revision=ledger.revision;receipt.headSha256=ledger.headSha256;receipt.godotTraceSha256=checkpoint.godotTraceSha256;receipt.bridgeReportSha256=checkpoint.bridgeReportSha256;
  const receiptJson=canonical(receipt),qualificationReceiptSha256=sha(receiptJson),current=documents["capture-manifest.json"].current;
  current.manifestSha256=manifestSha256;current.revision=ledger.revision;current.headSha256=ledger.headSha256;current.qualificationReceiptSha256=qualificationReceiptSha256;
  activation.manifestSha256=manifestSha256;activation.qualificationReceiptSha256=qualificationReceiptSha256;
  activated.manifestSha256=manifestSha256;activated.qualificationReceiptSha256=qualificationReceiptSha256;activated.currentSha256=sha(canonical(current));
  evidence.qualificationReceiptJson=receiptJson;evidence.qualificationReceiptSha256=qualificationReceiptSha256;evidence.activationJson=canonical(activation);evidence.activatedJson=canonical(activated);
  const capture=documents["capture-manifest.json"];
  capture.audit.current=structuredClone(current);
  for(const summary of capture.audit.timelines)if(summary.timelineId===current.timelineId){summary.manifestId=manifestSha256.slice(7);summary.revision=ledger.revision;summary.headSha256=ledger.headSha256;summary.qualificationReceiptSha256=qualificationReceiptSha256}
  const payloadTexts={
    "authority-manifest.json":authorityManifestJson,
    "behavior-policy.json":policyJson,
    "entity-bindings.json":bindingJson,
    "checkpoint.json":canonical(checkpoint),
    "world-event-ledger.json":ledgerJson,
    "behavior-trace.json":traceJson,
    "godot-trace.json":godotTraceJson,
    "bridge-report.json":bridgeReportJson,
    "qualification-evidence.json":canonical(evidence),
    "implementation-manifest.json":canonical(documents["implementation-manifest.json"]),
    "godot-binary-identity.json":canonical(documents["godot-binary-identity.json"]),
  };
  for(const [name,value] of Object.entries(payloadTexts)){capture.hashes[name]=sha(value);await writeFile(path.join(root,name),value)}
  await writeFile(path.join(root,"capture-manifest.json"),canonical(capture));
}

test("capture publishes one self-verifying bundle and supports offline verification",async t=>{
  const value=await fixture(t,"valid"),result=await runR20Capture(argumentsFor(value),value.operations);assert.equal(result.manifestSha256,value.currentValue.manifestSha256);assert.equal(value.isLeaseActive(),false);assert.deepEqual((await readdir(value.output)).sort(),R20_CAPTURE_ALLOWED_FILES);
  const verified=await verifyR20Capture({captureRoot:value.output});assert.equal(verified.ok,true);assert.equal(verified.liveVerified,false);
  const capture=JSON.parse(await readFile(path.join(value.output,"capture-manifest.json"),"utf8")),scriptBytes=await readFile(new URL("../scripts/capture-r20.mjs",import.meta.url));assert.equal(capture.captureScript.sha256,sha(scriptBytes));assert.equal(capture.captureScript.byteLength,scriptBytes.byteLength);assert.equal(Object.hasOwn(capture.hashes,"qualification-evidence.json"),true);assert.equal(Object.hasOwn(capture.hashes,"qualification-receipt.json"),false);
});

test("capture refuses an existing output without replacing it",async t=>{
  const value=await fixture(t,"exists");await mkdir(value.output);await writeFile(path.join(value.output,"sentinel.txt"),"owned");await assert.rejects(runR20Capture(argumentsFor(value),value.operations),/R20_CAPTURE_OUTPUT_EXISTS/u);assert.equal(await readFile(path.join(value.output,"sentinel.txt"),"utf8"),"owned");assert.deepEqual(value.events,[]);
});

test("offline verification rejects a tampered captured file",async t=>{
  const value=await fixture(t,"tamper");await runR20Capture(argumentsFor(value),value.operations);const unexpected=path.join(value.output,"unexpected.json");await writeFile(unexpected,"{}");await assert.rejects(verifyR20Capture({captureRoot:value.output}),/R20_CAPTURE_CONTENT_INVALID/u);await rm(unexpected);await writeFile(path.join(value.output,"behavior-policy.json"),canonical({format:"tampered"}));await assert.rejects(verifyR20Capture({captureRoot:value.output}),/R20_CAPTURE_FILE_HASH_INVALID/u);
});

test("offline verification rejects self-consistently rehashed invalid Godot and bridge evidence",async t=>{
  for(const [label,mutate] of [
    ["empty-godot",documents=>{documents["godot-trace.json"]={}}],
    ["partial-bridge",documents=>{const bridge=documents["bridge-report.json"];documents["bridge-report.json"]={timelineId:bridge.timelineId,worldEventLedgerSha256:bridge.worldEventLedgerSha256,behaviorTraceSha256:bridge.behaviorTraceSha256}}],
  ]){
    const value=await fixture(t,label);await runR20Capture(argumentsFor(value),value.operations);await rewriteSelfConsistentCapture(value.output,mutate);
    await assert.rejects(verifyR20Capture({captureRoot:value.output}),/R20_(?:GODOT_TRACE_INVALID|STORE_ARTIFACT_INVALID)/u);
  }
});

test("offline verification rejects self-consistently rehashed invalid static artifacts",async t=>{
  for(const [label,mutate] of [
    ["empty-policy",documents=>{documents["behavior-policy.json"]={}}],
    ["empty-bindings",documents=>{documents["entity-bindings.json"]={}}],
  ]){
    const value=await fixture(t,label);await runR20Capture(argumentsFor(value),value.operations);await rewriteSelfConsistentCapture(value.output,mutate);
    await assert.rejects(verifyR20Capture({captureRoot:value.output}),/R20_STORE_(?:POLICY|BINDINGS)_INVALID/u);
  }
});

test("offline verification rejects a self-consistently rehashed but semantically forged Ledger",async t=>{
  const value=await fixture(t,"semantic-ledger");await runR20Capture(argumentsFor(value),value.operations);
  await rewriteSelfConsistentCapture(value.output,documents=>{
    const ledger=documents["world-event-ledger.json"],entry=ledger.entries[0];entry.transition.from.index+=1;
    const {entrySha256:ignored,...body}=entry;entry.entrySha256=hashCanonicalValue(body);ledger.headSha256=entry.entrySha256;
  });
  await assert.rejects(verifyR20Capture({captureRoot:value.output}),/R20_CAPTURE_QUALIFICATION_REPLAY_INVALID/u);
});

test("offline verification rejects legacy qualification evidence even when the bundle is rehashed",async t=>{
  const value=await fixture(t,"legacy-evidence");await runR20Capture(argumentsFor(value),value.operations);
  await rewriteSelfConsistentCapture(value.output,documents=>{
    const evidence=documents["qualification-evidence.json"];evidence.formatVersion="0.1.0";
    for(const key of ["runtimeGamePackJson","runtimeGamePackSha256","runtimeReceiptJson","runtimeReceiptSha256","authorityPolicyJson","authorityPolicySha256"])delete evidence[key];
  });
  await assert.rejects(verifyR20Capture({captureRoot:value.output}),/R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID/u);
});

test("offline verification rejects a coherently re-signed command that does not map to its policy rule",async t=>{
  const value=await fixture(t,"forged-rule");await runR20Capture(argumentsFor(value),value.operations);
  await rewriteSelfConsistentCapture(value.output,documents=>{documents["behavior-trace.json"].commands[0].ruleIndex=1});
  await assert.rejects(verifyR20Capture({captureRoot:value.output}),/R20_CAPTURE_AUTHORITY_CHAIN_INVALID/u);
});

test("capture rejects structurally valid coverage evidence that was not derived from its timeline",async t=>{
  const value=await fixture(t,"forged-coverage",{transformCoverage(evidence){evidence.endingRevision+=1;return evidence}});
  await assert.rejects(runR20Capture(argumentsFor(value),value.operations),/R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID/u);assert.equal(await absent(value.output),true);assert.equal(value.isLeaseActive(),false);
});

test("capture rejects legacy qualification receipts as current evidence",async t=>{
  const value=await fixture(t,"legacy-receipt",{receiptVersion:"0.1.0"});
  await assert.rejects(runR20Capture(argumentsFor(value),value.operations),/R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID/u);assert.equal(await absent(value.output),true);assert.equal(value.isLeaseActive(),false);
});

test("mid-copy failure releases last and leaves no published output",async t=>{
  const value=await fixture(t,"copy-failure"),events=value.events;let writes=0;const operations={...value.operations,async writeFile(...args){assert.equal(value.isLeaseActive(),true);events.push("write");writes+=1;if(writes===4)throw new Error("SIMULATED_COPY_FAILURE");return writeFile(...args)}};
  await assert.rejects(runR20Capture(argumentsFor(value),operations),/SIMULATED_COPY_FAILURE/u);assert.equal(await absent(value.output),true);assert.equal(value.isLeaseActive(),false);assert.equal(events.at(-1),"release");
});

test("a post-effect rename error recovers only the exact verified bundle",async t=>{
  const value=await fixture(t,"rename-failure"),events=value.events;const operations={...value.operations,async rename(from,to){assert.equal(value.isLeaseActive(),true);events.push("rename");await rename(from,to);throw new Error("SIMULATED_RENAME_FAILURE")}};
  const result=await runR20Capture(argumentsFor(value),operations);assert.equal(result.manifestSha256,value.currentValue.manifestSha256);assert.equal((await verifyR20Capture({captureRoot:value.output})).ok,true);assert.equal(value.isLeaseActive(),false);assert.deepEqual(events.slice(-2),["rename","release"]);
});

test("a pre-effect rename conflict preserves the competing output",async t=>{
  const value=await fixture(t,"rename-competitor"),events=value.events;const operations={...value.operations,async rename(_from,to){assert.equal(value.isLeaseActive(),true);events.push("rename");await mkdir(to);await writeFile(path.join(to,"sentinel.txt"),"competing-output");const error=new Error("SIMULATED_RENAME_CONFLICT");error.code="EEXIST";throw error}};
  await assert.rejects(runR20Capture(argumentsFor(value),operations),/SIMULATED_RENAME_CONFLICT/u);assert.equal(await readFile(path.join(value.output,"sentinel.txt"),"utf8"),"competing-output");assert.equal(value.isLeaseActive(),false);assert.deepEqual(events.slice(-2),["rename","release"]);
});

test("current source reads, rename, and read-back verification all precede lease release",async t=>{
  const value=await fixture(t,"ordering"),events=value.events;const operations={...value.operations,
    async readStableFile(file,...rest){assert.equal(value.isLeaseActive(),true);if(path.resolve(file)===path.resolve(path.join(value.npcRunRoot,"npc-current.json")))events.push("current-read");return (await import("../scripts/lib/r20-cli-core.mjs")).readStableR20File(file,...rest)},
    async rename(from,to){assert.equal(value.isLeaseActive(),true);events.push("rename");return rename(from,to)},
    async verifyCapture(request,allOperations){assert.equal(value.isLeaseActive(),true);events.push("verify");return verifyR20Capture(request,allOperations)},
  };
  await runR20Capture(argumentsFor(value),operations);assert.equal(value.isLeaseActive(),false);for(const event of ["audit","current-read","rename","verify"])assert.ok(events.indexOf(event)>events.indexOf("acquire"));const verifyIndices=events.flatMap((event,index)=>event==="verify"?[index]:[]);assert.equal(verifyIndices.length,2);assert.ok(events.indexOf("current-read")<verifyIndices[0]);assert.ok(verifyIndices[0]<events.indexOf("rename"));assert.ok(events.indexOf("rename")<verifyIndices[1]);assert.ok(verifyIndices[1]<events.indexOf("release"));
  const source=await readFile(new URL("../scripts/capture-r20.mjs",import.meta.url),"utf8"),renameAt=source.indexOf("await operations.rename(stage,selected.output)"),firstVerifyAt=source.indexOf("await operations.verifyCapture"),lastVerifyAt=source.lastIndexOf("await operations.verifyCapture"),releaseAt=source.indexOf("await operations.releaseWriterLease(writerLease)");assert.ok(firstVerifyAt>=0&&firstVerifyAt<renameAt&&renameAt<lastVerifyAt&&lastVerifyAt<releaseAt);
});
