import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {hash,need,clone} from '../../../agent-core/contracts.mjs';
import {openSources} from '../source.mjs';import {DraftStore} from '../store.mjs';import {ProposalProvider,MODEL} from './provider.mjs';
const [mode,dir,port]=process.argv.slice(2),directory=resolve(dir||'.');
need(['mock','real'].includes(mode)&&directory===resolve('experiments/ai-rpg-engine/.rpg03-work/e4-context-'+mode+'-v1')&&port===({mock:'18887',real:'18888'}[mode]),'E4_CLI');
const token=process.env.E4_SERVICE_TOKEN;delete process.env.E4_SERVICE_TOKEN;delete process.env.RPG_S2S_TOKEN;
need(token?.length>=32,'E4_AUTH');const baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+token};
const save=(name,value)=>writeFile(join(directory,name),typeof value==='string'?value:JSON.stringify(value,null,2)+'\n',{flag:'wx'});
const cases=['我想服用两份养息丹，先拟方案，不要执行。','改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。'];
let ledger,provider,store,source,before,initial,latest;const outputs=[],receipts=[];let failure=null;
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8'));
 const response=await fetch(baseURL+'/api/rpg/v1/models?profile=core-query-r3',{headers,redirect:'error'});need(response.ok,'E4_CATALOG');
 const selection=(await response.json()).models.find(m=>m.model===MODEL&&m.available);need(selection,'E4_SELECTION');
 const authorization={id:'e4-context-'+mode+'-20261114',maximumCalls:2,maximumUSD:.40,perCallUSD:.20,expiresAt:new Date(Date.now()+3600000).toISOString(),
 pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'e4-context-budget'});
 provider=new ProposalProvider({records:ledger,url:new URL(baseURL),serviceToken:token,selection,authorization,fetcher:fetch,mode});
 const sources=await openSources(join(directory,'sources'));source=sources.get('cultivation');initial=await source.read();before=hash(initial.record);
 need(initial.resources.find(x=>x.id==='medicine').quantity===5,'E4_INITIAL_COUNT');
 const manifest={};for(const file of ['run.mjs','provider.mjs','protocol.py','bridge.py','launch.py','../provider.mjs','../draft.mjs','../store.mjs','../source.mjs'])manifest[file]=hash(await readFile(new URL(file,import.meta.url),'utf8'));
 await save('execution-claim.json',{mode,model:MODEL,authorization,selection,cases,manifest,source:initial.reference,tools:[],toolChoice:'none',retries:0,acceptance:'test-script intent-only acceptance after correct first reply; never execute'});
 await save('source-state.json',initial.record);
 const adapted={calls:0,async generate(request){
  const slot=++this.calls;need(slot<=2,'E4_CALL_LIMIT');
  const {realCalls,...frozen}=request;await save('request-'+slot+'.json',{...frozen,provider:provider.kind,mode});console.log(JSON.stringify({starting:slot,mode,capUSD:.20}));
  const result=await provider.generate('e4-context-'+mode+'-v1','proposal-'+slot,{messages:request.messages,tools:[],tool_choice:'none'});
  const entry=(await provider.read()).data.calls.find(c=>c.id==='proposal-'+slot),raw=entry?.response?.body?.raw??result.message?.content??'';
  await save('reply-'+slot+'.txt',raw);receipts.push({result,raw});return {...result,raw,provider:provider.kind};
 }};
 store=await DraftStore.open(join(directory,'drafts'),sources,{provider:adapted});latest=await store.view('cultivation');
 for(let i=0;i<cases.length;i++){
  const slot=i+1,previous=clone(latest.draft?.current??null),started=performance.now();
  latest=await store.command({adapterId:'cultivation',kind:'reply',operationId:'proposal-'+slot,expectedHash:latest.recordHash,text:cases[i]});
  const op=latest.draft.operations['proposal-'+slot],r=receipts[i],p=latest.draft.current;
  const sourceUnchanged=hash((await source.read()).record)===before;
  let rawValue=null;try{rawValue=JSON.parse(r?.raw);}catch{}
  const checks={completed:op.status==='completed'&&r?.result.status==='completed'&&r.result.finishReason==='stop',sourceUnchanged,confirmationCleared:p?.confirmation===null};
  if(slot===1)Object.assign(checks,{rawAction:rawValue?.action==='use_medicine',correctAction:p?.action==='use_medicine',correctItem:p?.intent.item?.value==='medicine',quantityTwo:p?.intent.quantity?.value===2,
   rulePreflight:p?.status==='rule-ready'&&p.preflight?.expected[0]?.before===5&&p.preflight?.expected[0]?.after===3});
  else Object.assign(checks,{rawAction:rawValue?.action==='gift',previousAccepted:previous?.status==='accepted',actionChanged:p?.action==='gift',quantityOne:p?.intent.quantity?.value===1,
   correctTarget:p?.intent.target?.value==='zhou',correctItem:p?.intent.item?.value==='medicine',intentOnly:p?.status==='intent-only',noConsumptionPreflight:p?.preflight===null,
   playerClaimOnly:p?.intent.condition?.source.kind==='model-interpretation'&&p.intent.condition.value.includes('答应'),
   worldConditionsUnknown:p?.worldConditions.length===2&&p.worldConditions.every(c=>c.status==='unknown')});
  const output={slot,input:cases[i],previous,current:clone(p),diff:clone(latest.draft.history.at(-1).diff),raw:r?.raw??'',rawValue,checks,passed:Object.values(checks).every(Boolean),operationStatus:op.status,diagnostic:op.error??null,
   usage:r?.result.usage??null,accounting:r?.result.accounting??null,timing:{...r?.result.timing,draftAvailableMs:performance.now()-started},executionStatus:'not_executed'};
  outputs.push(output);await save('proposal-'+slot+'.json',output);console.log(JSON.stringify({finished:slot,status:op.status,checks,diagnostic:op.error??null}));
  need(output.passed,'E4_SEMANTIC_CHECK_FAILED');
  if(slot===1){latest=await store.command({adapterId:'cultivation',kind:'accept',operationId:'accept-intent-only',expectedHash:latest.recordHash});await save('acceptance.json',latest.draft.current.confirmation);}
 }
}catch(e){failure={code:e.code||e.message};await save('execution-error.json',failure);console.log('Stopped without retry: '+failure.code);}
finally{
 const data=provider?(await provider.read())?.data??{calls:[],reservedUSD:0}:{calls:[],reservedUSD:0};
 await save('transport-evidence.json',data);for(const c of data.calls)await save('stage-'+c.id+'.json',c);
 if(store){const v=await store.view('cultivation');await save('draft-record.json',v.draft);await store.close();}
 const after=source?await source.read():null,sourceUnchanged=!!after&&hash(after.record)===before;
 if(after)await save('source-after.json',after.record);
 await save('review.json',{mode,model:MODEL,outputs,limits:{calls:2,perCallUSD:.20,totalUSD:.40,retries:0},executionStatus:'not_executed',sourceUnchanged,
 calls:data.calls.map(c=>({id:c.id,reservedUSD:c.reserveUSD,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing})),reservedUSD:data.reservedUSD,failure});
 if(ledger)await ledger.close();
 const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'E4_STOP');
}
if(mode==='mock')need(outputs.length===2&&outputs.every(o=>o.passed)&&!failure,'E4_MOCK_CHAIN');
console.log('E4 evidence saved; no world writes, retries or automatic follow-up.');
