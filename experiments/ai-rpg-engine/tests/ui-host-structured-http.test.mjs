import { projectKeyedRequest, projectReviewedKeyedRequest, REVIEWED_SEMANTICS_SHA256 } from '../ui-host/keyed-protocol.mjs';
import { TURN_EXCHANGE_SCHEMA } from '../src/index.mjs';
import { canonicalJson } from '../runtime/contracts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createStructuredAdapter } from '../ui-host/structured-http.mjs';
import { compileStructuredSchema } from '../ui-host/structured-schema.mjs';
import { createKeyedTurnCodec } from '../ui-host/keyed-turn.mjs';
import { loadBuiltinBundle } from '../ui-host/setup.mjs';
const card=loadBuiltinBundle().cardPackage,modelId='gpt-5.6-luna';
const request=()=>({sessionId:'journey.test',generationId:'gen.test',exchangeId:'ex.test',expectedRevision:0,input:{kind:'action',text:'Observe'},messages:[{role:'user',content:'Neutral offline fixture'}],modelId,settings:{temperature:0,maxTokens:4096}});
const output=()=>({format:'modelmirror.ai-rpg.turn-exchange',formatVersion:'0.1.0',exchangeId:'ex.test',cardPackageRef:{id:card.package.id,version:card.package.version},input:request().input,proposal:{narrative:'Neutral',suggestedActions:[],informationModules:[],stateProposals:[],uncertainties:[]}});
const receipt={requested_model:modelId,actual_model:modelId,provider:null,strategy:'newapi_preferred',engine:'newapi',reason_codes:['qualified'],latency_ms:null,ttft_ms:null,tokens:{input:1,output:1,total:2},response_cost_usd:null,cost_kind:'unavailable',fallback_attempts:0,cache_hit:null,request_id:'offline.fake',version:'2'};
const protocolRequest=()=>{const r=request();return {...r,messages:[{role:'system',content:loadBuiltinBundle().hostTemplate.content},{role:'user',content:canonicalJson({kind:'current_data',resources:{}}).value},{role:'user',content:canonicalJson({kind:'output_contract',schema:TURN_EXCHANGE_SCHEMA}).value},{role:'user',content:canonicalJson({kind:'current_turn',input:r.input}).value}]};};
async function fixture({finish='stop',refusal=false,incomplete=false,wrong=false,http=false,afterDone=false,keyed=false,protocol=false,reviewed=false,mutate=()=>{},rawOverride}={}) {
 const posts=[]; const result=output();if(wrong)result.exchangeId='ex.other';mutate(result);
 const rawText=rawOverride ?? (keyed ? createKeyedTurnCodec(card,request()).encode(result) : JSON.stringify(result));
 const stream='data: '+JSON.stringify({model:modelId,choices:[{delta:refusal?{refusal:'declined'}:{content:rawText},finish_reason:finish}]})+'\n\nevent: route_receipt\ndata: '+JSON.stringify(receipt)+'\n\n'+(incomplete?'':'data: [DONE]\n\n')+(afterDone?'data: {}\n\n':'');
 const fetchImpl=async(url,options={})=>{
  if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
  if(url.endsWith('/openapi.json'))return Response.json({paths:{'/api/chat':{post:{requestBody:{content:{'application/json':{schema:{properties:{require_managed_route:{type:'boolean'},response_format:{type:'object'}}}}}}}}}});
  if(url.includes('/api/models/'))return Response.json({contract_version:'modelmirror-provider-chat-routing-v1',feature_enabled:true,data_plane_integrated:true,available:true,model_id:modelId,capability:'chat_text',effective_mode:'newapi_preferred'});
  posts.push(JSON.parse(options.body));return http?Response.json({detail:'unsupported'},{status:400}):new Response(stream,{headers:{'content-type':'text/event-stream'}});
 };
 const created=createStructuredAdapter({cardPackage:card,fetchImpl,baseUrl:'http://127.0.0.1:18305',evidenceKind:'mock',trustedOutputBudget:{maxTokens:4096},wireFormat:keyed?'fixed_information_v1':'legacy',protocolMode:reviewed?'keyed_protocol_v2':protocol?'keyed_protocol_v1':'none'});assert.ok(created.valid);assert.ok((await created.value.initialize()).valid);
 return {adapter:created.value,posts,stream};
}
test('strict request, immutable raw client bytes, successful schema-bound response',async()=>{
 const {adapter,posts,stream}=await fixture();const result=await adapter.generate(request());assert.ok(result.valid);assert.equal(result.value.text,JSON.stringify(output()));assert.equal(posts.length,1);assert.deepEqual(posts[0].response_format,compileStructuredSchema(card,request()).responseFormat);
 const evidence=adapter.evidence();assert.equal(evidence.finishReason,'stop');assert.equal(evidence.done,true);assert.equal(evidence.rawComplete,true);assert.equal(Buffer.from(evidence.rawBase64,'base64').toString(),stream);assert.equal(evidence.rawSha256,createHash('sha256').update(stream).digest('hex'));evidence.done=false;assert.equal(adapter.evidence().done,true);
});
for(const [options,code] of [[{finish:'length'},'LENGTH'],[{finish:'content_filter'},'CONTENT_FILTER'],[{refusal:true},'REFUSAL'],[{incomplete:true},'STREAM_INCOMPLETE'],[{afterDone:true},'EVENT_AFTER_DONE'],[{wrong:true},'SCHEMA_OUTPUT_INVALID'],[{http:true},'HTTP_FAILED']])test('fails closed without retry: '+code,async()=>{
 const {adapter,posts}=await fixture(options);const result=await adapter.generate(request());assert.equal(result.valid,false);assert.equal(result.diagnostics[0].code,'RUNTIME_ADAPTER_'+code);assert.equal(posts.length,1);if(options.finish)assert.equal(adapter.evidence().finishReason,options.finish);
});
test('already cancelled request never posts',async()=>{
 const {adapter,posts}=await fixture();const result=await adapter.generate(request(),{signal:AbortSignal.abort()});assert.equal(result.valid,false);assert.equal(posts.length,0);assert.equal(result.value.status,'cancelled');
});
test('cancel while text callback is pending rejects late completion',async()=>{
 const {adapter,posts}=await fixture(),controller=new AbortController();const result=await adapter.generate(request(),{signal:controller.signal,onText:()=>{controller.abort();return new Promise(()=>{});}});assert.equal(result.valid,false);assert.equal(result.value.status,'cancelled');assert.equal(posts.length,1);assert.equal(adapter.evidence().done,false);
});
test('non-loopback address rejected before any request',()=>{
 assert.equal(createStructuredAdapter({cardPackage:card,baseUrl:'https://outside.invalid'}).valid,false);
});


