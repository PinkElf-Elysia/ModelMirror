import test from 'node:test';import assert from 'node:assert/strict';import {createServer} from 'node:http';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join,resolve} from 'node:path';
import {createControlledProvider} from '../studio/controlled-provider.mjs';import {models,sha} from '../studio/provider.mjs';import {canonical,hash} from './contracts.mjs';import {createHarnessControlledProvider} from './controlled-provider.mjs';
for(const kind of ['complete','length','missing','empty'])test('measured original transport persists '+kind+' usage and observation across reopen',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'shop-meter-')),token='synthetic-meter-token-not-a-real-secret',model='google/gemini-3.8-flash';let calls=0,active=false;
 const server=createServer(async(req,res)=>{
  res.setHeader('Content-Type','application/json');
  if(req.method==='GET'){res.end(JSON.stringify({text:active&&kind!=='empty'?'店主向你点头':''}));return;}
  const bytes=[];for await(const c of req)bytes.push(c);const b=JSON.parse(Buffer.concat(bytes));calls++;active=true;
  await new Promise(r=>setTimeout(r,80));active=false;const raw=kind==='empty'?'\n':'店主向你点头。';
  const usage={prompt_tokens:1000,completion_tokens:200,total_tokens:1200,prompt_tokens_details:{cached_tokens:600},completion_tokens_details:{reasoning_tokens:50},cost:0.0015};
  const sse='data: '+JSON.stringify({id:'gen-meter',model,choices:[{delta:{content:raw},finish_reason:kind==='length'?'length':'stop'}]})+'\n\n'+(kind==='missing'?'':'data: '+JSON.stringify({choices:[],usage})+'\n\n')+'data: [DONE]\n\n';
  const receipt={gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,dispatched:true,retries:0,requestHash:hash(canonical(b)),rawHash:sha(raw),sseHash:sha(sse),status:['length','empty'].includes(kind)?'failed_or_unknown':'complete',error:['length','empty'].includes(kind)?'rpg_incomplete_output':null};
  res.statusCode=['length','empty'].includes(kind)?502:200;res.end(JSON.stringify({raw,sse,receipt}));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 t.after(async()=>{await new Promise(r=>server.close(r));assert.ok(resolve(dir).startsWith(resolve(tmpdir())+'\\shop-meter-'));await rm(dir,{recursive:true,force:true});});
 const options={directory:dir,baseURL:'http://127.0.0.1:'+server.address().port+'/',serviceToken:token,enabled:true,limit:1,measurement:{pollIntervalMs:10}};
 const transport=await createControlledProvider(options),selection={selectionId:'meter-id',selectionRevision:'meter-revision',model};
 const configured={id:model,name:model,selection,parameters:models.earth.parameters,upperBoundMicros:250000,pricing:{currency:'USD',inputPerMillion:0.75,outputPerMillion:3.75}};
 const provider=createHarnessControlledProvider({transport,models:[configured],authorized:true});
 const request={id:'call-meter',sessionId:'session-meter',messages:[{role:'system',content:'world'},{role:'user',content:'buy'}],model:{...configured,available:true},purpose:'narration'};request.messagesHash=hash(request.messages);
 const result=await provider.dispatch(request);
 assert.equal(result.status,['length','empty'].includes(kind)?'failed':'succeeded');
 if(kind==='empty')assert.equal(result.code,'PROVIDER_EMPTY_OUTPUT');
 assert.equal(result.costMicros,kind==='missing'?null:1500);
 assert.equal(result.usage.cachedInputTokens,kind==='missing'?null:600);
 const timing=result.measurement.timing;if(kind==='empty')assert.equal(timing.firstContentObservedMs,null);else{assert.ok(timing.firstContentObservedMs>=0);assert.ok(timing.firstContentObservedMs<timing.completedMs);}assert.equal(timing.providerFirstTokenMs,null);assert.equal(timing.source,'host-progress-observation');
 if(kind!=='missing')assert.equal(result.measurement.accounting.estimatedCostMicros,1500);
 const reopened=await createControlledProvider(options),again=createHarnessControlledProvider({transport:reopened,models:[configured],authorized:true});
 assert.deepEqual((await again.lookup(request)).measurement,result.measurement);assert.equal(calls,1);
});
