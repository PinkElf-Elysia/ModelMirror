import {readFile} from 'node:fs/promises';
import {loadReviewedPlugins,loadReviewedCatalog,PLUGIN_ID,canonical,sha,fail} from '../../plugins/catalog.mjs';
const location=new URL('../resources/branch-save.manifest.json',import.meta.url);
const loaded=await readFile(location);
// A separate reviewed release, served only by an explicitly opted-in God host.
// The executable is the existing trusted branch implementation, unchanged.
export async function loadGodPlugins(options){
 const entries=await loadReviewedPlugins(options);
 const original=await loadReviewedCatalog(PLUGIN_ID),bytes=await readFile(location);
 const expected={...original.manifest,version:'1.1.0',compatibleCards:['earth','god']};
 let manifest;try{manifest=JSON.parse(bytes);}catch{throw fail('PLUGIN_MANIFEST_INVALID');}
 if(canonical(manifest)!==canonical(expected))throw fail('PLUGIN_MANIFEST_INVALID');
 if(sha(bytes)!==sha(loaded))throw fail('PLUGIN_RELEASE_CHANGED_RESTART_REQUIRED');
 return entries.map(e=>e.manifest?.id===PLUGIN_ID?{...original,manifest,manifestSha256:sha(bytes)}:e);
}

const releases={'rpg.model-selector':'1.1.0','rpg.history-window':'1.1.0','rpg.rolling-summary':'1.2.0','rpg.memory-palace':'1.1.0'};
const releaseURL=id=>new URL('../resources/'+id.slice(4)+'.manifest.json',import.meta.url);
const releaseBytes=new Map(await Promise.all(Object.keys(releases).map(async id=>[id,await readFile(releaseURL(id))])));
export async function loadGodPluginPackages(){
 const entries=await loadGodPlugins({rollingSummary:true,memoryPalace:true});
 return Promise.all(entries.map(async entry=>{
  const id=entry.manifest?.id;if(!releases[id])return entry;
  const bytes=await readFile(releaseURL(id));let manifest;
  try{manifest=JSON.parse(bytes);}catch{throw fail('PLUGIN_MANIFEST_INVALID');}
  if(canonical(manifest)!==canonical({...entry.manifest,version:releases[id],compatibleCards:['earth','god']}))throw fail('PLUGIN_MANIFEST_INVALID');
  if(sha(bytes)!==sha(releaseBytes.get(id)))throw fail('PLUGIN_RELEASE_CHANGED_RESTART_REQUIRED');
  return {...entry,manifest,manifestSha256:sha(bytes)};
 }));
}
