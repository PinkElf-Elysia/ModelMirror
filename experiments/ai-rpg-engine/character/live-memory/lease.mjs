import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {ParameterBudgetGuard} from '../long-context/budget.mjs';
import {leaseLegacyBudget as leaseRevisions} from '../long-context/legacy-budget.mjs';
import {copy, check, digest} from '../revisions/contracts.mjs';

// Validate the old policy with the old reader and retain its exclusive owner.
// Carry both layers' charges, including dispatched failures.
export async function leaseLegacyBudget(options) {
  const lower = options.prior ? await leaseRevisions(options.prior) : null;
  let guard;
  try {
    guard = await ParameterBudgetGuard.open({...options, legacy:lower});
    const status = await guard.status();
    check(!status.pending.length, 'SOURCE_PENDING');
    const path = join(options.directory,'records','budget.json');
    const original = await readFile(path,'utf8');
    const {data,sha256} = JSON.parse(original);
    check(digest(data)===sha256,'BUDGET_CORRUPT');
    const sourceUsed = copy(lower?.sourceUsed ?? {});
    for (const r of Object.values(data.requests)) {
      const spent = sourceUsed[r.payload.sessionId] ??= {calls:0,microUsd:0};
      if (r.charged) { spent.calls++; spent.microUsd += r.cost; }
    }
    return {hash:digest(original),authorization:copy(options.authorization),sessions:copy(data.sessions),
      port:data.listenPort,sourceUsed,used:{calls:status.usedCalls-options.authorization.initial.calls,
        microUsd:status.usedMicroUsd-options.authorization.initial.microUsd},
      async verify(){await guard.status();check(await readFile(path,'utf8')===original,'LEGACY_BUDGET_CHANGED');},
      async close(){await guard.close();await lower?.close();}};
  } catch(e) {await guard?.close();await lower?.close();throw e;}
}
