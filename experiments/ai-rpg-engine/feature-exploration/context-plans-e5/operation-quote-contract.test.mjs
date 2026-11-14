import test from 'node:test';import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {createHash} from 'node:crypto';
import {openSources} from '../context-actions-e4/source.mjs';import {build,parse,CONTRACT} from './provider.mjs';
import {apply,project,save,exportPlan} from './plan.mjs';
const root=new URL('../../.rpg03-work/e5-tests/',import.meta.url);await mkdir(root,{recursive:true});
const sources=await openSources(await mkdtemp(fileURLToPath(new URL('operation-quote-',root))));
const source=sources.get('cultivation'),frame=await source.read(),adapter=source.adapter;
const initialText='先服用两份养息丹，再给周禾一份养息丹；如果他不收，就不送。';
const text='撤回第一步服药。第二步仍给周禾养息丹，数量改为两份。把第二步原条件替换为我的陈述：“他已经答应了。”这只是我的陈述，尚未核实。仍只拟方案，不要执行。';
const c=(field,value,quote)=>({field,value,quote}),wrap=ops=>({ops,question:null,limit:null});
const previous=save(adapter,frame,apply(adapter,frame,null,wrap([
 {type:'add',action:'use_medicine',changes:[c('item','medicine','养息丹'),c('quantity',2,'两份')],condition:null,quote:'先服用两份养息丹'},
 {type:'add',action:'gift',changes:[c('target','zhou','周禾'),c('item','medicine','养息丹'),c('quantity',1,'一份')],condition:{kind:'prerequisite',value:'如果他不收，就不送。',quote:'如果他不收，就不送。'},quote:'再给周禾一份养息丹'}
]),{text:initialText,turnId:'initial-fixture'}));
// Independently authored fixture, not a repaired provider reply.
const valid=()=>wrap([{type:'remove',stepId:'step-1',quote:'撤回第一步服药。'},
 {type:'revise',stepId:'step-2',action:null,changes:[c('quantity',2,'两份')],condition:{kind:'player-claim',value:'他已经答应了。',quote:'他已经答应了。'},quote:'第二步仍给周禾养息丹，数量改为两份。'}]);
const run=r=>apply(adapter,frame,previous,r,{text,turnId:'revision-fixture'});

test('saved raw response remains rejected: slot and condition quotes cannot replace operation quote',async()=>{
 assert.equal(createHash('sha256').update(SAVED_RAW).digest('hex'),SAVED_SHA256);
 const parsed=parse(SAVED_RAW),rawBefore=JSON.stringify(parsed),planBefore=JSON.stringify(previous),worldBefore=JSON.stringify(await source.read());
 assert.equal(parsed.ops[1].quote,undefined);assert.equal(parsed.ops[1].changes[0].quote,'两份');assert.equal(parsed.ops[1].condition.quote,'他已经答应了。');
 assert.throws(()=>run(parsed),{code:'E5_EVIDENCE'});assert.equal(JSON.stringify(parsed),rawBefore);assert.equal(JSON.stringify(previous),planBefore);assert.equal(JSON.stringify(await source.read()),worldBefore);
 assert.equal(exportPlan(adapter,frame,previous).plan.saved.revision,previous.revision);assert.equal(project(adapter,frame,previous).steps[0].preflight.expected[0].after,3);
});
test('missing empty null nonliteral operation quotes reject even with valid inner quotes',()=>{
 for(const q of [undefined,null,'','   ','已确认周禾收药']){const r=valid();if(q===undefined)delete r.ops[1].quote;else r.ops[1].quote=q;assert.throws(()=>run(r),{code:'E5_EVIDENCE'});}
});
test('independent legal revision clears save and preflight but never changes world facts',async()=>{
 const before=JSON.stringify(await source.read()),candidate=run(valid()),view=project(adapter,frame,candidate);
 assert.equal(candidate.saved,null);assert.equal(candidate.orderReview,true);assert.equal(candidate.steps.length,1);assert.equal(candidate.steps[0].id,'step-2');assert.equal(candidate.nextId,previous.nextId);
 assert.equal(candidate.steps[0].atomic.action,'gift');assert.equal(candidate.steps[0].atomic.intent.quantity.value,2);assert.equal(candidate.steps[0].atomic.intent.item.value,'medicine');assert.equal(candidate.steps[0].atomic.intent.target.value,'zhou');
 assert.equal(candidate.steps[0].after,'step-1');assert.equal(candidate.steps[0].condition.kind,'player-claim');assert.equal(candidate.steps[0].condition.value,'他已经答应了。');assert.equal(view.steps[0].preflight,null);
 assert.throws(()=>exportPlan(adapter,frame,candidate),{code:'E5_NOT_SAVED'});assert.equal(JSON.stringify(await source.read()),before);
});
test('condition-only revision still requires operation quote and inner quotes remain independently validated',()=>{
 const r=valid();r.ops=[{...r.ops[1],changes:[],quote:'把第二步原条件替换为我的陈述：“他已经答应了。”'}];assert.equal(run(r).steps[1].condition.kind,'player-claim');delete r.ops[0].quote;assert.throws(()=>run(r),{code:'E5_EVIDENCE'});
 const wrongSlot=valid();delete wrongSlot.ops[1].changes[0].quote;assert.throws(()=>run(wrongSlot));
 const wrongCondition=valid();delete wrongCondition.ops[1].condition.quote;assert.throws(()=>run(wrongCondition),{code:'E5_CONDITION_LITERAL'});
});
test('actual request preserves plan and facts and explicitly separates all three quote levels',()=>{
 const request=build(frame,previous,text),u=JSON.parse(request.messages[1].content);assert.equal(request.messages[0].content,CONTRACT);
 for(const phrase of ['每条add、revise、remove都必须有自己的op.quote','revise完整结构为','changes无槽位变化时为[]','changes[i].quote','condition.quote','二者都不能替代op.quote','不会由宿主自动补写'])assert.ok(CONTRACT.includes(phrase),phrase);
 assert.equal(u.playerText,text);assert.equal(u.plan.steps.length,2);assert.deepEqual(u.facts.resources,frame.resources);assert.deepEqual(request.tools,[]);assert.equal(request.tool_choice,'none');
});

// Frozen original failed reply; never patched or adopted.
const SAVED_RAW="```json\n{\n  \"ops\": [\n    {\n      \"type\": \"remove\",\n      \"stepId\": \"step-1\",\n      \"quote\": \"撤回第一步服药。\"\n    },\n    {\n      \"type\": \"revise\",\n      \"stepId\": \"step-2\",\n      \"action\": null,\n      \"changes\": [\n        {\n          \"field\": \"quantity\",\n          \"value\": 2,\n          \"quote\": \"两份\"\n        }\n      ],\n      \"condition\": {\n        \"kind\": \"player-claim\",\n        \"value\": \"他已经答应了。\",\n        \"quote\": \"他已经答应了。\"\n      }\n    }\n  ],\n  \"question\": null,\n  \"limit\": null\n}\n```";
const SAVED_SHA256="afde6284a0d47168a6bcf18a999562a79105b2bf61cd5594f4a53f60804ff85e";
