import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {book,fixedPrompt,guide,systemPrompt,selectEntries,assembleTurn} from './assembly.mjs';
import {start} from '../studio/server.mjs';
import {installStarRail,starRailOptions} from './integration.mjs';
import {starRailPresentation} from '../card-replica/lib/star-rail-presentation.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {installCustomConfig,configFetch} from '../plugins/custom-config/host.mjs';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {modCatalog} from '../plugins/mod-panel/catalog.mjs';
test('isolated StarRail sessions preserve complete prompt and optional config/mod assembly',async t=>{
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'离线验收',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;seen.push({base,messages});
  const raw='离线测试回复',sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const directory=await mkdtemp(join(tmpdir(),'star-rail-test-'));
 const {server}=await start({port:0,directory,autoSummaryV2:true,transcript:true,messageTools:true});
 t.after(()=>new Promise(r=>server.close(r)));
 const origin='http://127.0.0.1:'+server.address().port;installStarRail(server,{directory,control:{enabled:true,limit:4,baseURL:'http://server:8000',serviceToken:'synthetic-star-rail-local-test-credential',fetcher:(u,o)=>configFetch(u,o,fake)}});installCustomConfig(server,[origin],directory);
 const call=async(path,body,headers={})=>{const r=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const v=await r.json();assert.equal(r.status,path.endsWith('/sessions')&&body!==undefined?201:200,JSON.stringify(v));return v;};
 const root='/rpg-app/star-rail/chat/api/';
 let s=await call(root+'sessions',{opening:''});const path=root+'sessions/'+s.id;
 assert.equal(seen.length,0);assert.equal(s.turns.length,0);assert.ok(s.runtime.compatible);
 const registry=await call(root+'plugins');for(const id of ['rpg.memory-palace','rpg.rolling-summary','rpg.transcript','rpg.model-selector'])assert.ok(registry.plugins.some(p=>p.id===id));
 const common=(await modCatalog()).find(m=>m.id==='common.rationality.v1');
 for(const i of [0,1]){
  const state=await call(path+'/memory-palace');
  const request={operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input:i?'我看看窗外。':'我想拜访卡厄斯兰那'};
  const config={...empty(),system:'本轮使用简洁文字',prefix:'前置样例',suffix:'后置样例'};
  s=await call(path+'/send',i?request:{request,profiles:[config],modIds:[common.id]},i?{}:{'X-Rpg-Custom-Config':'1','X-Rpg-Mod-Panel':'1'});
  assert.equal(s.turns.length,i+1);assert.equal(seen[i].base.messages[0].content.includes(book.find(e=>e.title==='白厄').body),i===0);
  if(!i){assert.ok(seen[i].messages[0].content.includes('【世界书名称库】'));assert.ok(seen[i].messages[0].content.endsWith(common.text));assert.ok(seen[i].messages.at(-1).content.includes('前置样例'));}
  else {assert.deepEqual(seen[i].messages,seen[i].base.messages);assert.equal(seen[i].messages.length,4);}
 }
 const other=await call('/rpg-app/ancient/chat/api/sessions');assert.deepEqual(other,[]);
 const html=await fetch(origin+'/rpg-app/star-rail/?editor=1');assert.equal(html.status,200);assert.match(html.headers.get('content-security-policy'),/sandbox allow-scripts/);assert.doesNotMatch(html.headers.get('content-security-policy'),/allow-same-origin/);
 const art=await fetch(origin+'/rpg-app/star-rail/title.webp');assert.equal(art.status,200);assert.equal(art.headers.get('content-type'),'image/webp');
 const denied=await fetch(origin+path+'/send',{method:'POST',headers:{Origin:'https://outside.invalid','Content-Type':'application/json'},body:'{}'});assert.equal(denied.status,403);assert.equal(seen.length,2);
 const privateFile=await fetch(origin+'/rpg-app/star-rail/resources/system.txt');assert.equal(privateFile.status,404);
});


