import assert from 'node:assert/strict';
import {isDeepStrictEqual} from 'node:util';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile,cp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {preview} from '../ordinary-preview.mjs';
import {entries} from '../../worldbook.mjs';
import {offlineNarrator} from '../transport.mjs';
import {startRegister,registerView,inspectStoryDelta} from '../place-register.mjs';
import {reserve} from '../../../agent-core/actions/real/provider.mjs';
import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {visibleText} from '../../../harness/measurement.mjs';

const mode=process.argv[2];assert.ok(['real','mock'].includes(mode));
const engine=fileURLToPath(new URL('../../../',import.meta.url));
const dir=join(engine,'.rpg03-work/ordinary-opening-gemini',mode==='mock'?'mock-02':'real-01');
const save=(name,x)=>writeFile(join(dir,name),JSON.stringify(x,null,2));
const url='http://127.0.0.1:19226';let origin,base;
const token=execFileSync('python',['-B',fileURLToPath(new URL('./bridge.py',import.meta.url)),'--opening',mode,'--local-auth'],{encoding:'utf8'}).trim();
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
const scenario=JSON.parse(await readFile(join(dir,'../selection.json'),'utf8'));
const frozen=JSON.parse(await readFile(join(dir,'../browser-request.json'),'utf8'));
const first=scenario.opening,follow='请王承恩把第一份奏疏呈上来。';
let captureOnly=false;
const report={mode,model:'google/gemini-3.8-flash',route:'google-ai-studio',calls:[],turns:[],passed:false};
let close,s,live=false,quote,selection,decoder,reserved=0,initial;
const world=()=>({known:s.frontier.known,locations:s.frontier.locations});
async function generate(packet){
 if(captureOnly){
   await save('next-request-offline.json',packet);
   return {status:'completed',finishReason:'stop',message:{role:'assistant',content:'仅用于隔离副本的离线装配检查。'}};
 }
 assert.ok(live);const n=report.calls.length+1;assert.equal(n,1);
 assert.deepEqual(packet,frozen);assert.deepEqual(packet.tools,[]);assert.equal(packet.tool_choice,'none');
 assert.deepEqual(packet.messages.map(m=>m.role),['system','user']);
 assert.equal(packet.messages[1].content,first);
 assert.deepEqual(entries.filter(e=>packet.messages[0].content.includes(e.text)).map(e=>e.name),['明朝']);
 const payload={sessionId:s.id,requestId:randomUUID(),selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...packet};
 const {reserveMicros}=reserve(payload,quote,{max:8192,cap:.10});reserved+=reserveMicros;assert.ok(reserved<=100000);
 const row={n,requestId:payload.requestId,status:'unknown',reserveMicros,firstVisibleMs:null,proseFirstMs:null};
 await save('request-'+n+'.json',payload);report.calls.push(row);await save('result.json',report);
 const start=performance.now();let active=true;
 const poll=(async()=>{while(active){await delay(250);if(!active)break;try{const res=await fetch(url+'/api/rpg/v1/progress/'+s.id+'/'+payload.requestId,{headers,signal:AbortSignal.timeout(900)});
 if(res.ok && visibleText((await res.json()).text||'')){row.firstVisibleMs=Math.round(performance.now()-start);break;}}catch{}}})();
 let body,status;
 try{const res=await fetch(url+'/api/rpg/v1/scene/chat/completions?profile=core-query-r3',{method:'POST',headers,body:JSON.stringify(payload),signal:AbortSignal.timeout(240000)});
 status=res.status;body=await res.json();row.ms=Math.round(performance.now()-start);}finally{active=false;await poll;}
 await save('raw-'+n+'.json',body);const out=decoder.decode({status,body},payload);
 Object.assign(row,{status:out.status,finish:out.finishReason,usage:out.usage,billing:out.accounting,http:status,text:out.message?.content??'',tools:out.message?.tool_calls?.map(c=>({id:c.id,...c.function}))??[]});
 await save('result.json',report);assert.equal(out.status,'completed',body.detail||out.error||'结果未知');
 assert.equal(out.finishReason,'stop');assert.equal(row.tools.length,0);assert.ok(visibleText(row.text).trim());
 return out;
}
async function send(input,real=false){
 live=real;
 const route=base+'/'+s.id;
 const memory=await (await fetch(route+'/memory-palace')).json();
 const count=s.turns.length;
 const request={operationId:randomUUID(),input,expectedSessionRevision:s.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision};
 const r=await fetch(route+'/send',{method:'POST',headers:{origin,'Content-Type':'application/json'},body:JSON.stringify(request)});
 if(!r.ok)throw Error((await r.json()).error);
 s=await r.json();
 if(s.turns.length!==count+1)throw Error(report.halt||s.operation?.error||'回合未采用');
 report.turns.push({input,source:real?mode:'local',adopted:s.turns.at(-1).raw,rev:s.revision});
 await save('result.json',report);
}
async function open(name='saves'){
 const host=await preview(join(dir,name),0,true,async packet=>{
  try{return await generate(packet);}catch(error){report.halt=error.message;await save('result.json',report);throw error;}
 });
 origin=host.origin;base=origin+'/rpg-app/ancient/chat/api/sessions';return host.close;
}
try{
 await writeFile(join(dir,'claim'),'once',{flag:'wx'});
 const ep=JSON.parse(await readFile(join(dir,'preflight.json'),'utf8')).endpoint;
 assert.equal(ep.tag,'google-ai-studio');assert.equal(ep.service_tier,null);assert.ok(!Number(ep.pricing.request||0));
 quote={inputUSD:+ep.pricing.prompt,outputUSD:Math.max(+ep.pricing.completion,+ep.pricing.internal_reasoning),quoteId:ep.tag};
 assert.ok(quote.inputUSD>0&&quote.outputUSD>0);
 selection=(await (await fetch(url+'/api/rpg/v1/models?profile=core-query-r3',{headers})).json()).models.find(x=>x.available&&x.model===report.model);
 assert.deepEqual(selection?.parameters,{max_tokens:8192,temperature:.7,top_p:.8});decoder=new NativeBridgeProvider({selection,authorization:{id:'story-map-'+mode,pricing:quote}});
 close=await open();s=await (await fetch(base,{method:'POST',headers:{origin,'Content-Type':'application/json'},body:JSON.stringify({opening:''})})).json();
 const zero=registerView(startRegister());
 initial={known:zero.known,locations:zero.locations};assert.equal(s.turns.length,0);await save('before.json',initial);
 await send(first,true);assert.equal(s.turns.length,1);assert.equal(s.turns[0].input,first);
 const parsed=inspectStoryDelta(report.calls[0].text);
 assert.equal(s.turns[0].raw,parsed.prose);
 assert.deepEqual(world().locations,initial.locations);
 if(parsed.delta){
   assert.equal(world().known.length,1);
   const [fact]=world().known;
   assert.equal(fact.kind,'rumor');assert.equal(fact.source,s.turns[0].requestId);
   assert.equal(fact.node,parsed.delta.node);assert.equal(fact.sourceText,parsed.evidence.source);
 }else assert.deepEqual(world().known,initial.known);
 const accepted=structuredClone(s);
 await close();close=null;
 close=await open();s=await (await fetch(base+'/'+accepted.id)).json();assert.deepEqual(s.turns,accepted.turns);assert.deepEqual(s.frontier,accepted.frontier);
 report.reloaded=true;report.id=s.id;await save('accepted.json',s);await save('after.json',world());
 await close();close=null;
 await cp(join(dir,'saves'),join(dir,'offline-follow-copy'),{recursive:true,errorOnExist:true,force:false});
 captureOnly=true;close=await open('offline-follow-copy');await send(follow);
 const next=JSON.parse(await readFile(join(dir,'next-request-offline.json'),'utf8'));
 assert.equal(next.messages.at(-1).content,follow);assert.ok(next.messages.some(m=>m.role==='user'&&m.content===first));
 assert.ok(next.messages.some(m=>m.role==='assistant'&&m.content===accepted.turns[0].raw));
 assert.equal(report.calls.length,1);report.passed=true;s=accepted;
}catch(e){report.stop=e.message;console.log('STOP',String(e.message).slice(0,240));process.exitCode=1;}
finally{
 if(s?.id){report.id=s.id;if(!captureOnly&&s.frontier)await save('after.json',world());}
 await close?.();await save('result.json',report);await fetch(url+'/task/stop',{method:'POST',headers}).catch(()=>{});console.log('Completed requests:',report.calls.length);
}
