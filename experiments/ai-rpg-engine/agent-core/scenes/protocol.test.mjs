import test from 'node:test';import assert from 'node:assert/strict';
import {validateSceneRequest} from './protocol.mjs';import {QUERY} from './projection.mjs';
const request=()=>({messages:[{role:'system',content:'rules'},{role:'assistant',content:'opening'},{role:'user',content:'question'}],tools:[QUERY],tool_choice:'auto'});
test('character opening is supported, tool pairing and role boundaries are enforced',()=>{
 assert.equal(validateSceneRequest(request()).messages.length,3);
 let r=request();r.messages.push({role:'system',content:'extra'});assert.throws(()=>validateSceneRequest(r),/ROLE/);
 r=request();r.tools=[{type:'function',function:{name:'write_anywhere'}}];assert.throws(()=>validateSceneRequest(r),/TOOLS/);
 r=request();r.messages.push({role:'assistant',content:null,tool_calls:[{id:'c',type:'function',function:{name:QUERY.function.name,arguments:'{"domain":"events"}'}}]});
 assert.throws(()=>validateSceneRequest(r),/PAIR/);
 r.messages.push({role:'tool',tool_call_id:'wrong',content:'{}'});assert.throws(()=>validateSceneRequest(r),/PAIR/);
 r.messages.at(-1).tool_call_id='c';assert.equal(validateSceneRequest(r).messages.length,5);
});
