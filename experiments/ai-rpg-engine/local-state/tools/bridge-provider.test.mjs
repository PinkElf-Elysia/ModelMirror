import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {NativeBridgeProvider} from './bridge-provider.mjs';import {hash} from '../definition.mjs';import {TOOL} from './query.mjs';
const selection={selectionId:'selection',selectionRevision:'revision',model:'synthetic',parameters:{max_tokens:100}};
const authorization={id:'offline-authorization',maximumCalls:2,maximumUSD:0.2,perCallUSD:0.1,expiresAt:'2099-01-01T00:00:00Z',pricing:{inputUSD:0.000001,outputUSD:0.000002,quoteId:'synthetic-quote'}};
const request={messages:[{role:'system',content:'system'},{role:'user',content:'query'}],tools:[TOOL],tool_choice:'auto'};
test('native HTTP adapter persists bounded calls, verifies receipts and recovers without POST replay',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'native-bridge-'));let posts=0,saved;
 const fetcher=async(url,options)=>{
  if(options.method==='GET')return Response.json({httpStatus:200,body:saved});
  posts++;const payload=JSON.parse(options.body),message={role:'assistant',content:'正文\n<state_delta>{"operations":[]}</state_delta>'};
  saved={raw:message.content,sse:'synthetic-sse',message,finishReason:'stop',receipt:{gateway:'rpg_scoped',sessionId:payload.sessionId,requestId:payload.requestId,selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,requestedModel:selection.model,actualModel:selection.model,requestHash:hash(payload),rawHash:hash(message.content),sseHash:hash('synthetic-sse'),messageHash:hash(message),retries:0,dispatched:true,status:'complete'}};
  if(posts===1)throw Error('lost response');return Response.json(saved);
 };
 let provider=await NativeBridgeProvider.open({directory:dir,enabled:true,baseURL:'http://127.0.0.1:1',serviceToken:'synthetic-service-token-for-tests-only',selection,authorization,fetcher});
 t.after(async()=>{await provider.close();assert.ok(dir.startsWith(join(tmpdir(),'native-bridge-')));await rm(dir,{recursive:true,force:true});});
 assert.equal((await provider.generate('session','one',request)).status,'unknown');
 await provider.close();
 provider=await NativeBridgeProvider.open({directory:dir,enabled:true,baseURL:'http://127.0.0.1:1',serviceToken:'synthetic-service-token-for-tests-only',selection,authorization,fetcher});
 assert.equal((await provider.lookup('session','one')).status,'completed');assert.equal(posts,1);
 await provider.generate('session','one',request);assert.equal(posts,1);
 await provider.generate('session','two',request);assert.equal(posts,2);
 await assert.rejects(()=>provider.generate('session','three',request),{code:'NATIVE_BUDGET'});
});
test('default off before filesystem or network',async()=>{await assert.rejects(()=>NativeBridgeProvider.open({}),{code:'NATIVE_PROVIDER_DISABLED'});});

test('host and native bridge adapter share the exact tool messages and commit only final response',async t=>{
 const {ToolsHost}=await import('./host.mjs'),{exampleState}=await import('../example.mjs');
 const dir=await mkdtemp(join(tmpdir(),'native-composed-'));let seen=[];
 const fetcher=async(url,options)=>{
  const p=JSON.parse(options.body);seen.push(p);assert.equal(url.searchParams.get('profile'),'default');
  let message,finishReason;
  if(seen.length===1){
   assert.equal(p.tool_choice,'auto');
   message={role:'assistant',content:null,tool_calls:[{id:'native-call',type:'function',function:{name:'query_local_state',arguments:'{"queries":[{"domain":"items","kind":"丹药"}]}'},extra_content:{google:{thought_signature:'keep-me'}}}]};finishReason='tool_calls';
  }else{
   assert.equal(p.tool_choice,'none');assert.equal(p.messages.at(-2).tool_calls[0].extra_content.google.thought_signature,'keep-me');
   assert.equal(JSON.parse(p.messages.at(-1).content).results[0].entries[0].quantity,5);
   message={role:'assistant',content:'你取出两份养息丹服下。\n<state_delta>{"operations":[{"type":"quantity","id":"medicine","delta":-2,"reason":"服用两份养息丹"}]}</state_delta>'};finishReason='stop';
  }
  const raw=message.content||'',sse='synthetic-exchange';
  return Response.json({raw,sse,message,finishReason,receipt:{gateway:'rpg_scoped',sessionId:p.sessionId,requestId:p.requestId,selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,requestedModel:selection.model,actualModel:selection.model,requestHash:hash(p),rawHash:hash(raw),sseHash:hash(sse),messageHash:hash(message),retries:0,dispatched:true,status:'complete'}});
 };
 const provider=await NativeBridgeProvider.open({directory:join(dir,'transport'),enabled:true,baseURL:'http://127.0.0.1:1',serviceToken:'synthetic-service-token-for-tests-only',selection,authorization:{...authorization,pricing:{inputUSD:0.00000001,outputUSD:0.00000002,quoteId:'synthetic'}},fetcher});
 const host=await ToolsHost.open({directory:join(dir,'host'),enabled:true,provider});
 t.after(async()=>{await host.close();assert.ok(dir.startsWith(join(tmpdir(),'native-composed-')));await rm(dir,{recursive:true,force:true});});
 const r=await host.create({operationId:'init',initial:exampleState(),confirmed:true});
 const n=await host.turn(r.id,{operationId:'query-use',revision:0,input:'查询后服用两份养息丹'});
 assert.equal(seen.length,2);assert.equal(n.turns.length,1);assert.equal(n.state.items[0].quantity,3);
});

test('native usage keeps reported cost distinct from estimate and does not double count reasoning',()=>{
 const provider=new NativeBridgeProvider({selection,authorization});
 const payload={sessionId:'s',requestId:'r',selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...request};
 const message={role:'assistant',content:'正文'},raw=message.content;
 const sse='data: '+JSON.stringify({usage:{prompt_tokens:100,completion_tokens:20,total_tokens:120,completion_tokens_details:{reasoning_tokens:5},cost:0.000123}})+'\n\ndata: [DONE]\n\n';
 const result=provider.decode({status:200,body:{raw,sse,message,finishReason:'stop',receipt:{gateway:'rpg_scoped',sessionId:'s',requestId:'r',selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,requestedModel:selection.model,actualModel:selection.model,requestHash:hash(payload),rawHash:hash(raw),sseHash:hash(sse),messageHash:hash(message),retries:0,dispatched:true,status:'complete'}}},payload);
 assert.equal(result.usage.reasoningTokens,5);assert.equal(result.accounting.reportedCostMicros,124); // Shared meter conservatively ceilings binary floating point; original SSE retains 0.000123.
assert.equal(result.accounting.estimatedCostMicros,140);
});
