import {check,pending,id} from './contracts.mjs';
// One in-process gate covers both legacy entry points and every revision write.
// Actual accounting remains in the original ledger / revision family, not copied allowances.
export class RevisionCoordinator {
 #tail=Promise.resolve();
 run(fn){const p=this.#tail.then(fn);this.#tail=p.catch(()=>{});return p;}
 constructor(store,sources){this.store=store;this.sources=sources;}
 async legacy(kind,sourceId,send){return this.run(async()=>{
  if(this.store.guardLegacy){await this.store.guardLegacy(kind,sourceId);return send();}
  const fid=id('family',kind,sourceId),family=(await this.store.list()).find(f=>f.id===fid);
  if(family){const f=await this.store.read(fid),live=await this.sources.verify(f.source);check(!pending(f)&&!live.pending,'REQUEST_PENDING');
   check(f.usedCalls+1<=live.budget.calls,'SHARED_BUDGET_EXHAUSTED');
   // Fixture source has a fixed 32k input / 512 output ceiling. Live mode is disabled.
   check(f.usedMicroUsd+4000<=live.budget.microUsd,'SHARED_BUDGET_EXHAUSTED');
  }return send();
 });}
}
