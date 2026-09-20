import {readFile} from 'node:fs/promises';
import {modelRuntime,modelRuntimeStatus} from './model-runtime.mjs';
import {setupHash} from './runtime-binding.mjs';
import {POLICY_VERSION} from '../plugins/history-window.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
const root=new URL('../',import.meta.url);
const paths=['studio/history-provider.mjs','studio/server.mjs','studio/history-dispatch.mjs','studio/history-branch-archive.mjs','plugins/history-window.mjs','plugins/history-window.manifest.json','plugins/catalog.mjs','plugins/host.mjs','studio/history-state.mjs','studio/history-runtime.mjs','studio/history-assembly.mjs','studio/history-session-store.mjs'];
const artifacts=async()=>Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha(await readFile(new URL(p,root)))])));
const frozen=await artifacts();
const descriptor={...structuredClone(modelRuntime.descriptor),format:3,historyPolicy:POLICY_VERSION,baseRuntimeHash:modelRuntime.hash,hostArtifacts:{...modelRuntime.descriptor.hostArtifacts,...frozen}};
export const historyRuntime={descriptor,hash:sha(canonical(descriptor)),async verify(){await modelRuntime.verify();if(canonical(await artifacts())!==canonical(frozen))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');}};
export const bindHistoryRuntime=s=>({format:3,hash:historyRuntime.hash,descriptor:structuredClone(descriptor),setupHash:setupHash(s)});
export function historyRuntimeStatus(s){
 if(s.runtime?.format!==3)return modelRuntimeStatus(s);
 if(s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!==setupHash(s))return {compatible:false,code:'RUNTIME_BINDING_INVALID'};
 return s.runtime.hash===historyRuntime.hash?{compatible:true,code:null,hash:s.runtime.hash}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'};
}
