import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {hash} from '../../../agent-core/contracts.mjs';
const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e3-clarification-real-v1');
const read=async n=>JSON.parse(await readFile(join(dir,n),'utf8')),r=await read('review.json'),e=await read('transport-evidence.json'),cleanup=await read('cleanup.json');
const costs=e.calls.map(c=>{let cost=null;for(const b of (c.response?.body?.sse??'').split('\n\n')){const s=b.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(!s||s==='[DONE]')continue;const v=JSON.parse(s);if(typeof v.usage?.cost==='number')cost=v.usage.cost;}return {id:c.id,reportedUSD:cost};});
const assessment={model:r.model,realCalls:r.calls.length,retries:0,allChecksPassed:r.outputs.length===2&&r.outputs.every(o=>o.passed),sourceUnchanged:r.sourceUnchanged,executionStatus:r.executionStatus,costs,
 reportedUSD:costs.every(c=>c.reportedUSD!==null)?costs.reduce((s,c)=>s+c.reportedUSD,0):null,
 inputTokens:r.calls.reduce((s,c)=>s+c.usage.inputTokens,0),outputTokens:r.calls.reduce((s,c)=>s+c.usage.outputTokens,0),reasoningTokens:r.calls.reduce((s,c)=>s+c.usage.reasoningTokens,0),
 cleanup,notes:['固定待澄清初始草稿不是模型生成。','两次原始回复的questions均为空；缺失对象问题由宿主生成。','第一轮condition保留了玩家关于答应、仅拟方案和不要送出的原话，不表示世界事实成立。','接受记录仅接受意图；没有执行授权。','样例验证不代表任意连续自由输入稳定；未增加额外样本或调用。'],
 rawHashes:await Promise.all([1,2].map(async i=>({slot:i,hash:hash(await readFile(join(dir,'reply-'+i+'.txt'),'utf8'))})))};
await writeFile(join(dir,'assessment.json'),JSON.stringify(assessment,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(assessment,null,2));
