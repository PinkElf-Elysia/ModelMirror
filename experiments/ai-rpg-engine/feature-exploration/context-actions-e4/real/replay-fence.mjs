// Offline-only replay: reads a saved response; no transport, credentials or fetch imports.
import {readFile,writeFile,mkdir} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {hash,need} from '../../../agent-core/contracts.mjs';import {actionAdapters} from '../../../agent-core/actions/adapters.mjs';
import {fileSource} from '../source.mjs';import {DraftStore} from '../store.mjs';import {build} from '../provider.mjs';
const origin=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-context-real-v1'),out=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-fence-replay-v1');
const names=['reply-1.txt','source-state.json','source-after.json','request-1.json','proposal-1.json','review.json','transport-evidence.json','draft-record.json','assessment.json','cleanup.json'];
const saved=Object.fromEntries(await Promise.all(names.map(async n=>[n,await readFile(join(origin,n),'utf8')])));
need(hash(saved['reply-1.txt'])==='b8e07ca3b31ec712ecbd1ba352f78c5e541b4701a896981121075bf8510edfad','E4_FROZEN_REPLY');
const originalReview=JSON.parse(saved['review.json']);need(originalReview.calls.length===1&&originalReview.failure&&originalReview.outputs[0].diagnostic==='E4_REPLY_JSON','E4_FROZEN_FAILURE');
await mkdir(out);const save=(n,v)=>writeFile(join(out,n),JSON.stringify(v,null,2)+'\n',{flag:'wx'});
const adapter=(await actionAdapters()).find(a=>a.id==='cultivation'),source=fileSource(adapter,join(origin,'source-state.json')),sources=new Map([['cultivation',source]]);
const initial=await source.read(),sourceHash=hash(initial.record),request=JSON.parse(saved['request-1.json']);
need(hash(build(initial,null,originalReview.outputs[0].input).messages)===hash(request.messages),'E4_FROZEN_CONTEXT');
const provider={calls:0,async generate(q){need(++this.calls===1&&q.input===originalReview.outputs[0].input,'E4_REPLAY_ONCE');
 return {status:'completed',raw:saved['reply-1.txt'],provider:'saved-response-offline-replay',originalReplyHash:hash(saved['reply-1.txt']),newRealCalls:0};}};
const store=await DraftStore.open(join(out,'drafts'),sources,{provider});
try{
 let v=await store.command({adapterId:'cultivation',kind:'reply',operationId:'replay-saved-response',expectedHash:null,text:originalReview.outputs[0].input});
 const current=v.draft.current,op=v.draft.operations['replay-saved-response'];
 const checks={completed:op.status==='completed',action:current?.action==='use_medicine',item:current?.intent.item?.value==='medicine',quantity:current?.intent.quantity?.value===2,
  preflightOnly:current?.status==='rule-ready'&&current.preflight.expected[0].before===5&&current.preflight.expected[0].after===3,
  originalReceiptPreserved:op.receipt.raw===saved['reply-1.txt'],sourceUnchanged:hash((await source.read()).record)===sourceHash};
 need(Object.values(checks).every(Boolean),'E4_REPLAY_FAILED');
 await save('parsed-draft.json',current);await save('preflight.json',current.preflight);
 v=await store.command({adapterId:'cultivation',kind:'accept',operationId:'offline-accept-intent',expectedHash:v.recordHash});
 need(v.draft.current.status==='accepted'&&v.draft.current.confirmation.meaning==='accept-intent-only','E4_REPLAY_ACCEPT');
 await save('accepted-draft.json',v.draft.current);
 const text='改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。';
 await save('proposed-next-request.json',{status:'not-dispatched-awaiting-authorization',basis:'offline replay of preserved real response, then intent-only acceptance',request:build(initial,v.draft.current,text)});
 await store.close();
 const reopened=await DraftStore.open(join(out,'drafts'),sources,{provider});try{const restored=await reopened.view('cultivation');
  need(restored.draft.operations['replay-saved-response'].receipt.raw===saved['reply-1.txt'],'E4_REPLAY_PERSISTED');
  await save('draft-record.json',restored.draft);
 }finally{await reopened.close();}
 const preserved=await Promise.all(names.map(async n=>({file:n,hash:hash(await readFile(join(origin,n),'utf8')),unchanged:(await readFile(join(origin,n),'utf8'))===saved[n]})));
 need(preserved.every(x=>x.unchanged),'E4_ORIGINAL_EVIDENCE_CHANGED');need(provider.calls===1,'E4_REPLAY_COUNT');
 const report={mode:'offline-saved-response-replay',newRealCalls:0,newCostUSD:0,worldExecution:'not_executed',checks,
  actualQuantity:(await source.read()).resources.find(x=>x.id==='medicine').quantity,
  originalEvidence:preserved,sourceReference:initial.reference,parserHash:hash(await readFile(new URL('../provider.mjs',import.meta.url),'utf8')),
  note:'新解析器仅在独立草稿中离线回放既有真实回复。原实测失败记录未修改。接受只接受意图；下一条请求仅冻结，未调用。'};
 await save('report.json',report);console.log(JSON.stringify(report,null,2));
}finally{await store.close();}
