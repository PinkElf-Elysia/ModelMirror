import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp,mkdir} from 'node:fs/promises';
import {randomUUID,createHash} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {assemble} from './assembly.mjs';
import {start,archiveChatOptions} from '../studio/server.mjs';
import {configFetch,installCustomConfig} from '../plugins/custom-config/host.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
const read=name=>readFile(new URL(name,import.meta.url),'utf8');

test('frozen author prompt, all 46 portraits and five audio/video pairs remain intact',async()=>{
 const manifest=JSON.parse(await read('resources/MANIFEST.json'));
 const {system}=JSON.parse(await read('resources/prompts.json'));
 assert.equal(sha(system),manifest.system.sha256);
 assert.equal(sha(await read('resources/original.html')),manifest.htmlSha256);
 const messages=assemble({input:'清晨，成年魂师来到城门。',history:[{role:'user',content:'前文'},{role:'assistant',content:'回应'}]}).messages;
 assert.equal(messages[0].content,system);assert.equal(messages.length,4);
 assert.throws(()=>assemble({input:'',history:[]}));
 assert.throws(()=>assemble({input:'继续',history:[{role:'assistant',content:'孤立回复'}]}));
 const assets=JSON.parse(await read('resources/assets.json'));
 for(const ext of ['jpg','mp3','mp4'])assert.equal(assets.filter(a=>a.file.endsWith('.'+ext)).length,ext==='jpg'?46:5);
 for(const a of assets)assert.equal(createHash('sha256').update(await readFile(new URL('public/'+a.file,import.meta.url))).digest('hex'),a.sha256,a.file);
 const html=await read('public/index.html');
 for(const id of ['entryGlass','hammerSvg','orbCanvas','soulFx','leftDrawer','rightDrawer','tabWorld','tabBook','mapSvg','mapModal','boneModal','wuhunModal','previewPre','enterChatBtn'])assert.ok(html.includes('id="'+id+'"')||html.includes(".id='"+id+"'"),id);
 assert.ok(!html.includes('if(BG_MUSIC_URL)'));assert.ok(html.includes("type:'douluo-opening'"));assert.ok(html.includes('prefers-reduced-motion'));
 assert.ok(!html.includes('https://catai.wiki'));assert.ok(!html.includes('https://raw.githubusercontent.com'));assert.ok(!html.includes('https://img.wjwj.top'));
});

test('new card assets are sandboxed, media ranges work, and provider stays opt-in',async t=>{
 const root=fileURLToPath(new URL('../.rpg04-work/douluo-tests/',import.meta.url));await mkdir(root,{recursive:true});
 const directory=await mkdtemp(join(root,'static-'));
 const {server}=await start({port:0,directory});t.after(()=>new Promise(ok=>server.close(ok)));
 const origin='http://127.0.0.1:'+server.address().port;
 const page=await fetch(origin+'/rpg-app/douluo/');assert.equal(page.status,200);assert.match(page.headers.get('content-security-policy'),/sandbox allow-scripts/);
 assert.match(await page.text(),/魂师觉醒台/);
 const assets=JSON.parse(await read('resources/assets.json'));const audio=assets.find(a=>a.file.endsWith('.mp3'));
 const response=await fetch(origin+'/rpg-app/douluo/'+audio.file,{headers:{Range:'bytes=0-31',Origin:'null','Sec-Fetch-Site':'cross-site'}});assert.equal(response.status,206);assert.equal((await response.arrayBuffer()).byteLength,32);
 assert.equal((await fetch(origin+'/rpg-app/douluo/'+audio.file,{headers:{Range:'bytes=999999999-'}})).status,416);
 assert.equal((await fetch(origin+'/rpg-app/douluo/resources/prompts.json')).status,404);
 assert.equal((await fetch(origin+'/rpg-app/douluo/assets/missing.mp3')).status,404);
 assert.equal((await fetch(origin+'/rpg-app/douluo/chat/api/sessions',{headers:{Origin:'null','Sec-Fetch-Site':'cross-site'}})).status,403);
 assert.equal(archiveChatOptions({}).archiveControls.douluo,null);
 assert.equal(archiveChatOptions({RPG_DOULUO_CHAT_ENABLED:'true',RPG_DOULUO_CHAT_CALL_LIMIT:'2'}).archiveControls.douluo.limit,2);
});

test('douluo two-turn mock transport preserves prompt, isolates sessions and mounts a public Mod without replay',async t=>{
 const root=fileURLToPath(new URL('../.rpg04-work/douluo-tests/',import.meta.url));await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'transport-'));
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:0.7,top_p:0.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{name:'离线验收',model,parameters,available:true,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;seen.push({base,messages});
  const raw='清晨的城门前，成年魂师检查行囊。',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const {server}=await start({port:0,directory,archiveControls:{douluo:{enabled:true,limit:8,baseURL:'http://server:8000',serviceToken:'synthetic-douluo-offline-fixture',fetcher:(u,o)=>configFetch(u,o,fake)}}});t.after(()=>new Promise(ok=>server.close(ok)));
 const origin='http://127.0.0.1:'+server.address().port;installCustomConfig(server,[origin],directory);
 const post=async(path,value,headers={})=>fetch(origin+path,{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(value)});
 const base='/rpg-app/douluo/chat/api/sessions';
 let session=await(await post(base,{opening:'姓名：林川；年龄：20；武魂：蓝银草。'})).json();assert.ok(session.id);assert.equal(seen.length,0);
 const path=base+'/'+session.id;const system=assemble({input:'档案'}).messages[0].content;
 const mods=await(await fetch(origin+'/rpg-app/mod-panel/catalog')).json();const common=mods.items.find(m=>m.id==='common.rationality.v1');
 for(let i=0;i<2;i++){
  const policy=await(await fetch(origin+path+'/memory-palace')).json();
  const request={operationId:randomUUID(),expectedSessionRevision:session.revision,expectedMemoryRevision:policy.memoryRevision,expectedContextPolicyRevision:policy.effectivePolicy.revision,input:i?'走进城门，向守卫问路。':'姓名：林川；年龄：20；武魂：蓝银草。'};
  const headers=i?{'X-Rpg-Mod-Panel':'1'}:{},body=i?{modIds:[common.id],request}:request;
  if(i)assert.equal((await post(path+'/send',{modIds:['ancient.strength.v1'],request},headers)).status,400);
  const r=await post(path+'/send',body,headers);assert.equal(r.status,200);session=await r.json();assert.equal(session.turns.length,i+1);
  assert.equal(seen[i].messages[0].content,system+(i?'\n\n'+common.text:''));assert.equal(seen[i].messages.at(-1).content,request.input);
  assert.equal(seen[i].messages.length,2+i*2);await post(path+'/send',body,headers);assert.equal(seen.length,i+1);
 }
 assert.deepEqual(await(await fetch(origin+'/rpg-app/ancient/chat/api/sessions')).json(),[]);
 assert.equal((await(await fetch(origin+path)).json()).turns.length,2);
});
