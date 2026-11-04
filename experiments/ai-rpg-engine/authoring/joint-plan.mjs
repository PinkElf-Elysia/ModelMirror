import {canonical,keys,isId,isHash,sha256,fail} from './contracts.mjs';
import {requireMemoryAuthorization} from './memory-state.mjs';
export const JOINT_PLAN_FORMAT='modelmirror.rpg.authored-joint-plan/1';
const digest=v=>sha256(canonical(v)),integer=n=>Number.isSafeInteger(n)&&n>=0;
export const stageOperationId=(sessionId,operationId,stageId)=>'stage-'+digest({sessionId,operationId,stageId});
export function planJointCalls(input){
 canonical(input);keys(input,['sessionId','operationId','kind','completedTurns','summaryCoveredThrough','memoryProcessedThrough','authorization','sourceHash','policyRevision']);
 if(!/^session-[a-f0-9]{64}$/.test(input.sessionId)||!isId(input.operationId)||!['send','update'].includes(input.kind)||
  ![input.completedTurns,input.summaryCoveredThrough,input.memoryProcessedThrough].every(integer)||input.completedTurns>1000||
  input.summaryCoveredThrough>Math.max(0,input.completedTurns-1)||input.memoryProcessedThrough>input.completedTurns||!isHash(input.sourceHash)||!isHash(input.policyRevision))fail('JOINT_PLAN_INVALID');
 keys(input.authorization,['history','summary','memory']);Object.values(input.authorization).forEach(requireMemoryAuthorization);
 const m2=input.authorization.summary.enabled,m3=input.authorization.memory.enabled,stages=[];
 const add=(stageId,purpose)=>stages.push({stageId,purpose,operationId:stageId==='story'?input.operationId:stageOperationId(input.sessionId,input.operationId,stageId)});
 if(m2&&input.summaryCoveredThrough<Math.max(0,input.completedTurns-1)){add('summary-before','summary');add('compression-before','compression');}
 if(input.kind==='send')add('story','story');
 if(input.kind==='send'&&m2&&input.completedTurns>=1){add('summary-after','summary');add('compression-after','compression');}
 if(m3&&(input.kind==='send'||input.memoryProcessedThrough<input.completedTurns))add('memory-after','memory');
 const body={format:JOINT_PLAN_FORMAT,input:structuredClone(input),stages,maximumCalls:stages.length};
 return {...body,sha256:digest(body)};
}
export function validateJointPlan(value){canonical(value);keys(value,['format','input','stages','maximumCalls','sha256']);if(canonical(value)!==canonical(planJointCalls(value.input)))fail('JOINT_PLAN_INVALID');return value;}
