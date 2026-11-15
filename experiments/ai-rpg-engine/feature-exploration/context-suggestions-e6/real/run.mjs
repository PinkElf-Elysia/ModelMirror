import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {hash,need} from '../../../agent-core/contracts.mjs';
import {openSource} from '../source.mjs';import {build,parse,fixture,INPUTS} from './contract.mjs';import {ProposalProvider,MODEL} from './provider.mjs';
const [mode,dir,port]=process.argv.slice(2),directory=resolve(dir||'.');
need(['mock','real'].includes(mode)&&directory===resolve('experiments/ai-rpg-engine/.rpg03-work/e6-suggestions-'+mode+'-v1')&&port===({mock:'18935',real:'18936'}[mode]),'E6_CLI');
const token=process.env.E6_SERVICE_TOKEN;delete process.env.E6_SERVICE_TOKEN;delete process.env.RPG_S2S_TOKEN;
need(token?.length>=32,'E6_AUTH');const baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+token};
const save=(n,v)=>writeFile(join(directory,n),typeof v==='string'?v:JSON.stringify(v,null,2)+'\n',{flag:'wx'});
let ledger,provider,host,before,frame,failure=null;const outputs=[];
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8'));
 const response=await fetch(baseURL+'/api/rpg/v1/models?profile=core-query-r3',{headers,redirect:'error'});need(response.ok,'E6_CATALOG');
 const selection=(await response.json()).models.find(m=>m.model===MODEL&&m.available);need(selection,'E6_SELECTION');
 const authorization={id:'e6-suggestions-'+mode+'-v1',maximumCalls:2,maximumUSD:.40,perCallUSD:.20,expiresAt:new Date(Date.now()+3600000).toISOString(),
 pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'e6-real-budget'});
 provider=new ProposalProvider({records:ledger,url:new URL(baseURL),serviceToken:token,selection,authorization,fetcher:fetch,mode});
 host=await openSource(join(directory,'source'));frame=await host.reader.read();before=await host.audit();
 await save('source-before.json',{frame,audit:before});
 const manifest={};for(const file of ['run.mjs','provider.mjs','protocol.py','bridge.py','launch.py','contract.mjs','../source.mjs'])manifest[file]=hash(await readFile(new URL(file,import.meta.url),'utf8'));
 await save('execution-claim.json',{mode,model:MODEL,authorization,selection,cases:INPUTS,manifest,tools:[],retries:0,executionStatus:'not_executed'});
 if(mode==='mock')for(let i=0;i<2;i++)await save('mock-reply-'+(i+1)+'.json',fixture(i));
 for(let i=0;i<2;i++){
  const id='suggestion-'+(i+1),request=build(frame,i);await save('request-'+(i+1)+'.json',request);
  console.log(JSON.stringify({starting:id,mode,perCallUSD:.20}));
  const result=await provider.generate('e6-suggestions-'+mode+'-v1',id,{messages:request.messages,tools:[],tool_choice:'none'});
  const entry=(await provider.read()).data.calls.find(c=>c.id===id),raw=entry?.response?.body?.raw??result.message?.content??'';
  await save('reply-'+(i+1)+'.txt',raw);
  let parsed=null,diagnostic=null;try{need(result.status==='completed'&&result.finishReason==='stop','E6_INCOMPLETE');parsed=parse(raw,frame);}catch(e){diagnostic=e.code||e.message;}
  const checks={completed:result.status==='completed',valid:!!parsed,sourceUnchanged:hash(await host.audit())===hash(before),
    shape:i===0?!!parsed&&parsed.question===null&&parsed.options.length>=2:!!parsed?.question&&parsed.options.length===0};
  const output={slot:i+1,input:INPUTS[i],raw,parsed,diagnostic,checks,passed:Object.values(checks).every(Boolean),usage:result.usage,accounting:result.accounting,timing:result.timing,executionStatus:'not_executed'};
  outputs.push(output);await save('suggestions-'+(i+1)+'.json',output);
  console.log(JSON.stringify({finished:id,checks,diagnostic}));
  need(output.passed,'E6_CHECK_FAILED');
 }
}catch(e){failure={code:e.code||e.message};await save('execution-error.json',failure);console.log('Stopped without retry: '+failure.code);}
finally{
 const data=provider?(await provider.read())?.data??{calls:[]}:{calls:[]};
 await save('transport-evidence.json',data);
 const after=host?await host.audit():null;await save('source-after.json',after);
 await save('review.json',{mode,model:MODEL,outputs,limits:{calls:2,perCallUSD:.20,totalUSD:.40,retries:0},sourceUnchanged:!!after&&hash(after)===hash(before),
 calls:data.calls.map(c=>({id:c.id,reservedUSD:c.reserveUSD,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing})),failure});
 if(host)await host.close();if(ledger)await ledger.close();
 const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'E6_STOP');
}
if(mode==='mock')need(outputs.length===2&&outputs.every(o=>o.passed)&&!failure,'E6_MOCK_CHAIN');
