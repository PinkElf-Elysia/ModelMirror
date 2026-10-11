import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {parseFragment} from 'parse5';
import {cardPresentation, cardDisplay, commentaryAsset, commentarySource, commentaryAssets} from '../lib/card-presentation.mjs';
import {safeHtml} from '../lib/render.mjs';

const nodes = html => {
  const result=[];
  const visit=n=>{result.push(n);for(const c of n.childNodes||[])visit(c);};
  visit(parseFragment(html));return result;
};
const ofTag=(html,tag)=>nodes(html).filter(n=>n.tagName===tag);
const attr=(node,name)=>node.attrs?.find(a=>a.name===name)?.value;
const display=(raw,profile='god')=>cardPresentation(raw,profile).html;

test('God exposes only the author suggestion comment as inert, escaped data',()=>{
 const raw='<!-- other hidden --><thinking><!--<suggested_questions>\n- hidden\n</suggested_questions>--></thinking><!--\n<suggested_questions>\n- 『正常互动』：交谈。\n- 『意外事件』：<img src="https://bad" onerror="x">\n</suggested_questions>\n-->';
 const html=display(raw);
 assert.equal(ofTag(html,'li').length,2);
 assert.match(html,/剧情辅助/);assert.match(html,/&lt;img/);
 assert.equal(ofTag(html,'img').length,0);
 assert.ok(!html.includes('other hidden'));assert.ok(!html.includes('<li>hidden'));
 assert.ok(!display(raw,'earth').includes('剧情辅助'));
 assert.ok(!display('```html\n'+raw+'\n```').includes('<li>'));
});

test('God plain field lines retain breaks without adding beautified cards or altering code',()=>{
 const raw='功能区\nUI美化：关闭\n剧情辅助：开启\n\n<macroscopic>第一行\n第二行</macroscopic>\n\n```text\n代码甲\n代码乙\n```';
 const html=display(raw);
 assert.match(html,/功能区<br>UI美化：关闭<br>剧情辅助：开启/);
 assert.match(html,/第一行<br>第二行/);
 assert.match(html,/代码甲\n代码乙/);
 assert.ok(!html.includes('flip-toggle'));assert.ok(!html.includes('status-box'));
 assert.equal(ofTag(display('甲\n乙','earth'),'br').length,0);
});

