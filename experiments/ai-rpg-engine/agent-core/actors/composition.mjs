import {join} from 'node:path';
import {openRuntime as openWorld} from '../actions/composition.mjs';
import {AuthoringStore} from '../../authoring/store.mjs';
import {TaskRecords} from './tasks.mjs';
import {ActorSimulator} from './simulator.mjs';
import {cultivationWorldPort} from './world-port.mjs';
import {BridgeHost} from './host.mjs';
import {ensureCharacter} from './fixtures.mjs';
import {runtimeManifest} from '../manifest.mjs';
import {need} from '../contracts.mjs';
export async function openRuntime(directory,{offlinePreview=false}={}){
 need(offlinePreview===true,'BRIDGE_PREVIEW_DISABLED');
 const manifest=await runtimeManifest([new URL('./composition.mjs',import.meta.url)]);
 const owners=[];
 try{
  const worlds=await openWorld(join(directory,'worlds'),{offlinePreview:true});owners.push(worlds);
  const works=await AuthoringStore.open(join(directory,'works'),{recoverDeadOwner:true});owners.push(works);
  const reference=(await ensureCharacter(works)).reference;
  const records=await TaskRecords.open(join(directory,'bridge'));owners.push(records);
  const provider=await ActorSimulator.open(join(directory,'provider'));owners.push(provider);
  const host=new BridgeHost({world:worlds.get('cultivation'),worldPort:cultivationWorldPort,works,records,provider,reference,runtimeHash:manifest.hash});
  return {host,manifest,async close(){for(const owner of [...owners].reverse())await owner.close();}};
 }catch(e){for(const owner of owners.reverse())await owner.close().catch(()=>{});throw e;}
}
