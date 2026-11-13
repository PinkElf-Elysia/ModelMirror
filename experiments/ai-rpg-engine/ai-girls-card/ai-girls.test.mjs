import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {systemPrompt} from './assembly.mjs';
import {start} from '../studio/server.mjs';
import {installAiGirls,aiGirlsOptions} from './integration.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {installCustomConfig,configFetch} from '../plugins/custom-config/host.mjs';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {modCatalog} from '../plugins/mod-panel/catalog.mjs';
test('frozen author prompt, local media, targeted frontend removals and guarded opening bridge',async()=>{
 const source=JSON.parse(await readFile(new URL('./resources/source.json',import.meta.url)));
 assert.equal(sha(systemPrompt()),'e308eec7f408333768596a9f6de27686716ea74f4508538a0b9bc6c583d95056');
 assert.equal(source.paragraphCount,886);
 const original=await readFile(new URL('./resources/original.html',import.meta.url),'utf8');assert.equal(sha(original),source.frontendSha256);
 const html=await readFile(new URL('./public/index.html',import.meta.url),'utf8');
 assert.doesNotMatch(html,/作者的话|未知艺术家|https:\/\//);
 assert.match(html,/<audio[^>]+loop/);assert.match(html,/id="fStart"/);assert.match(html,/id="btnSum"/);assert.match(html,/id="btnClear"/);
 assert.equal((html.match(/class="cardHead"/g)||[]).length,8);
 const assets=JSON.parse(await readFile(new URL('./resources/assets.json',import.meta.url)));assert.equal(assets.length,12);
 for(const asset of assets)assert.equal(sha(await readFile(new URL('./public/'+asset.file,import.meta.url))),asset.sha256);
 const bridge=await readFile(new URL('./public/bridge.js',import.meta.url),'utf8');
 assert.match(bridge,/type:'ai-girls-opening'/);assert.match(bridge,/new URL\(location.href\).origin/);assert.doesNotMatch(bridge,/fetch\(|XMLHttpRequest/);
 assert.equal(aiGirlsOptions({}),null);assert.equal(aiGirlsOptions({RPG_AI_GIRLS_CHAT_ENABLED:'true',RPG_AI_GIRLS_CHAT_CALL_LIMIT:'3'}).limit,3);
});
test('isolated AI girls sessions preserve complete prompt and optional config/mod assembly',async t=>{
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'离线验收',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;seen.push({base,messages});
  const raw='离线测试回复',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const directory=await mkdtemp(join(tmpdir(),'ai-girls-test-'));
 const {server}=await start({port:0,directory,autoSummaryV2:true,transcript:true,messageTools:true});
 t.after(()=>new Promise(r=>server.close(r)));
 const origin='http://127.0.0.1:'+server.address().port;installAiGirls(server,{directory,control:{enabled:true,limit:4,baseURL:'http://server:8000',serviceToken:'synthetic-ai-girls-local-test-credential',fetcher:(u,o)=>configFetch(u,o,fake)}});installCustomConfig(server,[origin],directory);
 const call=async(path,body,headers={})=>{const r=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const v=await r.json();assert.equal(r.status,path.endsWith('/sessions')&&body!==undefined?201:200,JSON.stringify(v));return v;};
 const root='/rpg-app/ai-girls/chat/api/';
 let s=await call(root+'sessions',{opening:''});const path=root+'sessions/'+s.id;
 assert.equal(seen.length,0);assert.equal(s.turns.length,0);assert.ok(s.runtime.compatible);
 const registry=await call(root+'plugins');for(const id of ['rpg.memory-palace','rpg.rolling-summary','rpg.transcript','rpg.model-selector'])assert.ok(registry.plugins.some(p=>p.id===id));
 const common=(await modCatalog()).find(m=>m.id==='common.rationality.v1');
 for(const i of [0,1]){
  const state=await call(path+'/memory-palace');
  const request={operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input:i?'继续润色':'为书店写一段温馨简介'};
  const config={...empty(),system:'本轮使用简洁文字',prefix:'前置样例',suffix:'后置样例'};
  s=await call(path+'/send',i?request:{request,profiles:[config],modIds:[common.id]},i?{}:{'X-Rpg-Custom-Config':'1','X-Rpg-Mod-Panel':'1'});
  assert.equal(s.turns.length,i+1);assert.equal(seen[i].base.messages[0].content,systemPrompt());
  if(!i){assert.ok(seen[i].messages[0].content.startsWith(systemPrompt()));assert.ok(seen[i].messages[0].content.endsWith(common.text));assert.ok(seen[i].messages.at(-1).content.includes('前置样例'));}
  else {assert.deepEqual(seen[i].messages,seen[i].base.messages);assert.equal(seen[i].messages.length,4);}
 }
 const other=await call('/rpg-app/ancient/chat/api/sessions');assert.deepEqual(other,[]);
 const html=await fetch(origin+'/rpg-app/ai-girls/?editor=1');assert.equal(html.status,200);assert.match(html.headers.get('content-security-policy'),/sandbox allow-scripts/);assert.doesNotMatch(html.headers.get('content-security-policy'),/allow-same-origin/);
 const audio=await fetch(origin+'/rpg-app/ai-girls/assets/background.mp3',{headers:{Origin:'null',Range:'bytes=0-127'}});assert.equal(audio.status,206);assert.equal((await audio.arrayBuffer()).byteLength,128);assert.equal(audio.headers.get('access-control-allow-origin'),'*');
 const badRange=await fetch(origin+'/rpg-app/ai-girls/assets/background.mp3',{headers:{Range:'bytes=999999999-'}});assert.equal(badRange.status,416);
 const denied=await fetch(origin+path+'/send',{method:'POST',headers:{Origin:'https://outside.invalid','Content-Type':'application/json'},body:'{}'});assert.equal(denied.status,403);assert.equal(seen.length,2);
 const privateFile=await fetch(origin+'/rpg-app/ai-girls/resources/system.txt');assert.equal(privateFile.status,404);
});
