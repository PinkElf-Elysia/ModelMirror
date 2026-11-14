import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {TaskLedger} from './ledger.mjs';import {MODEL_A,totals} from './policy.mjs';
const spec=id=>({id,purpose:'world-narration',session:'session-one',sourceHash:'fixed',ownerRef:{owner:'r5',operationId:id}});
test('one CAS allowance arbitrates two competing tasks; replay is not a new reservation',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'r7-'));let l=await TaskLedger.open(dir,{calls:1,microUSD:100});t.after(async()=>{await l.close();await rm(dir,{recursive:true,force:true});});
 for(const id of ['one','two']){await l.begin(spec(id),{});await l.stage(id,'session-one',id,{messages:[]},{});}
 const results=await Promise.allSettled(['one','two'].map(id=>l.claim(id,id,MODEL_A,80)));
 assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.equal(totals(await l.read()).calls,1);
 await assert.rejects(l.begin({...spec('one'),sourceHash:'changed'},{}),{code:'R7_TASK_COLLISION'});
 await l.close();l=await TaskLedger.open(dir,{calls:1,microUSD:100});assert.equal(totals(await l.read()).occupiedMicroUSD,80);
});