test('every DOCX paragraph is retained exactly once and the trigger library covers all names',async()=>{
 const template=await readFile(new URL('./resources/system.txt',import.meta.url),'utf8');
 const fixed=template.split('\n').filter(l=>!/^⟦.+⟧$/.test(l) && l!=='战斗场景调用〈战斗机制〉，按韧性、能量、命途描写。');
 // Freeze every field (including every hidden alias) of the original 171 entries.
 const original=book.filter(e=>e.mode!=='rule');
 assert.equal(original.length,171);
 assert.equal(createHash('sha256').update(JSON.stringify(original)).digest('hex'),'c8d3ccc1c2519f76c9bd12415ab38eae433ae56aa23554dd5d01f7a407a27ef8');
 assert.equal(book.length,178);
 const rows=Array(2656);
 for(const e of book){
  const lines=e.body.split('\n');
  assert.equal(lines.length,e.end-e.start+1);
  for(const [i,line] of lines.entries()){
   assert.equal(rows[e.start-1+i],undefined);
   rows[e.start-1+i]=line;
  }
  assert.ok(systemPrompt().includes(e.title));
  for(const key of e.keys){
   // Exact name calls can activate a rule; the host still recognizes every
   // original alias even when the model sees only its canonical chapter name.
   const selected=selectEntries([],key);
   assert.ok(selected.includes(e),key+' => '+e.title);
  }
 }
 for(let i=0;i<rows.length;i++)if(rows[i]===undefined)rows[i]=fixed.shift();
 assert.equal(fixed.length,0);
 assert.equal(createHash('sha256').update(rows.join('\n')).digest('hex'),'2d3d5f2889b0162bb07c1702d954473bf93d5fe4ae7a293c450dd7d4e5baf008');
});
test('character, path, format and lore slots are in the middle of the assembled prompt',()=>{
 const content=assembleTurn(systemPrompt(),[],'白厄走进千星城，询问欢愉命途。').messages[0].content;
 function inside(title,left,right){
  const body=book.find(e=>e.title===title).body, i=content.indexOf(body);
  assert.ok(content.indexOf(left)>=0 && i>content.indexOf(left) && i<content.indexOf(right),title);
 }
 inside('白厄','# [3.1. 角色真实性协议]','# [3.2. 表达过滤规则集]');
 inside('千星城志','### 2.3. 世界的边界','## 第三章');
 inside('命途机制 · 欢愉','# [4.2. 核心战斗协议]','## 第五章');
 inside('统一回复格式·状态栏','# [INFO_BARRIER]','# 〈世界线偏离度协议〉');
 assert.ok(!content.includes('⟦'));assert.ok(!content.includes(book.find(e=>e.title==='遐蝶').body));
});
test('only user text, active navigation and prior narrative trigger specific lore',()=>{
 const titles=(h,i)=>selectEntries(h,i).map(e=>e.title);
 const h=[{role:'assistant',content:'<details class="w"><i class="x">鸡窝头侦探</i><i class="g">死后就业中心</i><i>美梦与圣杯</i><pos>鸽川区</pos></details><details class="c">阿哈</details><z><p>白厄走来。</p></z><o>拜访那刻夏</o>'}];
 const result=titles(h,'继续。');
 for(const name of ['死后就业中心','鸽川区','白厄'])assert.ok(result.includes(name));
 for(const name of ['鸡窝头侦探','那刻夏','美梦与圣杯','星神：阿哈'])assert.ok(!result.includes(name));
 const neutral=titles([],'黑塔空间站内，刀刃映着山岚。');
 for(const name of ['角色档案 · 黑塔','角色档案 · 刃','巡猎 · 岚'])assert.ok(!neutral.includes(name));
 assert.ok(titles([],'找刃').includes('角色档案 · 刃'));
 assert.ok(titles([{role:'assistant',content:'天色渐暗。\n〈调用：幻造种族群图鉴〉'}],'继续').includes('幻造种族群图鉴'));
 const all=book.map(e=>'〈调用：'+e.title+'〉').join('\n');
 const selected=selectEntries([],all);
 assert.ok(selected.filter(e=>e.mode!=='always').reduce((n,e)=>n+e.body.length,0)<=24000);
 assert.deepEqual(selectEntries([],all),selected);
});
test('saved and streaming projection keeps authored panels and strips active markup',()=>{
 const raw='<details class="w" open><summary>星海航图</summary><pos>鸽川区</pos></details><z><p>旅程<d>「你好」</d></p></z><o onclick="x()">问好</o><img src=x onerror=y><script>alert(9)</script>';
 const rendered=starRailPresentation(raw);
 assert.match(rendered,/sr-panel-w/);assert.match(rendered,/sr-o/);assert.match(rendered,/你好/);
 assert.doesNotMatch(rendered,/onclick|onerror|<script|<img| open|alert\(9\)/);
});

