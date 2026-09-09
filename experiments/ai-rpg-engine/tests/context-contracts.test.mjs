import assert from "node:assert/strict";
import test from "node:test";
import { CONTEXT_FORMATS as F, validateContextProfile as profileCheck, validateContextInput as inputCheck, validateContextReceipt as receiptCheck, validatePreparedTurn as preparedCheck } from "../context/index.mjs";
import { canonicalJson, computeGenerationInputSha256 } from "../runtime/contracts.mjs";
import { baseRuntimeFixture, sha256 } from "./runtime-fixtures.mjs";
const hash = (x) => sha256(canonicalJson(x).value);
function fixture() {
  const f = baseRuntimeFixture(), w = f.cardPackage.defaults.worldRef, o = f.cardPackage.defaults.openingRef;
  const profile = { format: F.profile, formatVersion: "0.1.0", profile: { id: "profile.test", version: "0.1.0" }, cardPackage: f.session.resources.cardPackage, hostTemplate: { id: "host.test", version: "0.1.0", sha256: "a".repeat(64) }, budget: { inputLimit: 10000, loreLimit: 3000, historyLimit: 4000, outputLimit: 512 }, scenes: [{ id: "scene.test", worldRef: w, openingRef: o, requiredResourceRefs: [] }], aliases: [], loreRules: [] };
  const input = { format: F.input, formatVersion: "0.1.0", ...f, profile, sceneRef: "scene.test", resourceRefs: [], generationId: "generation.test", exchangeId: "exchange.test", expectedRevision: 0, input: { kind: "query", text: "状态" }, modelId: "test/model", settings: { temperature: 0, maxTokens: 512 } };
  const bindings = { sessionId: f.session.sessionId, revision: 0, generationId: input.generationId, exchangeId: input.exchangeId, cardPackageSha256: hash(f.cardPackage), playerSetupSha256: hash(f.playerSetup), profileSha256: hash(profile), hostTemplateSha256: profile.hostTemplate.sha256 };
  const request = { sessionId: input.session.sessionId, generationId: input.generationId, exchangeId: input.exchangeId, expectedRevision: 0, input: input.input, messages: [{ role: "user", content: "观察" }], modelId: input.modelId, settings: input.settings };
  const receipt = { format: F.receipt, formatVersion: "0.1.0", bindings, evidenceKind: "offline", compilerVersion: "0.4.0", messagesSha256: hash(request.messages), generationInputSha256: computeGenerationInputSha256(request,input.session,sha256).value, measurement: { method: "utf8_bytes_with_overhead", accuracy: "estimate", counterRef: null, input: 120, required: 100, lore: 0, history: 0, overhead: 20, inputLimit: 10000, outputLimit: 512 }, loreDecisions: [], history: { includedExchangeIds: [], excludedTurnCount: 0 }, sourceRefs: [] };
  const prepared = { format: F.preparedTurn, formatVersion: "0.1.0", bindings, request, receipt };
  return { input, profile, prepared, receipt, trust: { input, hash: sha256, hostTemplate: profile.hostTemplate } };
}
function rule(entryRef = "worldbook.gu-basics") { return { entryRef, worldRefs: ["world.reverend-insanity"], required: false, priority: 0, trigger: { sceneRefs: [], resourceRefs: [], allKeywords: [], anyKeywords: [], notKeywords: [], stateConditions: [] }, conflictGroup: null, replaces: [] }; }
test("valid bound profile input and prepared request reuse complete five-talent player", () => {
  const f=fixture(); assert.equal(f.input.playerSetup.talents.length,5); assert.equal(profileCheck(f.profile,f.input.cardPackage).valid,true); assert.equal(inputCheck(f.input,{hash:sha256}).valid,true); assert.equal(receiptCheck(f.receipt).valid,true); assert.equal(preparedCheck(f.prepared,f.trust).valid,true);
});
test("profile rejects missing worlds openings duplicate scenes and type-wrong aliases", () => {
  for(const mutate of [f=>f.profile.scenes[0].worldRef="world.missing",f=>f.profile.scenes[0].openingRef="opening.missing",f=>f.profile.scenes.push(structuredClone(f.profile.scenes[0])),f=>f.profile.aliases.push({text:"蛊",kind:"talent",worldRef:null,resourceRef:"worldbook.gu-basics"})]){const f=fixture();mutate(f);assert.equal(profileCheck(f.profile,f.input.cardPackage).valid,false);}
});
test("aliases with the same kind scope and text cannot bind ambiguously",()=>{const f=fixture();const a={text:"蛊",kind:"worldbook",worldRef:f.profile.scenes[0].worldRef,resourceRef:"worldbook.gu-basics"};f.profile.aliases=[a,structuredClone(a)];assert.equal(profileCheck(f.profile,f.input.cardPackage).diagnostics[0].code,"CONTEXT_ALIAS_AMBIGUOUS");});
test("lore validates exact references declared state types and replacement closure",()=>{
  for(const mutate of [r=>r.entryRef="lore.missing",r=>r.trigger.sceneRefs=["scene.missing"],r=>r.trigger.stateConditions=[{fieldRef:"state.player-alert",operator:"eq",value:"true"}],r=>r.replaces=["lore.missing"],r=>{r.conflictGroup="conflict.test";r.replaces=[r.entryRef];}]){const f=fixture();const r=rule();mutate(r);f.profile.loreRules=[r];assert.equal(profileCheck(f.profile,f.input.cardPackage).valid,false);}
  const f=fixture();f.profile.loreRules=[rule()];assert.equal(profileCheck(f.profile,f.input.cardPackage).valid,true);
});
test("input rejects stale revision hash drift commands and undeclared input resources",()=>{
  for(const mutate of [f=>f.input.expectedRevision=1,f=>f.input.profile.cardPackage={...f.input.profile.cardPackage,sha256:"b".repeat(64)},f=>f.input.input={kind:"command",text:"看",commandRef:"command.missing"},f=>f.input.resourceRefs=["resource.missing"],f=>f.input.sceneRef="scene.missing",f=>f.input.settings.maxTokens=513]){const f=fixture();mutate(f);assert.equal(inputCheck(f.input,{hash:sha256}).valid,false);}
});
test("hash service is explicit synchronous and failures return stable diagnostics",()=>{const f=fixture();for(const fn of [undefined,()=>{throw Error("secret");},()=>Promise.resolve("a".repeat(64)),()=>"bad"]){const r=inputCheck(f.input,{hash:fn});assert.equal(r.valid,false);assert.equal(JSON.stringify(r).includes("secret"),false);}});
test("prepared request rejects binding and message tampering",()=>{
  for(const mutate of [f=>f.prepared.bindings.revision=1,f=>f.prepared.request.messages[0].content="篡改",f=>f.prepared.request.modelId="other/model",f=>f.prepared.receipt.messagesSha256="b".repeat(64),f=>f.prepared.request.input={kind:"action",text:"跑"},f=>f.trust.hostTemplate={...f.trust.hostTemplate,sha256:"b".repeat(64)}]){const f=fixture();mutate(f);assert.equal(preparedCheck(f.prepared,f.trust).valid,false);}
});
test("receipt rejects fake exact byte measurement totals and duplicate decisions",()=>{
  for(const mutate of [r=>r.measurement.accuracy="exact",r=>r.measurement.input=999,r=>r.measurement.inputLimit=1,r=>r.loreDecisions=[{entryRef:"entry.test",disposition:"included",sourceRefs:[]},{entryRef:"entry.test",disposition:"included",sourceRefs:[]}]]){const f=fixture();mutate(f.receipt);assert.equal(receiptCheck(f.receipt).valid,false);}
});
test("host entries cannot appear in a bound public receipt",()=>{
  const f=fixture();f.input.cardPackage.resources.worldbookEntries[0].visibility="host";
  const h=hash(f.input.cardPackage);f.input.session.resources.cardPackage.sha256=h;f.profile.cardPackage.sha256=h;f.prepared.bindings.cardPackageSha256=h;f.prepared.bindings.profileSha256=hash(f.profile);f.receipt.generationInputSha256=computeGenerationInputSha256(f.prepared.request,f.input.session,sha256).value;
  f.receipt.loreDecisions=[{entryRef:"worldbook.gu-basics",disposition:"included",sourceRefs:["source.synthetic-player-card"]}];
  assert.equal(preparedCheck(f.prepared,f.trust).diagnostics[0].code,"CONTEXT_RECEIPT_VISIBILITY");
});
test("uncommitted history claims and foreign source references fail",()=>{for(const mutate of [f=>f.receipt.history.includedExchangeIds=["exchange.pending"],f=>f.receipt.sourceRefs=["source.missing"]]){const f=fixture();mutate(f);assert.equal(preparedCheck(f.prepared,f.trust).valid,false);}});
test("semantic validators are deterministic and never mutate caller input",()=>{const f=fixture();const before=JSON.stringify(f);assert.equal(preparedCheck(f.prepared,f.trust).valid,true);assert.equal(JSON.stringify(f),before);f.input.expectedRevision=3;assert.deepEqual(inputCheck(f.input,{hash:sha256}),inputCheck(f.input,{hash:sha256}));});
test("replacement traversal rejects a later dangling target without throwing",()=>{
  const f=fixture(), entry=f.input.cardPackage.resources.worldbookEntries[0];
  f.input.cardPackage.resources.worldbookEntries.push({...structuredClone(entry),id:"lore.second"});
  const a=rule(),b=rule("lore.second");a.conflictGroup=b.conflictGroup="conflict.test";a.replaces=[b.entryRef];b.replaces=["lore.missing"];f.profile.loreRules=[a,b];
  assert.equal(profileCheck(f.profile,f.input.cardPackage).diagnostics[0].code,"CONTEXT_REPLACEMENT_REFERENCE");
  b.replaces=[a.entryRef];assert.equal(profileCheck(f.profile,f.input.cardPackage).diagnostics[0].code,"CONTEXT_REPLACEMENT_CYCLE");
});
test("required host lore is rejected and malformed session cannot be prepared",()=>{
  const f=fixture();f.input.cardPackage.resources.worldbookEntries[0].visibility="host";const r=rule();r.required=true;f.profile.loreRules=[r];assert.equal(profileCheck(f.profile,f.input.cardPackage).diagnostics[0].code,"CONTEXT_HOST_REQUIRED");
  const other=fixture();other.input.session.pending={generationId:"generation.uncommitted",exchangeId:"exchange.uncommitted"};assert.equal(inputCheck(other.input,{hash:sha256}).valid,false);
});
