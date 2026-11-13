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

test('frozen author prompt, full original builder script and local assets remain intact',async()=>{
 const manifest=JSON.parse(await read('resources/MANIFEST.json'));
 const system=(await read('resources/system.txt')).toString();
 assert.equal(sha(system),manifest.system.sha256);
 assert.equal(Array.from(system).length,29062);
 const original=(await read('resources/original.html')).toString(),html=(await read('public/index.html')).toString();
 assert.equal(sha(original),manifest.sourceHtml.sha256);
 for(const [path,hash] of Object.entries(manifest.files))assert.equal(sha(await read(path)),hash,path);
 assert.equal(html.match(/<script>([\s\S]*?)<\/script>/)[1],original.match(/<script>([\s\S]*?)<\/script>/)[1]);
 const originalStyle=original.match(/<style>([\s\S]*?)<\/style>/)[1];
 const localStyle=html.match(/<style>([\s\S]*?)<\/style>/)[1].split('/* Local host:')[0];
 const expectedStyle=originalStyle.replace(/^@import[^\n]*$/m,"@import url('./fonts.css');")
  .replace('https://pic1.imgdb.cn/i/033zgVTXCoKk1QKtrygy0v.jpg','./background.jpg')
  .replace('https://pic1.imgdb.cn/i/033zgYzFxQB3mnFyFCpGmf.jpg','./opening.jpg');
 assert.equal(localStyle,expectedStyle);
 const body=html.slice(html.indexOf('<body>'),html.indexOf('<script>'));
 const originalBody=original.slice(original.indexOf('<body>'),original.indexOf('<script>'));
 assert.equal(body.replace('\n<button class="copy-btn" id="open-chat" hidden>以此角色进入聊天</button>',''),originalBody);

 assert.equal(assemble({input:'继续'}).messages[0].content,system);
 const history=[{role:'user',content:'开局'},{role:'assistant',content:'故事'}],saved=structuredClone(history);
 const result=assemble({input:'继续',history});assert.deepEqual(history,saved);assert.notEqual(result.messages[1],history[0]);
 assert.throws(()=>assemble({input:' '}));assert.throws(()=>assemble({input:'继续',history:[history[0]]}));
 assert.throws(()=>assemble({input:'继续',history:[history[1],history[0]]}));
 assert.equal(archiveChatOptions({}).archiveControls.medieval,null);
 assert.equal(archiveChatOptions({RPG_MEDIEVAL_CHAT_ENABLED:'true',RPG_MEDIEVAL_CHAT_CALL_LIMIT:'2'}).archiveControls.medieval.limit,2);
 assert.doesNotMatch(html,/https:\/\/(?:pic1.imgdb.cn|fonts.googleapis.com)/);
 assert.match(html,/prefers-reduced-motion/);
});

test('only allowlisted public assets bypass opaque-frame origin checks',async t=>{
 const work=new URL('../.rpg04-work/medieval-tests/',import.meta.url);await mkdir(work,{recursive:true});
 const directory=await mkdtemp(join(fileURLToPath(work),'assets-'));
 const {server}=await start({port:0,directory});t.after(()=>new Promise(ok=>server.close(ok)));
 const origin='http://127.0.0.1:'+server.address().port,root=origin+'/rpg-app/medieval/';
 for(const path of ['background.jpg','opening.jpg','fonts.css',...Array.from({length:9},(_,i)=>'fonts/font-'+i+'.woff2')]){
  const r=await fetch(root+path,{headers:{Origin:'null','Sec-Fetch-Site':'cross-site'}});
  assert.equal(r.status,200,path);assert.equal(r.headers.get('access-control-allow-origin'),'*');assert.ok((await r.arrayBuffer()).byteLength>100);
 }
 for(const path of ['chat/api/sessions','resources/system.txt','fonts/no-such-file.woff2','']){
  const r=await fetch(root+path,{headers:{Origin:'null','Sec-Fetch-Site':'cross-site'}});assert.equal(r.status,403,path);
 }
 assert.equal((await fetch(root+'resources/system.txt')).status,404);
 const html=await fetch(root);assert.match(html.headers.get('content-security-policy'),/connect-src 'none'/);
});

