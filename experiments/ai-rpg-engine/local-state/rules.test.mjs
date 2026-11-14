import test from 'node:test';import assert from 'node:assert/strict';
import {exampleState} from './example.mjs';import {applyOperations,candidate,queryState} from './rules.mjs';
const reason='本轮已发生的测试事件',op=(type,rest)=>({type,...rest,reason});
const receipt=operations=>({status:'completed',finishReason:'stop',text:'周禾道了声谢。<state_delta>'+JSON.stringify({operations})+'</state_delta>'});
test('consume and exhaust without losing archive, source or input',()=>{
 const s=exampleState(),a=applyOperations(s,[op('quantity',{id:'medicine',delta:-2})]);assert.equal(a.state.items[0].quantity,3);assert.equal(s.items[0].quantity,5);
 const b=applyOperations(a.state,[op('quantity',{id:'medicine',delta:-3})]);assert.equal(b.state.items[0].quantity,0);assert.equal(b.state.items[0].source,s.items[0].source);
});
test('balances independently calculated, overflow/unknown/negative rejected',()=>{
 const s=exampleState();const a=applyOperations(s,[op('balance',{account:'low',delta:8}),op('balance',{account:'contribution',delta:2})]);
 assert.equal(a.state.balances.low,28);assert.equal(a.state.balances.mid,0);
 for(const delta of [-21,1.5,Number.MAX_SAFE_INTEGER])assert.throws(()=>applyOperations(s,[op('balance',{account:'low',delta})]));
 s.balances.low=null;assert.throws(()=>applyOperations(s,[op('balance',{account:'low',delta:1})]),{code:'STATE_INITIAL_VALUE_REQUIRED'});
});
test('relationship event ranges and actual clamped delta',()=>{
 const s=exampleState();s.relations[0].relation=98;
 const a=applyOperations(s,[op('relation',{id:'zhou',axis:'relation',event:'help',delta:5}),op('relation',{id:'zhou',axis:'trust',event:'promise',delta:2})]);
 assert.equal(a.state.relations[0].relation,100);assert.equal(a.changes[0].after-a.changes[0].before,2);
 assert.throws(()=>applyOperations(s,[op('relation',{id:'zhou',axis:'trust',event:'promise',delta:6})]));
 const b=applyOperations(a.state,[op('relation',{id:'zhou',axis:'trust',event:'lie',delta:-5})]);assert.equal(b.state.relations[0].trust,0);
});
test('new ability, progress, equipment and qualitative states preserve rest',()=>{
 const s=exampleState(),a=applyOperations(s,[op('addAbility',{entry:{id:'rain',kind:'术法',name:'引露术',description:'原创测试术法',progress:'初学',source:'同门演示'}}),op('progress',{id:'breathing',value:'熟练'}),op('set',{field:'stamina',value:'稍有疲惫'}),op('equip',{slot:'storage',id:'bag'})]);
 assert.equal(a.state.abilities.length,2);assert.deepEqual(a.state.profile,s.profile);assert.equal(a.state.traits.stamina,'稍有疲惫');
 assert.throws(()=>applyOperations(s,[op('set',{field:'name',value:'别名'})]),{code:'STATE_FIELD_NOT_WRITABLE'});
 assert.throws(()=>applyOperations(s,[op('delete',{id:'breathing'})]),{code:'STATE_OPERATION_UNKNOWN'});
});
test('batch is atomic; forged final balance and invalid body cannot apply',()=>{
 const s=exampleState();assert.throws(()=>applyOperations(s,[op('balance',{account:'low',delta:8}),op('quantity',{id:'medicine',delta:-10})]));assert.equal(s.balances.low,20);
 assert.throws(()=>applyOperations(s,[op('balance',{account:'low',delta:1,value:99})]));
 const good=receipt([]);assert.equal(candidate(s,good).body,'周禾道了声谢。');
 for(const r of [{...good,finishReason:'length'},{...good,text:''},{...good,text:'正文'},{...good,text:good.text+'<state_delta>{}</state_delta>'},{...good,text:'关系：99'+good.text}])assert.throws(()=>candidate(s,r));
});
test('read only selected entries; new relationship defaults zero',()=>{
 const s=exampleState();assert.equal(queryState(s).items.length,0);assert.equal(queryState(s,{items:['medicine']}).items[0].quantity,5);
 const a=applyOperations(s,[op('addRelation',{id:'chen',name:'陈溪'})]);assert.equal(a.state.relations[1].trust,0);
 assert.throws(()=>candidate(s,{...receipt([]),text:'<local_relation id="missing"></local_relation>'+receipt([]).text}));
});
