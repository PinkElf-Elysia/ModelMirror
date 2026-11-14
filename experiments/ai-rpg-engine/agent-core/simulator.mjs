import {SessionRecords} from '../authoring/session-storage.mjs';
import {hash,clone,need,keys,bounded} from './contracts.mjs';
export const FAULTS=[null,'truncated','failed','unknown','unknown-first','invalid','bad-arguments','repeat'];
export class CoreSimulator{
 static async open(directory,fixture){return new CoreSimulator(await SessionRecords.open(directory,{scope:'rpg-core-r3-simulator',recoverDeadOwner:true}),fixture);}
 constructor(records,fixture){this.records=records;this.fixture=fixture;this.calls=0;this.kind='offline-core-simulator';this.selection={model:'deterministic-adapter-fixture/3'};}
 options(options={}){
  keys(options,['variant','fault','contextOptions','previewHash']);const out={variant:options.variant??'calm',fault:options.fault??null,contextOptions:options.contextOptions??{},previewHash:options.previewHash??null};
  need(['calm','warm'].includes(out.variant)&&FAULTS.includes(out.fault),'CORE_OPTIONS');bounded(out);return out;
 }
 preview(request,meta){return this.fixture(clone(request),clone(meta));}
 key(session,id){return 'mock-'+hash([session,id]).slice(0,40);}
 async generate(session,id,request,meta){
  const key=this.key(session,id),requestHash=hash({request,meta}),old=await this.records.get(key,x=>x);
  if(old){need(old.data.requestHash===requestHash,'NATIVE_REQUEST_COLLISION');return old.data.result;}
  const {variant,fault}=this.options(meta.options);need(FAULTS.includes(fault),'CORE_FAULT');this.calls++;const start=performance.now();
  const output=await this.fixture(clone(request),{...clone(meta),variant,fault});await new Promise(r=>setTimeout(r,35));
  let result={status:'completed',finishReason:output.query?'tool_calls':'stop',provider:this.kind,
   usage:{kind:'simulated',inputTokens:null,outputTokens:null,costUSD:0},
   timing:{firstContentMs:null,proseFirstMs:null,completedMs:performance.now()-start},
   message:output.query?{role:'assistant',content:null,tool_calls:[{id:'query-one',type:'function',function:{name:request.tools[0].function.name,arguments:JSON.stringify(output.query)},extra_content:{google:{thought_signature:'offline-opaque-signature'}}}]}:{role:'assistant',content:output.text}};
  if(fault==='invalid'&&output.query)result.message.tool_calls[0].function.name='write_state';
  if(fault==='bad-arguments'&&output.query)result.message.tool_calls[0].function.arguments='{"queries":[';
  if(fault==='truncated'&&!output.query)result.finishReason='length';
  if(fault==='failed'&&!output.query)result={status:'failed',error:'模拟明确失败',provider:this.kind};
  if(fault==='repeat'&&meta.stage===2)result={...result,finishReason:'tool_calls',message:{role:'assistant',content:null,tool_calls:[{id:'again',type:'function',function:{name:request.tools[0].function.name,arguments:'{}'}}]}};
  await this.records.put(key,null,{requestHash,result});
  if((fault==='unknown'&&!output.query)||(fault==='unknown-first'&&meta.stage===1))return {status:'unknown',error:'模拟回执丢失，请核对原请求'};
  return result;
 }
 async lookup(session,id){return (await this.records.get(this.key(session,id),x=>x))?.data.result||null;}
 close(){return this.records.close();}
}
