import {readFile} from 'node:fs/promises';import {join} from 'node:path';
import {SharedBudgetGuard} from '../revisions/shared-budget.mjs';import {digest,copy,check} from '../revisions/contracts.mjs';
// Own the old ledger without starting its transport or writing it. Only this new
// composition may hold the lease; old runtimes cannot reacquire the owner lock.
export async function leaseLegacyBudget(options){
 const guard=await SharedBudgetGuard.open(options);try{const status=await guard.status();check(status.pending.length===0,'SOURCE_PENDING');const path=join(options.directory,'records','budget.json');
 const bytes=await readFile(path),record=JSON.parse(bytes),data=record.data;check(record.sha256===digest(data),'BUDGET_CORRUPT');const hash=digest(data),used={calls:status.usedCalls-options.authorization.initial.calls,microUsd:status.usedMicroUsd-options.authorization.initial.microUsd},sourceUsed={};
 for(const r of Object.values(data.requests)){const s=sourceUsed[r.payload.sessionId]??={calls:0,microUsd:0};if(r.charged){s.calls++;s.microUsd+=r.cost;}}
 return {hash,authorization:copy(options.authorization),used,sourceUsed,sessions:copy(data.sessions),port:data.listenPort,async verify(){const s=await guard.status();check(!s.pending.length&&digest(JSON.parse(await readFile(path,'utf8')).data)===hash,'LEGACY_BUDGET_CHANGED');},close:()=>guard.close()};
 }catch(e){await guard.close();throw e;}
}
