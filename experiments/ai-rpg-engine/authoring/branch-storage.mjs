import {SessionRecords} from './session-storage.mjs';
import {canonical,fail} from './contracts.mjs';
import {validateBranchCheckpoint,validateBranchLineage} from './branch-checkpoint.mjs';
const idCheck=id=>{if(typeof id!=='string'||!/^branch-[a-f0-9]{64}$/.test(id))fail('BRANCH_CHECKPOINT_ID_INVALID');};
// Host-only immutable evidence store. It has no provider, grant or budget port.
export class BranchCheckpoints {
 #records;#validated=new Map();#queue=Promise.resolve();#closed=false;
 constructor(records){this.#records=records;}
 static async open({directory,recoverDeadOwner=false,storageFault}={}){
  return new BranchCheckpoints(await SessionRecords.open(directory,{recoverDeadOwner,fault:storageFault,scope:'authored-branch-checkpoints'}));
 }
 #serial(action){if(this.#closed)fail('BRANCH_STORE_CLOSED');const next=this.#queue.then(action);this.#queue=next.catch(()=>{});return next;}
 async #read(id,ancestors=new Set()){
  if(ancestors.has(id)||ancestors.size>32)fail('BRANCH_LINEAGE_INVALID');
  const value=await this.#records.get(id);if(!value)return null;
  if(this.#validated.get(id)!==value.hash){validateBranchCheckpoint(value.data);if(value.data.id!==id)fail('BRANCH_CHECKPOINT_INVALID');
   this.#validated.set(id,value.hash);if(this.#validated.size>64)this.#validated.delete(this.#validated.keys().next().value);}
  if(value.data.lineage){
   const chain=new Set([...ancestors,id]),sources=[];
   for(const ref of value.data.lineage){const source=await this.#read(ref.id,chain);if(!source||source.sha256!==ref.sha256)fail('BRANCH_LINEAGE_INVALID');sources.push(source);}
   validateBranchLineage(value.data,sources);
  }
  return value.data;
 }
 read(id){idCheck(id);return this.#serial(()=>this.#read(id));}
 save(checkpoint){
  validateBranchCheckpoint(checkpoint);const frozen=structuredClone(checkpoint);
  return this.#serial(async()=>{
   if(frozen.lineage){const sources=[];for(const ref of frozen.lineage){const source=await this.#read(ref.id);if(!source||source.sha256!==ref.sha256)fail('BRANCH_LINEAGE_INVALID');sources.push(source);}validateBranchLineage(frozen,sources);}
   const prior=await this.#read(frozen.id);
   if(prior){if(canonical(prior)!==canonical(frozen))fail('BRANCH_OPERATION_COLLISION');return {checkpoint:prior,replayed:true};}
   await this.#records.put(frozen.id,null,frozen);return {checkpoint:structuredClone(frozen),replayed:false};
  });
 }
 // Explicit read-back reconciliation only; never re-creates a missing branch.
 reconcileStorage(){return this.#serial(()=>this.#records.reconcile());}
 async close(){if(this.#closed)return;this.#closed=true;await this.#queue;await this.#records.close();}
}
