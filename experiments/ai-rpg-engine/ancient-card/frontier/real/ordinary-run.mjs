import assert from 'node:assert/strict';
import {isDeepStrictEqual} from 'node:util';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
import {preview} from '../ordinary-preview.mjs';
import {entries} from '../../worldbook.mjs';
import {offlineNarrator} from '../transport.mjs';
import {empty} from '../../../plugins/custom-config/public/config.mjs';
import {reserve} from '../../../agent-core/actions/real/provider.mjs';
import {NativeBridgeProvider} from '../../../local-state/tools/bridge-provider.mjs';
import {visibleText} from '../../../harness/measurement.mjs';

const mode=process.argv[2];assert.ok(['real','mock'].includes(mode));
const engine=fileURLToPath(new URL('../../../',import.meta.url));
const dir=join(engine,'.rpg03-work/ordinary-map-gemini',mode==='mock'?'mock-02':'real-01');
const save=(name,x)=>writeFile(join(dir,name),JSON.stringify(x,null,2));
const url='http://127.0.0.1:19221';let origin,base;
const token=execFileSync('python',['-B',fileURLToPath(new URL('./bridge.py',import.meta.url)),'--ordinary-map',mode,'--local-auth'],{encoding:'utf8'}).trim();
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
const config=empty();config.depth=1;
config.worldbook.entries=[{...config.worldbook.defaults,id:'east-book',name:'青册',enabled:true,order:0,depth:0,keywords:['东番'],categoryId:null,regex:false,chance:100,mode:'regular',body:'御前称地点登记卷为青册。',contents:[]}];
config.worldbook.entries.push({...config.worldbook.entries[0],id:'tool-only',name:'鸡笼',keywords:['鸡笼'],body:'仅鸡笼关键词触发的测试条目'});
const first='接着写朝议。',second='东番现在各归谁？';
const report={mode,model:'google/gemini-3.8-flash',route:'google-ai-studio',calls:[],turns:[],passed:false};
let close,s,live=false,quote,selection,decoder,reserved=0,initial;
const world=()=>({known:s.frontier.known,locations:s.frontier.locations});
async function generate(packet){
 if(!live)return offlineNarrator(packet);
 const n=report.calls.length+1;assert.ok(n<=3);
 const user=packet.messages.findLast(m=>m.role==='user').content,sys=packet.messages[0].content;
 assert.equal(user,n===1?first:second);assert.deepEqual(entries.filter(e=>sys.includes(e.text)).map(e=>e.name),['明朝']);assert.ok(!sys.includes(config.worldbook.entries[1].body));assert.equal(sys.split(config.worldbook.entries[0].body).length,n===1?1:2);
 if(n===1){assert.match(sys,/御前新到记录：[\s\S]*大员.*改登为明/);}
 else{
  assert.ok(sys.includes(config.worldbook.entries[0].body));assert.ok(!sys.includes('御前新到记录：'));
  if(n===2){assert.equal(packet.messages.length,4);assert.equal(packet.messages[1].content,'阁臣今日饮茶如何？');
    assert.doesNotMatch(packet.messages.map(m=>m.content).join('\n'),/大员.{0,20}(改登为明|已归明)|鸡笼.{0,12}西班牙|各社自治/);}
  else{const prev=JSON.parse(await readFile(join(dir,'request-2.json'),'utf8'));assert.deepEqual(packet.messages.slice(0,-2),prev.messages);
    assert.equal(packet.messages.at(-1).tool_call_id,packet.messages.at(-2).tool_calls[0].id);
    assert.deepEqual(JSON.parse(packet.messages.at(-1).content).entries.map(e=>e.holder),['明','西班牙','各社自治']);assert.deepEqual(packet.tools,[]);}
 }
 const payload={sessionId:s.id,requestId:randomUUID(),selectionId:selection.selectionId,selectionRevision:selection.selectionRevision,parameters:selection.parameters,...packet};
 const {reserveMicros}=reserve(payload,quote,{max:8192,cap:.10});reserved+=reserveMicros;assert.ok(reserved<=300000);
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
  assert.equal(row.tools.length,1);assert.equal(row.tools[0].name,'query_map');assert.deepEqual(JSON.parse(row.tools[0].arguments),{node:'台湾',use:'scene'});
 }else{if(row.tools.length)throw Error('回执已给出时仍发起工具调用；停止');assert.equal(out.finishReason,'stop');assert.ok(visibleText(row.text).trim());assert.doesNotMatch(row.text,/<map_delta/);
   const prefix=row.text.split(/<details\b/i)[0].replace(/<[^>]*>/g,'').replace(/^\s*(?:【明朝】|\[明朝\])/,'');
   const names=['时间与环境','玩家状态','当前交互角色','财产状况','世界风云','关系网络','星记忆回廊','接下来'];
   const blocks=[...row.text.matchAll(/<details\b[^>]*>\s*<summary\b[^>]*>([\s\S]*?)<\/summary>([\s\S]*?)<\/details>/gi)];
   row.han=[...prefix.matchAll(/\p{Script=Han}/gu)].length;
   row.panels=blocks.map(b=>b[1].replace(/<[^>]*>/g,''));
   row.panelsComplete=blocks.length===8&&blocks.every((b,i)=>b[1].includes(names[i])&&visibleText(b[2]).trim());
   if(row.firstVisibleMs!==null && /^\s*[\p{Script=Han}“「]/u.test(row.text))row.proseFirstMs=row.firstVisibleMs;
   await save('result.json',report);
   if(n===1&&(!(row.han>=1200&&row.han<=1500)||!row.panelsComplete))throw Error('首轮正文篇幅或六组面板未满足验收，停止后续派发');}
 return out;
}
async function send(input,real=false){
 live=real;
 const route=base+'/'+s.id;
 const memory=await (await fetch(route+'/memory-palace')).json();
 const count=s.turns.length;
 const request={operationId:randomUUID(),input,expectedSessionRevision:s.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision};
 const r=await fetch(route+'/send',{method:'POST',headers:{origin,'Content-Type':'application/json','X-Rpg-Custom-Config':'1'},body:JSON.stringify({profiles:[config],request})});
 if(!r.ok)throw Error((await r.json()).error);
 s=await r.json();
 if(s.turns.length!==count+1)throw Error(report.halt||s.operation?.error||'回合未采用');
 report.turns.push({input,source:real?mode:'local',adopted:s.turns.at(-1).raw,rev:s.revision});
 if(initial)assert.deepEqual(world(),initial);await save('result.json',report);
}
async function open(){
 const host=await preview(join(dir,'saves'),0,true,async packet=>{
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
 const original=JSON.parse(await readFile(process.argv[3],'utf8'));
 const opening=original.turns[0].input;
 assert.match(opening,/【帝王】崇祯帝朱由检/);
 for(const input of [opening,'下旨经略台湾，命郑芝龙赴大员办理交接','推进至大员捷报抵京','核对大员交割','确认这份交割'])await send(input);
 initial=structuredClone(world());await save('before.json',initial);assert.equal(initial.known.at(-1).kind,'settled');
 await send(first,true);await send('阁臣今日饮茶如何？');await close();close=null;close=await open();s=await (await fetch(base+'/'+s.id)).json();assert.deepEqual(world(),initial);report.reloaded=true;
 await send(second,true);assert.equal(report.calls.length,3);report.passed=true;
}catch(e){report.stop=e.message;console.log('STOP',String(e.message).slice(0,240));process.exitCode=1;}
finally{if(s?.id){try{s=await (await fetch(base+'/'+s.id)).json();}catch{}report.id=s.id;report.unchanged=!!initial&&isDeepStrictEqual(world(),initial);await save('after.json',world());}
 await close?.();await save('result.json',report);await fetch(url+'/task/stop',{method:'POST',headers}).catch(()=>{});console.log('Completed requests:',report.calls.length);}
