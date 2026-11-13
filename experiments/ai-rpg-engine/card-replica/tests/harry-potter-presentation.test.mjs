import test from 'node:test';
import assert from 'node:assert/strict';
import {parseFragment} from 'parse5';
import {harryPotterPresentation, harryPotterResponse} from '../lib/harry-potter-presentation.mjs';
import {streamPresentation} from '../lib/stream-presentation.mjs';
const nodes = tree => [tree, ...(tree.childNodes || []).flatMap(nodes)];
const raw = '</details><details open><summary>角色状态</summary><p>林川：23 加隆</p><details open="false"><summary>信件</summary>给米拉，尚未打开</details></details></details>\n\n```html\n</details>\n```';

test('Harry Potter panels are balanced, closed by default, and keep summaries, prose and literal code', () => {
  const html = harryPotterPresentation(raw), all = nodes(parseFragment(html));
  const panels = all.filter(n => n.tagName === 'details');
  assert.equal(panels.length, 2);
  assert(panels.every(n => n.attrs.every(a => a.name !== 'open')));
  assert.equal((html.match(/<\/details>/g) || []).length, 2);
  assert.equal(all.filter(n => n.tagName === 'summary').length, 2);
  for (const text of ['林川：23 加隆', '给米拉，尚未打开', '&lt;/details&gt;']) assert(html.includes(text));
  assert.equal(streamPresentation(raw, 'harry-potter'), html);
  assert.match(streamPresentation(raw, 'jinyong'), /<details open/);
});

test('display projection preserves the original response, history, receipts and raw bytes', () => {
  const value = {revision: 4, turns: [{raw, html: 'old', receipt: {status: 'complete'}}], history: [{content: raw}]};
  const before = JSON.stringify(value), after = harryPotterResponse(value);
  assert.equal(JSON.stringify(value), before);
  assert.equal(after.turns[0].raw, raw);
  assert.equal(after.history, value.history);
  assert.equal(after.turns[0].receipt, value.turns[0].receipt);
  assert.equal(harryPotterResponse({session: value}).session.turns[0].html, after.turns[0].html);
});

test('partial and hostile HTML retains the existing sanitizer boundary', () => {
  const html = harryPotterPresentation('<details open onclick="alert(1)"><summary>行囊</summary><script>alert(2)</script><p>未完');
  assert(!/onclick|script|alert| open/.test(html));
  assert.match(html, /行囊/);
  assert.match(html, /未完<\/p><\/details>/);
});
