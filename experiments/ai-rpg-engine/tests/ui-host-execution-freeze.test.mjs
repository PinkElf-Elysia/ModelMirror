import { PROTOCOL_AUTHORIZATION, REVIEWED_PROTOCOL_AUTHORIZATION } from '../ui-host/protocol-ledger.mjs';
import { KEYED_PROTOCOL_VERSION, KEYED_PROTOCOL_TEXT_SHA256, REVIEWED_PROTOCOL_VERSION, REVIEWED_SEMANTICS_SHA256 } from '../ui-host/keyed-protocol.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createExecutionFreeze } from '../ui-host/execution-freeze.mjs';
import { hashText, hashValue } from '../ui-host/setup.mjs';
const work = fileURLToPath(new URL('../.rpg04-work/', import.meta.url));
async function fixture(t) {
 const root = await fs.mkdtemp(path.join(work,'rpg05-freeze-'));
 t.after(async()=>{const real=await fs.realpath(root); assert.equal(path.dirname(real),await fs.realpath(work));assert.ok(path.basename(real).startsWith('rpg05-freeze-'));await fs.rm(real,{recursive:true});});
 await fs.writeFile(path.join(root,'source.mjs'),'frozen');
 const initial={neutral:true}, input={kind:'action',text:'瑙傚療'}, manifest={format:'modelmirror.ai-rpg.rpg05-execution-freeze/0.5.0',base:'81fc14f6e0dada2447a63c1e532ee55b1f914ded',branch:'codex/ai-rpg-rpg05-ui',modelId:'gpt-5.6-luna',baseUrl:'http://127.0.0.1:18305',maxTokens:4096,temperature:0,maxDispatches:8,automaticRetry:false,files:[{path:'source.mjs',sha256:hashText('frozen')}],gates:Object.fromEntries(['offline','mock','agentUi','prototype'].map(g=>[g,{passed:true,sha256:'a'.repeat(64)}])),journeys:['gu','minecraft'].map(world=>({id:'journey.'+world,world,initialSha256:hashValue(initial),turns:[{input},{input:{kind:'speech',text:'浣犲ソ'}},{input:{kind:'query',text:'鐜扮姸'}}]}))};
 return {root,initial,input,manifest,expectedSha256:hashValue(manifest)};
}
test('exact approved neutral journey/input is admitted; changes and regeneration are rejected',async t=>{
 const f=await fixture(t),guard=await createExecutionFreeze(f),args={sessionId:'journey.gu',initial:f.initial,input:f.input,turnCount:0,kind:'generate'};
 assert.equal((await guard.admit(args)).maxTokens,4096);
 for(const change of [{sessionId:'journey.private'},{initial:{neutral:false}},{input:{kind:'action',text:'鍏朵粬'}},{turnCount:3},{kind:'regenerate'}]) await assert.rejects(guard.admit({...args,...change}),/FREEZE_/);
});
test('source changes are detected again immediately before every admission',async t=>{
 const f=await fixture(t),guard=await createExecutionFreeze(f);await fs.writeFile(path.join(f.root,'source.mjs'),'changed');await assert.rejects(guard.verify(),/FREEZE_SOURCE_DRIFT/);
});
test('approval digest, incomplete gates, route changes and escaped files fail closed',async t=>{
 const f=await fixture(t);await assert.rejects(createExecutionFreeze({...f,expectedSha256:'b'.repeat(64)}),/FREEZE_APPROVAL_MISMATCH/);
 for(const mutate of [m=>m.baseUrl='http://127.0.0.1:8000',m=>m.gates.agentUi.passed=false,m=>m.files[0].path='../outside',m=>m.maxTokens=4097]){const manifest=structuredClone(f.manifest);mutate(manifest);await assert.rejects(createExecutionFreeze({...f,manifest,expectedSha256:hashValue(manifest)}),/FREEZE_/);}
});

test('ten calls require a bound continuation and retain the original eight-call default', async t => {
 const f=await fixture(t), manifest=structuredClone(f.manifest); manifest.maxDispatches=10;
 await assert.rejects(createExecutionFreeze({...f,manifest,expectedSha256:hashValue(manifest)}),/FREEZE_SCOPE_INVALID/);
 manifest.continuation={format:'rpg05-authorized-continuation/1',authorization:'user-approved-plus-two-after-slot3-failure',priorFreezeSha256:'c'.repeat(64),priorRecords:['d'.repeat(64),'e'.repeat(64),'f'.repeat(64)]};
 await createExecutionFreeze({...f,manifest,expectedSha256:hashValue(manifest)});
 manifest.continuation.priorRecords.pop();
 await assert.rejects(createExecutionFreeze({...f,manifest,expectedSha256:hashValue(manifest)}),/FREEZE_CONTINUATION_INVALID/);
});

