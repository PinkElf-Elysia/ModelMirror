import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {actionAdapters} from '../../../agent-core/actions/adapters.mjs';
import {hash,need} from '../../../agent-core/contracts.mjs';
import {fileSource} from '../../context-actions-e4/source.mjs';
import {project,exportPlan} from '../plan.mjs';
export const INPUT='撤回第一步服药。第二步仍给周禾养息丹，数量改为两份。把第二步原条件替换为我的陈述：“他已经答应了。”这只是我的陈述，尚未核实。仍只拟方案，不要执行。';
export async function seed(directory){
 const origin=resolve('experiments/ai-rpg-engine/.rpg03-work/e5-slot-real-v1');
 const read=async name=>JSON.parse(await readFile(join(origin,name),'utf8'));
 const record=await read('source-state.json'),plan=await read('plan-final.json'),assessment=await read('assessment.json');
 need(assessment.allChecksPassed&&hash(record)==='ec441d30df9a498f7cde8e4139ed2c224209ec5659e92f8ed90e4be634972ae5','E5_FROZEN_SOURCE');
 need(hash(await read('source-after.json'))===hash(record)&&hash(plan.source)===hash((await read('saved-plan-1.json')).plan.source),'E5_SAVED_SOURCE');
 const adapter=(await actionAdapters()).find(a=>a.id==='cultivation');
 await writeFile(join(directory,'frozen-source.json'),JSON.stringify(record,null,2)+'\n',{flag:'wx'});
 const source=fileSource(adapter,join(directory,'frozen-source.json')),initial=await source.read();
 const exported=exportPlan(adapter,initial,plan),prior=await read('saved-plan-1.json');
 need(hash(exported)===hash(prior)&&plan.saved?.revision===1&&plan.steps.length===2,'E5_SAVED_PLAN');
 const beforeView=project(adapter,initial,plan);need(beforeView.steps[0].preflight?.expected[0].after===3,'E5_OLD_PREFLIGHT');
 await writeFile(join(directory,'seed-plan.json'),JSON.stringify(plan,null,2)+'\n',{flag:'wx'});
 await writeFile(join(directory,'seed-evidence.json'),JSON.stringify({origin,planHash:hash(plan),sourceHash:hash(record),beforeView,readOnly:true},null,2)+'\n',{flag:'wx'});
 return {source,initial,plan};
}
export function checksFor(previous,candidate,view,parsed){
 const g=view?.steps?.[0],oldGift=previous.steps[1],get=k=>g?.atomic.intent[k]?.value;
 return {
  previousSaved:!!previous.saved,oneStep:view?.steps.length===1,giftIDRetained:g?.id===oldGift.id&&g?.atomic.action==='gift',
  quantityTwo:get('quantity')===2,objectsRetained:get('item')==='medicine'&&get('target')==='zhou',
  noMedicine:!!view&&view.steps.every(s=>s.atomic.action!=='use_medicine'),noPreflight:!!view&&view.steps.every(s=>s.preflight===null),
  claimLiteral:g?.condition?.kind==='player-claim'&&g.condition.value==='他已经答应了。'&&g.condition.quote===g.condition.value,
  dependencyUnconfirmed:view?.orderReview===true&&g?.after===previous.steps[0].id,
  noRecreatedStep:candidate?.nextId===previous.nextId&&parsed?.ops.every(o=>o.type!=='add'),
  rawRevisionFields:parsed?.ops?.length===2&&parsed.ops.some(o=>o.type==='remove'&&o.stepId==='step-1')&&parsed.ops.some(o=>o.type==='revise'&&o.stepId==='step-2'&&o.changes.length===1&&o.changes[0].field==='quantity'&&o.changes[0].value===2)
 };
}
