import {HistoryBranchArchive} from './history-branch-archive.mjs';
import {initialModelState,validChoice} from './model-runtime.mjs';
import {initialHistoryState} from './history-state.mjs';
import {requireSummaryState} from './summary-state.mjs';
import {requireContextEvidence} from './summary-assembly.mjs';
import {canonical,fail} from '../plugins/catalog.mjs';
export class SummaryBranchArchive extends HistoryBranchArchive {
 prepare(source,input){
  if(source.runtime?.format!==4)return super.prepare(source,input);
  requireContextEvidence(source);
  const at=source.turns[input.turn-1];if(!at?.contextPolicy||!validChoice(at.model?.selection))throw fail('BRANCH_SUMMARY_EVIDENCE_MISSING');
  const projected={...source,modelState:initialModelState(at.model.selection),historyWindow:initialHistoryState(at.contextPolicy.historyConfig),rollingSummary:structuredClone(at.contextPolicy.summarySnapshot)};
  delete projected.summaryTasks;
  const envelope=super.prepare(projected,input);requireSummaryState(envelope.session);return envelope;
 }
 async read(id){
  const e=await super.read(id);if(e.session.runtime?.format!==4)return e;
  const frozen={...e.snapshot.configuration,...e.snapshot,requests:{}};
  requireContextEvidence(frozen);requireContextEvidence(e.session);requireSummaryState(e.session);
  const at=e.snapshot.turns.at(-1),config=e.snapshot.configuration;
  if(!at?.contextPolicy||'summaryTasks' in config||canonical(config.rollingSummary)!==canonical(at.contextPolicy.summarySnapshot)||canonical(config.historyWindow)!==canonical(initialHistoryState(at.contextPolicy.historyConfig))||canonical(config.modelState)!==canonical(initialModelState(at.model.selection))||canonical(e.session.modelState?.timeline?.[0]?.choice)!==canonical(at.model.selection))throw fail('BRANCH_SUMMARY_ORIGIN_MISMATCH');
  return e;
 }
}
