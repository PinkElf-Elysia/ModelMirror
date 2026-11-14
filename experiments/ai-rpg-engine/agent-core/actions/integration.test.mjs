import {test} from 'node:test';import assert from 'node:assert/strict';
import {actionAdapters} from './adapters.mjs';import {hash} from '../contracts.mjs';
import {decodeSSE,simulatedWire,validateRequest} from './transport.mjs';import {inspectHandoff} from './e2.mjs';
const adapters=await actionAdapters();
for(const a of adapters)test(a.id+' task contracts and ownership, original untouched',async()=>{
 const r={id:'session',stateRevision:0,binding:a.binding,state:a.initial(),config:{profiles:[],mods:[]},source:{history:[]},turns:[],events:[]};
 const p=await a.assembleTask(r,'聊聊今日安排'),n=await a.assembleTask(r,'使用物资','narration',{eventId:'event',status:'executed'});
 assert.equal(p.tools.length,2);assert.equal(n.tools.length,0);assert.ok(n.contextTrace.materials.some(m=>m.id==='execution-receipt'&&m.selected));
 assert.ok(!n.messages[0].content.includes('数量使用 quantity'));assert.ok(!n.messages[0].content.includes('仅在回复末尾输出一份'));
 const body=a.id==='cultivation'?'正文\n<state_delta>{"operations":[{"type":"quantity","id":"medicine","delta":-2,"reason":"重复扣减"}]}</state_delta>':'正文\n<state_delta>{"operations":[{"type":"consumeSupply","id":"lamp-oil","amount":2,"reason":"重复扣减"}]}</state_delta>';
 assert.throws(()=>a.candidate(r.state,{status:'completed',finishReason:'stop',text:body}),{code:'ACTION_QUANTITY_OWNED'});
 validateRequest({messages:p.messages,tools:p.tools,tool_choice:p.tool_choice},a.tools.tools());
});
test('complete fragmented native stream supports both tools and preserves metadata',()=>{
 const a=adapters[0],tools=a.tools.tools();
 for(const name of ['query_local_state','prepare_action']){
  const message={role:'assistant',content:null,extra_content:{opaque:'kept'},tool_calls:[{id:'call',type:'function',function:{name,arguments:JSON.stringify(name==='prepare_action'?{actionId:'use_medicine',itemId:'medicine',quantity:2}:{queries:[{domain:'items'}]})},extra_content:{google:{thought_signature:'unaltered'}}}]};
  const wire=simulatedWire({message,finishReason:'tool_calls'}),out=decodeSSE(wire,tools);assert.deepEqual(out.message,message);
  const req={messages:[{role:'system',content:'规则'},{role:'user',content:'查询'},message,{role:'tool',tool_call_id:'call',content:'{}'}],tools:a.tools.tools(['prepare_action']),tool_choice:'auto'};
  assert.deepEqual(validateRequest(req,tools),req);
  assert.throws(()=>decodeSSE(wire.slice(0,-4),tools));req.messages.at(-1).tool_call_id='other';assert.throws(()=>validateRequest(req,tools));
 }
});
test('E2 confirmed gift retained as non executable; expected values never treated as state patch',()=>{
 const p={format:'modelmirror.rpg.e2-proposal/1',id:'e2',revision:1,source:{session:'old',branch:'old',version:1,hash:'a'.repeat(64)},goal:'拟赠送两份丹药',steps:['先确认对方是否接受'],requirements:[{itemId:'medicine',quantity:2}],expected:[{before:3,after:1}],status:'review',confirmation:null};
 p.hash=hash(p);p.status='awaiting_execution';p.confirmation={proposalHash:p.hash,revision:1,at:'test'};
 const v={format:'modelmirror.rpg.e2-handoff/1',executionStatus:'not_executed',requiresExecutionValidation:true,proposal:p},target={id:'new',state:{quantity:5}};
 assert.equal(inspectHandoff(v,target).status,'not_executable');assert.equal(target.state.quantity,5);
 v.proposal.expected[0].after=100;assert.throws(()=>inspectHandoff(v,target),{code:'E2_HASH'});
});
