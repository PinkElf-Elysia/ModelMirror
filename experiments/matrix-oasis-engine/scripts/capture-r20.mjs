import { createHash } from "node:crypto";
import { lstat, mkdtemp, readdir, realpath, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { acquireR20WriterLease, auditR20TimelineStore, readStableR20File, releaseR20WriterLease, validateR20BridgeReport, validateR20GodotTraceJson, validateR20QualificationEvidenceJson, validateR20StaticArtifacts, validateR20TraceStaticSemantics, verifyR20QualificationRuntimeEvidence } from "./lib/r20-cli-core.mjs";
import { evaluateR20QualificationCoverage, validateR20QualificationCoverageEvidence, validateR20QualificationCoverageRequirement } from "./lib/r20-qualification-coverage.mjs";
import { calculateR20GodotBinaryIdentity, calculateR20ImplementationIdentity } from "./qualify-r20-npc-bridge.mjs";
const captureScriptFile=fileURLToPath(import.meta.url),moduleRoot=path.resolve(fileURLToPath(new URL("..",import.meta.url))),temporaryRoot=path.resolve(path.parse(captureScriptFile).root,"tmp"),SHA=/^sha256:[0-9a-f]{64}$/u;
const storeFiles=Object.freeze(["authority-manifest.json","behavior-policy.json","entity-bindings.json","checkpoint.json","world-event-ledger.json","behavior-trace.json","godot-trace.json","bridge-report.json","qualification-evidence.json"]);
const identityFiles=Object.freeze(["implementation-manifest.json","godot-binary-identity.json"]);
export const R20_CAPTURE_PAYLOAD_FILES=Object.freeze([...storeFiles,...identityFiles]);
export const R20_CAPTURE_ALLOWED_FILES=Object.freeze([...R20_CAPTURE_PAYLOAD_FILES,"capture-manifest.json"].sort());
const defaults=Object.freeze({acquireWriterLease:acquireR20WriterLease,auditTimelineStore:auditR20TimelineStore,calculateGodotBinaryIdentity:calculateR20GodotBinaryIdentity,calculateImplementationIdentity:calculateR20ImplementationIdentity,lstat,mkdtemp,readStableFile:readStableR20File,readdir,realpath,releaseWriterLease:releaseR20WriterLease,rename,rm,verifyCapture:verifyR20Capture,writeFile});
function ops(overrides={}){if(!overrides||typeof overrides!=="object"||Array.isArray(overrides))throw new Error("R20_CAPTURE_OPERATIONS_INVALID");return Object.freeze({...defaults,...overrides})}
function fail(code){const error=new Error(code);error.code=code;throw error}
const sha=(value)=>`sha256:${createHash("sha256").update(value).digest("hex")}`;
const exact=(value,keys)=>value&&typeof value==="object"&&!Array.isArray(value)&&Object.keys(value).sort().join("\0")===[...keys].sort().join("\0");
function canonicalText(text,code){if(typeof text!=="string")fail(code);let value;try{value=JSON.parse(text)}catch{fail(code)}if(canonicalizeJsonValue(value)!==text)fail(code);return value}
function text(bytes,code){try{return new TextDecoder("utf-8",{fatal:true}).decode(bytes)}catch{fail(code)}}
function canonicalBytes(bytes,code){return canonicalText(text(bytes,code),code)}
function requireSha(value,code){if(!SHA.test(value??""))fail(code)}
function same(left,right){return canonicalizeJsonValue(left)===canonicalizeJsonValue(right)}
function current(value){if(!exact(value,["format","formatVersion","manifestSha256","timelineId","revision","headSha256","qualificationReceiptSha256"])||value.format!=="matrix-oasis.npc-current"||value.formatVersion!=="0.1.0"||typeof value.timelineId!=="string"||value.timelineId.length<1||!Number.isSafeInteger(value.revision)||value.revision<0)fail("R20_CAPTURE_CURRENT_INVALID");requireSha(value.manifestSha256,"R20_CAPTURE_CURRENT_INVALID");requireSha(value.qualificationReceiptSha256,"R20_CAPTURE_CURRENT_INVALID");if(value.headSha256!==null)requireSha(value.headSha256,"R20_CAPTURE_CURRENT_INVALID");return value}
async function scriptIdentity(operations){const bytes=await operations.readStableFile(captureScriptFile,16*1024*1024);return Object.freeze({path:"scripts/capture-r20.mjs",sha256:sha(bytes),byteLength:bytes.byteLength})}
async function directory(root,operations){let stat;try{stat=await operations.lstat(root)}catch{fail("R20_CAPTURE_PATH_INVALID")}if(!stat.isDirectory()||stat.isSymbolicLink()||path.resolve(await operations.realpath(root))!==path.resolve(root))fail("R20_CAPTURE_PATH_INVALID")}
async function missing(output,operations){try{await operations.lstat(output)}catch(error){if(error?.code==="ENOENT")return;throw error}fail("R20_CAPTURE_OUTPUT_EXISTS")}

function captureManifest(value){
  if(!exact(value,["format","formatVersion","canonicalization","current","audit","implementationSha256","godotBinarySha256","captureScript","hashes"])||value.format!=="matrix-oasis.r20-capture"||value.formatVersion!=="0.1.0"||value.canonicalization!=="matrix-oasis.canonical-json/1"||!exact(value.captureScript,["path","sha256","byteLength"])||value.captureScript.path!=="scripts/capture-r20.mjs"||!Number.isSafeInteger(value.captureScript.byteLength)||value.captureScript.byteLength<1||!exact(value.hashes,R20_CAPTURE_PAYLOAD_FILES))fail("R20_CAPTURE_MANIFEST_INVALID");
  for(const hash of [value.implementationSha256,value.godotBinarySha256,value.captureScript.sha256,...Object.values(value.hashes)])requireSha(hash,"R20_CAPTURE_MANIFEST_INVALID");
  current(value.current);
  if(!exact(value.audit,["ok","current","pendingCurrent","timelines"])||value.audit.ok!==true||!Array.isArray(value.audit.timelines)||!same(value.current,value.audit.current))fail("R20_CAPTURE_AUDIT_INVALID");
  return value;
}

function implementationManifest(value){
  if(!exact(value,["format","formatVersion","canonicalization","files"])||value.format!=="matrix-oasis.r20-implementation-manifest"||value.formatVersion!=="0.1.0"||value.canonicalization!=="matrix-oasis.canonical-json/1"||!Array.isArray(value.files)||value.files.length<1)fail("R20_CAPTURE_IMPLEMENTATION_MANIFEST_INVALID");
  let previous="";
  for(const [index,file] of value.files.entries()){
    if(!exact(file,["path","byteLength","sha256"])||typeof file.path!=="string"||file.path.length<1||file.path.includes("\0")||file.path.includes("\\")||path.posix.isAbsolute(file.path)||path.posix.normalize(file.path)!==file.path||file.path.split("/").includes("..")||!Number.isSafeInteger(file.byteLength)||file.byteLength<0||index>0&&file.path.localeCompare(previous)<=0)fail("R20_CAPTURE_IMPLEMENTATION_MANIFEST_INVALID");
    requireSha(file.sha256,"R20_CAPTURE_IMPLEMENTATION_MANIFEST_INVALID");previous=file.path;
  }
}

function qualificationEvidence(text){
  try{return validateR20QualificationEvidenceJson(text)}catch{fail("R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID")}
}

function qualificationReceipt(value,requirement,expectedCoverageEvidence){
  if(!exact(value,["format","formatVersion","canonicalization","manifestSha256","timelineId","revision","headSha256","godotTraceSha256","bridgeReportSha256","godotVersion","renderer","processExitCode","processLogSha256","implementationSha256","godotBinarySha256","runtimeProjectSha256","qualificationCoverage"])||value.format!=="matrix-oasis.r20-qualification-receipt"||value.formatVersion!=="0.2.0"||value.canonicalization!=="matrix-oasis.canonical-json/1"||value.godotVersion!=="4.6.3"||value.renderer!=="forward_plus"||value.processExitCode!==0||typeof value.timelineId!=="string"||!Number.isSafeInteger(value.revision)||value.revision<0)fail("R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID");
  for(const hash of [value.manifestSha256,value.godotTraceSha256,value.bridgeReportSha256,value.processLogSha256,value.implementationSha256,value.godotBinarySha256,value.runtimeProjectSha256])requireSha(hash,"R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID");
  if(value.headSha256!==null)requireSha(value.headSha256,"R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID");
  let coverage;try{coverage=validateR20QualificationCoverageEvidence(value.qualificationCoverage,requirement)}catch{fail("R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID")}
  if(canonicalizeJsonValue(coverage)!==canonicalizeJsonValue(expectedCoverageEvidence))fail("R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID");
}

function activationDocuments(activation,activated){
  if(!exact(activation,["format","formatVersion","canonicalization","manifestSha256","timelineId","qualificationReceiptSha256","previousCurrentSha256"])||activation.format!=="matrix-oasis.r20-qualification-activation"||activation.formatVersion!=="0.1.0"||activation.canonicalization!=="matrix-oasis.canonical-json/1"||!exact(activated,["format","formatVersion","canonicalization","manifestSha256","timelineId","qualificationReceiptSha256","currentSha256"])||activated.format!=="matrix-oasis.r20-qualification-activated"||activated.formatVersion!=="0.1.0"||activated.canonicalization!=="matrix-oasis.canonical-json/1")fail("R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID");
  for(const hash of [activation.manifestSha256,activation.qualificationReceiptSha256,activated.manifestSha256,activated.qualificationReceiptSha256,activated.currentSha256])requireSha(hash,"R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID");
  if(activation.previousCurrentSha256!==null)requireSha(activation.previousCurrentSha256,"R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID");
}

async function verifyAuthority(capture,documents,texts){
  const selected=capture.current,authority=documents["authority-manifest.json"],checkpoint=documents["checkpoint.json"],ledger=documents["world-event-ledger.json"],trace=documents["behavior-trace.json"],bridge=documents["bridge-report.json"],godot=documents["godot-binary-identity.json"],manifestHash=capture.hashes["authority-manifest.json"];
  if(authority?.format!=="matrix-oasis.npc-authority-manifest"||authority?.formatVersion!=="0.2.0"||authority.canonicalization!=="matrix-oasis.canonical-json/1"||authority.timelineId!==selected.timelineId||selected.manifestSha256!==manifestHash||checkpoint?.manifestSha256!==manifestHash||checkpoint?.timelineId!==selected.timelineId||checkpoint?.revision!==selected.revision||checkpoint?.headSha256!==selected.headSha256||ledger?.timeline?.id!==selected.timelineId||ledger?.revision!==selected.revision||ledger?.headSha256!==selected.headSha256||trace?.timelineId!==selected.timelineId||trace?.finalRevision!==selected.revision||trace?.finalHeadSha256!==selected.headSha256)fail("R20_CAPTURE_AUTHORITY_CHAIN_INVALID");
  const staticArtifacts=validateR20StaticArtifacts(authority,texts["behavior-policy.json"],texts["entity-bindings.json"]);
  try{validateR20TraceStaticSemantics(trace,staticArtifacts)}catch{fail("R20_CAPTURE_AUTHORITY_CHAIN_INVALID")}
  let coverageRequirement,expectedCoverageEvidence;try{coverageRequirement=validateR20QualificationCoverageRequirement(authority.qualificationCoverage);expectedCoverageEvidence=evaluateR20QualificationCoverage({requirement:coverageRequirement,worldEventLedgerJson:texts["world-event-ledger.json"],behaviorTraceJson:texts["behavior-trace.json"]}).evidence}catch{fail("R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID")}
  validateR20GodotTraceJson(texts["godot-trace.json"],trace.commands,staticArtifacts.bindingSha256);
  validateR20BridgeReport({manifest:authority,ledgerText:texts["world-event-ledger.json"],traceText:texts["behavior-trace.json"],reportText:texts["bridge-report.json"]});
  if(!authority.identities||authority.identities.runtimePackSha256!==coverageRequirement.runtimePackSha256||staticArtifacts.policySha256!==capture.hashes["behavior-policy.json"]||staticArtifacts.bindingSha256!==capture.hashes["entity-bindings.json"]||authority.identities.implementationSha256!==capture.implementationSha256||authority.identities.godotBinarySha256!==capture.godotBinarySha256||trace.behaviorPolicySha256!==staticArtifacts.policySha256||trace.entityBindingSha256!==staticArtifacts.bindingSha256||checkpoint.godotTraceSha256!==capture.hashes["godot-trace.json"]||checkpoint.bridgeReportSha256!==capture.hashes["bridge-report.json"]||bridge.timelineId!==selected.timelineId||bridge.worldEventLedgerSha256!==capture.hashes["world-event-ledger.json"]||bridge.behaviorTraceSha256!==capture.hashes["behavior-trace.json"])fail("R20_CAPTURE_AUTHORITY_HASH_INVALID");

  implementationManifest(documents["implementation-manifest.json"]);
  if(sha(texts["implementation-manifest.json"])!==capture.implementationSha256)fail("R20_CAPTURE_IMPLEMENTATION_MANIFEST_INVALID");
  if(!exact(godot,["format","formatVersion","sha256","byteLength"])||godot.format!=="matrix-oasis.r20-godot-binary-identity"||godot.formatVersion!=="0.1.0"||godot.sha256!==capture.godotBinarySha256||!Number.isSafeInteger(godot.byteLength)||godot.byteLength<1)fail("R20_CAPTURE_GODOT_IDENTITY_INVALID");

  const evidence=qualificationEvidence(texts["qualification-evidence.json"]);
  const runtimeProject=canonicalText(evidence.runtimeProjectManifestJson,"R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID"),receipt=canonicalText(evidence.qualificationReceiptJson,"R20_CAPTURE_QUALIFICATION_RECEIPT_INVALID"),activation=canonicalText(evidence.activationJson,"R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID"),activated=canonicalText(evidence.activatedJson,"R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID");
  if(!exact(runtimeProject,["format","formatVersion","canonicalization","files"])||runtimeProject.format!=="matrix-oasis.r20-runtime-project-manifest"||runtimeProject.formatVersion!=="0.1.0"||runtimeProject.canonicalization!=="matrix-oasis.canonical-json/1"||!Array.isArray(runtimeProject.files)||runtimeProject.files.length<1)fail("R20_CAPTURE_QUALIFICATION_EVIDENCE_INVALID");
  qualificationReceipt(receipt,coverageRequirement,expectedCoverageEvidence);activationDocuments(activation,activated);
  const receiptHash=sha(evidence.qualificationReceiptJson),currentJson=canonicalizeJsonValue(selected);
  if(evidence.processLogSha256!==sha(Buffer.from(evidence.processLogUtf8,"utf8"))||evidence.runtimeProjectSha256!==sha(evidence.runtimeProjectManifestJson)||evidence.qualificationReceiptSha256!==receiptHash||selected.qualificationReceiptSha256!==receiptHash||receipt.manifestSha256!==manifestHash||receipt.timelineId!==selected.timelineId||receipt.revision!==selected.revision||receipt.headSha256!==selected.headSha256||receipt.godotTraceSha256!==capture.hashes["godot-trace.json"]||receipt.bridgeReportSha256!==capture.hashes["bridge-report.json"]||receipt.processLogSha256!==evidence.processLogSha256||receipt.runtimeProjectSha256!==evidence.runtimeProjectSha256||receipt.implementationSha256!==capture.implementationSha256||receipt.godotBinarySha256!==capture.godotBinarySha256||activation.manifestSha256!==manifestHash||activation.timelineId!==selected.timelineId||activation.qualificationReceiptSha256!==receiptHash||activated.manifestSha256!==manifestHash||activated.timelineId!==selected.timelineId||activated.qualificationReceiptSha256!==receiptHash||activated.currentSha256!==sha(currentJson))fail("R20_CAPTURE_QUALIFICATION_HASH_INVALID");
  await verifyR20QualificationRuntimeEvidence({evidence,manifest:authority,staticArtifacts,worldEventLedgerJson:texts["world-event-ledger.json"],timelineId:selected.timelineId,revision:selected.revision,headSha256:selected.headSha256},"R20_CAPTURE_QUALIFICATION_REPLAY_INVALID");
  const id=manifestHash.slice(7),summaries=capture.audit.timelines.filter(item=>item?.manifestId===id&&item?.status==="qualified");
  if(summaries.length!==1||summaries[0].qualified!==true||summaries[0].timelineId!==selected.timelineId||summaries[0].revision!==selected.revision||summaries[0].headSha256!==selected.headSha256||summaries[0].implementationSha256!==capture.implementationSha256||summaries[0].godotBinarySha256!==capture.godotBinarySha256||summaries[0].qualificationReceiptSha256!==receiptHash)fail("R20_CAPTURE_AUDIT_INVALID");
}

export async function verifyR20Capture({captureRoot,verifyLive=false}={},overrides={}){
  if(typeof captureRoot!=="string"||!path.isAbsolute(captureRoot)||captureRoot.includes("\0")||typeof verifyLive!=="boolean")fail("R20_CAPTURE_VERIFY_ARGUMENT_INVALID");
  const operations=ops(overrides),root=path.resolve(captureRoot);await directory(root,operations);
  const names=(await operations.readdir(root)).sort();if(names.join("\0")!==R20_CAPTURE_ALLOWED_FILES.join("\0"))fail("R20_CAPTURE_CONTENT_INVALID");
  const bytes=new Map();for(const name of R20_CAPTURE_ALLOWED_FILES)bytes.set(name,await operations.readStableFile(path.join(root,name),name==="qualification-evidence.json"?32*1024*1024:16*1024*1024));
  const capture=captureManifest(canonicalBytes(bytes.get("capture-manifest.json"),"R20_CAPTURE_MANIFEST_INVALID")),documents=Object.create(null),texts=Object.create(null);
  for(const name of R20_CAPTURE_PAYLOAD_FILES){const value=bytes.get(name);if(sha(value)!==capture.hashes[name])fail("R20_CAPTURE_FILE_HASH_INVALID");texts[name]=text(value,"R20_CAPTURE_DOCUMENT_INVALID");documents[name]=canonicalText(texts[name],"R20_CAPTURE_DOCUMENT_INVALID")}
  await verifyAuthority(capture,documents,texts);
  if(verifyLive){const implementation=await operations.calculateImplementationIdentity(moduleRoot),godot=await operations.calculateGodotBinaryIdentity(),script=await scriptIdentity(operations);if(implementation?.sha256!==capture.implementationSha256||implementation?.manifestJson!==texts["implementation-manifest.json"]||godot?.sha256!==capture.godotBinarySha256||godot?.byteLength!==documents["godot-binary-identity.json"].byteLength||script.sha256!==capture.captureScript.sha256||script.byteLength!==capture.captureScript.byteLength)fail("R20_CAPTURE_LIVE_IDENTITY_MISMATCH")}
  return Object.freeze({ok:true,captureRoot:root,revision:capture.current.revision,manifestSha256:capture.current.manifestSha256,captureManifestSha256:sha(bytes.get("capture-manifest.json")),liveVerified:verifyLive});
}
function parse(args){
  if(!Array.isArray(args)||args.length!==4||args[0]!=="--npc-run-root"||args[2]!=="--output"||typeof args[1]!=="string"||typeof args[3]!=="string")fail("R20_CAPTURE_ARGUMENT_INVALID");
  const npcRunRoot=path.resolve(args[1]),output=path.resolve(args[3]);
  if(!path.isAbsolute(args[1])||!path.isAbsolute(args[3])||path.dirname(npcRunRoot)!==temporaryRoot||path.dirname(output)!==temporaryRoot||!npcRunRoot.endsWith("-npc")||npcRunRoot===output)fail("R20_CAPTURE_ARGUMENT_INVALID");
  return Object.freeze({npcRunRoot,output});
}

export async function runR20Capture(args,overrides={}){
  const selected=parse(args),operations=ops(overrides);await missing(selected.output,operations);
  const writerLease=await operations.acquireWriterLease({npcRunRoot:selected.npcRunRoot,temporaryRoot});
  let stage=null;
  try{
    const scriptBefore=await scriptIdentity(operations),implementationBefore=await operations.calculateImplementationIdentity(moduleRoot),godotBefore=await operations.calculateGodotBinaryIdentity(),audit=await operations.auditTimelineStore({npcRunRoot:selected.npcRunRoot,temporaryRoot,writerLease,expectedImplementationSha256:implementationBefore.sha256,expectedGodotBinarySha256:godotBefore.sha256});
    if(audit.current===null)fail("R20_CAPTURE_CURRENT_INVALID");
    const currentJson=text(await operations.readStableFile(path.join(selected.npcRunRoot,"npc-current.json")),"R20_CAPTURE_CURRENT_INVALID"),selectedCurrent=current(canonicalText(currentJson,"R20_CAPTURE_CURRENT_INVALID"));
    if(!same(selectedCurrent,audit.current))fail("R20_CAPTURE_CURRENT_INVALID");
    const timeline=path.join(selected.npcRunRoot,"timelines",selectedCurrent.manifestSha256.slice(7));stage=await operations.mkdtemp(path.join(temporaryRoot,`.${path.basename(selected.output)}-`));
    const hashes={};
    for(const name of storeFiles){const bytes=await operations.readStableFile(path.join(timeline,name),name==="qualification-evidence.json"?32*1024*1024:16*1024*1024);hashes[name]=sha(bytes);await operations.writeFile(path.join(stage,name),bytes,{flag:"wx"})}
    const implementationManifestJson=implementationBefore.manifestJson,godotBinaryIdentityJson=canonicalizeJsonValue({format:"matrix-oasis.r20-godot-binary-identity",formatVersion:"0.1.0",sha256:godotBefore.sha256,byteLength:godotBefore.byteLength});
    for(const [name,value] of [["implementation-manifest.json",implementationManifestJson],["godot-binary-identity.json",godotBinaryIdentityJson]]){hashes[name]=sha(value);await operations.writeFile(path.join(stage,name),value,{flag:"wx"})}
    const implementationAfter=await operations.calculateImplementationIdentity(moduleRoot),godotAfter=await operations.calculateGodotBinaryIdentity(),scriptAfter=await scriptIdentity(operations);
    if(implementationAfter.sha256!==implementationBefore.sha256||implementationAfter.manifestJson!==implementationManifestJson||godotAfter.sha256!==godotBefore.sha256||godotAfter.byteLength!==godotBefore.byteLength||!same(scriptAfter,scriptBefore))fail("R20_CAPTURE_IMPLEMENTATION_CHANGED");
    const captureManifestJson=canonicalizeJsonValue({format:"matrix-oasis.r20-capture",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",current:selectedCurrent,audit,implementationSha256:implementationBefore.sha256,godotBinarySha256:godotBefore.sha256,captureScript:scriptBefore,hashes});
    await operations.writeFile(path.join(stage,"capture-manifest.json"),captureManifestJson,{flag:"wx"});
    const prepared=await operations.verifyCapture({captureRoot:stage,verifyLive:true},operations);if(prepared.captureManifestSha256!==sha(captureManifestJson))fail("R20_CAPTURE_STAGE_INVALID");
    await missing(selected.output,operations);
    try{await operations.rename(stage,selected.output);stage=null}catch(error){
      let stageExists=true;try{await operations.lstat(stage)}catch(stageError){if(stageError?.code==="ENOENT")stageExists=false;else throw stageError}
      if(stageExists)throw error;
      let recovered;try{recovered=await operations.verifyCapture({captureRoot:selected.output,verifyLive:true},operations)}catch{throw error}
      if(recovered.captureManifestSha256!==prepared.captureManifestSha256)throw error;stage=null;
    }
    const verified=await operations.verifyCapture({captureRoot:selected.output,verifyLive:true},operations);if(verified.captureManifestSha256!==prepared.captureManifestSha256)fail("R20_CAPTURE_PUBLICATION_INVALID");
    return Object.freeze({output:selected.output,revision:selectedCurrent.revision,manifestSha256:selectedCurrent.manifestSha256});
  }finally{
    if(stage!==null)await operations.rm(stage,{recursive:true,force:true}).catch(()=>{});
    await operations.releaseWriterLease(writerLease);
  }
}

if(captureScriptFile===path.resolve(process.argv[1]??"")){try{const result=await runR20Capture(process.argv.slice(2));process.stdout.write(`R20_CAPTURE_READY ${JSON.stringify(result)}\n`)}catch(error){process.stderr.write(`${error?.message??"R20_CAPTURE_INTERNAL_ERROR"}\n`);process.exitCode=2}}
