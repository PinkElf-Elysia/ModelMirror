import {readFile} from 'node:fs/promises';
import {memoryRuntime} from '../../studio/memory-runtime.mjs';
import {createModelPolicy} from '../../studio/card-model-policy.mjs';
import {createCardState} from '../../studio/card-state.mjs';
import {createCardContext} from '../../studio/card-context.mjs';
import {setupHash} from '../../studio/runtime-binding.mjs';
import {godAdapter} from './adapter.mjs';
import {loadFrozen} from './assembly.mjs';
import {canonical,sha,fail} from '../../plugins/catalog.mjs';
// Simulation profiles only. No production God provider/model configuration exists.
export const offlineModels=Object.freeze(['a','b'].map((x,i)=>Object.freeze({model:'synthetic/god-offline-'+x,name:'离线模拟 '+x.toUpperCase()+'（不调用模型）',selectionId:'god-offline-'+x,selectionRevision:'offline-v1',available:true,parameters:Object.freeze({max_tokens:2048+i*1024,temperature:.3+i*.1})})));
const modelPolicy=createModelPolicy({cardId:'god',parametersFor:name=>offlineModels.find(m=>m.model===name)?.parameters});
const state=createCardState(modelPolicy),context=createCardContext({cardId:'god',assemble:godAdapter.assemble,state});
const root=new URL('../../',import.meta.url);
const paths=[...new Set([...Object.keys(memoryRuntime.descriptor.hostArtifacts),
 'studio/card-context.mjs','studio/card-state.mjs','studio/card-session-store.mjs','studio/memory-http.mjs','studio/summary-http.mjs',
 ...['adapter','assembly','setup','http','plugins','plugin-runtime','plugin-host'].map(n=>'god-card/lib/'+n+'.mjs'),
 'god-card/resources/prompts.json','god-card/resources/MANIFEST.json',
 ...['branch-save','model-selector','history-window','rolling-summary','memory-palace'].map(n=>'god-card/resources/'+n+'.manifest.json')])].sort();
const artifacts=async()=>Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha(await readFile(new URL(p,root)))])));
const frozen=await artifacts();
const descriptor={format:5,cardId:'god',release:'god-plugins-offline-v1',author:{system:loadFrozen().manifest.system.sha256,prefix:null,suffix:null,worldbook:null},hostArtifacts:frozen,models:offlineModels,mode:'offline-only',contextPolicy:'default:complete-raw-history; actual policy is per-session'};
const runtime={descriptor,hash:sha(canonical(descriptor)),async verify(){if(canonical(await artifacts())!==canonical(frozen))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');loadFrozen();}};
export const godPluginProfile=Object.freeze({
 state,context,modelPolicy,runtime,create:godAdapter.create,initialSelection:()=>modelPolicy.controlledChoice(offlineModels[0]),
 bind:s=>({format:5,hash:runtime.hash,descriptor:structuredClone(descriptor),setupHash:setupHash(s)}),
 status(s){
  if(s.cardId!=='god'||s.runtime?.format!==5)return {compatible:false,code:'GOD_PLUGIN_RUNTIME_INCOMPATIBLE'};
  if(s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!==setupHash(s))return {compatible:false,code:'RUNTIME_BINDING_INVALID'};
  return s.runtime.hash===runtime.hash?{compatible:true,code:null,hash:runtime.hash}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'};
 }
});
