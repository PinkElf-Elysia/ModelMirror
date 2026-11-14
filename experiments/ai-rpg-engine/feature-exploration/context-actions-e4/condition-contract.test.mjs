import test from 'node:test';import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openSources} from './source.mjs';import {CONTRACT,build,parse} from './provider.mjs';
import {revise,accept} from './draft.mjs';import {hash} from '../../agent-core/contracts.mjs';
const text='改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。';
const root=fileURLToPath(new URL('../../.rpg03-work/e4-tests/',import.meta.url));await mkdir(root,{recursive:true});
const source=(await openSources(await mkdtemp(root+'condition-'))).get('cultivation'),frame=await source.read(),adapter=source.adapter;
const previous=accept(adapter,frame,revise(adapter,frame,null,{action:'use_medicine',kind:'user-edit',turnId:'seed',changes:[{field:'item',value:'medicine',quote:''},{field:'quantity',value:2,quote:''}]}));
const fixture=condition=>({action:'gift',changes:[{field:'target',value:'zhou',quote:'周禾'},{field:'item',value:'medicine',quote:'养息丹'},{field:'quantity',value:1,quote:'一份'},
 {field:'condition',value:condition,quote:'他已经答应了'}],questions:[],unsupported:false});
test('actual request includes verbatim condition contract once and keeps player text intact',()=>{
 const q=build(frame,previous,text),system=q.messages.find(m=>m.role==='system').content;
 assert(system.includes(CONTRACT));assert.equal(system.split(CONTRACT).length,2);
 assert(system.includes('condition.value非null时必须逐字摘录当前玩家输入中的连续原文'));
 assert(system.includes('不做代词消解、概括或改写'));assert(system.includes('撤回条件时value为null'));
 assert.equal(JSON.parse(q.messages.find(m=>m.role==='user').content).playerText,text);assert.equal(q.carry.status,'accepted');
});
test('unchanged validator rejects paraphrase; verbatim fixture changes only intent and leaves consent unknown',()=>{
 const before=hash(frame.record),old=hash(previous);
 assert.throws(()=>revise(adapter,frame,previous,{...parse(JSON.stringify(fixture('周禾已经答应'))),text,turnId:'negative'}),{code:'INTENT_CONDITION_EVIDENCE'});
 const d=revise(adapter,frame,previous,{...parse('```json\n'+JSON.stringify(fixture('他已经答应了'))+'\n```'),text,turnId:'fixture'});
 assert.equal(d.action,'gift');assert.equal(d.intent.quantity.value,1);assert.equal(d.intent.target.value,'zhou');assert.equal(d.intent.item.value,'medicine');
 assert.equal(d.intent.condition.value,'他已经答应了');assert.equal(d.intent.condition.source.quote,'他已经答应了');
 assert.equal(d.status,'intent-only');assert.equal(d.preflight,null);assert.equal(d.confirmation,null);assert(d.worldConditions.every(c=>c.status==='unknown'));
 assert.equal(hash(frame.record),before);assert.equal(hash(previous),old);
});
