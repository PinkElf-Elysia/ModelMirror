import {join} from 'node:path';
import {AuthoringStore} from '../../authoring/store.mjs';
import {runtimeManifest} from '../manifest.mjs';
import {OfflineTransport} from '../tasks/transport.mjs';
import {openAssembly} from '../assembly/runtime.mjs';
import {ensureParticipants} from './participants.mjs';
import {sceneCatalog} from './catalog.mjs';
import {SceneRuntime} from './runtime.mjs';
import {sceneFixture} from './fixture.mjs';
import {need} from '../contracts.mjs';
export async function openScene(directory,{offlinePreview=false,limits={calls:60,microUSD:4000000},delayMs=30}={}){
 need(offlinePreview===true,'SCENE_OFFLINE_ONLY');
 const works=await AuthoringStore.open(join(directory,'templates'),{recoverDeadOwner:true});
 try{
  const participants=await ensureParticipants(works),catalog=await sceneCatalog(participants);
  const manifest=await runtimeManifest([new URL('./bootstrap.mjs',import.meta.url)],[{url:new URL('../../package-lock.json',import.meta.url)}]);
  const core=await openAssembly(join(directory,'runtime'),{offlinePreview:true,limits,catalog,manifestHash:manifest.hash,
   transportFactory:(path,w)=>OfflineTransport.open(path,sceneFixture(w),null,{delayMs})});
  core.closers.unshift(works);return new SceneRuntime(core,participants);
 }catch(e){await works.close();throw e;}
}
