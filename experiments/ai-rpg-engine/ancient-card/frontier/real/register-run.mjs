import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import assert from 'node:assert/strict';
import {inputs} from './register-inputs.mjs';
import {emptySession,play,publicSession} from '../session.mjs';
import {SessionRecords} from '../../../authoring/session-storage.mjs';
import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {reserve} from '../../../agent-core/actions/real/provider.mjs';
import {visibleText} from '../../../harness/measurement.mjs';

const mode=process.argv[2];assert.ok(['mock','real'].includes(mode));
const engine=fileURLToPath(new URL('../../../',import.meta.url));
const dir=resolve(engine,'.rpg03-work/place-register-gemini',mode==='mock'?'mock-02':'real-01');
const save=(name,v)=>writeFile(join(dir,name),JSON.stringify(v,null,2));
const read=async name=>JSON.parse(await readFile(join(dir,name),'utf8'));
const token=execFileSync('python',['-B',fileURLToPath(new URL('./bridge.py',import.meta.url)),'--place-register',mode,'--local-auth'],{encoding:'utf8',windowsHide:true}).trim();
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
const url='http://127.0.0.1:19189',profile='core-query-r3';
const report={mode,model:'google/gemini-3.8-flash',route:'google-ai-studio',maxUSD:.50,passed:false,calls:[],turns:[]};
let s,db,hash,q,selection,decode,reserved=0;
const view=()=>publicSession(s).frontier;
const expected=[['propose_place_order','大员'],['query_map','大员'],['propose_map_change','大员'],['query_map','台湾']];
async function dispatch(packet){
  const n=report.calls.length+1;assert.ok(n<=5);const i=n===1?0:n<=3?1:2;
  assert.equal(packet.messages.findLast(m=>m.role==='user').content,inputs[i]);
  const payload={sessionId:s.id,requestId:randomUUID(),selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...packet};
  const bound=reserve(payload,q,{max:4096,cap:.10});reserved+=bound.reserveMicros;assert.ok(reserved<=500000);
  const row={n,input:inputs[i],requestId:payload.requestId,reservedUSD:bound.reserveMicros/1e6,status:'unknown',firstVisibleMs:null,proseFirstMs:null};
  const last=packet.messages.at(-1);
  if(n===3||n===5){
    assert.equal(last.role,'tool');assert.equal(last.tool_call_id,packet.messages.at(-2).tool_calls[0].id);
    row.toolResult=JSON.parse(last.content);assert.equal(row.toolResult.kind,'current_register');
    const dayuan=row.toolResult.entries.find(e=>e.node==='大员');
    assert.equal(dayuan.holder,n===3?'荷兰东印度公司':'明');
    assert.ok(dayuan.evidence.some(f=>f.kind==='receipt'&&f.receivedAt>f.occurredAt));
    if(n===5)assert.deepEqual(row.toolResult.entries.map(e=>e.holder),['明','西班牙','各社自治']);
  }else assert.equal(last.role,'user');
  await save('request-'+n+'.json',payload);report.calls.push(row);await save('result.json',report);
  let active=true;
  const start=performance.now();
  const poll=(async()=>{while(active){await delay(250);if(!active)break;
    try{const r=await fetch(url+'/api/rpg/v1/progress/'+s.id+'/'+payload.requestId,{headers,signal:AbortSignal.timeout(1200)});
      if(r.ok&&visibleText((await r.json()).text||'')){row.firstVisibleMs=Math.round(performance.now()-start);break;}
    }catch{}
  }})();
  let status,body;
  try{
    const res=await fetch(url+'/api/rpg/v1/scene/chat/completions?profile='+profile,{method:'POST',headers,body:JSON.stringify(payload),signal:AbortSignal.timeout(240000)});
    status=res.status;body=await res.json();row.ms=Math.round(performance.now()-start);
  }finally{active=false;await poll;}
  await save('raw-'+n+'.json',body);row.http=status;row.dispatched=body.receipt?.dispatched??null;
  const out=decode.decode({status,body},payload);Object.assign(row,{status:out.status,finish:out.finishReason,usage:out.usage,billing:out.accounting,body:out.message?.content??'',tools:out.message?.tool_calls?.map(c=>({id:c.id,...c.function}))??[]});
  await save('result.json',report);
  assert.equal(out.status,'completed',body.detail||out.error||'result unknown');
  if(out.accounting.reportedCostMicros!=null)assert.ok(out.accounting.reportedCostMicros<=100000);
  if(n<5){
    if(n===1&&row.tools.length===2){
      assert.equal(row.tools[0].name,'query_map');
      assert.ok(['台湾','大员'].some(node=>JSON.stringify(JSON.parse(row.tools[0].arguments))===JSON.stringify({node})));
    }else assert.equal(row.tools.length,1,'本步工具组合与验收场景不符');
    const [name,node]=expected[n-1];const tool=row.tools.at(-1);assert.equal(tool.name,name);
    const args=JSON.parse(tool.arguments);
    if(n===2&&args.node==='台湾')assert.deepEqual(args,{node:'台湾'});
    else if(n===3)assert.deepEqual(args,{node,source:view().known.find(f=>f.node===node&&f.kind==='receipt').id});
    else assert.deepEqual(args,{node});
  }else{
    assert.equal(out.finishReason,'stop');assert.ok(visibleText(row.body).trim(),'末轮正文为空');
    if(mode==='real'){
      console.log('FINAL_REVIEW_READY');
      let decision;
      for(let i=0;i<600&&!decision;i++){try{decision=await read('decision-5.json');}catch(e){if(e.code!=='ENOENT')throw e;await delay(1000);}}
      row.judgment=decision;assert.ok(decision?.pass,'末轮正文未通过人工核对，原文已保存');
    }
  }
  console.log(JSON.stringify({mode,n,result:row.finish,tools:row.tools.map(t=>t.name),usd:row.billing.reportedCostMicros/1e6}));
  return out;
}
async function turn(input,live=false){
  const next=structuredClone(s);
  await play(next,{revision:s.revision,requestId:randomUUID(),input},live?dispatch:undefined);
  hash=(await db.put(s.id,hash,next)).hash;s=next;
  report.turns.push({input,source:live?'模型参与回合':'本地宿主／开局夹具',adopted:s.turns.at(-1).raw,revision:s.revision,state:view()});
  await save('result.json',report);
}
const states=()=>view().locations.map(p=>p.control);
try{
  await writeFile(join(dir,'claim'),'once',{flag:'wx'});
  const ep=(await read('preflight.json')).endpoint;assert.equal(ep.tag,'google-ai-studio');assert.equal(ep.service_tier,null);assert.ok(!Number(ep.pricing.request||0));
  q={inputUSD:+ep.pricing.prompt,outputUSD:Math.max(+ep.pricing.completion,+ep.pricing.internal_reasoning),quoteId:ep.tag};assert.ok(q.inputUSD>0&&q.outputUSD>0);
  selection=(await (await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers})).json()).models.find(x=>x.model===report.model&&x.available);
  assert.equal(selection?.parameters.max_tokens,4096);
  decode=new NativeBridgeProvider({selection,authorization:{id:'place-register-'+mode,pricing:q}});
  const frozen=JSON.parse(await readFile(join(engine,'.rpg03-work/ancient-place-register/records/b4c136ff-165b-4ffc-807a-99757ab5d7a1.json'),'utf8'));
  const scroll=frozen.data.characterText;assert.match(scroll,/【帝王】崇祯帝朱由检/);
  db=await SessionRecords.open(join(dir,'saves'),{scope:'ancient-frontier-v1'});s=emptySession(false,true);hash=(await db.put(s.id,null,s)).hash;
  await turn(scroll);const before=states();await turn(inputs[0],true);assert.equal(s.world.pending?.kind,'order');assert.deepEqual(states(),before);
  await turn('签发这份旨意');await turn('推进至大员捷报抵京');assert.deepEqual(states(),before);
  await turn(inputs[1],true);assert.equal(s.world.pending.node,'大员');assert.deepEqual(states(),before);
  await turn('确认这份交割');const after=states();const correct=before.slice();correct[4]='明';assert.deepEqual(after,correct);
  const receipt=view().known.filter(f=>f.kind==='settled');assert.equal(receipt.length,1);assert.equal(receipt[0].source,view().known.find(f=>f.kind==='receipt').id);
  await save('handover.json',{before,after,receipt,events:view().known});await db.close();db=await SessionRecords.open(join(dir,'saves'),{scope:'ancient-frontier-v1'});const restored=await db.get(s.id);assert.equal(restored.hash,hash);s=restored.data;report.reloaded=true;
  await turn(inputs[2],true);assert.deepEqual(states(),correct);assert.equal(report.calls.length,5);report.passed=true;
}catch(e){report.stop=e.message;process.exitCode=1;console.log('STOP',String(e.message).slice(0,240));}
finally{
  if(s?.world)report.lastCommitted=publicSession(s);
  await db?.close();await save('result.json',report);
  await fetch(url+'/task/stop',{method:'POST',headers}).catch(()=>{});
}
