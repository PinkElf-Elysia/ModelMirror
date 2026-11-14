import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
import {loadAdapter} from './adapter.mjs';import {exampleState} from './example.mjs';
import {assembleOpening} from '../cultivation-card/host.mjs';
import {assembleContext} from './context.mjs';import {simulatedReply} from './simulator.mjs';
import {candidate} from './rules.mjs';import {referencePanels} from './panels.mjs';import {hash} from './definition.mjs';
const size=text=>({characters:[...text].length,utf8Bytes:Buffer.byteLength(text)});
const inputSize=messages=>size(messages.map(m=>m.content).join(''));
export async function measure(){
 const a=await loadAdapter(),initial=exampleState(),first='服用两份养息丹',input='帮周禾扶好竹架';
 const raw1=simulatedReply(first,initial),one=candidate(initial,raw1);
 const record={state:one.state,stateRevision:1,source:{history:[]},config:{profiles:[],mods:[]},
 turns:[{id:'one',input:first,body:one.body,changes:one.changes}],events:[{id:'one',kind:'turn',after:one.state,changes:one.changes}]};
 const prepared=await assembleOpening(first),previousPanel=referencePanels(one.state);
 const original=[prepared[0],{role:'user',content:first},{role:'assistant',content:one.body+'\n'+previousPanel},{role:'user',content:input}];
 const adapted=await assembleContext(record,input);
 const raw2=simulatedReply(input,one.state),two=candidate(one.state,raw2),nextPanel=referencePanels(two.state);
 const sections={
 originalFixedPrompt:size(a.original),adaptedFixedPrompt:size(a.system),
 priorNarrative:size(one.body),priorFullPanel:size(previousPanel),
 stateAndEventInput:size(adapted.messages[0].content.slice(a.system.length)),
 originalTotalInput:inputSize(original),adaptedTotalInput:inputSize(adapted.messages),
 outputNarrative:size(two.body),outputDelta:size(raw2.text.slice(raw2.text.indexOf('<state_delta>'))),
 originalPanelOutput:size(nextPanel),adaptedPanelOutput:size(''),
 originalTotalOutput:size(two.body+'\n'+nextPanel),adaptedTotalOutput:size(raw2.text)};
 return {kind:'offline-fixture-structure-only',baseline:'a6563a9b8e761e3190d0ac09cfd373886802516b',
 sourceHash:a.binding.sourceHash,effectiveHash:a.binding.effectiveHash,fixtureHash:hash({initial,first,input}),
 originalRequestHash:hash(original),adaptedRequestHash:adapted.hash,sections,
 tokens:null,cost:null,quality:null,
 note:'原版请求使用原卡 assembleOpening 系统材料；历史与下一轮面板由同一档案模板生成，属于可复现比较夹具，不是真实模型旧回复。未计供应商封装和隐藏推理；未使用匹配分词器，不能换算费用或质量。'};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))console.log(JSON.stringify(await measure(),null,2));
