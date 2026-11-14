import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {hash,need,clone} from '../../../agent-core/contracts.mjs';
import {archiveSource,ARCHIVE_HASH} from '../source.mjs';import {createContextBuilder} from '../context.mjs';import {validateProposal} from '../model.mjs';import {ProposalProvider,MODEL} from './provider.mjs';
const [mode,dir,port]=process.argv.slice(2),directory=resolve(dir||'.');
need(['mock','real'].includes(mode)&&directory===resolve('experiments/ai-rpg-engine/.rpg03-work/e2-proposal-'+mode+'-v1')&&port===({mock:'18863',real:'18864'}[mode]),'E2_CLI');
const token=process.env.E2_SERVICE_TOKEN;delete process.env.E2_SERVICE_TOKEN;delete process.env.RPG_S2S_TOKEN;
need(token?.length>=32,'E2_AUTH');const baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+token};
const save=(name,value)=>writeFile(join(directory,name),typeof value==='string'?value:JSON.stringify(value,null,2)+'\n',{flag:'wx'});
const cases=[
 '如果周禾愿意收，我打算给他两份养息丹，然后再谈今日杂务。现在先拟方案，不要送出或服用，也别动其他物品。',
 '帮我给那人一些丹药，先想想怎么安排，暂时不要动。'];
let ledger,provider;const outputs=[];let failure=null;
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8'));
 const r=await fetch(baseURL+'/api/rpg/v1/models?profile=core-query-r3',{headers,redirect:'error'});need(r.ok,'E2_CATALOG');const selection=(await r.json()).models.find(m=>m.model===MODEL&&m.available);need(selection,'E2_SELECTION');
 const authorization={id:'e2-proposal-'+mode+'-20261114',maximumCalls:2,maximumUSD:.40,perCallUSD:.20,expiresAt:new Date(Date.now()+3600000).toISOString(),
  pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'e2-proposal-real-budget'});
 provider=new ProposalProvider({records:ledger,url:new URL(baseURL),serviceToken:token,selection,authorization,fetcher:fetch,mode});
 const source=archiveSource(resolve('experiments/ai-rpg-engine/.rpg03-work/e2-source/session-1.json')),world=await source.read(),before=hash(world),build=await createContextBuilder();
 const manifest={};for(const name of ['run.mjs','provider.mjs','protocol.py','bridge.py','launch.py','../context.mjs','../model.mjs','../source.mjs'])manifest[name]=hash(await readFile(new URL(name,import.meta.url),'utf8'));
 await save('execution-claim.json',{mode,authorization,selection,cases,manifest,sourceHash:ARCHIVE_HASH,source:world.provenance,sourceSession:world.session,stateRevision:world.version,tools:[],toolChoice:'none',retries:0});
 await save('source-state.json',world);
 for(let i=0;i<cases.length;i++){
  const slot=i+1,request={...build(world,{text:cases[i],selection:[]}),provider:provider.kind,mode,realCalls:undefined};
  await save('request-'+slot+'.json',request);console.log(JSON.stringify({starting:slot,mode,capUSD:.20}));
  const result=await provider.generate('e2-proposal-'+mode+'-v1','proposal-'+slot,{messages:request.messages,tools:[],tool_choice:'none'});
  const budget=(await provider.read()).data,entry=budget.calls.find(c=>c.id==='proposal-'+slot);
  const raw=entry?.response?.body?.raw??result.message?.content??'';
  await save('reply-'+slot+'.txt',raw);
  let proposal=null,diagnostic=null;
  const parseStart=performance.now();
  if(result.status==='completed'&&result.finishReason==='stop'){
   try{proposal=validateProposal(world,request,raw,{id:'real-proposal-'+slot,revision:1});proposal.generator=provider.kind;delete proposal.hash;proposal.hash=hash(proposal);}
   catch(e){diagnostic=e.code||e.message;}
  }else diagnostic=result.error??'E2_INCOMPLETE';
  const unchanged=before===hash(await source.read());need(unchanged,'E2_SOURCE_CHANGED');
  const assessment=slot===1?{kind:'clear-intent',registeredIDs:!!proposal?.refs.some(x=>x.domain==='items'&&x.id==='medicine')&&!!proposal?.refs.some(x=>x.domain==='relations'&&x.id==='zhou'),
   quantityTwo:proposal?.requirements.length===1&&proposal.requirements[0].quantity===2,
   localConditionalAfterOne:proposal?.expected[0]?.after===1,meaningRequiresHumanReview:true}:
   {kind:'ambiguous',clarification:proposal?.status==='clarification',noQuantityChosen:proposal?.requirements.length===0,meaningRequiresHumanReview:true};
  const output={slot,input:cases[i],status:result.status,finishReason:result.finishReason,proposal,diagnostic,assessment,sourceUnchanged:unchanged,sourceState:clone(world.state),usage:result.usage??null,accounting:result.accounting??null,timing:{...result.timing,proposalAvailableMs:proposal?result.timing.completedMs+performance.now()-parseStart:null}};
  outputs.push(output);await save('proposal-'+slot+'.json',output);console.log(JSON.stringify({finished:slot,status:result.status,diagnostic,assessment,sourceUnchanged:unchanged}));
  if(result.status==='unknown')break;
 }
}catch(e){failure={code:e.code||e.message};await save('execution-error.json',failure);throw e;}
finally{
 const data=provider?(await provider.read())?.data??{calls:[],reservedUSD:0}:{calls:[],reservedUSD:0};
 await save('transport-evidence.json',data);
 for(const c of data.calls)await save('stage-'+c.id+'.json',c);
 await save('review.json',{mode,model:MODEL,outputs,limits:{calls:2,perCallUSD:.20,totalUSD:.40,retries:0},calls:data.calls.map(c=>({id:c.id,reservedUSD:c.reserveUSD,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing})),reservedUSD:data.reservedUSD,failure});
 if(ledger)await ledger.close();
 const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'E2_STOP');
}
if(mode==='mock')need(outputs.length===2&&outputs.every(o=>o.status==='completed'&&!o.diagnostic&&o.sourceUnchanged)&&outputs[0].assessment.quantityTwo&&outputs[0].assessment.localConditionalAfterOne&&outputs[1].assessment.clarification,'E2_MOCK_CHAIN');
console.log('E2 evidence saved. No world writes or retries.');
