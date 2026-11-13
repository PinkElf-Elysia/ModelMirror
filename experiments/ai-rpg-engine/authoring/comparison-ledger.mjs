import {memoryComparisonPort} from '../studio/memory-budget.mjs';
import {jointComparisonPort} from './joint-budget.mjs';
import {comparisonControlPort} from './comparison-control.mjs';
import {SessionRecords} from './session-storage.mjs';
import {keys,isId,isHash,fail} from './contracts.mjs';
import {clone,digest,same,integer,terminal,validatePlan} from './comparison-contract.mjs';
const FORMAT='modelmirror.rpg.comparison-ledger/1',brand=new WeakMap(),token=Symbol();
const totals=s=>{let used=0,reserved=0;for(const g of s.groups)for(const t of g.tasks){if(t.state==='reserved')reserved++;else if(!['released','not_dispatched'].includes(t.state))used++;}return {limit:s.limit,used,reserved,remaining:s.limit-used-reserved};};
function validate(s){
 keys(s,['format','allowanceId','level','limit','revision','groups']);
 if(s.format!==FORMAT||!isId(s.allowanceId)||!['local','control'].includes(s.level)||!integer(s.limit)||s.limit>100000||!integer(s.revision)||!Array.isArray(s.groups)||s.groups.length>1000)fail('COMPARISON_LEDGER_CORRUPT');
 const ids=new Set(),requests=new Set();for(const g of s.groups){keys(g,['plan','tasks']);validatePlan(g.plan);if(ids.has(g.plan.id)||!Array.isArray(g.tasks)||g.tasks.length!==g.plan.stages.length)fail('COMPARISON_LEDGER_CORRUPT');ids.add(g.plan.id);
  for(const [i,t]of g.tasks.entries()){keys(t,['id','state','requestHash','resultHash']);if(t.id!==g.plan.stages[i].id||requests.has(t.id)||!['reserved','dispatching','unknown','completed','failed','cancelled','not_dispatched','released'].includes(t.state))fail('COMPARISON_LEDGER_CORRUPT');requests.add(t.id);
   if(['reserved','released'].includes(t.state)){if(t.requestHash!==null||t.resultHash!==null)fail('COMPARISON_LEDGER_CORRUPT');}
   else if(!isHash(t.requestHash)||(terminal(t.state)?!isHash(t.resultHash):t.resultHash!==null))fail('COMPARISON_LEDGER_CORRUPT');
  }
 }
 if(totals(s).remaining<0)fail('COMPARISON_LEDGER_CORRUPT');return s;
}
export class ComparisonLedger{
 #records;#identity;#queue=Promise.resolve();
 constructor(key,records,identity){if(key!==token)fail('COMPARISON_LEDGER_REQUIRED');this.#records=records;this.#identity=identity;brand.set(this,Object.freeze(Object.fromEntries(['reserve','read','claim','settle','release','status','reconcile'].map(k=>[k,this[k].bind(this)]))));}
 static async open({directory,allowanceId,level,limit=0,recoverDeadOwner=false,storageFault}){
  if(!isId(allowanceId)||!['local','control'].includes(level)||!integer(limit)||limit>100000)fail('COMPARISON_ALLOWANCE_INVALID');
  const records=await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'comparison-'+level});
  try{const identity={allowanceId,level,limit},r=await records.get('allowance');if(!r)await records.put('allowance',null,{format:FORMAT,...identity,revision:0,groups:[]});else{validate(r.data);if(Object.keys(identity).some(k=>r.data[k]!==identity[k]))fail('COMPARISON_ALLOWANCE_CHANGED');}return new ComparisonLedger(token,records,identity);}catch(e){await records.close();throw e;}
 }
 #run(fn){const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #load(){const r=await this.#records.get('allowance',validate);if(!r||Object.keys(this.#identity).some(k=>r.data[k]!==this.#identity[k]))fail('COMPARISON_LEDGER_CORRUPT');return r;}
 async #save(r){r.data.revision++;validate(r.data);await this.#records.put('allowance',r.hash,r.data);}
 status(){return this.#run(async()=>{const r=await this.#load();return {...this.#identity,...totals(r.data),revision:r.data.revision};});}
 read(id){return this.#run(async()=>clone((await this.#load()).data.groups.find(g=>g.plan.id===id)??null));}
 reserve(value){const plan=clone(validatePlan(value));return this.#run(async()=>{const r=await this.#load(),old=r.data.groups.find(g=>g.plan.id===plan.id);if(old){if(!same(old.plan,plan))fail('COMPARISON_RESERVATION_CONFLICT');return clone(old);}
  if(r.data.groups.length>=1000)fail('COMPARISON_LEDGER_CAPACITY');if(totals(r.data).remaining<plan.maximumCalls)fail('COMPARISON_BUDGET_EXHAUSTED');
  const g={plan,tasks:plan.stages.map(t=>({id:t.id,state:'reserved',requestHash:null,resultHash:null}))};r.data.groups.push(g);await this.#save(r);return clone(g);});}
 #task(r,batch,id){const t=r.data.groups.find(g=>g.plan.id===batch)?.tasks.find(t=>t.id===id);if(!t)fail('COMPARISON_RESERVATION_MISSING');return t;}
 claim(batch,id,hash){return this.#run(async()=>{if(!isHash(hash))fail('COMPARISON_REQUEST_INVALID');const r=await this.#load(),t=this.#task(r,batch,id);
  if(t.state!=='reserved'){if(t.state==='released'||t.requestHash!==hash)fail('COMPARISON_CLAIM_CONFLICT');return {dispatch:false,state:t.state};}
  t.state='dispatching';t.requestHash=hash;await this.#save(r);return {dispatch:true,state:t.state};});}
 settle(batch,id,requestHash,result){return this.#run(async()=>{if(!terminal(result.status)||result.status==='released'||!isHash(result.hash))fail('COMPARISON_RESULT_INVALID');const r=await this.#load(),t=this.#task(r,batch,id);
  if(t.requestHash!==requestHash)fail('COMPARISON_CLAIM_CONFLICT');if(terminal(t.state)){if(t.state!==result.status||t.resultHash!==result.hash)fail('COMPARISON_RESULT_CONFLICT');return clone(t);}
  if(!['dispatching','unknown'].includes(t.state))fail('COMPARISON_CLAIM_REQUIRED');t.state=result.status;t.resultHash=result.hash;await this.#save(r);return clone(t);});}
 release(batch,id){return this.#run(async()=>{const r=await this.#load(),t=this.#task(r,batch,id);if(t.state==='released')return clone(t);if(t.state!=='reserved')fail('COMPARISON_DISPATCH_RETAINED');t.state='released';await this.#save(r);return clone(t);});}
 reconcile(){return this.#run(()=>this.#records.reconcile());}
 async close(){await this.#queue;await this.#records.close();}
}
export function comparisonLedgerPort(value){const p=brand.get(value)??comparisonControlPort(value)??jointComparisonPort(value)??memoryComparisonPort(value);if(!p)fail('COMPARISON_LEDGER_REQUIRED');return p;}
