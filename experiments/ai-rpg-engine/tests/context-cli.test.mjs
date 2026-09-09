import assert from "node:assert/strict";
import test from "node:test";
import { createContextCommandDriver } from "../tooling/context-cli.mjs";
import { createPreparedRuntime } from "../tooling/context-runtime.mjs";
import { loadRpg04ApprovedHost } from "../tooling/context-host.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";
import { baseRuntimeFixture,sha256 } from "./runtime-fixtures.mjs";
class Store{value=null;async read(){return {valid:true,diagnostics:[],value:structuredClone(this.value)}}async write(v){this.value=structuredClone(v);return {valid:true,diagnostics:[],value:structuredClone(v)}}}
class Artifacts{files=new Map();async writeExclusive(name,text){if(this.files.has(name))return {valid:false};this.files.set(name,text);return {valid:true}}}
const digest=x=>sha256(canonicalJson(x).value);
test("single process prepares generates explicitly commits restores and privately exports a query",async()=>{
 const f=baseRuntimeFixture(),host=loadRpg04ApprovedHost().value,store=new Store(),artifacts=new Artifacts();
 const adapter={evidenceKind:"mock",async generate(request){const exchange={format:"modelmirror.ai-rpg.turn-exchange",formatVersion:"0.1.0",exchangeId:request.exchangeId,cardPackageRef:f.playerSetup.cardPackageRef,input:request.input,proposal:{narrative:"A private replayable answer.",suggestedActions:[{id:"suggestion.one",label:"Unselected",inputKind:"action",text:"Unselected"}],informationModules:[],stateProposals:[],uncertainties:[]}};return {valid:true,diagnostics:[],value:{status:"succeeded",outcome:"completed",dispatched:true,text:JSON.stringify(exchange),observedModel:null,serverReceipt:null,cancellation:{requested:false,clientAborted:false,upstreamConfirmed:null},usage:{input:null,output:null,total:null}}}}};
 const runtime=createPreparedRuntime({store,modelAdapter:adapter,hash:sha256,hostTemplate:host.hostTemplate}).value;
 const profile={format:"modelmirror.ai-rpg.context-profile",formatVersion:"0.1.0",profile:{id:"profile.cli",version:"0.1.0"},cardPackage:f.session.resources.cardPackage,hostTemplate:host.binding,budget:{inputLimit:64000,loreLimit:8000,historyLimit:8000,outputLimit:2048},scenes:[{id:"scene.cli",worldRef:f.cardPackage.defaults.worldRef,openingRef:f.cardPackage.defaults.openingRef,requiredResourceRefs:[]}],aliases:[],loreRules:[]};
 const contextInput={format:"modelmirror.ai-rpg.context-input",formatVersion:"0.1.0",...f,profile,sceneRef:"scene.cli",resourceRefs:[],generationId:"generation.cli",exchangeId:"exchange.cli",expectedRevision:0,input:{kind:"query",text:"What is known?"},modelId:"model.mock",settings:{temperature:0,maxTokens:2048}};
 const driver=createContextCommandDriver({runtime,hash:sha256,artifactStore:artifacts}).value;
 const created=await driver.runContextCommand({requestId:"request.create",operation:"create",input:{sessionId:f.session.sessionId,cardPackage:f.cardPackage,playerSetup:f.playerSetup}});assert.deepEqual(created.value,{operation:"create",sessionId:f.session.sessionId,revision:0,pending:false,turnCount:0});
 const prep=await driver.runContextCommand({requestId:"request.prepare",operation:"prepare",input:{contextInput}});assert.equal(prep.valid,true,JSON.stringify(prep));
 const generated=await driver.runContextCommand({requestId:"request.generate",operation:"generate",input:{contextInput}});assert.equal(generated.valid,true,JSON.stringify(generated));assert.equal(generated.value.pending,true);
 const pending=store.value;assert.equal(pending.turns.length,0);assert.equal(JSON.stringify(generated.value).includes("private replayable"),false);
 const committed=await driver.runContextCommand({requestId:"request.commit",operation:"commit",input:{sessionId:f.session.sessionId,request:{format:"modelmirror.ai-rpg.turn-commit",formatVersion:"0.1.0",sessionId:f.session.sessionId,generationId:"generation.cli",exchangeId:"exchange.cli",expectedRevision:pending.revision,acceptedStateFields:[]}}});assert.equal(committed.valid,true,JSON.stringify(committed));
 const restored=await driver.runContextCommand({requestId:"request.read",operation:"read",input:{sessionId:f.session.sessionId}});assert.deepEqual(restored.value,{operation:"read",sessionId:f.session.sessionId,revision:3,pending:false,turnCount:1});
 const exported=await driver.runContextCommand({requestId:"request.export",operation:"export",input:{sessionId:f.session.sessionId}});assert.equal(exported.valid,true);const body=JSON.parse(artifacts.files.get(exported.value.artifactPath));assert.equal(body.turns[0].exchange.proposal.narrative,"A private replayable answer.");assert.equal(body.turns[0].acceptedStateFields.length,0);
 assert.equal([...artifacts.files.keys()].length,5);assert.equal((await driver.runContextCommand({requestId:"request.export",operation:"read",input:{sessionId:f.session.sessionId}})).diagnostics[0].code,"RPG04_CLI_REQUEST_DUPLICATE");
 const restarted=createContextCommandDriver({runtime,hash:sha256,artifactStore:new Artifacts()}).value;
 assert.equal((await restarted.runContextCommand({requestId:"request.restart-read",operation:"read",input:{sessionId:f.session.sessionId}})).diagnostics[0].code,"RPG04_CLI_SESSION_UNKNOWN");
 assert.equal((await restarted.runContextCommand({requestId:"request.register",operation:"register",input:{sessionId:f.session.sessionId,cardPackage:f.cardPackage,playerSetup:f.playerSetup}})).valid,true);
 assert.equal((await restarted.runContextCommand({requestId:"request.restart-read2",operation:"read",input:{sessionId:f.session.sessionId}})).value.turnCount,1);
 const stale=structuredClone(contextInput);stale.expectedRevision=0;
 assert.equal((await restarted.runContextCommand({requestId:"request.stale",operation:"prepare",input:{contextInput:stale}})).diagnostics[0].code,"RPG04_CLI_SESSION_DRIFT");
 const failed=await restarted.runContextCommand({requestId:"request.bad-commit",operation:"commit",input:{sessionId:f.session.sessionId,request:{format:"bad",privateText:"DO-NOT-LEAK"}}});assert.equal(failed.valid,false);assert.equal(failed.value,null);assert.equal(JSON.stringify(failed).includes("DO-NOT-LEAK"),false);
});
