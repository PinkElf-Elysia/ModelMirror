import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {cp,readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {preview} from '../ordinary-preview.mjs';
import {reserve} from '../../../agent-core/actions/real/provider.mjs';
import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';

const mode=process.argv[2];
assert.ok(['mock','real'].includes(mode));
const engine=fileURLToPath(new URL('../../../',import.meta.url));
const dir=join(engine,'.rpg03-work/ordinary-docket-gemini',mode==='real'?'real-01':'mock-02');
const old=join(engine,'.rpg03-work/ordinary-opening-gemini/real-01');
const read=async path=>JSON.parse(await readFile(path,'utf8'));
const save=(name,value)=>writeFile(join(dir,name),JSON.stringify(value,null,2));
const url='http://127.0.0.1:19236';
const question='刚才说宁远欠饷，哪一份文书送到了京师？';
const saved=await read(join(old,'accepted.json'));
const report={mode,model:'google/gemini-3.8-flash',question,sourceRequest:saved.turns[0].requestId,calls:[],passed:false};
const token=execFileSync('python',['-B',fileURLToPath(new URL('./bridge.py',import.meta.url)),'--ningyuan',mode,'--local-auth'],{encoding:'utf8'}).trim();
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
let host,selection,quote,decoder,initial;
async function generate(packet){
  const n=report.calls.length+1;assert.equal(n,1);
  const payload={sessionId:saved.id,requestId:randomUUID(),selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...packet};
  const {reserveMicros}=reserve(payload,quote,{max:8192,cap:.10});
  assert.ok(reserveMicros<=100000);
  const row={requestId:payload.requestId,reserveMicros,status:'unknown'};
  await save('request-'+n+'.json',payload);report.calls.push(row);await save('result.json',report);
  if(mode==='real'){
    const frozen=await read(join(dir,'../mock-02/request-1.json'));
    for(const key of ['messages','tools','tool_choice','parameters'])assert.deepEqual(payload[key],frozen[key]);
  }
  const start=performance.now();
  const res=await fetch(url+'/api/rpg/v1/scene/chat/completions?profile=core-query-r3',{method:'POST',headers,body:JSON.stringify(payload),signal:AbortSignal.timeout(240000)});
  const body=await res.json();row.ms=Math.round(performance.now()-start);
  await save('raw-'+n+'.json',body);
  const out=decoder.decode({status:res.status,body},payload);
  Object.assign(row,{http:res.status,status:out.status,finish:out.finishReason,usage:out.usage,tools:out.message?.tool_calls?.map(t=>t.function)??[]});
  await save('result.json',report);assert.equal(out.status,'completed',body.detail||out.error);
  assert.equal(row.finish,'tool_calls','模型直接作答；保留原文并停止');
  assert.equal(row.tools.length,1);assert.equal(row.tools[0].name,'query_map');
  const args=JSON.parse(row.tools[0].arguments);
  assert.deepEqual(args,Object.hasOwn(args,'use')?{node:'宁远',use:'documents'}:{node:'宁远'});
  report.selectedUse=args.use??'default-documents';
  return out;
}
async function api(suffix,body){
  const res=await fetch(host.origin+'/rpg-app/ancient/chat/api/sessions/'+saved.id+suffix,body?{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  const data=await res.json();assert.ok(res.ok,data.error);return data;
}
const world=s=>({known:s.frontier.known,locations:s.frontier.locations,actions:s.frontier.actions});
try{
  await writeFile(join(dir,'claim'),'once',{flag:'wx'});
  const ep=(await read(join(dir,'preflight.json'))).endpoint;assert.equal(ep.tag,'google-ai-studio');assert.equal(ep.service_tier,null);assert.ok(!Number(ep.pricing.request||0));
  quote={inputUSD:+ep.pricing.prompt,outputUSD:Math.max(+ep.pricing.completion,+ep.pricing.internal_reasoning),quoteId:ep.tag};
  assert.ok(quote.inputUSD>0&&quote.outputUSD>0);
  selection=(await (await fetch(url+'/api/rpg/v1/models?profile=core-query-r3',{headers})).json()).models.find(m=>m.available&&m.model===report.model);
  assert.deepEqual(selection.parameters,{max_tokens:8192,temperature:.7,top_p:.8});
  decoder=new NativeBridgeProvider({selection,authorization:{id:'ningyuan-'+mode,pricing:quote}});
  await cp(join(old,'saves'),join(dir,'saves'),{recursive:true,errorOnExist:true,force:false});
  host=await preview(join(dir,'saves'),0,true,generate);
  const s=await api('');assert.deepEqual(s.turns,saved.turns);initial=world(s);await save('before.json',initial);
  const m=await api('/memory-palace');
  const next=await api('/send',{operationId:randomUUID(),input:question,expectedSessionRevision:s.revision,expectedMemoryRevision:m.memoryRevision,expectedContextPolicyRevision:m.effectivePolicy.revision});
  assert.equal(next.turns.length,s.turns.length+1,JSON.stringify(next.operation));
  await save('accepted.json',next);await save('after.json',world(next));assert.deepEqual(world(next),initial);
  assert.equal(report.calls.length,1);
  assert.deepEqual(next.turns.at(-1).atlasLookup?.entries,[{node:'宁远',holder:'明',evidence:[]}]);
  await host.close();host=null;
  host=await preview(join(dir,'saves'),0,true,()=>{throw Error('读档时不应派发');});
  const reopened=await api('');assert.deepEqual(reopened.turns,next.turns);assert.deepEqual(world(reopened),initial);
  report.reloaded=true;report.passed=true;
}catch(e){report.stop=String(e.message).slice(0,650);process.exitCode=1;}
finally{await host?.close();await save('result.json',report);await fetch(url+'/task/stop',{method:'POST',headers}).catch(()=>{});console.log(JSON.stringify(report));}