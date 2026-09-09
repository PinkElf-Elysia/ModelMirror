import { compileContext } from "../context/compiler.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";
import { loadRpg04ApprovedHost } from "./context-host.mjs";

const OPS=new Set(["create","register","prepare","generate","commit","read","export"]), ID=/^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$/u;
const fail=code=>Object.freeze({valid:false,diagnostics:Object.freeze([{phase:"context-cli",severity:"error",code,path:""}]),value:null});
const ok=value=>Object.freeze({valid:true,diagnostics:Object.freeze([]),value:Object.freeze(value)});
const clone=value=>{const c=canonicalJson(value);return c.valid?JSON.parse(c.value):null;};
const summary=session=>({sessionId:session?.sessionId??null,revision:session?.revision??null,pending:session?.pending===null?false:session?.pending?true:null,turnCount:Array.isArray(session?.turns)?session.turns.length:null});
const safeFailure=report=>Object.freeze({valid:false,diagnostics:Object.freeze((Array.isArray(report?.diagnostics)?report.diagnostics:[]).slice(0,64).map(d=>Object.freeze({phase:typeof d?.phase==="string"?d.phase:"context-cli",severity:d?.severity==="warning"?"warning":"error",code:/^[A-Z0-9_]{1,128}$/u.test(d?.code)?d.code:"RPG04_CLI_FAILED",path:""}))),value:null});
const artifactName=(requestId,kind)=>`${requestId}.${kind}.json`;

/** Single-process RPG04 command driver. Full artifacts leave only through an exclusive private writer port. */
export function createContextCommandDriver({runtime,hash,artifactStore}={}) {
 const host=loadRpg04ApprovedHost();
 if(!host.valid||host.value.activation.ready!==true||!runtime||typeof hash!=="function"||!artifactStore||typeof artifactStore.writeExclusive!=="function")return fail("RPG04_CLI_CONFIG_INVALID");
 const prepared=new Map(),seen=new Set(),cardBySession=new Map(),playerBySession=new Map();
 async function write(requestId,kind,value){try{const name=artifactName(requestId,kind),r=await artifactStore.writeExclusive(name,canonicalJson(value).value);return r?.valid===true?name:null;}catch{return null;}}
 return ok(Object.freeze({
  async runContextCommand(command){
   const c=clone(command);if(!c||Object.keys(c).sort().join(",")!=="input,operation,requestId"||!ID.test(c.requestId)||!OPS.has(c.operation)||!c.input||typeof c.input!=="object"||Array.isArray(c.input))return fail("RPG04_CLI_COMMAND_INVALID");
   if(seen.has(c.requestId))return fail("RPG04_CLI_REQUEST_DUPLICATE");seen.add(c.requestId);
   try{
    if(c.operation==="create"||c.operation==="register"){
     const {sessionId,cardPackage,playerSetup}=c.input;if(!ID.test(sessionId)||!clone(cardPackage)||!clone(playerSetup))return fail("RPG04_CLI_RESOURCE_BINDING_INVALID");
     const result=c.operation==="create"?await runtime.createSession({sessionId,cardPackage,playerSetup}):await runtime.readSession({sessionId,cardPackage,playerSetup});
     if(!result.valid)return safeFailure(result);cardBySession.set(sessionId,clone(cardPackage));playerBySession.set(sessionId,clone(playerSetup));return ok({operation:c.operation,...summary(result.value)});
    }
    if(c.operation==="prepare"||c.operation==="generate"){
     const input=c.input.contextInput;if(!input)return fail("RPG04_CLI_CONTEXT_INPUT_REQUIRED");
     if(input.profile?.hostTemplate?.sha256!==host.value.binding.sha256||input.settings?.maxTokens>2048)return fail("RPG04_CLI_TRUST_POLICY");
     const current=await runtime.readSession({sessionId:input.session?.sessionId,cardPackage:input.cardPackage,playerSetup:input.playerSetup});if(!current.valid)return safeFailure(current);
     if(canonicalJson(current.value).value!==canonicalJson(input.session).value||input.expectedRevision!==current.value.revision)return fail("RPG04_CLI_SESSION_DRIFT");
     const bound=input;
     const compiled=compileContext(bound,{hash,hostTemplate:host.value.hostTemplate});if(!compiled.valid)return compiled;
     const key=compiled.value.request.generationId;prepared.set(key,{contextInput:bound,prepared:compiled.value});cardBySession.set(bound.session.sessionId,clone(bound.cardPackage));playerBySession.set(bound.session.sessionId,clone(bound.playerSetup));
     const preparedPath=await write(c.requestId,"prepared",compiled.value);if(!preparedPath)return fail("RPG04_CLI_ARTIFACT_WRITE_FAILED");
     if(c.operation==="prepare")return ok({operation:"prepare",sessionId:bound.session.sessionId,generationId:key,exchangeId:bound.exchangeId,preparedPath});
     const generated=await runtime.generatePreparedTurn({contextInput:bound,prepared:compiled.value});if(!generated.valid)return safeFailure(generated);
     const generationPath=await write(c.requestId,"generation",generated.value);if(!generationPath)return fail("RPG04_CLI_ARTIFACT_WRITE_FAILED");
     return ok({operation:"generate",...summary(generated.value.session),generationId:key,exchangeId:bound.exchangeId,preparedPath,generationPath,evidenceKind:generated.value.bridgeReceipt?.evidenceKind??null});
    }
    const sessionId=c.input.sessionId,card=cardBySession.get(sessionId),player=playerBySession.get(sessionId);if(!card||!player)return fail("RPG04_CLI_SESSION_UNKNOWN");
    if(c.operation==="commit"){
     const result=await runtime.commitTurn(c.input.request);if(!result.valid)return safeFailure(result);
     const path=await write(c.requestId,"commit",result.value);if(!path)return fail("RPG04_CLI_ARTIFACT_WRITE_FAILED");
     return ok({operation:"commit",...summary(result.value),artifactPath:path});
    }
    const result=await runtime.readSession({sessionId,cardPackage:card,playerSetup:player});if(!result.valid)return safeFailure(result);
    if(c.operation==="read")return ok({operation:"read",...summary(result.value)});
    const path=await write(c.requestId,"export",result.value);if(!path)return fail("RPG04_CLI_ARTIFACT_WRITE_FAILED");
    return ok({operation:"export",...summary(result.value),artifactPath:path});
   }catch{return fail("RPG04_CLI_FAILED");}
  }
 }));
}
