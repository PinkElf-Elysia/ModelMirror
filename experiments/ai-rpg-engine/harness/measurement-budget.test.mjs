import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import{tmpdir}from'node:os';import{join,resolve}from'node:path';import {MeasurementBudget} from './measurement-budget.mjs';import{hash}from'./contracts.mjs';
const messages=[{role:'system',content:'world'},{role:'user',content:'buy'}],parameters={max_tokens:100};
const request={id:'call1',sessionId:'test',purpose:'narration',model:{selection:{model:'m'},parameters},messages,messagesHash:hash(messages)};
const plan={limits:{maxCalls:2,maxCostMicros:10,inputAdmissionUnits:1000},parameters,requests:['a','b'].map(id=>({id,model:'m',purpose:'narration',messagesHash:hash(messages),parametersHash:hash(parameters),reserveMicros:5}))};
async function fixture(t){const directory=await mkdtemp(join(tmpdir(),'shop-budget-'));const options={directory,plan,authorization:{confirmed:true,planHash:hash(plan),maxCalls:2,maxCostMicros:10}};const b=await MeasurementBudget.open(options);t.after(async()=>{await b.close();assert.ok(resolve(directory).startsWith(resolve(tmpdir())+'\\shop-budget-'));await rm(directory,{recursive:true,force:true});});return {b,options};}
test('unconfirmed budget never opens; changed requests and unlisted calls do not reserve',async t=>{
 const {b,options}=await fixture(t);await assert.rejects(MeasurementBudget.open({...options,authorization:null}),/NOT_AUTHORIZED/);
 await assert.rejects(b.reserve('a',{...request,messages:[]}),/REQUEST_CHANGED/);await assert.rejects(b.reserve('extra',request),/REQUEST_CHANGED/);
 assert.equal(Object.keys((await b.snapshot()).entries).length,0);
});
test('reservation survives unknown outcome and replay without new charge across reopening',async t=>{
 const {b,options}=await fixture(t);await b.reserve('a',request);await b.settle('a',request,{status:'unknown'});
 assert.equal((await b.reserve('a',request)).replayed,true);await assert.rejects(b.reserve('b',{...request,id:'call2'}),/RESULT_PENDING/);
 assert.equal(Object.keys((await b.snapshot()).entries).length,1);
 await b.close();const reopened=await MeasurementBudget.open(options);try{assert.equal((await reopened.reserve('a',request)).replayed,true);assert.equal((await reopened.snapshot()).entries.a.costMicros,null);}finally{await reopened.close();}
});
test('failed charge counted, overrun blocks following calls; same ID different request rejected',async t=>{
 const {b}=await fixture(t);await b.reserve('a',request);await b.settle('a',request,{status:'failed',costMicros:6});
 await assert.rejects(b.reserve('b',{...request,id:'call2'}),/BLOCKED/);
 await assert.rejects(b.reserve('a',{...request,id:'changed'}),/ID_COLLISION/);
 assert.equal((await b.snapshot()).entries.a.costMicros,6);
});
