import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {seed,INPUT,checksFor} from './revision-case.mjs';import {parse,build} from '../provider.mjs';import {apply,project,exportPlan} from '../plan.mjs';import {hash} from '../../../agent-core/contracts.mjs';
const reply=()=>({ops:[{type:'remove',stepId:'step-1',quote:'撤回第一步服药。'},{type:'revise',stepId:'step-2',action:null,changes:[{field:'quantity',value:2,quote:'两份'}],condition:{kind:'player-claim',value:'他已经答应了。',quote:'他已经答应了。'},quote:'第二步仍给周禾养息丹，数量改为两份。'}],question:null,limit:null});
const load=async()=>seed(await mkdtemp(join(resolve('experiments/ai-rpg-engine/.rpg03-work'),'e5-revision-test-')));
test('reuse saved two steps, revision invalidates save and preflight without changing world',async()=>{
 const {source,initial,plan}=await load(),before=hash(initial.record),old=hash(plan),raw=JSON.stringify(reply());
 const request=build(initial,plan,INPUT);assert.equal(JSON.parse(request.messages[1].content).plan.steps.length,2);
 const parsed=parse(raw),candidate=apply(source.adapter,initial,plan,parsed,{text:INPUT,turnId:'revision-test'}),view=project(source.adapter,initial,candidate);
 assert(Object.values(checksFor(plan,candidate,view,parsed)).every(Boolean));assert.equal(candidate.saved,null);assert.equal(candidate.revision,2);
 assert.throws(()=>exportPlan(source.adapter,initial,candidate),e=>e.code==='E5_NOT_SAVED');
 assert.equal(hash(plan),old);assert.equal(hash((await source.read()).record),before);assert.equal(initial.resources.find(r=>r.id==='medicine').quantity,5);
});
test('wrong rule aliases and changed condition punctuation are rejected without conversion',async()=>{
 const {source,initial,plan}=await load(),before=hash(plan);
 const wrong=reply();wrong.ops[1].changes[0].field='itemId';assert.throws(()=>apply(source.adapter,initial,plan,wrong,{text:INPUT,turnId:'bad'}));
 const punctuation=reply();punctuation.ops[1].condition.value='他已经答应了';assert.throws(()=>apply(source.adapter,initial,plan,punctuation,{text:INPUT,turnId:'bad'}),e=>e.code==='E5_CONDITION_LITERAL');assert.equal(hash(plan),before);
});
