import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {assembleWorldbook,entries,fixed,source,stages,works,worldbook,library} from './worldbook.mjs';
const form=(work,n=0)=>'【世界归属】\n单一作品线 · '+work+' → '+stages[work][n]+'\n【入局方式】\n故事初起';
const run=(work,input,n=0)=>assembleWorldbook({opening:form(work,n),input});
const sourceLines=source.split('\n'),html=readFileSync(new URL('resources/original.html',import.meta.url),'utf8').split('\n');
test('every excerpt and the original manuscript layout resolve to their exact author coordinates',()=>{
  for(const e of entries)for(const p of e.parts){
    const lines=p.source==='resources/system.txt'?sourceLines:html;
    assert.equal(lines[p.line-1].slice(p.column,p.column+p.text.length),p.text,e.id);
  }
  assert.equal(worldbook.manuscript.map(p=>p.text).join('\n'),sourceLines.slice(772).join('\n'));
  assert.ok(fixed.includes('作品锚定协议'));assert.ok(!fixed.includes('断臂之后'));assert.ok(!fixed.includes('全书共计 230'));
});
test('all fifteen work selectors, all forty-five original stages, factions and martial options stay in their work',()=>{
 assert.equal(works.length,15);
 for(const work of works){
   assert.equal(stages[work].length,3);
   for(const [i,stage] of stages[work].entries()){
     const r=run(work,'查阅丐帮、少林、郭靖、桃花岛和杨过。',i);
     assert.equal(r.work,work);assert.equal(r.stage,stage);
     assert.deepEqual([...new Set(r.hits.filter(e=>e.category==='剧情阶段').map(e=>e.name))],[stage]);
     assert.ok(r.hits.every(e=>e.work===work||e.work==='通用'));
   }
   for(const e of entries.filter(e=>e.work===work&&e.parts[0].source==='resources/original.html'&&!e.rule))
     for(const key of e.keys)assert.ok(run(work,'查阅'+key).hits.some(h=>h.id===e.id),e.id+' '+key);
 }
});
test('the two Condor books handle the same faction independently and never unlock each other through dialogue',()=>{
 const forbidden=['江湖离散与绝情风波','十六年后与襄阳战云','断臂之后','古墓初缘','小龙女','霍都（一流弱）'];
 const opening=form('射雕英雄传');
 const result=assembleWorldbook({opening,history:[{role:'user',content:opening},{role:'assistant',content:'提到《神雕侠侣》和杨过。'}],
   input:'【世界归属】\n单一作品线 · 神雕侠侣 → 十六年后与襄阳战云\n丐帮、全真教与郭靖。'});
 assert.equal(result.work,'射雕英雄传');assert.ok(result.hits.every(e=>e.work==='射雕英雄传'||e.work==='通用'));
 for(const needle of forbidden)assert.ok(!result.system.includes(needle),needle);
 assert.match(result.catalog,/丐帮（射雕英雄传）/);assert.match(run('神雕侠侣','丐帮').catalog,/丐帮（神雕侠侣）/);
 const early=run('神雕侠侣','查神雕与杨过'),late=run('神雕侠侣','查神雕与杨过',2);
 assert.ok(!early.system.includes(sourceLines[846]));assert.ok(late.system.includes(sourceLines[846]));
});
test('form routes, explicit progression, fusion regions and unbound input fail closed across scopes',()=>{
 const work='射雕英雄传',opening=form(work),phase=stages[work][1];
 assert.equal(assembleWorldbook({opening,progress:['推进至'+phase],input:'继续'}).stage,phase);
 assert.equal(assembleWorldbook({opening,input:'推进至十六年后与襄阳战云'}).stage,stages[work][0]);
 assert.equal(assembleWorldbook({input:'郭靖和杨过在丐帮吗？'}).work,null);
 assert.equal(assembleWorldbook({input:'《射雕英雄传》开局'}).work,work);
 const fusion='【世界归属】\n融合宇宙线 ·金庸群侠传· 中州';
 const r=assembleWorldbook({opening:fusion,input:'古墓初缘、海疆、名门剑理。'});assert.ok(r.hits.every(h=>h.work==='金庸群侠传'||h.work==='通用'));
 assert.ok(r.hits.some(h=>h.name==='名门剑理'));assert.ok(!r.hits.some(h=>h.name==='海疆'||h.name==='古墓初缘'));
 const shared=assembleWorldbook({opening:fusion,input:'查阅丐帮（射雕英雄传）'});assert.ok(shared.hits.some(h=>h.work===work&&h.name==='丐帮'));
});
test('source aliases and positions survive, and person names do not unlock long plot summaries',()=>{
 const r=run('天龙八部','萧峰在琅嬛福地，打听凌波微步。');
 assert.ok(r.hits.some(e=>e.name==='乔峰'&&e.line===783));assert.ok(r.hits.some(e=>e.name==='琅嬛福地'));assert.ok(!r.hits.some(e=>e.category==='剧情篇章'));
 assert.ok(!library({work:'天龙八部'}).includes('乔峰（萧峰）'));
 const chapter=run('天龙八部','查阅雁门关绝唱与英雄末路',2);
 assert.ok(chapter.system.includes(sourceLines[838]));
 assert.ok(chapter.system.indexOf(sourceLines[838])<chapter.system.indexOf('· 三流高手'));
});
