import {readFile} from 'node:fs/promises';
import {activeSystem,loadFrozen} from '../card-replica/lib/assembly.mjs';
import {models} from './provider.mjs';
import {sha,canonical,fail} from '../plugins/catalog.mjs';
const paths=['card-replica/lib/store.mjs','card-replica/lib/assembly.mjs','card-replica/resources/prompts.json','card-replica/resources/MANIFEST.json','card-replica/src/data.ts','card-replica/src/main.tsx','studio/provider.mjs','studio/models.json','studio/versioned-store.mjs','studio/branch-archive.mjs','studio/runtime-binding.mjs'];
const root=new URL('../',import.meta.url);
async function artifacts(){return Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha(await readFile(new URL(p,root)))])));}
const loaded=await artifacts();
const {prompts}=loadFrozen();
const descriptor={format:1,cardId:'earth',artifacts:loaded,author:{system:sha(activeSystem),prefix:sha(prompts.prefix),suffix:sha(prompts.suffix),worldbookSource:sha(prompts.worldbookSource)},model:models.earth,worldbook:'disabled-missing-author-metadata',historyPolicy:'complete-raw-history'};
export const earthRuntime={descriptor,hash:sha(canonical(descriptor)),async verify(){if(canonical(await artifacts())!==canonical(loaded))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');}};
// The reviewed v1 saves predate the platform shell and optional opening text.
// Rebuild only that exact descriptor from current author/model artifacts; any
// unrelated drift still changes its hash. Saved bindings are never rewritten.
const legacyDescriptor={...descriptor,artifacts:{...loaded,
 'card-replica/lib/assembly.mjs':'7507921b7c15695d62a71e65acc4f7bf5f8d8083a9812b855f17bd5585905bed',
 'card-replica/src/main.tsx':'56aff4ac4476151b6077f5a795b30886dbafb511340b44f4beec36fa06d6d9f9',
 'studio/runtime-binding.mjs':'e603069d121e0d0b1e99daeeb671fbb04e65df5a85c642edb34adeb9e2ee57b0'
}};
export const legacyEarthRuntime={descriptor:legacyDescriptor,hash:sha(canonical(legacyDescriptor)),verify:earthRuntime.verify};
export const setupHash=s=>sha(canonical({characterText:s.characterText,world:s.world,params:s.params,mode:s.mode}));
export function bindRuntime(runtime,s){return {format:1,hash:runtime.hash,descriptor:structuredClone(runtime.descriptor),setupHash:setupHash(s)};}
export function runtimeStatus(runtime,s){
 if(!s.runtime)return {compatible:false,code:'RUNTIME_UNVERIFIED_LEGACY'};
 if(s.runtime.format!==1||s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!==setupHash(s))return {compatible:false,code:'RUNTIME_BINDING_INVALID'};
 if(s.runtime.hash!==runtime.hash&&!(runtime===earthRuntime&&s.runtime.hash===legacyEarthRuntime.hash))return {compatible:false,code:'RUNTIME_VERSION_MISMATCH'};
 return {compatible:true,code:null,hash:s.runtime.hash};
}
