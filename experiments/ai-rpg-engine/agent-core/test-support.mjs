import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {openRuntime} from './composition.mjs';import {operationId} from './contracts.mjs';
export {operationId};
export async function fixture(t,adapterId='lighthouse',options={}){
 const directory=await mkdtemp(join(tmpdir(),'rpg-core-r3-test-'));let runtime=await openRuntime(directory,{offlinePreview:true,...options});
 t.after(async()=>{await runtime.close();await rm(directory,{recursive:true,force:true});});
 let host=runtime.get(adapterId);
 const record=await host.create({operationId:operationId(),initial:host.adapter.initial(),confirmed:true});
 return {directory,get runtime(){return runtime;},get host(){return host;},record,
 async reopen(){await runtime.close();runtime=await openRuntime(directory,{offlinePreview:true,...options});host=runtime.get(adapterId);return host;},
 async turn(input,opts={}){const r=await host.store.read(record.id);return host.turn(r.id,{operationId:operationId(),revision:r.revision,input},opts);}};
}