test('new transport matches reviewed transport except the explicit card identity',async()=>{
 for(const name of ['controlled-provider.mjs','memory-budget.mjs','memory-task-provider.mjs']){
  const previous=await readFile(new URL('../jinyong-card/transport/'+name,import.meta.url),'utf8');
  assert.equal((await read('transport/'+name)).toString(),previous.replaceAll('\r\n','\n').replaceAll('jinyong','medieval'));
 }
});
test('Medieval route preserves system, two turns, idempotence, common Mod and card isolation without external calls',async t=>{
 const work=new URL('../.rpg04-work/medieval-tests/',import.meta.url);await mkdir(work,{recursive:true});
 const directory=await mkdtemp(join(fileURLToPath(work),'run-'));
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'offline fixture',parameters,available:true,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;
  seen.push({base,messages});const raw='城门前，茶摊老板指了指进城的道路。',sse='fixture';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const host=await start({port:0,directory,archiveControls:{medieval:{baseURL:'http://server:8000',serviceToken:'synthetic-medieval-fixture-only-not-a-real-token',enabled:true,limit:3,fetcher:(u,o)=>configFetch(u,o,fake)}}});
 t.after(()=>new Promise(ok=>host.server.close(ok)));
 const origin='http://127.0.0.1:'+host.server.address().port;installCustomConfig(host.server,[origin],directory);
 async function api(path,body,headers={}){const response=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const value=await response.json();assert.equal(response.ok,true,JSON.stringify(value));return value;}
 const root='/rpg-app/medieval/chat/api/';
 const page=await fetch(origin+'/rpg-app/medieval/');assert.equal(page.status,200);assert.match(page.headers.get('content-security-policy'),/sandbox allow-scripts/);
 assert.match(await page.text(),/中世纪真实人生/);
 assert.equal((await fetch(origin+'/rpg-app/medieval/background.jpg')).headers.get('content-type'),'image/jpeg');
 assert.equal((await fetch(origin+'/rpg-app/medieval/resources/system.txt')).status,404);
 let session=await api(root+'sessions',{opening:''}),path=root+'sessions/'+session.id;
 assert.equal(seen.length,0);assert.equal(session.characterText,'');assert.equal(session.runtime.compatible,true);
 for(const [i,input] of ['姓名：马丁\n14世纪 · 英格兰\n我走向城门。','向茶摊老板问路。'].entries()){
  const memory=await api(path+'/memory-palace');
  const request={operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input};
  const headers=i?{'X-Rpg-Mod-Panel':'1'}:{},body=i?{modIds:['common.rationality.v1'],request}:request;
  session=await api(path+'/send',body,headers);assert.equal(session.turns.at(-1).input,input);
  assert.equal(seen.length,i+1);await api(path+'/send',body,headers);assert.equal(seen.length,i+1);
 }
 const original=assemble({input:'样例'}).messages[0].content;
 assert.equal(seen[0].messages[0].content,original);assert.equal(seen[1].base.messages[0].content,original);
 const [mod]=await resolveMods('medieval',['common.rationality.v1']);assert.equal(seen[1].messages[0].content,original+'\n\n'+mod.text);
 assert.deepEqual(seen[1].messages.slice(1).map(m=>m.role),['user','assistant','user']);
 assert.equal((await api(path)).turns.length,2);assert.equal((await api(path)).runtime.compatible,true);
 const others=await api('/rpg-app/ancient/chat/api/sessions');assert.equal(others.length,0);
 assert.ok((await api(root+'plugins')).plugins.length>0);
});


test('offline chat creates an empty draft and restores a completed turn without a provider',async t=>{
 const work=new URL('../.rpg04-work/medieval-tests/',import.meta.url);await mkdir(work,{recursive:true});
 const directory=await mkdtemp(join(fileURLToPath(work),'offline-'));
 let host=await start({port:0,directory,enabled:false});t.after(async()=>{await host.plugins.close();await new Promise(ok=>host.server.close(ok));});
 const origin='http://127.0.0.1:'+host.server.address().port,root=origin+'/rpg-app/medieval/chat/api/sessions';
 async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const v=await r.json();assert.equal(r.ok,true,JSON.stringify(v));return v;}
 const session=await api(root,{opening:''});assert.equal(session.mode,'offline');assert.equal(session.turns.length,0);
 const memory=await api(root+'/'+session.id+'/memory-palace');
 const completed=await api(root+'/'+session.id+'/send',{operationId:randomUUID(),input:'我到茶摊问路。',expectedSessionRevision:session.revision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision});
 assert.equal(completed.turns.length,1);assert.match(completed.turns[0].raw,/离线演示/);
 assert.equal((await api(root+'/'+session.id)).runtime.compatible,true);
 await host.plugins.close();await new Promise(ok=>host.server.close(ok));
 host=await start({port:0,directory,enabled:false});
 const resumed=await fetch('http://127.0.0.1:'+host.server.address().port+'/rpg-app/medieval/chat/api/sessions/'+session.id);
 assert.equal(resumed.status,200);const restored=await resumed.json();
 assert.equal(restored.runtime.compatible,true);assert.deepEqual(restored.turns,completed.turns);
});



test('Medieval illustration rewrite uses its own card budget with a simulated provider',async()=>{
 const {createIllustrationWriter}=await import('../studio/illustration-rewrite.mjs');
 const work=new URL('../.rpg04-work/medieval-tests/',import.meta.url);await mkdir(work,{recursive:true});
 const directory=await mkdtemp(join(fileURLToPath(work),'rewrite-'));
 const {parametersFor}=await import('../studio/model-parameters.mjs');
 const {models}=await import('../studio/provider.mjs');const model=models.earth.model;let calls=0;
 const writer=await createIllustrationWriter(directory,{enabled:true,limit:1,baseURL:'http://server:8000/',serviceToken:'synthetic-illustration-fixture-not-a-secret',fetcher:async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'fixture',available:true,selectionId:'fixture',selectionRevision:'v1',parameters:parametersFor(model)}]});
  const b=JSON.parse(options.body);calls++;const raw='中世纪铁匠铺，一位青衣旅人向店家问路。',sse='fixture';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:b.sessionId,requestId:b.requestId,selectionId:b.selectionId,selectionRevision:b.selectionRevision,requestedModel:model,actualModel:model,parameters:b.parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(b)),rawHash:sha(raw),sseHash:sha(sse)}});
 }});
 assert.equal((await writer.status('medieval')).enabled,true);
 assert.match(await writer.rewrite('medieval',{sessionId:randomUUID(),requestId:randomUUID(),source:'茶摊问路',focus:'回合剧情',beforeDispatch:start=>({response:start()})}),/中世纪铁匠铺/);
 assert.equal(calls,1);assert.equal((await writer.status('medieval')).remaining,0);assert.equal((await writer.status('ancient')).remaining,1);
});