test('all 17 official commentary sources have local mappings, including the second-turn selection',()=>{
 assert.equal(Object.keys(commentaryAssets).length,17);
 const source='https://catai.wiki/287eba7e-830e-4ba3-032a-f55393131900/w=3000';
 const html=display('<div class="aa"><label><input type="checkbox" class="bb"><img src="'+source+'"></label><div class="cc">正文</div></div>');
 assert.equal(ofTag(html,'img').length,1);
 assert.match(attr(ofTag(html,'img')[0],'src'),/^\.\/assets\//);
 for(const asset of Object.values(commentaryAssets))assert.ok(readFileSync(new URL('../public/'+asset,import.meta.url)).length>0);
});

test('host selects a known profile; source and existing renderer stay unchanged',()=>{
  assert.throws(()=>display('x','unknown'),/UNKNOWN_PRESENTATION_PROFILE/);
  assert.throws(()=>display(null),/INVALID_PRESENTATION_SOURCE/);
  const raw='<div class="live-container"><input type="checkbox"><img src="'+commentarySource+'"></div>';
  const before=JSON.stringify(raw), old=safeHtml(raw);
  display(raw);assert.equal(JSON.stringify(raw),before);assert.equal(safeHtml(raw),old);
  assert.ok(!old.includes('live-container'));
});

test('God preserves both flip faces, native checkbox and three independent danmu tracks',()=>{
  const html=display('<div class="page"><main class="page-main"><div class="scene-wrapper"><label class="flip-button-wrapper"><input class="flip-toggle" type="checkbox" hidden name="evil" id="global"><span class="text-front">微观→</span><span class="text-back">←宏观</span></label><div class="flip-card"><div class="flip-card-inner"><div class="flip-card-front"><macroscopic>第一段\n\n第二段</macroscopic></div><div class="flip-card-back"><microscopic>第三段\n\n第四段</microscopic></div></div></div></div></main><div class="live-container"><div class="danmu-area">'+[1,2,3].map(n=>'<div class="danmu-track"><span style="color:#FF5733">弹幕'+n+'</span></div>').join('')+'</div></div></div>');
  assert.equal(ofTag(html,'input').length,1);
  assert.equal(attr(ofTag(html,'input')[0],'aria-label'),'切换宏观与微观视角');
  assert.ok(!/hidden|name=|id=/.test(html));
  assert.equal(nodes(html).filter(n=>attr(n,'class')==='danmu-track').length,3);
  assert.equal(nodes(html).filter(n=>attr(n,'class')==='rpg-narrative').length,2);
  assert.equal(ofTag(html,'p').length,4);
  assert.match(html,/class="flip-card-back"/);
});

test('known commentary asset is local and the corresponding toggle survives',()=>{
  const html=display('<div class="aa"><label><input type="checkbox" class="bb"><img src="'+commentarySource+'" onerror="run()" srcset="https://bad/2 2x"></label><div class="cc"><div class="dd">吐槽内容</div></div></div>');
  assert.equal(attr(ofTag(html,'img')[0],'src'),commentaryAsset);
  assert.equal(ofTag(html,'input').length,1);
  assert.ok(!/https:|onerror|srcset/.test(html));
  assert.match(html,/class="cc"/);
  for(const [source,asset] of Object.entries(commentaryAssets))assert.equal(attr(ofTag(display('<div class="aa"><label><img src="'+source+'"></label></div>'),'img')[0],'src'),asset);
});

test('only exact observed image in God commentary is mapped; all other media remain inert',()=>{
  for(const [raw,profile] of [
    ['<img src="'+commentarySource+'">','god'],
    ['<div class="aa"><img src="'+commentarySource+'?tracking=x"></div>','god'],
    ['<div class="aa"><img src="'+commentarySource+'"></div>','earth'],
    ['<div class="aa"><img src="data:image/svg+xml,attack" alt="远端图"></div>','god'],
  ])assert.equal(ofTag(display(raw,profile),'img').length,0);
  assert.match(display('![描述](https://bad/pixel)\n\n[外链文字](https://bad/path)'),/描述/);
});

test('Earth keeps observed grids, cards, memory sections and bounded stat fill',()=>{
  const html=display('<div class="char-card"><div class="char-header"><span class="char-name">人物</span><span class="tag arbitrary">标签</span></div><div class="info-grid"><div class="grid-item"><span class="key">职业</span><span class="val">工匠</span></div></div><div class="status-bars"><div class="fill health" style="width:72.5%;position:fixed;background-image:url(https://bad)"></div></div></div><details class="memory-core"><summary class="section-header">记忆</summary><div class="stm-card">原文</div></details>','earth');
  for(const cls of ['char-card','info-grid','grid-item','memory-core','stm-card'])assert.ok(html.includes('class="'+cls+'"'));
  assert.match(html,/style="width:72.5%"/);
  assert.ok(!/arbitrary|position|url\(/.test(html));
  assert.ok(!display('<div class="live-container flip-card">正文</div>','earth').includes('class='));
});

test('fenced summaries become literal code, including truncated fences',()=>{
  const html=display('<details><summary>剧情总结</summary>\n\n```Python\n短期总结：\n1. <img src="https://bad">\n```\n---\n~~~text\n长期总结\n~~~\n</details>');
  assert.equal(ofTag(html,'pre').length,2);assert.equal(ofTag(html,'hr').length,1);
  assert.equal(ofTag(html,'img').length,0);
  assert.match(html,/&lt;img src="https:\/\/bad"&gt;/);
  assert.match(display('```\n未结束<script>literal</script>'),/&lt;script&gt;literal/);
  assert.equal(ofTag(html,'details').length,1);
});

test('native disclosures, table text and inline styling survive without execution attributes',()=>{
  const html=display('<details open id="x"><summary onclick="run()">状态</summary><details><summary>嵌套</summary><table><tr><td colspan="2">文本</td></tr></table></details><p style="color:blue;position:fixed">对话</p></details>');
  assert.equal(ofTag(html,'details').length,2);assert.equal(attr(ofTag(html,'details')[0],'open'),'');
  assert.equal(attr(ofTag(html,'td')[0],'colspan'),'2');assert.match(html,/color:blue/);
  assert.ok(!/onclick|id=|position/.test(html));
});

test('mixed prose preserves inline HTML inside paragraphs while rendering Markdown',()=>{
  const html=display('<div class="main-text">第一段<span style="color:blue">对话</span>仍是同段。\n\n第二段**重点**与<em>强调</em>。</div>','earth');
  assert.equal(ofTag(html,'p').length,2);
  assert.match(html,/<p>第一段<span style="color:blue">对话<\/span>仍是同段。<\/p>/);
  assert.match(html,/<p>第二段<strong>重点<\/strong>与<em>强调<\/em>。<\/p>/);
});

test('existing pre/code content is not interpreted again as Markdown',()=>{
  const raw='<pre><code>原始代码\n```html\n&lt;span&gt;文本&lt;/span&gt;\n```\n</code></pre>';
  assert.equal(display(raw),raw);
  assert.equal(ofTag(display('```html\r\n<pre>literal</pre>\r\n```'),'pre').length,1);
});

test('untrusted markup cannot emit active resources, namespace content, events or arbitrary CSS',()=>{
  const samples=[
    '<script>alert(1)</script><style>@import "https://bad";</style><iframe src="https://bad"></iframe>',
    '<svg><foreignObject><div onload="x">attack</div></foreignObject></svg><math><mtext><img src=x onerror=x></mtext></math>',
    '<form action="https://bad"><input type=submit></form><input type=checkbox class=flip-toggle><button onclick=x>button</button>',
    '<div class="fixed page" style="background:url(https://bad);--x:url(https://bad);animation:evil;transform:scale(999)"><a href="javascript:run()" target="_blank">文字</a></div>',
    '<img src=x onerror=x><video autoplay src=x></video><object data=x></object><embed src=x><link rel=stylesheet href=x>',
    '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=run()>">',
    '<svg><style><img src=x onerror=run()></style></svg>',
    '<div xmlns="evil" xlink:href="https://bad" tabindex="0" contenteditable="true" onpointerenter="x">文本</div>',
  ];
  for(const raw of samples)for(const profile of ['earth','god']) {
    const html=display(raw,profile);
    for(const n of nodes(html)) {
      assert.ok(!['script','style','iframe','svg','math','form','input','button','video','object','embed','link','img'].includes(n.tagName),html);
      for(const {name,value} of n.attrs||[]) {
        assert.ok(!/^on|href|src|action|id$|name$|contenteditable|tabindex|xmlns/.test(name),html);
        assert.ok(!/url\(|animation|transform|position|javascript:/i.test(value),html);
      }
    }
  }
});

test('private thinking tags and comments do not enter visible prose',()=>{
  const html=display('<thinking>hidden-a</thinking><think>hidden-b</think><!-- hidden-c --><p>正文</p>');
  assert.equal(html,'<p>正文</p>');
});

test('malformed/truncated HTML is repaired for display; source is not repaired',()=>{
  const raw='<details><summary>状态</summary><div class="status-details"><p>末尾';
  const before=raw;const html=display(raw);
  assert.equal(raw,before);assert.match(html,/末尾<\/p><\/div><\/details>$/);
  assert.throws(()=>display('<div>'.repeat(170)+'文本'),/PRESENTATION_DEPTH_LIMIT/);
});

test('long reply text is preserved without clipping',()=>{
  const raw='<p>'+('甲乙丙丁'.repeat(15000))+'</p>';
  assert.equal(display(raw),raw);
});

test('bundled image bytes match the observed asset receipt',()=>{
  const bytes=readFileSync(new URL('../public/assets/god-commentary-v1.webp',import.meta.url));
  assert.equal(bytes.length,67546);
  assert.equal(bytes.toString('ascii',8,12),'WEBP');
  assert.equal(createHash('sha256').update(bytes).digest('hex'),'d76ff72d86bcb4dd73e104fffd838630a7e20ab87876931d5dc66cdee78c7b8f');
  const second=readFileSync(new URL('../public/assets/god-commentary-alt-v1.jpg',import.meta.url));
  assert.equal(second.length,125057);
  assert.equal(second.subarray(0,3).toString('hex'),'ffd8ff');
  assert.equal(createHash('sha256').update(second).digest('hex'),'bae8e3a381d94322fdb810f2e653aeaae21a680a3c99bf97bb7990482e9163e5');
});

test('host scope cannot be supplied by model HTML; deep replies fail closed with raw-view advice',()=>{
  const html=cardDisplay('<section class="card-output" data-profile="god"><p>正文</p></section>','earth');
  assert.equal(ofTag(html,'section').length,1);assert.equal(attr(ofTag(html,'section')[0],'data-profile'),'earth');
  assert.match(cardDisplay('<div>'.repeat(170)+'文本','god'),/原文/);
  assert.throws(()=>cardDisplay('正文','untrusted'),/UNKNOWN_PRESENTATION_PROFILE/);
});

test('reviewed Earth dialogue roles and extended card selectors survive without attributes',()=>{
  const html=display('<div class="main-text">甲<say role="female" onclick="x">乙</say>丙。</div><div class="memory-section ltm-section"><span class="ltm-id">1</span></div>','earth');
  assert.match(html,/<span class="rpg-say-female">乙<\/span>/);
  assert.match(html,/class="memory-section ltm-section"/);
  assert.ok(!html.includes('onclick'));
});
