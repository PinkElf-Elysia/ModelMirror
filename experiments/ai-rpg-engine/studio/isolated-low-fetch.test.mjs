import test from 'node:test';
import assert from 'node:assert/strict';
import {isolatedLowFetch} from './isolated-low-fetch.mjs';
test('explicit low transport preserves request and routes each purpose once',async()=>{
 const calls=[],records=[];
 const f=isolatedLowFetch({memoryInstruction:'memory',smallInstruction:'small',invoke:async x=>{calls.push(x);return {status:200,body:{raw:'fixture'}};},record:async r=>records.push(r)});
 await f('http://127.0.0.1:1/api/rpg/v1/models',{method:'GET'});
 for(const instruction of ['story','memory','small']){
  const body={requestId:instruction,sessionId:'fixture',messages:[{role:'system',content:instruction},{role:'user',content:'unchanged'}],parameters:{temperature:.7,top_p:.8,max_tokens:16384}};
  await f('http://127.0.0.1:1/api/rpg/v1/chat/completions',{method:'POST',body:JSON.stringify(body)});
  assert.deepEqual(calls.at(-1).request,body);assert.equal(calls.at(-1).purpose,instruction);
 }
 assert.equal(calls.length,4);assert.equal(records.length,3);
});
test('no other endpoint, abort, or automatic retry can invoke a model',async()=>{
 let calls=0;const f=isolatedLowFetch({memoryInstruction:'memory',smallInstruction:'small',invoke:async()=>{calls++;throw Error('synthetic connection lost');}});
 await assert.rejects(f('https://example.invalid/api/rpg/v1/models'));
 await assert.rejects(f('http://127.0.0.1:1/api/rpg/v1/chat/completions?profile=other',{method:'POST'}));
 await assert.rejects(f('http://127.0.0.1:1/api/rpg/v1/chat/completions',{method:'POST',body:JSON.stringify({messages:[]}),signal:AbortSignal.abort()}));
 assert.equal(calls,0);
 await assert.rejects(f('http://127.0.0.1:1/api/rpg/v1/models'));
 assert.equal(calls,1);
});
