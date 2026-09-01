import { randomBytes, createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { builtinModules, createRequire } from "node:module";
import { lstat, mkdir, open, readFile, readdir, realpath, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { createNpcAuthoritySession, restoreNpcAuthoritySession, verifyNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { recoverQualifiedCreatorRuns } from "@matrix-oasis/prototype-creator-qualification";
import { prepareDeterministicNpcBehavior, synthesizeNpcBehaviorPolicy, synthesizeNpcEntityBindings } from "@matrix-oasis/npc-behavior-runtime";
import { createR20Coordinator, exportR20Coordinator, startR20LoopbackCoordinator } from "./lib/r20-host-core.mjs";
import { abandonR20PendingQualification, acquireR20WriterLease, auditR20TimelineStore, createR20TimelineStore, parseR20RunArguments, readStableR20File, recoverR20UnfinishedTimeline, releaseR20WriterLease, resumeR20EmptyTimelineStore, resumeR20QualificationPublication, resumeR20TimelineStore, validateR20QualificationEvidenceJson, validateR20StaticArtifacts, validateR20TraceStaticSemantics } from "./lib/r20-cli-core.mjs";
import { createR16QualificationReferenceVerifier } from "./lib/r16-creator-core.mjs";
import { selectR15EvidenceRun } from "./lib/r15-preview-core.mjs";
import { resolveGodotBinary, runGodotCommand, assertGodotOutputClean } from "./lib/godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";
import { copySpatialPreviewFiles } from "./preview-spatial-prototype.mjs";
import { r14GodotArguments } from "./lib/r14-preview-core.mjs";
import { deriveR20QualificationCoverageRequirement, evaluateR20QualificationCoverage, validateR20QualificationCoverageEvidence } from "./lib/r20-qualification-coverage.mjs";

const moduleRoot=path.resolve(fileURLToPath(new URL("..",import.meta.url))),temporaryRoot=path.resolve(path.parse(moduleRoot).root,"tmp");
const READY="MATRIX_OASIS_R20_NPC_BRIDGE_READY",TRACE="MATRIX_OASIS_R20_NPC_TRACE_JSON:",SHA=/^sha256:[0-9a-f]{64}$/u;
const QUALIFICATION_REQUEST_JSON=canonicalizeJsonValue({format:"matrix-oasis.r20-qualification-request",formatVersion:"0.1.0"});
const sha=(value)=>`sha256:${createHash("sha256").update(value).digest("hex")}`;
const text=(bytes)=>new TextDecoder("utf-8",{fatal:true}).decode(bytes);
export const R20_IMPLEMENTATION_RESOURCE_TREES=Object.freeze(["apps/runtime-godot"]);
export const R20_IMPLEMENTATION_ENTRY_FILES=Object.freeze(["scripts/preview-r20.mjs","scripts/qualify-r20-npc-bridge.mjs"]);
function canonical(bytes){const value=text(bytes),parsed=JSON.parse(value);if(canonicalizeJsonValue(parsed)!==value)throw new Error("R20_SOURCE_CANONICAL_INVALID");return value}
function childEnvironment(token){const env={MATRIX_OASIS_R20_SESSION_TOKEN:token};for(const key of ["SystemRoot","WINDIR","PATH","PATHEXT","TEMP","TMP","APPDATA","LOCALAPPDATA","USERPROFILE"]){if(typeof process.env[key]==="string")env[key]=process.env[key]}return env}
function restoredBehavior(trace){const executions=new Map();let nextSequence=1;for(const command of trace.commands){const key=`${command.actorEntityId}\0${command.ruleIndex}`,prior=executions.get(key)??{actorEntityId:command.actorEntityId,ruleIndex:command.ruleIndex,count:0,lastRevision:0};executions.set(key,{...prior,count:prior.count+1,lastRevision:command.revisionFinished});nextSequence=Math.max(nextSequence,command.sequence+1)}return Object.freeze({nextSequence,executions:[...executions.values()]})}
async function selectQualified(parsed){const verifier=createR16QualificationReferenceVerifier({...parsed,temporaryRoot}),recovered=await recoverQualifiedCreatorRuns({qualifiedRunRoot:parsed.qualifiedRunRoot,temporaryRoot,verifyReferences:verifier}),id=parsed.qualificationRunId??recovered.currentQualificationRunId??(recovered.runs.length===1?recovered.runs[0].qualificationRunId:null),selected=recovered.runs.find((run)=>run.qualificationRunId===id);if(!selected)throw new Error("R20_QUALIFICATION_CACHE_INVALID");const evidence=await selectR15EvidenceRun({evidenceRunRoot:parsed.evidenceRunRoot,temporaryRoot,runId:selected.qualification.evidence.runId});if(evidence.runId!==selected.qualification.evidence.runId)throw new Error("R20_QUALIFICATION_REFERENCE_INVALID");return Object.freeze({qualificationRunId:id,qualification:selected.qualification,evidence})}

async function verifySolvedSource(parsed,selected,preview){
  const runId=selected.qualification.sourceRunId,solutionSha256=selected.qualification.hashes?.spatialSolutionSha256,verificationSha256=selected.qualification.hashes?.spatialVerificationSha256;
  if(typeof runId!=="string"||!/^sha256:[0-9a-f]{64}$/u.test(solutionSha256??"")||!/^sha256:[0-9a-f]{64}$/u.test(verificationSha256??""))throw new Error("R20_SOLVED_SOURCE_INVALID");
  const overlay=path.join(parsed.solvedRunRoot,"solved-runs",runId,solutionSha256.slice(7));
  const solution=await readStableR20File(path.join(overlay,"spatial-solution.json")),verification=await readStableR20File(path.join(overlay,"spatial-verification-report.json"));
  if(sha(solution)!==solutionSha256||sha(verification)!==verificationSha256||sha(preview.get("spatial-solution.json"))!==solutionSha256||sha(preview.get("spatial-verification-report.json"))!==verificationSha256)throw new Error("R20_SOLVED_SOURCE_INVALID");
}

async function stableFileIdentity(file,{maximumBytes=2*1024*1024*1024,allowEmpty=true}={}){
  let handle;
  try{
    const candidate=path.resolve(file);handle=await open(candidate,"r");
    const before=await handle.stat({bigint:true}),linked=await lstat(candidate,{bigint:true}),resolved=path.resolve(await realpath(candidate));
    if(!before.isFile()||!linked.isFile()||linked.isSymbolicLink()||resolved!==candidate||before.dev!==linked.dev||before.ino!==linked.ino||before.size!==linked.size||before.size>BigInt(maximumBytes)||before.size<0n||!allowEmpty&&before.size===0n||before.size>BigInt(Number.MAX_SAFE_INTEGER))throw new Error("R20_FILE_IDENTITY_INVALID");
    const digest=createHash("sha256"),buffer=Buffer.allocUnsafe(1024*1024);let position=0;
    while(position<Number(before.size)){const length=Math.min(buffer.length,Number(before.size)-position),{bytesRead}=await handle.read(buffer,0,length,position);if(bytesRead<1)throw new Error("R20_FILE_IDENTITY_INVALID");digest.update(buffer.subarray(0,bytesRead));position+=bytesRead}
    const after=await handle.stat({bigint:true});
    if(after.dev!==before.dev||after.ino!==before.ino||after.size!==before.size||after.mtimeNs!==before.mtimeNs||after.ctimeNs!==before.ctimeNs)throw new Error("R20_FILE_IDENTITY_INVALID");
    return Object.freeze({sha256:`sha256:${digest.digest("hex")}`,byteLength:Number(before.size)});
  }finally{await handle?.close().catch(()=>{})}
}

function implementationPath(root,file){const relative=path.relative(root,file).replaceAll("\\","/");if(relative===""||relative===".."||relative.startsWith("../")||path.isAbsolute(relative))throw new Error("R20_IMPLEMENTATION_IDENTITY_INVALID");return relative}
async function stableImplementationBytes(file,{allowEmpty=false}={}){const before=await stableFileIdentity(file,{maximumBytes:16*1024*1024,allowEmpty}),bytes=await readFile(file),after=await stableFileIdentity(file,{maximumBytes:16*1024*1024,allowEmpty});if(before.sha256!==after.sha256||before.byteLength!==after.byteLength||sha(bytes)!==before.sha256)throw new Error("R20_IMPLEMENTATION_IDENTITY_CHANGED");return Object.freeze({bytes,identity:before})}
function skipModuleTrivia(source,start){let index=start;for(;;){while(/\s/u.test(source[index]??""))index+=1;if(source[index]==="/"&&source[index+1]==="/"){index+=2;while(index<source.length&&!"\r\n".includes(source[index]))index+=1;continue}if(source[index]==="/"&&source[index+1]==="*"){const end=source.indexOf("*/",index+2);if(end<0)throw new Error("R20_IMPLEMENTATION_SOURCE_INVALID");index=end+2;continue}return index}}
function readModuleString(source,start){const quote=source[start];if(quote!=="'"&&quote!=='"')return null;let value="";for(let index=start+1;index<source.length;index+=1){const character=source[index];if(character===quote)return Object.freeze({value,end:index+1});if(character==="\\"||character==="\r"||character==="\n")throw new Error("R20_IMPLEMENTATION_SPECIFIER_INVALID");value+=character}throw new Error("R20_IMPLEMENTATION_SOURCE_INVALID")}
function skipModuleString(source,start){const quote=source[start];let escaped=false;for(let index=start+1;index<source.length;index+=1){const character=source[index];if(escaped){escaped=false;continue}if(character==="\\"){escaped=true;continue}if(character===quote)return index+1}throw new Error("R20_IMPLEMENTATION_SOURCE_INVALID")}
function skipModuleRegex(source,start){let escaped=false,inClass=false;for(let index=start+1;index<source.length;index+=1){const character=source[index];if(escaped){escaped=false;continue}if(character==="\\"){escaped=true;continue}if(character==="[")inClass=true;else if(character==="]")inClass=false;else if(character==="/"&&!inClass){index+=1;while(/[A-Za-z]/u.test(source[index]??""))index+=1;return index}else if(character==="\r"||character==="\n")return start+1}return start+1}
function scanFromSpecifier(source,start){let index=start,depth=0;while(index<source.length){index=skipModuleTrivia(source,index);const character=source[index];if(character===";"&&depth===0)return null;if(character==="'"||character==='"'){index=skipModuleString(source,index);continue}if(character==="`"){index=skipModuleString(source,index);continue}if(character==="{"||character==="("||character==="["){depth+=1;index+=1;continue}if(character==="}"||character===")"||character==="]"){depth=Math.max(0,depth-1);index+=1;continue}if(/[A-Za-z_$]/u.test(character??"")){const begin=index;index+=1;while(/[A-Za-z0-9_$]/u.test(source[index]??""))index+=1;if(source.slice(begin,index)==="from"){index=skipModuleTrivia(source,index);return readModuleString(source,index)}continue}index+=1}return null}
function moduleSpecifiers(source){
  const found=[],validSpecifier=/^(?:node:|\.{1,2}\/|\/|#|@?[A-Za-z0-9_-])[A-Za-z0-9@._~+:/?=-]*$/u;let index=0,lastToken=null;
  const add=(kind,literal)=>{if(literal&&validSpecifier.test(literal.value))found.push({kind,specifier:literal.value})};
  while(index<source.length){index=skipModuleTrivia(source,index);const character=source[index];if(character===undefined)break;if(character==="'"||character==='"'||character==="`"){index=skipModuleString(source,index);lastToken="value";continue}if(character==="/"&&[null,"(","[","{","=",",",":",";","!","?","&","|","return","case","throw","yield","await"].includes(lastToken)){index=skipModuleRegex(source,index);lastToken="value";continue}if(/[A-Za-z_$]/u.test(character)){const begin=index;index+=1;while(/[A-Za-z0-9_$]/u.test(source[index]??""))index+=1;const identifier=source.slice(begin,index);if(identifier==="import"){let cursor=skipModuleTrivia(source,index);if(source[cursor]==="."){lastToken=identifier;continue}if(source[cursor]==="("){cursor=skipModuleTrivia(source,cursor+1);add("import",readModuleString(source,cursor))}else{const direct=readModuleString(source,cursor);if(direct)add("import",direct);else add("import",scanFromSpecifier(source,cursor))}}else if(identifier==="export"){const cursor=skipModuleTrivia(source,index);if(source[cursor]==="*"||source[cursor]==="{")add("import",scanFromSpecifier(source,cursor))}else if(identifier==="require"){let cursor=skipModuleTrivia(source,index);if(source[cursor]==="("){cursor=skipModuleTrivia(source,cursor+1);add("require",readModuleString(source,cursor))}}lastToken=identifier;continue}lastToken=character;index+=1}
  return found.sort((left,right)=>left.specifier.localeCompare(right.specifier)||left.kind.localeCompare(right.kind));
}
function packageName(specifier){if(specifier.startsWith("@")){const parts=specifier.split("/");return parts.length>=2?`${parts[0]}/${parts[1]}`:null}return specifier.split("/")[0]??null}
function selectExportTarget(value,kind){if(typeof value==="string")return value;if(Array.isArray(value)){for(const item of value){const selected=selectExportTarget(item,kind);if(selected!==null)return selected}return null}if(!value||typeof value!=="object")return null;for(const key of kind==="require"?["require","node","default"]:["import","node","default"]){const selected=selectExportTarget(value[key],kind);if(selected!==null)return selected}return null}
function workspaceExportTarget(workspace,specifier,kind){
  const subpath=specifier===workspace.name?".":`.${specifier.slice(workspace.name.length)}`,exportsValue=workspace.manifest.exports;let selected=null;
  if(typeof exportsValue==="string"&&subpath===".")selected=exportsValue;
  else if(exportsValue&&typeof exportsValue==="object"){
    if(Object.keys(exportsValue).some((key)=>key.startsWith("."))){let value=exportsValue[subpath];if(value===undefined){for(const [key,candidate] of Object.entries(exportsValue)){if(!key.includes("*"))continue;const [prefix,suffix]=key.split("*");if(subpath.startsWith(prefix)&&subpath.endsWith(suffix)){const wildcard=subpath.slice(prefix.length,subpath.length-suffix.length),target=selectExportTarget(candidate,kind);if(typeof target==="string")value=target.replaceAll("*",wildcard);break}}}selected=selectExportTarget(value,kind)}
    else if(subpath===".")selected=selectExportTarget(exportsValue,kind);
  }
  if(exportsValue===undefined&&subpath!==".")selected=subpath;
  if(selected===null&&subpath===".")selected=workspace.manifest.module??workspace.manifest.main??"./index.js";
  if(typeof selected!=="string"||!selected.startsWith("./"))throw new Error("R20_IMPLEMENTATION_EXPORT_INVALID");return path.resolve(workspace.root,selected);
}
async function resolveImplementationFile(candidate){for(const file of [candidate,`${candidate}.mjs`,`${candidate}.js`,`${candidate}.cjs`,`${candidate}.json`,path.join(candidate,"index.mjs"),path.join(candidate,"index.js"),path.join(candidate,"index.cjs")]){try{const linked=await lstat(file);if(linked.isFile()&&!linked.isSymbolicLink())return path.resolve(file)}catch(error){if(error?.code!=="ENOENT"&&error?.code!=="ENOTDIR")throw error}}throw new Error("R20_IMPLEMENTATION_MODULE_NOT_FOUND")}
async function discoverWorkspaces(root,rootManifest){
  if(!Array.isArray(rootManifest.workspaces))throw new Error("R20_IMPLEMENTATION_WORKSPACES_INVALID");const workspaces=new Map();
  for(const pattern of [...rootManifest.workspaces].sort()){if(typeof pattern!=="string"||!pattern.endsWith("/*")||pattern.includes(".."))throw new Error("R20_IMPLEMENTATION_WORKSPACES_INVALID");const parent=path.join(root,pattern.slice(0,-2));for(const entry of (await readdir(parent,{withFileTypes:true})).sort((left,right)=>left.name.localeCompare(right.name))){if(!entry.isDirectory()||entry.isSymbolicLink())continue;const packageRoot=path.join(parent,entry.name),manifestFile=path.join(packageRoot,"package.json");let bytes;try{bytes=(await stableImplementationBytes(manifestFile)).bytes}catch(error){if(error?.code==="ENOENT")continue;throw error}const manifest=JSON.parse(text(bytes));if(typeof manifest.name!=="string"||workspaces.has(manifest.name))throw new Error("R20_IMPLEMENTATION_WORKSPACES_INVALID");workspaces.set(manifest.name,Object.freeze({name:manifest.name,root:packageRoot,manifestFile,manifest}))}}
  return workspaces;
}
async function externalPackageForFile(root,file){let directory=path.dirname(file);while(directory!==root&&directory!==path.dirname(directory)){const manifestFile=path.join(directory,"package.json");try{const bytes=(await stableImplementationBytes(manifestFile)).bytes,manifest=JSON.parse(text(bytes));if(typeof manifest.name!=="string")throw new Error("R20_IMPLEMENTATION_EXTERNAL_IDENTITY_INVALID");return Object.freeze({name:manifest.name,root:directory,manifestFile,manifest})}catch(error){if(error?.code!=="ENOENT")throw error}directory=path.dirname(directory)}throw new Error("R20_IMPLEMENTATION_EXTERNAL_IDENTITY_INVALID")}
async function implementationIdentity(root=moduleRoot,options={}){
  if(typeof root!=="string"||!path.isAbsolute(root)||!options||typeof options!=="object"||Array.isArray(options))throw new Error("R20_IMPLEMENTATION_IDENTITY_INVALID");
  root=path.resolve(root);
  const entryFiles=options.entryFiles??R20_IMPLEMENTATION_ENTRY_FILES,resourceFiles=options.resourceFiles??[],resourceTrees=options.resourceTrees??(root===moduleRoot?R20_IMPLEMENTATION_RESOURCE_TREES:[]);
  if(!Array.isArray(entryFiles)||entryFiles.length<1||!Array.isArray(resourceFiles)||!Array.isArray(resourceTrees))throw new Error("R20_IMPLEMENTATION_IDENTITY_INVALID");
  const rootManifestFile=path.join(root,"package.json"),lockFile=path.join(root,"package-lock.json"),rootManifestBytes=(await stableImplementationBytes(rootManifestFile)).bytes,rootManifest=JSON.parse(text(rootManifestBytes)),workspaces=await discoverWorkspaces(root,rootManifest),filesByPath=new Map(),pending=[];
  const addFile=async(file,parseModule=false,allowEmpty=false)=>{const absolute=path.resolve(file),relative=implementationPath(root,absolute);if(filesByPath.has(relative))return;const loaded=await stableImplementationBytes(absolute,{allowEmpty});filesByPath.set(relative,{path:relative,...loaded.identity});if(parseModule&&/\.(?:[cm]?js)$/iu.test(absolute))pending.push({absolute,source:text(loaded.bytes)})};
  const addResourceTree=async(relativeRoot)=>{
    if(typeof relativeRoot!=="string"||relativeRoot.length<1||relativeRoot.includes("\0")||path.isAbsolute(relativeRoot))throw new Error("R20_IMPLEMENTATION_RESOURCE_TREE_INVALID");
    const absoluteRoot=path.resolve(root,relativeRoot),normalizedRoot=implementationPath(root,absoluteRoot);if(normalizedRoot!==relativeRoot.replaceAll("\\","/").replace(/\/$/u,""))throw new Error("R20_IMPLEMENTATION_RESOURCE_TREE_INVALID");
    let fileCount=0;
    const walk=async(directory)=>{
      const before=await lstat(directory,{bigint:true}),resolved=path.resolve(await realpath(directory));
      if(!before.isDirectory()||before.isSymbolicLink()||resolved!==path.resolve(directory))throw new Error("R20_IMPLEMENTATION_RESOURCE_TREE_INVALID");
      const entries=(await readdir(directory,{withFileTypes:true})).sort((left,right)=>left.name.localeCompare(right.name));
      for(const entry of entries){
        if(entry.name===".godot")continue;
        const candidate=path.join(directory,entry.name),linked=await lstat(candidate,{bigint:true});
        if(linked.isSymbolicLink())throw new Error("R20_IMPLEMENTATION_RESOURCE_TREE_INVALID");
        if(linked.isDirectory()){await walk(candidate);continue}
        if(!linked.isFile())throw new Error("R20_IMPLEMENTATION_RESOURCE_TREE_INVALID");
        await addFile(candidate,false,true);fileCount+=1;if(fileCount>10000)throw new Error("R20_IMPLEMENTATION_RESOURCE_TREE_INVALID");
      }
      const after=await lstat(directory,{bigint:true});
      if(!after.isDirectory()||after.isSymbolicLink()||after.dev!==before.dev||after.ino!==before.ino||after.size!==before.size||after.mtimeNs!==before.mtimeNs||after.ctimeNs!==before.ctimeNs)throw new Error("R20_IMPLEMENTATION_RESOURCE_TREE_CHANGED");
    };
    await walk(absoluteRoot);
  };
  await addFile(rootManifestFile);await addFile(lockFile);for(const relative of [...resourceFiles].sort())await addFile(path.join(root,relative));for(const relative of [...resourceTrees].sort())await addResourceTree(relative);for(const relative of [...entryFiles].sort())await addFile(path.join(root,relative),true);
  while(pending.length>0){const current=pending.shift();for(const dependency of moduleSpecifiers(current.source)){
    const specifier=dependency.specifier;if(specifier.startsWith("node:")||builtinModules.includes(specifier)||builtinModules.includes(specifier.replace(/^node:/u,"")))continue;
    let resolved,workspace=null,externalPackage=null;try{if(specifier.startsWith(".")||specifier.startsWith("/")){resolved=await resolveImplementationFile(path.resolve(path.dirname(current.absolute),specifier));const currentRelative=implementationPath(root,current.absolute);if(currentRelative.startsWith("node_modules/")){const owner=await externalPackageForFile(root,current.absolute),escape=path.relative(owner.root,resolved);if(escape===".."||escape.startsWith(`..${path.sep}`)||path.isAbsolute(escape))throw new Error("R20_IMPLEMENTATION_EXTERNAL_SCOPE_INVALID")}}else{const name=packageName(specifier);workspace=workspaces.get(name)??null;if(workspace)resolved=await resolveImplementationFile(workspaceExportTarget(workspace,specifier,dependency.kind));else{try{resolved=await resolveImplementationFile(createRequire(pathToFileURL(current.absolute)).resolve(specifier));externalPackage=await externalPackageForFile(root,resolved)}catch(error){const currentRelative=implementationPath(root,current.absolute);if(!currentRelative.startsWith("node_modules/"))throw error;const owner=await externalPackageForFile(root,current.absolute),declared={...owner.manifest.dependencies,...owner.manifest.optionalDependencies,...owner.manifest.peerDependencies};if(Object.hasOwn(declared,name))throw error;continue}}}}catch{throw new Error(`R20_IMPLEMENTATION_MODULE_RESOLUTION_INVALID:${implementationPath(root,current.absolute)}:${JSON.stringify(specifier)}`)}
    const relative=implementationPath(root,resolved),external=relative.startsWith("node_modules/");if(workspace)await addFile(workspace.manifestFile);else if(external){externalPackage??=await externalPackageForFile(root,resolved);await addFile(externalPackage.manifestFile)}else if(!relative.startsWith("packages/")&&!relative.startsWith("apps/")&&!relative.startsWith("scripts/"))throw new Error("R20_IMPLEMENTATION_MODULE_SCOPE_INVALID");
    await addFile(resolved,true);
  }}
  const files=[...filesByPath.values()].sort((left,right)=>left.path.localeCompare(right.path));if(files.length<entryFiles.length+resourceFiles.length+2)throw new Error("R20_IMPLEMENTATION_IDENTITY_INVALID");
  const manifestJson=canonicalizeJsonValue({format:"matrix-oasis.r20-implementation-manifest",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",files});
  return Object.freeze({manifestJson,sha256:sha(manifestJson)});
}

function ignoredRuntimeProjectPath(relative){return relative===".godot"||relative.startsWith(".godot/")||/(?:^|\/)[^/]+\.(?:log|tmp)$/iu.test(relative)}
async function runtimeProjectIdentity(projectRoot){
  const root=path.resolve(projectRoot),files=[];
  async function walk(directory,relativeDirectory=""){
    const linked=await lstat(directory,{bigint:true});
    if(!linked.isDirectory()||linked.isSymbolicLink()||path.resolve(await realpath(directory))!==path.resolve(directory))throw new Error("R20_RUNTIME_PROJECT_IDENTITY_INVALID");
    const entries=(await readdir(directory,{withFileTypes:true})).sort((left,right)=>left.name.localeCompare(right.name));
    for(const entry of entries){const relative=relativeDirectory?`${relativeDirectory}/${entry.name}`:entry.name;if(ignoredRuntimeProjectPath(relative))continue;const absolute=path.join(directory,entry.name),candidate=await lstat(absolute,{bigint:true});if(candidate.isSymbolicLink())throw new Error("R20_RUNTIME_PROJECT_IDENTITY_INVALID");if(candidate.isDirectory()){await walk(absolute,relative);continue}if(!candidate.isFile())throw new Error("R20_RUNTIME_PROJECT_IDENTITY_INVALID");const identity=await stableFileIdentity(absolute);files.push({path:relative,byteLength:identity.byteLength,sha256:identity.sha256});if(files.length>10000)throw new Error("R20_RUNTIME_PROJECT_IDENTITY_INVALID")}
  }
  await walk(root);files.sort((left,right)=>left.path.localeCompare(right.path));
  if(files.length<1)throw new Error("R20_RUNTIME_PROJECT_IDENTITY_INVALID");
  const manifestJson=canonicalizeJsonValue({format:"matrix-oasis.r20-runtime-project-manifest",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",files});
  return Object.freeze({manifestJson,sha256:sha(manifestJson)});
}
export async function calculateR20GodotBinaryIdentity(godot=resolveGodotBinary()){
  if(godot?.version!=="4.6.3"||typeof godot.command!=="string"||!path.isAbsolute(godot.command))throw new Error("GODOT_4_6_3_NOT_AVAILABLE");
  return stableFileIdentity(godot.command,{allowEmpty:false});
}
export { implementationIdentity as calculateR20ImplementationIdentity, runtimeProjectIdentity as calculateR20RuntimeProjectIdentity };

export function assertR20FinalizedQualificationIdentity({qualified,manifestJson,ledgerJson}={}){
  let manifest,ledger;
  try{manifest=JSON.parse(manifestJson);ledger=JSON.parse(ledgerJson)}catch{throw new Error("R20_QUALIFICATION_FINALIZED_IDENTITY_MISMATCH")}
  if(!qualified||typeof qualified!=="object"||typeof manifestJson!=="string"||typeof ledgerJson!=="string"||canonicalizeJsonValue(manifest)!==manifestJson||canonicalizeJsonValue(ledger)!==ledgerJson||!/^[0-9a-f]{64}$/u.test(qualified.manifestId??"")||sha(manifestJson)!==`sha256:${qualified.manifestId}`||typeof manifest.timelineId!=="string"||ledger.timeline?.id!==manifest.timelineId||!Number.isSafeInteger(qualified.revision)||qualified.revision<0||ledger.revision!==qualified.revision||qualified.headSha256!==ledger.headSha256)throw new Error("R20_QUALIFICATION_FINALIZED_IDENTITY_MISMATCH");
  return Object.freeze({manifest,ledger});
}

async function createQualificationReceipt({qualified,godotTraceJson,godotLogBytes,exitCode,implementationSha256,godotBinarySha256,runtimeProjectManifestJson}){
  if(exitCode!==0||!qualified?.timelineRoot)throw new Error("R20_GODOT_EXIT_INVALID");
  const files={};
  for(const name of ["authority-manifest.json","behavior-policy.json","entity-bindings.json","world-event-ledger.json","behavior-trace.json","bridge-report.json"])files[name]=await readStableR20File(path.join(qualified.timelineRoot,name));
  const manifestJson=canonical(files["authority-manifest.json"]),ledgerJson=canonical(files["world-event-ledger.json"]),{manifest,ledger}=assertR20FinalizedQualificationIdentity({qualified,manifestJson,ledgerJson}),behaviorTraceJson=canonical(files["behavior-trace.json"]);
  const staticArtifacts=validateR20StaticArtifacts(manifest,canonical(files["behavior-policy.json"]),canonical(files["entity-bindings.json"]));validateR20TraceStaticSemantics(JSON.parse(behaviorTraceJson),staticArtifacts);
  const observedTrace=JSON.parse(godotTraceJson);if(observedTrace.renderer!=="forward_plus")throw new Error("R20_GODOT_RENDERER_INVALID");
  const qualificationCoverage=evaluateR20QualificationCoverage({requirement:manifest.qualificationCoverage,worldEventLedgerJson:ledgerJson,behaviorTraceJson}).evidence;validateR20QualificationCoverageEvidence(qualificationCoverage,manifest.qualificationCoverage);
  return canonicalizeJsonValue({
    format:"matrix-oasis.r20-qualification-receipt",formatVersion:"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",
    manifestSha256:sha(files["authority-manifest.json"]),timelineId:manifest.timelineId,revision:ledger.revision,headSha256:ledger.headSha256,
    godotTraceSha256:sha(godotTraceJson),bridgeReportSha256:sha(files["bridge-report.json"]),godotVersion:"4.6.3",renderer:observedTrace.renderer,
    processExitCode:0,processLogSha256:sha(godotLogBytes),implementationSha256,godotBinarySha256,runtimeProjectSha256:sha(runtimeProjectManifestJson),qualificationCoverage,
  });
}

function validateProcessLog(bytes,sessionToken){
  if(!(bytes instanceof Uint8Array)||bytes.byteLength<1||bytes.byteLength>8*1024*1024)throw new Error("R20_GODOT_LOG_INVALID");
  let value;try{value=text(bytes)}catch{throw new Error("R20_GODOT_LOG_INVALID")}
  if(value.includes("\0")||typeof sessionToken==="string"&&sessionToken.length>0&&value.includes(sessionToken))throw new Error("R20_GODOT_LOG_INVALID");
  assertGodotOutputClean(value);return value;
}

export async function validateR20QualifierRecoveryIdentity({recovery,npcRunRoot,manifestFor}){
  if(!recovery||typeof recovery!=="object"||typeof npcRunRoot!=="string"||!path.isAbsolute(npcRunRoot)||typeof manifestFor!=="function")throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
  const candidates=[recovery.recovered,recovery.emptyTimeline,...(Array.isArray(recovery.evidencePending)?recovery.evidencePending:[]),...(Array.isArray(recovery.qualificationPending)?recovery.qualificationPending:[])].filter((value)=>value!==null&&value!==undefined);
  const verified=[];
  for(const candidate of candidates){
    const manifestId=typeof candidate.manifestId==="string"?candidate.manifestId:null;
    if(manifestId===null||!/^[0-9a-f]{64}$/u.test(manifestId))throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
    const expectedTimelineRoot=path.join(npcRunRoot,"timelines",manifestId);
    if(candidate.timelineRoot!==undefined&&path.resolve(candidate.timelineRoot)!==expectedTimelineRoot)throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
    const authorityManifestJson=canonical(await readStableR20File(path.join(expectedTimelineRoot,"authority-manifest.json")));
    if(candidate.authorityManifestJson!==undefined&&candidate.authorityManifestJson!==authorityManifestJson||sha(authorityManifestJson)!==`sha256:${manifestId}`)throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
    const manifest=JSON.parse(authorityManifestJson),timelineId=manifest.timelineId;
    if(typeof timelineId!=="string"||candidate.timelineId!==undefined&&candidate.timelineId!==timelineId||authorityManifestJson!==manifestFor(timelineId))throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
    verified.push(Object.freeze({manifestId,timelineId,timelineRoot:expectedTimelineRoot,authorityManifestJson,status:candidate.status??"unfinished"}));
  }
  return Object.freeze(verified);
}

export async function resumeR20EmptyQualifierTimeline(request,operations={createNpcAuthoritySession,resumeR20EmptyTimelineStore}){
  const empty=request?.emptyTimeline;
  if(!empty||typeof empty.authorityManifestJson!=="string"||typeof request?.manifestFor!=="function"||typeof operations?.createNpcAuthoritySession!=="function"||typeof operations?.resumeR20EmptyTimelineStore!=="function")throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
  let manifest;try{manifest=JSON.parse(empty.authorityManifestJson)}catch{throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH")}
  if(empty.authorityManifestJson!==request.manifestFor(manifest.timelineId))throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
  const session=await operations.createNpcAuthoritySession({runtimeGamePackJson:request.runtimeGamePackJson,runtimeReceiptJson:request.runtimeReceiptJson,policyJson:request.policyJson,timelineId:manifest.timelineId});
  if(!session?.ok)throw new Error(session?.diagnostics?.[0]?.code??"R20_STORE_RECOVERY_IDENTITY_MISMATCH");
  const store=await operations.resumeR20EmptyTimelineStore({npcRunRoot:request.npcRunRoot,temporaryRoot:request.temporaryRoot,recovery:empty,behaviorPolicyJson:request.behaviorPolicyJson,entityBindingJson:request.entityBindingJson,writerLease:request.writerLease});
  return Object.freeze({session,store,resumedTimeline:false});
}

export function createRetryableR20WriterRelease(writerLease,releaseOperation=releaseR20WriterLease){
  if(writerLease===null||writerLease===undefined||typeof releaseOperation!=="function")throw new Error("R20_STORE_WRITER_LEASE_INVALID");
  let released=false,pending=null;
  const release=async()=>{
    if(released)return;
    if(pending!==null)return pending;
    pending=(async()=>{await releaseOperation(writerLease);released=true})();
    try{await pending}finally{if(!released)pending=null}
  };
  return Object.freeze({release,isReleased:()=>released});
}

export async function releaseR20WriterWithRetry(releaseWriter){
  if(typeof releaseWriter!=="function")throw new Error("R20_STORE_WRITER_LEASE_INVALID");
  try{await releaseWriter();return}catch{/* one bounded retry handles a transient filesystem race */}
  try{await releaseWriter()}catch{throw new Error("R20_WRITER_RELEASE_FAILED")}
}

function attachR20CleanupDiagnostic(error,diagnostic="R20_QUALIFICATION_CLEANUP_FAILED"){
  const target=error instanceof Error?error:new Error("R20_QUALIFICATION_INTERNAL_ERROR");
  if(target.cleanupDiagnostic===undefined)Object.defineProperty(target,"cleanupDiagnostic",{value:diagnostic,enumerable:false,configurable:false,writable:false});
  return target;
}

function sameR20ActivationIdentity(left,right){try{return canonicalizeJsonValue(left)===canonicalizeJsonValue(right)}catch{return false}}

async function verifyR20RecoveredAuthorityLedger({request,operations,worldEventLedgerJson,runtimeEvidence=null,diagnostic}){
  let ledger;
  try{
    ledger=JSON.parse(worldEventLedgerJson);
    if(canonicalizeJsonValue(ledger)!==worldEventLedgerJson||ledger.timeline?.id!==request.qualificationPending.timelineId||ledger.revision!==request.qualificationPending.revision||ledger.headSha256!==request.qualificationPending.headSha256)throw new Error("identity");
    const runtimeGamePackJson=runtimeEvidence?.runtimeGamePackJson??request.runtimeGamePackJson,runtimeReceiptJson=runtimeEvidence?.runtimeReceiptJson??request.runtimeReceiptJson,policyJson=runtimeEvidence?.authorityPolicyJson??request.policyJson;
    if(runtimeEvidence!==null&&(runtimeGamePackJson!==request.runtimeGamePackJson||runtimeReceiptJson!==request.runtimeReceiptJson||policyJson!==request.policyJson))throw new Error("identity");
    const restored=await operations.restoreAuthoritySession({runtimeGamePackJson,runtimeReceiptJson,policyJson,worldEventLedgerJson});
    if(restored?.ok!==true||restored.canonicalWorldEventLedgerJson!==worldEventLedgerJson||restored.session===undefined)throw new Error("restore");
    const verified=await operations.verifyAuthoritySession(restored.session),reportJson=verified?.canonicalWorldEventLedgerReplayReportJson,report=typeof reportJson==="string"?JSON.parse(reportJson):null;
    if(verified?.ok!==true||verified.canonicalWorldEventLedgerJson!==worldEventLedgerJson||verified.fullReplayCount!==2||canonicalizeJsonValue(report)!==reportJson||report.timelineId!==ledger.timeline.id||report.ledgerSha256!==sha(worldEventLedgerJson)||report.throughRevision!==ledger.revision||report.throughHeadSha256!==ledger.headSha256)throw new Error("verify");
  }catch{throw new Error(diagnostic)}
  return Object.freeze(ledger);
}

export async function resumeR20QualifiedPendingCurrent(request,operations={resumeQualificationPublication:resumeR20QualificationPublication,readStableFile:readStableR20File,publishProcessLog,restoreAuthoritySession:restoreNpcAuthoritySession,verifyAuthoritySession:verifyNpcAuthoritySession}){
  if(request?.waitForTrace!==true)throw new Error("R20_QUALIFICATION_RESUME_REQUIRES_QUALIFICATION");
  if(!request.qualificationPending||typeof request.manifestFor!=="function"||typeof request.releaseWriter!=="function"||typeof request.runtimeGamePackJson!=="string"||typeof request.runtimeReceiptJson!=="string"||typeof request.policyJson!=="string"||typeof operations?.resumeQualificationPublication!=="function"||typeof operations?.readStableFile!=="function"||typeof operations?.publishProcessLog!=="function"||typeof operations?.restoreAuthoritySession!=="function"||typeof operations?.verifyAuthoritySession!=="function")throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
  await validateR20QualifierRecoveryIdentity({recovery:{recovered:null,emptyTimeline:null,evidencePending:[],qualificationPending:[request.qualificationPending]},npcRunRoot:request.npcRunRoot,manifestFor:request.manifestFor});
  const publication=await operations.resumeQualificationPublication({npcRunRoot:request.npcRunRoot,temporaryRoot:request.temporaryRoot,qualificationPending:request.qualificationPending,writerLease:request.writerLease,expectedImplementationSha256:request.expectedImplementationSha256,expectedGodotBinarySha256:request.expectedGodotBinarySha256});
  const worldEventLedgerJson=canonical(await operations.readStableFile(path.join(publication.timelineRoot,"world-event-ledger.json")));
  const evidenceJson=canonical(await operations.readStableFile(path.join(publication.timelineRoot,"qualification-evidence.json"),32*1024*1024));let evidence;try{evidence=validateR20QualificationEvidenceJson(evidenceJson)}catch{throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH")}
  await verifyR20RecoveredAuthorityLedger({request,operations,worldEventLedgerJson,runtimeEvidence:evidence,diagnostic:"R20_STORE_RECOVERY_REPLAY_INVALID"});
  const godotTraceJson=canonical(await operations.readStableFile(path.join(publication.timelineRoot,"godot-trace.json"))),trace=JSON.parse(godotTraceJson),receiptJson=publication.qualificationReceiptJson,receipt=JSON.parse(receiptJson),processLogBytes=Buffer.from(evidence.processLogUtf8??"","utf8");
  validateProcessLog(processLogBytes,null);
  const activationIdentity=publication.activationIdentity;
  if(!activationIdentity||publication.manifestId!==request.qualificationPending.manifestId||publication.timelineId!==request.qualificationPending.timelineId||publication.revision!==request.qualificationPending.revision||publication.headSha256!==request.qualificationPending.headSha256||publication.qualificationReceiptSha256!==request.qualificationPending.qualificationReceiptSha256||activationIdentity.manifestId!==publication.manifestId||activationIdentity.timelineId!==publication.timelineId||activationIdentity.revision!==publication.revision||activationIdentity.headSha256!==publication.headSha256||activationIdentity.qualificationReceiptSha256!==publication.qualificationReceiptSha256||activationIdentity.worldEventLedgerSha256!==sha(worldEventLedgerJson)||activationIdentity.current!==publication.current||activationIdentity.currentSha256!==publication.currentSha256||evidence.qualificationReceiptJson!==receiptJson||sha(receiptJson)!==evidence.qualificationReceiptSha256||sha(processLogBytes)!==publication.persistedProcessLogSha256||evidence.runtimeProjectSha256!==publication.persistedRuntimeProjectSha256||receipt.godotTraceSha256!==sha(godotTraceJson)||receipt.implementationSha256!==request.expectedImplementationSha256||receipt.godotBinarySha256!==request.expectedGodotBinarySha256)throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
  const processLog=await operations.publishProcessLog(processLogBytes),qualified=Object.freeze({manifestId:publication.manifestId,timelineRoot:publication.timelineRoot,verified:true,recovered:true});
  const verifyPublishedTarget=async(actual)=>{if(!sameR20ActivationIdentity(actual?.identity,activationIdentity)||actual.worldEventLedgerJson!==worldEventLedgerJson)throw new Error("identity");const publishedCurrentJson=canonical(await operations.readStableFile(path.join(request.npcRunRoot,"npc-current.json"))),publishedLedgerJson=canonical(await operations.readStableFile(path.join(publication.timelineRoot,"world-event-ledger.json")));if(publishedCurrentJson!==publication.current||sha(publishedCurrentJson)!==publication.currentSha256||publishedLedgerJson!==worldEventLedgerJson)throw new Error("identity");await verifyR20RecoveredAuthorityLedger({request,operations,worldEventLedgerJson:publishedLedgerJson,runtimeEvidence:evidence,diagnostic:"R20_STORE_RECOVERY_ACTIVATION_INVALID"})};
  const published=await publication.activateQualification({expectedImplementationSha256:request.expectedImplementationSha256,expectedGodotBinarySha256:request.expectedGodotBinarySha256,expectedActivationIdentity:activationIdentity,verifyBeforePublish:async(actual)=>{if(!sameR20ActivationIdentity(actual?.identity,activationIdentity)||actual.worldEventLedgerJson!==worldEventLedgerJson)throw new Error("identity");await verifyR20RecoveredAuthorityLedger({request,operations,worldEventLedgerJson:actual.worldEventLedgerJson,runtimeEvidence:evidence,diagnostic:"R20_STORE_RECOVERY_ACTIVATION_INVALID"})},verifyAfterPublish:verifyPublishedTarget});
  if(!published||published.manifestId!==publication.manifestId||published.timelineId!==publication.timelineId||published.revision!==publication.revision||published.headSha256!==publication.headSha256||published.qualificationReceiptSha256!==publication.qualificationReceiptSha256||published.current!==publication.current||published.currentSha256!==publication.currentSha256||published.qualified!==true||!sameR20ActivationIdentity(published.activationIdentity,activationIdentity))throw new Error("R20_STORE_RECOVERY_ACTIVATION_INVALID");
  await releaseR20WriterWithRetry(request.releaseWriter);
  return Object.freeze({ok:true,trace,qualified,published,receiptJson,processLog,sourceRunId:request.sourceRunId,qualificationRunId:request.qualificationRunId,bindingCount:request.bindingCount,recoveredPublication:true});
}

export async function auditR20PreviewQualificationBasis(request,operations={auditTimelineStore:auditR20TimelineStore,readStableFile:readStableR20File,restoreAuthoritySession:restoreNpcAuthoritySession,verifyAuthoritySession:verifyNpcAuthoritySession}){
  const invalid=()=>{throw new Error("R20_PREVIEW_QUALIFICATION_BASIS_INVALID")};
  if(!request||typeof request.npcRunRoot!=="string"||!path.isAbsolute(request.npcRunRoot)||typeof request.temporaryRoot!=="string"||!path.isAbsolute(request.temporaryRoot)||request.writerLease===undefined||!SHA.test(request.expectedImplementationSha256??"")||!SHA.test(request.expectedGodotBinarySha256??"")||typeof request.manifestFor!=="function"||typeof request.runtimeGamePackJson!=="string"||typeof request.runtimeReceiptJson!=="string"||typeof request.policyJson!=="string"||typeof operations?.auditTimelineStore!=="function"||typeof operations?.readStableFile!=="function"||typeof operations?.restoreAuthoritySession!=="function"||typeof operations?.verifyAuthoritySession!=="function")invalid();
  const auditRequest=Object.freeze({npcRunRoot:request.npcRunRoot,temporaryRoot:request.temporaryRoot,writerLease:request.writerLease,expectedImplementationSha256:request.expectedImplementationSha256,expectedGodotBinarySha256:request.expectedGodotBinarySha256});
  const auditCurrent=async()=>{
    let audit;
    try{audit=await operations.auditTimelineStore(auditRequest)}catch(error){if(error?.code==="ENOENT"||error?.message==="R20_STORE_IMPLEMENTATION_IDENTITY_INVALID")throw new Error("R20_PREVIEW_QUALIFICATION_REQUIRED");throw error}
    const current=audit?.current;
    if(audit?.ok!==true||current===null||current===undefined)throw new Error("R20_PREVIEW_QUALIFICATION_REQUIRED");
    if(!SHA.test(current.manifestSha256??"")||typeof current.timelineId!=="string"||!Number.isSafeInteger(current.revision)||current.revision<0||current.headSha256!==null&&!SHA.test(current.headSha256??"")||!SHA.test(current.qualificationReceiptSha256??"")||!Array.isArray(audit.timelines))invalid();
    const manifestId=current.manifestSha256.slice(7),matches=audit.timelines.filter((item)=>item?.manifestId===manifestId&&item.timelineId===current.timelineId&&item.revision===current.revision&&item.headSha256===current.headSha256&&item.qualificationReceiptSha256===current.qualificationReceiptSha256&&item.implementationSha256===request.expectedImplementationSha256&&item.godotBinarySha256===request.expectedGodotBinarySha256&&item.qualified===true&&item.status==="qualified");
    if(matches.length!==1)invalid();
    return Object.freeze({audit,current,manifestId,timelineRoot:path.join(request.npcRunRoot,"timelines",manifestId)});
  };
  const before=await auditCurrent(),manifestJson=canonical(await operations.readStableFile(path.join(before.timelineRoot,"authority-manifest.json"))),evidenceJson=canonical(await operations.readStableFile(path.join(before.timelineRoot,"qualification-evidence.json"),32*1024*1024)),worldEventLedgerJson=canonical(await operations.readStableFile(path.join(before.timelineRoot,"world-event-ledger.json")));
  let manifest,evidence,receipt,ledger;
  try{manifest=JSON.parse(manifestJson);evidence=validateR20QualificationEvidenceJson(evidenceJson);receipt=JSON.parse(evidence.qualificationReceiptJson);ledger=JSON.parse(worldEventLedgerJson)}catch{invalid()}
  const expectedManifestJson=request.manifestFor(before.current.timelineId);
  if(typeof expectedManifestJson!=="string"||typeof evidence.processLogUtf8!=="string")invalid();
  const processLogBytes=Buffer.from(evidence.processLogUtf8,"utf8");
  if(manifestJson!==expectedManifestJson||sha(manifestJson)!==before.current.manifestSha256||manifest.timelineId!==before.current.timelineId||evidence.runtimeGamePackJson!==request.runtimeGamePackJson||evidence.runtimeReceiptJson!==request.runtimeReceiptJson||evidence.authorityPolicyJson!==request.policyJson||typeof evidence.qualificationReceiptJson!=="string"||evidence.qualificationReceiptSha256!==before.current.qualificationReceiptSha256||sha(evidence.qualificationReceiptJson)!==evidence.qualificationReceiptSha256||evidence.processLogSha256!==sha(processLogBytes)||typeof evidence.runtimeProjectManifestJson!=="string"||evidence.runtimeProjectSha256!==sha(evidence.runtimeProjectManifestJson)||receipt.manifestSha256!==before.current.manifestSha256||receipt.timelineId!==before.current.timelineId||receipt.revision!==before.current.revision||receipt.headSha256!==before.current.headSha256||receipt.implementationSha256!==request.expectedImplementationSha256||receipt.godotBinarySha256!==request.expectedGodotBinarySha256||receipt.processLogSha256!==evidence.processLogSha256||receipt.runtimeProjectSha256!==evidence.runtimeProjectSha256||ledger.timeline?.id!==before.current.timelineId||ledger.revision!==before.current.revision||ledger.headSha256!==before.current.headSha256)invalid();
  try{
    const restored=await operations.restoreAuthoritySession({runtimeGamePackJson:evidence.runtimeGamePackJson,runtimeReceiptJson:evidence.runtimeReceiptJson,policyJson:evidence.authorityPolicyJson,worldEventLedgerJson});
    if(restored?.ok!==true||restored.canonicalWorldEventLedgerJson!==worldEventLedgerJson||restored.session===undefined)throw new Error("replay");
    const verified=await operations.verifyAuthoritySession(restored.session),reportJson=verified?.canonicalWorldEventLedgerReplayReportJson,report=typeof reportJson==="string"?JSON.parse(reportJson):null;
    if(verified?.ok!==true||verified.canonicalWorldEventLedgerJson!==worldEventLedgerJson||verified.fullReplayCount!==2||canonicalizeJsonValue(report)!==reportJson||report.timelineId!==before.current.timelineId||report.ledgerSha256!==sha(worldEventLedgerJson)||report.throughRevision!==before.current.revision||report.throughHeadSha256!==before.current.headSha256)throw new Error("replay");
  }catch{throw new Error("R20_PREVIEW_QUALIFICATION_REPLAY_INVALID")}
  const after=await auditCurrent(),manifestAfter=canonical(await operations.readStableFile(path.join(before.timelineRoot,"authority-manifest.json"))),evidenceAfter=canonical(await operations.readStableFile(path.join(before.timelineRoot,"qualification-evidence.json"),32*1024*1024)),ledgerAfter=canonical(await operations.readStableFile(path.join(before.timelineRoot,"world-event-ledger.json")));
  if(canonicalizeJsonValue(after.current)!==canonicalizeJsonValue(before.current)||after.manifestId!==before.manifestId||manifestAfter!==manifestJson||evidenceAfter!==evidenceJson||ledgerAfter!==worldEventLedgerJson)invalid();
  return Object.freeze({qualificationBasisManifestSha256:before.current.manifestSha256,qualificationBasisTimelineId:before.current.timelineId,qualificationBasisRevision:before.current.revision,qualificationBasisHeadSha256:before.current.headSha256,timelineCount:after.audit.timelines.length});
}

export async function waitForR20GodotCloseAfterTrace({closePromise,terminate,closeTimeoutMs=10_000,terminationWaitMs=5_000}){
  if(!closePromise||typeof closePromise.then!=="function"||typeof terminate!=="function"||!Number.isSafeInteger(closeTimeoutMs)||closeTimeoutMs<1||closeTimeoutMs>10_000||!Number.isSafeInteger(terminationWaitMs)||terminationWaitMs<1||terminationWaitMs>5_000)throw new Error("R20_GODOT_CLOSE_WAIT_INVALID");
  const timeout=Object.freeze(Object.create(null));
  let timer=null;
  const wait=(milliseconds)=>new Promise((resolve)=>{timer=setTimeout(()=>resolve(timeout),milliseconds)});
  let closed;
  try{closed=await Promise.race([closePromise,wait(closeTimeoutMs)])}finally{if(timer!==null){clearTimeout(timer);timer=null}}
  if(closed!==timeout)return closed;
  try{terminate()}catch{/* the missing close remains the authoritative failure */}
  try{await Promise.race([closePromise,wait(terminationWaitMs)])}finally{if(timer!==null)clearTimeout(timer)}
  throw new Error("R20_GODOT_CLOSE_TIMEOUT");
}

async function publishProcessLog(godotLogBytes){
  const processLogSha256=sha(godotLogBytes),target=path.join(temporaryRoot,`matrix-oasis-r20-godot-${processLogSha256.slice(7)}.log`);
  try{await writeFile(target,godotLogBytes,{flag:"wx"})}catch(error){if(error?.code!=="EEXIST")throw error;const existing=await readStableR20File(target,8*1024*1024);if(sha(existing)!==processLogSha256)throw new Error("R20_GODOT_LOG_IDENTITY_INVALID")}
  return Object.freeze({path:target,sha256:processLogSha256});
}

export async function launchR20Bridge(parsed,{headless=false,waitForTrace=false,godot=resolveGodotBinary(),spawnProcess=spawn}={}){
  if(godot?.version!=="4.6.3")throw new Error("GODOT_4_6_3_NOT_AVAILABLE");
  const selected=await selectQualified(parsed);
  const preview=selected.evidence.previewFiles,required=["runtime-game-pack.json","runtime-receipt.json","scene-pack.json","prototype-asset-bundle.json","spatial-solution.json","spatial-verification-report.json","environment-facts.json"];
  if(!(preview instanceof Map)||required.some((name)=>!(preview.get(name) instanceof Uint8Array)))throw new Error("R20_SOURCE_CACHE_INVALID");
  await verifySolvedSource(parsed,selected,preview);
  const prototypeRoot=parsed.prototypeRunRoot,sourceRunId=selected.qualification.sourceRunId;
  const blueprint=canonical(await readStableR20File(path.join(prototypeRoot,"runs",sourceRunId,"scene-blueprint.json")));
  const policyJson=canonicalizeJsonValue(JSON.parse(text(await readStableR20File(parsed.policyPath)))),runtimeGamePackJson=canonical(preview.get("runtime-game-pack.json")),runtimeReceiptJson=canonical(preview.get("runtime-receipt.json")),coverage=deriveR20QualificationCoverageRequirement(runtimeGamePackJson);
  const behavior=synthesizeNpcBehaviorPolicy({authorityPolicyJson:policyJson});if(!behavior.ok)throw new Error(behavior.diagnostics[0].code);
  const bindings=synthesizeNpcEntityBindings({authorityPolicyJson:policyJson,sceneBlueprintJson:blueprint,scenePackJson:canonical(preview.get("scene-pack.json")),assetBundleJson:canonical(preview.get("prototype-asset-bundle.json")),spatialSolutionJson:canonical(preview.get("spatial-solution.json")),spatialVerificationJson:canonical(preview.get("spatial-verification-report.json"))});if(!bindings.ok||bindings.npcEntityBindings.bindings.length<1)throw new Error(bindings.diagnostics?.[0]?.code??"R20_ENTITY_BINDINGS_EMPTY");
  const prepared=prepareDeterministicNpcBehavior({behaviorPolicyJson:behavior.canonicalNpcBehaviorPolicyJson,entityBindingJson:bindings.canonicalNpcEntityBindingJson,authorityPolicyJson:policyJson});if(!prepared.ok)throw new Error(prepared.diagnostics[0].code);
  if(!path.isAbsolute(godot.command))throw new Error("R20_GODOT_BINARY_PATH_INVALID");
  const implementationBefore=await implementationIdentity(),godotBinaryBefore=await stableFileIdentity(godot.command,{allowEmpty:false});
  const manifestFor=(id)=>canonicalizeJsonValue({format:"matrix-oasis.npc-authority-manifest",formatVersion:"0.2.0",canonicalization:"matrix-oasis.canonical-json/1",timelineId:id,qualificationRunId:selected.qualificationRunId,sourceRunId,qualificationCoverage:coverage.requirement,toolchain:{godotVersion:"4.6.3",renderer:"forward_plus"},identities:{authorityPolicySha256:sha(policyJson),behaviorPolicySha256:sha(behavior.canonicalNpcBehaviorPolicyJson),entityBindingSha256:sha(bindings.canonicalNpcEntityBindingJson),implementationSha256:implementationBefore.sha256,godotBinarySha256:godotBinaryBefore.sha256,runtimePackSha256:sha(preview.get("runtime-game-pack.json")),runtimeReceiptSha256:sha(preview.get("runtime-receipt.json")),spatialSolutionSha256:sha(preview.get("spatial-solution.json")),spatialVerificationSha256:sha(preview.get("spatial-verification-report.json"))}});
  const writerLease=await acquireR20WriterLease({npcRunRoot:parsed.npcRunRoot,temporaryRoot}),writerRelease=createRetryableR20WriterRelease(writerLease),releaseWriter=writerRelease.release;
  const releaseWriterAfterFailure=async()=>{try{await releaseR20WriterWithRetry(releaseWriter);return null}catch{return "R20_WRITER_RELEASE_FAILED"}};
  let session,store,restoredBehaviorState=null,restoredCommands=[],resumedTimeline=false,coordinator,qualified=null,timelineVerified=false,resolveQualified,server=null,project=null,child=null,childClosePromise=null,cleanup=null,qualificationBasis=null,teardownPromise=null,cleanupPromise=null,cleanupComplete=false;
  const qualifiedPromise=new Promise((resolve)=>{resolveQualified=resolve});
  try{
    if(!waitForTrace)qualificationBasis=await auditR20PreviewQualificationBasis({npcRunRoot:parsed.npcRunRoot,temporaryRoot,writerLease,expectedImplementationSha256:implementationBefore.sha256,expectedGodotBinarySha256:godotBinaryBefore.sha256,manifestFor,runtimeGamePackJson:canonical(preview.get("runtime-game-pack.json")),runtimeReceiptJson:canonical(preview.get("runtime-receipt.json")),policyJson});
    let recovery=await recoverR20UnfinishedTimeline({npcRunRoot:parsed.npcRunRoot,temporaryRoot,writerLease});
    await validateR20QualifierRecoveryIdentity({recovery,npcRunRoot:parsed.npcRunRoot,manifestFor});
    if(recovery.qualificationPending.length===1&&!waitForTrace)throw new Error("R20_QUALIFICATION_RESUME_REQUIRES_QUALIFICATION");
    if(recovery.qualificationPending.length===1)return await resumeR20QualifiedPendingCurrent({waitForTrace,qualificationPending:recovery.qualificationPending[0],manifestFor,npcRunRoot:parsed.npcRunRoot,temporaryRoot,writerLease,releaseWriter,expectedImplementationSha256:implementationBefore.sha256,expectedGodotBinarySha256:godotBinaryBefore.sha256,sourceRunId,qualificationRunId:selected.qualificationRunId,bindingCount:bindings.npcEntityBindings.bindings.length,runtimeGamePackJson:canonical(preview.get("runtime-game-pack.json")),runtimeReceiptJson:canonical(preview.get("runtime-receipt.json")),policyJson});
    if(recovery.evidencePending.length===1){await abandonR20PendingQualification({npcRunRoot:parsed.npcRunRoot,temporaryRoot,manifestId:recovery.evidencePending[0].manifestId,reason:"process-error",writerLease});recovery=Object.freeze({...recovery,evidencePending:Object.freeze([])})}
    let recovered=recovery.recovered;
    if(recovered&&recovered.partialGodotTraceJson!==null){
      const recoveredTimelineId=JSON.parse(recovered.authorityManifestJson).timelineId;if(recovered.authorityManifestJson!==manifestFor(recoveredTimelineId))throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
      const recoverySession=await restoreNpcAuthoritySession({runtimeGamePackJson:canonical(preview.get("runtime-game-pack.json")),runtimeReceiptJson:canonical(preview.get("runtime-receipt.json")),policyJson,worldEventLedgerJson:recovered.canonicalWorldEventLedgerJson});if(!recoverySession.ok)throw new Error(recoverySession.diagnostics[0].code);
      const recoveryStore=await resumeR20TimelineStore({npcRunRoot:parsed.npcRunRoot,temporaryRoot,recovery:recovered,behaviorPolicyJson:behavior.canonicalNpcBehaviorPolicyJson,entityBindingJson:bindings.canonicalNpcEntityBindingJson,writerLease});
      const recoveryCoordinator=createR20Coordinator({authoritySession:recoverySession.session,preparedBehavior:prepared.prepared,initialBehaviorState:prepared.initialState,restoredBehaviorState:restoredBehavior(recovered.behaviorTrace),restoredCommands:recovered.behaviorTrace.commands,entityBindingSha256:sha(bindings.canonicalNpcEntityBindingJson),sessionToken:"recovery".repeat(8),qualificationCoverageRequirement:coverage.requirement});if(!recoveryCoordinator)throw new Error("R20_COORDINATOR_INVALID");
      const qualificationCoverageEvidence=evaluateR20QualificationCoverage({requirement:coverage.requirement,worldEventLedgerJson:recovered.canonicalWorldEventLedgerJson,behaviorTraceJson:canonicalizeJsonValue(recovered.behaviorTrace)}).evidence;
      const finalizedRecovery=await recoveryStore.finalize(exportR20Coordinator(recoveryCoordinator),{godotTraceJson:recovered.partialGodotTraceJson,qualificationCoverageEvidence});
      await abandonR20PendingQualification({npcRunRoot:parsed.npcRunRoot,temporaryRoot,manifestId:finalizedRecovery.manifestId,reason:"process-error",writerLease});recovered=null;
    }
    if(recovered){
      const recoveredTimelineId=JSON.parse(recovered.authorityManifestJson).timelineId;if(recovered.authorityManifestJson!==manifestFor(recoveredTimelineId))throw new Error("R20_STORE_RECOVERY_IDENTITY_MISMATCH");
      session=await restoreNpcAuthoritySession({runtimeGamePackJson:canonical(preview.get("runtime-game-pack.json")),runtimeReceiptJson:canonical(preview.get("runtime-receipt.json")),policyJson,worldEventLedgerJson:recovered.canonicalWorldEventLedgerJson});if(!session.ok)throw new Error(session.diagnostics[0].code);
      store=await resumeR20TimelineStore({npcRunRoot:parsed.npcRunRoot,temporaryRoot,recovery:recovered,behaviorPolicyJson:behavior.canonicalNpcBehaviorPolicyJson,entityBindingJson:bindings.canonicalNpcEntityBindingJson,writerLease});restoredBehaviorState=restoredBehavior(recovered.behaviorTrace);restoredCommands=recovered.behaviorTrace.commands;resumedTimeline=true;
    }else if(recovery.emptyTimeline){
      const emptyResume=await resumeR20EmptyQualifierTimeline({emptyTimeline:recovery.emptyTimeline,manifestFor,runtimeGamePackJson:canonical(preview.get("runtime-game-pack.json")),runtimeReceiptJson:canonical(preview.get("runtime-receipt.json")),policyJson,npcRunRoot:parsed.npcRunRoot,temporaryRoot,behaviorPolicyJson:behavior.canonicalNpcBehaviorPolicyJson,entityBindingJson:bindings.canonicalNpcEntityBindingJson,writerLease});
      session=emptyResume.session;store=emptyResume.store;resumedTimeline=emptyResume.resumedTimeline;
    }else{
      let attemptIndex=0;try{attemptIndex=(await auditR20TimelineStore({npcRunRoot:parsed.npcRunRoot,temporaryRoot,writerLease})).timelines.length}catch(error){if(error?.code!=="ENOENT")throw error}
      const timelineId=`timeline-${sha(canonicalizeJsonValue({qualificationRunId:selected.qualificationRunId,authorityPolicySha256:sha(policyJson),implementationSha256:implementationBefore.sha256,attemptIndex})).slice(7,31)}`;
      session=await createNpcAuthoritySession({runtimeGamePackJson:canonical(preview.get("runtime-game-pack.json")),runtimeReceiptJson:canonical(preview.get("runtime-receipt.json")),policyJson,timelineId});if(!session.ok)throw new Error(session.diagnostics[0].code);
      store=await createR20TimelineStore({npcRunRoot:parsed.npcRunRoot,temporaryRoot,authorityManifestJson:manifestFor(timelineId),behaviorPolicyJson:behavior.canonicalNpcBehaviorPolicyJson,entityBindingJson:bindings.canonicalNpcEntityBindingJson,writerLease});
    }
  }catch(error){const diagnostic=await releaseWriterAfterFailure();throw diagnostic===null?error:attachR20CleanupDiagnostic(error,diagnostic)}
  const token=randomBytes(32).toString("hex");
  try{
    coordinator=createR20Coordinator({authoritySession:session.session,preparedBehavior:prepared.prepared,initialBehaviorState:prepared.initialState,restoredBehaviorState,restoredCommands,entityBindingSha256:sha(bindings.canonicalNpcEntityBindingJson),sessionToken:token,qualificationCoverageRequirement:coverage.requirement,
      onCommit:async(snapshot)=>{await store.append(snapshot)},
      onReset:async(snapshot,{previousSnapshot})=>{if(!previousSnapshot||typeof store.seal!=="function")throw new Error("R20_RESET_SNAPSHOT_MISSING");if(!timelineVerified)await store.seal(previousSnapshot);const ledger=JSON.parse(snapshot.authority.canonicalWorldEventLedgerJson);store=await createR20TimelineStore({npcRunRoot:parsed.npcRunRoot,temporaryRoot,authorityManifestJson:manifestFor(ledger.timeline.id),behaviorPolicyJson:behavior.canonicalNpcBehaviorPolicyJson,entityBindingJson:bindings.canonicalNpcEntityBindingJson,writerLease});timelineVerified=false;qualified=null;await store.append(snapshot)},
      onVerify:async(snapshot,{godotTraceJson,qualificationCoverageEvidence})=>{if(snapshot.commands.length<1||typeof godotTraceJson!=="string"||!qualificationCoverageEvidence)throw new Error("R20_NO_VERIFIED_COMMANDS");validateR20QualificationCoverageEvidence(qualificationCoverageEvidence,coverage.requirement);qualified=await store.finalize(snapshot,{godotTraceJson,qualificationCoverageEvidence});timelineVerified=true;resolveQualified(qualified)},
    });if(!coordinator)throw new Error("R20_COORDINATOR_INVALID");if(!resumedTimeline)await store.append(exportR20Coordinator(coordinator));
    server=await startR20LoopbackCoordinator({coordinator});
  }catch(error){const diagnostic=await releaseWriterAfterFailure();throw diagnostic===null?error:attachR20CleanupDiagnostic(error,diagnostic)}
  const teardownRuntime=async()=>teardownPromise??=(async()=>{
    let firstError=null;
    const attempt=async(operation)=>{try{await operation()}catch(error){firstError??=error}};
    if(child&&child.exitCode===null&&child.signalCode===null)await attempt(async()=>{if(child.kill()!==true&&child.exitCode===null&&child.signalCode===null)throw new Error("R20_GODOT_TERMINATION_FAILED")});
    if(childClosePromise!==null)await attempt(async()=>{
      const timeout=Object.freeze(Object.create(null));let timer=null;
      try{const closed=await Promise.race([childClosePromise,new Promise((resolve)=>{timer=setTimeout(()=>resolve(timeout),5000)})]);if(closed===timeout)throw new Error("R20_GODOT_TERMINATION_TIMEOUT")}finally{if(timer!==null)clearTimeout(timer)}
    });
    if(!waitForTrace&&!timelineVerified&&store&&coordinator)await attempt(async()=>{await store.seal(exportR20Coordinator(coordinator));timelineVerified=true});
    if(server!==null)await attempt(async()=>{const active=server;server=null;await active.close()});
    if(project!==null)await attempt(async()=>{const active=project;project=null;removeRuntimePreviewProject(active.temporaryRoot,{moduleRoot,identity:active.identity})});
    if(firstError!==null)throw firstError;
  })();
  cleanup=async()=>{
    if(cleanupComplete)return;
    if(cleanupPromise!==null)return cleanupPromise;
    cleanupPromise=(async()=>{
      let firstError=null;
      try{await teardownRuntime()}catch(error){firstError=error}
      try{await releaseR20WriterWithRetry(releaseWriter)}catch(error){firstError??=error}
      cleanupComplete=true;
      if(firstError!==null)throw firstError;
    })();
    return cleanupPromise;
  };
  try{
    project=createRuntimePreviewProject({moduleRoot});
    configureGdgsProject(project.projectRoot);
    const fs=await import("node:fs/promises"),runDirectory=await copySpatialPreviewFiles(project.projectRoot,preview,{mkdir,openFile:open,lstat:fs.lstat,realpath:fs.realpath});
    const overlay=path.join(project.projectRoot,"npc_authority_prototype");await mkdir(overlay,{recursive:true});
    await writeFile(path.join(overlay,"entity-bindings.json"),bindings.canonicalNpcEntityBindingJson,{flag:"wx"});
    await writeFile(path.join(overlay,"environment-facts.json"),preview.get("environment-facts.json"),{flag:"wx"});
    await writeFile(path.join(overlay,"spatial-solution.json"),preview.get("spatial-solution.json"),{flag:"wx"});
    if(waitForTrace)await writeFile(path.join(overlay,"qualification-request.json"),QUALIFICATION_REQUEST_JSON,{flag:"wx"});
    if(restoredCommands.length>0)await writeFile(path.join(overlay,"recovery-state.json"),canonicalizeJsonValue({format:"matrix-oasis.npc-godot-recovery",formatVersion:"0.1.0",canonicalization:"matrix-oasis.canonical-json/1",entityBindingSha256:sha(bindings.canonicalNpcEntityBindingJson),commands:restoredCommands}),{flag:"wx"});
    const attempt=randomBytes(12).toString("hex"),importLogPath=path.join(temporaryRoot,`.matrix-oasis-r20-import-${attempt}.log`),godotLogPath=path.join(temporaryRoot,`.matrix-oasis-r20-godot-${attempt}.log`);
    const imported=runGodotCommand({command:godot.command,args:["--headless","--log-file",importLogPath,"--editor","--path",project.projectRoot,"--quit"],cwd:moduleRoot,timeout:120000});assertGodotOutputClean(imported);
    const implementationAtLaunch=await implementationIdentity(),godotBinaryAtLaunch=await stableFileIdentity(godot.command,{allowEmpty:false}),runtimeProjectBefore=await runtimeProjectIdentity(project.projectRoot);
    if(implementationAtLaunch.sha256!==implementationBefore.sha256||godotBinaryAtLaunch.sha256!==godotBinaryBefore.sha256)throw new Error("R20_LAUNCH_IDENTITY_CHANGED");
    const args=[...r14GodotArguments({projectRoot:project.projectRoot,runDirectory,smoke:false})];args[args.indexOf("res://solved_spatial_prototype/solved_spatial_lab.tscn")]="res://npc_authority_prototype/npc_authority_lab.tscn";args.unshift("--log-file",godotLogPath);
    child=spawnProcess(godot.command,args,{cwd:moduleRoot,shell:false,windowsHide:headless,stdio:["ignore","pipe","pipe"],env:childEnvironment(token)});
    let output="",outputBytes=0,readySettled=false,traceSettled=false,readyResolve,readyReject,traceResolve,traceReject,closeResolve,spawnError=null;const outputChunks=[];
    const readyPromise=new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject});
    const tracePromise=waitForTrace?new Promise((resolve,reject)=>{traceResolve=resolve;traceReject=reject}):null;
    childClosePromise=new Promise((resolve)=>{closeResolve=resolve});
    const readyTimer=setTimeout(()=>{if(!readySettled){readySettled=true;readyReject(new Error("R20_GODOT_READY_TIMEOUT"));child.kill()}},120000);
    const traceTimer=waitForTrace?setTimeout(()=>{if(!traceSettled){traceSettled=true;traceReject(new Error("R20_GODOT_TRACE_TIMEOUT"));child.kill()}},180000):null;
    const failMonitor=(error)=>{if(!readySettled){readySettled=true;clearTimeout(readyTimer);readyReject(error)}if(waitForTrace&&!traceSettled){traceSettled=true;clearTimeout(traceTimer);traceReject(error)}};
    const collect=(chunk)=>{
      const bytes=Buffer.from(chunk);outputChunks.push(bytes);outputBytes+=bytes.byteLength;output+=bytes.toString("utf8");
      if(outputBytes>8*1024*1024){failMonitor(new Error("R20_GODOT_OUTPUT_LIMIT"));child.kill();return}
      if(/\b(?:SCRIPT ERROR|ERROR:|R20_GODOT_[A-Z0-9_]+)\b/u.test(output)){failMonitor(new Error("R20_GODOT_OUTPUT_INVALID"));child.kill();return}
      const readyCount=output.split(/\r?\n/u).filter((line)=>line===READY).length;if(readyCount>1){failMonitor(new Error("R20_GODOT_READY_MARKER_INVALID"));child.kill();return}
      if(readyCount===1&&!readySettled){readySettled=true;clearTimeout(readyTimer);readyResolve()}
      if(!waitForTrace)return;
      const traceCount=output.split(TRACE).length-1;if(traceCount>1){failMonitor(new Error("R20_GODOT_TRACE_MARKER_INVALID"));child.kill();return}
      if(traceCount===1&&!traceSettled){const index=output.indexOf(TRACE),end=output.indexOf("\n",index);if(end<0)return;const line=output.slice(index+TRACE.length,end).replace(/\r$/u,"");try{const trace=JSON.parse(line);if(canonicalizeJsonValue(trace)!==line)throw new Error("canonical");traceSettled=true;clearTimeout(traceTimer);traceResolve(Object.freeze({trace,canonicalJson:line}))}catch{failMonitor(new Error("R20_GODOT_TRACE_INVALID"));child.kill()}}
    };
    child.stdout.on("data",collect);child.stderr.on("data",collect);
    child.once("error",(error)=>{spawnError=error;failMonitor(error)});
    child.once("close",(code,signal)=>{
      clearTimeout(readyTimer);if(traceTimer!==null)clearTimeout(traceTimer);
      let completeOutput=null,outputError=spawnError;
      try{completeOutput=text(Buffer.concat(outputChunks));assertGodotOutputClean(completeOutput);if(completeOutput.includes(token)||/\bR20_GODOT_[A-Z0-9_]+\b/u.test(completeOutput))throw new Error("R20_GODOT_OUTPUT_INVALID")}catch(error){outputError??=error}
      const readyCount=completeOutput?.split(/\r?\n/u).filter((line)=>line===READY).length??0,traceCount=(completeOutput?.split(TRACE).length??1)-1;
      if(readyCount!==1||waitForTrace&&traceCount!==1||!waitForTrace&&traceCount!==0)outputError??=new Error(code===0?"R20_GODOT_EVIDENCE_MISSING":"R20_GODOT_EXIT");
      if(outputError!==null)failMonitor(outputError);closeResolve(Object.freeze({code,signal,output:completeOutput,error:outputError}));
    });
    await readyPromise;
    if(waitForTrace){
      const observed=await tracePromise,closed=await waitForR20GodotCloseAfterTrace({closePromise:childClosePromise,terminate:()=>child.kill()});if(closed.error!==null||closed.code!==0||closed.signal!==null)throw closed.error??new Error("R20_GODOT_EXIT_INVALID");
      const implementationAfter=await implementationIdentity(),godotBinaryAfter=await stableFileIdentity(godot.command,{allowEmpty:false}),runtimeProjectAfter=await runtimeProjectIdentity(project.projectRoot);
      if(implementationAfter.sha256!==implementationBefore.sha256||godotBinaryAfter.sha256!==godotBinaryBefore.sha256||runtimeProjectAfter.manifestJson!==runtimeProjectBefore.manifestJson)throw new Error("R20_EXIT_IDENTITY_CHANGED");
      const finalized=await qualifiedPromise;if(finalized!==qualified||typeof store.prepareQualification!=="function"||typeof store.activateQualification!=="function")throw new Error("R20_QUALIFICATION_PUBLICATION_UNAVAILABLE");
      const godotLogBytes=await readFile(godotLogPath);validateProcessLog(godotLogBytes,token);
      const processLog=await publishProcessLog(godotLogBytes);
      const receiptJson=await createQualificationReceipt({qualified:finalized,godotTraceJson:observed.canonicalJson,godotLogBytes,exitCode:closed.code,implementationSha256:implementationBefore.sha256,godotBinarySha256:godotBinaryBefore.sha256,runtimeProjectManifestJson:runtimeProjectBefore.manifestJson}),expectedImplementationSha256=implementationBefore.sha256,expectedGodotBinarySha256=godotBinaryBefore.sha256;
      await teardownRuntime();
      const preparedQualification=await store.prepareQualification(receiptJson,{expectedImplementationSha256,expectedGodotBinarySha256,processLogBytes:godotLogBytes,runtimeProjectManifestJson:runtimeProjectBefore.manifestJson,runtimeGamePackJson,runtimeReceiptJson,authorityPolicyJson:policyJson});
      const pendingAudit=await auditR20TimelineStore({npcRunRoot:parsed.npcRunRoot,temporaryRoot,writerLease,expectedImplementationSha256,expectedGodotBinarySha256,targetManifestId:finalized.manifestId}),pendingMatches=pendingAudit.timelines.filter((item)=>item.manifestId===finalized.manifestId&&item.status==="qualified-pending-current");
      if(preparedQualification.manifestId!==finalized.manifestId||preparedQualification.prepared!==true||preparedQualification.qualified!==false||pendingMatches.length!==1)throw new Error("R20_QUALIFICATION_PUBLICATION_UNAVAILABLE");
      const published=await store.activateQualification({expectedImplementationSha256,expectedGodotBinarySha256});
      await releaseR20WriterWithRetry(releaseWriter);
      cleanupComplete=true;
      return Object.freeze({ok:true,trace:observed.trace,qualified:finalized,published,receiptJson,processLog,sourceRunId,qualificationRunId:selected.qualificationRunId,bindingCount:bindings.npcEntityBindings.bindings.length})
    }
    return Object.freeze({ok:true,child,server,project,qualifiedPromise,cleanup,sourceRunId,qualificationRunId:selected.qualificationRunId,bindingCount:bindings.npcEntityBindings.bindings.length,qualificationStatus:"unqualified-observation",qualificationBasisManifestSha256:qualificationBasis.qualificationBasisManifestSha256});
  }catch(error){
    void qualifiedPromise.catch(()=>{});let cleanupDiagnostic=null;
    try{if(cleanup!==null)await cleanup();else cleanupDiagnostic=await releaseWriterAfterFailure()}catch{cleanupDiagnostic="R20_QUALIFICATION_CLEANUP_FAILED"}
    throw cleanupDiagnostic===null?error:attachR20CleanupDiagnostic(error,cleanupDiagnostic);
  }
}

export async function runR20QualificationCli(args){const parsed=parseR20RunArguments(args,temporaryRoot);return await launchR20Bridge(parsed,{headless:true,waitForTrace:true})}
if(fileURLToPath(import.meta.url)===path.resolve(process.argv[1]??"")){try{const result=await runR20QualificationCli(process.argv.slice(2));process.stdout.write(`R20_GODOT_ENTITY_BRIDGE_QUALIFIED ${JSON.stringify({sourceRunId:result.sourceRunId,qualificationRunId:result.qualificationRunId,bindingCount:result.bindingCount,medianFpsMilli:result.trace.performance.medianFpsMilli,processLogSha256:result.processLog.sha256})}\n`)}catch(error){process.stderr.write(`${error?.message??"R20_QUALIFICATION_INTERNAL_ERROR"}${error?.cleanupDiagnostic?` cleanup=${error.cleanupDiagnostic}`:""}\n`);process.exitCode=2}}
