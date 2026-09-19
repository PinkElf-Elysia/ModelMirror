import {BranchArchive,completeHistory} from './branch-archive.mjs';
import {initialModelState,validChoice} from './model-runtime.mjs';
import {canonical,fail} from '../plugins/catalog.mjs';

export class ModelBranchArchive extends BranchArchive {
 prepare(source,input){
  if(source.runtime?.format!==2)return super.prepare(source,input);
  completeHistory(source);
  const at=source.turns[input.turn-1]?.model;
  if(!validChoice(at?.selection))throw fail('BRANCH_MODEL_EVIDENCE_MISSING');
  // This is a snapshot projection, never a mutation or migration of the parent.
  const projected={...source,modelState:initialModelState(at.selection)};
  return super.prepare(projected,input);
 }
 async read(id){
  const e=await super.read(id);
  if(e.session.runtime?.format===2){
   const model=e.snapshot.turns.at(-1)?.model;
   if(!validChoice(model?.selection)||canonical(e.snapshot.configuration.modelState)!==canonical(initialModelState(model.selection)))throw fail('BRANCH_MODEL_EVIDENCE_MISSING');
   const initial=e.session.modelState?.timeline?.[0]?.choice;
   if(canonical(initial)!==canonical(model.selection))throw fail('BRANCH_MODEL_ORIGIN_MISMATCH');
  }
  return e;
 }
}
