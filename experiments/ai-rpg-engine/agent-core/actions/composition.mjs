import {actionAdapters} from './adapters.mjs';
import {ActionHost} from './host.mjs';import {ActionSimulator} from './simulator.mjs';
import {runtimeManifest} from '../manifest.mjs';import {need} from '../contracts.mjs';import {join} from 'node:path';
export async function openRuntime(directory,{offlinePreview=false,fault}={}){
 need(offlinePreview===true,'CORE_PREVIEW_DISABLED');
 const manifest=await runtimeManifest([new URL('./composition.mjs',import.meta.url)],[
 {url:new URL('../../cultivation-card/author-system.txt',import.meta.url)},{url:new URL('../../local-state/tools/query-tool.json',import.meta.url)},
 {url:new URL('../context/reference-index.json',import.meta.url)},{url:new URL('../../package-lock.json',import.meta.url)},
 {url:new URL('../../plugins/custom-config/',import.meta.url),directory:true},{url:new URL('../../plugins/mod-panel/',import.meta.url),directory:true}]);
 const list=await actionAdapters(),hosts=new Map();
 try{for(const a of list){a.binding.manifestHash=manifest.hash;
  const provider=await ActionSimulator.open(join(directory,a.id,'provider'),a);
  try{hosts.set(a.id,await ActionHost.open({directory:join(directory,a.id),adapter:a,provider,offlinePreview,fault}));}catch(e){await provider.close();throw e;}
 }}catch(e){for(const h of hosts.values())await h.close();throw e;}
 return {registry:{list,manifest},get(id){need(hosts.has(id),'CORE_ADAPTER_NOT_REGISTERED');return hosts.get(id);},async close(){for(const h of hosts.values())await h.close();}};
}
