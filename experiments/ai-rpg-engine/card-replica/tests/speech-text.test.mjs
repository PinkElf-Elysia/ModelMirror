import test from 'node:test';
import assert from 'node:assert/strict';
import {extractSpeechText} from '../src/speech-text.mjs';
test('extracts narrative paragraphs without hidden reasoning, UI or scripts',()=>{
 const raw='<cot><!-- private --><p>hidden thought</p></cot><style>.x{}</style><script>bad()</script><div class="rpg-narrative"><p>钟楼响了。</p><p>旅人抬头。<br>风穿过广场。</p><button>进入</button><details><summary>属性</summary>积分 200</details><p hidden>不可见</p><p style="display:none">隐藏</p></div><div class="status-panel">年龄 18</div><!-- no -->';
 assert.equal(extractSpeechText(raw),'钟楼响了。\n旅人抬头。\n风穿过广场。');
});
test('plain text, markdown, HTML fences, entities and blank input',()=>{
 assert.equal(extractSpeechText('## 场景\n**旅人**看着[钟楼](https://example.com)。![图](a.png)'),'场景\n旅人看着钟楼。');
 assert.equal(extractSpeechText('```html\n<p>A &amp; B</p>\n```'),'A & B');
 assert.equal(extractSpeechText('<think>never speak</think><p>你好</p>'),'你好');
 assert.equal(extractSpeechText(''),'');
});
