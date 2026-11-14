import {test} from 'node:test';import assert from 'node:assert/strict';
import {decodeSSE,validateRequest,simulatedWire} from '../transport.mjs';import {parseResult,continuation} from '../protocol.mjs';import {actionAdapters} from '../adapters.mjs';
const tools=(await actionAdapters())[0].tools.tools(),frame=x=>'data: '+JSON.stringify(x)+'\n\n';
const call={index:0,id:'call-real-shape',type:'function',function:{name:'prepare_action',arguments:'{"actionId":"use_medicine","itemId":"medicine","quantity":2}'}};
const parts=[
 {choices:[{delta:{content:'',reasoning_details:[{type:'reasoning.text',text:'part-a',format:'google-gemini-v1',index:0}]},finish_reason:null}]},
 {choices:[{delta:{reasoning_details:[{type:'reasoning.text',text:'part-b',format:'google-gemini-v1',index:0},{type:'reasoning.encrypted',data:'opaque-fixture',id:call.id,format:'google-gemini-v1',index:0}]},finish_reason:null}]},
 {choices:[{delta:{tool_calls:[call]},finish_reason:'tool_calls'}]}
];
test('terminal usage repeat accepted without accepting more generated content; reasoning metadata retained',()=>{
 const tail={choices:[{delta:{role:'assistant',content:''},finish_reason:'tool_calls'}],usage:{prompt_tokens:100,completion_tokens:20}};
 const result=decodeSSE(parts.map(frame).join('')+frame(tail)+'data: [DONE]\n\n',tools);
 assert.equal(result.message.reasoning_details[0].text,'part-apart-b');assert.equal(result.message.reasoning_details[1].data,'opaque-fixture');
 const parsed=parseResult(result,tools),request=continuation({messages:[{role:'system',content:'rules'},{role:'user',content:'input'}]},parsed.message,{},tools.filter(t=>t.function.name==='prepare_action'));
 validateRequest(request,tools);assert.deepEqual(request.messages.at(-2).reasoning_details,result.message.reasoning_details);
 assert.deepEqual(decodeSSE(simulatedWire(result),tools).message,result.message);
 for(const delta of [{content:'new prose'},{tool_calls:[call]},{reasoning_details:[]}]){
  assert.throws(()=>decodeSSE(parts.map(frame).join('')+frame({...tail,choices:[{delta,finish_reason:'tool_calls'}]})+'data: [DONE]\n\n',tools),{code:'TRANSPORT_AFTER_FINISH'});
 }
});
