import { projectKeyedRequest, projectReviewedKeyedRequest, REVIEWED_SEMANTICS_SHA256 } from '../ui-host/keyed-protocol.mjs';
import { TURN_EXCHANGE_SCHEMA } from '../src/index.mjs';
import { canonicalJson } from '../runtime/contracts.mjs';
import { loadBuiltinBundle, hashValue } from '../ui-host/setup.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createGuardedAdapter, executionFor, settleExecution } from '../ui-host/real-adapter.mjs';
import { createKeyedTurnCodec } from '../ui-host/keyed-turn.mjs';
const modelId='gpt-5.6-luna';
const request=()=>({sessionId:'journey.test',generationId:'gen.op.test',exchangeId:'ex.op.test',expectedRevision:0,input:{kind:'action',text:'瑙傚療'},messages:[{role:'system',content:'Neutral offline fixture'},{role:'user',content:'Observe'}],modelId,settings:{temperature:0,maxTokens:4096}});
const protocolRequest=()=>{const r=request();return {...r,messages:[{role:'system',content:loadBuiltinBundle().hostTemplate.content},{role:'user',content:canonicalJson({kind:'current_data',resources:{}}).value},{role:'user',content:canonicalJson({kind:'output_contract',schema:TURN_EXCHANGE_SCHEMA}).value},{role:'user',content:canonicalJson({kind:'current_turn',input:r.input}).value}]};};
function fixture(t,{failure=false,structured=false,keyed=false,duplicate=false,protocol=false,reviewed=false}={}) {
 const fixtureRequest=protocol?protocolRequest:request;
 const calls=[],order=[],writes=[],reservations=[];const card=loadBuiltinBundle().cardPackage;let drift=false;
 const turn={format:'modelmirror.ai-rpg.turn-exchange',formatVersion:'0.1.0',exchangeId:fixtureRequest().exchangeId,cardPackageRef:{id:card.package.id,version:card.package.version},input:fixtureRequest().input,proposal:{narrative:'Neutral',suggestedActions:[],informationModules:[],stateProposals:[],uncertainties:[]}};
 if(duplicate)turn.proposal.informationModules=[{moduleRef:'info.start',values:[]},{moduleRef:'info.start',values:[]}];
 t.mock.method(globalThis,'fetch',async(url,options)=>{
  calls.push({url,method:options?.method??'GET',body:options?.body});
  if(url.endsWith('/openapi.json'))return Response.json({paths:{'/api/chat':{post:{requestBody:{content:{'application/json':{schema:{properties:{require_managed_route:{type:'boolean'},response_format:{type:'object'}}}}}}}}}});
  if(url.includes('/api/models/'))return Response.json({contract_version:'modelmirror-provider-chat-routing-v1',feature_enabled:true,data_plane_integrated:true,available:true,model_id:modelId,capability:'chat_text',effective_mode:'newapi_preferred'});
  assert.equal(url,'http://127.0.0.1:18305/api/chat');order.push('post');assert.deepEqual(order.slice(0,3),['reserve','request','post']);
  if(failure)throw Error('transport');
  const receipt={requested_model:modelId,actual_model:modelId,provider:null,strategy:'newapi_preferred',engine:'newapi',reason_codes:['qualified'],latency_ms:null,ttft_ms:null,tokens:{input:1,output:1,total:2},response_cost_usd:null,cost_kind:'unavailable',fallback_attempts:0,cache_hit:null,request_id:'offline.fake',version:'2'};
  return new Response('data: '+JSON.stringify({model:modelId,choices:[{delta:{content:structured?(keyed?createKeyedTurnCodec(card,fixtureRequest()).encode(turn):JSON.stringify(turn)):'{}'},finish_reason:'stop'}]})+'\n\nevent: route_receipt\ndata: '+JSON.stringify(receipt)+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});
 });
 const guard={sha256:'a'.repeat(64),async admit(){if(drift)throw Error('FREEZE_SOURCE_DRIFT');return {...(protocol?{protocolMode:reviewed?'keyed_protocol_v2':'keyed_protocol_v1'}:{}),...(structured?{responseMode:'json_schema',...(keyed?{wireFormat:'fixed_information_v1'}:{})}:{}),evidenceKind:'real',modelId,maxTokens:4096,temperature:0,dispatchKind:'gu'};}},ledger={async reserve(p){order.push('reserve');reservations.push(p);return {id:'dispatch.2',payload:{slot:2},request:p};},async complete(r,p){order.push('complete');writes.push(p);}},evidenceStore={async write(kind,id,p){order.push(id.startsWith('request')?'request':'response');writes.push(p);}};
 return {calls,order,writes,reservations,request:fixtureRequest,drift:()=>{drift=true;},options:{guard,ledger,evidenceStore,admission:{sessionId:'journey.test',input:fixtureRequest().input,...(structured?{initial:{cardPackage:card}}:{})}}};
}
test('real wrapper reserves before transport and completes only after explicit publication outcome',async t=>{
 const f=fixture(t),adapter=await createGuardedAdapter(f.options);assert.equal(executionFor(adapter).maxTokens,4096);
 assert.equal((await adapter.generate(request(),{})).valid,true);const saved=f.writes.find(w=>w.report);assert.equal(saved.report.value.text,'{}');assert.equal(saved.outputDiagnostics.json,'passed');assert.equal(saved.outputDiagnostics.rawSseCaptured,false);assert.deepEqual(f.order,['reserve','request','post','response']);
 await settleExecution(adapter,'committed');await settleExecution(adapter,'committed');assert.equal(f.order.filter(x=>x==='complete').length,1);assert.equal(f.writes.at(-1).outcome,'succeeded');
 await assert.rejects(adapter.generate(request(),{}),/DISPATCH_REPLAY_FORBIDDEN/);assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
});
test('post-construction source drift and mismatched request never reserve or post',async t=>{
 const f=fixture(t),adapter=await createGuardedAdapter(f.options);await assert.rejects(adapter.generate({...request(),sessionId:'journey.other'},{}),/REAL_REQUEST_BINDING_DRIFT/);f.drift();await assert.rejects(adapter.generate(request(),{}),/FREEZE_SOURCE_DRIFT/);assert.deepEqual(f.order,[]);
 assert.throws(()=>executionFor({evidenceKind:'real'}),/REAL_EXECUTION_NOT_AUTHORIZED/);
});
test('transport failure retains response and settles unknown without retry',async t=>{
 const f=fixture(t,{failure:true}),adapter=await createGuardedAdapter(f.options);assert.equal((await adapter.generate(request(),{})).valid,false);await settleExecution(adapter,'failed');assert.equal(f.writes.at(-1).outcome,'unknown');assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
});

