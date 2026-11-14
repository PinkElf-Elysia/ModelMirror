import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {hash,need,clone} from '../../../agent-core/contracts.mjs';
import {actionAdapters} from '../../../agent-core/actions/adapters.mjs';import {fileSource} from '../source.mjs';import {DraftStore} from '../store.mjs';import {handoff} from '../draft.mjs';import {build,parse} from '../provider.mjs';
import {ProposalProvider,MODEL} from './retest-provider.mjs';
const [mode,dir,port]=process.argv.slice(2),directory=resolve(dir||'.');
need(['mock','real'].includes(mode)&&directory===resolve('experiments/ai-rpg-engine/.rpg03-work/e4-gift-retest-'+mode+'-v1')&&port===({mock:'18901',real:'18902'}[mode]),'E4_RETEST_CLI');
const token=process.env.E4_SERVICE_TOKEN;delete process.env.E4_SERVICE_TOKEN;delete process.env.RPG_S2S_TOKEN;need(token?.length>=32,'E4_AUTH');
const baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+token},save=(n,v)=>writeFile(join(directory,n),typeof v==='string'?v:JSON.stringify(v,null,2)+'\n',{flag:'wx'});
const priorDir=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-context-real-v1'),replayDir=resolve('experiments/ai-rpg-engine/.rpg03-work/e4-fence-replay-v1');
const text='改成赠送给周禾一份养息丹。他已经答应了。只拟方案，不要送出。';
let ledger,provider,store,source,before,previous,latest,receipt,failure=null;const outputs=[];let originalEvidence=[];
try{
 const read=async(d,n)=>JSON.parse(await readFile(join(d,n),'utf8'));
 const replay=await read(replayDir,'report.json'),seed=await read(replayDir,'draft-record.json'),accepted=await read(replayDir,'accepted-draft.json');
 need(replay.newRealCalls===0&&Object.values(replay.checks).every(Boolean),'E4_REPLAY');
 need(replay.parserHash===hash(await readFile(new URL('../provider.mjs',import.meta.url),'utf8')),'E4_PARSER_CHANGED');
 need(hash(seed.current)===hash(accepted)&&!seed.pending,'E4_SEED');
 for(const [d,n] of [[priorDir,'reply-1.txt'],[priorDir,'source-state.json'],[priorDir,'review.json'],[replayDir,'report.json'],[replayDir,'draft-record.json'],[replayDir,'accepted-draft.json'],[replayDir,'proposed-next-request.json']])originalEvidence.push({path:join(d,n),hash:hash(await readFile(join(d,n),'utf8'))});
 const adapter=(await actionAdapters()).find(x=>x.id==='cultivation');source=fileSource(adapter,join(priorDir,'source-state.json'));const frame=await source.read();before=hash(frame.record);
 need(handoff(adapter,frame,accepted).executionStatus==='not_executed'&&frame.resources.find(x=>x.id==='medicine').quantity===5,'E4_SEED_NOT_EXECUTION');
 need(hash(build(frame,accepted,text).messages)===hash((await read(replayDir,'proposed-next-request.json')).request.messages),'E4_FROZEN_NEXT');
 const quote=await read(directory,'preflight.json'),response=await fetch(baseURL+'/api/rpg/v1/models?profile=core-query-r3',{headers,redirect:'error'});need(response.ok,'E4_CATALOG');
 const selection=(await response.json()).models.find(m=>m.model===MODEL&&m.available);need(selection,'E4_SELECTION');
 const authorization={id:'e4-gift-retest-'+mode+'-20261114',maximumCalls:1,maximumUSD:.20,perCallUSD:.20,expiresAt:new Date(Date.now()+3600000).toISOString(),pricing:{inputUSD:Number(quote.endpoint.pricing.prompt),outputUSD:Number(quote.endpoint.pricing.completion),quoteId:hash(quote.endpoint)}};
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'e4-gift-retest-budget'});provider=new ProposalProvider({records:ledger,url:new URL(baseURL),serviceToken:token,selection,authorization,fetcher:fetch,mode});
 const manifest={};for(const f of ['retest-run.mjs','retest-provider.mjs','retest_protocol.py','retest_bridge.py','retest_launch.py','../provider.mjs','../draft.mjs','../store.mjs','../source.mjs'])manifest[f]=hash(await readFile(new URL(f,import.meta.url),'utf8'));
 await save('execution-claim.json',{mode,model:MODEL,authorization,selection,input:text,manifest,basis:originalEvidence,source:frame.reference,tools:[],toolChoice:'none',retries:0});await save('source-state.json',frame.record);await save('seed.json',{origin:'saved real reply, parsed and intent-accepted offline; not a new model request',draft:accepted});
 const wrapped={calls:0,async generate(request){need(++this.calls===1,'E4_RETEST_ONE');delete request.realCalls;request.provider=provider.kind;request.mode=mode;await save('request-1.json',request);
  console.log(JSON.stringify({starting:1,mode,maximumCalls:1,maximumUSD:.20}));const result=await provider.generate('e4-gift-retest-'+mode+'-v1','gift-1',{messages:request.messages,tools:[],tool_choice:'none'});
  const call=(await provider.read()).data.calls[0],raw=call.response?.body?.raw??result.message?.content??'';await save('reply-1.txt',raw);receipt={result,raw};return {...result,raw,provider:provider.kind};}};
 store=await DraftStore.open(join(directory,'drafts'),new Map([['cultivation',source]]),{provider:wrapped});
 await store.records.put(store.key(frame),null,seed);latest=await store.view('cultivation');previous=clone(latest.draft.current);const began=performance.now();
 latest=await store.command({adapterId:'cultivation',kind:'reply',operationId:'gift-1',expectedHash:latest.recordHash,text});
 const op=latest.draft.operations['gift-1'],current=latest.draft.current;let rawValue=null;try{rawValue=parse(receipt?.raw);}catch{}
 const checks={completed:op.status==='completed'&&receipt?.result.status==='completed'&&receipt.result.finishReason==='stop',rawGift:rawValue?.action==='gift',actionGift:current?.action==='gift',
 item:current?.intent.item?.value==='medicine',quantityOne:current?.intent.quantity?.value===1,target:current?.intent.target?.value==='zhou',previousAccepted:previous.status==='accepted',
 oldAcceptanceInvalidated:current?.confirmation===null,noMedicinePreflight:current?.preflight===null,intentOnly:current?.status==='intent-only',
 claimOnly:current?.intent.condition?.source.kind==='model-interpretation'&&current.intent.condition.value.includes('答应'),
 worldConditionsUnknown:current?.worldConditions.length===2&&current.worldConditions.every(x=>x.status==='unknown'),sourceUnchanged:hash((await source.read()).record)===before};
 const output={slot:1,input:text,previous,current:clone(current),raw:receipt?.raw??'',rawValue,diff:clone(latest.draft.history.at(-1).diff),checks,passed:Object.values(checks).every(Boolean),
  diagnostic:op.error??null,operationStatus:op.status,usage:receipt?.result.usage??null,accounting:receipt?.result.accounting??null,timing:{...receipt?.result.timing,draftAvailableMs:performance.now()-began},executionStatus:'not_executed'};
 outputs.push(output);await save('proposal-1.json',output);console.log(JSON.stringify({finished:1,checks,diagnostic:op.error??null}));need(output.passed,'E4_RETEST_SEMANTIC');
}catch(e){failure={code:e.code||e.message};await save('execution-error.json',failure);console.log('Stopped without retry: '+failure.code);}
finally{
 const data=provider?(await provider.read())?.data??{calls:[],reservedUSD:0}:{calls:[],reservedUSD:0};await save('transport-evidence.json',data);
 for(const c of data.calls)await save('stage-'+c.id+'.json',c);if(store){await save('draft-record.json',(await store.view('cultivation')).draft);await store.close();}
 const after=source?await source.read():null;if(after)await save('source-after.json',after.record);
 originalEvidence=await Promise.all(originalEvidence.map(async e=>({...e,unchanged:hash(await readFile(e.path,'utf8'))===e.hash})));
 await save('review.json',{mode,model:MODEL,outputs,limits:{calls:1,perCallUSD:.20,totalUSD:.20,retries:0},executionStatus:'not_executed',sourceUnchanged:!!after&&hash(after.record)===before,
  originalEvidence,calls:data.calls.map(c=>({id:c.id,reservedUSD:c.reserveUSD,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing})),reservedUSD:data.reservedUSD,failure});
 if(ledger)await ledger.close();const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'E4_STOP');
}
if(mode==='mock')need(outputs.length===1&&outputs[0].passed&&!failure&&originalEvidence.every(e=>e.unchanged),'E4_RETEST_MOCK');
console.log('E4 one-request retest ended; no execution, no retry.');
