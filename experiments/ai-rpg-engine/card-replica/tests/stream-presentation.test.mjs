import test from 'node:test';
import assert from 'node:assert/strict';
import {parseFragment} from 'parse5';
import {streamPresentation} from '../lib/stream-presentation.mjs';
import {cardDisplay, cardPresentation} from '../lib/card-presentation.mjs';
const cards = ['earth','god','xingjian','ancient','cultivation','lord-of-mysteries','blue-fish','douluo','dragon-raja','jinyong'];
function nodes(html) {
  const result = [], visit = n => {result.push(n); for (const c of n.childNodes || []) visit(c);};
  visit(parseFragment(html)); return result;
}
for (const card of cards) test(card + ': partial HTML and Markdown render before completion; final projection matches', () => {
  const pieces = ['<details open><summary>状态</summary>', '\n\n**旅人**抵达港口。\n\n',
    '| 时间 | 地点 |\n| --- | --- |\n| 清晨 | 港口 |\n\n',
    '```html\n<div>代码示例</div>\n', '```\n</details>'];
  let raw = '';
  for (const piece of pieces) {
    raw += piece;
    const tree = nodes(streamPresentation(raw, card));
    assert.ok(tree.some(n => n.tagName === 'details'));
    assert.ok(tree.some(n => n.tagName === 'summary'));
    if (raw.includes('**旅人**')) assert.ok(tree.some(n => n.tagName === 'strong'));
    if (raw.includes('| 清晨')) assert.ok(tree.some(n => n.tagName === 'table'));
    if (raw.includes('```html')) assert.ok(tree.some(n => n.tagName === 'code'));
  }
  assert.equal(streamPresentation(raw, card), ['earth','god','douluo'].includes(card) ? cardDisplay(raw, card) : cardPresentation(raw, 'archive').html);
});
test('every split of unsafe HTML remains inert, including incomplete attributes, scripts and comments', () => {
  const raw = '<details><summary>查看</summary><script>bad()</script><!--private--><img src=x onerror=bad()><svg onload=bad()></svg><a href="javascript:bad()">链接</a><div onclick="bad()" style="background:url(https://bad)">结束</div></details>';
  for (let i = 0; i <= raw.length; i++) for (const card of ['earth','god','archive']) {
    const html = streamPresentation(raw.slice(0, i), card);
    for (const n of nodes(html)) {
      assert.ok(!['script','svg','img','iframe','style'].includes(n.tagName));
      assert.ok(!(n.attrs || []).some(a => /^(on|src|href)/.test(a.name) || /url\(/.test(a.value)));
    }
    assert.ok(!html.includes('private')); assert.ok(!html.includes('bad()'));
  }
});
test('empty projections stay empty and oversized or deeply nested previews fail closed', () => {
  assert.equal(streamPresentation('', 'ancient'), '');
  assert.equal(streamPresentation('<script>secret', 'ancient'), '');
  assert.throws(() => streamPresentation('x'.repeat(2097153), 'earth'), /STREAM_SOURCE_LIMIT/);
  assert.throws(() => streamPresentation('<div>'.repeat(170), 'earth'), /PRESENTATION_DEPTH_LIMIT/);
});

test('Douluo live, marquee and commentary remain scoped and interactive with unavailable avatars', () => {
 const raw='<div class="live-container"><div class="status-row"><strong>直播</strong></div><div class="danmu-area"><marquee class="marquee" onstart="bad()"><span>加油</span></marquee></div></div><div class="aa"><label><input type="checkbox" class="bb"><img src="https://invalid/avatar.png" onerror="bad()"></label><div class="cc"><div class="dd">中性吐槽样例</div></div></div>';
 const html=streamPresentation(raw,'douluo');
 const tree=nodes(html), cls=c=>tree.filter(n=>(n.attrs||[]).some(a=>a.name==='class'&&a.value.split(' ').includes(c)));
 for(const c of ['live-container','danmu-track','aa','bb','cc','dd','commentary-placeholder'])assert.equal(cls(c).length,1,c);
 assert.ok(html.includes('data-profile="douluo"'));
 assert.ok(!tree.some(n=>['img','marquee','script'].includes(n.tagName)));
 assert.ok(!html.includes('bad()'));
 assert.ok(!streamPresentation(raw,'ancient').includes('live-container'));
});
