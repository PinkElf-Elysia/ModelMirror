import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdir,mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {assemble} from './assembly.mjs';
import {start,archiveChatOptions} from '../studio/server.mjs';
import {sha,canonical} from '../plugins/catalog.mjs';
import {configFetch,installCustomConfig} from '../plugins/custom-config/host.mjs';
import {resolveMods} from '../plugins/mod-panel/catalog.mjs';
const read=name=>readFile(new URL(name,import.meta.url));

test('author prompt and original UI are hash-bound, with only documented local adaptations',async()=>{
 const manifest=JSON.parse(await read('resources/MANIFEST.json'));
 const system=(await read('resources/system.txt')).toString();
 assert.equal(sha(system),manifest.system.sha256);
 assert.equal(sha(await read('resources/original.html')),manifest.sourceHtml.sha256);
 for(const [path,hash] of Object.entries(manifest.files))assert.equal(sha(await read(path)),hash,path);
 const history=[{role:'user',content:'第一步'},{role:'assistant',content:'原文回复'}];
 const frozen=structuredClone(history),result=assemble({input:'第二步',history});
 assert.equal(result.messages[0].content,system);assert.deepEqual(history,frozen);
 assert.equal(result.messages.length,4);assert.notEqual(result.messages[1],history[0]);
 assert.throws(()=>assemble({input:' '}));assert.throws(()=>assemble({input:'继续',history:[history[0]]}));
 assert.throws(()=>assemble({input:'继续',history:[history[1],history[0]]}));
 const html=(await read('public/index.html')).toString();
 const restored=html.replace('./background.jpg','https://pic1.imgdb.cn/item/6a18877b105bdfb81151f91b.jpg')
  .replace('*{margin:0;padding:0;box-sizing:border-box;}\n','*{margin:0;padding:0;box-sizing:border-box;}\n'+"@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;700;900&display=swap');")
  .replace('\n        <button class="copy-btn" id="open-chat" hidden>以此人物帖进入聊天</button>','')
  .replace(/<script>\nif\(new URLSearchParams[\s\S]*?<\/script>\n<\/body>/,'</body>');
 assert.equal(restored,(await read('resources/original.html')).toString());
 assert.equal(archiveChatOptions({}).archiveControls.jinyong,null);
 assert.equal(archiveChatOptions({RPG_JINYONG_CHAT_ENABLED:'true',RPG_JINYONG_CHAT_CALL_LIMIT:'2'}).archiveControls.jinyong.limit,2);
});

test('Jinyong route preserves system, two turns, idempotence, common Mod and card isolation without external calls',async t=>{
 const work=new URL('../.rpg04-work/jinyong-tests/',import.meta.url);await mkdir(work,{recursive:true});
 const directory=await mkdtemp(join(fileURLToPath(work),'run-'));
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'offline fixture',parameters,available:true,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;
  seen.push({base,messages});const raw='城门前，茶摊老板指了指进城的道路。',sse='fixture';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const host=await start({port:0,directory,archiveControls:{jinyong:{baseURL:'http://server:8000',serviceToken:'synthetic-jinyong-fixture-only-not-a-real-token',enabled:true,limit:3,fetcher:(u,o)=>configFetch(u,o,fake)}}});
 t.after(()=>new Promise(ok=>host.server.close(ok)));
 const origin='http://127.0.0.1:'+host.server.address().port;installCustomConfig(host.server,[origin],directory);
 async function api(path,body,headers={}){const response=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const value=await response.json();assert.equal(response.ok,true,JSON.stringify(value));return value;}
 const root='/rpg-app/jinyong/chat/api/';
 const page=await fetch(origin+'/rpg-app/jinyong/');assert.equal(page.status,200);assert.match(page.headers.get('content-security-policy'),/sandbox allow-scripts/);
 assert.match(await page.text(),/金庸群侠世界构建者/);
 assert.equal((await fetch(origin+'/rpg-app/jinyong/background.jpg')).headers.get('content-type'),'image/jpeg');
 assert.equal((await fetch(origin+'/rpg-app/jinyong/resources/system.txt')).status,404);
 let session=await api(root+'sessions',{opening:''}),path=root+'sessions/'+session.id;
 assert.equal(seen.length,0);assert.equal(session.characterText,'');assert.equal(session.runtime.compatible,true);
 for(const [i,input] of ['姓名：沈砚\n单一作品线 · 天龙八部\n我走向城门。','向茶摊老板问路。'].entries()){
  const memory=await api(path+'/memory-palace');
  const request={operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input};
  const headers=i?{'X-Rpg-Mod-Panel':'1'}:{},body=i?{modIds:['common.rationality.v1'],request}:request;
  session=await api(path+'/send',body,headers);assert.equal(session.turns.at(-1).input,input);
  assert.equal(seen.length,i+1);await api(path+'/send',body,headers);assert.equal(seen.length,i+1);
 }
 const original=assemble({input:'样例'}).messages[0].content;
 assert.equal(seen[0].messages[0].content,original);assert.equal(seen[1].base.messages[0].content,original);
 const [mod]=await resolveMods('jinyong',['common.rationality.v1']);assert.equal(seen[1].messages[0].content,original+'\n\n'+mod.text);
 assert.deepEqual(seen[1].messages.slice(1).map(m=>m.role),['user','assistant','user']);
 assert.equal((await api(path)).turns.length,2);assert.equal((await api(path)).runtime.compatible,true);
 const others=await api('/rpg-app/ancient/chat/api/sessions');assert.equal(others.length,0);
 assert.ok((await api(root+'plugins')).plugins.length>0);
});


test('offline chat creates an empty draft and restores a completed turn without a provider',async t=>{
 const work=new URL('../.rpg04-work/jinyong-tests/',import.meta.url);await mkdir(work,{recursive:true});
 const directory=await mkdtemp(join(fileURLToPath(work),'offline-'));
 const host=await start({port:0,directory,enabled:false});t.after(()=>new Promise(ok=>host.server.close(ok)));
 const origin='http://127.0.0.1:'+host.server.address().port,root=origin+'/rpg-app/jinyong/chat/api/sessions';
 async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const v=await r.json();assert.equal(r.ok,true,JSON.stringify(v));return v;}
 const session=await api(root,{opening:''});assert.equal(session.mode,'offline');assert.equal(session.turns.length,0);
 const memory=await api(root+'/'+session.id+'/memory-palace');
 const completed=await api(root+'/'+session.id+'/send',{operationId:randomUUID(),input:'我到茶摊问路。',expectedSessionRevision:session.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision});
 assert.equal(completed.turns.length,1);assert.match(completed.turns[0].raw,/离线演示/);
 assert.equal((await api(root+'/'+session.id)).runtime.compatible,true);
});


