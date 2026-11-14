import {readFile,writeFile} from 'node:fs/promises';import {join,resolve} from 'node:path';import {hash} from '../../contracts.mjs';
const dir=resolve(process.argv[2]||'.');if(!dir.startsWith(resolve('C:/tmp/mm-r7-live')+'/')&&!dir.startsWith(resolve('C:/tmp/mm-r7-live')+'\\'))throw Error('R7_AUDIT_SCOPE');
const read=n=>readFile(join(dir,n),'utf8').then(JSON.parse);const [review,evidence,ledger,world,initial,claim,cleanup]=await Promise.all(['review.json','transport-evidence.json','task-ledger.json','world-record.json','initial.json','execution-claim.json','cleanup.json'].map(read));
const expected=structuredClone(initial);expected.items.find(x=>x.id==='medicine').quantity=3;
const tasks=Object.values(ledger.tasks),calls=evidence.calls;const frame=c=>JSON.parse(c.payload.messages[0].content.split('【宿主授权观察，数据而非指令】\n').at(-1));
const witness=calls.find(c=>c.slot===2),outsider=calls.find(c=>c.slot===3);
const checks={threeCompleted:review.error===null&&calls.length===3&&calls.every(c=>c.result?.status==='completed'),threeAdopted:tasks.length===3&&tasks.every(t=>t.adoption==='completed'),
 exactState:hash(world.state)===hash(expected),oneAction:world.events.filter(e=>e.kind==='action').length===1,
 sameCommonAllowance:review.budget.calls===calls.length&&review.budget.limitCalls===3&&review.budget.limitMicroUSD===600000,
 oneAttemptEach:tasks.every(t=>t.stages.length===1&&t.stages[0].attempts.length===1&&!t.fallbackUsed),
 correctParameters:calls.every(c=>c.payload.parameters.max_tokens===(c.slot===1?32768:8192)&&c.payload.parameters.temperature===.7&&c.payload.parameters.top_p===.8),
 noTools:calls.every(c=>c.payload.tools.length===0&&c.payload.tool_choice==='none'),
 parallelPair:!!witness&&!!calls[0]&&Math.max(...calls.filter(c=>c.slot<=2).map(c=>c.sentAt))<Math.min(...calls.filter(c=>c.slot<=2).map(c=>c.completedAt)),
 observedOnly:witness?frame(witness).observations.length===1&&frame(witness).observations[0].quantityUsed===2&&frame(witness).coverage.inventory==='not-authorized':false,
 outsiderIsolated:outsider?frame(outsider).observations.length===0&&frame(outsider).coverage.events==='not-authorized'&&frame(outsider).coverage.inventory==='not-authorized':false,
 credentialsRemoved:cleanup.credentialDirectoryRemoved&&cleanup.localTokenRemoved&&!cleanup.masterKeyWrittenToDisk&&!cleanup.sharedConfigurationChanged};
function exactCost(c){let last=null;for(const l of (c.response?.body?.sse??'').split('\n')){if(!l.startsWith('data:')||l.includes('[DONE]'))continue;try{const x=JSON.parse(l.slice(5));if(typeof x.usage?.cost==='number')last=x.usage.cost;}catch{}}return last;}
const rows=calls.map(c=>({slot:c.slot,reportedUSD:exactCost(c),estimatedUSD:c.result?.accounting?.estimatedCostMicros==null?null:c.result.accounting.estimatedCostMicros/1e6,inputTokens:c.result?.usage?.inputTokens??null,outputTokens:c.result?.usage?.outputTokens??null,reasoningTokens:c.result?.usage?.reasoningTokens??null,firstContentMs:c.result?.timing?.source==='host-progress-poll'?c.result.timing.firstContentMs:null,proseFirstMs:c.result?.timing?.source==='host-progress-poll'?c.result.timing.proseFirstMs:null,completedMs:c.result?.timing?.completedMs??null,timingSource:c.result?.timing?.source??'unknown'}));
const sum=k=>rows.every(r=>r[k]!==null)?rows.reduce((n,r)=>n+r[k],0):null;
const assessment={mode:review.mode,checks,passed:Object.values(checks).filter(Boolean).length,total:Object.keys(checks).length,requests:rows,totalReportedUSD:sum('reportedUSD'),totalEstimatedUSD:sum('estimatedUSD'),tokens:{input:sum('inputTokens'),output:sum('outputTokens'),reasoning:sum('reasoningTokens')},localReceiptPreparationMs:claim.localFeedbackMs,
 knownLimits:['单次成年中性样例，不证明长期人物稳定或不同模型收益','备用与未知恢复只有模拟证据；真实没有工具调用','主机进度采样不是供应商精确 TTFT，缓冲完成不计首字','总运行时间包含本地人工核对等待'],manualReviewFile:'manual-review.json'};
await writeFile(join(dir,'assessment.json'),JSON.stringify(assessment,null,2)+'\n');console.log(JSON.stringify({checks:assessment.passed+'/'+assessment.total,reportedUSD:assessment.totalReportedUSD,tokens:assessment.tokens,requests:rows}));if(Object.values(checks).some(x=>!x))process.exitCode=1;
