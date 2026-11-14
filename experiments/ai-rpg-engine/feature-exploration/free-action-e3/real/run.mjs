import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {hash,need,clone} from '../../../agent-core/contracts.mjs';
import {archiveSource,ARCHIVE_HASH} from '../../free-action-e2/source.mjs';import {createBuilder} from '../context.mjs';import {DraftStore} from '../store.mjs';import {ProposalProvider,MODEL} from './provider.mjs';
const [mode,dir,port]=process.argv.slice(2),directory=resolve(dir||'.');
need(['mock','real'].includes(mode)&&directory===resolve('experiments/ai-rpg-engine/.rpg03-work/e3-clarification-'+mode+(mode==='mock'?'-v2':'-v1'))&&port===({mock:'18882',real:'18883'}[mode]),'E3_CLI');
const token=process.env.E3_SERVICE_TOKEN;delete process.env.E3_SERVICE_TOKEN;delete process.env.RPG_S2S_TOKEN;
need(token?.length>=32,'E3_AUTH');const baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+token};
const save=(name,value)=>writeFile(join(directory,name),typeof value==='string'?value:JSON.stringify(value,null,2)+'\n',{flag:'wx'});
const cases=['周禾，养息丹，两份。他已经答应收了；先拟方案，不要送出。','数量改成一份。不是周禾，送给谁先留空；刚才说他答应了的那条也撤回。'];
let ledger,provider,store,source,before,initial,latest;const outputs=[];let failure=null;
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8'));
 const response=await fetch(baseURL+'/api/rpg/v1/models?profile=core-query-r3',{headers,redirect:'error'});need(response.ok,'E3_CATALOG');
 const selection=(await response.json()).models.find(m=>m.model===MODEL&&m.available);need(selection,'E3_SELECTION');
 const authorization={id:'e3-clarification-'+mode+'-20261114',maximumCalls:2,maximumUSD:.40,perCallUSD:.20,expiresAt:new Date(Date.now()+3600000).toISOString(),
 pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'e3-clarification-budget'});
 provider=new ProposalProvider({records:ledger,url:new URL(baseURL),serviceToken:token,selection,authorization,fetcher:fetch,mode});
 source=archiveSource(resolve('experiments/ai-rpg-engine/.rpg03-work/e3-source/session-1.json'));
 initial=await source.read();before=hash(initial);const build=await createBuilder(),manifest={};
 for(const file of ['run.mjs','provider.mjs','protocol.py','bridge.py','launch.py','../context.mjs','../provider.mjs','../intent.mjs','../store.mjs','../../free-action-e2/source.mjs'])manifest[file]=hash(await readFile(new URL(file,import.meta.url),'utf8'));
 await save('execution-claim.json',{mode,model:MODEL,authorization,selection,cases,manifest,sourceHash:ARCHIVE_HASH,sourceSession:initial.session,stateRevision:initial.version,tools:[],toolChoice:'none',retries:0,acceptance:'test-script intent-only acceptance after correct first reply; never execute'});
 await save('source-state.json',initial);
 const receipts=[];
 const adapted={calls:0,async generate(request){
  const slot=++this.calls;need(slot<=2,'E3_CALL_LIMIT');await save('request-'+slot+'.json',request);console.log(JSON.stringify({starting:slot,mode,capUSD:.20}));
  const result=await provider.generate('e3-clarification-'+mode+'-v1','proposal-'+slot,{messages:request.messages,tools:[],tool_choice:'none'});
  const entry=(await provider.read()).data.calls.find(c=>c.id==='proposal-'+slot),raw=entry?.response?.body?.raw??result.message?.content??'';
  await save('reply-'+slot+'.txt',raw);receipts.push({result,raw});
  return {...result,raw,provider:provider.kind};
 }};
 store=await DraftStore.open(join(directory,'drafts'),{source,builder:(w,p,text)=>{const {realCalls,...request}=build(w,p,text);return {...request,provider:provider.kind,mode};},provider:adapted});
 latest=await store.command({kind:'new',operationId:'seed',expectedHash:null});
 await save('seed.json',{nature:'deterministic-test-initial-condition-not-model-generated',draft:latest.draft.current});
 for(let i=0;i<cases.length;i++){
  const slot=i+1,previous=clone(latest.draft.current),started=performance.now();
  latest=await store.command({kind:'reply',operationId:'proposal-'+slot,expectedHash:latest.recordHash,text:cases[i]});
  const op=latest.draft.operations['proposal-'+slot],r=receipts[i],p=latest.draft.current;
  const sourceUnchanged=hash(await source.read())===before;
  const checks={completed:op.status==='completed'&&r?.result.status==='completed'&&r.result.finishReason==='stop',
   sourceUnchanged,worldConditionsUnknown:p.worldConditions.every(c=>c.status==='unknown'),confirmationCleared:p.confirmation===null};
  if(slot===1)Object.assign(checks,{correctTarget:p.intent.target?.value==='zhou',correctItem:p.intent.item?.value==='medicine',quantityTwo:p.intent.quantity?.value===2,
   playerClaimOnly:p.intent.condition?.source.kind==='model-interpretation'&&p.intent.condition.value.includes('答应'),readyForIntentReview:p.status==='review',
   conditionalOnly:p.expected.length===1&&p.expected[0].before===3&&p.expected[0].conditionalAfter===1});
  else Object.assign(checks,{sameDraft:p.id===previous.id,quantityOne:p.intent.quantity?.value===1,targetCleared:p.intent.target===null,itemRetained:p.intent.item?.value==='medicine',
   claimWithdrawn:p.intent.condition===null,needsTarget:p.status==='clarification'&&p.questions.some(q=>q.field==='target'),noCompletePlan:p.steps.length===0&&p.expected.length===0});
  const output={slot,input:cases[i],previous,current:clone(p),diff:clone(p.diff),raw:r?.raw??'',checks,passed:Object.values(checks).every(Boolean),operationStatus:op.status,diagnostic:op.error??null,
   usage:r?.result.usage??null,accounting:r?.result.accounting??null,timing:{...r?.result.timing,draftAvailableMs:performance.now()-started},executionStatus:'not_executed'};
  outputs.push(output);await save('proposal-'+slot+'.json',output);console.log(JSON.stringify({finished:slot,status:op.status,checks,diagnostic:op.error??null}));
  need(output.passed,'E3_SEMANTIC_CHECK_FAILED');
  if(slot===1){latest=await store.command({kind:'accept',operationId:'accept-intent-only',expectedHash:latest.recordHash});await save('acceptance.json',latest.draft.current.confirmation);}
 }
}catch(e){failure={code:e.code||e.message};await save('execution-error.json',failure);console.log('Stopped without retry: '+failure.code);}
finally{
 const data=provider?(await provider.read())?.data??{calls:[],reservedUSD:0}:{calls:[],reservedUSD:0};
 await save('transport-evidence.json',data);for(const c of data.calls)await save('stage-'+c.id+'.json',c);
 if(store){const v=await store.view();await save('draft-record.json',v.draft);await store.close();}
 const sourceUnchanged=source&&hash(await source.read())===before;
 await save('review.json',{mode,model:MODEL,outputs,limits:{calls:2,perCallUSD:.20,totalUSD:.40,retries:0},executionStatus:'not_executed',sourceUnchanged,
 calls:data.calls.map(c=>({id:c.id,reservedUSD:c.reserveUSD,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing})),reservedUSD:data.reservedUSD,failure});
 if(ledger)await ledger.close();
 const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'E3_STOP');
}
if(mode==='mock')need(outputs.length===2&&outputs.every(o=>o.passed)&&!failure,'E3_MOCK_CHAIN');
console.log('E3 evidence saved; no world writes, retries or automatic follow-up.');
