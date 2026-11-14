import {hash,need,clone,bounded} from '../contracts.mjs';
// E2 currently exports narrative requirements, not a typed executable action.
// Never infer "consume" from a gift, invitation or an expected inventory difference.
export function inspectHandoff(value,target){
 bounded(value,{bytes:128*1024,depth:20});
 need(value?.format==='modelmirror.rpg.e2-handoff/1'&&value.executionStatus==='not_executed'&&value.requiresExecutionValidation===true,'E2_FORMAT');
 const p=value.proposal;need(p?.format==='modelmirror.rpg.e2-proposal/1'&&p.status==='awaiting_execution'&&p.confirmation?.proposalHash===p.hash&&p.confirmation.revision===p.revision,'E2_CONFIRMATION');
 const original={...clone(p),status:'review',confirmation:null};delete original.hash;
 need(hash(original)===p.hash,'E2_HASH');
 need(p.source&&typeof p.source.session==='string'&&typeof p.source.branch==='string'&&Number.isSafeInteger(p.source.version)&&/^[a-f0-9]{64}$/.test(p.source.hash),'E2_SOURCE');
 need(typeof p.goal==='string'&&Array.isArray(p.steps)&&Array.isArray(p.requirements),'E2_PROPOSAL');
 return {status:'not_executable',code:'E2_ACTION_MAPPING_REQUIRED',executionStatus:'not_executed',targetSession:target.id,
  source:clone(p.source),proposalHash:p.hash,goal:p.goal,steps:clone(p.steps),requirements:clone(p.requirements),
  notice:'这是外部历史提案，不是当前执行授权。E2 尚无已登记动作编号；赠送或邀请不能转换为消耗。预计值不会写入状态。'};
}
