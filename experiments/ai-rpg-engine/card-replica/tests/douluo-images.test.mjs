import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {parseFragment} from 'parse5';
import {douluoAssets, cardPresentation} from '../lib/card-presentation.mjs';
import {streamPresentation} from '../lib/stream-presentation.mjs';
import inventory from '../../douluo-card/resources/assets.json' with {type:'json'};
import unavailable from '../../douluo-card/resources/unavailable-images.json' with {type:'json'};
const nodes=html=>{const out=[];function walk(n){out.push(n);for(const c of n.childNodes||[])walk(c);}walk(parseFragment(html));return out;};
const attrs=n=>Object.fromEntries((n.attrs||[]).map(a=>[a.name,a.value]));
test('all authored opening images map exactly to existing hash-verified local files',async()=>{
 const images=inventory.filter(a=>a.contentType.startsWith('image/'));
 assert.equal(images.length,46);assert.equal(Object.keys(douluoAssets).length,images.length);
 for(const a of images){
  const bytes=await readFile(new URL('../../douluo-card/public/'+a.file,import.meta.url));
  assert.equal(createHash('sha256').update(bytes).digest('hex'),a.sha256);
  for(const raw of [`<img src="${a.url}" onerror="bad()" srcset="https://bad/x 2x">`,`![角色](${a.url})`]){
   const result=cardPresentation(raw,'douluo'), imgs=nodes(result.html).filter(n=>n.tagName==='img');
   assert.equal(imgs.length,1);assert.equal(attrs(imgs[0]).src,'/rpg-app/douluo/'+a.file);
   assert.equal(result.report.unavailableImages,0);assert.ok(!/onerror|srcset|https:/.test(result.html));
  }
 }
});
test('12 unavailable avatars cover the original prompt IDs; no invented image substitutions',async()=>{
 const prompt=JSON.parse(await readFile(new URL('../../douluo-card/resources/prompts.json',import.meta.url),'utf8')).system;
 const ids=[...new Set(prompt.match(/\b[0-9a-f]{32}\b/g))];
 assert.equal(ids.length,12);assert.deepEqual(new Set(unavailable.images.map(a=>a.id)),new Set(ids));
 for(const a of unavailable.images){
  assert.equal(a.status,404);assert.equal(a.excludedFromRandomDisplay,true);assert.equal(Object.hasOwn(douluoAssets,a.url),false);
  const raw=`<div class="aa"><label><input class="bb" type="checkbox"><img src="${a.url}"></label><div class="cc">吐槽</div></div>`;
  const html=streamPresentation(raw,'douluo');assert.match(html,/随机头像已停用/);
  assert.match(html,/type="checkbox"/);assert.ok(!nodes(html).some(n=>n.tagName==='img'));
 }
});
test('image whitelist rejects unknown, forged, traversal, query and other card URLs',()=>{
 const url=Object.keys(douluoAssets)[0];
 for(const src of [url+'?tracker=1',url.replace('https:','http:'),url.replace('catai.wiki','catai.wiki.bad'),'/rpg-app/douluo/assets/asset-00.jpg','../assets/asset-00.jpg','data:image/svg+xml,x','javascript:bad()','https://invalid/avatar.png']){
  const html=streamPresentation(`<img src="${src}">`,'douluo');assert.ok(!nodes(html).some(n=>n.tagName==='img'));assert.match(html,/配图未收录/);
 }
 for(const card of ['earth','god','archive'])assert.ok(!nodes(cardPresentation(`<img src="${url}">`,card).html).some(n=>n.tagName==='img'));
});
test('partial image attributes never become external fetches; completed source maps locally',()=>{
 const url=Object.keys(douluoAssets)[1],raw=`<div class="aa"><label><input class="bb" type="checkbox"><img src="${url}"></label><div class="cc">中性旁白</div></div>`;
 for(let i=0;i<=raw.length;i++){
  for(const n of nodes(streamPresentation(raw.slice(0,i),'douluo')).filter(n=>n.tagName==='img'))assert.equal(attrs(n).src,douluoAssets[url]);
 }
 assert.equal(nodes(streamPresentation(raw,'douluo')).filter(n=>n.tagName==='img').length,1);
});