test('eleven calls require second explicit history binding',async t=>{
 const f=await fixture(t),m=structuredClone(f.manifest);m.maxDispatches=11;m.continuation={format:'rpg05-authorized-continuation/1',authorization:'user-approved-plus-two-after-slot3-failure',priorFreezeSha256:'b'.repeat(64),priorRecords:Array(3).fill('c'.repeat(64))};
 await assert.rejects(createExecutionFreeze({...f,manifest:m,expectedSha256:hashValue(m)}),/FREEZE_SCOPE_INVALID/);
 m.continuation.extension={authorization:'user-approved-plus-one-after-slot5-failure',priorFreezeSha256:'d'.repeat(64),priorPolicySha256:'e'.repeat(64),priorRecords:Array(5).fill('f'.repeat(64))};await createExecutionFreeze({...f,manifest:m,expectedSha256:hashValue(m)});
});

test('strict mode cannot be enabled by adding an unbound flag to an old freeze',async t=>{
 const f=await fixture(t);f.manifest.structuredOutput={format:'rpg05-strict-output/1'};await assert.rejects(createExecutionFreeze({...f,expectedSha256:hashValue(f.manifest)}),/FREEZE_STRUCTURED_BINDING_INVALID/);
});

test('strict freeze binds current adapter/compiler and exact backend files',async t=>{
 const f=await fixture(t),root=fileURLToPath(new URL('../',import.meta.url)),repo=path.resolve(root,'../..');
 f.manifest.files=await Promise.all(['ui-host/structured-schema.mjs','ui-host/structured-http.mjs','ui-host/real-adapter.mjs','ui-host/execution-freeze.mjs','ui-host/keyed-turn.mjs'].map(async p=>({path:p,sha256:hashText(await fs.readFile(path.join(root,p)))})));
 f.manifest.structuredOutput={format:'rpg05-strict-output/1',compilerVersion:'rpg05-structured/1',backendSha256:hashText(await fs.readFile(path.join(repo,'server/main.py'))),backendTestSha256:hashText(await fs.readFile(path.join(repo,'server/tests/test_provider_chat_structured_output.py'))),qualificationSha256:'a'.repeat(64)};
 const guard=await createExecutionFreeze({...f,root,expectedSha256:hashValue(f.manifest)});assert.equal((await guard.admit({sessionId:'journey.gu',initial:f.initial,input:f.input,turnCount:0,kind:'generate'})).responseMode,'json_schema');
 f.manifest.structuredOutput.backendSha256='b'.repeat(64);await assert.rejects(createExecutionFreeze({...f,root,expectedSha256:hashValue(f.manifest)}),/FREEZE_BACKEND_DRIFT/);
});


test('keyed format requires new version, exact codec hash and separate keyed qualification binding',async t=>{
 const f=await fixture(t),root=fileURLToPath(new URL('../',import.meta.url)),repo=path.resolve(root,'../..');
 f.manifest.files=await Promise.all(['ui-host/structured-schema.mjs','ui-host/structured-http.mjs','ui-host/real-adapter.mjs','ui-host/execution-freeze.mjs','ui-host/keyed-turn.mjs'].map(async p=>({path:p,sha256:hashText(await fs.readFile(path.join(root,p)))})));
 const structured={format:'rpg05-strict-output/2',compilerVersion:'rpg05-keyed/2',codecVersion:'rpg05-keyed-codec/1',codecSha256:f.manifest.files.find(f=>f.path==='ui-host/keyed-turn.mjs').sha256,keyedQualificationSha256:'c'.repeat(64),backendSha256:hashText(await fs.readFile(path.join(repo,'server/main.py'))),backendTestSha256:hashText(await fs.readFile(path.join(repo,'server/tests/test_provider_chat_structured_output.py'))),qualificationSha256:'a'.repeat(64)};
 f.manifest.structuredOutput=structured;
 const guard=await createExecutionFreeze({...f,root,expectedSha256:hashValue(f.manifest)});
 assert.equal((await guard.admit({sessionId:'journey.gu',initial:f.initial,input:f.input,turnCount:0,kind:'generate'})).wireFormat,'fixed_information_v1');
 for(const mutate of [s=>s.compilerVersion='rpg05-keyed/1',s=>delete s.keyedQualificationSha256,s=>s.codecSha256='e'.repeat(64),s=>s.compilerVersion='rpg05-structured/1',s=>{s.format='rpg05-strict-output/1';s.compilerVersion='rpg05-structured/1';}]) {
  const manifest=structuredClone(f.manifest);mutate(manifest.structuredOutput);
  await assert.rejects(createExecutionFreeze({...f,root,manifest,expectedSha256:hashValue(manifest)}),/FREEZE_(KEYED|STRUCTURED)_BINDING_INVALID/);
 }
 const manifest=structuredClone(f.manifest);manifest.files=manifest.files.filter(f=>f.path!=='ui-host/keyed-turn.mjs');
 await assert.rejects(createExecutionFreeze({...f,root,manifest,expectedSha256:hashValue(manifest)}),/FREEZE_STRUCTURED_BINDING_INVALID/);
});


