import {panelDefinition} from './variable-panel.mjs';
import {emptyRequirements} from './requirements.mjs';
import {CONTROLLED_BRANCH_FORMAT,isControlledSession} from './controlled-receipt.mjs';
import {canonical,sha256} from './contracts.mjs';
import {validateBranchCheckpoint} from './branch-checkpoint.mjs';
import {branchOrigin,originTurns} from './branch-origin.mjs';
import {BRANCH_SESSION_FORMAT,jointSessionId,validateJointSession} from './joint-session-state.mjs';
import {emptyModelState} from './model-control.mjs';
import {emptyHistoryState} from './history-control.mjs';
import {initialJointSummary} from './joint-summary.mjs';
import {initialMemoryJobs} from './memory-jobs.mjs';
const digest=v=>sha256(canonical(v));
const createId=c=>'branch-child-'+digest(c.id);
export const branchChildId=c=>jointSessionId(createId(c));
export const branchCreateHash=c=>digest({checkpointId:c.id,checkpointHash:c.sha256,intent:c.intent});
export function createBranchSession(checkpoint,runtime,executionHash){
 validateBranchCheckpoint(checkpoint);
 const origin=branchOrigin(checkpoint),source=origin.inheritance,id=branchChildId(checkpoint),time=new Date().toISOString();
 const binding=runtime.bindSession({sessionId:id,openingText:source.binding.openingText});
 const s={format:isControlledSession(checkpoint.archive)?CONTROLLED_BRANCH_FORMAT:BRANCH_SESSION_FORMAT,id,revision:0,createdAt:time,updatedAt:time,
  create:{operationId:createId(checkpoint),requestHash:branchCreateHash(checkpoint)},binding,executionHash,
  allowanceId:source.allowanceId,presetId:source.presetId,examples:structuredClone(source.examples),draft:'',origin,
  requirements:emptyRequirements(source.settings.requirements),runs:[],operations:[],turns:originTurns(origin),models:emptyModelState(),history:emptyHistoryState(source.settings.history),memoryEdits:[]};
 const panel=panelDefinition(runtime.configuration().document.settings.variablePanel);if(panel)s.variablePanel=panel;
 s.summary=initialJointSummary(s);s.memory=initialMemoryJobs(s);
 return validateJointSession(s,executionHash);
}
