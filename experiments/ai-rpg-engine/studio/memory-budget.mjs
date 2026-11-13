import {validatePlan as validateComparisonPlan} from '../authoring/comparison-contract.mjs';
import {mkdir,readFile,readdir,open,rename} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {atomicJson} from './branch-archive.mjs';
import {sha,canonical,fail} from '../plugins/catalog.mjs';
const queues=new Map(),comparisonPorts=new WeakMap();
export const memoryComparisonPort=b=>comparisonPorts.get(b)??null;
const identity=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const validLease=lease=>lease&&identity(lease.groupId)&&Number.isInteger(lease.index)&&lease.index>=0&&lease.index<=8&&typeof lease.slot==='string'&&/^slot-\d+$/.test(lease.slot);
const exists=async p=>{try{return JSON.parse(await readFile(p,'utf8'));}catch(e){if(e.code==='ENOENT')return null;throw e;}};
// Shared slot names also fence older transports. Released evidence is moved, never erased.
export async function createMemoryBudget({directory,limit=0,cardId='earth',budgetExemptions=[]}){
 if(!['earth','god','ancient','xingjian','cultivation','lord-of-mysteries','douluo','dragon-raja','elderland'].includes(cardId))throw fail('CONTROL_CARD_INCOMPATIBLE');
 if(!Number.isSafeInteger(limit)||limit<0||limit>100)throw fail('INVALID_BUDGET');
 const folder=resolve(directory,cardId),groups=join(folder,'m3-reservations');await mkdir(groups,{recursive:true});
 // Explicit server-side administrative authorization only; never infer refunds
 // from errors. Preserve original slots, hashes and no-replay claims forever.
 if(!Array.isArray(budgetExemptions)||budgetExemptions.length>100||limit+budgetExemptions.length>100)throw fail('BUDGET_EXEMPTION_INVALID');
 const exemptions=new Map();
 for(const item of budgetExemptions){
  if(!item||Object.keys(item).sort().join(',')!=='authorizationId,requestId,resultSha256,slot'||!/^slot-[1-9]\d*$/.test(item.slot)||!identity(item.requestId)||!identity(item.resultSha256)||typeof item.authorizationId!=='string'||! /^[a-zA-Z0-9_-]{1,100}$/.test(item.authorizationId)||exemptions.has(item.slot))throw fail('BUDGET_EXEMPTION_INVALID');
  exemptions.set(item.slot,structuredClone(item));
 }
 async function verifyExemptions(){for(const item of exemptions.values()){
  const location=join(folder,item.slot),bytes=await readFile(join(location,'result.json'));
  const result=JSON.parse(bytes),dispatch=await exists(join(location,'dispatched.json'));
  if(sha(bytes)!==item.resultSha256||result.card!==cardId||result.requestId!==item.requestId||dispatch?.requestId!==item.requestId||result.status==='complete')throw fail('BUDGET_EXEMPTION_INVALID');
 }}
 await verifyExemptions();
 const serial=action=>{const next=(queues.get(folder)||Promise.resolve()).then(action);queues.set(folder,next.catch(()=>{}));return next;};
 const slots=async()=>(await readdir(folder)).filter(n=>/^slot-\d+$/.test(n));
 const path=id=>{if(!identity(id))throw fail('BUDGET_ID_INVALID');return join(groups,id+'.json');};
 async function claim(lease,releasing=false){
  if(!validLease(lease))throw fail('BUDGET_LEASE_INVALID');
  const group=await exists(path(lease.groupId)),saved=group?.leases[lease.index];
  if(!(releasing?['reserved','allocating','abandoned'].includes(group?.status):group?.status==='reserved')||canonical(saved)!==canonical(lease))throw fail('BUDGET_LEASE_INVALID');
  const location=join(folder,lease.slot),owner=await exists(join(location,'m3-claim.json'));
  if(canonical(owner)!==canonical(lease))throw fail('BUDGET_LEASE_INVALID');return location;
 }
 const budget={
  async status(){await verifyExemptions();let consumed=0,reserved=0;for(const name of await slots()){const p=join(folder,name);if(await exists(join(p,'m3-claim.json'))&&!await exists(join(p,'dispatched.json'))&&!await exists(join(p,'comparison-claimed.json')))reserved++;else consumed++;}const used=consumed-exemptions.size;return {limit,used,reserved,remaining:Math.max(0,limit-used-reserved),...(exemptions.size?{attempts:consumed,exempted:exemptions.size}:{})};},
  async reserve(groupId,sessionId,purposes,comparison=false){return serial(async()=>{
   if(!Array.isArray(purposes)||!purposes.length||purposes.length>(comparison===comparisonPorts?9:6)||purposes.some(p=>!p||Object.keys(p).sort().join(',')!=='purpose,stageId'||!['summary','compression','story','memory'].includes(p.purpose)||! /^[a-z][a-z-]{0,39}$/.test(p.stageId))||new Set(purposes.map(p=>p.stageId)).size!==purposes.length||typeof sessionId!=='string')throw fail('BUDGET_RESERVATION_INVALID');
   const target=path(groupId),prior=await exists(target);if(prior)throw fail('BUDGET_OPERATION_ALREADY_RESERVED');
   if((await budget.status()).remaining<purposes.length)throw fail('BUDGET_EXHAUSTED');
   const group={groupId,sessionId,purposes,leases:[],status:'allocating'};
   let initial;try{initial=await open(target,'wx',0o600);await initial.writeFile(JSON.stringify(group));await initial.sync();}catch(e){if(e.code==='EEXIST')throw fail('BUDGET_OPERATION_ALREADY_RESERVED');throw e;}finally{if(initial)await initial.close();}
   try{
    for(let index=0;index<purposes.length;index++){
     let name;for(let n=1;n<=limit+exemptions.size;n++){const candidate='slot-'+n;try{await mkdir(join(folder,candidate));name=candidate;break;}catch(e){if(e.code!=='EEXIST')throw e;}}
     if(!name)throw fail('BUDGET_EXHAUSTED');
     const lease={groupId,index,slot:name,sessionId,purpose:purposes[index].purpose,stageId:purposes[index].stageId};
     await atomicJson(join(folder,name,'m3-claim.json'),lease);group.leases.push(lease);await atomicJson(target,group);
    }
    group.status='reserved';await atomicJson(target,group);return structuredClone(group.leases);
   }catch(e){
    for(const lease of group.leases)await rename(join(folder,lease.slot),join(groups,'released-'+groupId+'-'+lease.index));
    group.status='failed';await atomicJson(target,group);throw e;
   }
  });},
  async resolve(lease,{sessionId,purpose}){const location=await claim(lease);if(lease.sessionId!==sessionId||lease.purpose!==purpose)throw fail('BUDGET_LEASE_INVALID');return location;},
  async dispatched(lease,requestId,requestHash){return serial(async()=>{const location=await claim(lease);const file=await open(join(location,'dispatched.json'),'wx',0o600);try{await file.writeFile(JSON.stringify({requestId,requestHash,purpose:lease.purpose}));await file.sync();}finally{await file.close();}});},
  async release(lease){if(!validLease(lease))throw fail('BUDGET_LEASE_INVALID');return serial(async()=>{
   const group=await exists(path(lease.groupId));if(group?.released?.includes(lease.index))return false;
   const archived=join(groups,'released-'+lease.groupId+'-'+lease.index);
   if(group&&canonical(await exists(join(archived,'m3-claim.json')))===canonical(lease)){
    group.released=[...(group.released||[]),lease.index];await atomicJson(path(lease.groupId),group);return true;
   }
   const location=await claim(lease,true);if(await exists(join(location,'dispatched.json'))||await exists(join(location,'comparison-claimed.json')))return false;
   // Move all pre-dispatch evidence aside before making the slot available again.
   await rename(location,join(groups,'released-'+lease.groupId+'-'+lease.index));
   group.released=[...(group.released||[]),lease.index];await atomicJson(path(lease.groupId),group);return true;
  });},
  async recover(groupId){return serial(async()=>{
   const group=await exists(path(groupId));if(!group)return null;
   if(group.status==='allocating'){
    // A process can stop between a slot claim and the group journal write.
    // Only an exact durable ownership record permits recovery/release; never
    // infer ownership of an unmarked slot, or resume the unfinished allocation.
    for(const slot of await slots()){
     const lease=await exists(join(folder,slot,'m3-claim.json'));
     if(!lease||lease.groupId!==groupId)continue;
     const stage=group.purposes[lease.index];
     if(!validLease(lease)||lease.slot!==slot||lease.sessionId!==group.sessionId||!stage||lease.stageId!==stage.stageId||lease.purpose!==stage.purpose)throw fail('BUDGET_LEASE_INVALID');
     group.leases[lease.index]=lease;
    }
    group.status='abandoned';await atomicJson(path(groupId),group);
   }
   return {...structuredClone(group),leases:group.leases.filter(Boolean)};
  });},
  async wasDispatched(lease){if(!validLease(lease))throw fail('BUDGET_LEASE_INVALID');try{return !!await exists(join(await claim(lease),'dispatched.json'));}catch(e){const group=await exists(path(lease.groupId));if(group?.released?.includes(lease.index)||canonical(await exists(join(groups,'released-'+lease.groupId+'-'+lease.index,'m3-claim.json')))===canonical(lease))return false;throw e;}}
 };
 const journal=id=>{if(typeof id!=='string'||!/^compare-[a-f0-9]{64}$/.test(id))throw fail('COMPARISON_BATCH_INVALID');return join(groups,id+'.json');};
 const project=g=>g?{plan:structuredClone(g.plan),tasks:structuredClone(g.tasks)}:null;
 const load=async id=>{const g=await exists(journal(id));if(!g)return null;validateComparisonPlan(g.plan);if(g.plan.id!==id||!Array.isArray(g.tasks)||g.tasks.length!==g.plan.stages.length||!Array.isArray(g.leases)||g.leases.length!==0&&g.leases.length!==g.tasks.length)throw fail('COMPARISON_LEDGER_CORRUPT');
  if(!g.leases.length){const held=await exists(path(sha(g.plan.id)));if(held?.status==='reserved'){
   const purposes=g.plan.stages.map((t,i)=>({stageId:'comparison-'+names[i],purpose:t.purpose}));
   if(held.groupId!==sha(g.plan.id)||held.sessionId!==g.plan.intent.sessionId||canonical(held.purposes)!==canonical(purposes)||held.leases.length!==g.tasks.length)throw fail('COMPARISON_LEDGER_CORRUPT');g.leases=held.leases;
  }}
  for(let i=0;i<g.tasks.length;i++){const t=g.tasks[i],lease=g.leases[i];if(t.id!==g.plan.stages[i].id||!['reserved','released','dispatching','unknown','completed','failed','cancelled','not_dispatched'].includes(t.state)||t.requestHash!==null&&!identity(t.requestHash)||t.resultHash!==null&&!identity(t.resultHash)||lease&&(!validLease(lease)||lease.groupId!==sha(g.plan.id)||lease.index!==i))throw fail('COMPARISON_LEDGER_CORRUPT');
   if(lease&&t.state==='reserved'){const owner=await exists(join(folder,lease.slot,'comparison-claimed.json'));if(owner){if(owner.requestId!==t.id||!identity(owner.requestHash))throw fail('COMPARISON_CLAIM_CONFLICT');t.state='dispatching';t.requestHash=owner.requestHash;}}}
  return g;};
 const groupId=p=>sha(p.id),names=['one','two','three','four','five','six','seven','eight','nine'];
 async function selected(id,taskId){const g=await load(id),t=g?.tasks.find(t=>t.id===taskId);if(!g||!t||!g.leases?.length)throw fail('COMPARISON_RESERVATION_MISSING');const lease=g.leases[g.tasks.indexOf(t)];return {g,t,lease};}
 const port={
  status:async()=>({...await budget.status(),level:'local'}),
  async reserve(plan){validateComparisonPlan(plan);let g=await serial(async()=>{const prior=await load(plan.id);if(prior){if(canonical(prior.plan)!==canonical(plan))throw fail('COMPARISON_RESERVATION_CONFLICT');return {...prior,prior:true};}const v={plan:structuredClone(plan),tasks:plan.stages.map(t=>({id:t.id,state:'reserved',requestHash:null,resultHash:null})),leases:[]};await atomicJson(journal(plan.id),v);return {...v,prior:false};});
   if(g.leases.length)return project(g);
   const held=await budget.recover(groupId(plan));let leases;
   if(held?.status==='reserved')leases=held.leases;
   else if(held||g.prior)throw fail('COMPARISON_RESERVATION_UNCONFIRMED');
   else try{leases=await budget.reserve(groupId(plan),plan.intent.sessionId,plan.stages.map((t,i)=>({stageId:'comparison-'+names[i],purpose:t.purpose})),comparisonPorts);}catch(e){if(e.code==='BUDGET_EXHAUSTED')throw fail('COMPARISON_BUDGET_EXHAUSTED');throw e;}
   return serial(async()=>{g=await load(plan.id);g.leases=leases;await atomicJson(journal(plan.id),g);return project(g);});
  },
  async read(id){const g=await load(id);if(!g)return null;if(!g.leases.length)return null;return project(g);},
  async claim(id,taskId,requestHash){return serial(async()=>{if(!identity(requestHash))throw fail('COMPARISON_REQUEST_INVALID');const {g,t,lease}=await selected(id,taskId);if(t.state!=='reserved'){if(t.state==='released'||t.requestHash!==requestHash)throw fail('COMPARISON_CLAIM_CONFLICT');return {dispatch:false,state:t.state};}
   const location=await claim(lease),f=join(location,'comparison-claimed.json'),prior=await exists(f);if(prior){if(prior.requestHash!==requestHash||prior.requestId!==taskId)throw fail('COMPARISON_CLAIM_CONFLICT');return {dispatch:false,state:'unknown'};}
   const file=await open(f,'wx',0o600);try{await file.writeFile(JSON.stringify({requestId:taskId,requestHash}));await file.sync();}finally{await file.close();}
   t.state='dispatching';t.requestHash=requestHash;await atomicJson(journal(id),g);return {dispatch:true,state:t.state};
  });},
  async settle(id,taskId,requestHash,result){return serial(async()=>{const {g,t,lease}=await selected(id,taskId);if(t.requestHash!==requestHash||result.requestHash!==requestHash||result.requestId!==taskId||!['completed','failed','cancelled','not_dispatched'].includes(result.status))throw fail('COMPARISON_RESULT_INVALID');const {hash,...body}=result;if(hash!==sha(canonical(body)))throw fail('COMPARISON_RESULT_INVALID');
   if(['completed','failed','cancelled','not_dispatched'].includes(t.state)){if(t.state!==result.status||t.resultHash!==result.hash)throw fail('COMPARISON_RESULT_CONFLICT');return structuredClone(t);}
   const archived=join(groups,'released-'+lease.groupId+'-'+lease.index);let location;
   if(result.status==='not_dispatched'&&canonical(await exists(join(archived,'m3-claim.json')))===canonical(lease))location=archived;else location=await claim(lease);
   const owner=await exists(join(location,'comparison-claimed.json'));if(owner?.requestId!==taskId||owner.requestHash!==requestHash)throw fail('COMPARISON_CLAIM_CONFLICT');
   await atomicJson(join(location,'comparison-result.json'),result);
   if(result.status==='not_dispatched'){
    if(result.receipt?.dispatched!==false)throw fail('COMPARISON_REFUND_UNPROVEN');
    if(location!==archived)await rename(location,archived);
    const saved=await exists(path(lease.groupId));saved.released=[...new Set([...(saved.released||[]),lease.index])];await atomicJson(path(lease.groupId),saved);
   }
   t.state=result.status;t.resultHash=result.hash;await atomicJson(journal(id),g);return structuredClone(t);
  });},
  async release(id,taskId){const {g,t,lease}=await selected(id,taskId);if(t.state==='released')return structuredClone(t);if(t.state!=='reserved')throw fail('COMPARISON_DISPATCH_RETAINED');if(!await budget.release(lease)){const saved=await budget.recover(lease.groupId);if(!saved.released?.includes(lease.index))throw fail('COMPARISON_DISPATCH_RETAINED');}return serial(async()=>{const current=await load(id),task=current.tasks.find(t=>t.id===taskId);if(task.state!=='reserved')throw fail('COMPARISON_DISPATCH_RETAINED');task.state='released';await atomicJson(journal(id),current);return structuredClone(task);});},
  async reconcile(){return {replayed:false};}
 };
 comparisonPorts.set(budget,Object.freeze(port));
 return budget;
}
// Separate group evidence; slot-N remains the shared card budget with prior transports.
export const reservationId=(sessionId,operationId)=>sha(canonical({sessionId,operationId}));
