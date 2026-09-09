import assert from "node:assert/strict";
import test from "node:test";
import { compileContext } from "../context/compiler.mjs";
import { createPreparedRuntime } from "../tooling/context-runtime.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";
import { baseRuntimeFixture, sha256 } from "./runtime-fixtures.mjs";

const good = (value) => ({ valid: true, diagnostics: [], value });
class MemoryStore {
  constructor() { this.sessions = new Map(); }
  async read(id) { return good(this.sessions.has(id) ? structuredClone(this.sessions.get(id)) : null); }
  async write(session, { expectedRevision }) { const prior=this.sessions.get(session.sessionId); if(expectedRevision===null ? prior : !prior || prior.revision!==expectedRevision) return {valid:false,diagnostics:[]}; this.sessions.set(session.sessionId,structuredClone(session)); return good(structuredClone(session)); }
}
const digest = (value) => sha256(canonicalJson(value).value);
function setup(handler) {
  const base=baseRuntimeFixture(), hostTemplate={id:"host.test",version:"0.1.0",content:"Return the strict exchange object."};
  const profile={format:"modelmirror.ai-rpg.context-profile",formatVersion:"0.1.0",profile:{id:"profile.test",version:"0.1.0"},cardPackage:base.session.resources.cardPackage,hostTemplate:{id:hostTemplate.id,version:hostTemplate.version,sha256:digest(hostTemplate)},budget:{inputLimit:64000,loreLimit:8000,historyLimit:8000,outputLimit:512},scenes:[{id:"scene.test",worldRef:base.cardPackage.defaults.worldRef,openingRef:base.cardPackage.defaults.openingRef,requiredResourceRefs:[]}],aliases:[],loreRules:[]};
  let calls=0;
  const adapter={evidenceKind:"mock",async generate(request,options){calls++; return handler(request,options,base.cardPackage);}};
  const store=new MemoryStore(), built=createPreparedRuntime({store,modelAdapter:adapter,hash:sha256,hostTemplate}); assert.equal(built.valid,true);
  const input={format:"modelmirror.ai-rpg.context-input",formatVersion:"0.1.0",...base,profile,sceneRef:"scene.test",resourceRefs:[],generationId:"generation.next",exchangeId:"exchange.next",expectedRevision:0,input:{kind:"query",text:"What is known?"},modelId:"model.test",settings:{temperature:0,maxTokens:512}};
  return {runtime:built.value,input,store,hostTemplate,get calls(){return calls;}};
}
const exchange=(request,card,proposal={narrative:"Known facts only.",suggestedActions:[],informationModules:[],stateProposals:[],uncertainties:[]})=>({format:"modelmirror.ai-rpg.turn-exchange",formatVersion:"0.1.0",exchangeId:request.exchangeId,cardPackageRef:{id:card.package.id,version:card.package.version},input:request.input,proposal});
const completed=(text)=>good({status:"succeeded",outcome:"completed",dispatched:true,text,observedModel:null,serverReceipt:null,cancellation:{requested:false,clientAborted:false,upstreamConfirmed:null},usage:{input:null,output:null,total:null}});

test("recompiles caller context, accepts a full P08 exchange, and leaves query state unchanged until explicit commit",async()=>{
 let raw; const f=setup(async(request,_options,card)=>{raw=JSON.stringify(exchange(request,card)); return completed(raw);});
 f.input.session=await f.runtime.createSession({sessionId:f.input.session.sessionId,cardPackage:f.input.cardPackage,playerSetup:f.input.playerSetup}).then(r=>r.value);
 const prepared=compileContext(f.input,{hash:sha256,hostTemplate:f.hostTemplate}).value;
 const generated=await f.runtime.generatePreparedTurn({contextInput:f.input,prepared}); assert.equal(generated.valid,true,JSON.stringify(generated));
 assert.equal(generated.value.generation.status,"pending"); assert.equal(f.calls,1); assert.equal(generated.value.bridgeReceipt.rawTurnExchangeSha256,sha256(raw));
 const before=structuredClone(generated.value.session.state);
 const committed=await f.runtime.commitTurn({format:"modelmirror.ai-rpg.turn-commit",formatVersion:"0.1.0",sessionId:f.input.session.sessionId,generationId:f.input.generationId,exchangeId:f.input.exchangeId,expectedRevision:generated.value.session.revision,acceptedStateFields:[]});
 assert.equal(committed.valid,true); assert.deepEqual(committed.value.state,before);
});