test('legacy duplicate module output is transport success but contract rejection, with raw evidence intact', async () => {
 const {adapter,posts,stream}=await fixture({mutate:result=>{result.proposal.informationModules=[{moduleRef:'info.start',values:[]},{moduleRef:'info.start',values:[{fieldRef:'identity',value:'Different'}]}];}});
 const report=await adapter.generate(request());assert.equal(report.valid,false);assert.equal(report.diagnostics[0].code,'RUNTIME_ADAPTER_CONTRACT_OUTPUT_INVALID');
 const evidence=adapter.evidence();assert.equal(evidence.outputValidation.transport,'passed');assert.equal(evidence.outputValidation.schema,'passed');assert.equal(evidence.outputValidation.contract,'failed');assert.ok(evidence.outputValidation.diagnostics.some(d=>d.code==='TURN_EXCHANGE_INFORMATION_MODULE_DUPLICATE'));assert.equal(Buffer.from(evidence.rawBase64,'base64').toString(),stream);assert.equal(posts.length,1);assert.equal(evidence.conversion,undefined);
});

test('keyed response converts once after complete stream, retains both texts and hashes privately', async () => {
 const {adapter,posts,stream}=await fixture({keyed:true});const chunks=[];const result=await adapter.generate(request(),{onText:chunk=>chunks.push(chunk)});assert.ok(result.valid);assert.equal(result.value.text,JSON.stringify(output()));
 const evidence=adapter.evidence();assert.equal(evidence.conversion.rawText,chunks.join(''));assert.equal(evidence.conversion.convertedText,result.value.text);assert.equal(evidence.conversion.rawSha256,createHash('sha256').update(chunks.join('')).digest('hex'));assert.equal(evidence.conversion.convertedSha256,createHash('sha256').update(result.value.text).digest('hex'));assert.equal(evidence.outputValidation.contract,'passed');assert.equal(posts.length,1);assert.equal(Buffer.from(evidence.rawBase64,'base64').toString(),stream);assert.equal(Object.hasOwn(result.value,'conversion'),false);
});

test('duplicate keyed JSON members and keyed late cancellation never yield a converted success', async () => {
 const raw=createKeyedTurnCodec(card,request()).encode(output()).replace('"info.start":[]','"info.start":[],"info.start":[]');
 const first=await fixture({keyed:true,rawOverride:raw});const rejected=await first.adapter.generate(request());assert.equal(rejected.diagnostics[0].code,'RUNTIME_ADAPTER_JSON_DUPLICATE_KEY');assert.equal(first.adapter.evidence().conversion,undefined);assert.equal(first.adapter.evidence().outputValidation.json,'failed');assert.equal(first.posts.length,1);
 const second=await fixture({keyed:true}),controller=new AbortController();const cancelled=await second.adapter.generate(request(),{signal:controller.signal,onText:()=>controller.abort()});assert.equal(cancelled.value.status,'cancelled');assert.equal(second.adapter.evidence().conversion,undefined);assert.equal(second.posts.length,1);
});


