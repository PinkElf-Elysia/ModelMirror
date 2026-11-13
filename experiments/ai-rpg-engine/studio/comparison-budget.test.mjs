import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {OfflineModelControl} from '../authoring/model-control.mjs';
import {comparisonPlan,digest} from '../authoring/comparison-contract.mjs';
import {createMemoryBudget,memoryComparisonPort} from './memory-budget.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/c1b-budget-tests/',import.meta.url));await mkdir(root,{recursive:true});
function plan(){const control=new OfflineModelControl({catalogId:'slots',models:[{selectionId:'model',selectionRevision:digest('model'),modelId:'fixture/model',name:'Test',available:true,contextLength:128000,maxOutputTokens:8192,parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:8192,defaultValue:128,required:true,wireName:'max_tokens'}},exclusive:[]}}]}),c=control.catalog(),choice=control.select({catalogRevision:c.revision,selectionId:'model',selectionRevision:digest('model'),parameters:{maxOutputTokens:128}}),auth=digest('auth');return comparisonPlan({sessionId:'session',operationId:'op',expectedContextRevision:0,expectedAuthorizationRevision:auth,input:'test',candidates:[0,1,2,3].map(i=>({id:'c'+i,choice}))},{contextRevision:0,authorizationRevision:auth,sourceHash:digest('state'),summaryBefore:true,summaryAfter:true,memoryAfter:true});}
test('maximum nine-stage card hold blocks ordinary sends and releases only unused slots',async()=>{
 const directory=await mkdtemp(join(root,'nine-')),budget=await createMemoryBudget({directory,limit:9}),port=memoryComparisonPort(budget),p=plan();await port.reserve(p);assert.equal((await budget.status()).reserved,9);await assert.rejects(budget.reserve(digest('ordinary'),'other',[{stageId:'story',purpose:'story'}]),{code:'BUDGET_EXHAUSTED'});for(const t of p.stages)await port.release(p.id,t.id);assert.equal((await budget.status()).remaining,9);await budget.reserve(digest('ordinary'),'other',[{stageId:'story',purpose:'story'}]);assert.equal((await budget.status()).reserved,1);
});
test('durable claim before journal acknowledgement survives reopen and cannot be released or dispatched twice',async()=>{
 const directory=await mkdtemp(join(root,'crash-')),budget=await createMemoryBudget({directory,limit:9}),port=memoryComparisonPort(budget),p=plan();await port.reserve(p);const id=p.stages[0].id,requestHash=digest('request');await writeFile(join(directory,'earth','slot-1','comparison-claimed.json'),JSON.stringify({requestId:id,requestHash}));
 const reopened=await createMemoryBudget({directory,limit:9}),again=memoryComparisonPort(reopened);assert.equal((await again.read(p.id)).tasks[0].state,'dispatching');assert.equal((await again.claim(p.id,id,requestHash)).dispatch,false);await assert.rejects(again.release(p.id,id),{code:'COMPARISON_DISPATCH_RETAINED'});assert.equal((await reopened.status()).used,1);assert.equal((await reopened.status()).reserved,8);
});

test('completed slot reservation with lost host link is recovered without creating another group',async()=>{
 const directory=await mkdtemp(join(root,'link-')),budget=await createMemoryBudget({directory,limit:9}),port=memoryComparisonPort(budget),p=plan();await port.reserve(p);const path=join(directory,'earth','m3-reservations',p.id+'.json'),saved=JSON.parse(await readFile(path,'utf8'));saved.leases=[];await writeFile(path,JSON.stringify(saved));
 const reopened=await createMemoryBudget({directory,limit:9}),again=memoryComparisonPort(reopened);assert.equal((await again.read(p.id)).tasks.length,9);await again.reserve(p);assert.equal((await reopened.status()).reserved,9);for(const t of p.stages)await again.release(p.id,t.id);assert.equal((await reopened.status()).remaining,9);
});
