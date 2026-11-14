import test from 'node:test';import assert from 'node:assert/strict';
import {validate,parseWire} from './wire.mjs';import {QUERY_TOOL} from '../observations.mjs';
const base=()=>({messages:[{role:'system',content:'host'},{role:'assistant',content:'opening'},{role:'user',content:'question'},{role:'system',content:'post-history'}],tools:[QUERY_TOOL],tool_choice:'auto'});
test('native actor messages preserve greeting and post-system without relaxing legacy world protocol',()=>{
 assert.deepEqual(validate(base()),base());const b=base();b.tools=[{...QUERY_TOOL,function:{...QUERY_TOOL.function,name:'prepare_action'}}];assert.throws(()=>validate(b));
});
test('native metadata and tool ID pair preserved; third batch and wrong ID rejected',()=>{
 const b=base(),call={id:'call-a',type:'function',function:{name:'query_local_state',arguments:'{"domain":"inventory"}'},extra_content:{google:{thought_signature:'opaque'}}};
 b.messages.push({role:'assistant',content:null,tool_calls:[call]},{role:'tool',tool_call_id:'call-a',content:'{"items":[]}'});b.tools=[];b.tool_choice='none';
 assert.deepEqual(validate(b),b);b.messages.at(-1).tool_call_id='foreign';assert.throws(()=>validate(b));
});
test('SSE parses final, native query and rejects model mismatch',()=>{
 const sse='data: '+JSON.stringify({model:'test',choices:[{delta:{content:'hello'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n';
 assert.equal(parseWire({kind:'sse',sse,model:'test'}).raw,'hello');assert.throws(()=>parseWire({kind:'sse',sse,model:'other'}));
});
