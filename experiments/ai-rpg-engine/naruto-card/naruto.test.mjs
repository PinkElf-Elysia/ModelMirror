import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {assembleTurn,selectEntries,systemPrompt} from './assembly.mjs';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from '../studio/server.mjs';
import {installNaruto,narutoOptions} from './integration.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
import {installCustomConfig,configFetch} from '../plugins/custom-config/host.mjs';
import {empty} from '../plugins/custom-config/public/config.mjs';
import {modCatalog} from '../plugins/mod-panel/catalog.mjs';

const book=JSON.parse(readFileSync(new URL('./resources/worldbook.json',import.meta.url)));
const titles=(history,input)=>selectEntries(history,input).map(e=>e.title);

test('fixed prompt preserves the supplied document followed by chapters, triggers and rules',()=>{
  const fixed=readFileSync(new URL('./resources/system.txt',import.meta.url),'utf8');
  assert.equal(sha(fixed),'6d8999fff39071f7bde70363c5ffed67d72bf8af078ac8b3d44d0f20b2881b63');
  const appendix=readFileSync(new URL('./resources/worldbook-index.txt',import.meta.url),'utf8');
  const markers=appendix.match(/^【(?:篇章库|触发表|触发规则)】$/gm);
  assert.deepEqual(markers,['【篇章库】','【触发表】','【触发规则】']);
  assert.deepEqual(appendix.split('\n')[1].split('；'),book.filter(e=>e.kind==='篇章').map(e=>e.title));
  assert.equal(systemPrompt(),fixed+'\n'+appendix);
  assert.equal(assembleTurn(systemPrompt(),[],'我在河边等候。').messages[0].content,systemPrompt());
});

test('each sourced entry is indexed and addressable without injecting bodies into the appendix',()=>{
  const appendix=readFileSync(new URL('./resources/worldbook-index.txt',import.meta.url),'utf8');
  assert.equal(book.length,169);
  assert.equal(new Set(book.map(e=>e.title)).size,book.length);
  for (const e of book) {
    assert.ok(e.body.length>0 && e.paragraphs[0]<=e.paragraphs[1],e.title);
    for(const key of e.keywords)assert.ok(appendix.includes(key),key);
    assert.ok(titles([],e.title).includes(e.title),e.title);
    assert.ok(!appendix.includes(e.body),e.title);
  }
  assert.deepEqual(titles([],'我在河边等候。'),[]);
});
test('panel fences keep literal HTML out of the narrative and chapter lookup',()=>{
  const history=[{role:'user',content:'【所选篇章】中忍考试篇。'}];
  for (const marker of ['````','~~~~']) {
    const reply=[
      '<details><summary>质量管控</summary>',marker+'Checking',
      '大蛇丸 <details>',marker.slice(1),'波之国篇','</details> 我爱罗 <details>',marker,
      '</details>','<p>卡卡西点了点头。</p>',
      '<details><summary>状态</summary>',marker+'Status',
      '</details> 纲手 雪姬忍法帖篇',marker,'</details>',
      marker+'Self','九尾',marker
    ].join('\r\n');
    assert.deepEqual(titles([...history,{role:'assistant',content:reply}],'我继续整理。'),
      ['中忍考试篇','旗木卡卡西']);
  }
});

test('chapter continuity and conditional context follow conversation order',()=>{
  const history=[{role:'user',content:'博人传·忍者学校篇，去找小樱。'},
    {role:'assistant',content:'<details><summary>质量管控</summary>雪姬忍法帖篇，我爱罗</details><p>卡卡西提到<font>九尾</font>。</p><details><summary>自身状态</summary>大蛇丸，木叶村</details>'}];
  assert.deepEqual(titles(history,'我留在这里。'),['博人传·忍者学校篇','旗木卡卡西','九喇嘛']);
  assert.deepEqual(titles(history,'转入波之国篇'),['波之国篇','小国与中立地区','旗木卡卡西','九喇嘛']);
  assert.deepEqual(assembleTurn(systemPrompt(),history,'继续').messages.slice(1,-1),history);
  assert.deepEqual(titles([],'猿飞木叶丸'),['猿飞木叶丸']);
  assert.deepEqual(titles([],'我天天练习。'),[]);
  assert.deepEqual(titles([],'拜访天天'),['天天']);
  assert.deepEqual(titles([],'大筒木辉夜'),['大筒木辉夜']);
  assert.deepEqual(titles([],'月夜见的辉夜'),['辉夜（月夜见）']);
  const all=selectEntries([],book.map(e=>e.title).join('、'));
  assert.ok(all.reduce((n,e)=>n+2+e.body.length,0)<=16000);
});



