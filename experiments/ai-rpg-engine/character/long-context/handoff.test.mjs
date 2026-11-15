import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {parameterMock,token} from '../revisions/parameter-mock.mjs';
import {SharedBudgetGuard} from '../revisions/shared-budget.mjs';
import {revisionWire} from '../revisions/controlled.mjs';
import {controlledPayload} from '../../authoring/controlled-receipt.mjs';
import {digest} from '../revisions/contracts.mjs';
import {openContextControl} from './composition.mjs';

const root=process.env.CHARACTER_R4A_TEST_ROOT??'C:/tmp/mm-cr4a-data/tests';
await mkdir(root,{recursive:true});
function payload(m,id,sourceId='source'){
 return controlledPayload(revisionWire({sourceId,sourceKind:'single',id,targetId:'actor',branchId:'branch',prefixHash:digest([]),hash:digest(id),model:m,context:[{role:'system',content:'中性夹具'},{role:'user',content:'确认可见内容'}]}));
}
async function setup(t,upstreamOptions={}){
 const mock=await parameterMock(t,upstreamOptions);
 const choice=await mock.control.selectParameters(mock.selection('story',{maxOutputTokens:{mode:'explicit',value:512}}));
 const model={id:'test',label:'测试',choice,contextTokens:256000,outputTokens:512,price:{revision:'old-price',inputMicroUsdPerMillion:1000000,outputMicroUsdPerMillion:1000000,overheadTokens:128}};
 const dir=await mkdtemp(join(root,'handoff-'));
 const authorization={id:'old-task',calls:7,microUsd:100000,initial:{calls:5,microUsd:10000}};
 const oldOptions={directory:join(dir,'old'),upstream:mock.origin,serviceToken:token,models:[model],authorization,timeoutMs:1000};
 const old=await SharedBudgetGuard.open(oldOptions);
 await old.register('source',{calls:2,microUsd:20000});
 await old.dispatch(payload(model,'before'));
 await old.close();
 const path=join(oldOptions.directory,'records','budget.json'),bytes=await readFile(path),data=JSON.parse(bytes).data,cost=Object.values(data.requests)[0].cost;
 const current={...model,price:{...model.price,revision:'new-price',outputMicroUsdPerMillion:2000000}};
 const next={directory:join(dir,'new'),upstream:mock.origin,serviceToken:token,models:[current],authorization:{id:'new-task',calls:9,microUsd:150000,initial:{...authorization.initial}},legacyDirectory:oldOptions.directory,legacyPolicy:{models:[model],authorization},timeoutMs:1000};
 return {mock,model,current,oldOptions,next,path,bytes,cost};
}

test('separate old policy from new grant; source and total limits survive restart without repricing',async t=>{
 const f=await setup(t);let ctx=await openContextControl(f.next);
 t.after(async()=>ctx?.close());
 assert.deepEqual(await ctx.guard.status(),{authorizationId:'new-task',calls:9,microUsd:150000,usedCalls:6,usedMicroUsd:10000+f.cost,pending:[]});
 await assert.rejects(()=>SharedBudgetGuard.open(f.oldOptions)); // Exclusive lease, no second dispatcher.
 await assert.rejects(()=>ctx.guard.register('source',{calls:99,microUsd:999999}),/SOURCE_BUDGET_CHANGED/);
 const p=payload(f.current,'after');
 await ctx.guard.dispatch(p);
 const denied=await ctx.guard.dispatch(payload(f.current,'source-exhausted'));
 assert.equal(denied.receipt.error,'r3b_budget_exhausted');
 assert.equal(f.mock.tasks.length,2);
 const prior=await ctx.guard.status();await ctx.close();ctx=await openContextControl(f.next);
 assert.deepEqual(await ctx.guard.status(),prior);
 await ctx.guard.dispatch(p);assert.equal(f.mock.tasks.length,2);
 await ctx.guard.register('other',{calls:10,microUsd:100000});
 await ctx.guard.dispatch(payload(f.current,'third','other'));
 await ctx.guard.dispatch(payload(f.current,'fourth','other'));
 assert.equal((await ctx.guard.dispatch(payload(f.current,'total-exhausted','other'))).receipt.error,'r3b_budget_exhausted');
 assert.equal(f.mock.tasks.length,4);assert.equal((await ctx.guard.status()).usedCalls,9);
 assert.deepEqual(await readFile(f.path),f.bytes);
});

