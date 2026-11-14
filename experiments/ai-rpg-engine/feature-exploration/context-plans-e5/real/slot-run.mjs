import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {hash,need,clone} from '../../../agent-core/contracts.mjs';
import {openSources} from '../../context-actions-e4/source.mjs';import {build,parse} from '../provider.mjs';import {apply,project,save as savePlan,exportPlan} from '../plan.mjs';
import {ProposalProvider,MODEL} from './slot-provider.mjs';
const [mode,dir,port]=process.argv.slice(2),directory=resolve(dir||'.');
need(['mock','real'].includes(mode)&&directory===resolve('experiments/ai-rpg-engine/.rpg03-work/e5-slot-'+mode+'-v1')&&port===({mock:'18923',real:'18924'}[mode]),'E5_CLI');
const token=process.env.E5_SERVICE_TOKEN;delete process.env.E5_SERVICE_TOKEN;delete process.env.RPG_S2S_TOKEN;
need(token?.length>=32,'E5_AUTH');const baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+token};
const save=(name,value)=>writeFile(join(directory,name),typeof value==='string'?value:JSON.stringify(value,null,2)+'\n',{flag:'wx'});
export const cases=['先服用两份养息丹，再给周禾一份养息丹；如果他不收，就不送。'];
let ledger,records,provider,source,initial,before,plan=null;let failure=null;const outputs=[];
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8'));
 const response=await fetch(baseURL+'/api/rpg/v1/models?profile=core-query-r3',{headers,redirect:'error'});need(response.ok,'E5_CATALOG');
 const selection=(await response.json()).models.find(m=>m.model===MODEL&&m.available);need(selection,'E5_SELECTION');
 const authorization={id:'e5-slot-'+mode+'-20261114',maximumCalls:1,maximumUSD:.20,perCallUSD:.20,expiresAt:new Date(Date.now()+3600000).toISOString(),
 pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'e5-real-budget'});records=await SessionRecords.open(join(directory,'plans'),{scope:'e5-real-evidence'});
 provider=new ProposalProvider({records:ledger,url:new URL(baseURL),serviceToken:token,selection,authorization,fetcher:fetch,mode});
 const sources=await openSources(join(directory,'sources'));source=sources.get('cultivation');initial=await source.read();before=hash(initial.record);
 need(initial.resources.find(x=>x.id==='medicine').quantity===5,'E5_INITIAL_COUNT');
 const manifest={};for(const file of ['slot-run.mjs','slot-provider.mjs','slot_protocol.py','slot_bridge.py','slot_launch.py','../provider.mjs','../plan.mjs','../store.mjs'])manifest[file]=hash(await readFile(new URL(file,import.meta.url),'utf8'));
 await save('execution-claim.json',{mode,model:MODEL,authorization,selection,cases,manifest,source:initial.reference,tools:[],toolChoice:'none',retries:0,
  acceptance:'script saves first validated readonly plan only; no world execution or dependency confirmation'});
 await save('source-state.json',initial.record);
 for(let i=0;i<cases.length;i++){
  const slot=i+1,id='plan-'+slot,previous=clone(plan),request=build(initial,plan,cases[i]),began=performance.now();
  await save('request-'+slot+'.json',{...request,mode,provider:provider.kind});
  let row=await records.put(id,null,{status:'pending',previous,requestHash:hash(request),input:cases[i]});
  console.log(JSON.stringify({starting:slot,mode,capUSD:.20}));
  const result=await provider.generate('e5-slot-'+mode+'-v1',id,{messages:request.messages,tools:[],tool_choice:'none'});
  const entry=(await provider.read()).data.calls.find(c=>c.id===id),raw=entry?.response?.body?.raw??result.message?.content??'';
  await save('reply-'+slot+'.txt',raw);
  let parsed=null,candidate=null,diagnostic=null;
  try{need(result.status==='completed'&&result.finishReason==='stop','E5_REPLY_INCOMPLETE');parsed=parse(raw);
   need(hash((await source.read()).record)===before,'E5_SOURCE_CHANGED');
   candidate=apply(source.adapter,initial,plan,parsed,{text:cases[i],turnId:id});
  }catch(e){diagnostic=e.code||e.message;}
  const view=candidate?project(source.adapter,initial,candidate):null;
  const checks={completed:result.status==='completed'&&result.finishReason==='stop',parsed:!!parsed,validPlan:!!candidate,sourceUnchanged:hash((await source.read()).record)===before,
   noWorldExecution:hash((await source.read()).record)===before,confirmationCleared:!!candidate&&candidate.saved===null,noInventedQuestion:parsed?.question===null&&parsed?.limit===null,
   rawTwoAdds:parsed?.ops?.length===2&&parsed.ops.every(o=>o.type==='add'),
   rawFieldsAllowed:!!parsed?.ops?.length&&parsed.ops.every(o=>Array.isArray(o.changes)&&o.changes.every(c=>(o.action==='gift'?['target','item','quantity']:['item','quantity']).includes(c.field)))};
  const get=(s,k)=>s?.atomic.intent[k]?.value;
  const literal=(c,text)=>!!c&&c.value===c.quote&&text.includes(c.value);
  if(slot===1){const [a,b]=view?.steps??[];Object.assign(checks,{twoSteps:view?.steps.length===2,medicine:a?.atomic.action==='use_medicine'&&get(a,'item')==='medicine'&&get(a,'quantity')===2,
   gift:b?.atomic.action==='gift'&&get(b,'item')==='medicine'&&get(b,'quantity')===1&&get(b,'target')==='zhou',
   order:b?.after===a?.id&&a?.after===null,conditionLiteral:literal(b?.condition,cases[i])&&b.condition.kind==='prerequisite'&&b.condition.value==='如果他不收，就不送。',
   onlyFirstPreflight:a?.preflight?.expected?.[0]?.after===3&&b?.preflight===null});}
  const passed=Object.values(checks).every(Boolean);
  // A failed semantic check cannot become the next request's accepted plan.
  if(candidate)plan=candidate;
  const output={slot,input:cases[i],previous,current:clone(plan),view,raw,parsed,checks,passed,diagnostic,
   operationStatus:diagnostic?(result.status==='unknown'?'unknown':'failed'):'completed',usage:result.usage??null,accounting:result.accounting??null,
   timing:{...result.timing,planAvailableMs:candidate?performance.now()-began:null},executionStatus:'not_executed',
   diff:{beforeSteps:previous?.steps??[],afterSteps:plan?.steps??[],beforeSaved:previous?.saved??null,afterSaved:plan?.saved??null}};
  row=await records.put(id,row.hash,{status:output.operationStatus,result:output});outputs.push(output);await save('proposal-'+slot+'.json',output);
  console.log(JSON.stringify({finished:slot,status:output.operationStatus,checks,diagnostic}));need(passed,'E5_SEMANTIC_CHECK_FAILED');
  if(slot===1){plan=savePlan(source.adapter,initial,plan);await save('saved-plan-1.json',exportPlan(source.adapter,initial,plan));}
 }
}catch(e){failure={code:e.code||e.message};await save('execution-error.json',failure);console.log('Stopped without retry: '+failure.code);}
finally{
 const data=provider?(await provider.read())?.data??{calls:[],reservedUSD:0}:{calls:[],reservedUSD:0};
 await save('transport-evidence.json',data);for(const c of data.calls)await save('stage-'+c.id+'.json',c);
 await save('plan-final.json',plan);if(records)await records.close();
 const after=source?await source.read():null,sourceUnchanged=!!after&&hash(after.record)===before;if(after)await save('source-after.json',after.record);
 await save('review.json',{mode,model:MODEL,outputs,limits:{calls:1,perCallUSD:.20,totalUSD:.20,retries:0},executionStatus:'not_executed',sourceUnchanged,
 calls:data.calls.map(c=>({id:c.id,reservedUSD:c.reserveUSD,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing})),reservedUSD:data.reservedUSD,failure});
 if(ledger)await ledger.close();const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'E5_STOP');
}
if(mode==='mock')need(outputs.length===1&&outputs.every(o=>o.passed)&&!failure,'E5_MOCK_CHAIN');
console.log('E5 evidence saved; no execution, retries or automatic follow-up.');