test('opt-in strict mode binds exact schema into reservation and retains private SSE evidence',async t=>{
 const f=fixture(t,{structured:true}),adapter=await createGuardedAdapter(f.options);const report=await adapter.generate(request(),{});assert.ok(report.valid);
 const req=f.writes.find(w=>w.responseFormat),response=f.writes.find(w=>w.transportEvidence);assert.ok(req);assert.equal(req.requestSha256,hashValue({request:request(),responseFormat:req.responseFormat}));assert.equal(response.requestSha256,req.requestSha256);assert.equal(response.transportEvidence.schemaSha256,req.schemaSha256);assert.equal(response.transportEvidence.done,true);assert.ok(response.transportEvidence.rawBase64);assert.equal(response.outputDiagnostics.rawSseCaptured,true);assert.equal(response.outputDiagnostics.observedTerminal.finishReason,'stop');assert.equal(Object.hasOwn(report.value,'transportEvidence'),false);
 await assert.rejects(adapter.generate(request(),{}),/DISPATCH_REPLAY_FORBIDDEN/);assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
});


test('fixed-key generation binds new schema and preserves model raw plus legacy conversion in private evidence',async t=>{
 const f=fixture(t,{structured:true,keyed:true}),adapter=await createGuardedAdapter(f.options);const report=await adapter.generate(request(),{});assert.ok(report.valid);
 const req=f.writes.find(w=>w.responseFormat),saved=f.writes.find(w=>w.transportEvidence);
 assert.equal(req.wireFormat,'fixed_information_v1');assert.equal(req.compilerVersion,'rpg05-keyed/2');assert.equal(req.codecVersion,'rpg05-keyed-codec/1');assert.equal(req.requestSha256,hashValue({request:request(),responseFormat:req.responseFormat}));
 assert.equal(saved.transportEvidence.conversion.convertedText,report.value.text);assert.equal(saved.outputDiagnostics.rawSha256,saved.transportEvidence.conversion.rawSha256);assert.equal(saved.outputDiagnostics.convertedSha256,saved.transportEvidence.conversion.convertedSha256);assert.notEqual(saved.outputDiagnostics.rawSha256,saved.outputDiagnostics.convertedSha256);
 assert.equal(JSON.parse(report.value.text).format,'modelmirror.ai-rpg.turn-exchange');assert.equal(saved.outputDiagnostics.contract,'passed');
 assert.deepEqual(f.order,['reserve','request','post','response']);await settleExecution(adapter,'committed');assert.equal(f.writes.at(-1).outcome,'succeeded');assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
});

