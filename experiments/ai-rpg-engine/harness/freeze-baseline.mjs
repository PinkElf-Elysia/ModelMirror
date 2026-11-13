import{readFile,writeFile,mkdir}from'node:fs/promises';import{fileURLToPath}from'node:url';import{resolve,join}from'node:path';import{execFileSync}from'node:child_process';
import{rules,binding,adaptationReport,assembleNarration}from'./earth-adapter.mjs';import{initialAssets,purchaseResult}from'./shop-rules.mjs';import{assemble,loadFrozen}from'../card-replica/lib/assembly.mjs';import{hash}from'./contracts.mjs';
const root=fileURLToPath(new URL('../',import.meta.url)),repo=resolve(root,'../..'),docs=join(repo,'docs/ai-rpg-experiment/harness'),work=join(root,'.rpg04-work/harness-baseline');
const revision=execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim();
if(revision!=='d9dd7094bfbab9db3402e308b35c6da09c228de9')throw Error('BASELINE_REVISION_CHANGED_REVIEW_REQUIRED');
await mkdir(work,{recursive:true});
const cases=[
 {id:'success',input:'购买两个面包',assets:initialAssets(rules),action:{productId:'bread',quantity:2}},
 {id:'insufficient-balance',input:'购买一个笔记本',assets:initialAssets(rules,500),action:{productId:'notebook',quantity:1}},
 {id:'insufficient-stock',input:'购买三个面包',assets:initialAssets(rules),action:{productId:'bread',quantity:3}},
 {id:'ambiguous',input:'给朋友带些面包吧',assets:initialAssets(rules),action:null},
 {id:'continuation',input:'问店主附近有什么适合散步的地方',assets:{balance:8800,inventory:{water:0,bread:2,notebook:0},stock:{water:5,bread:0,notebook:1}},action:null}
];
const samples=[];
for(const c of cases){
 const history=c.id==='continuation'?[{role:'user',content:'购买两个面包'},{role:'assistant',content:'店主把两个面包装进纸袋交给你，银行卡余额88元。'}]:[];
 const opening=rules.opening.replace('银行卡余额：100元','银行卡余额：'+(c.id==='continuation'?'100':String(c.assets.balance/100))+'元')+'\n街角杂货店固定价：饮用水3元（库存5）、面包6元（库存2）、笔记本12元（库存1）；只用银行卡，不赊账，不超卖。';
 const session={world:'表世界',characterText:opening,history,enabledRuleIds:[]};
 const result=c.action?purchaseResult(c.assets,rules,c.action):{outcome:c.id==='ambiguous'?'clarify':'story',assets:c.assets};
 const e={id:'baseline-'+c.id,input:c.input,...(c.action||{}),...result};
 const a=assemble({...session,input:c.input}),b=assembleNarration({session,event:e,state:{...result.assets,binding,revision:1}});
 for(const [variant,data] of [['A',a],['B',b]]){
  const bytes=JSON.stringify(data.messages),file=c.id+'-'+variant+'.json';
  await writeFile(join(work,file),JSON.stringify({case:c.id,variant,messages:data.messages},null,2));
  samples.push({case:c.id,variant,openingHash:hash(opening),preActionAssets:c.assets,semanticAction:c.input,requestHash:hash(data.messages),characters:[...bytes].length,utf8Bytes:Buffer.byteLength(bytes),inputTokens:null,outputTokens:null,cost:null,firstFeedbackMs:null,firstTextMs:null,completedMs:null,providerCalls:0});
 }
}
const frozen={format:'modelmirror.rpg.harness-baseline/1',baseCommit:revision,binding,openingHash:hash(rules.opening),rulesHash:hash(rules),sourceHash:hash(loadFrozen()),conditions:{plugins:'off for primary A/B; separate matched-on cohort required',seed:null,parameters:'freeze at provider authorization',realCallsAuthorized:0},samples,
 interpretation:'Characters and bytes are offline request sizes, NOT tokens or provider prices. B adds an event contract; a reduction has not been demonstrated. Failed direct purchases dispatch zero narration calls; B text samples for failures are context audit fixtures, not planned dispatches.'};
await writeFile(join(docs,'baseline.json'),JSON.stringify(frozen,null,2)+'\n');
await writeFile(join(docs,'adaptation.json'),JSON.stringify(adaptationReport(),null,2)+'\n');
const requests=[];
for(let repeat=1;repeat<=3;repeat++)for(const c of cases){
 requests.push({id:'A-'+c.id+'-'+repeat,variant:'A',case:c.id,repeat,stage:'narration',inputFixture:c.id+'-A.json',status:'NOT_AUTHORIZED'});
 if(['success','continuation'].includes(c.id))requests.push({id:'B-'+c.id+'-'+repeat,variant:'B',case:c.id,repeat,stage:'narration',inputFixture:c.id+'-B.json',status:'NOT_AUTHORIZED'});
 if(c.id==='ambiguous')requests.push({id:'B-'+c.id+'-'+repeat,variant:'B',case:c.id,repeat,stage:'intent',expected:'clarification only; no quantity inference, no settlement',status:'NOT_AUTHORIZED'});
}
const manifest={format:'modelmirror.rpg.harness-real-call-plan/1',status:'NOT_AUTHORIZED',authorizedCalls:0,authorizedCostMicros:0,proposedRequests:requests,proposedBaseRequestCount:requests.length,model:null,selectionRevision:null,parameters:null,
 primaryCohort:{variants:['A','B'],cases:cases.map(c=>c.id),repetitions:3,note:'Proposed repeats only. A: one story request per case; B: explicit legal click one narration, deterministic rejection zero, ambiguous parse then clarification or narration as applicable. Freeze actual branch and ceiling before authorization.'},
 extensionCohorts:[{id:'C',status:'conditional',purpose:'ambiguous input, same model parse + narration'},{id:'D',status:'conditional',purpose:'same task, fast parser + narrator'},{id:'memory-on',status:'conditional',purpose:'matched summary + memory policies in A and B; count all calls'}],
 totalCallCeiling:null,totalCostCeilingMicros:null,unknownResults:'lookup original receipt, never auto replay',quality:['有效情节','人物互动','连贯性','后续行动','面板重复','数值一致性'],ledger:['stage','model','parameters','requestHash','attempt','usageReported','usageUnknown','costReported','costEstimated','costReserved','firstFeedback','firstText','completed','backgroundCompleted'],humanAcceptance:'pending',executionGate:'Each actual frozen stage request must be captured from the assembled host; audit B ambiguity/rejection fixtures are not dispatch requests. Controlled transport usage export and streaming timing remain to be wired before real measurement. No automatic retries or memory tasks are included in the proposed 24-request primary cohort; any expansion needs a revised authorized manifest.'};
let existingPlan;try{existingPlan=JSON.parse(await readFile(join(docs,'real-call-plan.json'),'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
if(!existingPlan)await writeFile(join(docs,'real-call-plan.json'),JSON.stringify(manifest,null,2)+'\n');
// Detailed proposals and approvals have their own lifecycle; baseline refresh never replaces them.
console.log(JSON.stringify({base:revision,samples:samples.length,realCalls:0,files:['baseline.json','adaptation.json','real-call-plan.json'],rawRequests:work}));
