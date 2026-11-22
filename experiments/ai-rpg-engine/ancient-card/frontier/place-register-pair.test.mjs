import test from 'node:test';
import assert from 'node:assert/strict';
import {decodeSSE,simulatedWire} from '../../agent-core/actions/transport.mjs';
import {registerTools,registerPair,registerTurn,startRegister} from './place-register.mjs';

const call=(id,name,node)=>({id,type:'function',function:{name,arguments:JSON.stringify({node})}});
const query=call('q','query_map','台湾'), draft=call('d','propose_place_order','大员');
const result=(...tools)=>({status:'completed',finishReason:'tool_calls',message:{role:'assistant',content:null,tool_calls:tools}});
const decode=wire=>decodeSSE(wire,registerTools,{allowedPair:registerPair});

test('interleaved fragments match call IDs; the default decoder stays single-call',async()=>{
  const msg=result(query,draft);
  const wire=simulatedWire(msg);
  assert.throws(()=>decodeSSE(wire,registerTools),/TRANSPORT_CALL/);
  assert.deepEqual(decode(wire).message,msg.message);
  assert.throws(()=>decode(wire.replace('[DONE]','')),/TRUNCATED/);
  const w=startRegister(),audit=[];
  await registerTurn(w,'请拟大员旨稿',async()=>decode(wire),audit);
  assert.deepEqual(w.pending,{node:'大员',kind:'order'});
  assert.deepEqual(w.facts,[]);
  assert.deepEqual(audit.map(e=>e.tool_call_id),['q','d']);
  assert.deepEqual(audit[0].result.entries.map(e=>e.holder),['荷兰东印度公司','西班牙','各社自治']);
});

test('reject malformed, unrelated, repeated and out-of-order calls before a draft or audit is adopted',async()=>{
  for(const calls of [[draft,query],[query,query],[query,{...draft,id:'q'}],[query,draft,draft],
    [call('q','query_map','不存在'),draft],[query,call('d','propose_place_order','宁远')],
    [query,{...draft,function:{...draft.function,arguments:'{'}}]]){
    const w=startRegister(),audit=[];
    await assert.rejects(()=>registerTurn(w,'拟旨',async()=>decode(simulatedWire(result(...calls))),audit));
    assert.deepEqual(w,startRegister());assert.deepEqual(audit,[]);
  }
});
