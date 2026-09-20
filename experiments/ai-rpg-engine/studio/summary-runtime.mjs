import {readFile} from 'node:fs/promises';
import {historyRuntime,historyRuntimeStatus} from './history-runtime.mjs';
import {setupHash} from './runtime-binding.mjs';
import {POLICY_VERSION} from '../plugins/rolling-summary.mjs';
import {SUMMARY_INSTRUCTION_HASH,SUMMARY_WRAPPER} from './summary-state.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
const paths=['studio/summary-host.mjs','studio/summary-http.mjs','studio/summary-assembly.mjs','studio/summary-branch-archive.mjs','studio/summary-operations.mjs','plugins/rolling-summary.mjs','plugins/rolling-summary.manifest.json','studio/summary-state.mjs','studio/summary-runtime.mjs','studio/summary-session-store.mjs','studio/summary-tasks.mjs','studio/summary-budget.mjs','studio/summary-task-provider.mjs'];
const root=new URL('../',import.meta.url);
const artifacts=async()=>Object.fromEntries(await Promise.all(paths.map(async path=>[path,sha(await readFile(new URL(path,root)))])));
const frozen=await artifacts();
const descriptor={...structuredClone(historyRuntime.descriptor),format:4,baseRuntimeHash:historyRuntime.hash,contextPolicy:'default:complete-raw-history; actual policy is per-session',summaryPolicy:POLICY_VERSION,summaryInstructionHash:SUMMARY_INSTRUCTION_HASH,summaryWrapperHash:sha(SUMMARY_WRAPPER),hostArtifacts:{...historyRuntime.descriptor.hostArtifacts,...frozen}};
export const summaryRuntime={descriptor,hash:sha(canonical(descriptor)),async verify(){await historyRuntime.verify();if(canonical(await artifacts())!==canonical(frozen))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');}};
export const bindSummaryRuntime=s=>({format:4,hash:summaryRuntime.hash,descriptor:structuredClone(descriptor),setupHash:setupHash(s)});
export function summaryRuntimeStatus(s){
 if(s.runtime?.format!==4)return historyRuntimeStatus(s);
 if(s.runtime.hash!==sha(canonical(s.runtime.descriptor))||s.runtime.setupHash!==setupHash(s))return {compatible:false,code:'RUNTIME_BINDING_INVALID'};
 return s.runtime.hash===summaryRuntime.hash?{compatible:true,code:null,hash:s.runtime.hash}:{compatible:false,code:'RUNTIME_VERSION_MISMATCH'};
}