test('targeted continuation binds exact existing turn offsets and cannot reset the world history',async t=>{
 const f=await fixture(t),root=fileURLToPath(new URL('../',import.meta.url)),repo=path.resolve(root,'../..');
 f.manifest.files=await Promise.all(['ui-host/structured-schema.mjs','ui-host/structured-http.mjs','ui-host/real-adapter.mjs','ui-host/execution-freeze.mjs','ui-host/keyed-turn.mjs','ui-host/keyed-ledger.mjs','ui-host/real-cli.mjs'].map(async p=>({path:p,sha256:hashText(await fs.readFile(path.join(root,p)))})));
 f.manifest.maxDispatches=20;f.manifest.targetedContinuation={authorization:'user-authorized-keyed-real-plan-total20-after-offline-closeout',maxDispatches:20,priorRecords:Array(12).fill('d'.repeat(64))};
 for(const j of f.manifest.journeys)j.startTurnCount=j.world==='gu'?3:1;
 f.manifest.structuredOutput={format:'rpg05-strict-output/2',compilerVersion:'rpg05-keyed/2',codecVersion:'rpg05-keyed-codec/1',codecSha256:f.manifest.files.find(f=>f.path==='ui-host/keyed-turn.mjs').sha256,keyedQualificationSha256:'c'.repeat(64),backendSha256:hashText(await fs.readFile(path.join(repo,'server/main.py'))),backendTestSha256:hashText(await fs.readFile(path.join(repo,'server/tests/test_provider_chat_structured_output.py'))),qualificationSha256:'a'.repeat(64)};
 const guard=await createExecutionFreeze({...f,root,expectedSha256:hashValue(f.manifest)});
 assert.equal((await guard.admit({sessionId:'journey.gu',initial:f.initial,input:f.input,turnCount:3,kind:'generate'})).dispatchKind,'gu');
 assert.equal((await guard.admit({sessionId:'journey.minecraft',initial:f.initial,input:f.input,turnCount:1,kind:'generate'})).dispatchKind,'minecraft');
 await assert.rejects(guard.admit({sessionId:'journey.gu',initial:f.initial,input:f.input,turnCount:0,kind:'generate'}),/FREEZE_OPERATION_FORBIDDEN/);
 f.manifest.journeys[0].startTurnCount=0;await assert.rejects(createExecutionFreeze({...f,root,expectedSha256:hashValue(f.manifest)}),/FREEZE_TARGETED_BINDING_INVALID/);
});


test('protocol total24 requires exact text, source, fresh qualification and new journey offsets',async t=>{
 const f=await fixture(t),root=fileURLToPath(new URL('../',import.meta.url)),repo=path.resolve(root,'../..');
 f.manifest.files=await Promise.all(['ui-host/structured-schema.mjs','ui-host/structured-http.mjs','ui-host/real-adapter.mjs','ui-host/execution-freeze.mjs','ui-host/keyed-turn.mjs','ui-host/keyed-protocol.mjs','ui-host/protocol-ledger.mjs','ui-host/real-cli.mjs','docs/RPG05_KEYED_PROTOCOL_CANDIDATE.txt'].map(async p=>({path:p,sha256:hashText(await fs.readFile(path.join(root,p)))})));
 f.manifest.maxDispatches=24;f.manifest.protocolContinuation={authorization:PROTOCOL_AUTHORIZATION,maxDispatches:24,priorRecords:Array(16).fill('d'.repeat(64))};for(const j of f.manifest.journeys)j.startTurnCount=0;
 f.manifest.structuredOutput={format:'rpg05-strict-output/2',compilerVersion:'rpg05-keyed/2',codecVersion:'rpg05-keyed-codec/1',codecSha256:f.manifest.files.find(f=>f.path==='ui-host/keyed-turn.mjs').sha256,keyedQualificationSha256:'c'.repeat(64),backendSha256:hashText(await fs.readFile(path.join(repo,'server/main.py'))),backendTestSha256:hashText(await fs.readFile(path.join(repo,'server/tests/test_provider_chat_structured_output.py'))),qualificationSha256:'a'.repeat(64)};
 f.manifest.protocolProjection={format:KEYED_PROTOCOL_VERSION,textSha256:KEYED_PROTOCOL_TEXT_SHA256,inputLimit:65536,qualificationSha256:'c'.repeat(64)};
 const guard=await createExecutionFreeze({...f,root,expectedSha256:hashValue(f.manifest)});assert.equal((await guard.admit({sessionId:'journey.gu',initial:f.initial,input:f.input,turnCount:0,kind:'generate'})).protocolMode,'keyed_protocol_v1');
 for(const mutate of [m=>delete m.protocolProjection,m=>m.protocolProjection.textSha256='b'.repeat(64),m=>m.protocolProjection.inputLimit=131072,m=>m.protocolProjection.qualificationSha256='b'.repeat(64),m=>m.journeys[0].startTurnCount=5,m=>m.files=m.files.filter(f=>f.path!=='ui-host/keyed-protocol.mjs'),m=>m.targetedContinuation={}]){
  const manifest=structuredClone(f.manifest);mutate(manifest);await assert.rejects(createExecutionFreeze({...f,root,manifest,expectedSha256:hashValue(manifest)}),/FREEZE_PROTOCOL_BINDING_INVALID/);
 }
});


