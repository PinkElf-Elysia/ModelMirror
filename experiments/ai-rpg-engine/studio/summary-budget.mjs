import {mkdir,readFile,readdir,open,rename} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {atomicJson} from './branch-archive.mjs';
import {sha,canonical,fail} from '../plugins/catalog.mjs';
const queues=new Map();
const identity=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const validLease=lease=>lease&&identity(lease.groupId)&&Number.isInteger(lease.index)&&lease.index>=0&&lease.index<=1&&typeof lease.slot==='string'&&/^slot-\d+$/.test(lease.slot);
const exists=async p=>{try{return JSON.parse(await readFile(p,'utf8'));}catch(e){if(e.code==='ENOENT')return null;throw e;}};
// Shared slot names also fence older transports. Released evidence is moved, never erased.
export async function createSummaryBudget({directory,limit=0}){
 if(!Number.isSafeInteger(limit)||limit<0||limit>100)throw fail('INVALID_BUDGET');
 const folder=resolve(directory,'earth'),groups=join(folder,'m2-reservations');await mkdir(groups,{recursive:true});
 const serial=action=>{const next=(queues.get(folder)||Promise.resolve()).then(action);queues.set(folder,next.catch(()=>{}));return next;};
 const slots=async()=>(await readdir(folder)).filter(n=>/^slot-\d+$/.test(n));
 const path=id=>{if(!identity(id))throw fail('BUDGET_ID_INVALID');return join(groups,id+'.json');};
 async function claim(lease){
  if(!validLease(lease))throw fail('BUDGET_LEASE_INVALID');
  const group=await exists(path(lease.groupId)),saved=group?.leases[lease.index];
  if(group?.status!=='reserved'||canonical(saved)!==canonical(lease))throw fail('BUDGET_LEASE_INVALID');
  const location=join(folder,lease.slot),owner=await exists(join(location,'m2-claim.json'));
  if(canonical(owner)!==canonical(lease))throw fail('BUDGET_LEASE_INVALID');return location;
 }
 const budget={
  async status(){let consumed=0,reserved=0;for(const name of await slots()){const p=join(folder,name);if(await exists(join(p,'m2-claim.json'))&&!await exists(join(p,'dispatched.json')))reserved++;else consumed++;}return {limit,used:consumed,reserved,remaining:Math.max(0,limit-consumed-reserved)};},
  async reserve(groupId,sessionId,purposes){return serial(async()=>{
   if(!Array.isArray(purposes)||!purposes.length||purposes.length>2||purposes.some(p=>!['summary','story'].includes(p))||new Set(purposes).size!==purposes.length||typeof sessionId!=='string')throw fail('BUDGET_RESERVATION_INVALID');
   const target=path(groupId),prior=await exists(target);if(prior)throw fail('BUDGET_OPERATION_ALREADY_RESERVED');
   if((await budget.status()).remaining<purposes.length)throw fail('BUDGET_EXHAUSTED');
   const group={groupId,sessionId,purposes,leases:[],status:'allocating'};
   let initial;try{initial=await open(target,'wx',0o600);await initial.writeFile(JSON.stringify(group));await initial.sync();}catch(e){if(e.code==='EEXIST')throw fail('BUDGET_OPERATION_ALREADY_RESERVED');throw e;}finally{if(initial)await initial.close();}
   try{
    for(let index=0;index<purposes.length;index++){
     let name;for(let n=1;n<=limit;n++){const candidate='slot-'+n;try{await mkdir(join(folder,candidate));name=candidate;break;}catch(e){if(e.code!=='EEXIST')throw e;}}
     if(!name)throw fail('BUDGET_EXHAUSTED');
     const lease={groupId,index,slot:name,sessionId,purpose:purposes[index]};
     await atomicJson(join(folder,name,'m2-claim.json'),lease);group.leases.push(lease);await atomicJson(target,group);
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
   if(group&&canonical(await exists(join(archived,'m2-claim.json')))===canonical(lease)){
    group.released=[...(group.released||[]),lease.index];await atomicJson(path(lease.groupId),group);return true;
   }
   const location=await claim(lease);if(await exists(join(location,'dispatched.json')))return false;
   // Move all pre-dispatch evidence aside before making the slot available again.
   await rename(location,join(groups,'released-'+lease.groupId+'-'+lease.index));
   group.released=[...(group.released||[]),lease.index];await atomicJson(path(lease.groupId),group);return true;
  });},
  async recover(groupId){const group=await exists(path(groupId));if(!group)return null;return structuredClone(group);},
  async wasDispatched(lease){if(!validLease(lease))throw fail('BUDGET_LEASE_INVALID');try{return !!await exists(join(await claim(lease),'dispatched.json'));}catch(e){const group=await exists(path(lease.groupId));if(group?.released?.includes(lease.index)||canonical(await exists(join(groups,'released-'+lease.groupId+'-'+lease.index,'m2-claim.json')))===canonical(lease))return false;throw e;}}
 };
 return budget;
}
export const reservationId=(sessionId,operationId)=>sha(canonical({sessionId,operationId}));
