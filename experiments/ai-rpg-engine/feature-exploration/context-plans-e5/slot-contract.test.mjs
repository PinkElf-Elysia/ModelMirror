import test from 'node:test';import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {createHash} from 'node:crypto';
import {openSources} from '../context-actions-e4/source.mjs';
import {build,parse,CONTRACT} from './provider.mjs';import {apply,project} from './plan.mjs';
const root=new URL('../../.rpg03-work/e5-tests/',import.meta.url);await mkdir(root,{recursive:true});
const sources=await openSources(await mkdtemp(fileURLToPath(new URL('slots-',root))));
const source=sources.get('cultivation'),frame=await source.read(),adapter=source.adapter;
const input='先服用两份养息丹，再给周禾一份养息丹；如果他不收，就不送。';
const wrap=ops=>({ops,question:null,limit:null});
const change=(field,value,quote)=>({field,value,quote});
const medicine=()=>({type:'add',action:'use_medicine',changes:[change('item','medicine','养息丹'),change('quantity',2,'两份')],condition:null,quote:'服用两份养息丹'});
const gift=()=>({type:'add',action:'gift',changes:[change('target','zhou','周禾'),change('item','medicine','养息丹'),change('quantity',1,'一份')],condition:{kind:'prerequisite',value:'如果他不收，就不送。',quote:'如果他不收，就不送。'},quote:'给周禾一份养息丹'});
const applyOps=ops=>apply(adapter,frame,null,wrap(ops),{text:input,turnId:'fixture'});

test('saved raw Gemini reply still fails unchanged, without alias conversion or partial adoption',async()=>{
 const before=JSON.stringify(await source.read()),parsed=parse(SAVED_RAW),original=JSON.stringify(parsed);
 assert.equal(createHash('sha256').update(SAVED_RAW).digest('hex'),SAVED_SHA256);
 assert.throws(()=>applyOps(parsed.ops),{code:'E4_CHANGES'});
 assert.equal(JSON.stringify(parsed),original);assert.equal(JSON.stringify(await source.read()),before);
 assert.deepEqual(parsed.ops[0].changes.map(c=>c.field),['actionId','itemId','quantity']);
});

test('field aliases remain rejected even below the changes count limit',()=>{
 for(const field of ['actionId','itemId','targetId','parameters']){
  const op=medicine();op.changes=[change(field,'medicine','养息丹')];assert.throws(()=>applyOps([op]),{code:'E4_FIELD'});
  const g=gift();g.changes=[change(field,'medicine','养息丹')];assert.throws(()=>applyOps([g]),{code:'INTENT_FIELD'});
 }
 const duplicated=medicine();duplicated.changes=[change('item','medicine','养息丹'),change('item','medicine','养息丹')];assert.throws(()=>applyOps([duplicated]),{code:'E4_FIELD'});
 const nested=medicine();nested.parameters={itemId:'medicine',quantity:2};assert.throws(()=>applyOps([nested]));
 const conditionInChanges=gift();conditionInChanges.changes=[change('condition','如果他不收，就不送。','如果他不收，就不送。')];assert.throws(()=>applyOps([conditionInChanges]),{code:'E5_USE_STEP_CONDITION'});
});

test('independent authored valid fixture uses proposal fields and preserves no-execution boundary',async()=>{
 // Independently authored expected structure, never a repaired provider reply.
 const before=JSON.stringify(await source.read()),p=applyOps([medicine(),gift()]),v=project(adapter,frame,p);
 assert.deepEqual(v.steps.map(s=>s.atomic.action),['use_medicine','gift']);assert.equal(v.steps[0].preflight.expected[0].after,3);
 assert.equal(v.steps[1].atomic.intent.target.value,'zhou');assert.equal(v.steps[1].atomic.intent.quantity.value,1);assert.equal(v.steps[1].preflight,null);
 assert.equal(v.steps[1].condition.value,'如果他不收，就不送。');assert.equal(JSON.stringify(await source.read()),before);
});

test('request separates proposal slots from unchanged capability parameter schemas',()=>{
 const r=build(frame,null,input),u=JSON.parse(r.messages[1].content);assert.equal(r.messages[0].content,CONTRACT);
 for(const text of ['use_medicine、fuel_lamp仅item和quantity','gift仅target、item、quantity','actionId','itemId','targetId','宿主不会自动改名或删除错误字段','E5条件只放在步骤op.condition'])assert.ok(CONTRACT.includes(text),text);
 assert.deepEqual(u.capabilities,frame.capabilities.filter(c=>c.kind!=='read'));assert.equal(u.playerText,input);assert.deepEqual(r.tools,[]);assert.equal(r.tool_choice,'none');
 assert.ok(u.capabilities.find(c=>c.id==='use_medicine').parameters.properties.itemId);
});

test('same explicit slot names work for the registered lighthouse rule only',async()=>{
 const s=sources.get('lighthouse'),f=await s.read(),before=JSON.stringify(f),text='添两份灯油';
 const p=apply(s.adapter,f,null,wrap([{type:'add',action:'fuel_lamp',changes:[change('item','lamp-oil','灯油'),change('quantity',2,'两份')],condition:null,quote:text}]),{text,turnId:'light-fixture'});
 assert.equal(project(s.adapter,f,p).steps[0].preflight.expected[0].after,3);assert.equal(JSON.stringify(await s.read()),before);
 assert.throws(()=>applyOps([{...medicine(),action:'fuel_lamp'}]),{code:'E4_ACTION_UNSUPPORTED'});
});

// Frozen original provider response, preserved verbatim as a regression fixture.
const SAVED_RAW="```json\n{\n  \"ops\": [\n    {\n      \"type\": \"add\",\n      \"action\": \"use_medicine\",\n      \"changes\": [\n        {\n          \"field\": \"actionId\",\n          \"value\": \"use_medicine\",\n          \"quote\": \"服用\"\n        },\n        {\n          \"field\": \"itemId\",\n          \"value\": \"medicine\",\n          \"quote\": \"养息丹\"\n        },\n        {\n          \"field\": \"quantity\",\n          \"value\": 2,\n          \"quote\": \"两份\"\n        }\n      ],\n      \"condition\": null,\n      \"quote\": \"先服用两份养息丹\"\n    },\n    {\n      \"type\": \"add\",\n      \"action\": \"gift\",\n      \"changes\": [\n        {\n          \"field\": \"target\",\n          \"value\": \"zhou\",\n          \"quote\": \"周禾\"\n        },\n        {\n          \"field\": \"itemId\",\n          \"value\": \"medicine\",\n          \"quote\": \"养息丹\"\n        },\n        {\n          \"field\": \"quantity\",\n          \"value\": 1,\n          \"quote\": \"一份\"\n        }\n      ],\n      \"condition\": {\n        \"kind\": \"prerequisite\",\n        \"value\": \"如果他不收，就不送。\",\n        \"quote\": \"如果他不收，就不送。\"\n      },\n      \"quote\": \"再给周禾一份养息丹；如果他不收，就不送。\"\n    }\n  ],\n  \"question\": null,\n  \"limit\": null\n}\n```";
const SAVED_SHA256="9f2c9aa49ad0db7e39138ccdf159b2a1ead4c43fab6a7a606a39c3b1cfc6e9fb";
