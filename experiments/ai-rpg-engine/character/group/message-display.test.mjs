import test from 'node:test';import assert from 'node:assert/strict';import {createRequire} from 'node:module';
import {messagePresentation,renderMessageBody} from './message-display.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');
const model=content=>({content,source:{kind:'model-result'}});
test('literal prose paragraphs render without changing raw response or existing newlines',()=>{
 for(const [raw,expected] of [[String.raw`第一段\n\n第二段\n结尾`,'第一段\n\n第二段\n结尾'],[String.raw`第一段\r\n\r\n第二段`,'第一段\n\n第二段'],['真实换行\n'+String.raw`段落\n\n后文`,'真实换行\n段落\n\n后文']]){
  const m=Object.freeze(model(raw)),before=JSON.stringify(m),v=messagePresentation(m);assert.equal(v.text,expected);assert.equal(v.changed,true);assert.equal(JSON.stringify(m),before);
 }
});
test('code, paths, structured data, single escapes, player and opening text stay exact',()=>{
 for(const raw of ['正常\n\n段落',String.raw`单个\n转义`,String.raw`双重\\n\\n转义`,String.raw`C:\notes\n\n`,String.raw`https://example.invalid/\n\n`,String.raw`说明 "\n\n"`,JSON.stringify({text:String.raw`甲\n\n乙`}),'{"text":"甲\\n\\n乙"}',String.raw`甲\n\n`+'`code`','```txt\n'+String.raw`甲\n\n乙`+'\n```','~~~\n'+String.raw`甲\n\n乙`])assert.deepEqual(messagePresentation(model(raw)),{text:raw,changed:false},raw);
 for(const kind of ['player-input','opening','fixture']){const raw=String.raw`甲\n\n乙`;assert.deepEqual(messagePresentation({content:raw,source:{kind}}),{text:raw,changed:false});}
});
test('DOM toggle exposes exact original, stays text-only and never mutates stored message',()=>{
 const dom=new JSDOM('<article></article>'),doc=dom.window.document,row=doc.querySelector('article');
 const m=model(String.raw`<img src=x onerror=alert(1)>\n\n后文`),before=JSON.stringify(m);renderMessageBody(doc,row,m);
 assert.equal(row.querySelector('p').textContent,'<img src=x onerror=alert(1)>\n\n后文');assert.equal(row.querySelector('img'),null);
 const button=row.querySelector('button');assert.equal(button.type,'button');assert.equal(button.textContent,'查看原始响应');button.click();assert.equal(row.querySelector('p').textContent,m.content);assert.equal(button.getAttribute('aria-pressed'),'true');button.click();assert.match(row.querySelector('p').textContent,/\n\n/);assert.equal(JSON.stringify(m),before);
 const reopened=doc.createElement('article');renderMessageBody(doc,reopened,m);assert.equal(reopened.querySelector('p').textContent,row.querySelector('p').textContent);assert.equal(JSON.stringify(m),before);dom.window.close();
});
