import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {ControlledProvider,controlledProviderPort} from './controlled-provider.mjs';
import {controlReceipt,validateControlledReceipt} from './controlled-receipt.mjs';
import {fakeControl,syntheticToken,digest} from './controlled-test-support.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6aj-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t,options={}){
 const control=await fakeControl(t,options),directory=await mkdtemp(join(root,'run-'));let provider;
 const open=async()=>{provider=await ControlledProvider.open({directory,origin:control.origin,serviceToken:syntheticToken,timeoutMs:1000});};await open();t.after(()=>provider.close());
 const request={id:'request-test',sessionId:'session-test',operationId:'operation-test',kind:'story',modelChoice:control.choice(),messages:[{role:'system',content:'system'}, {role:'user',content:'player'}]};request.messagesHash=digest(request.messages);
 return {...control,directory,request,get provider(){return provider;},async reopen(){await provider.close();await open();}};
}
test('controlled receipt preserves origin, payload and restart lookup without another POST',async t=>{
 const f=await fixture(t),r=await f.provider.dispatch(f.request);assert.equal(r.kind,'control-plane');assert.equal(r.result.text,'Full original story.');
 assert.equal(f.calls.length,1);assert.deepEqual(f.calls[0].messages,f.request.messages);assert.equal(f.calls[0].sourceRequestHash,digest(f.request));
 validateControlledReceipt(r,f.request);assert.equal(controlledProviderPort(f.provider).mode,'control-plane-joint');
 await f.reopen();assert.deepEqual(await f.provider.lookup(f.request),r);assert.equal(f.calls.length,1);
 await assert.rejects(()=>f.provider.dispatch(f.request),{code:'CONTROL_REQUEST_ALREADY_CLAIMED'});
 const names=await readdir(join(f.directory,'records'));for(const name of names){const content=await readFile(join(f.directory,'records',name),'utf8');assert.ok(!content.includes(syntheticToken));}
});
test('lost response is recovered by original GET, never reposted',async t=>{
 const f=await fixture(t,{dropAfterStored:true});await assert.rejects(()=>f.provider.dispatch(f.request),{code:'CONTROL_RESULT_UNKNOWN'});
 await f.reopen();const r=await f.provider.lookup(f.request);assert.equal(r.result.status,'completed');assert.equal(f.calls.length,1);assert.equal(f.lookups.length,1);
});
test('unknown receipt and raw are retained while recovery stays unknown',async t=>{
 const f=await fixture(t,{status:'unknown'});await assert.rejects(()=>f.provider.dispatch(f.request),{code:'CONTROL_RESULT_UNKNOWN'});
 await f.reopen();assert.equal(await f.provider.lookup(f.request),null);assert.equal(f.calls.length,1);
 const names=await readdir(join(f.directory,'records')),contents=await Promise.all(names.map(n=>readFile(join(f.directory,'records',n),'utf8')));assert.ok(contents.some(v=>v.includes('Full original story.')));
});
test('absence never authorizes recovery to dispatch',async t=>{
 const f=await fixture(t);assert.equal(await f.provider.lookup(f.request),null);assert.equal(f.calls.length,0);
});
test('known failed and length receipts remain distinct',async t=>{
 const f=await fixture(t,{finishReason:'length'}),r=await f.provider.dispatch(f.request);assert.equal(r.result.finishReason,'length');
 const f2=await fixture(t,{status:'failed'}),failed=await f2.provider.dispatch(f2.request);assert.equal(failed.result.finishReason,'control_failure');
});
for(const change of ['request','model','parameters','raw','source'])test('reject mismatched '+change,async t=>{
 const f=await fixture(t,{corrupt:v=>{if(change==='request')v.receipt.requestId='other';if(change==='model')v.receipt.actualModel='other';if(change==='parameters')v.receipt.parameters.max_tokens=42;if(change==='raw')v.raw+='changed';if(change==='source')v.receipt.sourceRequestHash='0'.repeat(64);}});
 await assert.rejects(()=>f.provider.dispatch(f.request),{code:'CONTROL_RECEIPT_MISMATCH'});assert.equal(f.calls.length,1);
 await assert.rejects(()=>f.provider.dispatch(f.request),{code:'CONTROL_REQUEST_ALREADY_CLAIMED'});
});
test('reject nonloopback endpoints and invalid credentials before opening storage',async t=>{
 const directory=await mkdtemp(join(root,'invalid-'));
 for(const origin of ['https://example.com','http://localhost:9999','http://127.0.0.1:9999/path','http://127.0.0.1:99999'])await assert.rejects(()=>ControlledProvider.open({directory,origin,serviceToken:syntheticToken}),{code:'CONTROL_ENDPOINT_INVALID'});
 await assert.rejects(()=>ControlledProvider.open({directory,origin:'http://127.0.0.1:1',serviceToken:'short'}),{code:'CONTROL_CREDENTIAL_REQUIRED'});
 assert.equal(controlledProviderPort({dispatch(){}}),null);
});


test('readiness checks execution gate without creating tasks or allowing absence replay',async t=>{
 const f=await fixture(t);assert.equal((await controlledProviderPort(f.provider).ready()).executionEnabled,true);assert.equal(f.calls.length,0);
 const closed=await fixture(t,{ready:false});await assert.rejects(()=>closed.provider.ready(),{code:'CONTROL_EXECUTION_DISABLED'});assert.equal(closed.calls.length,0);
 for(const options of [{readinessStatus:503},{readinessValue:{executionEnabled:true}},{readinessValue:{format:'modelmirror.rpg.authoring-readiness/1',executionEnabled:true,dispatched:true}}]){
  const broken=await fixture(t,options);await assert.rejects(()=>broken.provider.ready(),{code:'CONTROL_READINESS_UNAVAILABLE'});assert.equal(broken.calls.length,0);
 }
});
test('undispatched terminal rejection preserves identity and is recovered without POST',async t=>{
 const f=await fixture(t,{status:'failed',dispatched:false});const r=await f.provider.dispatch(f.request);
 assert.equal(r.control.dispatched,false);assert.equal(r.result.status,'failed');assert.equal(r.control.error,'rpg_authoring_execution_disabled');
 await f.reopen();assert.deepEqual(await f.provider.lookup(f.request),r);await assert.rejects(()=>f.provider.dispatch(f.request),{code:'CONTROL_REQUEST_ALREADY_CLAIMED'});assert.equal(f.calls.length,1);
});
