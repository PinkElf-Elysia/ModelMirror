import {runtimeManifest} from '../manifest.mjs';
import {OfflineTransport} from '../tasks/transport.mjs';
import {openAssembly} from './runtime.mjs';
import {exampleCatalog} from './catalog.mjs';
import {wireFixture} from './fixture.mjs';
export async function openPreview(directory,{offlinePreview=false,limits,delayMs=40,catalog}={}){
 const manifest=await runtimeManifest([new URL('./bootstrap.mjs',import.meta.url)],[{url:new URL('../../package-lock.json',import.meta.url)}]);
 return openAssembly(directory,{offlinePreview,limits,manifestHash:manifest.hash,catalog:catalog??await exampleCatalog(),
  transportFactory:(path,w)=>OfflineTransport.open(path,wireFixture(w),null,{delayMs})});
}
