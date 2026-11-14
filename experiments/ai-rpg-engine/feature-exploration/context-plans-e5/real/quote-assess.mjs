import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {hash,need} from '../../../agent-core/contracts.mjs';
const mode=process.argv[2];need(['mock','real'].includes(mode),'E5_MODE');const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e5-quote-'+mode+'-v1');
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
 notes:['复用已验收两步计划与原状态快照；旧材料未改写。','本次仅一个连续修订请求；撤回服药后旧预检不得沿用。','玩家陈述不得更新世界状态；本次是否采用到计划看allChecksPassed，实际丹药仍5。','仅合法采用的修订才清除旧保存确认；失败整批不采用，原计划与保存保持。','推理token包含于输出；未知费用不记为0。','模拟结果与真实结果分列，不代表任意多轮输入均稳定。']};
await writeFile(join(dir,'assessment.json'),JSON.stringify(assessment,null,2)+'\n');console.log(JSON.stringify(assessment,null,2));