test('missing or altered historical policy and reduced carried allowance fail closed',async t=>{
 const f=await setup(t);
 const bad=[
  [{...f.next,legacyPolicy:null},/LEGACY_POLICY_REQUIRED/],
  [{...f.next,legacyPolicy:{models:[f.current],authorization:f.oldOptions.authorization}},/AUTHORIZATION_CHANGED/],
  [{...f.next,legacyPolicy:{models:[f.model],authorization:f.next.authorization}},/AUTHORIZATION_CHANGED/],
  [{...f.next,authorization:{...f.next.authorization,initial:{calls:0,microUsd:0}}},/LEGACY_INITIAL_CHANGED/],
  [{...f.next,authorization:{...f.next.authorization,calls:6}},/LEGACY_ALLOWANCE_REDUCED/],
  [{...f.next,authorization:{...f.next.authorization,microUsd:99999}},/LEGACY_ALLOWANCE_REDUCED/]
 ];
 for(const [opts,error] of bad)await assert.rejects(()=>openContextControl(opts),error);
 assert.equal(f.mock.tasks.length,1);assert.deepEqual(await readFile(f.path),f.bytes);
 const ctx=await openContextControl(f.next);await ctx.close(); // Failed opens released ownership.
});

test('source money ceiling remains fixed even when new task has ample money',async t=>{
 const f=await setup(t);const old=await SharedBudgetGuard.open(f.oldOptions);
 await old.register('money-source',{calls:100,microUsd:1});await old.close();
 const bytes=await readFile(f.path),ctx=await openContextControl(f.next);t.after(()=>ctx.close());
 const result=await ctx.guard.dispatch(payload(f.current,'too-expensive','money-source'));
 assert.equal(result.receipt.error,'r3b_budget_exhausted');assert.equal(f.mock.tasks.length,1);
 assert.deepEqual(await readFile(f.path),bytes);
});

test('new unknown request retains reservation on restart; lookup only adopts original receipt',async t=>{
 const options={},f=await setup(t,options);options.dropPurpose='story';
 let ctx=await openContextControl(f.next);t.after(async()=>ctx?.close());
 const p=payload(f.current,'unknown');
 await assert.rejects(()=>ctx.guard.dispatch(p));
 const before=await ctx.guard.status();assert.equal(before.pending.length,1);assert.equal(before.usedCalls,7);
 await ctx.close();ctx=await openContextControl(f.next);
 assert.deepEqual(await ctx.guard.status(),before);
 const result=await ctx.guard.lookup(p.sessionId,p.requestId,p.sourceRequestHash);
 assert.equal(result.status,'completed');assert.equal(f.mock.tasks.length,2);
 assert.equal((await ctx.guard.status()).usedMicroUsd,before.usedMicroUsd);
 assert.equal((await ctx.guard.status()).pending.length,0);
 await ctx.guard.dispatch(p);assert.equal(f.mock.tasks.length,2);
 assert.deepEqual(await readFile(f.path),f.bytes);
});

test('unresolved legacy request blocks handoff without lookup or replay',async t=>{
 const options={},f=await setup(t,options);options.dropPurpose='story';
 const old=await SharedBudgetGuard.open(f.oldOptions);
 await assert.rejects(()=>old.dispatch(payload(f.model,'legacy-unknown')));await old.close();
 const bytes=await readFile(f.path);
 await assert.rejects(()=>openContextControl(f.next),/SOURCE_PENDING/);
 assert.equal(f.mock.tasks.length,2);assert.equal(f.mock.lookups.length,0);
 assert.deepEqual(await readFile(f.path),bytes);
});