test('new-card transport retains the reviewed budget and receipt logic',async()=>{
 for(const name of ['controlled-provider.mjs','memory-budget.mjs','memory-task-provider.mjs']){
  let expected=(await readFile(new URL('../studio/'+name,import.meta.url),'utf8')).replaceAll('\r\n','\n');
  expected=expected.replaceAll("'../plugins/catalog.mjs'","'../../plugins/catalog.mjs'");
  for(const dependency of ['model-parameters.mjs','provider.mjs','branch-archive.mjs'])expected=expected.replaceAll("'./"+dependency+"'","'../../studio/"+dependency+"'");
  // Only the explicit card allowlists differ from the merged three-card baseline.
  expected=expected.replace("['earth','god','cultivation','lord-of-mysteries','douluo','dragon-raja','ancient','xingjian'].includes(cardId)","['earth','god','cultivation','lord-of-mysteries','ancient','xingjian','jinyong'].includes(cardId)");
  expected=expected.replace("['earth','god','ancient','xingjian','cultivation','lord-of-mysteries','douluo','dragon-raja'].includes(cardId)","['earth','god','ancient','xingjian','cultivation','lord-of-mysteries','jinyong'].includes(cardId)");
  expected=expected.replace('(earth|god|ancient|xingjian|cultivation|lord-of-mysteries|douluo|dragon-raja)','(earth|god|ancient|xingjian|cultivation|lord-of-mysteries|jinyong)');
  assert.equal((await read('transport/'+name)).toString().split('\n').slice(1).join('\n'),expected,name);
 }
});

test('Jinyong illustration rewrite uses its own card budget with a simulated provider',async()=>{
 const {createIllustrationWriter}=await import('../studio/illustration-rewrite.mjs');
 const work=new URL('../.rpg04-work/jinyong-tests/',import.meta.url);await mkdir(work,{recursive:true});
 const directory=await mkdtemp(join(fileURLToPath(work),'rewrite-'));
 const {parametersFor}=await import('../studio/model-parameters.mjs');
 const {models}=await import('../studio/provider.mjs');const model=models.earth.model;let calls=0;
 const writer=await createIllustrationWriter(directory,{enabled:true,limit:1,baseURL:'http://server:8000/',serviceToken:'synthetic-illustration-fixture-not-a-secret',fetcher:async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'fixture',available:true,selectionId:'fixture',selectionRevision:'v1',parameters:parametersFor(model)}]});
  const b=JSON.parse(options.body);calls++;const raw='江南茶摊，一位青衣旅人向店家问路。',sse='fixture';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
 }});
 assert.equal((await writer.status('jinyong')).enabled,true);
 assert.match(await writer.rewrite('jinyong',{sessionId:randomUUID(),requestId:randomUUID(),source:'茶摊问路',focus:'回合剧情',beforeDispatch:start=>({response:start()})}),/江南茶摊/);
 assert.equal(calls,1);assert.equal((await writer.status('jinyong')).remaining,0);assert.equal((await writer.status('ancient')).remaining,1);
});
