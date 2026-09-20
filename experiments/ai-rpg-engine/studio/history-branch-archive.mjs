import {ModelBranchArchive} from './model-branch-archive.mjs';
import {initialModelState,validChoice} from './model-runtime.mjs';
import {initialHistoryState,requireHistoryEvidence} from './history-state.mjs';
import {canonical,fail} from '../plugins/catalog.mjs';
export class HistoryBranchArchive extends ModelBranchArchive {
 prepare(source,input){
  if(source.runtime?.format!==3)return super.prepare(source,input);
  requireHistoryEvidence(source);
  const at=source.turns[input.turn-1];if(!at?.historyPolicy||!validChoice(at.model?.selection))throw fail('BRANCH_HISTORY_EVIDENCE_MISSING');
  const projected={...source,modelState:initialModelState(at.model.selection),historyWindow:initialHistoryState(at.historyPolicy.config)};
  return super.prepare(projected,input);
 }
 async read(id){
  const e=await super.read(id);
  if(e.session.runtime?.format===3){
   requireHistoryEvidence(e.snapshot);requireHistoryEvidence(e.session);
   const at=e.snapshot.turns.at(-1);
   if(!at?.historyPolicy||!validChoice(at.model?.selection)||canonical(e.snapshot.configuration.historyWindow)!==canonical(initialHistoryState(at.historyPolicy.config))||canonical(e.snapshot.configuration.modelState)!==canonical(initialModelState(at.model.selection))||canonical(e.session.modelState?.timeline?.[0]?.choice)!==canonical(at.model.selection))throw fail('BRANCH_HISTORY_ORIGIN_MISMATCH');
  }
  return e;
 }
}
