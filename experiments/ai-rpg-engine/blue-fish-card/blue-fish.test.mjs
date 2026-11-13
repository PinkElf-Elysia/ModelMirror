import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {systemPrompt} from './assembly.mjs';
import {start} from '../studio/server.mjs';
import {installBlueFish,blueFishOptions} from './integration.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {installCustomConfig,configFetch} from '../plugins/custom-config/host.mjs';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {modCatalog} from '../plugins/mod-panel/catalog.mjs';
test('prompt integrity, rebranding and local vendor assets',async()=>{
 const source=JSON.parse(await readFile(new URL('./resources/source.json',import.meta.url)));
 assert.equal(sha(systemPrompt()),source.systemSha256);assert.equal(source.paragraphs,550);
 assert.doesNotMatch(systemPrompt(),/知鱼|风月/);
 assert.equal(source.extractedPromptSha256,'7da1ce77ac4967819dabeabf563013005cd230b5ee1168b706bb6f11fc8d1229');
 assert.equal(source.systemSha256,'deb2b0f61b5b59faa1f0272c14656b0c22db063662090f3b5b5e30b2a972d68a');
 assert.equal(source.promptStatus,'unstable-intermediate');
 assert.deepEqual(source.promptRevision.removedOriginalPhysicalLines,[3,4,5,6,7,8,97]);
 assert.equal(source.promptRevision.awaitingFurtherOptimization,true);
 assert.ok(systemPrompt().includes('<大肥鱼的交流身份>'));
 assert.ok(systemPrompt().includes('<角色创作的正文呈现>'));
 assert.ok(systemPrompt().includes('自称鱼鱼、本鱼'));
 for(const path of ['index.html','app.js']){
  const text=await readFile(new URL('./public/'+path,import.meta.url),'utf8');
  assert.doesNotMatch(text,/知鱼|AAA光合作用|Rikaku\.|cg-img\.pornai|xy\.rikaku|积分/);
 }
 const vendor=JSON.parse(await readFile(new URL('./public/vendor/manifest.json',import.meta.url)));
 for(const file of ['mdi.css','sortable.min.js','materialdesignicons-webfont.woff2','SORTABLE-LICENSE.txt','MDI-LICENSE.txt'])assert.equal(sha(await readFile(new URL('./public/vendor/'+file,import.meta.url))),vendor[file].vendoredSha256??vendor[file].sha256);
 assert.equal(blueFishOptions({}),null);
 assert.equal(blueFishOptions({RPG_BLUE_FISH_CHAT_ENABLED:'true',RPG_BLUE_FISH_CHAT_CALL_LIMIT:'3'}).limit,3);
});
test('isolated Blue Fish sessions preserve complete prompt and optional config/mod assembly',async t=>{
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'离线验收',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;seen.push({base,messages});
  const raw='离线测试回复',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const directory=await mkdtemp(join(tmpdir(),'blue-fish-test-'));
 const {server}=await start({port:0,directory,autoSummaryV2:true,transcript:true,messageTools:true});
 t.after(()=>new Promise(r=>server.close(r)));
 const origin='http://127.0.0.1:'+server.address().port;installBlueFish(server,{directory,control:{enabled:true,limit:4,baseURL:'http://server:8000',serviceToken:'synthetic-blue-fish-local-test-credential',fetcher:(u,o)=>configFetch(u,o,fake)}});installCustomConfig(server,[origin],directory);
 const call=async(path,body,headers={})=>{const r=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const v=await r.json();assert.equal(r.status,path.endsWith('/sessions')&&body!==undefined?201:200,JSON.stringify(v));return v;};
 const root='/rpg-app/blue-fish/chat/api/';
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
 const html=await fetch(origin+'/rpg-app/blue-fish/?editor=1');assert.equal(html.status,200);assert.match(html.headers.get('content-security-policy'),/sandbox allow-scripts allow-downloads/);assert.doesNotMatch(html.headers.get('content-security-policy'),/allow-same-origin/);
 const font=await fetch(origin+'/rpg-app/blue-fish/vendor/materialdesignicons-webfont.woff2',{headers:{Origin:'null'}});assert.equal(font.status,200);assert.equal(font.headers.get('access-control-allow-origin'),'*');
 const privateFile=await fetch(origin+'/rpg-app/blue-fish/resources/system.txt');assert.equal(privateFile.status,404);
});
