import test from 'node:test';import assert from 'node:assert/strict';
import {nativeResult,continueRequest,measureRequest} from './protocol.mjs';
import {TOOL} from './query.mjs';
const query={status:'completed',finishReason:'tool_calls',message:{role:'assistant',content:null,tool_calls:[{id:'call-a',type:'function',function:{name:'query_local_state',arguments:'{"queries":[{"domain":"items"}]}'},extra_content:{google:{thought_signature:'signature'}}}]}};
test('native query retained exactly, result uses tool role and signature is preserved',()=>{
 const r=nativeResult(query),first={messages:[{role:'system',content:'s'},{role:'user',content:'u'}],tools:[TOOL],tool_choice:'auto'};
 const next=continueRequest(first,r.message,{stateRevision:0,results:[]});
 assert.deepEqual(next.messages[2],query.message);assert.equal(next.messages[3].role,'tool');assert.equal(next.messages[3].tool_call_id,'call-a');assert.equal(next.tool_choice,'none');assert.equal(first.messages.length,2);
 assert.ok(measureRequest(next).toolResultBytes>0);assert.equal(measureRequest(next).tokens,null);
});
test('empty, truncated, undeclared, extra calls and recursive requests rejected',()=>{
 for(const bad of [{status:'completed',finishReason:'stop',message:{role:'assistant',content:''}},{status:'completed',finishReason:'length',message:{role:'assistant',content:'partial'}},{...query,message:{...query.message,tool_calls:[...query.message.tool_calls,...query.message.tool_calls]}}])assert.throws(()=>nativeResult(bad));
 assert.throws(()=>nativeResult(query,{toolChoice:'none'}));
 const bad=structuredClone(query);bad.message.tool_calls[0].function.name='write_state';assert.throws(()=>nativeResult(bad));
});
