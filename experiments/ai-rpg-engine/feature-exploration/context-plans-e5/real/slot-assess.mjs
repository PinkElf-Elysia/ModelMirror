import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {hash,need} from '../../../agent-core/contracts.mjs';
const mode=process.argv[2];need(['mock','real'].includes(mode),'E5_MODE');const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e5-slot-'+mode+'-v1');
const read=async n=>JSON.parse(await readFile(join(dir,n),'utf8')),r=await read('review.json'),e=await read('transport-evidence.json'),cleanup=await read('cleanup.json'),dispatch=await read('dispatch-audit.json');
const costs=e.calls.map(c=>{let reportedUSD=null;for(const block of (c.response?.body?.sse??'').split('\n\n')){const s=block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(!s||s==='[DONE]')continue;const v=JSON.parse(s);if(typeof v.usage?.cost==='number')reportedUSD=v.usage.cost;}return {id:c.id,reportedUSD,accounting:c.result?.accounting??null,reservedUSD:c.reserveUSD};});
const sum=k=>r.calls.length&&r.calls.every(c=>typeof c.usage?.[k]==='number')?r.calls.reduce((s,c)=>s+c.usage[k],0):null;
const allReported=costs.length>0&&costs.every(c=>typeof c.reportedUSD==='number');
const assessment={mode,model:r.model,realCalls:mode==='real'?dispatch.length:0,mockCalls:mode==='mock'?dispatch.length:0,retries:0,
 allChecksPassed:r.outputs.length===1&&r.outputs.every(o=>o.passed)&&r.failure===null,sourceUnchanged:r.sourceUnchanged,executionStatus:r.executionStatus,
 costs,reportedUSD:allReported?costs.reduce((s,c)=>s+c.reportedUSD,0):null,reservedUSD:r.reservedUSD,
 withinReportedCaps:allReported?costs.every(c=>c.reportedUSD<=.20)&&costs.reduce((s,c)=>s+c.reportedUSD,0)<=.20:null,
 inputTokens:sum('inputTokens'),outputTokens:sum('outputTokens'),reasoningTokens:sum('reasoningTokens'),cleanup,dispatch,
 sourceHashes:{before:hash(await read('source-state.json')),after:hash(await read('source-after.json'))},
 rawHashes:await Promise.all(r.outputs.map(async o=>({slot:o.slot,hash:hash(await readFile(join(dir,'reply-'+o.slot+'.txt'),'utf8'))}))),
 notes:['初始来源为独立成年样例快照，非共享存档；实际丹药始终5。','5→3为只读单步预检，本次是否通过以outputs为准；实际库存始终5。','本轮仅授权一个条件边界请求，不含连续修订或删除依赖的真实验证。','玩家称对方答应保留为未核实陈述，不提升为世界事实。','首次文本是宿主观测；JSON计划没有叙事正文首字。','推理token包含在输出内，不重复计费；未知费用不记为0。','样例上限一条；实际派发数单列，不能证明任意输入稳定。']};
await writeFile(join(dir,'assessment.json'),JSON.stringify(assessment,null,2)+'\n');console.log(JSON.stringify(assessment,null,2));
