// Offline evidence check; no provider, credentials, network or world execution.
import {readFile,writeFile,mkdir} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {hash,need,clone} from '../../../agent-core/contracts.mjs';import {actionAdapters} from '../../../agent-core/actions/adapters.mjs';
import {fileSource} from '../source.mjs';import {CONTRACT,build,parse} from '../provider.mjs';import {revise,handoff} from '../draft.mjs';
const work=resolve('experiments/ai-rpg-engine/.rpg03-work'),prior=join(work,'e4-gift-retest-real-v1'),replay=join(work,'e4-fence-replay-v1'),origin=join(work,'e4-context-real-v1'),out=join(work,'e4-condition-contract-offline-v1');
const paths=[join(prior,'reply-1.txt'),join(prior,'request-1.json'),join(prior,'execution-claim.json'),join(prior,'review.json'),join(prior,'assessment.json'),join(replay,'accepted-draft.json'),join(origin,'source-state.json')];
const saved=await Promise.all(paths.map(async path=>({path,text:await readFile(path,'utf8')})));
const [raw,requestText,claimText,,,acceptedText]=saved.map(x=>x.text),oldRequest=JSON.parse(requestText),claim=JSON.parse(claimText),accepted=JSON.parse(acceptedText);
need(hash(raw)==='5ad21334c9d935c4b2238717d27d1f254b88aac8d657de91a775053431a5a721','E4_RAW_CHANGED');
const adapter=(await actionAdapters()).find(a=>a.id==='cultivation'),source=fileSource(adapter,join(origin,'source-state.json')),frame=await source.read(),before=hash(frame.record),seedBefore=hash(accepted);
need(handoff(adapter,frame,accepted).executionStatus==='not_executed','E4_SEED');
const text=oldRequest.input,request=build(frame,accepted,text),oldSystem=oldRequest.messages[0].content;
const oldContract=oldSystem.slice(oldSystem.indexOf('\n')+1,oldSystem.indexOf('\n\n【材料 e4-facts'));
const sourceText=await readFile(new URL('../provider.mjs',import.meta.url),'utf8');
need(hash(sourceText.replace(CONTRACT,oldContract))===claim.manifest['../provider.mjs'],'E4_ONLY_CONTRACT_CHANGE');
need(request.messages[0].content===oldSystem.replace(oldContract,CONTRACT),'E4_ONLY_SYSTEM_CONTRACT');
need(hash(request.messages.slice(1))===hash(oldRequest.messages.slice(1)),'E4_PLAYER_CONTEXT_CHANGED');
let diagnostic=null;try{revise(adapter,frame,accepted,{...parse(raw),text,turnId:'offline-negative'});}catch(e){diagnostic=e.code;}
need(diagnostic==='INTENT_CONDITION_EVIDENCE','E4_VALIDATOR_LOOSENED');
const fixture={action:'gift',changes:[{field:'target',value:'zhou',quote:'周禾'},{field:'item',value:'medicine',quote:'养息丹'},{field:'quantity',value:1,quote:'一份'},{field:'condition',value:'他已经答应了',quote:'他已经答应了'}],questions:[],unsupported:false};
const d=revise(adapter,frame,accepted,{...parse(JSON.stringify(fixture)),text,turnId:'handwritten-positive-fixture'});
const checks={onlyContractChanged:true,playerInputAndPriorDraftUnchanged:true,savedParaphraseStillRejected:true,
 fixtureGift:d.action==='gift'&&d.intent.item.value==='medicine'&&d.intent.target.value==='zhou'&&d.intent.quantity.value===1,
 fixtureVerbatim:d.intent.condition.value==='他已经答应了'&&d.intent.condition.source.quote==='他已经答应了',
 fixtureIntentOnly:d.status==='intent-only'&&d.preflight===null&&d.confirmation===null,
 worldConditionsUnknown:d.worldConditions.every(x=>x.status==='unknown'),sourceUnchanged:hash((await source.read()).record)===before,
 priorDraftUnchanged:hash(accepted)===seedBefore};need(Object.values(checks).every(Boolean),'E4_CONTRACT_CHECK');
const preservation=await Promise.all(saved.map(async x=>({file:x.path,hash:hash(x.text),unchanged:(await readFile(x.path,'utf8'))===x.text})));need(preservation.every(x=>x.unchanged),'E4_EVIDENCE_CHANGED');
await mkdir(out);const save=(n,v)=>writeFile(join(out,n),JSON.stringify(v,null,2)+'\n',{flag:'wx'});
await save('proposed-request.json',{status:'not-dispatched-awaiting-user-authorization',basis:'same accepted prior draft and user input; only E4 condition contract changed',request});
await save('positive-fixture.json',{origin:'handwritten-offline-fixture-not-model-generated',reply:fixture,draft:clone(d)});
const report={mode:'offline-contract-verification',realCalls:0,costUSD:0,diagnostic,checks,actualQuantity:frame.resources.find(x=>x.id==='medicine').quantity,
 oldContractHash:hash(oldContract),newContractHash:hash(CONTRACT),originalEvidence:preservation,
 note:'旧错误真实回复仍被拒绝；正例是手写样例，不是模型改进证据。不更新原失败记录，不执行或派发。'};
await save('report.json',report);console.log(JSON.stringify(report,null,2));
