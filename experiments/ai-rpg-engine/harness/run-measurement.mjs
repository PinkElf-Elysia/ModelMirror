
import{readFile,writeFile,mkdir}from'node:fs/promises';
import{fileURLToPath}from'node:url';import{join}from'node:path';
import{hash,canonical}from'./contracts.mjs';import{MeasurementBudget}from'./measurement-budget.mjs';
import{createControlledProvider}from'../studio/controlled-provider.mjs';
import{createHarnessControlledProvider}from'./controlled-provider.mjs';
const root=fileURLToPath(new URL('../',import.meta.url)),run=join(root,'.rpg04-work/harness-real-20261113');
const plan=JSON.parse(await readFile(join(root,'../../docs/ai-rpg-experiment/harness/real-call-plan.json'),'utf8'));
if(hash(plan)!=='3e59a84fbfbd118d988c8322a5f45c1dccf15f0e94d6480536a88545cd006a86')throw Error('APPROVED_PLAN_CHANGED');
if(process.argv[2]!=='--execute')throw Error('EXPLICIT_EXECUTION_REQUIRED');
const row=plan.requests.find(r=>r.id===process.argv[3]);if(!row)throw Error('REQUEST_NOT_LISTED');
for(const [f,h]of Object.entries(plan.implementationHashes))if(hash(await readFile(join(root,f),'utf8'))!==h)throw Error('IMPLEMENTATION_CHANGED');
const fixture=JSON.parse(await readFile(join(root,'.rpg04-work/harness-real-plan',row.fixture),'utf8'));
if(hash(fixture.messages)!==row.messagesHash)throw Error('INPUT_CHANGED');
await mkdir(run,{recursive:true});
const authorization={confirmed:true,planHash:hash(plan),maxCalls:3,maxCostMicros:750000,source:'User authorized reduced plan: maximum three calls, USD 0.75; save replies and provide review page.',at:'2026-11-13'};
async function freeze(name,value){try{await writeFile(join(run,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});}catch(e){if(e.code!=='EEXIST')throw e;if(canonical(JSON.parse(await readFile(join(run,name),'utf8')))!==canonical(value))throw Error('FROZEN_FILE_CHANGED '+name);}}
await freeze('authorization.json',authorization);
const transport=await createControlledProvider({directory:join(run,'transport'),baseURL:'http://127.0.0.1:8000/',serviceToken:process.env.RPG_S2S_TOKEN,enabled:true,limit:3,measurement:{pollIntervalMs:250},catalogModels:[plan.model]});
const selection=(await transport.catalog()).find(m=>m.model===plan.model&&m.available);if(!selection)throw Error('MODEL_UNAVAILABLE_NO_CERTIFICATION_AUTHORIZED');
await freeze('selection.json',selection);
const model={id:plan.model,name:selection.name,selection,parameters:plan.parameters,upperBoundMicros:250000,pricing:plan.pricing};
const request={id:row.id,sessionId:'harness-real-20261113',purpose:row.purpose,model:{...model,available:true},messages:fixture.messages,messagesHash:row.messagesHash};
await freeze(row.id+'-request.json',request);
const budget=await MeasurementBudget.open({directory:join(run,'budget'),plan,authorization,recoverDeadOwner:true});
try{
 const provider=createHarnessControlledProvider({transport,models:[model],authorized:true,measurementRun:{budget,testId:row.id}});
 console.log(JSON.stringify({dispatching:row.id,maxCalls:3,maxCostUSD:0.75}));
 const result=await provider.dispatch(request);
 if(result.status!=='succeeded'){const saved=await transport.recoveredOutput(request.sessionId,request.id);if(saved?.raw){result.raw=saved.raw;result.partial=true;}}
 await writeFile(join(run,row.id+'-result.json'),JSON.stringify(result,null,2)+'\n');
 if(result.raw)await writeFile(join(run,row.id+'-reply.txt'),result.raw);
 console.log(JSON.stringify({id:row.id,status:result.status,code:result.code||null,costMicros:result.costMicros??null,usage:result.usage||null,timing:result.measurement?.timing||null}));
 if(result.status!=='succeeded')process.exitCode=2;
}finally{await budget.close();}
