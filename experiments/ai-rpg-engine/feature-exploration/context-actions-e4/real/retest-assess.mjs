import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {hash,need} from '../../../agent-core/contracts.mjs';
const mode=process.argv[2];need(['mock','real'].includes(mode),'E4_MODE');const dir=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-gift-retest-'+mode+'-v1');
const read=async n=>JSON.parse(await readFile(join(dir,n),'utf8')),r=await read('review.json'),e=await read('transport-evidence.json'),cleanup=await read('cleanup.json');
const costs=e.calls.map(c=>{let cost=null;for(const b of (c.response?.body?.sse??'').split('\n\n')){const s=b.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trim()).join('\n');if(!s||s==='[DONE]')continue;const v=JSON.parse(s);if(typeof v.usage?.cost==='number')cost=v.usage.cost;}return {id:c.id,reportedUSD:cost,accounting:c.result?.accounting??null};});
const formatDiagnostics=r.outputs.map(o=>{const fenced=o.raw.match(/^```(?:json)?\s*\n([\s\S]*?)\n```\s*$/);let inner=null;
 if(fenced){try{inner=JSON.parse(fenced[1]);}catch{}}
 return {slot:o.slot,rawAccepted:o.operationStatus==='completed',error:o.diagnostic,markdownFence:!!fenced,
  innerJSONReadable:!!inner,innerAction:inner?.action??null,innerChanges:inner?.changes??null,
  meaning:'离线诊断展示，不回填草稿、不接受、不结算、不作为下一次请求输入。'};});
const sum=k=>r.calls.every(c=>typeof c.usage?.[k]==='number')?r.calls.reduce((s,c)=>s+c.usage[k],0):null;
const assessment={mode,model:r.model,realCalls:mode==='real'?r.calls.length:0,mockCalls:mode==='mock'?r.calls.length:0,retries:0,
 allChecksPassed:r.outputs.length===1&&r.outputs.every(o=>o.passed),sourceUnchanged:r.sourceUnchanged,executionStatus:r.executionStatus,originalEvidenceUnchanged:r.originalEvidence.every(e=>e.unchanged),costs,
 reportedUSD:costs.every(c=>c.reportedUSD!==null)?costs.reduce((s,c)=>s+c.reportedUSD,0):null,
 inputTokens:sum('inputTokens'),outputTokens:sum('outputTokens'),reasoningTokens:sum('reasoningTokens'),cleanup,formatDiagnostics,
 notes:['只新增一次赠送修订请求；服药提案来自已保存真实回复的离线回放。','修订仅改变独立草稿，不是执行授权。','赠送没有执行规则；条件估算不是服药预检或账本变更。','玩家关于答应的陈述不证明世界中的同意事实。','全部阶段和源记录以保存证据为准；不自动修补模型回复。','仅验证此连续样例，不代表任意表达均稳定。'],
 rawHashes:await Promise.all(r.outputs.map(async o=>({slot:o.slot,hash:hash(await readFile(join(dir,'reply-'+o.slot+'.txt'),'utf8'))})))};
await writeFile(join(dir,'assessment.json'),JSON.stringify(assessment,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(assessment,null,2));