test('broad starts pick one first arc and precise starts take precedence',()=>{
 const initial=book.find(e=>e.openingPlace==='翁法罗斯');
 const start='【游玩档案】\n◆ 开局：翁法罗斯';
 assert.ok(selectEntries([],start).includes(initial));
 assert.ok(!selectEntries([],start+'\n◆ 开局章节：门扉之启，王座之终').includes(initial));
 assert.ok(!selectEntries([],'我听说过翁法罗斯。').includes(initial));
});

test('standing text and directory exactly match the approved review',()=>{
 const checksum=s=>createHash('sha256').update(s).digest('hex');
 assert.equal(checksum(fixedPrompt()),'388a66889fe3b65316aef143e8113803304ab4ad76d1bd06c67f44c24bdcfc3b');
 assert.equal(checksum(guide),'dcd8c5df9167482f7ae7220d1c857b116ed4c49017bc6c6e7f98de993fc8ed0c');
 const standing=fixedPrompt()+'\n'+guide;
 assert.equal([...standing].length,25877);
 const past=[{role:'assistant',content:'<z><p>车窗映着茶杯。</p></z>'}];
 const calm=Array.from({length:20},(_,i)=>'我们继续喝茶。'+i).find(input=>selectEntries(past,input).every(e=>e.mode==='always'));
 assert.ok(calm);assert.equal(assembleTurn(systemPrompt(),past,calm).messages[0].content,standing);
});
test('scene rules precede optional lore and observe only the selected option',()=>{
 const prev=[{role:'assistant',content:'<z><p>我们喝茶。</p></z><o>选项A：拔剑迎敌</o><o>选项B：喝茶闲聊</o>'}];
 const titles=input=>selectEntries(prev,input).filter(e=>e.mode==='rule').map(e=>e.title);
 assert.deepEqual(titles('选A'),['战斗机制','引擎02执行']);
 assert.deepEqual(titles('喝茶闲聊'),[]);
 assert.deepEqual(titles('选B'),[]);
 const unknown=titles('那扇门后会发生什么？');
 for(const rule of book.filter(e=>e.mode==='rule'&&e.title!=='文风正反例'))assert.ok(unknown.includes(rule.title));
 const requested=book.filter(e=>e.mode!=='rule').map(e=>'〈调用：'+e.title+'〉').join('\n');
 const loaded=selectEntries(prev,'拔剑迎敌。'+requested);
 assert.ok(loaded.some(e=>e.title==='战斗机制') && loaded.some(e=>e.title==='引擎02执行'));
});

test('defensive collapse, emotional collapse and combat resolution select distinct rules',()=>{
 const pair=['战斗机制','引擎02执行'];
 const emotion=['引擎03执行'];
 const triggered=(story,input='继续。',panels='')=>selectEntries(
   [{role:'assistant',content:'<z><p>'+story+'</p></z>'+panels}],input
 ).filter(e=>e.mode==='rule').map(e=>e.title);
 assert.deepEqual(triggered('能量护层彻底崩溃涣散。三月七正锁定那具僵直的怪物。'),pair);
 assert.deepEqual(triggered('韧性被击穿。敌人仍在僵直。'),pair);
 assert.deepEqual(triggered('她的情绪彻底崩溃。'),emotion);
 assert.deepEqual(triggered('护盾轰然崩溃，她也情绪崩溃。'),[...pair,...emotion]);
 assert.deepEqual(triggered('交战中护盾崩溃。战斗已经结束。','继续。','<o>选项A：拔剑迎敌</o>'),[]);
 assert.deepEqual(triggered('护层彻底崩溃。战斗结束，她情绪崩溃。'),emotion);
 assert.deepEqual(triggered('战斗已经结束。','〈调用：战斗机制〉'),pair);
 assert.deepEqual(triggered('战斗已经结束。大家打扫战场，回想刚才的战斗。'),[]);
 assert.deepEqual(triggered('敌人已经撤离。','拔剑迎敌。'),pair);
 assert.deepEqual(triggered('警报解除。又有敌人发动攻击。'),pair);
 assert.deepEqual(triggered('敌人仍在攻击，等战斗结束再喝茶。'),pair);
 assert.deepEqual(triggered('护盾彻底崩溃。战斗结束了吗？'),pair);
});
