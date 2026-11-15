import {readFile,writeFile} from 'node:fs/promises';import {join,resolve,dirname} from 'node:path';import {hash} from '../../contracts.mjs';import {SLOTS,INPUTS} from './contract.mjs';
const dir=resolve(process.argv[2]||'.');if(dirname(dir)!==resolve('C:/tmp/mm-r9-live'))throw Error('R9_AUDIT_SCOPE');
const read=n=>readFile(join(dir,n),'utf8').then(JSON.parse);
const [review,evidence,ledger,world,claim,cleanup,before,keeper,visitor,afterKeeper,afterVisitor,next]=await Promise.all(['review.json','transport-evidence.json','task-ledger.json','world-record.json','execution-claim.json','cleanup.json','before-confirm.json','after-slot-2.json','after-slot-3.json','after-adopt-2.json','after-adopt-3.json','next-world-context-offline.json'].map(read));
const tasks=Object.values(ledger.tasks),calls=evidence.calls,k=tasks.find(t=>t.purpose==='participant-keeper'),v=tasks.find(t=>t.purpose==='participant-visitor');
const checks={
 threeCompleted:review.error===null&&calls.length===3&&calls.every(c=>c.result?.status==='completed')&&tasks.length===3&&tasks.every(t=>t.adoption==='completed'),
 localProposalDidNotExecute:hash(before.state)===hash(claim.initial)&&before.events.filter(e=>e.kind==='action').length===0,
 exactState:hash(world.state)===hash(claim.expected)&&world.state.supplies[0].quantity===3,
 oneAction:world.events.filter(e=>e.kind==='action').length===1,
 commonAllowance:review.budget.calls===3&&review.budget.limitCalls===3&&review.budget.limitMicroUSD===300000,
 oneAttemptEach:tasks.every(t=>t.stages.length===1&&t.stages[0].attempts.length===1&&!t.fallbackUsed&&t.policy.maxCalls===1),
 withinCaps:calls.every(c=>c.bound.reserveMicros<=SLOTS[c.slot].cap*1e6&&c.result.billing?.microUSD<=c.bound.reserveMicros)&&review.budget.occupiedMicroUSD<=300000,
 frozenParameters:calls.every(c=>c.payload.parameters.max_tokens===SLOTS[c.slot].max&&c.payload.parameters.temperature===.7&&c.payload.parameters.top_p===.8),
 noTools:calls.every(c=>c.payload.tools.length===0&&c.payload.tool_choice==='none'&&!c.payload.messages.some(m=>m.role==='tool'||m.tool_calls)),
 keeperScope:k.observation.events.length===1&&k.observation.dialogue.length===0&&!JSON.stringify(k.stages[0].request).includes('陆遥'),
 visitorScope:v.observation.events.length===0&&v.observation.resources.length===0&&v.observation.facts.length===0&&v.observation.dialogue.length===0&&!JSON.stringify(v.stages[0].request).includes('沈砚'),
 explicitAdoption:keeper.scene.entries.length===0&&visitor.scene.entries.length===1&&afterKeeper.scene.entries.length===1&&afterVisitor.scene.entries.length===2&&world.scene.entries.length===2,
 noParticipantStateWrites:[keeper,visitor,afterKeeper,afterVisitor].every(x=>hash(x.world.state)===hash(claim.expected)),
 frozenInputs:calls.find(c=>c.slot===2).packet.meta.input===INPUTS[2]&&calls.find(c=>c.slot===3).packet.meta.input===INPUTS[3],
 nextContextIsOffline:next.kind==='assembly-only-no-dispatch'&&next.messages[0].content.includes('adopted-speech-not-world-fact')&&world.scene.entries.every(e=>next.messages[0].content.includes(JSON.stringify(e.body))),
 credentialsRemoved:cleanup.credentialDirectoryRemoved&&cleanup.localTokenRemoved&&!cleanup.masterKeyWrittenToDisk&&!cleanup.sharedConfigurationChanged
};
function exactCost(c){let last=null;for(const l of (c.response?.body?.sse??'').split('\n')){if(!l.startsWith('data:')||l.includes('[DONE]'))continue;try{const f=JSON.parse(l.slice(5));if(typeof f.usage?.cost==='number')last=f.usage.cost;}catch{}}return last;}
const rows=calls.map(c=>({slot:c.slot,reportedUSD:exactCost(c),estimatedUSD:c.result?.accounting?.estimatedCostMicros==null?null:c.result.accounting.estimatedCostMicros/1e6,inputTokens:c.result?.usage?.inputTokens??null,outputTokens:c.result?.usage?.outputTokens??null,reasoningTokens:c.result?.usage?.reasoningTokens??null,proseFirstMs:c.result?.timing?.source==='host-progress-poll'?c.result.timing.proseFirstMs:null,completedMs:c.result?.timing?.completedMs??null,timingSource:c.result?.timing?.source??'unknown'}));
const sum=k=>rows.every(r=>r[k]!==null)?rows.reduce((n,r)=>n+r[k],0):null;
const result={mode:review.mode,checks,passed:Object.values(checks).filter(Boolean).length,total:Object.keys(checks).length,requests:rows,totalReportedUSD:sum('reportedUSD'),totalEstimatedUSD:sum('estimatedUSD'),tokens:{input:sum('inputTokens'),output:sum('outputTokens'),reasoning:sum('reasoningTokens')},limits:['本轮关闭工具，不重复验证真实原生查询','人物语义另作人工阅读，不以状态无写入替代正文一致性评价','后续世界续写仅装配核对，未真实派发','首字为250ms主机采样；未捕获标为空，完成时间不冒充首字']};
await writeFile(join(dir,'assessment.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));if(Object.values(checks).some(x=>!x))process.exitCode=1;