test('isolated Naruto sessions preserve complete prompt and optional config/mod assembly',async t=>{
 const seen=[],model='google/gemini-3.8-flash',parameters={temperature:.7,top_p:.8,max_tokens:16384};
 const fake=async(url,options)=>{
  if(options.method==='GET')return Response.json({models:[{model,name:'离线验收',available:true,parameters,selectionId:'fixture',selectionRevision:'v1'}]});
  const {customStoryMessages,...base}=JSON.parse(options.body),messages=customStoryMessages??base.messages;seen.push({base,messages});
  const raw=['<details><summary>质量管控</summary>','```Checking','大蛇丸 <details>','```','</details>',
    '<p>九尾点了点头。</p>','<details><summary>状态</summary>我爱罗</details>'].join('\n'),sse='data: [DONE]\n\n';
  return Response.json({raw,sse,receipt:{gateway:'rpg_scoped',sessionId:base.sessionId,requestId:base.requestId,selectionId:'fixture',selectionRevision:'v1',requestedModel:model,actualModel:model,parameters,status:'complete',error:null,dispatched:true,retries:0,requestHash:sha(canonical(base)),effectiveRequestHash:sha(canonical({...base,messages})),rawHash:sha(raw),sseHash:sha(sse)}});
 };
 const directory=await mkdtemp(join(tmpdir(),'naruto-test-'));
 const {server}=await start({port:0,directory,autoSummaryV2:true,transcript:true,messageTools:true});
 t.after(()=>new Promise(r=>server.close(r)));
 const origin='http://127.0.0.1:'+server.address().port;installNaruto(server,{directory,control:{enabled:true,limit:4,baseURL:'http://server:8000',serviceToken:'synthetic-naruto-local-test-credential',fetcher:(u,o)=>configFetch(u,o,fake)}});installCustomConfig(server,[origin],directory);
 const call=async(path,body,headers={})=>{const r=await fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json',...headers},body:JSON.stringify(body)});const v=await r.json();assert.equal(r.status,path.endsWith('/sessions')&&body!==undefined?201:200,JSON.stringify(v));return v;};
 const root='/rpg-app/naruto/chat/api/';
 let s=await call(root+'sessions',{opening:''});const path=root+'sessions/'+s.id;
 assert.equal(seen.length,0);assert.equal(s.turns.length,0);assert.ok(s.runtime.compatible);
 const registry=await call(root+'plugins');for(const id of ['rpg.memory-palace','rpg.rolling-summary','rpg.transcript','rpg.model-selector'])assert.ok(registry.plugins.some(p=>p.id===id));
 const common=(await modCatalog()).find(m=>m.id==='common.rationality.v1');
 for(const i of [0,1]){
  const state=await call(path+'/memory-palace');
  const request={operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input:i?'继续修炼':'【所选篇章】中忍考试篇，我想拜访卡卡西'};
  const config={...empty(),system:'本轮使用简洁文字',prefix:'前置样例',suffix:'后置样例'};
  s=await call(path+'/send',i?request:{request,profiles:[config],modIds:[common.id]},i?{}:{'X-Rpg-Custom-Config':'1','X-Rpg-Mod-Panel':'1'});
  assert.equal(s.turns.length,i+1);assert.ok(seen[i].base.messages[0].content.startsWith(systemPrompt()+'\n\n'));assert.equal(seen[i].base.messages[0].content.slice(systemPrompt().length).includes(book.find(e=>e.title==='旗木卡卡西').body),i===0);
  const sentBook=seen[i].base.messages[0].content.slice(systemPrompt().length);
  assert.deepEqual(book.filter(e=>sentBook.includes(e.body)).map(e=>e.title).sort(),
    (i?['中忍考试篇','九喇嘛']:['中忍考试篇','旗木卡卡西']).sort());
  if(!i){assert.ok(seen[i].messages[0].content.startsWith(systemPrompt()));assert.ok(seen[i].messages[0].content.endsWith(common.text));assert.ok(seen[i].messages.at(-1).content.includes('前置样例'));}
  else {assert.deepEqual(seen[i].messages,seen[i].base.messages);assert.equal(seen[i].messages.length,4);}
 }
 const other=await call('/rpg-app/ancient/chat/api/sessions');assert.deepEqual(other,[]);
 const html=await fetch(origin+'/rpg-app/naruto/?editor=1');assert.equal(html.status,200);assert.match(html.headers.get('content-security-policy'),/sandbox allow-scripts/);assert.doesNotMatch(html.headers.get('content-security-policy'),/allow-same-origin/);
 const art=await fetch(origin+'/rpg-app/naruto/opening.jpg');assert.equal(art.status,200);assert.equal(art.headers.get('content-type'),'image/jpeg');
 const denied=await fetch(origin+path+'/send',{method:'POST',headers:{Origin:'https://outside.invalid','Content-Type':'application/json'},body:'{}'});assert.equal(denied.status,403);assert.equal(seen.length,2);
 const privateFile=await fetch(origin+'/rpg-app/naruto/resources/system.txt');assert.equal(privateFile.status,404);
});


test('opening selectors use chosen values and preserve the original draft',()=>{
 const heading='═'.repeat(50)+'\n     火影大世界 · 角色建卡\n'+'═'.repeat(50);
 const sheet=heading+'\n【原著扮演】辉夜\n姓名：辉夜\n忍者评级：\n人柱力/尾兽：非人柱力\n查克拉性质：—\n血继限界：无';
 assert.deepEqual(titles([],sheet),['辉夜（月夜见）']);
 assert.equal(assembleTurn(systemPrompt(),[],sheet).messages.at(-1).content,sheet);
 assert.deepEqual(titles([],'超辉夜姬'),['辉夜（月夜见）']);
 assert.deepEqual(titles([],heading+'\n【原著扮演】大和'),['大和']);
});
