import {readFile} from 'node:fs/promises';
import {summaryRuntime,summaryRuntimeStatus} from './summary-runtime.mjs';
import {setupHash} from './runtime-binding.mjs';
import {MEMORY_INSTRUCTION_HASH,TASK_POLICY} from './memory-task-state.mjs';
import {MEMORY_WRAPPER} from './memory-assembly.mjs';
import {POLICY_VERSION} from '../plugins/memory-palace.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
const paths=['studio/memory-runtime.mjs','studio/memory-assembly.mjs','studio/memory-branch-archive.mjs','studio/memory-session-store.mjs','studio/memory-state.mjs','studio/memory-task-state.mjs','studio/memory-tasks.mjs','studio/memory-task-provider.mjs','studio/memory-budget.mjs','plugins/memory-palace.mjs','plugins/memory-palace.manifest.json','plugins/host.mjs','plugins/catalog.mjs'];
const root=new URL('../',import.meta.url),artifacts=async()=>Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha(await readFile(new URL(p,root)))]))),frozen=await artifacts();
const descriptor={...structuredClone(summaryRuntime.descriptor),format:5,baseRuntimeHash:summaryRuntime.hash,memoryPolicy:POLICY_VERSION,memoryTaskPolicy:TASK_POLICY,memoryInstructionHash:MEMORY_INSTRUCTION_HASH,memoryWrapperHash:sha(MEMORY_WRAPPER),contextPolicy:'default:complete-raw-history; actual policy and memory contributions are per-session',hostArtifacts:{...summaryRuntime.descriptor.hostArtifacts,...frozen}};
export const memoryRuntime={descriptor,hash:sha(canonical(descriptor)),async verify(){await summaryRuntime.verify();if(canonical(await artifacts())!==canonical(frozen))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');}};
export const bindMemoryRuntime=s=>({format:5,hash:memoryRuntime.hash,descriptor:structuredClone(descriptor),setupHash:setupHash(s)});
export function memoryRuntimeStatus(s){
 if(s.runtime?.format!==5)return summaryRuntimeStatus(s);
 if(s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!==setupHash(s))return {compatible:false,code:'RUNTIME_BINDING_INVALID'};
 return s.runtime.hash===memoryRuntime.hash?{compatible:true,code:null,hash:s.runtime.hash}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'};
}
