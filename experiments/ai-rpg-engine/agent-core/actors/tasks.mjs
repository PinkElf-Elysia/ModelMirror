import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,need,id,bounded} from '../contracts.mjs';
export const FORMAT='modelmirror.rpg.actor-bridge/6';
// Coordination records reference owner receipts; they are not a world or budget ledger.
export class TaskRecords{
 static async open(directory){return new TaskRecords(await SessionRecords.open(directory,{scope:'rpg-actor-bridge-r6',recoverDeadOwner:true}));}
 constructor(records){this.records=records;}
 async read(key){const e=await this.records.get(key);need(e,'BRIDGE_NOT_FOUND');need(e.data.format===FORMAT,'BRIDGE_FORMAT');return e.data;}
 async create(record){const old=await this.records.get(record.id);if(old){need(old.data.creationHash===record.creationHash,'STATE_OPERATION_COLLISION');return old.data;}
  await this.records.put(record.id,null,record);return clone(record);}
 async update(key,change){const e=await this.records.get(key);need(e,'BRIDGE_NOT_FOUND');const r=clone(e.data);change(r);r.revision++;bounded(r,{bytes:16*1024*1024,depth:40});await this.records.put(key,e.hash,r);return r;}
 async begin(key,spec,input){
  need(id(spec.id)&&['world-action','actor-response'].includes(spec.purpose),'TASK_INVALID');
  const r=await this.read(key),requestHash=hash({spec,input}),old=r.tasks[spec.id];
  if(old){need(old.requestHash===requestHash,'STATE_OPERATION_COLLISION');return old;}
  const task={...clone(spec),sequence:Object.keys(r.tasks).length+1,requestHash,input:clone(input),status:'prepared',stages:[],receipt:null,error:null};
  await this.update(key,row=>{row.tasks[spec.id]=task;});return clone(task);
 }
 close(){return this.records.close();}
}
