import test from 'node:test';import assert from 'node:assert/strict';import {createHarnessControlledProvider} from './controlled-provider.mjs';import {hash} from './contracts.mjs';
const model={id:'model-a',name:'A',selection:{selectionId:'s',selectionRevision:'r',model:'model-a'},parameters:{max_tokens:200},upperBoundMicros:100};
const request={id:'call-x',sessionId:'session',messages:[{role:'system',content:'world'},{role:'user',content:'act'}],model:{...model,available:true},purpose:'narration'};
request.messagesHash=hash(request.messages);
function fixture(status='complete',failureKnown=false){let calls=0;const raw=status==='complete'?'店主点头。':'';
 const payload={sessionId:request.sessionId,requestId:request.id,selectionId:'s',selectionRevision:'r',messages:request.messages,parameters:model.parameters};
 const out={raw,failureKnown,evidence:{record:{sessionId:'session',requestId:'call-x',requestedModel:'model-a',actualModel:'model-a',selectionId:'s',selectionRevision:'r',parameters:model.parameters,rawHash:hash(raw),requestHash:hash(JSON.stringify(payload)),status}}};
 return {out,transport:{generate:async()=>{calls++;if(status!=='complete')throw Error('transport');},recoveredOutput:async()=>out},get calls(){return calls;}};
}
test('controlled bridge defaults to disabled and never reads secrets',async()=>{
 const f=fixture(),p=createHarnessControlledProvider({transport:f.transport,models:[model]});assert.equal((await p.catalog())[0].available,false);
 await assert.rejects(p.dispatch(request),/SHOP_REAL_CALL_NOT_AUTHORIZED/);assert.equal(f.calls,0);
});
test('verified original receipt is reusable and unknown billing stays unknown',async()=>{
 const f=fixture(),p=createHarnessControlledProvider({transport:f.transport,models:[model],authorized:true});
 const r=await p.dispatch(request);assert.equal(r.status,'succeeded');assert.equal(r.costMicros,null);await p.lookup(request);assert.equal(f.calls,1);
 f.out.evidence.record.requestHash='0'.repeat(64);assert.equal((await p.lookup(request)).status,'unknown');
});
test('only known terminal transport failure permits fallback',async()=>{
 const f=fixture('failed_or_unknown'),p=createHarnessControlledProvider({transport:f.transport,models:[model],authorized:true});
 assert.equal((await p.dispatch(request)).status,'unknown');f.out.failureKnown=true;
 assert.equal((await p.lookup(request)).safeToRetry,true);assert.equal(f.calls,1);
});

test('measurement replay settles recovered receipt without redispatch',async()=>{
 const f=fixture();let reserved=false,settles=0;
 const budget={reserve:async()=>{const replayed=reserved;reserved=true;return {replayed};},settle:async()=>{settles++;}};
 const p=createHarnessControlledProvider({transport:f.transport,models:[model],authorized:true,measurementRun:{budget,testId:'sample'}});
 await p.dispatch(request);await p.dispatch(request);
 assert.equal(f.calls,1);assert.equal(settles,2);
});
test('budget rejection occurs before transport call',async()=>{
 const f=fixture();const budget={reserve:async()=>{throw Error('NOT_AUTHORIZED');}};
 const p=createHarnessControlledProvider({transport:f.transport,models:[model],authorized:true,measurementRun:{budget,testId:'sample'}});
 await assert.rejects(p.dispatch(request),/NOT_AUTHORIZED/);assert.equal(f.calls,0);
});