test('reviewed total26 requires exact approved semantics, slot19 qualification and fresh UI journeys',async t=>{
 const f=await fixture(t),root=fileURLToPath(new URL('../',import.meta.url)),repo=path.resolve(root,'../..');
 f.manifest.files=await Promise.all(['ui-host/structured-schema.mjs','ui-host/structured-http.mjs','ui-host/real-adapter.mjs','ui-host/execution-freeze.mjs','ui-host/keyed-turn.mjs','ui-host/keyed-protocol.mjs','ui-host/protocol-ledger.mjs','ui-host/real-cli.mjs','docs/RPG05_KEYED_PROTOCOL_CANDIDATE.txt','docs/RPG05_SEMANTIC_CLARIFICATION.txt'].map(async p=>({path:p,sha256:hashText(await fs.readFile(path.join(root,p)))})));
 f.manifest.maxDispatches=26;f.manifest.reviewedContinuation={authorization:REVIEWED_PROTOCOL_AUTHORIZATION,maxDispatches:26,priorRecords:Array(18).fill('d'.repeat(64))};for(const j of f.manifest.journeys)j.startTurnCount=0;
 f.manifest.structuredOutput={format:'rpg05-strict-output/2',compilerVersion:'rpg05-keyed/2',codecVersion:'rpg05-keyed-codec/1',codecSha256:f.manifest.files.find(f=>f.path==='ui-host/keyed-turn.mjs').sha256,keyedQualificationSha256:'c'.repeat(64),backendSha256:hashText(await fs.readFile(path.join(repo,'server/main.py'))),backendTestSha256:hashText(await fs.readFile(path.join(repo,'server/tests/test_provider_chat_structured_output.py'))),qualificationSha256:'a'.repeat(64)};
 f.manifest.protocolProjection={format:REVIEWED_PROTOCOL_VERSION,textSha256:KEYED_PROTOCOL_TEXT_SHA256,semanticTextSha256:REVIEWED_SEMANTICS_SHA256,inputLimit:65536,qualificationSlot:19,qualificationRecordSha256:'e'.repeat(64),qualificationSha256:'c'.repeat(64)};
 const guard=await createExecutionFreeze({...f,root,expectedSha256:hashValue(f.manifest)});assert.equal((await guard.admit({sessionId:'journey.gu',initial:f.initial,input:f.input,turnCount:0,kind:'generate'})).protocolMode,'keyed_protocol_v2');
 for(const mutate of [m=>m.protocolProjection.format=KEYED_PROTOCOL_VERSION,m=>delete m.protocolProjection.semanticTextSha256,m=>m.protocolProjection.semanticTextSha256='f'.repeat(64),m=>m.protocolProjection.qualificationSlot=17,m=>delete m.protocolProjection.qualificationRecordSha256,m=>m.protocolProjection.qualificationSha256='e'.repeat(64),m=>m.journeys[0].startTurnCount=1,m=>m.files=m.files.filter(f=>f.path!=='docs/RPG05_SEMANTIC_CLARIFICATION.txt'),m=>m.protocolContinuation={},m=>m.reviewedContinuation.priorRecords.pop()]) {
  const manifest=structuredClone(f.manifest);mutate(manifest);await assert.rejects(createExecutionFreeze({...f,root,manifest,expectedSha256:hashValue(manifest)}),/FREEZE_REVIEWED_BINDING_INVALID/);
 }
});
