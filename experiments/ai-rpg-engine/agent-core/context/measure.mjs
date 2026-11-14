// Deterministic offline chain comparison. Raw requests are retained only in ignored runtime data.
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';
import {openRuntime} from './composition.mjs';import {openRuntime as openR3} from '../composition.mjs';import {operationId,hash} from '../contracts.mjs';import {CONTINUITY} from './adapters.mjs';
const base=fileURLToPath(new URL('../../.rpg03-work/context-r4-measurements/',import.meta.url));await mkdir(base,{recursive:true});const directory=await mkdtemp(join(base,'run-'));
const old=await openR3(join(directory,'r3'),{offlinePreview:true}),current=await openRuntime(join(directory,'r4'),{offlinePreview:true});
const samples=[];const bytes=x=>Buffer.byteLength(typeof x==='string'?x:JSON.stringify(x));
async function sample(runtime,adapterId,label,inputs,selection){
 const h=runtime.get(adapterId);let r=await h.create({operationId:operationId(),initial:h.adapter.initial(),confirmed:true});const initialHash=hash(r.state);
 for(const [index,input] of inputs.entries()){
  const id=operationId(),start=performance.now(),contextOptions={...(selection?{selection}:{}),...(adapterId==='cultivation'&&input.includes('服用')?{quantityOnly:{id:'medicine',delta:-2}}:{})};
  r=await h.turn(r.id,{operationId:id,revision:r.revision,input},{contextOptions});if(r.pending)throw Error('sample not committed: '+r.operations[id].error);
  const o=r.operations[id],trace=o.context.contextTrace;
  const output=o.stages.at(-1).result.message.content,match=output.match(/<state_delta>([\s\S]*?)<\/state_delta>/);
  samples.push({label,adapter:adapterId,index,input,initialStateHash:initialHash,finalStateHash:hash(r.state),adapterBinding:r.binding,
   stages:o.stages.map(s=>({index:s.index,measurement:s.measurement,usage:s.result.usage,timing:s.result.timing})),
   totalRequestBytes:o.stages.reduce((n,s)=>n+s.measurement.wireBytes,0),simulationWallMs:Math.round(performance.now()-start),
   body:{characters:r.turns.at(-1).body.length,bytes:bytes(r.turns.at(-1).body)},deltaBytes:match?bytes(match[1]):null,
   renderedPanelBytes:bytes(await h.adapter.render(r.state)),generatedPanelBytes:0,
   selection:trace?{mode:trace.mode,refs:trace.selection.refs,fallbackReasons:trace.fallbackReasons}:null,
   materials:trace?.materials.map(({id,purpose,nature,selected,bytes,characters,hash})=>({id,purpose,nature,selected,bytes,characters,hash}))??null,
   changes:r.turns.at(-1).changes,realCalls:0,providerTokens:null,providerCostUSD:null,proseFirstMs:null,rawRecordFingerprint:hash(r)});
 }
 await writeFile(join(directory,label+'-raw.json'),JSON.stringify(await h.evidence(r.id),null,2)+'\n');
}
try{
 const direct='服用两份养息丹，其他状态不变',query='先查询丹药，再服用两份养息丹，其他状态不变';
 for(const [name,input] of [['direct',direct],['query',query]]){
  await sample(old,'cultivation','r3-'+name,[input]);
  await sample(current,'cultivation','r4-full-'+name,[input],{mode:'full',refs:[]});
  await sample(current,'cultivation','r4-scoped-'+name,[input],{mode:'scoped',refs:['scene:qingyun']});
 }
 await sample(current,'lighthouse','r4-continuity',CONTINUITY.map(x=>x.input),{mode:'scoped',refs:['scene:lighthouse']});
 await sample(old,'lighthouse','r3-continuity',CONTINUITY.map(x=>x.input));
 const comparisons=['direct','query'].map(kind=>{const a=samples.find(s=>s.label==='r3-'+kind),full=samples.find(s=>s.label==='r4-full-'+kind),b=samples.find(s=>s.label==='r4-scoped-'+kind);return {kind,r3Bytes:a.totalRequestBytes,r4FullBytes:full.totalRequestBytes,r4ScopedBytes:b.totalRequestBytes,scopedReductionPercent:Math.round((1-b.totalRequestBytes/a.totalRequestBytes)*10000)/100,sameFinalState:a.finalStateHash===b.finalStateHash};});
 const report={baseline:'92849f3142ae67805e9cb627495ac8fab7d25df2',mode:'offline-deterministic-fixture',generatedAt:new Date().toISOString(),manifests:{r3:old.registry.manifest.hash,r4:current.registry.manifest.hash},comparisons,samples,note:'UTF-8 bytes and JS string lengths only. No tokenizer, provider call, paid cost or real prose first-token measurement. R3 lighthouse fixtures do not implement R4 continuity; compare assembly overhead, not model capability. Output fixtures are not quality evidence.',rawDirectory:directory};
 const reportPath=new URL('../../../../docs/ai-rpg-experiment/harness/R4-OFFLINE-MEASUREMENTS.json',import.meta.url);await writeFile(reportPath,JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({comparisons,samples:samples.length,rawDirectory:directory,report:fileURLToPath(reportPath)},null,2));
}finally{await old.close();await current.close();}
