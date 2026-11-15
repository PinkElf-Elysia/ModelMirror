import {ActionStore} from '../actions/store.mjs';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {clone,hash,id,need} from '../contracts.mjs';
export class AssemblyStore extends ActionStore{
 static async open({directory,adapter}){return new AssemblyStore(await SessionRecords.open(directory,{scope:'rpg-assembly-r8',recoverDeadOwner:true}),adapter);}
 // Only host-created immutable checkpoints, never client state snapshots.
 async forkCheckpoint(checkpoint,operationId){
  need(id(operationId)&&checkpoint.hash===hash(checkpoint.snapshot),'CHECKPOINT_HASH');this.validate(checkpoint.snapshot);need(!checkpoint.snapshot.pending,'CHECKPOINT_PENDING');
  const creationHash=hash({checkpoint:checkpoint.id,hash:checkpoint.hash,operationId}),key='core-'+creationHash.slice(0,40);
  const old=await this.records.get(key,r=>this.validate(r));if(old){need(old.data.creationHash===creationHash,'STATE_OPERATION_COLLISION');return old.data;}
  const r=clone(checkpoint.snapshot);r.id=key;r.revision=0;r.creationHash=creationHash;
  r.source={kind:'checkpoint',id:checkpoint.snapshot.id,eventId:r.events.at(-1)?.id??null,history:clone(r.source.history),checkpointId:checkpoint.id};
  r.operations={};r.pending=null;r.proposals={};this.validate(r);await this.records.put(key,null,r);return r;
 }
}