test('unregistered keyed resource reference preserves raw failure evidence with no retry or conversion', async () => {
  const wire = JSON.parse(createKeyedTurnCodec(card,request()).encode(output()));
  wire.proposal.uncertainties = [{code:'uncertainty.offline',description:'Neutral invalid resource reference.',relatedResourceRefs:['resource.not-registered']}];
  const raw = JSON.stringify(wire), {adapter,posts,stream} = await fixture({keyed:true,rawOverride:raw});
  const report = await adapter.generate(request()), evidence = adapter.evidence();
  assert.equal(report.valid,false);
  assert.equal(report.diagnostics[0].code,'RUNTIME_ADAPTER_SCHEMA_OUTPUT_INVALID');
  assert.equal(evidence.outputValidation.json,'passed');
  assert.equal(evidence.outputValidation.schema,'failed');
  assert.equal(evidence.outputValidation.contract,'not_checked');
  assert.equal(evidence.outputValidation.rawText,raw);
  assert.equal(evidence.outputValidation.rawSha256,createHash('sha256').update(raw).digest('hex'));
  assert.equal(Buffer.from(evidence.rawBase64,'base64').toString(),stream);
  assert.equal(evidence.rawSha256,createHash('sha256').update(stream).digest('hex'));
  assert.equal(report.value.text,raw);
  assert.equal(evidence.conversion,undefined);
  assert.equal(posts.length,1);
});


test('approved projection sends only the bound schema and records actual payload bytes', async () => {
 const r=protocolRequest(), before=structuredClone(r), expected=projectKeyedRequest(card,r), {adapter,posts}=await fixture({keyed:true,protocol:true});
 const result=await adapter.generate(r);assert.equal(result.valid,true);assert.deepEqual(r,before);assert.deepEqual(posts[0].messages,expected.request.messages);assert.deepEqual(posts[0].response_format,expected.responseFormat);
 const binding=adapter.evidence().requestBinding;assert.equal(binding.requestSha256,expected.requestSha256);assert.equal(binding.originalMessagesSha256,expected.binding.originalMessagesSha256);assert.equal(binding.payloadSha256,createHash('sha256').update(JSON.stringify(posts[0])).digest('hex'));assert.equal(posts.length,1);
});
test('projection rejects host drift before a post and retains binding for cancelled late content', async () => {
 const first=await fixture({keyed:true,protocol:true}),r=protocolRequest();r.messages[0].content+=' changed';
 const rejected=await first.adapter.generate(r);assert.equal(rejected.valid,false);assert.equal(rejected.diagnostics[0].code,'KEYED_PROTOCOL_HOST_DRIFT');assert.equal(first.posts.length,0);
 const second=await fixture({keyed:true,protocol:true}),controller=new AbortController(),original=protocolRequest();
 const cancelled=await second.adapter.generate(original,{signal:controller.signal,onText:()=>controller.abort()});assert.equal(cancelled.value.status,'cancelled');assert.equal(second.posts.length,1);assert.equal(second.adapter.evidence().requestBinding.requestSha256,projectKeyedRequest(card,original).requestSha256);assert.equal(second.adapter.evidence().conversion,undefined);
});


test('reviewed semantics bind exact sent bytes, preserve output and capture cancellation without retry',async()=>{
 const r=protocolRequest(),expected=projectReviewedKeyedRequest(card,r),normal=await fixture({keyed:true,reviewed:true});
 const result=await normal.adapter.generate(r);assert.equal(result.valid,true);assert.deepEqual(normal.posts[0].messages,expected.request.messages);assert.deepEqual(JSON.parse(result.value.text),output());
 assert.equal(normal.adapter.evidence().requestBinding.semanticTextSha256,REVIEWED_SEMANTICS_SHA256);assert.equal(normal.adapter.evidence().requestBinding.requestSha256,expected.requestSha256);
 const second=await fixture({keyed:true,reviewed:true}),controller=new AbortController();const cancelled=await second.adapter.generate(r,{signal:controller.signal,onText(){controller.abort();}});
 assert.equal(cancelled.valid,false);assert.equal(cancelled.value.status,'cancelled');assert.equal(second.posts.length,1);assert.equal(second.adapter.evidence().requestBinding.semanticTextSha256,REVIEWED_SEMANTICS_SHA256);
});
