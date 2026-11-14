import {test} from 'node:test';import assert from 'node:assert/strict';
import {resourceRule,prepare,verifyProposal} from './rules.mjs';import {registry} from './registry.mjs';import {parseResult,continuation} from './protocol.mjs';
const rule=resourceRule({id:'use',label:'使用',entries:s=>s.items,accept:()=>true,path:id=>'items.'+id,validateState:()=>{}});
const r={id:'s-one',stateRevision:0,binding:{v:5},state:{items:[{id:'a',name:'物资',quantity:5}]}};
test('pure prepare, recompute and stale/tampered rejection',()=>{
 const p=prepare(r,rule,{actionId:'use',itemId:'a',quantity:2});assert.equal(r.state.items[0].quantity,5);assert.equal(verifyProposal(r,rule,p).state.items[0].quantity,3);
 assert.throws(()=>verifyProposal({...r,stateRevision:1},rule,p),{code:'ACTION_STALE'});assert.throws(()=>verifyProposal({...r,id:'s-other'},rule,p),{code:'ACTION_SCOPE'});
 p.expected[0].after=99;assert.throws(()=>verifyProposal(r,rule,p),{code:'ACTION_PROPOSAL'});
});
test('invalid amounts, insufficient/unset and depleted catalog',()=>{
 for(const quantity of [0,-1,1.5,6,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>rule.evaluate(r.state,{actionId:'use',itemId:'a',quantity}));
 assert.throws(()=>rule.evaluate({items:[{id:'a',quantity:null}]},{actionId:'use',itemId:'a',quantity:1}),{code:'STATE_INITIAL_VALUE_REQUIRED'});
 const out=rule.evaluate(r.state,{actionId:'use',itemId:'a',quantity:5});assert.equal(out.state.items.length,1);assert.equal(out.state.items[0].quantity,0);
});
test('registry permissions, limits and read clone',()=>{
 const reg=registry({query:{tool:{type:'function',function:{name:'query_local_state'}},validate:()=>{},execute:s=>{s.items=[];return {rows:[]};}}},rule);
 reg.execute('query_local_state',{},r,{allowed:['query_local_state']});assert.equal(r.state.items.length,1);
 assert.throws(()=>reg.execute('prepare_action',{},r,{allowed:['query_local_state']}),{code:'TOOL_NOT_ALLOWED'});
 assert.throws(()=>reg.execute('query_local_state',{},r,{allowed:['query_local_state'],counts:{query_local_state:1}}),{code:'TOOL_LIMIT'});
});
test('native metadata retained; unknown tools and incomplete replies rejected',()=>{
 const tools=[{type:'function',function:{name:'prepare_action'}}],message={role:'assistant',content:null,tool_calls:[{id:'c-one',type:'function',function:{name:'prepare_action',arguments:'{}'},extra_content:{google:{thought_signature:'opaque'}}}]};
 const p=parseResult({status:'completed',finishReason:'tool_calls',message},tools),next=continuation({messages:[]},p.message,{status:'review'},[]);
 assert.deepEqual(next.messages[0],message);assert.equal(next.messages[1].tool_call_id,'c-one');
 assert.throws(()=>parseResult({status:'completed',finishReason:'tool_calls',message},[]),{code:'TOOL_NOT_ALLOWED'});
 assert.throws(()=>parseResult({status:'completed',finishReason:'length',message:{role:'assistant',content:'cut'}},tools));
});
