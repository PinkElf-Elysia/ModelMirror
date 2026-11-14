import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {hash,need} from '../../../agent-core/contracts.mjs';
const mode=process.argv[2];need(['mock','real'].includes(mode),'E4_MODE');const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-context-'+mode+'-v1');
const read=async n=>JSON.parse(await readFile(join(dir,n),'utf8')),r=await read('review.json'),e=await read('transport-evidence.json'),cleanup=await read('cleanup.json');
const costs=e.calls.map(c=>{let cost=null;for(const b of (c.response?.body?.sse??'').split('\n\n')){const s=b.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(!s||s==='[DONE]')continue;const v=JSON.parse(s);if(typeof v.usage?.cost==='number')cost=v.usage.cost;}return {id:c.id,reportedUSD:cost,accounting:c.result?.accounting??null};});
const formatDiagnostics=r.outputs.map(o=>{const fenced=o.raw.match(/^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/);let inner=null;
 if(fenced){try{inner=JSON.parse(fenced[1]);}catch{}}
 return {slot:o.slot,rawAccepted:o.operationStatus==='completed',error:o.diagnostic,markdownFence:!!fenced,
  innerJSONReadable:!!inner,innerAction:inner?.action??null,innerChanges:inner?.changes??null,
  meaning:'离线诊断展示，不回填草稿、不接受、不结算、不作为下一次请求输入。'};});
const sum=k=>r.calls.every(c=>typeof c.usage?.[k]==='number')?r.calls.reduce((s,c)=>s+c.usage[k],0):null;
const assessment={mode,model:r.model,realCalls:mode==='real'?r.calls.length:0,mockCalls:mode==='mock'?r.calls.length:0,retries:0,
 allChecksPassed:r.outputs.length===2&&r.outputs.every(o=>o.passed),sourceUnchanged:r.sourceUnchanged,executionStatus:r.executionStatus,costs,
 reportedUSD:costs.every(c=>c.reportedUSD!==null)?costs.reduce((s,c)=>s+c.reportedUSD,0):null,
 inputTokens:sum('inputTokens'),outputTokens:sum('outputTokens'),reasoningTokens:sum('reasoningTokens'),cleanup,formatDiagnostics,
 notes:['初始 R5 成年样例快照为本任务新建，非共享存档。','5→3预检在完整模拟链路通过；真实请求是否形成预检以逐请求记录为准。实际库存始终5。','第二次仅在第一版校验通过后派发；未派发不能算赠送边界通过。','玩家称对方答应不等于已核实世界事实。','第一版仅在校验通过后接受意图；本次是否发生接受以记录为准。','原始JSON与本地校验分别展示；不自动修补模型输出。','单个连续样例不能代表任意自由输入稳定。'],
 rawHashes:await Promise.all(r.outputs.map(async o=>({slot:o.slot,hash:hash(await readFile(join(dir,'reply-'+o.slot+'.txt'),'utf8'))})))};
await writeFile(join(dir,'assessment.json'),JSON.stringify(assessment,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(assessment,null,2));
