import {readFile} from 'node:fs/promises';
import {godPluginProfile} from './plugin-runtime.mjs';
import {godAdapter} from './adapter.mjs';
import {loadFrozen} from './assembly.mjs';
import {assembleWithOutputSuffix,loadOutputSuffix} from './output-suffix.mjs';
import {createModelPolicy} from '../../studio/card-model-policy.mjs';
import {createCardState} from '../../studio/card-state.mjs';
import {createCardContext} from '../../studio/card-context.mjs';
import {setupHash} from '../../studio/runtime-binding.mjs';
import {canonical,sha,fail} from '../../plugins/catalog.mjs';
export const GOD_REAL_MODEL='google/gemini-3.8-flash';
export const GOD_REAL_PARAMETER_PROFILE='god-long-v1';
export const GOD_REAL_PARAMETERS=Object.freeze({temperature:0.7,top_p:0.8,max_tokens:32768});
export const godRealPolicy=createModelPolicy({cardId:'god',parametersFor:m=>m===GOD_REAL_MODEL?GOD_REAL_PARAMETERS:null});
export async function createGodRealProfile(selection){
 if(selection?.model!==GOD_REAL_MODEL||selection.available!==true)throw fail('GOD_REAL_SELECTION_UNAVAILABLE');
 const initial=godRealPolicy.controlledChoice(selection);
 const suffix=loadOutputSuffix();
 const paths=[...Object.keys(godPluginProfile.runtime.descriptor.hostArtifacts),'god-card/lib/real-profile.mjs','god-card/lib/real-host.mjs','god-card/lib/output-suffix.mjs','god-card/resources/output-suffix.json'];
 const artifacts=async()=>Object.fromEntries(await Promise.all([...new Set(paths)].sort().map(async p=>[p,sha(await readFile(new URL('../../'+p,import.meta.url)))])));
 const frozen=await artifacts();
 const descriptor={format:5,cardId:'god',release:'god-plugins-real-output-suffix-v1',parameterProfile:GOD_REAL_PARAMETER_PROFILE,author:godPluginProfile.runtime.descriptor.author,userSupplement:{placement:'story-user-suffix-every-turn',sha256:suffix.textIntegrity.sha256,sourceSha256:suffix.source.sha256},hostArtifacts:frozen,models:[initial],mode:'controlled-real',contextPolicy:'default:complete-raw-history; actual policy is per-session'};
 const runtime={descriptor,hash:sha(canonical(descriptor)),async verify(){if(canonical(await artifacts())!==canonical(frozen))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');loadFrozen();}};
 const state=createCardState(godRealPolicy),context=createCardContext({cardId:'god',assemble:assembleWithOutputSuffix,state});
 return Object.freeze({state,context,modelPolicy:godRealPolicy,runtime,
  create(input){if(!input||Object.keys(input).sort().join(',')!=='mode,setup'||input.mode!=='real')throw fail('GOD_REAL_CONFIGURATION_REQUIRED',400);return {...godAdapter.create({setup:input.setup,mode:'offline'}),mode:'real'};},
  initialSelection:()=>structuredClone(initial),
  bind:s=>({format:5,hash:runtime.hash,descriptor:structuredClone(descriptor),setupHash:setupHash(s)}),
  status(s){
   if(s.cardId!=='god'||s.runtime?.format!==5||s.mode!=='real')return {compatible:false,code:'GOD_REAL_RUNTIME_INCOMPATIBLE'};
   if(s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!==setupHash(s))return {compatible:false,code:'RUNTIME_BINDING_INVALID'};
   return s.runtime.hash===runtime.hash?{compatible:true,code:null,hash:runtime.hash}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'};
  }
 });
}
