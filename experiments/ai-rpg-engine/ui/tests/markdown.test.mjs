import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { build } from 'esbuild';
const ui = fileURLToPath(new URL('../', import.meta.url)), work = path.resolve(ui, '../.rpg04-work');
async function render(t, text) {
  await fs.mkdir(work, { recursive: true }); const directory = await fs.mkdtemp(path.join(work, 'rpg05-markdown-'));
  t.after(async () => { const actual = await fs.realpath(directory); assert.equal(path.dirname(actual), await fs.realpath(work)); assert.ok(path.basename(actual).startsWith('rpg05-markdown-')); await fs.rm(actual, { recursive: true }); });
  const outfile = path.join(directory, 'render.cjs');
  await build({ stdin: { contents: `import React from 'react';import {renderToStaticMarkup} from 'react-dom/server';import {SafeMarkdown,safeExternalUrl} from './src/SafeMarkdown.tsx';const text=JSON.parse(process.argv[2]);console.log(JSON.stringify({html:renderToStaticMarkup(React.createElement(SafeMarkdown,{text})),urls:['javascript:alert(1)','data:image/svg+xml,x','//example.org/x','https://user:password@example.org/','https://example.org/','http://example.org/'].map(safeExternalUrl)}));`, resolveDir: ui, loader: 'tsx' }, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic', outfile, logLevel: 'silent' });
  const result = spawnSync(process.execPath, [outfile, JSON.stringify(text)], { encoding: 'utf8', windowsHide: true });
  assert.equal(result.status, 0, result.stderr); return JSON.parse(result.stdout);
}
test('untrusted Markdown produces no active HTML, image loads, script URLs or automatic external anchors', async t => {
  const { html, urls } = await render(t, '# 小标题\n\n<script>globalThis.pwned=true</script>\n\n<iframe src="https://attacker.example/"></iframe>\n\n![图片](https://example.org/tracker.png) [外链](https://example.org/path) [脚本](javascript:alert%281%29)\n\n**保留文字**');
  assert.doesNotMatch(html, /<(?:script|iframe|img)\b|href="https?:|src=|javascript:|onerror=/iu);
  assert.match(html, /<h3>小标题<\/h3>/u); assert.match(html, /<strong>保留文字<\/strong>/u);
  assert.match(html, /图片：/u); assert.match(html, /打开外部链接/u);
  assert.deepEqual(urls, ['', '', '', '', 'https://example.org/', 'http://example.org/']);
});
test('code remains inert and long text is preserved without fixed state extraction', async t => {
  const { html } = await render(t, '```html\n<img src=x onerror=alert(1)>\n```\n\n人物资料由卡片正文提供。');
  assert.match(html, /&lt;img/u); assert.doesNotMatch(html, /<img\b/iu);
  assert.match(html, /人物资料由卡片正文提供。/u); assert.doesNotMatch(html, /<details|状态提案|冲突审阅/u);
});
