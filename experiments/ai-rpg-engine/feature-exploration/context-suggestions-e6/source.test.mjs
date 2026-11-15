import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {openSource,project,stamp} from './source.mjs';import {suggest} from './suggestions.mjs';
test('R8 projection and E5 selection remain read only',async t=>{
 const dir=await mkdtemp(resolve('.rpg03-work/e6-source-')),h=await openSource(join(dir,'r8'));t.after(()=>h.close());
 const before=await h.audit(),f=await h.reader.read();
 assert.deepEqual(Object.keys(h.reader),['read']);assert.equal(f.facts.resources.find(x=>x.id==='medicine').quantity,5);
 assert.equal(f.facts.people[0].name,'周禾');assert.ok(!JSON.stringify(f).includes('trust'));assert.ok(!('budget' in f));
 assert.equal(suggest(f,'我想向周禾表达谢意，尽量保留丹药').options.length,3);
 const p=await h.selectedPlan({id:'choice-1',kind:'gift',origin:'player-selection',targetId:'zhou',itemId:'medicine',quantity:2,text:'我选择考虑赠送周禾两份养息丹。'},stamp(f));
 assert.equal(p.executionStatus,'not_executed');assert.equal(p.plan.steps[0].atomic.intent.quantity.value,2);
 assert.equal(p.plan.steps[0].atomic.intent.quantity.source.kind,'user-edit');assert.equal(p.plan.steps[0].preflight,null);
 assert.deepEqual(await h.audit(),before);await assert.rejects(h.selectedPlan({kind:'gift'},'old'),/E6_STALE/);
});
test('whitelist and record revision, narrative never promoted',()=>{
 const v={id:'run',adapterId:'cultivation',worldId:'world',binding:'b',busy:false,world:{revision:2,stateRevision:1,pending:null,
 state:{relations:[{id:'zhou',name:'周禾',trust:99},{id:'secret',name:'秘密人物'}]},resources:[{id:'medicine',name:'养息丹',quantity:5,secret:'hidden'}],
 events:[{id:'e',kind:'action',private:'no'}],turns:[{body:'此前疲劳。',eventId:'e'}]},tools:[{name:'query_local_state',effect:'read',version:'1',enabled:false}],tasks:['secret'],budget:{private:1}};
 const f=project(v);assert.equal(f.capabilities.length,0);assert.equal(f.facts.people.length,1);assert.equal(f.narrative[0].kind,'public-narrative');
 assert.ok(!JSON.stringify(f).includes('hidden'));assert.notEqual(stamp(f),stamp(project({...v,world:{...v.world,revision:3}})));
});
test('clarification, refusal and unsupported input',()=>{
 const f={source:{revision:0},ready:true,facts:{people:[{id:'zhou',name:'周禾'}],resources:[{id:'medicine',name:'养息丹',quantity:5}]},capabilities:[]};
 assert.equal(suggest(f,'我想感谢他').options.length,0);assert.ok(suggest(f,'我想感谢他').question);
 assert.equal(suggest(f,'我想感谢他','周禾').options.length,3);assert.equal(suggest(f,'感谢周禾，但不送丹药').options.length,2);assert.ok(suggest(f,'我打算去旅行').notice);
});
