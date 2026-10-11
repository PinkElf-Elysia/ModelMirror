import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createMemoryBudget,reservationId} from './memory-budget.mjs';
import {sha} from '../plugins/catalog.mjs';
async function fixture(){
 const directory=await mkdtemp(join(tmpdir(),'rpg-budget-exemption-'));
 for(let n=1;n<=3;n++)await mkdir(join(directory,'god','slot-'+n),{recursive:true});
 const requestId=sha('failed-request'),raw=JSON.stringify({card:'god',requestId,status:'failed_or_unknown'});
 await writeFile(join(directory,'god','slot-3','result.json'),raw);
 await writeFile(join(directory,'god','slot-3','dispatched.json'),JSON.stringify({requestId}));
 return {directory,limit:4,cardId:'god',budgetExemptions:[{slot:'slot-3',requestId,resultSha256:sha(raw),authorizationId:'explicit-user-waiver'}]};
}
test('explicit waiver restores 2/4, preserves failed slot, allocates two new slots and survives restart',async()=>{
 const o=await fixture(),b=await createMemoryBudget(o),evidence=await readFile(join(o.directory,'god/slot-3/result.json'));
 assert.deepEqual(await b.status(),{limit:4,used:2,reserved:0,remaining:2,attempts:3,exempted:1});
 const leases=await b.reserve(reservationId('new','one'),'new',[{stageId:'story',purpose:'story'},{stageId:'memory-after',purpose:'memory'}]);
 assert.deepEqual(leases.map(x=>x.slot),['slot-4','slot-5']);
 for(const l of leases)await b.dispatched(l,sha(l.slot),sha('payload'));
 assert.equal((await b.status()).remaining,0);
 assert.equal((await (await createMemoryBudget(o)).status()).used,4);
 await assert.rejects(b.reserve(reservationId('new','two'),'new',[{stageId:'story',purpose:'story'}]),/BUDGET_EXHAUSTED/);
 assert.deepEqual(await readFile(join(o.directory,'god/slot-3/result.json')),evidence);
});
test('no automatic waiver; invalid, duplicate, mismatched or changed evidence fails closed',async()=>{
 const o=await fixture();assert.equal((await (await createMemoryBudget({...o,budgetExemptions:[]})).status()).used,3);
 for(const budgetExemptions of [[...o.budgetExemptions,...o.budgetExemptions],[{...o.budgetExemptions[0],resultSha256:sha('wrong')}],[{...o.budgetExemptions[0],slot:'../escape'}]])await assert.rejects(createMemoryBudget({...o,budgetExemptions}),/BUDGET_EXEMPTION_INVALID/);
 const b=await createMemoryBudget(o);await writeFile(join(o.directory,'god/slot-3/result.json'),'{}');await assert.rejects(b.status(),/BUDGET_EXEMPTION_INVALID/);
});
