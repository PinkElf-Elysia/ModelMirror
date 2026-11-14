import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openSources} from '../context-actions-e4/source.mjs';
import {empty,apply,project,save,exportPlan,refresh} from './plan.mjs';
import {Simulator,build,parse,SAMPLES,CONTRACT} from './provider.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/e5-tests/',import.meta.url));await mkdir(root,{recursive:true});
const sources=await openSources(await mkdtemp(root+'plan-'));
async function setup(id='cultivation'){const s=sources.get(id);return {a:s.adapter,f:await s.read()};}
async function say(a,f,p,text){const r=await new Simulator().generate(build(f,p,text));return apply(a,f,p,parse(r.raw),{text,turnId:'turn'});}
const command=(a,f,p,ops,text='界面明确修改')=>apply(a,f,p,{ops,question:null,limit:null},{text,turnId:'edit',kind:'user-edit'});
test('two steps retain IDs and evidence; only first unconditional rule is preflighted',async()=>{
 const {a,f}=await setup(),before=JSON.stringify(f);let p=await say(a,f,null,SAMPLES.cultivation[0]);const v=project(a,f,p);
 assert.equal(v.steps.length,2);assert.equal(v.steps[0].preflight.expected[0].after,3);assert.equal(v.steps[1].atomic.action,'gift');
 assert.equal(v.steps[1].preflight,null);assert.equal(v.steps[1].condition.value,'如果他不收，就不送。');assert.equal(v.steps[1].status,'condition');
 p=save(a,f,p);assert.equal(exportPlan(a,f,p).executionStatus,'not_executed');assert.equal(JSON.stringify(f),before);
 const id=p.steps[1].id;p=await say(a,f,p,'第二步改成两份');assert.equal(p.steps[1].id,id);assert.equal(p.steps[1].atomic.intent.quantity.value,2);
 assert.equal(p.steps[0].atomic.intent.quantity.value,2);assert.equal(p.saved,null);
});
test('ambiguous quantity changes no slots; missing single quantity can be clarified',async()=>{
 const {a,f}=await setup();let p=await say(a,f,null,SAMPLES.cultivation[0]);const steps=JSON.stringify(p.steps);
 p=await say(a,f,p,'改成两份');assert.equal(JSON.stringify(p.steps),steps);assert.match(p.question,/哪一步/);assert.throws(()=>save(a,f,p));
 p=await say(a,f,null,SAMPLES.cultivation[1]);assert.equal(p.steps[0].atomic.intent.quantity,undefined);
 p=await say(a,f,p,'两份');assert.equal(project(a,f,p).steps[0].preflight.expected[0].after,3);
});
test('withdraw and reorder preserve IDs and require explicit dependency review',async()=>{
 const {a,f}=await setup();let p=await say(a,f,null,SAMPLES.cultivation[0]);const second=p.steps[1].id;
 p=await say(a,f,p,'先不要服药');assert.equal(p.steps[0].id,second);assert.equal(p.steps[0].after,'step-1');assert.equal(p.orderReview,true);
 assert.equal(project(a,f,p).steps[0].preflight,null);assert.throws(()=>save(a,f,p));
 p=command(a,f,p,[{type:'review-order',quote:'界面明确修改'}]);assert.equal(p.steps[0].after,null);assert.equal(p.orderReview,false);
 p=await say(a,f,null,SAMPLES.cultivation[0]);p=command(a,f,p,[{type:'move',stepId:p.steps[1].id,to:0,quote:'界面明确修改'}]);
 assert.equal(p.steps[0].id,second);assert.equal(p.orderReview,true);assert.ok(project(a,f,p).steps.every(s=>s.preflight===null));
});
test('condition preserves exact punctuation and never grants consent; paraphrase rejects',async()=>{
 const {a,f}=await setup();let p=await say(a,f,null,SAMPLES.cultivation[0]);p=await say(a,f,p,'第二步的条件改为：他已经答应了。');
 assert.deepEqual(p.steps[1].condition,{kind:'player-claim',value:'他已经答应了。',quote:'他已经答应了。'});
 assert.equal(project(a,f,p).steps[1].status,'condition');assert.equal(project(a,f,p).steps[1].preflight,null);
 const op={type:'revise',stepId:p.steps[1].id,action:null,changes:[],condition:{kind:'player-claim',value:'周禾已经答应',quote:'他已经答应了。'},quote:'他已经答应了。'};
 assert.throws(()=>apply(a,f,p,{ops:[op],question:null,limit:null},{text:'他已经答应了。',turnId:'bad'}),{code:'E5_CONDITION_LITERAL'});
});
test('separate valid steps never imply chain affordability or future balance',async()=>{
 const {a,f}=await setup();const p=await say(a,f,null,'先服用两份养息丹，再服用四份养息丹。'),v=project(a,f,p);
 assert.equal(v.steps[0].preflight.expected[0].after,3);assert.equal(v.steps[1].preflight,null);assert.equal(v.steps[1].status,'waiting');
 assert.equal(p.steps[1].atomic.preflight,undefined);assert.equal(p.steps[1].atomic.giftEstimate,undefined);
});
test('limits, unsupported targets and unknown reply preserve full player input and plan',async()=>{
 const {a,f}=await setup();let p=await say(a,f,null,SAMPLES.cultivation[0]);const prior=JSON.stringify(p.steps);
 for(const text of ['先服药，再送药，再休息，再修炼。','如果他同意，否则再循环做一遍']){
  p=await say(a,f,p,text);assert.ok(p.limit);assert.equal(JSON.stringify(p.steps),prior);
 }
 assert.throws(()=>parse(JSON.stringify({ops:[{type:'move'}],question:null,limit:null})),{code:'E5_MODEL_OPERATION'});
 assert.throws(()=>parse('说明\n```json\n{}\n```'));assert.match(CONTRACT,/标点/);
});
test('three-step cap rejects whole fourth addition; forbidden IDs and snapshots reject',async()=>{
 const {a,f}=await setup();let p=empty(f);const op={type:'add',action:'use_medicine',changes:[],condition:null,quote:'服药'};
 p=command(a,f,p,[op,op,op],'服药');assert.equal(p.steps.length,3);
 assert.throws(()=>command(a,f,p,[op],'服药'),{code:'E5_STEP_LIMIT'});assert.equal(p.steps.length,3);
 assert.throws(()=>command(a,f,p,[{type:'revise',stepId:'step-1',action:null,changes:[{field:'item',value:'forged',quote:'不存在'}],quote:'不存在'}],'不存在'));
 assert.throws(()=>parse('{"ops":[],"question":null,"limit":null,"state":{}}'));
});
test('stale state needs explicit refresh; same plan logic works for lighthouse',async()=>{
 const {a,f}=await setup('lighthouse');let p=await say(a,f,null,SAMPLES.lighthouse[0]);const v=project(a,f,p);
 assert.equal(v.steps[0].preflight.expected[0].after,3);assert.equal(v.steps[1].preflight,null);
 p=save(a,f,p);const next=structuredClone(f);next.reference.stateRevision++;
 assert.equal(project(a,next,p).stale,true);assert.throws(()=>exportPlan(a,next,p),{code:'E5_SOURCE_STALE'});
 p=refresh(next,p);assert.equal(p.saved,null);assert.equal(project(a,next,p).stale,false);
});
