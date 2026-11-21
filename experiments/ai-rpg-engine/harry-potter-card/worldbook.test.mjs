import test from 'node:test';
import assert from 'node:assert/strict';
import {assembleWorldbook as assemble,slots,source,fixed,titles,library,indexHeading} from './worldbook.mjs';

const school=work=>'【作品】'+work+'\n【入场时机】未定\n—— 当前处境 ——\n我在霍格沃茨的大礼堂等候分院。';
const first='哈利·波特与魔法石',prequel='霍格沃茨之遗',game='哈利·波特：魔法觉醒';
test('original lines remain reconstructible, including both author copies of the same setting',()=>{
  assert.equal(slots.map(s=>s.body).join(''),source);
  assert.equal([...source].length,89475);
  assert.equal(slots.filter(s=>s.kind==='作品').length,12);
  assert.equal(slots.filter(s=>s.name==='对角巷').length,2);
  for(const [i,s] of slots.entries())assert.equal(s.line,i?slots[i-1].end+1:1);
});
test('each canonical work triggers its own original chronology at the original slot',()=>{
  for(const work of titles.keys()){
    const out=assemble({input:school(work)}),books=out.hits.filter(h=>h.type==='作品');
    assert.deepEqual(books.map(h=>h.name),work===game?[]:[work]);
    assert.equal(out.system.split(indexHeading).length,2);
    for(const book of slots.filter(s=>s.kind==='作品'))assert.equal(out.system.includes(book.body),book.name===work);
    assert.ok(out.system.includes(slots.find(s=>s.line===1016).body));
  }
  const neutral=assemble({input:'准备开局。'});
  assert.equal(neutral.work,'');assert.equal(neutral.hits.length,0);
});
test('same school uses period-appropriate staff, hides adjacent chronologies and preserves original source order',()=>{
  const a=assemble({input:school(first)}),b=assemble({input:school(prequel)});
  const faculty='校长：阿不思·邓布利多；副校长：米勒娃·麦格';
  assert.ok(a.system.includes(faculty));assert.ok(!b.system.includes(faculty));
  assert.ok(b.system.includes('菲尼亚斯·奈杰勒斯·布莱克（校长）'));assert.ok(!a.system.includes('菲尼亚斯·奈杰勒斯·布莱克（校长）'));
  for(const out of [a,b])assert.deepEqual(out.hits.map(h=>h.start),out.hits.map(h=>h.start).sort((x,y)=>x-y));
  const follow=assemble({history:[{role:'user',content:school(prequel)},{role:'assistant',content:'请入座。<details><summary>可前往</summary>对角巷 《哈利·波特与密室》</details>'}],input:'我想读一读《哈利·波特与密室》。'});
  assert.equal(follow.work,prequel);
  assert.ok(!follow.hits.some(h=>h.name==='对角巷'||h.name==='哈利·波特与密室'));
});
test('shopping keeps exact source fragments, distinguishes movement from a mention and protects the unwritten work',()=>{
  const open='【作品】'+first+'\n—— 当前处境 ——\n我在对角巷。',history=[{role:'user',content:open},{role:'assistant',content:'店门开着。'}];
  const a=assemble({history,input:'走进丽痕书店，打听书单。'});
  assert.ok(a.system.includes('丽痕书店：教科书与魔法书籍'));assert.ok(!a.system.includes('1996年店主加里克·奥利凡德被伏地魔绑架后关店'));
  const b=assemble({history,input:'返回霍格莫德村。'});
  assert.ok(b.hits.some(h=>h.name==='霍格莫德村'));assert.ok(!b.hits.some(h=>h.name==='对角巷'));
  const g=assemble({input:school(game)});assert.ok(!g.hits.some(h=>h.type==='作品'||h.type==='人物'));
  assert.ok(!g.system.includes('校长：阿不思·邓布利多；副校长：米勒娃·麦格'));
  assert.ok(library.includes('前传作品｜'));assert.ok(library.includes('正篇人物专录｜'));assert.ok(fixed.includes('作品名 = 世界书触发关键词'));
});
