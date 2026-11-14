import {CoreHost} from './host.mjs';
import {CoreSimulator} from './simulator.mjs';
import {cultivationAdapter} from './adapters/cultivation/index.mjs';
import {lighthouseAdapter} from './adapters/lighthouse/index.mjs';
import {runtimeManifest} from './manifest.mjs';
import {validateAdapter,need} from './contracts.mjs';
import {join} from 'node:path';
export async function adapters(){
 const manifest=await runtimeManifest([new URL('./composition.mjs',import.meta.url)],[
  {url:new URL('../cultivation-card/author-system.txt',import.meta.url)},
  {url:new URL('../local-state/tools/query-tool.json',import.meta.url)},
  {url:new URL('../package-lock.json',import.meta.url)},
  {url:new URL('../plugins/custom-config/',import.meta.url),directory:true},
  {url:new URL('../plugins/mod-panel/',import.meta.url),directory:true},
 ]);
 const list=await Promise.all([cultivationAdapter(),lighthouseAdapter()]);
 for(const a of list){a.binding.manifestHash=manifest.hash;validateAdapter(a);}
 return {list,manifest};
}
export async function openRuntime(directory,{offlinePreview=false,fault}={}){
 need(offlinePreview===true,'CORE_PREVIEW_DISABLED');const registry=await adapters(),hosts=new Map();
 try{for(const a of registry.list){const provider=await CoreSimulator.open(join(directory,a.id,'provider'),a.fixture);try{hosts.set(a.id,await CoreHost.open({directory:join(directory,a.id),adapter:a,offlinePreview,fault,provider}));}catch(e){await provider.close();throw e;}}}
 catch(e){for(const h of hosts.values())await h.close();throw e;}
 return {registry,hosts,get(id){need(hosts.has(id),'CORE_ADAPTER_NOT_REGISTERED');return hosts.get(id);},async close(){for(const h of hosts.values())await h.close();}};
}
