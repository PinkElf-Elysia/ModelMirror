import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {need,hash} from '../../../agent-core/contracts.mjs';
const mode=process.argv[2];need(['mock','real'].includes(mode),'MODE');const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e6-suggestions-'+mode+'-v1');
const read=async n=>JSON.parse(await readFile(join(dir,n),'utf8')),r=await read('review.json'),e=await read('transport-evidence.json'),cleanup=await read('cleanup.json'),dispatch=await read('dispatch-audit.json');
const costs=e.calls.map(c=>{let reportedUSD=null;for(const block of (c.response?.body?.sse??'').split('\n\n')){const s=block.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(!s||s==='[DONE]')continue;const v=JSON.parse(s);if(typeof v.usage?.cost==='number')reportedUSD=v.usage.cost;}return {id:c.id,reportedUSD,estimatedUSD:c.result?.accounting?.estimatedCostMicros==null?null:c.result.accounting.estimatedCostMicros/1e6,reservedUSD:c.reserveUSD};});
const sum=k=>r.calls.length&&r.calls.every(c=>typeof c.usage?.[k]==='number')?r.calls.reduce((s,c)=>s+c.usage[k],0):null;
const reportedUSD=costs.length&&costs.every(c=>c.reportedUSD!==null)?costs.reduce((s,c)=>s+c.reportedUSD,0):null;
const a={mode,model:r.model,realCalls:mode==='real'?dispatch.length:0,mockCalls:mode==='mock'?dispatch.length:0,retries:0,allChecksPassed:r.outputs.length===2&&r.outputs.every(o=>o.passed)&&!r.failure,
 sourceUnchanged:r.sourceUnchanged,costs,reportedUSD,inputTokens:sum('inputTokens'),outputTokens:sum('outputTokens'),reasoningTokens:sum('reasoningTokens'),cleanup,
 withinReportedCaps:reportedUSD===null?null:reportedUSD<=.40&&costs.every(c=>c.reportedUSD<=.20),
 rawHashes:await Promise.all(r.outputs.map(async o=>({slot:o.slot,hash:hash(await readFile(join(dir,'reply-'+o.slot+'.txt'),'utf8'))}))),
 notes:['两个独立请求；第二个不继承第一个的对象或建议。','自动检查不代替对建议质量的人工阅读。','首次文本包含JSON，不等于叙事正文首字。','选择只保存玩家意图，不执行，也不调用模型。','推理包含于输出；未知费用不当作零。']};
await writeFile(join(dir,'assessment.json'),JSON.stringify(a,null,2));console.log(JSON.stringify(a,null,2));
