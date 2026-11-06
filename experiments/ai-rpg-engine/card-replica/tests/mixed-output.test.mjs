import test from 'node:test';
import assert from 'node:assert/strict';
import {parseFragment} from 'parse5';
import {cardDisplay} from '../lib/card-presentation.mjs';
import {safeHtml} from '../lib/render.mjs';

// Synthetic structure only; no player transcript or provider call is used.
const raw = [
  '<!-- decoration --><details open><summary>状态检查</summary>',
  '```Checking', '地点：雾港', '天气：小雨', '```', '</details>', '',
  '旅人收起旧信。', '', '*脚步声渐近。* <font color="#1A3A5C">“请往这边走。”</font>', '',
  '<details><summary>角色状态</summary>', '```Status', '姓名：旅人', '物品：旧信', '```', '</details>', '',
  '<details><summary>记忆记录</summary>', '**最近事件**', '',
  '| 时间 | 事件 |', '| --- | --- |', '| 清晨 | 抵达码头 |', '| 午后 | 交付旧信 |', '</details>',
].join('\n');
const tree=html=>{const result=[];const visit=n=>{result.push(n);for(const c of n.childNodes||[])visit(c);};visit(parseFragment(html));return result;};
const tag=(html,name)=>tree(html).filter(n=>n.tagName===name);
const text=n=>n.nodeName==='#text'?n.value:(n.childNodes||[]).map(text).join('');
const renderers={earth:r=>cardDisplay(r,'earth'),god:r=>cardDisplay(r,'god'),xingjian:safeHtml,ancient:safeHtml,cultivation:safeHtml,'lord-of-mysteries':safeHtml};
for(const [card,render] of Object.entries(renderers)) {
  test(card+': mixed disclosures, code, prose, dialogue and tables stay readable',()=>{
    const before=raw, html=render(raw);
    assert.equal(raw,before);
    assert.equal(tag(html,'details').length,3);
    assert.ok(tag(html,'details')[0].attrs.some(a=>a.name==='open'));
    assert.equal(tag(html,'pre').length,2);
    assert.equal(text(tag(html,'pre')[0]),'地点：雾港\n天气：小雨\n');
    assert.equal(tag(html,'em').length,1);
    assert.ok(tag(html,'p').some(n=>text(n)==='旅人收起旧信。'));
    assert.ok(tag(html,'span').some(n=>n.attrs.some(a=>a.name==='style'&&a.value==='color:#1A3A5C')));
    assert.equal(tag(html,'table').length,1);assert.equal(tag(html,'tr').length,3);
    assert.ok(!html.includes('```'));assert.ok(!html.includes('| ---'));
    assert.equal(tag(html,'strong').length,1);
  });
  test(card+': pure Markdown and unsafe mixed markup use the same safe projection',()=>{
    assert.equal(tag(render('| A | B |\n| --- | --- |\n| one | two |'),'table').length,1);
    const html=render('<details><summary>记录</summary>\n\n[链接](javascript:alert%281%29) ![图](https://invalid/pixel)\n\n<font color="red" onmouseover="bad()">字</font><script>bad()</script><svg><a onload="bad()">bad</a></svg>\n\n&lt;img src=x onerror=bad()&gt;\n</details>');
    for(const n of tree(html)) {
      assert.ok(!['script','iframe','img','svg','form','input'].includes(n.tagName));
      assert.ok(!(n.attrs||[]).some(a=>/^(on|src|href|id|name)/.test(a.name)));
    }
    assert.ok(html.includes('&lt;img')); // decoded text must never become an active tag
  });
}

test('an excessively nested reply retains raw-view access instead of failing the session read',()=>{
 for(const render of Object.values(renderers))assert.match(render('<div>'.repeat(170)+'saved text'),/原文/);
});
