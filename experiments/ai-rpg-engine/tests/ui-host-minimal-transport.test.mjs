import test from 'node:test';
import assert from 'node:assert/strict';
import {receiveMinimalText} from '../ui-host/minimal-transport.mjs';
const payload={model_id:'gpt-5.6-luna',max_tokens:4096,temperature:0,require_managed_route:true,tool_mode:'none',compression:{mode:'off'},output_mode:'none',gateway:'default',messages:[{role:'user',content:'继续'}]};
const receipt={actual_model:'gpt-5.6-luna',requested_model:'gpt-5.6-luna',fallback_attempts:0,reason_codes:['qualified'],strategy:'newapi_preferred',engine:'newapi'};
const event=(value,name='message')=>(name==='message'?'':'event: '+name+'\n')+'data: '+(typeof value==='string'?value:JSON.stringify(value))+'\n\n';
function stream(text,tail=receipt){return event({model:'gpt-5.6-luna',choices:[{delta:{content:text},finish_reason:null}]})+event({choices:[{delta:{},finish_reason:'stop'}]})+event(tail,'route_receipt')+event('[DONE]');}
test('arbitrary prose and optional HTML are preserved as inert text, not parsed into panels',async()=>{
 const text='她没有立刻回答。\n<details><summary>线索</summary>未拆的信</details>\n{"not":"a turn"}';
 const raw=stream(text);let calls=0;
 const r=await receiveMinimalText(payload,{fetchImpl:async(url,options)=>{calls++;assert.equal(url,'http://127.0.0.1:18305/api/chat');assert.equal(JSON.parse(options.body).response_format,undefined);return new Response(raw,{headers:{'content-type':'text/event-stream'}});}});
 assert.equal(calls,1);assert.equal(r.text,text);assert.equal(r.raw.toString(),raw);assert.equal(r.status,'transport_complete');
});
test('route failure retains received text without repair or retry',async()=>{
 let calls=0;const r=await receiveMinimalText(payload,{fetchImpl:async()=>{calls++;return new Response(stream('原文',{...receipt,fallback_attempts:1}),{headers:{'content-type':'text/event-stream'}});}});
 assert.equal(calls,1);assert.equal(r.text,'原文');assert.equal(r.status,'failed_or_unknown');
});
test('truncated and duplicate terminal streams are not accepted as complete',async()=>{
 for(const raw of [stream('原文').replace(event('[DONE]'),''),stream('原文')+event('[DONE]')]){
 const r=await receiveMinimalText(payload,{fetchImpl:async()=>new Response(raw,{headers:{'content-type':'text/event-stream'}})});
 assert.equal(r.status,'failed_or_unknown');assert.equal(r.text,'原文');
 }
});
test('cancelled request is not retried and structured mode is not silently used',async()=>{
 let calls=0;const c=new AbortController();c.abort();
 const r=await receiveMinimalText(payload,{signal:c.signal,fetchImpl:async(_url,o)=>{calls++;o.signal.throwIfAborted();}});
 assert.equal(calls,1);assert.equal(r.error,'CANCELLED_OR_TIMEOUT');
 await assert.rejects(()=>receiveMinimalText({...payload,response_format:{}},{fetchImpl:()=>{throw Error('must not call');}}),/POLICY/);
});
