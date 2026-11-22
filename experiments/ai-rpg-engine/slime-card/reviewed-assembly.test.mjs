import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {slots} from './assembly.mjs';
import {reviewedPrompt,reviewedTurn,selectReviewed,triggerIndex} from './reviewed-assembly.mjs';
const read=name=>readFileSync(new URL('./resources/'+name,import.meta.url),'utf8');
const records=JSON.parse(read('reviewed-worldbook.json'));
const orig=JSON.parse(read('worldbook.json'));
const chapters=JSON.parse(read('chapters.json'));
const lookup=(phase,term,point='')=>selectReviewed([],term,'【时间线】'+chapters[phase].title+(point?'\n【入场时机】'+point:''));
const lore=(phase,term,point)=>lookup(phase,term,point).slice(1).map(x=>x.body).join('\n');
test('catalogue completeness, every exact alias, all original slots and all chapter choices',()=>{
  for(const [prefix,count] of [['no-',120],['sup-',50],['lore-',29],['author-',47]])assert.equal(records.filter(x=>x.id.startsWith(prefix)).length,count);
  assert.equal(new Set(records.map(x=>x.id)).size,records.length);
  assert.equal(reviewedPrompt(),read('system.txt')+'\n\n'+triggerIndex);
  assert.doesNotMatch(triggerIndex,/[A-Za-z0-9ァ-ヿ]|https|待核|存疑/);
  assert.ok(!records.some(e=>e.title==='精神世界'));
  for(const [i,author] of orig.entries()){
    const e=records[i];assert.equal(e.slot,author.slot);
    for(const term of author.keywords)assert.ok(e.keywords.includes(term),term);
  }
  for(const record of records){
    const id=record.profile??record.id;
    const at=record.only??23;
    for(const key of record.keywords){
      const hits=lookup(at,key);
      assert.ok(hits.some(x=>x.id===id),record.id+' '+key);
    }
  }
  for(const [i,c] of chapters.entries()){
    assert.deepEqual(lookup(i,'我停下来。').map(x=>x.id),['chapter-'+i]);
    for(const point of ['篇章开始','篇章中期','自定义入场']){
      const early=lookup(i,'继续。',point)[0];
      assert.ok(early.body.includes(c.year));assert.ok(!early.body.includes(c.body));
    }
  }
  for(const anchor of slots.values())assert.equal(reviewedPrompt().split('\n').filter(l=>l===anchor).length,1);
  for(const e of records.filter(x=>!x.profile)){
    const at=e.only??23,open='【时间线】'+chapters[at].title;
    const hits=selectReviewed([],e.title,open),hit=hits.find(v=>v.id===e.id);
    assert.ok(hit,e.id);
    const text=reviewedTurn(reviewedPrompt(),[],e.title,open).messages[0].content;
    assert.ok(text.includes(hit.body));assert.ok(text.indexOf(hit.body)<text.indexOf(slots.get(hit.slot)),e.id);
  }
});
test('plot gates hold on missed, delayed, alias, cross-film and hidden-panel triggers',()=>{
  assert.deepEqual(selectReviewed([],'夏尔、阿皮托、时空尽头。').map(x=>x.id),[]);
  assert.doesNotMatch(lore(4,'希波库特草、利姆鲁、时空尽头、夏尔'),/智慧之王|时空尽头|神智核|第79层/);
  assert.match(lore(4,'希波库特草'),/回复药/);
  assert.doesNotMatch(lookup(2,'继续。')[0].body,/三名战死亲卫/);
  assert.doesNotMatch(lookup(19,'继续。')[0].body,/日向复活进化为神人，成为真之勇者/);
  assert.doesNotMatch(lookup(23,'继续。')[0].body,/优树和拉普拉斯从时空尽头归还/);
  const festival=lore(15,'アピト');
  assert.match(festival,/共管第71至80层/);assert.doesNotMatch(festival,/第79层|米尼兹|代战日向|神蜂/);
  assert.match(lore(18,'阿毕特'),/第80层代战日向/);assert.doesNotMatch(lore(18,'阿毕特'),/第79层|米尼兹/);
  assert.match(lore(20,'阿皮托'),/第79层/);assert.match(lore(20,'阿皮托'),/重伤生还/);assert.doesNotMatch(lore(20,'阿皮托'),/神蜂/);
  assert.match(lore(15,'復活の腕輪'),/有效区域内死亡/);assert.doesNotMatch(lore(9,'复活腕轮'),/普通款/);
  assert.match(lore(14,'尼古拉斯'),/日曜师格兰/);assert.doesNotMatch(lore(14,'尼古拉斯'),/剑吸收/);
  assert.match(lore(19,'尼古拉斯'),/剑吸收/);
  assert.match(lore(11,'ゴブゾウ'),/保护朱菜/);assert.doesNotMatch(lore(11,'ゴブゾウ'),/头部被剑/);
  assert.match(lore(20,'ゴブゾウ'),/头部被剑/);
  assert.match(lore(19,'地狱门'),/阿尔罗斯亲自勘察/);assert.doesNotMatch(lore(23,'魔界'),/雷昂亲自查看/);
  assert.match(lore(13,'托娃'),/拉贾/);assert.equal(lore(16,'托娃'),'');
  assert.match(lore(16,'米奥'),/护送尤拉/);assert.equal(lore(13,'米奥'),'');
  const story=[{role:'user',content:'【时间线】开国祭篇'},{role:'assistant',content:'<p>阿<span>皮托</span>正在采蜜。</p><details>复活腕轮</details>'}];
  assert.deepEqual(selectReviewed(story,'打听终焉之神篇').map(h=>h.id),['chapter-15','sup-17']);
  assert.doesNotMatch(lookup(20,'阿皮托和哥布藏','篇章开始').map(e=>e.body).join('\n'),/米尼兹|头部被剑刺伤|魂之回廊被撕裂/);
  const panel=[{role:'assistant',content:'<details>│  ├─ 当前时间线篇章：圣魔激战篇</details>'}];
  assert.equal(selectReviewed(panel,'尼古拉斯')[0].title,'圣魔激战篇');
  const dense=records.flatMap(e=>e.keywords).join('。');
  const selected=lookup(23,dense);
  assert.ok(selected.reduce((n,e)=>n+e.body.length+2,0)<=12000);
  assert.equal(new Set(selected.map(e=>e.id)).size,selected.length);
});