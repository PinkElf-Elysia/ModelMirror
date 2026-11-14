import{mkdtemp,writeFile,rm}from'node:fs/promises';import{tmpdir}from'node:os';import{join,resolve}from'node:path';import{fileURLToPath}from'node:url';import{performance}from'node:perf_hooks';
import{createShopHost}from'./host.mjs';import{createMockProvider}from'./mock-provider.mjs';import{rules}from'./earth-adapter.mjs';import{initialAssets}from'./shop-rules.mjs';
const directory=await mkdtemp(join(tmpdir(),'shop-measure-')),root=fileURLToPath(new URL('../',import.meta.url));
const host=await createShopHost({directory,provider:createMockProvider({delay:25}),sourceLoader:async()=>null});
const rows=[];
try{
 for(const memory of [false,true]){
  let s=await host.activate({operationId:memory?'with-memory':'without-memory',sourceId:'example',initial:initialAssets(rules),confirmed:true,context:{profiles:[],modIds:[],summaryEnabled:memory,memoryEnabled:memory}});
  const start=performance.now(),wallStart=Date.now();let receipt;
  await host.handle({pathname:'/rpg-app/earth/api/shop/sessions/'+s.id+'/purchase',method:'POST',data:{operationId:'buy',revision:s.revision,ruleVersion:rules.version,productId:'bread',quantity:2},send:(code,data)=>{if(code!==200)throw Error('purchase failed');receipt={time:performance.now(),revision:data.revision};}});
  s=await host.process(s.id,'buy');const end=performance.now(),story=s.stageRuns.find(x=>x.purpose==='narration');
  rows.push({provenance:'simulated-provider-local-host',memoryPlugins:memory,feedbackMs:receipt.time-start,bodyAvailableAt:story.completedAt,bodyCompleteMs:story.completedAt-wallStart,bodyFirstTokenMs:null,bodyTimingMode:'buffered; first token not observed',allRequiredStagesMs:end-start,calls:s.usage.calls,costReportedMicros:0,tokenUsage:null,assets:s.assets,stages:s.stageRuns.map(x=>({purpose:x.purpose,durationMs:x.completedAt-x.startedAt,attempts:x.attempts.length}))});
 }
 const report={format:'modelmirror.rpg.harness-offline-measure/1',measuredAt:new Date().toISOString(),realCalls:0,warning:'Synthetic delay only. No prose-quality, live latency, token reduction or real-price conclusion.',rows};
 await writeFile(resolve(root,'../../docs/ai-rpg-experiment/harness/offline-measure.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
}finally{await host.close();if(!resolve(directory).startsWith(resolve(tmpdir())+'\\shop-measure-'))throw Error('CLEANUP_OUT_OF_SCOPE');await rm(directory,{recursive:true,force:true});}
