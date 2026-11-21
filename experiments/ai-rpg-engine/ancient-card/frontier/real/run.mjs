import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve,join} from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {emptySession,play,publicSession,knowledge,prompts} from '../session.mjs';
import {SessionRecords} from '../../../authoring/session-storage.mjs';
import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {reserve} from '../../../agent-core/actions/real/provider.mjs';
import {visibleText} from '../../../harness/measurement.mjs';

const mode=process.argv[2];assert.ok(['mock','real'].includes(mode));
const engine=fileURLToPath(new URL('../../../',import.meta.url));
const dir=resolve(engine,'.rpg03-work/ancient-map-gemini',mode+'-01');
const save=(name,value)=>writeFile(join(dir,name),JSON.stringify(value,null,2));
const read=async name=>JSON.parse(await readFile(join(dir,name),'utf8'));
const url='http://127.0.0.1:18987';
const token=execFileSync('python',[fileURLToPath(new URL('./bridge.py',import.meta.url)),mode,'--local-auth'],{encoding:'utf8',windowsHide:true}).trim();
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
const result={mode,model:'google/gemini-3.8-flash',route:'google-ai-studio',passed:false,calls:[],cases:[],local:[],maxUSD:.60};
let db,s,id,totalReserved=0;
async function reviewText(row){
 const plain=visibleText(row.raw).replace(/\s+/g,' ').trim();
 row.visible=plain;await save('result.json',result);
 if(mode==='mock')return;
 console.log(JSON.stringify({review:row.phase,call:row.n,visible:plain.slice(0,4800),length:plain.length}));
 for(let i=0;i<600;i++){
  let d;try{d=await read('decision-'+row.n+'.json');}catch(e){if(e.code!=='ENOENT')throw e;await delay(1000);continue;}
  row.judgment=d;assert.equal(d.pass,true,'正文与该时点可见信息不一致');return;
 }
 throw Error('正文待核对，未继续派发');
}
try{
 await writeFile(join(dir,'claim'),'once',{flag:'wx'});
 const ep=(await read('preflight.json')).endpoint;
 assert.equal(ep.tag,'google-ai-studio');assert.equal(ep.service_tier,null);
 assert.ok(!Number(ep.pricing.request||0));
 const q={inputUSD:Number(ep.pricing.prompt),outputUSD:Math.max(+ep.pricing.completion,+ep.pricing.internal_reasoning),quoteId:ep.tag};
 assert.ok(q.inputUSD>0&&q.outputUSD>0);
 const selection=(await (await fetch(url+'/api/rpg/v1/models?profile=core-query-r3',{headers})).json()).models.find(x=>x.model===result.model&&x.available);
 assert.equal(selection?.parameters.max_tokens,4096);result.parameters=selection.parameters;
 const decode=new NativeBridgeProvider({selection,authorization:{id:'ancient-map-review-'+mode,pricing:q}});
 const archived=JSON.parse(await readFile(join(engine,'.rpg03-work/ancient-frontier-v1/records/04c12557-58ba-4aaa-b9f4-7711072ea77a.json'),'utf8'));
 const scroll=archived.data.characterText;assert.match(scroll,/【帝王】崇祯帝朱由检/);
 db=await SessionRecords.open(join(dir,'saves'),{scope:'ancient-frontier-v1'});s=emptySession();id=s.id;
 let revisionHash=(await db.put(id,null,s)).hash;
 async function persist(input,generate){
  const candidate=structuredClone(s);
  await play(candidate,{input,revision:s.revision,requestId:randomUUID()},generate);
  revisionHash=(await db.put(id,revisionHash,candidate)).hash;s=candidate;
 }
 async function local(input){
  await persist(input);result.local.push({revision:s.revision,input:s.revision===1?'原表单命书（冻结开局）':input,body:s.turns.at(-1).raw});
 }
 async function check(phase,input,expectedControl){
  const info={phase,input,known:knowledge(s.world,'court'),tool:'未查询',adopted:null};
  result.cases.push(info);let stage=0;
  await persist(input,async packet=>{
   assert.ok(++stage<=2);
   assert.equal(packet.messages.findLast(m=>m.role==='user').content,input);
   if(phase==='获报前'){
    for(const f of s.world.facts){assert.ok(!JSON.stringify(packet).includes(f.id));assert.ok(!JSON.stringify(packet).includes(f.text));}
    assert.doesNotMatch(JSON.stringify(packet),/宁远军士因欠饷鼓噪|关报已到，记载营变/);
   }
   const n=result.calls.length+1;assert.ok(n<=6);
   const payload={sessionId:id,requestId:randomUUID(),selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...packet};
   const bound=reserve(payload,q,{max:4096,cap:.10});totalReserved+=bound.reserveMicros;assert.ok(totalReserved<=600000);
   const row={n,phase,stage,reservedUSD:bound.reserveMicros/1e6,firstVisibleMs:null,proseFirstMs:null,status:'unknown'};result.calls.push(row);
   if(stage===2){
    const tool=packet.messages.at(-1);assert.equal(tool.role,'tool');const tr=JSON.parse(tool.content);
    assert.equal(tr.control,expectedControl);assert.deepEqual(tr.evidence.map(e=>e.id),info.known.filter(e=>['report','receipt'].includes(e.kind)).map(e=>e.id));
    info.toolResult=tr;assert.equal(tool.tool_call_id,packet.messages.at(-2).tool_calls[0].id);
   }
   await save('request-'+n+'.json',payload);await save('result.json',result);
   let active=true;const start=performance.now();
   const poll=(async()=>{while(active){await delay(250);if(!active)break;
    try{const res=await fetch(url+'/api/rpg/v1/progress/'+id+'/'+payload.requestId,{headers,signal:AbortSignal.timeout(1200)});
     if(res.ok&&visibleText((await res.json()).text||'')){row.firstVisibleMs=Math.round(performance.now()-start);return;}}catch{}
   }})();
   let resp,raw;
   try{resp=await fetch(url+'/api/rpg/v1/scene/chat/completions?profile=core-query-r3',{method:'POST',headers,body:JSON.stringify(payload),signal:AbortSignal.timeout(240000)});
    raw=await resp.json();row.ms=Math.round(performance.now()-start);await save('raw-'+n+'.json',raw);
   }finally{active=false;await poll;}
   row.http=resp.status;row.gateway=raw.receipt?.status;row.dispatched=raw.receipt?.dispatched??null;
   const output=decode.decode({status:resp.status,body:raw},payload);
   Object.assign(row,{status:output.status,finish:output.finishReason,usage:output.usage,billing:output.accounting});
   assert.equal(output.status,'completed',raw.detail||raw.error||'provider result unavailable');
   assert.ok((output.accounting.reportedCostMicros??0)<=100000);
   const m=output.message;row.raw=m.content||'';
   if(m.tool_calls?.length){
    row.tools=m.tool_calls.map(c=>({id:c.id,name:c.function.name,arguments:c.function.arguments}));
    info.tool='查询未通过';assert.equal(stage,1);assert.equal(row.tools.length,1);assert.equal(row.tools[0].name,'query_map');
    assert.deepEqual(JSON.parse(row.tools[0].arguments),{node:'宁远'});info.tool='主动查对宁远';
   }else{
    assert.equal(output.finishReason,'stop');await reviewText(row);
   }
   return output;
  });
  info.adopted=s.turns.at(-1).raw;assert.equal(s.world.control,expectedControl);assert.equal(s.world.agent.pending,null);
  await save('result.json',result);
 }
 await local(scroll);
 await check('获报前',prompts.ask,'明');
 await local(prompts.report);
 await check('获报后',prompts.ask,'明');
 for(const input of [prompts.draft,prompts.decree,prompts.receive,prompts.handover]){await local(input);assert.equal(s.world.control,'明');}
 const before=s.world.control;await local(prompts.settle);
 const receipts=s.world.agent.events.filter(e=>e.kind==='settled');assert.equal(receipts.length,1);
 result.handover={before,after:s.world.control,receipt:receipts[0],chain:s.world.agent.events};await save('handover.json',result.handover);
 await db.close();db=await SessionRecords.open(join(dir,'saves'),{scope:'ancient-frontier-v1'});const restored=await db.get(id);
 assert.equal(restored.hash,revisionHash);s=restored.data;result.reloaded=true;
 await check('读档续写','接着上回写：宁远现在归谁，京师凭什么这样登记？','后金');
 const panel=publicSession(s).frontier;result.map=panel.locations;result.register=panel.locations.find(p=>p.name==='宁远').control;
 assert.deepEqual(result.map.map(p=>p.control),['明','明','后金','明']);assert.equal(s.world.agent.events.filter(e=>e.kind==='settled').length,1);
 result.passed=true;
}catch(e){result.stop=e.message;process.exitCode=1;console.log('STOP',e.message.slice(0,400));}
finally{
 if(s){result.lastCommitted={id:s.id,revision:s.revision,control:s.world?.control,pending:s.world?.agent.pending??null,settlements:s.world?.agent.events.filter(e=>e.kind==='settled').length??0};}
 await db?.close();await save('result.json',result);
 await fetch(url+'/task/stop',{method:'POST',headers}).catch(()=>{});
 console.log(JSON.stringify({mode,passed:result.passed,stages:result.calls.length}));
}
