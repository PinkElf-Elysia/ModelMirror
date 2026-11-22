import assert from 'node:assert/strict';
import {isDeepStrictEqual} from 'node:util';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {preview} from '../server.mjs';
import {adaptStorySystem} from '../story-prompt.mjs';
import {offlineNarrator} from '../transport.mjs';
import {empty} from '../../../plugins/custom-config/public/config.mjs';
import {reserve} from '../../../agent-core/actions/real/provider.mjs';
import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {visibleText} from '../../../harness/measurement.mjs';

const mode=process.argv[2];assert.ok(['real','mock'].includes(mode));
const engine=fileURLToPath(new URL('../../../',import.meta.url));
const retest=process.env.STORY_MAP_RETEST==='1';
const dir=join(engine,retest?'.rpg03-work/story-map-gemini-prose-retest':'.rpg03-work/story-map-gemini',mode==='mock'?'mock-02':'real-01');
const save=(name,x)=>writeFile(join(dir,name),JSON.stringify(x,null,2));
const url='http://127.0.0.1:19193',origin='http://127.0.0.1:19194',base=origin+'/rpg-app/ancient/chat/api/sessions';
const token=execFileSync('python',['-B',fileURLToPath(new URL('./bridge.py',import.meta.url)),'--story-map',mode,'--local-auth'],{encoding:'utf8'}).trim();
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
const config=empty();config.depth=1;config.system='以御前朝议口吻续写';
config.worldbook.entries=[{...config.worldbook.defaults,id:'east-book',name:'青册',enabled:true,order:0,depth:0,keywords:['东番'],categoryId:null,regex:false,chance:100,mode:'regular',body:'御前称地点登记卷为青册。',contents:[]}];
const first='接着写朝议。',second='翻开青册。东番如今各地归谁，朕凭什么这样记？';
const report={mode,model:'google/gemini-3.8-flash',route:'google-ai-studio',calls:[],turns:[],passed:false};
let close,s,live=false,quote,selection,decoder,reserved=0,initial;
const world=()=>({known:s.frontier.known,locations:s.frontier.locations});
async function generate(packet){
 if(!live)return offlineNarrator(packet);
 const n=report.calls.length+1;assert.ok(n<=3);
 const user=packet.messages.findLast(m=>m.role==='user').content,sys=packet.messages[0].content;
 assert.equal(user,n===1?first:second);assert.ok(sys.includes(config.system));
 if(n===1){assert.match(sys,/御前新到记录：[\s\S]*大员.*改登为明/);}
 else{
  assert.ok(sys.includes(config.worldbook.entries[0].body));assert.ok(!sys.includes('御前新到记录：'));
  if(n===2){assert.equal(packet.messages.length,4);assert.equal(packet.messages[1].content,'阁臣今日饮茶如何？');
    assert.doesNotMatch(packet.messages.map(m=>m.content).join('\n'),/大员.{0,20}(改登为明|已归明)|鸡笼.{0,12}西班牙|各社自治/);}
  else{const prev=JSON.parse(await readFile(join(dir,'request-2.json'),'utf8'));assert.deepEqual(packet.messages.slice(0,-2),prev.messages);
    assert.equal(packet.messages.at(-1).tool_call_id,packet.messages.at(-2).tool_calls[0].id);
    assert.deepEqual(JSON.parse(packet.messages.at(-1).content).entries.map(e=>e.holder),['明','西班牙','各社自治']);assert.deepEqual(packet.tools,[]);}
 }
 if(retest && n===1){
  const previous=JSON.parse(await readFile(join(engine,'.rpg03-work/story-map-gemini-retest/real-01/request-1.json'),'utf8'));
  previous.messages[0].content=adaptStorySystem(previous.messages[0].content);
  assert.deepEqual(packet,{messages:previous.messages,tools:previous.tools,tool_choice:previous.tool_choice});
  assert.deepEqual(selection.parameters,previous.parameters);
 }
 const payload={sessionId:s.id,requestId:randomUUID(),selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...packet};
 const {reserveMicros}=reserve(payload,quote,{max:4096,cap:.10});reserved+=reserveMicros;assert.ok(reserved<=300000);
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
 if(n===2){
  if(out.finishReason==='stop')throw Error('隔轮直接作答，未查档；原文已保存');
  assert.equal(row.tools.length,1);assert.equal(row.tools[0].name,'query_map');assert.deepEqual(JSON.parse(row.tools[0].arguments),{node:'台湾'});
 }else{if(row.tools.length)throw Error('回执已给出时仍发起工具调用；停止');assert.equal(out.finishReason,'stop');assert.ok(visibleText(row.text).trim());assert.doesNotMatch(row.text,/<map_delta/);}
 return out;
}
async function send(input,real=false){
 live=real;const r=await fetch(base+'/'+s.id+'/send',{method:'POST',headers:{origin,'Content-Type':'application/json','X-Rpg-Custom-Config':'1'},body:JSON.stringify({profiles:[config],request:{requestId:randomUUID(),revision:s.revision,input}})});
 if(!r.ok)throw Error((await r.json()).error);
 s=await r.json();report.turns.push({input,source:real?mode:'local',adopted:s.turns.at(-1).raw,rev:s.revision});
 if(initial)assert.deepEqual(world(),initial);await save('result.json',report);
}
const open=()=>preview(19194,join(dir,'saves'),{storyMap:true,placeRegister:true,generate});
try{
 await writeFile(join(dir,'claim'),'once',{flag:'wx'});
 const ep=JSON.parse(await readFile(join(dir,'preflight.json'),'utf8')).endpoint;
 assert.equal(ep.tag,'google-ai-studio');assert.equal(ep.service_tier,null);assert.ok(!Number(ep.pricing.request||0));
 quote={inputUSD:+ep.pricing.prompt,outputUSD:Math.max(+ep.pricing.completion,+ep.pricing.internal_reasoning),quoteId:ep.tag};
 assert.ok(quote.inputUSD>0&&quote.outputUSD>0);
 selection=(await (await fetch(url+'/api/rpg/v1/models?profile=core-query-r3',{headers})).json()).models.find(x=>x.available&&x.model===report.model);
 assert.equal(selection?.parameters.max_tokens,4096);decoder=new NativeBridgeProvider({selection,authorization:{id:'story-map-'+mode,pricing:quote}});
 close=await open();s=await (await fetch(base,{method:'POST',headers:{origin,'Content-Type':'application/json'},body:'{}'})).json();
 const original=JSON.parse(await readFile(join(engine,'.rpg03-work/ancient-place-register/records/b4c136ff-165b-4ffc-807a-99757ab5d7a1.json'),'utf8'));
 for(const input of [original.data.characterText,'今日朝议，先呈各地文书。','下旨经略台湾，命郑芝龙赴大员办理交接','推进至大员捷报抵京','核对大员交割','确认这份交割'])await send(input);
 initial=structuredClone(world());await save('before.json',initial);assert.equal(initial.known.at(-1).kind,'settled');
 await send(first,true);await send('阁臣今日饮茶如何？');await close();close=null;close=await open();s=await (await fetch(base+'/'+s.id)).json();assert.deepEqual(world(),initial);report.reloaded=true;
 await send(second,true);assert.equal(report.calls.length,3);report.passed=true;
}catch(e){report.stop=e.message;console.log('STOP',String(e.message).slice(0,240));process.exitCode=1;}
finally{if(s?.id){try{s=await (await fetch(base+'/'+s.id)).json();}catch{}report.id=s.id;report.unchanged=!!initial&&isDeepStrictEqual(world(),initial);await save('after.json',world());}
 await close?.();await save('result.json',report);await fetch(url+'/task/stop',{method:'POST',headers}).catch(()=>{});console.log('Completed requests:',report.calls.length);}
