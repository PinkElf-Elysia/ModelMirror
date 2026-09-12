import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createManualLedger, createManualGuard, createManualAccess, MANUAL_AUTHORIZATION } from '../ui-host/manual-access.mjs';
import { createRecordStore } from '../ui-host/storage.mjs';
import { loadBuiltinBundle, blankPlayer, freezeSetup, hashValue } from '../ui-host/setup.mjs';
const work = fileURLToPath(new URL('../.rpg04-work/',import.meta.url));
const policyIds = ['dispatch-policy','dispatch-continuation','dispatch-extension-slot5','dispatch-structured-slot7','dispatch-keyed-slot12','dispatch-protocol-slot16','dispatch-reviewed-slot18'];
const request = (operationId='gen.manual',change={}) => ({kind:'manual',operationId,requestSha256:'b'.repeat(64),maxTokens:4096,...change});
const result = outcome => ({outcome,dispatched:true,reportSha256:'c'.repeat(64)});
async function fixture(t) {
 const root = await fs.mkdtemp(path.join(work,'rpg05-manual-test-'));
 t.after(async()=>{const actual=await fs.realpath(root);assert.equal(path.dirname(actual),await fs.realpath(work));assert.ok(path.basename(actual).startsWith('rpg05-manual-test-'));await fs.rm(actual,{recursive:true});});
 const store=await createRecordStore(path.join(root,'manual')),automaticStore=await createRecordStore(path.join(root,'automatic'));
 const approval={authorization:MANUAL_AUTHORIZATION,operator:'user',maxDispatches:5,automaticConsumed:26,automaticRetry:false,automaticRecords:[],automaticPolicies:[]};
 for(let slot=1;slot<=26;slot++) {const p={slot,status:'completed',outcome:slot===26?'cancelled':'succeeded'};await automaticStore.write('operation','dispatch.'+slot,p,0);approval.automaticRecords.push((await automaticStore.write('operation','dispatch.'+slot,p,1)).sha256);}
 for(const id of policyIds)approval.automaticPolicies.push({id,sha256:(await automaticStore.write('bundle',id,{historical:id},0)).sha256});
 const options={store,automaticStore,freezeSha256:'a'.repeat(64),approval};return {root,...options,options,ledger:await createManualLedger(options)};
}
test('five manual sends include failures/cancellation and never change the automatic26 ledger',async t=>{
 const f=await fixture(t);
 for(const [i,outcome] of ['succeeded','failed','cancelled','succeeded','succeeded'].entries()) {const r=await f.ledger.reserve(request('gen.manual.'+i));assert.equal(r.payload.slot,i+1);assert.equal(r.payload.cumulativeSlot,27+i);await f.ledger.complete(r,result(outcome));}
 await assert.rejects(f.ledger.reserve(request('gen.extra')),/BUDGET_EXHAUSTED/);
 assert.deepEqual((await f.automaticStore.list('operation')).sort((a,b)=>a.payload.slot-b.payload.slot).map(r=>r.sha256),f.approval.automaticRecords);
 assert.equal((await f.store.read('bundle','manual-policy')).payload.automaticRetry,false);
});
test('manual quota survives restart and permits only one concurrent reservation',async t=>{
 const f=await fixture(t),other=await createManualLedger(f.options);
 const races=await Promise.allSettled([f.ledger.reserve(request('gen.one')),other.reserve(request('gen.two'))]);assert.equal(races.filter(r=>r.status==='fulfilled').length,1);
 const winner=races.find(r=>r.status==='fulfilled').value;await f.ledger.complete(winner,result('succeeded'));
 const restored=await createManualLedger(f.options);assert.equal((await restored.snapshot()).length,1);
 await assert.rejects(restored.reserve(request(winner.payload.operationId)),/REPLAY_FORBIDDEN/);
 assert.equal((await restored.reserve(request('gen.next'))).payload.slot,2);
});
test('unresolved or unknown manual dispatch cannot silently retry after restart',async t=>{
 const f=await fixture(t),r=await f.ledger.reserve(request());
 await assert.rejects((await createManualLedger(f.options)).reserve(request('gen.other')),/UNRESOLVED/);
 await f.ledger.complete(r,result('unknown'));
 await assert.rejects((await createManualLedger(f.options)).reserve(request('gen.other')),/UNRESOLVED/);
 await assert.rejects(f.ledger.complete(r,result('succeeded')),/COMPLETION_CONFLICT/);
});
test('budget, operator, token cap and automatic-kind substitution are rejected',async t=>{
 const f=await fixture(t);
 for(const mutate of [a=>a.maxDispatches=6,a=>a.operator='agent',a=>a.automaticConsumed=25,a=>a.automaticRetry=true,a=>a.automaticRecords.pop(),a=>a.automaticPolicies[6]={...a.automaticPolicies[0]}]) {const approval=structuredClone(f.approval);mutate(approval);await assert.rejects(createManualLedger({...f.options,approval}),/APPROVAL_INVALID/);}
 for(const change of [{kind:'gu'},{kind:'certification'},{maxTokens:4097},{requestSha256:'invalid'}])await assert.rejects(f.ledger.reserve(request('gen.invalid',change)),/REQUEST_INVALID/);
 assert.equal((await f.ledger.snapshot()).length,0);
});
test('old evidence and policy edits or a different manual freeze cannot reopen the allowance',async t=>{
 const f=await fixture(t);await assert.rejects(createManualLedger({...f.options,freezeSha256:'d'.repeat(64)}),/POLICY_DRIFT/);
 const p=await f.automaticStore.read('operation','dispatch.26');await f.automaticStore.write('operation',p.id,{...p.payload,outcome:'succeeded'},p.revision);
 await assert.rejects(f.ledger.snapshot(),/AUTOMATIC_HISTORY_DRIFT/);
});
test('manual policy mutation is detected before a provider reservation',async t=>{
 const f=await fixture(t),p=await f.store.read('bundle','manual-policy');await f.store.write('bundle',p.id,{...p.payload,maxDispatches:50},p.revision);
 await assert.rejects(f.ledger.reserve(request()),/POLICY_DRIFT/);
});
function guardFixture(world) {
 const builtin=loadBuiltinBundle();builtin.contextProfile.budget.outputLimit=4096;
 const scene=builtin.contextProfile.scenes.find(s=>s.worldRef===world),player=blankPlayer(builtin.cardPackage);
 player.character={name:'Offline manual player',appearance:'Work clothes',personality:'Careful',preferences:[]};player.world.resourceRef=world;player.opening.openingRef=scene.openingRef;player.currentIdentity.resourceRef=builtin.cardPackage.resources.openings.find(o=>o.id===scene.openingRef).identityRefs[0];
 player.possessions=[{resource:{source:'custom',resource:{id:'item.manual',kind:'item',displayName:'Notebook',description:'A plain notebook'}},quantity:1}];
 const initial=freezeSetup(player,builtin,scene.id).value;let drift=false,verified=0;
 const sourceGuard={async verify(){verified++;if(drift)throw Error('FREEZE_SOURCE_DRIFT');}};
 const guard=createManualGuard({sourceGuard,expectedSha256:'a'.repeat(64),hostTemplate:builtin.hostTemplate});
 return {guard,initial,args:{sessionId:'journey.manual',initial,input:{kind:'action',text:'自由输入，查看笔记'},turnCount:0,kind:'generate'},drift:()=>{drift=true;},verified:()=>verified};
}
test('both worlds admit new UI custom data and free input, including explicit regeneration',async()=>{
 for(const world of ['world.gu','world.minecraft']) {const f=guardFixture(world),before=structuredClone(f.initial);
  for(const kind of ['generate','regenerate'])for(const inputKind of ['action','speech','query']) {const execution=await f.guard.admit({...f.args,kind,input:{kind:inputKind,text:'Different player case'}});assert.equal(execution.dispatchKind,'manual');assert.equal(execution.protocolMode,'keyed_protocol_v2');assert.equal(execution.maxTokens,4096);}
  assert.deepEqual(f.initial,before);assert.equal(f.verified(),6);
 }
});
test('forged initial hashes, privileged input, unknown scenes and other output limits fail before dispatch',async()=>{
 const f=guardFixture('world.gu');
 for(const mutate of [a=>a.initial.playerSetup.character.name='Changed after freeze',a=>a.initial.bindings.playerSetupSha256='b'.repeat(64),a=>a.initial.sceneRef='scene.unknown',a=>a.initial.playerSetup.runtimePermissions=['root'],a=>a.initial.contextProfile.budget.outputLimit=8192,a=>a.input={kind:'command',commandRef:'command.unknown',text:'Run'},a=>a.input={kind:'action',text:'test',messages:[]},a=>a.kind='execute',a=>a.sessionId='../private',a=>a.turnCount=-1]) {const args=structuredClone(f.args);mutate(args);await assert.rejects(f.guard.admit(args),/MANUAL_/);}
 f.drift();await assert.rejects(f.guard.admit(f.args),/FREEZE_SOURCE_DRIFT/);
});
test('manual factory refuses an unbound manifest without touching stores or network',async()=>{
 let touched=false;const manifest={format:'modelmirror.ai-rpg.rpg05-manual-freeze/1'};
 await assert.rejects(createManualAccess({manifest,expectedSha256:hashValue(manifest),store:{read(){touched=true;}}}),/MANUAL_APPROVAL_INVALID/);assert.equal(touched,false);
});
// Mock transport only: no request in this test reaches a provider.
import { createJourneyEngine } from '../ui-host/journeys.mjs';
import { createUiService } from '../ui-host/service.mjs';
import { createGuardedAdapter } from '../ui-host/real-adapter.mjs';
import { createKeyedTurnCodec } from '../ui-host/keyed-turn.mjs';
test('manual UI service uses reviewed transport, stores exact bindings and does not replay after recovery',async t=>{
 const f=await fixture(t),builtin=loadBuiltinBundle();builtin.contextProfile.budget.outputLimit=4096;
 const records=await createRecordStore(path.join(f.root,'records')),evidenceStore=await createRecordStore(path.join(f.root,'evidence'));let posts=0;
 t.mock.method(globalThis,'fetch',async(url,options={})=>{
  if(url.endsWith('/openapi.json'))return Response.json({paths:{'/api/chat':{post:{requestBody:{content:{'application/json':{schema:{properties:{require_managed_route:{type:'boolean'},response_format:{type:'object'}}}}}}}}}});
  if(url.includes('/api/models/'))return Response.json({contract_version:'modelmirror-provider-chat-routing-v1',feature_enabled:true,data_plane_integrated:true,available:true,model_id:'gpt-5.6-luna',capability:'chat_text',effective_mode:'newapi_preferred'});
  assert.equal(url,'http://127.0.0.1:18305/api/chat');assert.equal(options.method,'POST');posts++;
  const payload=JSON.parse(options.body),schema=payload.response_format.json_schema.schema;
  const request={exchangeId:schema.properties.exchangeId.enum[0],input:Object.fromEntries(Object.entries(schema.properties.input.properties).map(([k,s])=>[k,s.enum[0]]))};
  const turn={format:'modelmirror.ai-rpg.turn-exchange',formatVersion:'0.1.0',exchangeId:request.exchangeId,cardPackageRef:{id:builtin.cardPackage.package.id,version:builtin.cardPackage.package.version},input:request.input,proposal:{narrative:'Offline manual UI fixture',suggestedActions:[],informationModules:[],stateProposals:[],uncertainties:[]}};
  const content=createKeyedTurnCodec(builtin.cardPackage,request).encode(turn),model='gpt-5.6-luna';
  const receipt={requested_model:model,actual_model:model,provider:null,strategy:'newapi_preferred',engine:'newapi',reason_codes:['qualified'],latency_ms:null,ttft_ms:null,tokens:{input:1,output:1,total:2},response_cost_usd:null,cost_kind:'unavailable',fallback_attempts:0,cache_hit:null,request_id:'offline.manual',version:'2'};
  return new Response('data: '+JSON.stringify({model,choices:[{delta:{content},finish_reason:'stop'}]})+'\n\nevent: route_receipt\ndata: '+JSON.stringify(receipt)+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
 });
 const originalPolicy=await f.store.read('bundle','manual-policy');
 f.ledger=await createManualLedger({...f.options,freezeSha256:'e'.repeat(64),sourceBinding:{format:'rpg05-manual-source-binding/1',authorization:'user-approved-rpg05-closeout-runtime-cutover',version:'closeout-1',priorFreezeSha256:f.options.freezeSha256,priorPolicySha256:originalPolicy.sha256,priorRecords:[]}});
 const engines=[],guard=createManualGuard({sourceGuard:{async verify(){}},expectedSha256:'e'.repeat(64),hostTemplate:builtin.hostTemplate});
 const open=async()=>{const e=await createJourneyEngine({store:records,root:path.join(f.root,'runtime'),hostTemplate:builtin.hostTemplate,evidenceKind:'real',adapterFor:(_card,admission)=>createGuardedAdapter({guard,ledger:f.ledger,evidenceStore,admission})});engines.push(e);return e;};
 const engine=await open(),service=createUiService({store:records,builtin,engine});
 try {
  let last;
  for(const [i,world] of ['world.gu','world.minecraft'].entries()) {
   const g=guardFixture(world),draftId='draft.manual.'+i,journeyId='journey.manual.'+i;
   await service.command('draft.save',{id:draftId,expectedRevision:0,playerSetup:g.initial.playerSetup,sceneRef:g.initial.sceneRef,bundleId:'builtin'});
   await service.command('journey.create',{id:journeyId,draftId,expectedRevision:1});
   const before=await service.command('journey.read',{id:journeyId});last={id:journeyId,expectedRevision:before.revision,operationId:'op.manual.'+i,text:'/查询 A new custom UI case '+i};
   await service.command('journey.generate',last);await engine.idle();const current=await service.command('journey.read',{id:journeyId});assert.equal(current.turnCount,1);assert.equal(current.operation.status,'committed');
   const sent=await evidenceStore.read('bundle','request.'+(i+1)),received=await evidenceStore.read('bundle','response.'+(i+1));assert.equal(received.payload.transportEvidence.requestBinding.requestSha256,(await f.ledger.snapshot())[i].payload.requestSha256);assert.equal(sent.payload.request.input.text,'A new custom UI case '+i);assert.equal((await f.ledger.snapshot())[i].payload.freezeSha256,'e'.repeat(64));assert.equal((await f.store.read('bundle','manual-policy')).sha256,originalPolicy.sha256);
   for(const key of ['messages','hostTemplate','rawBase64','responseFormat'])assert.equal(JSON.stringify(current).includes('"'+key+'"'),false);
  }
  assert.equal(posts,2);await engine.close();const restored=await open();await restored.command('journey.generate',last);await restored.idle();assert.equal(posts,2);assert.equal((await f.ledger.snapshot()).length,2);
 } finally {for(const e of engines)await e.close();}
});
import { runUiTurn } from '../ui-host/turn.mjs';
import { settleExecution } from '../ui-host/real-adapter.mjs';
test('actual guarded transport preserves unknown versus rejected, completed-invalid and cancelled ledger outcomes',async t=>{
 for(const mode of ['disconnect','preflight-reject','complete-invalid','cancel','evidence-failure']) await t.test(mode,async t=>{
  const f=await fixture(t),g=guardFixture('world.minecraft'),controller=new AbortController();let posts=0;
  t.mock.method(globalThis,'fetch',async(url,options={})=>{
   if(url.endsWith('/openapi.json')) return Response.json({paths:{'/api/chat':{post:{requestBody:{content:{'application/json':{schema:{properties:{require_managed_route:{type:'boolean'},response_format:{type:'object'}}}}}}}}}});
   if(url.includes('/api/models/')) return Response.json({contract_version:'modelmirror-provider-chat-routing-v1',feature_enabled:true,data_plane_integrated:true,available:mode!=='preflight-reject',model_id:'gpt-5.6-luna',capability:'chat_text',effective_mode:'newapi_preferred'});
   assert.equal(url,'http://127.0.0.1:18305/api/chat');assert.equal(options.method,'POST');posts++;
   if(mode==='cancel'){controller.abort();throw Error('client stop');}
   if(mode==='disconnect')throw Error('connection lost after POST');
   const model='gpt-5.6-luna',receipt={requested_model:model,actual_model:model,provider:null,strategy:'newapi_preferred',engine:'newapi',reason_codes:['qualified'],latency_ms:null,ttft_ms:null,tokens:{input:1,output:1,total:2},response_cost_usd:null,cost_kind:'unavailable',fallback_attempts:0,cache_hit:null,request_id:'offline.terminal',version:'2'};
   return new Response('data: '+JSON.stringify({model,choices:[{delta:{content:'{}'},finish_reason:'stop'}]})+'\n\nevent: route_receipt\ndata: '+JSON.stringify(receipt)+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
  });
  const saved=await createRecordStore(path.join(f.root,'evidence'));
  const evidenceStore=mode==='evidence-failure'?{async write(){throw Error('before transport');}}:saved;
  const adapter=await createGuardedAdapter({guard:g.guard,ledger:f.ledger,evidenceStore,admission:g.args});
  const output=await runUiTurn({root:path.join(f.root,'turns'),operationId:'op.once',sessionId:g.args.sessionId,initial:g.initial,hostTemplate:loadBuiltinBundle().hostTemplate,adapter,input:g.args.input,signal:controller.signal});
  assert.notEqual(output.status,'committed');const publishedStatus=controller.signal.aborted?'cancelled':output.status;await settleExecution(adapter,publishedStatus);await settleExecution(adapter,publishedStatus);
  const records=await f.ledger.snapshot();assert.equal(records.length,1);
  const expected=mode==='disconnect'?'unknown':mode==='cancel'?'cancelled':'failed';assert.equal(records[0].payload.outcome,expected);
  assert.equal(records[0].payload.dispatched,!['preflight-reject','evidence-failure'].includes(mode));
  assert.equal(posts,['preflight-reject','evidence-failure'].includes(mode)?0:1);
  const restored=await createManualLedger(f.options);
  if(expected==='unknown')await assert.rejects(restored.reserve(request('gen.later')),/MANUAL_DISPATCH_UNRESOLVED/);
  else assert.equal((await restored.reserve(request('gen.later'))).payload.slot,2);
 });
});
