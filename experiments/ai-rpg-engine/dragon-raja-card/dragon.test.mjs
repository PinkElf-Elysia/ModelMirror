import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {Script} from 'node:vm';
import {assemble} from './assembly.mjs';
import {render} from './build.mjs';
import {configFetch,installCustomConfig} from '../plugins/custom-config/host.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {start,archiveChatOptions} from '../studio/server.mjs';
const file=name=>readFile(new URL(name,import.meta.url));
const hash=value=>createHash('sha256').update(value).digest('hex');
test('reviewed prompt, source page and local artwork retain their recorded hashes',async()=>{
 const manifest=JSON.parse(await file('resources/MANIFEST.json'));
 const prompt=JSON.parse(await file('resources/prompts.json')).system;
 assert.equal(hash(prompt),manifest.system.sha256);
 assert.equal([...prompt].length,manifest.system.unicodeCharacters);
 assert.equal(hash(await file('resources/original.html')),manifest.frontend.sha256);
 for(const asset of JSON.parse(await file('resources/ASSETS.json'))){
  const bytes=await file('public/'+asset.file);assert.equal(hash(bytes),asset.sha256);assert.equal(bytes.length,asset.bytes);
 }
 const history=[{role:'user',content:'第一轮'},{role:'assistant',content:'原始答复'}];
 const result=assemble({input:'第二轮',history});
 assert.deepEqual(result.messages,[{role:'system',content:prompt},...history,{role:'user',content:'第二轮'}]);
 result.messages[1].content='副本';assert.equal(history[0].content,'第一轮');
 assert.throws(()=>assemble({input:''}));assert.throws(()=>assemble({input:'开始',history:history.slice(0,1)}));
});
test('local adapter is reproducible, has no remote dependencies and contains valid scripts',async()=>{
 const html=await render();assert.equal((await file('public/index.html')).toString(),html);
 assert(!/https?:\/\//.test(html));assert(html.includes('dragon-raja-opening'));
 for(const [,script] of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new Script(script);
});
test('Dragon control is opt-in and independently budgeted',()=>{
 assert.equal(archiveChatOptions({}).archiveControls['dragon-raja'],null);
 const options=archiveChatOptions({RPG_DRAGON_CHAT_ENABLED:'true',RPG_DRAGON_CHAT_CALL_LIMIT:'2',RPG_CONTROL_URL:'http://fixture',RPG_S2S_TOKEN:'offline-fixture'});
 assert.equal(options.archiveControls['dragon-raja'].limit,2);
 assert.equal(options.archiveControls.ancient,null);
});
test('card assets, empty opening, two offline turns and restart preserve an isolated session',async()=>{
 const directory=await mkdtemp(join(tmpdir(),'dragon-card-'));
 let host=await start({port:0,directory,autoSummaryV2:true});
 let origin='http://127.0.0.1:'+host.server.address().port;
 const root='/rpg-app/dragon-raja/chat/api/';
 async function request(path,body){return fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});}
 async function api(path,body){const response=await request(root+path,body);const data=await response.json();assert(response.ok,JSON.stringify(data));return data;}
 try{
  for(const [path,type] of [['','text/html'],['background.jpg','image/jpeg'],['awakening.gif','image/gif']]){
   const response=await request('/rpg-app/dragon-raja/'+path);assert.equal(response.status,200);assert(response.headers.get('content-type').startsWith(type));
   if(!path)assert(response.headers.get('content-security-policy').includes('sandbox allow-scripts'));
  }
  assert.equal((await request('/rpg-app/dragon-raja/resources/prompts.json')).status,404);
  const session=await api('sessions',{opening:''});assert.equal(session.turns.length,0);assert.equal(session.characterText,'');
  const path='sessions/'+session.id;
  for(const input of ['姓名：林澈；收到卡塞尔学院的面试邀请。','确认面试时间，并整理申请材料。']){
   const memory=await api(path+'/memory-palace');
   await api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:memory.sessionRevision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input});
  }
  const saved=await api(path);assert.equal(saved.turns.length,2);
  assert.equal((await request('/rpg-app/ancient/chat/api/sessions/'+session.id)).status,404);
  await new Promise(resolve=>host.server.close(resolve));
  // Host close releases persistent plugin ownership asynchronously.
  for(let i=0;i<50;i++){
   try{host=await start({port:0,directory,autoSummaryV2:true});break;}
   catch(error){if(!/LOCK|OWNER/.test(error.message)||i===49)throw error;await new Promise(resolve=>setTimeout(resolve,20));}
  }
  origin='http://127.0.0.1:'+host.server.address().port;
  const restored=await api(path);assert.deepEqual(restored.turns,saved.turns);assert.equal(restored.revision,saved.revision);
 }finally{await new Promise(resolve=>host.server.close(resolve));}
});

test('controlled fixture preserves the full prompt and history while toggling a common Mod',async t=>{
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{name:'fixture',model,parameters,available:true,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;
  seen.push({base,messages});
  const raw='已核对面试材料。',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const directory=await mkdtemp(join(tmpdir(),'dragon-control-'));
 const host=await start({port:0,directory,archiveControls:{'dragon-raja':{enabled:true,limit:2,baseURL:'http://127.0.0.1:8000',serviceToken:'synthetic-dragon-test-only-credential',fetcher:(u,o)=>configFetch(u,o,fake)}}});
 t.after(()=>new Promise(resolve=>host.server.close(resolve)));
 const origin='http://127.0.0.1:'+host.server.address().port;
 installCustomConfig(host.server,[origin],directory);
 const root='/rpg-app/dragon-raja/chat/api/';
 const post=async(path,body,headers={})=>{const r=await fetch(origin+root+path,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;};
 const catalog=await (await fetch(origin+'/rpg-app/mod-panel/catalog')).json();
 const mod=catalog.items.find(item=>item.id==='common.rationality.v1');
 const session=await post('sessions',{opening:''}),path='sessions/'+session.id;
 assert.equal(seen.length,0,'creation and catalog must never dispatch');
 for(const [i,input] of ['核对面试材料。','确认到达时间。'].entries()){
  const memory=await (await fetch(origin+root+path+'/memory-palace')).json();
  const request={operationId:randomUUID(),expectedSessionRevision:memory.sessionRevision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input};
  const result=await post(path+'/send',i?request:{modIds:[mod.id],request},i?{}:{'X-Rpg-Mod-Panel':'1'});
  assert.equal(result.turns.length,i+1);assert.equal(result.turns.at(-1).raw,'已核对面试材料。');
  const system=assemble({input}).messages[0].content;
  assert.equal(seen[i].base.messages[0].content,system);
  assert.equal(seen[i].messages[0].content,system+(i?'':'\n\n'+mod.text));
  assert.equal(seen[i].messages.at(-1).content,input);
 }
 assert.deepEqual(seen[1].messages.slice(1,-1),[{role:'user',content:'核对面试材料。'},{role:'assistant',content:'已核对面试材料。'}]);
 assert.equal(seen.length,2);
});
