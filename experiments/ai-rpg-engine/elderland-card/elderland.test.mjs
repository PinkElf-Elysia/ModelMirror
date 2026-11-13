import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdtemp} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createRequire} from 'node:module';
import {Script} from 'node:vm';
import {assemble} from './assembly.mjs';
import {render} from './build.mjs';
import {start,archiveChatOptions} from '../studio/server.mjs';
const file=name=>readFile(new URL(name,import.meta.url));
const hash=x=>createHash('sha256').update(x).digest('hex');
const {JSDOM}=createRequire(new URL('../../../client/package.json',import.meta.url))('jsdom');

test('Word extraction, original frontend and all eleven local images retain exact hashes',async()=>{
 const manifest=JSON.parse(await file('resources/MANIFEST.json'));
 const prompt=JSON.parse(await file('resources/prompts.json')).system;
 assert.equal(hash(prompt),manifest.system.sha256);assert.equal([...prompt].length,66964);
 assert.equal(hash(await file('resources/original.html')),manifest.frontend.sha256);
 assert.equal(hash(JSON.parse(await file('resources/summary.json')).summary),manifest.summary.sha256);
 const assets=JSON.parse(await file('resources/ASSETS.json'));assert.equal(assets.length,11);
 for(const asset of assets){const bytes=await file('public/'+asset.file);assert.equal(hash(bytes),asset.sha256);assert.equal(bytes.length,asset.bytes);}
 const history=[{role:'user',content:'第一轮'},{role:'assistant',content:'原始答复'}];
 assert.deepEqual(assemble({input:'第二轮',history}).messages,[{role:'system',content:prompt},...history,{role:'user',content:'第二轮'}]);
 assert.throws(()=>assemble({input:''}));assert.throws(()=>assemble({input:'开始',history:history.slice(0,1)}));
});

test('generated frontend preserves author text, layouts and animation rules',async()=>{
 const html=await render();assert.equal((await file('public/index.html')).toString(),html);assert(!/https?:\/\//.test(html));
 for(const [,script] of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new Script(script);
 const original=new JSDOM(await file('resources/original.html')),adapted=new JSDOM(html);
 for(const selector of ['.main-title','.tab-nav','.tab-content','footer'])assert.deepEqual([...adapted.window.document.querySelectorAll(selector)].map(e=>e.textContent.replace('带入聊天 →','')),[...original.window.document.querySelectorAll(selector)].map(e=>e.textContent));
 const oldCss=original.window.document.querySelector('style').textContent.replace(/^@import.*$/m,'');
 const newCss=adapted.window.document.querySelector('style').textContent;
 for(const rule of oldCss.matchAll(/@keyframes[^}]+}(?:[^}]+})?}/g))assert(newCss.includes(rule[0]));
 original.window.close();adapted.window.close();
});

test('thirteen tabs, eight regions, character, custom abilities, talents and preview work',async()=>{
 const errors=[],alerts=[];
 const dom=new JSDOM(await render(),{url:'http://localhost/rpg-app/elderland/?chat=1',runScripts:'dangerously',beforeParse(w){
  w.matchMedia=()=>({matches:false});w.HTMLElement.prototype.scrollIntoView=()=>{};w.alert=s=>alerts.push(s);
  w.setTimeout=fn=>{queueMicrotask(fn);return 1;};w.addEventListener('error',e=>errors.push(e.message));
 }});
 await new Promise(r=>dom.window.addEventListener('load',r,{once:true}));
 const d=dom.window.document;
 d.getElementById('opening').click();await new Promise(r=>setImmediate(r));assert(d.getElementById('main').classList.contains('active'));
 assert.equal(d.querySelectorAll('.tab-btn').length,13);assert.equal(d.querySelectorAll('.world-map').length,9);
 for(const key of ['central','north','west','east','south','elfwood','dwarf','underdark']){
  d.querySelector(`[data-tab="${key}"]`).click();assert(d.getElementById('tab-'+key).classList.contains('active'));
  d.querySelector(`#${key}-countries .country-card`).click();d.querySelector(`#${key}-loc-list .location-item`).click();
  d.querySelector('[data-tab="preview"]').click();assert(d.getElementById('preview-box').textContent.includes(d.querySelector(`#${key}-loc-list label`).textContent));
 }
 // Return to a previously rendered location after selecting another country's card.
 d.querySelector('[data-tab="central"]').click();d.querySelector('#central-loc-list .location-item').click();
 d.querySelector('[data-tab="character"]').click();d.getElementById('char-name').value='林澈';d.getElementById('char-age').value='28';
 d.querySelector('[data-tab="ability"]').click();const ability=d.querySelector('input[name="combat"]');ability.click();assert(ability.checked);
 d.getElementById('custom-ability-name').value='<img src=x onerror=alert(1)>';d.getElementById('custom-ability-desc').value='<b>星光护盾</b>';
 dom.window.addCustomAbility();assert.equal(d.querySelectorAll('#custom-abilities-list img').length,0);
 d.querySelector('[data-tab="heritage"]').click();d.getElementById('roll-btn').click();await new Promise(r=>setImmediate(r));
 const talents=[...d.querySelectorAll('#talent-list input')];assert.equal(talents.length,8);talents.slice(0,4).forEach(t=>t.click());assert.equal(talents.filter(t=>t.checked).length,3);assert.equal(alerts.length,1);
 d.querySelector('[data-tab="preview"]').click();const preview=d.getElementById('preview-box').textContent;
 for(const text of ['林澈','28岁','中央·诸王之地','星光护盾','已选择3/3',ability.value])assert(preview.includes(text),text);
 assert.deepEqual(errors,[]);dom.window.dispatchEvent(new dom.window.Event('pagehide'));dom.window.close();
});

