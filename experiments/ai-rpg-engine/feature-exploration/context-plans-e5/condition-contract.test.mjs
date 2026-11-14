import test from 'node:test';import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {openSources} from '../context-actions-e4/source.mjs';
import {build,parse,CONTRACT} from './provider.mjs';
import {apply,project,save,exportPlan} from './plan.mjs';
const root=new URL('../../.rpg03-work/e5-tests/',import.meta.url);await mkdir(root,{recursive:true});
const sources=await openSources(await mkdtemp(fileURLToPath(new URL('condition-',root))));
const source=sources.get('cultivation'),frame=await source.read(),adapter=source.adapter;
const wrap=ops=>({ops,question:null,limit:null});
const gift=(text,condition)=>({type:'add',action:'gift',changes:[{field:'item',value:'medicine',quote:'养息丹'},{field:'quantity',value:1,quote:'一份'},{field:'target',value:'zhou',quote:'周禾'}],condition:{kind:'prerequisite',value:condition,quote:condition},quote:text});

test('actual request clarifies single-step guard versus alternate action without changing other assembly',async()=>{
 const before=JSON.stringify(frame),text='给周禾一份养息丹；如果他不收，就不送。',r=build(frame,null,text);
 assert.equal(r.messages.filter(m=>m.role==='system').length,1);assert.equal(r.messages[0].content,CONTRACT);
 for(const phrase of ['不满足仅不做该步','没有另一项替代行动','否则卖药','不清楚条件属于哪一步','player-claim','不增加“不送”步骤','不改写条件为正向表达'])assert.ok(CONTRACT.includes(phrase),phrase);
 assert.deepEqual(JSON.parse(r.messages[1].content),{facts:{resources:frame.resources,people:frame.people,coverage:frame.coverage},capabilities:frame.capabilities.filter(c=>c.kind!=='read'),plan:null,playerText:text});
 assert.deepEqual(r.tools,[]);assert.equal(r.tool_choice,'none');assert.equal(JSON.stringify(frame),before);
});

test('synthetic one-step negative guards remain literal, unresolved and unexecuted',()=>{
 const before=JSON.stringify(frame);
 for(const condition of ['如果他不收，就不送。','只在他愿意时赠送，否则不做这一步。']){
  const text='给周禾一份养息丹；'+condition;
  // Authored fixture verifies existing application semantics, not model understanding.
  const p=apply(adapter,frame,null,parse(JSON.stringify(wrap([gift(text,condition)]))),{text,turnId:'fixture'}),v=project(adapter,frame,p);
  assert.equal(v.steps.length,1);assert.equal(v.steps[0].condition.value,condition);assert.equal(v.steps[0].status,'condition');assert.equal(v.steps[0].preflight,null);
  assert.equal(exportPlan(adapter,frame,save(adapter,frame,p)).executionStatus,'not_executed');
 }
 assert.equal(JSON.stringify(frame),before);
});

test('unsupported alternate-flow fixture retains prior plan; validators still reject nested structure and paraphrase',()=>{
 const text='给周禾一份养息丹；如果他不收，就不送。',op=gift(text,'如果他不收，就不送。');
 const p=apply(adapter,frame,null,wrap([op]),{text,turnId:'first'}),before=JSON.stringify(p);
 const unsupported='他同意就给周禾一份养息丹，否则卖掉养息丹。';
 const r=parse(JSON.stringify({ops:[],question:null,limit:'本轮不支持条件两侧分别执行不同动作。'}));
 const v=apply(adapter,frame,p,r,{text:unsupported,turnId:'limit'});assert.deepEqual(v.steps,p.steps);assert.throws(()=>save(adapter,frame,v),{code:'E5_REVIEW_REQUIRED'});
 const bad=structuredClone(op);bad.condition.branches=[{action:'gift'}];assert.throws(()=>apply(adapter,frame,null,wrap([bad]),{text,turnId:'bad'}));
 const rewrite=structuredClone(op);rewrite.condition.value='如果周禾不收，就不赠送。';assert.throws(()=>apply(adapter,frame,null,wrap([rewrite]),{text,turnId:'rewrite'}),{code:'E5_CONDITION_LITERAL'});
 assert.throws(()=>apply(adapter,frame,p,{...r,ops:[op]},{text,turnId:'partial'}),{code:'E5_PARTIAL_LIMIT'});
 assert.throws(()=>parse('{"ops":[{"type":"execute"}],"question":null,"limit":null}'),{code:'E5_MODEL_OPERATION'});
 assert.equal(JSON.stringify(p),before);
});

test('ambiguous attachment asks without prefill; player claim remains unresolved',()=>{
 const text='给周禾一份养息丹；如果他不收，就不送。',p=apply(adapter,frame,null,wrap([gift(text,'如果他不收，就不送。')]),{text,turnId:'first'});
 const q=apply(adapter,frame,p,{ops:[],question:'这个条件约束哪一步？',limit:null},{text:'如果合适就做。',turnId:'question'});
 assert.deepEqual(q.steps,p.steps);assert.throws(()=>save(adapter,frame,q));
 const claim='他已经答应了。',revised=apply(adapter,frame,p,wrap([{type:'revise',stepId:p.steps[0].id,action:null,changes:[],condition:{kind:'player-claim',value:claim,quote:claim},quote:claim}]),{text:claim,turnId:'claim'});
 assert.equal(project(adapter,frame,revised).steps[0].status,'condition');assert.equal(project(adapter,frame,revised).steps[0].preflight,null);
});

test('saved failed reply stays an empty limited plan; no repair or reinterpretation',()=>{
 const raw='{"ops":[],"question":null,"limit":"输入包含分支/后验条件逻辑（“如果他不收，就不送”），当前仅支持最多三个线性有序步骤，不支持分支流程。"}';
 const result=parse(raw),p=apply(adapter,frame,null,result,{text:'先服用两份养息丹，再给周禾一份养息丹；如果他不收，就不送。',turnId:'old-result'});
 assert.deepEqual(p.steps,[]);assert.equal(p.limit,result.limit);assert.throws(()=>save(adapter,frame,p),{code:'E5_PLAN_MISSING'});
});
