import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,mkdir} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';import {execFileSync} from 'node:child_process';
import {AuthoringStore} from '../../../authoring/store.mjs';import {ensureParticipants} from '../participants.mjs';import {SceneRuntime} from '../runtime.mjs';import {openAssembly} from '../../assembly/runtime.mjs';
import {MODEL,SLOTS,INPUTS,policyFor,validateGrant,liveCatalog} from './contract.mjs';import {hash,clone} from '../../contracts.mjs';import {reserve} from '../../actions/real/provider.mjs';
const quote={inputUSD:.00000075,outputUSD:.00000375,quoteId:'unit'},wire=fileURLToPath(new URL('./wire.mjs',import.meta.url));
test('frozen caps, one attempt, no native continuation or undeclared tools',()=>{
 assert.equal(Object.values(SLOTS).reduce((n,s)=>n+Math.round(s.cap*1e6),0),300000);
 for(const s of Object.values(SLOTS)){assert.equal(policyFor(s,quote).maxCalls,1);assert.equal(policyFor(s,quote).backup,null);}
 const p={taskId:'a',session:'s',purpose:'participant-visitor',attempt:0,meta:{input:INPUTS[3]},request:{messages:[{role:'user',content:INPUTS[3]}],tools:[],tool_choice:'none'}};
 const g={taskId:'a',session:'s',purpose:p.purpose,slot:3,inputHash:hash(INPUTS[3])};validateGrant(g,p);
 assert.throws(()=>validateGrant(g,{...p,attempt:1}),/GRANT/);assert.throws(()=>validateGrant(g,{...p,request:{...p.request,tool_choice:'auto'}}),/TOOL_LIMIT/);
 assert.throws(()=>reserve({text:'x'.repeat(200000)},quote,SLOTS[3]),/BUDGET/);
});
test('isolated wire handles character openings and refuses tools without weakening old world path',()=>{
 const run=request=>JSON.parse(execFileSync(process.execPath,[wire],{input:JSON.stringify({kind:'request',request}),encoding:'utf8',windowsHide:true}));
 const messages=[{role:'system',content:'rules'},{role:'assistant',content:'opening'},{role:'user',content:'question'}];
 assert.deepEqual(run({messages,tools:[],tool_choice:'none'}),{ok:true});
 assert.throws(()=>run({messages,tools:[{type:'function',function:{name:'write'}}],tool_choice:'auto'}));
});
test('three common tasks: local action once, response adoption, restricted visitor and unchanged world',async t=>{
 const root=fileURLToPath(new URL('../../../.rpg03-work/r9-tests/',import.meta.url));await mkdir(root,{recursive:true});const dir=await mkdtemp(join(root,'real-unit-'));
 const works=await AuthoringStore.open(join(dir,'templates')),participants=await ensureParticipants(works);let calls=0;
 const tr={mode:'mock',async generate(p){calls++;const slot=Object.values(SLOTS).find(s=>s.purpose===p.purpose);validateGrant({slot:slot.id,taskId:p.taskId,session:p.session,purpose:p.purpose,inputHash:hash(p.meta.input)},p);
  if(slot.id===3){assert.deepEqual(p.meta.frame.events,[]);assert.deepEqual(p.meta.frame.resources,[]);assert.ok(!JSON.stringify(p.request).includes('沈砚'));}
  return {status:'completed',finishReason:'stop',message:{role:'assistant',content:slot.id===1?'添油两份，剩三份。':slot.id===2?'看到添加两份的记录，余量未知。':'没有亲眼看到，余量不知道。'},provider:'offline-r9-unit',billing:{kind:'simulated',microUSD:100}};
 },close(){}};
 const core=await openAssembly(join(dir,'runtime'),{controlledReal:true,catalog:await liveCatalog(quote,participants),manifestHash:'c'.repeat(64),limits:{calls:3,microUSD:300000},transportFactory:async()=>tr});
 core.closers.unshift(works);const rt=new SceneRuntime(core,participants);t.after(()=>rt.close());let v=await rt.create({operationId:'create',adapterId:'lighthouse'}),p=rt.port(await rt.run(v.id));
 const expected=clone(v.world.state);expected.supplies[0].quantity=3;
 const r=await p.store.propose(v.worldId,{operationId:'plan',revision:v.world.revision,action:{actionId:p.adapter.rule.id,itemId:p.adapter.resources(v.world.state)[0].id,quantity:2}});
 const confirmation={operationId:'once',revision:r.revision,proposalId:'plan',proposalHash:r.proposals.plan.proposal.hash};
 v=await rt.confirm(v.id,confirmation);v=await rt.confirm(v.id,confirmation);assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
 v=await rt.narrate(v.id,{strategy:'slot-1'});assert.equal(v.tasks[0].adoption,'completed');
 for(const n of [2,3]){v=await rt.submit(v.id,{operationId:'p'+n,ownerId:SLOTS[n].purpose,input:INPUTS[n],strategy:'slot-'+n});assert.equal(v.scene.entries.length,n-2);assert.equal(v.tasks.at(-1).adoption,'awaiting-adoption');
  v=await rt.adopt(v.id,v.tasks.at(-1).id);const revision=v.world.revision;v=await rt.adopt(v.id,v.tasks.at(-1).id);assert.equal(v.world.revision,revision);}
 assert.deepEqual(v.world.state,expected);assert.equal(calls,3);assert.equal(v.scene.entries.length,2);assert.equal(v.budget.limitMicroUSD,300000);
});
