// Offline measurement only. Prints a small summary; raw requests stay in temporary records.
import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {openRuntime} from './composition.mjs';import {operationId,hash} from './contracts.mjs';
const directory=await mkdtemp(join(tmpdir(),'rpg-core-measure-'));let runtime;
try{
 runtime=await openRuntime(directory,{offlinePreview:true});const samples=[];
 for(const adapter of runtime.registry.list){
  const host=runtime.get(adapter.id);
  for(const kind of ['direct','query']){
   const r=await host.create({operationId:operationId(),initial:adapter.initial(),confirmed:true});
   const input=kind==='query'?adapter.scenarios[0][1]:adapter.scenarios.find(([id])=>id==='use'||id==='use-two')[1];
   const started=performance.now(),done=await host.turn(r.id,{operationId:operationId(),revision:r.revision,input}),duration=performance.now()-started;
   const o=Object.values(done.operations)[0];
   samples.push({adapter:adapter.id,kind,input,mode:'offline-simulator',stages:o.stages.map(s=>({measurement:s.measurement,usage:s.result.usage})),totalRequestBytes:o.stages.reduce((n,s)=>n+s.measurement.wireBytes,0),simulationWallMs:Math.round(duration),stateRevision:done.stateRevision,finalStateHash:hash(done.state),bodyChars:done.turns[0].body.length,changes:done.turns[0].changes,realCalls:0,actualProviderCostUSD:null,proseFirstMs:null});
  }
 }
 console.log(JSON.stringify({baseline:'ae67eb2510066a7301825a58de96872c64593298',manifestHash:runtime.registry.manifest.hash,note:'Offline bytes and deterministic fixtures only; no token or quality/cost improvement claim.',samples},null,2));
}finally{if(runtime)await runtime.close();await rm(directory,{recursive:true,force:true});}
