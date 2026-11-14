import {test} from 'node:test';import assert from 'node:assert/strict';
import {createContextBuilder,CONTRACT} from './context.mjs';
import {validateProposal} from './model.mjs';import {intentEvidence} from './intent.mjs';
import {exampleState} from '../../local-state/example.mjs';
const build=await createContextBuilder();
const world={session:'test',branch:'test',version:1,revision:1,binding:{},enabled:true,pending:null,state:exampleState()};
const input=(text,selection=[])=>({text,selection});
const raw={goal:'拟赠送丹药',steps:['确认后拟交付'],refs:[{domain:'items',id:'medicine'},{domain:'relations',id:'zhou'}],requirements:[{itemId:'medicine',quantity:1}],questions:['那人是否为周禾？准备给多少？'],limitations:['仅提案，未执行']};
function parse(text,v=raw,selection=[]){return validateProposal(world,build(world,input(text,selection)),JSON.stringify(v),{id:'p',revision:1});}
test('ambiguous prefilled IDs and amount become clarification with no bindings or projection',()=>{
 const before=structuredClone(world),p=parse('帮我给那人一些丹药，先想想怎么安排，暂时不要动。');
 assert.equal(p.status,'clarification');for(const k of ['refs','requirements','expected','basis','steps'])assert.deepEqual(p[k],[]);
 assert(p.intentDiagnostics.length);assert.deepEqual(world,before);
});
test('missing quantity alone or object alone does not allocate; existing singleton is not a default',()=>{
 for(const text of ['把养息丹送给周禾','给那人两份养息丹','赠给周禾一些丹药']){
  const p=parse(text);assert.equal(p.status,'clarification');assert.deepEqual(p.expected,[]);assert.deepEqual(p.requirements,[]);
 }
});
test('explicit names, selected objects and quantities can remain proposals; NPC consent is still unknown',()=>{
 const v={...raw,requirements:[{itemId:'medicine',quantity:2}],questions:['对方是否愿意接受仍需核对']};
 const p=parse('如果周禾愿意，拟给他两份养息丹',v);
 assert.equal(p.requirements[0].quantity,2);assert.equal(p.expected[0].after,3);assert.equal(p.status,'clarification');
 const selected=parse('拟赠两份养息丹',v,['relations:zhou']);assert.equal(selected.refs.length,2);
});
test('wrong or conflicting amount cannot silently override explicit input',()=>{
 for(const text of ['给周禾两份养息丹','两份养息丹或三份养息丹给周禾'])assert.deepEqual(parse(text).expected,[]);
 assert.deepEqual(intentEvidence(world,input('养息丹十一份给周禾')).quantities,[{itemId:'medicine',quantity:11}]);
});
test('unsupported container inference is held for clarification even when IDs and quantity are explicit',()=>{
 for(const step of ['从随身布囊中取出一份养息丹','把养息丹放入旧布囊','养息丹装在布囊里']){
  const p=parse('给周禾一份养息丹',{...raw,steps:[step],questions:[]});
  assert.equal(p.status,'clarification');assert.deepEqual(p.steps,[]);assert.deepEqual(p.expected,[]);
 }
 assert.match(CONTRACT,/容器关系/);assert.match(CONTRACT,/requirements.*空数组/);
});
test('model cannot submit its own host quantity override',()=>{
 const r=build(world,input('给周禾一些养息丹'));r.input.quantityOverride={itemId:'medicine',quantity:1};
 const p=validateProposal(world,r,JSON.stringify(raw),{id:'p',revision:1});assert.deepEqual(p.requirements,[]);
});

test('selected objects do not force unresolved proposal to bind or allocate',()=>{
 const p=parse('给他一些丹药',{...raw,refs:[],requirements:[],steps:[]},['relations:zhou']);
 assert.equal(p.status,'clarification');assert.deepEqual(p.refs,[]);assert.deepEqual(p.expected,[]);
});
test('unsupported negative fractional or Chinese large quantity is not a suffix integer',()=>{
 for(const q of ['-2','1.5','一百二','负二'])assert.deepEqual(intentEvidence(world,input(q+'份养息丹给周禾')).quantities,[]);
});
