import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';import {join} from 'node:path';import {fileURLToPath} from 'node:url';
import {openPreview} from './bootstrap.mjs';import {measureRequest} from '../query-protocol.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/r8-validation/',import.meta.url));await mkdir(root,{recursive:true});const dir=await mkdtemp(join(root,'measure-'));
const rt=await openPreview(join(dir,'data'),{offlinePreview:true,delayMs:1}),rows=[];
try{
 for(const adapterId of ['cultivation','lighthouse']){
  // Independent starts avoid silently comparing different accumulated histories.
  for(const [kind,input] of [['direct','聊聊今日安排'],['query','查看登记物资'],['compute','统计登记物资']]){
   const r=await rt.create({operationId:adapterId+'-'+kind,adapterId});
   const v=await rt.submit(r.id,{operationId:'turn',ownerId:'world-dialogue',input}),t=v.tasks[0];
   rows.push({adapterId,kind,status:t.adoption,stages:t.stages.map(s=>({input:measureRequest(s.request),
    outputBytes:Buffer.byteLength(JSON.stringify(s.result?.message??{})),attempts:s.attempts.map(a=>({model:a.model.id,billing:a.result?.billing,timing:a.timing}))}))});
  }
 }
 const report={mode:'offline-fixture',realCalls:0,actualUSD:0,tokens:null,proseFirstMs:null,
  limitations:'No matching tokenizer or real streaming boundary. System bytes combine selected material, rules and state. Fixture completion time is not real-model latency or quality.',rows,budget:(await rt.list()).budget};
 await writeFile(join(dir,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({directory:dir,rows:rows.map(r=>({adapter:r.adapterId,kind:r.kind,stages:r.stages.length,requestBytes:r.stages.reduce((n,s)=>n+s.input.wireBytes,0),outputBytes:r.stages.reduce((n,s)=>n+s.outputBytes,0)}))}));
}finally{await rt.close();}
