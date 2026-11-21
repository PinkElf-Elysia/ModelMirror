import { Sources } from '../long-context/sources.mjs';
import { boundary, copy, digest, pending, check } from '../revisions/contracts.mjs';

export class MemorySources {
  constructor(contextStore, lower) { this.context = contextStore; this.lower = lower; }
  async list() {
    const rows=await this.context.list();
    return rows.flatMap(f=>f.branches.map(b=>({id:b.id,kind:'context',title:(f.source.kind==='group'?'群聊':'单聊')+' · '+b.title+' · '+f.id.slice(-6),pending:f.pending,
      reference:{kind:'context',familyId:f.id,branchId:b.id}})));
  }
  async read(ref) {
    if(ref.kind!=='context') return this.lower.read(ref);
    const f=await this.context.read(ref.familyId), b=f.branches[ref.branchId];
    check(b&&!pending(f),'SOURCE_PENDING');
    const live=await this.lower.read({kind:f.source.kind,sourceId:f.source.id});
    const rows=await this.context.list();
    let calls=0,money=0;
    for(const row of rows.filter(r=>r.source.kind===f.source.kind&&r.source.id===f.source.id)) {
      const family=await this.context.read(row.id);
      check(!pending(family),'SOURCE_PENDING'); calls+=family.usedCalls; money+=family.usedMicroUsd;
    }
    const s={...copy(f.source),revision:f.revision,current:copy(boundary(b)),messages:copy(b.messages),
      derived:copy(b.derived),pending:false,evidence:{...copy(f.source.evidence),...copy(f.requests)},
      budget:{calls:Math.max(0,live.budget.calls-calls),microUsd:Math.max(0,live.budget.microUsd-money)}};
    delete s.sourceHash; s.sourceHash=digest(s); return s;
  }
  async settings(ref){return copy((await this.context.view(ref.familyId,ref.branchId)).defaults);}
  async verify(ref,frozen){return Sources.prototype.verify.call(this,ref,frozen);}
}
