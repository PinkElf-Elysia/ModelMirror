import {SummaryBranchArchive} from './summary-branch-archive.mjs';
import {initialModelState} from './model-runtime.mjs';
import {initialHistoryState} from './history-state.mjs';
import {requireSummaryState} from './summary-state.mjs';
import {requireMemoryEvidence,standbyMemoryTasks,requireMemorySnapshot} from './memory-assembly.mjs';
import {canonical,fail} from '../plugins/catalog.mjs';
export class MemoryBranchArchive extends SummaryBranchArchive{
 prepare(source,input){
  if(source.runtime?.format!==5)return super.prepare(source,input);
  requireMemoryEvidence(source);const at=source.turns[input.turn-1];if(!at?.memorySettlement)throw fail('BRANCH_MEMORY_NOT_SETTLED');
  const snapshot=requireMemorySnapshot(at.memorySettlement.snapshot,source.turns.slice(0,input.turn));
  const projected={...source,modelState:initialModelState(at.model.selection),historyWindow:initialHistoryState(at.contextPolicy.historyConfig),rollingSummary:structuredClone(at.contextPolicy.summarySnapshot),memoryPalace:structuredClone(snapshot.library),memoryTasks:standbyMemoryTasks(snapshot.config)};
  delete projected.summaryTasks;delete projected.memoryLegacyInputs;
  const envelope=super.prepare(projected,input);requireSummaryState(envelope.session);requireMemoryEvidence(envelope.session);return envelope;
 }
 async read(id){
  const e=await super.read(id);if(e.session.runtime?.format!==5)return e;
  const frozen={...e.snapshot.configuration,...e.snapshot,revision:e.snapshot.source.revision,requests:{}};requireMemoryEvidence(frozen);requireMemoryEvidence(e.session);requireSummaryState(e.session);
  const at=e.snapshot.turns.at(-1),config=e.snapshot.configuration,settled=at?.memorySettlement?.snapshot;
  if(!settled||'summaryTasks' in config||'memoryLegacyInputs' in config||canonical(config.memoryPalace)!==canonical(settled.library)||canonical(config.memoryTasks)!==canonical(standbyMemoryTasks(settled.config))||canonical(config.rollingSummary)!==canonical(at.contextPolicy.summarySnapshot)||canonical(config.historyWindow)!==canonical(initialHistoryState(at.contextPolicy.historyConfig))||canonical(config.modelState)!==canonical(initialModelState(at.model.selection))||canonical(e.session.modelState?.timeline?.[0]?.choice)!==canonical(at.model.selection))throw fail('BRANCH_MEMORY_ORIGIN_MISMATCH');
  return e;
 }
}