test('opt-in control, null-origin artwork, two offline turns, isolation and restart',async()=>{
 assert.equal(archiveChatOptions({}).archiveControls.elderland,null);
 assert.equal(archiveChatOptions({RPG_ELDERLAND_CHAT_ENABLED:'true',RPG_ELDERLAND_CHAT_CALL_LIMIT:'2'}).archiveControls.elderland.limit,2);
 const directory=await mkdtemp(join(tmpdir(),'elderland-card-'));let host=await start({port:0,directory,autoSummaryV2:true});
 let origin='http://127.0.0.1:'+host.server.address().port;
 const request=(path,body)=>fetch(origin+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
 const api=async(path,body)=>{const response=await request('/rpg-app/elderland/chat/api/'+path,body);const data=await response.json();assert(response.ok,JSON.stringify(data));return data;};
 try{
  const page=await request('/rpg-app/elderland/');assert.equal(page.status,200);assert(page.headers.get('content-security-policy').includes('img-src '+origin));
  for(const asset of JSON.parse(await file('resources/ASSETS.json'))){const r=await fetch(origin+'/rpg-app/elderland/'+asset.file,{headers:{Origin:'null','sec-fetch-site':'cross-site'}});assert.equal(r.status,200);assert.equal(hash(Buffer.from(await r.arrayBuffer())),asset.sha256);}
  assert.equal((await request('/rpg-app/elderland/resources/prompts.json')).status,404);
  assert.equal((await fetch(origin+'/rpg-app/elderland/chat/api/sessions',{headers:{Origin:'null'}})).status,403);
  const session=await api('sessions',{opening:''});assert.equal(session.turns.length,0);const path='sessions/'+session.id;
  for(const input of ['姓名：林澈，28岁，在圣光王国开始冒险。','到冒险者公会核对任务。']){
   const memory=await api(path+'/memory-palace');await api(path+'/send',{operationId:randomUUID(),expectedSessionRevision:memory.sessionRevision,expectedMemoryRevision:memory.memoryRevision,expectedContextPolicyRevision:memory.effectivePolicy.revision,input});
  }
  const saved=await api(path);assert.equal(saved.turns.length,2);assert.equal((await request('/rpg-app/ancient/chat/api/'+path)).status,404);
  await new Promise(r=>host.server.close(r));host=await start({port:0,directory,autoSummaryV2:true});origin='http://127.0.0.1:'+host.server.address().port;
  const restored=await api(path);assert.deepEqual(restored.turns,saved.turns);assert.equal(restored.revision,saved.revision);
 }finally{await new Promise(r=>host.server.close(r));}
});


test('reduced motion starts immediately using the keyboard',async()=>{
 const dom=new JSDOM(await render(),{runScripts:'dangerously',url:'http://localhost/rpg-app/elderland/',beforeParse(w){w.matchMedia=()=>({matches:true});}});
 await new Promise(r=>dom.window.addEventListener('load',r,{once:true}));
 const d=dom.window.document;d.getElementById('opening').dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
 assert(d.getElementById('main').classList.contains('active'));assert.equal(d.getElementById('opening').style.display,'none');
 assert(d.querySelector('style').textContent.includes('@media(prefers-reduced-motion:reduce)'));
 dom.window.dispatchEvent(new dom.window.Event('pagehide'));dom.window.close();
});
