import test from 'node:test';import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openSources,project} from './source.mjs';import {revise,refresh,accept,handoff} from './draft.mjs';
import {build,parse,Simulator} from './provider.mjs';import {prepare} from '../../agent-core/actions/rules.mjs';import {hash} from '../../agent-core/contracts.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/e4-tests/',import.meta.url));await mkdir(root,{recursive:true});
const sources=await openSources(await mkdtemp(root+'draft-'));
const change=(field,value)=>({field,value,quote:'界面明确修改'});
async function setup(id='cultivation'){const s=sources.get(id);return {a:s.adapter,f:await s.read()};}
const edit=(a,f,d,action,changes)=>revise(a,f,d,{action,changes,kind:'user-edit',turnId:'edit',text:'界面明确选择'});
test('click and simulated text share exact R5 arithmetic, no state writes in two configs',async()=>{
 for(const id of ['cultivation','lighthouse']){const {a,f}=await setup(id),before=hash(f.record);
 const d=edit(a,f,null,a.rule.id,[change('item',f.resources[0].id),change('quantity',2)]);
 const text=id==='cultivation'?'我想服用两份养息丹':'我想添两份灯油',request=build(f,null,text);
 const result=parse((await new Simulator().generate(request)).raw),n=revise(a,f,null,{...result,text,turnId:'text'});
 assert.deepEqual(d.preflight,n.preflight);assert.deepEqual(d.preflight,prepare(f.record,a.rule,{actionId:a.rule.id,itemId:f.resources[0].id,quantity:2}));
 assert.equal(d.preflight.expected[0].after,3);assert.equal(n.intent.quantity.status,'candidate');assert.equal(d.intent.quantity.status,'explicit');
 assert.equal(handoff(a,f,accept(a,f,d)).executionStatus,'not_executed');assert.equal(hash(f.record),before);
 }});
test('gift uses E3 evidence, not consent; switch clears incompatible fields and acceptance',async()=>{
 const {a,f}=await setup();let d=edit(a,f,null,'gift',[change('item','medicine'),change('quantity',2),change('target','zhou')]);
 d=revise(a,f,d,{text:'他已经答应了',turnId:'claim',changes:[{field:'condition',value:'他已经答应了',quote:'他已经答应了'}]});
 assert.equal(d.status,'intent-only');assert.equal(d.preflight,null);assert.equal(d.worldConditions.every(x=>x.status==='unknown'),true);
 d=accept(a,f,d);d=edit(a,f,d,a.rule.id,[]);assert.equal(d.confirmation,null);assert.deepEqual(d.intent,{});assert.equal(d.status,'clarification');
 assert.throws(()=>handoff(a,f,d),{code:'E4_NOT_ACCEPTED'});
});
test('quantity revise, withdrawal and stale sources preserve intent without silent refresh',async()=>{
 const {a,f}=await setup();let d=edit(a,f,null,a.rule.id,[change('item','medicine'),change('quantity',2)]);d=accept(a,f,d);
 d=edit(a,f,d,null,[change('quantity',1)]);assert.equal(d.confirmation,null);assert.equal(d.preflight.expected[0].after,4);
 const r=structuredClone(f.record);r.state.items[0].quantity=0;r.stateRevision++;r.revision++;const fresh=project(a,r);
 d=edit(a,fresh,d,null,[change('quantity',2)]);assert.equal(d.status,'stale');assert.equal(d.intent.quantity.value,2);assert.equal(d.source.stateRevision,0);assert.equal(d.preflight,null);
 d=refresh(a,fresh,d);assert.equal(d.status,'blocked');assert.equal(d.diagnostics[0],'STATE_INSUFFICIENT');
 d=edit(a,fresh,d,null,[change('item',null)]);assert.equal(d.status,'clarification');
});
test('uninitialized, excessive, unsupported and forged drafts never pass preflight',async()=>{
 const {a,f}=await setup();assert.throws(()=>edit(a,f,null,'fuel_lamp',[]),{code:'E4_ACTION_UNSUPPORTED'});
 assert.throws(()=>edit(a,f,null,a.rule.id,[change('quantity',0)]),{code:'E4_QUANTITY'});
 let d=edit(a,f,null,a.rule.id,[change('item','medicine'),change('quantity',6)]);assert.equal(d.status,'blocked');
 const r=structuredClone(f.record);r.state.items[0].quantity=null;d=edit(a,project(a,r),null,a.rule.id,[change('item','medicine'),change('quantity',1)]);assert.equal(d.diagnostics[0],'STATE_INITIAL_VALUE_REQUIRED');
 d=accept(a,f,edit(a,f,null,a.rule.id,[change('item','medicine'),change('quantity',1)]));d.intent.quantity.value=2;
 assert.throws(()=>handoff(a,f,d),{code:'E4_DRAFT_HASH'});
 assert.throws(()=>revise(a,f,null,{action:a.rule.id,text:'一些',turnId:'m',changes:[{field:'quantity',value:1,quote:'一些'}]}),{code:'E4_QUANTITY_EVIDENCE'});
});
test('bounded interpretation context has only current intent, capabilities and owner projection',async()=>{
 const {a,f}=await setup();const d=edit(a,f,null,a.rule.id,[]),q=build(f,{...d,privateHistory:'secret-history'},'一些');
 assert.equal(JSON.stringify(q).includes('secret-history'),false);assert.deepEqual(q.tools,[]);assert.equal(q.messages.filter(x=>x.role==='user').length,1);
 const r=parse((await new Simulator().generate(q)).raw);assert.equal(r.changes.length,0);assert.ok(r.questions.length);
 assert.throws(()=>parse('{"action":null}'),{code:'E4_REPLY_FIELDS'});
});