test('duplicate module failure retains successful transport diagnosis and settles failed without retry',async t=>{
 const f=fixture(t,{structured:true,duplicate:true}),adapter=await createGuardedAdapter(f.options);const report=await adapter.generate(request(),{});assert.equal(report.valid,false);
 const saved=f.writes.find(w=>w.transportEvidence);assert.equal(saved.outputDiagnostics.transport,'passed');assert.equal(saved.outputDiagnostics.wireSchema,'passed');assert.equal(saved.outputDiagnostics.contract,'failed');assert.ok(saved.outputDiagnostics.diagnostics.some(d=>d.code==='TURN_EXCHANGE_INFORMATION_MODULE_DUPLICATE'));
 await settleExecution(adapter,'failed');assert.equal(f.writes.at(-1).outcome,'failed');await assert.rejects(adapter.generate(request(),{}),/DISPATCH_REPLAY_FORBIDDEN/);assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
});


test('projected wrapper binds reservation, private original, transport request and actual payload',async t=>{
 const f=fixture(t,{structured:true,keyed:true,protocol:true}),adapter=await createGuardedAdapter(f.options),r=f.request(),before=structuredClone(r);
 const pending=adapter.generate(r,{});r.messages[1].content='caller mutation after invocation';const report=await pending;assert.equal(report.valid,true);
 const sent=f.writes.find(w=>w.transportRequest),response=f.writes.find(w=>w.transportEvidence),post=JSON.parse(f.calls.find(c=>c.method==='POST').body);
 assert.deepEqual(sent.request,before);assert.deepEqual(sent.transportRequest.messages,post.messages);assert.notDeepEqual(sent.request.messages,post.messages);assert.equal(sent.requestSha256,hashValue({request:sent.transportRequest,responseFormat:sent.responseFormat}));
 assert.equal(f.reservations[0].requestSha256,sent.requestSha256);assert.equal(response.transportEvidence.requestBinding.requestSha256,sent.requestSha256);assert.equal(response.requestSha256,sent.requestSha256);assert.equal(response.transportEvidence.requestBinding.originalMessagesSha256,hashValue(before.messages));assert.equal(JSON.stringify(report).includes('projectedMessagesSha256'),false);
 await settleExecution(adapter,'committed');await assert.rejects(adapter.generate(before,{}),/DISPATCH_REPLAY_FORBIDDEN/);assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
});
test('projected wrapper blocks an unapproved host before reservation',async t=>{
 const f=fixture(t,{structured:true,keyed:true,protocol:true}),adapter=await createGuardedAdapter(f.options),r=f.request();r.messages[0].content+=' changed';
 await assert.rejects(adapter.generate(r,{}),/KEYED_PROTOCOL_HOST_DRIFT/);assert.equal(f.reservations.length,0);assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
});


test('reviewed real wrapper binds new semantic version and settles exactly one formal outcome',async t=>{
 const f=fixture(t,{structured:true,keyed:true,protocol:true,reviewed:true}),adapter=await createGuardedAdapter(f.options),r=f.request();
 const result=await adapter.generate(r,{});assert.equal(result.valid,true);const expected=projectReviewedKeyedRequest(loadBuiltinBundle().cardPackage,r),saved=f.writes.find(w=>w.transportRequest),response=f.writes.find(w=>w.transportEvidence);
 assert.equal(saved.requestBinding.semanticTextSha256,REVIEWED_SEMANTICS_SHA256);assert.equal(f.reservations[0].requestSha256,expected.requestSha256);assert.equal(response.transportEvidence.requestBinding.requestSha256,expected.requestSha256);
 assert.deepEqual(saved.request,r);assert.deepEqual(saved.transportRequest,expected.request);assert.equal(JSON.stringify(result).includes(REVIEWED_SEMANTICS_SHA256),false);
 await settleExecution(adapter,'committed');await settleExecution(adapter,'committed');assert.equal(f.order.filter(x=>x==='complete').length,1);assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
});
