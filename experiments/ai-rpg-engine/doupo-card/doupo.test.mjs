import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {systemPrompt,selectEntries,assembleTurn} from './assembly.mjs';
import {start} from '../studio/server.mjs';
import {installDoupo,doupoOptions} from './integration.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {installCustomConfig,configFetch} from '../plugins/custom-config/host.mjs';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {modCatalog} from '../plugins/mod-panel/catalog.mjs';
test('isolated Doupo sessions preserve complete prompt and optional config/mod assembly',async t=>{
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'离线验收',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;seen.push({base,messages});
  const raw='离线测试回复',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const directory=await mkdtemp(join(tmpdir(),'doupo-test-'));
 const {server}=await start({port:0,directory,autoSummaryV2:true,transcript:true,messageTools:true});
 t.after(()=>new Promise(r=>server.close(r)));
 const origin='http://127.0.0.1:'+server.address().port;installDoupo(server,{directory,control:{enabled:true,limit:4,baseURL:'http://server:8000',serviceToken:'synthetic-doupo-local-test-credential',fetcher:(u,o)=>configFetch(u,o,fake)}});installCustomConfig(server,[origin],directory);
 const call=async(path,body,headers={})=>{const r=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const v=await r.json();assert.equal(r.status,path.endsWith('/sessions')&&body!==undefined?201:200,JSON.stringify(v));return v;};
 const root='/rpg-app/doupo/chat/api/';
 let s=await call(root+'sessions',{opening:''});const path=root+'sessions/'+s.id;
 assert.equal(seen.length,0);assert.equal(s.turns.length,0);assert.ok(s.runtime.compatible);
 const registry=await call(root+'plugins');for(const id of ['rpg.memory-palace','rpg.rolling-summary','rpg.transcript','rpg.model-selector'])assert.ok(registry.plugins.some(p=>p.id===id));
 const common=(await modCatalog()).find(m=>m.id==='common.rationality.v1');
 for(const i of [0,1]){
  const state=await call(path+'/memory-palace');
  const request={operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input:i?'继续修炼':'【所选篇章】乌坦城少年篇，我想拜访冰皇'};
  const config={...empty(),system:'本轮使用简洁文字',prefix:'前置样例',suffix:'后置样例'};
  s=await call(path+'/send',i?request:{request,profiles:[config],modIds:[common.id]},i?{}:{'X-Rpg-Custom-Config':'1','X-Rpg-Mod-Panel':'1'});
  assert.equal(s.turns.length,i+1);assert.ok(seen[i].base.messages[0].content.startsWith(systemPrompt()+'\n\n'));assert.equal(seen[i].base.messages[0].content.slice(systemPrompt().length).includes('【海波东】人物卡'),i===0);
  if(!i){assert.ok(seen[i].messages[0].content.startsWith(systemPrompt()));assert.ok(seen[i].messages[0].content.endsWith(common.text));assert.ok(seen[i].messages.at(-1).content.includes('前置样例'));}
  else {assert.deepEqual(seen[i].messages,seen[i].base.messages);assert.equal(seen[i].messages.length,4);}
 }
 const other=await call('/rpg-app/ancient/chat/api/sessions');assert.deepEqual(other,[]);
 const html=await fetch(origin+'/rpg-app/doupo/?editor=1');assert.equal(html.status,200);assert.match(html.headers.get('content-security-policy'),/sandbox allow-scripts/);assert.doesNotMatch(html.headers.get('content-security-policy'),/allow-same-origin/);
 const art=await fetch(origin+'/rpg-app/doupo/intro.jpg');assert.equal(art.status,200);assert.equal(art.headers.get('content-type'),'image/jpeg');
 const denied=await fetch(origin+path+'/send',{method:'POST',headers:{Origin:'https://outside.invalid','Content-Type':'application/json'},body:'{}'});assert.equal(denied.status,403);assert.equal(seen.length,2);
 const privateFile=await fetch(origin+'/rpg-app/doupo/resources/system.txt');assert.equal(privateFile.status,404);
});

test('chapter continuity, aliases, nonrecursive matching and bounded injection',()=>{
 const titles=(h,i)=>selectEntries(h,i).map(e=>e.title);
 assert.deepEqual(titles([],'我四处走走'),['篇章系统']);
 assert.deepEqual(titles([],'迦南学院篇（含黑角域）'),['篇章系统','迦南学院篇']);
 assert.deepEqual(titles([],'见见药尘和彩鳞'),['篇章系统','药老','美杜莎']);
 const history=[{role:'user',content:'乌坦城少年篇，我要寻找冰皇'},{role:'assistant',content:'山中有雾。'}];
 assert.deepEqual(titles(history,'进入中州丹会篇'),['篇章系统','中州丹会篇']);
 history.push({role:'user',content:'继续'},{role:'assistant',content:'雨停了。'});
 assert.deepEqual(titles(history,'继续'),['篇章系统','乌坦城少年篇']);
});

test('only current input and previous narrative trigger conditional entries',()=>{
 const titles=(history,input)=>selectEntries(history,input).map(e=>e.title);
 const history=[{role:'user',content:'中州丹会篇。我来找冰皇。'}, {role:'assistant',content:[
  '<!-- 曹颖 --><details open><summary>质量管控</summary>慕骨老人<details>萧炎</details>曹颖</details>',
  '<p>他提起<font title="曹颖">药</font>尘。</p>',
  '<details><summary>状态栏</summary>斗气属性，加玛帝国篇</details>',
  '<div class="status-panel">曹颖</div><details>美杜莎'
 ].join('')}];
 assert.deepEqual(titles(history,'我继续等候'),['篇章系统','中州丹会篇','药老']);
 history.push({role:'user',content:'我继续等候'}, {role:'assistant',content:'<p>进入迦南学院篇。</p><details>魂殿大战篇</details>'});
 assert.deepEqual(titles(history,'继续'),['篇章系统','迦南学院篇']);
 assert.deepEqual(titles(history,'现在进入乌坦城少年篇'),['篇章系统','乌坦城少年篇']);
 history.push({role:'user',content:'继续'}, {role:'assistant',content:'<details open><summary>时间线</summary>双帝之战篇、药尘</details>'});
 assert.deepEqual(titles(history,'继续'),['篇章系统','迦南学院篇']);
});

test('permanent index covers configured keys without recursively injecting bodies',async()=>{
 const resource=name=>readFile(new URL('./resources/'+name,import.meta.url),'utf8');
 const entries=JSON.parse(await resource('worldbook.json'));
 const index=await resource('worldbook-index.txt');
 const system=systemPrompt();
 assert.equal(system,(await resource('system.txt'))+'\n\n'+index);
 for(const e of entries.filter(e=>e.kind!=='constant')){
   const row=index.split('\n').find(line=>line.split('｜')[0].split('／').includes(e.title));
   assert.ok(row,e.title);
   assert.ok(row.split('｜')[1]?.trim(),e.title);
   for(const key of e.keywords)assert.ok(row.includes(key),key);
 }
 const constant=entries.find(e=>e.kind==='constant');
 const first=assembleTurn(system,[],'我在城门口等候。');
 assert.equal(first.messages[0].content,system+'\n\n'+constant.body);
 const reply={role:'assistant',content:'乌坦城少年篇。冰皇走到你面前。'};
 const next=assembleTurn(system,[first.messages[1],reply],'我点了点头。');
 assert.deepEqual(next.messages[0].content,[system,constant.body,...['乌坦城少年篇','海波东'].map(title=>entries.find(e=>e.title===title).body)].join('\n\n'));
});