test("prepared tamper, stale session and unresolved pending fail before another dispatch",async()=>{
 const f=setup(async(request,_options,card)=>completed(JSON.stringify(exchange(request,card))));
 f.input.session=(await f.runtime.createSession({sessionId:f.input.session.sessionId,cardPackage:f.input.cardPackage,playerSetup:f.input.playerSetup})).value;
 const prepared=compileContext(f.input,{hash:sha256,hostTemplate:f.hostTemplate}).value, tampered=structuredClone(prepared); tampered.request.messages.at(-1).content="tampered";
 assert.equal((await f.runtime.generatePreparedTurn({contextInput:f.input,prepared:tampered})).valid,false); assert.equal(f.calls,0);
 const generated=await f.runtime.generatePreparedTurn({contextInput:f.input,prepared}); assert.equal(generated.valid,true); assert.equal(f.calls,1);
 const repeated=await f.runtime.generatePreparedTurn({contextInput:f.input,prepared}); assert.equal(repeated.valid,true); assert.equal(repeated.value.generation.generationId,f.input.generationId); assert.equal(f.calls,1);
 const fresh=structuredClone(f.input); fresh.session=generated.value.session; fresh.expectedRevision=fresh.session.revision; fresh.generationId="generation.blocked"; fresh.exchangeId="exchange.blocked";
 assert.equal((await f.runtime.generatePreparedTurn({contextInput:fresh})).valid,false); assert.equal(f.calls,1);
});

test("invalid, semantically invalid, mismatched and cancelled full exchanges never become pending and dispatch only once",async()=>{
 for(const mode of ["extra","state","mismatch","cancel"]){
  const f=setup(async(request,options,card)=>{const value=exchange(request,card); if(mode==="extra") return completed(JSON.stringify(value)+" trailing"); if(mode==="state") value.proposal.stateProposals=[{fieldRef:"state.scene-note",proposedValue:"forbidden query change",rationale:"invalid"}]; if(mode==="mismatch") value.exchangeId="exchange.other"; if(mode==="cancel"){await options.onText("draft only"); await new Promise(resolve=>options.signal.addEventListener("abort",resolve,{once:true})); return good({status:"cancelled",outcome:"cancelled",dispatched:true,text:"",observedModel:null,serverReceipt:null,cancellation:{requested:true,clientAborted:true,upstreamConfirmed:null},usage:{input:null,output:null,total:null}});} return completed(JSON.stringify(value));});
  f.input.session=(await f.runtime.createSession({sessionId:f.input.session.sessionId,cardPackage:f.input.cardPackage,playerSetup:f.input.playerSetup})).value;
  let cancellation;
  const options=mode==="cancel"?{onEvent(event){if(event.type==="draft"&&!cancellation)cancellation=f.runtime.cancelGeneration({sessionId:event.sessionId,generationId:event.generationId,expectedRevision:event.revision});}}:{};
  const result=await f.runtime.generatePreparedTurn({contextInput:f.input},options); if(cancellation) assert.equal((await cancellation).valid,true); assert.equal(result.valid,false); assert.equal(f.calls,1);
  const session=(await f.runtime.readSession({sessionId:f.input.session.sessionId,cardPackage:f.input.cardPackage,playerSetup:f.input.playerSetup})).value;
  assert.equal(session.pending,null); assert.equal(session.turns.length,0); assert.equal(session.state.some((field,index)=>field.value!==f.input.session.state[index].value),false);
 }
});
