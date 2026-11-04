import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {projectSuggestions,checkPlayerInput,addBlockedWord,removeBlockedWord,blockedWordsIssue,ASSISTANCE_LIMITS as limits} from './input-assistance.mjs';
import {compilePresentation} from './presentation.mjs';
import {blankDocument,validateDocument,capabilityDeclaration} from './contracts.mjs';
import {startAuthoringServer} from './server.mjs';

const block=(items=['检查信封','询问守灯人','环顾四周'],tag='',prefix=i=>(i+1)+'. ')=>'<suggested_questions>\n'+items.map((s,i)=>(tag?'<'+tag+'>':'')+prefix(i)+s+(tag?'</'+tag+'>':'')).join('\n')+'\n</suggested_questions>';
const compile=(html,suggestions=true,extra={})=>compilePresentation({mode:'reply',html,origin:'http://127.0.0.1:18560',token:'a'.repeat(48),suggestions,...extra});
const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b5b/tests/',import.meta.url));
await mkdir(parent,{recursive:true});
async function fixture(t,options={}){
  const host=await startAuthoringServer({directory:await mkdtemp(join(parent,'http-')),...options});t.after(()=>host.close());
  const request=async(path,body)=>{const r=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
  return {host,request};
}
function document(){const d=blankDocument();d.metadata.name='雾港 B5-B';d.metadata.audience='female';d.sources.system='系统原文\n\n';d.sources.prefix='前置\n';d.sources.suffix=' 后置 ';d.enhancements.suggestions=true;d.enhancements.blockedWords=['封印','STOP','  ','封印'];return d;}

test('五种公开格式和本地短建议均可提取；源字符串与数组不变',()=>{
  const items=['一','🧭二','三'];for(const tag of ['', 'p','li','span'])for(const prefix of [i=>(i+1)+'. ',i=>String.fromCharCode(65+i)+'. ',()=>'- ']){
    const source='正文\n\n'+block(items,tag,prefix)+'\n结束',result=projectSuggestions(source,true);
    assert.deepEqual(result.choices,items);assert.equal(result.display,'正文\n\n结束');assert.equal(result.diagnostics.length,0);assert.ok(source.includes('<suggested_questions>'));
  }
  assert.deepEqual(items,['一','🧭二','三']);
});
test('关闭无转换；CRLF、Unicode及内容首尾空白原样保留',()=>{
  const source=block([' 一 ','🧭','尾 ']).replaceAll('\n','\r\n');assert.deepEqual(projectSuggestions(source,false),{display:source,choices:[],diagnostics:[]});
  assert.deepEqual(projectSuggestions(source,true).choices,[' 一 ','🧭','尾 ']);
});
test('代码围栏、缩进代码、注释与不活动HTML不变成建议',()=>{
  const b=block();for(const source of ['```html\n'+b+'\n```','~~~~\n'+b+'\n~~~~','<!--示例，不是建议\n'+b+'\n-->','<div title="\n'+b+'\n">x</div>',...['pre','code','script','style','textarea','template','noscript'].map(t=>'<'+t+'>\n'+b+'\n</'+t+'>')]){
    assert.equal(projectSuggestions(source,true).display,source);assert.deepEqual(projectSuggestions(source,true).choices,[]);
  }
  const code='```html\n<script>\n```\n';assert.equal(projectSuggestions(code+b,true).choices.length,3);
});
test('建议内容看似标签时仅作为文本解码；不执行或接受属性/嵌套标签',()=>{
  const source=block(['&lt;img onerror=&quot;x&quot;&gt;','&#x1f9ed; &amp; &#65;','&#0; &unknown;']);
  assert.deepEqual(projectSuggestions(source,true).choices,['<img onerror="x">','🧭 & A','&#0; &unknown;']);
  for(const broken of [block(['<b>字</b>','二','三']),block().replace('<suggested_questions>','<suggested_questions bad="x">'),block(undefined,'span').replace('<span>','<span title="x">')])assert.deepEqual(projectSuggestions(broken,true).choices,[]);
});
test('未闭合、缩进、空行、错误序号、空白项与嵌套块保留并诊断',()=>{
  for(const source of [block().replace('</suggested_questions>',''),block().replace('1.',' 1.'),block().replace('2.','6.'),block().replace('2.','2.\n'),block([' ','二','三']),block(['一','二']),block(['一',block(),'三'])]){
    const r=projectSuggestions(source,true);assert.equal(r.display,source);assert.deepEqual(r.choices,[]);assert.ok(r.diagnostics.length);
  }
});
test('多个合法块顺序稳定；合法块提取不吞掉相邻非法块',()=>{
  const bad=block(['单项']);const source=block()+'\n'+bad+'\n'+block(['甲','乙','丙'],'p');const r=projectSuggestions(source,true);
  assert.deepEqual(r.choices,['检查信封','询问守灯人','环顾四周','甲','乙','丙']);assert.equal(r.display,bad+'\n');assert.equal(r.diagnostics.length,1);
});
test('容量按Unicode计数；超过总数或单项时整份不部分提取',()=>{
  assert.equal(projectSuggestions(block(['🧭'.repeat(2000),'二','三']),true).choices.length,3);
  for(const source of [block(['🧭'.repeat(2001),'二','三']),Array(7).fill(block()).join('\n')]){
    const r=projectSuggestions(source,true);assert.equal(r.display,source);assert.deepEqual(r.choices,[]);assert.equal(r.diagnostics[0].code,'SUGGESTIONS_CAPACITY');
  }
  assert.equal(projectSuggestions('x'.repeat(limits.sourceUnits+1),true).diagnostics[0].code,'SUGGESTIONS_SOURCE_LIMIT');
});
test('空白、重复、空字符串与移除均按原站已观察行为保存',()=>{
  const words=['甲','甲','  '],result=addBlockedWord(words,'  乙  ');assert.deepEqual(result.words,['甲','甲','  ','  乙  ']);assert.deepEqual(words,['甲','甲','  ']);
  assert.deepEqual(addBlockedWord(words,'').words,words);assert.deepEqual(removeBlockedWord(words,'甲'),['  ']);assert.deepEqual(addBlockedWord(words,'甲').words,['甲','甲','  ','甲']);
});
test('屏蔽词只按提供的玩家原文做区分大小写字面匹配，无正则语义',()=>{
  assert.equal(checkPlayerInput('stop',['STOP']).allowed,true);assert.equal(checkPlayerInput('xSTOPy',['STOP']).allowed,false);
  assert.equal(checkPlayerInput('封印前行',['封印']).allowed,false);assert.equal(checkPlayerInput('普通',['.*']).allowed,true);
  assert.equal(checkPlayerInput('空  格',['  ']).allowed,false);assert.equal(checkPlayerInput('文本',['']).allowed,true);
  assert.deepEqual(checkPlayerInput('甲',['甲','甲']).matches,[{index:0,word:'甲'},{index:1,word:'甲'}]);
});
test('非法配置与输入失败关闭；容量诊断不会截断输入或词条',()=>{
  assert.equal(addBlockedWord([], '🧭'.repeat(200)).issue,null);assert.equal(addBlockedWord([], '🧭'.repeat(201)).issue,'BLOCKED_WORD_INVALID');
  assert.equal(addBlockedWord(Array(1000).fill('x'),'y').words.length,1000);assert.ok(blockedWordsIssue(['\ud800']));assert.ok(addBlockedWord(null,'x').issue);
  assert.equal(checkPlayerInput('a',null).allowed,false);assert.equal(checkPlayerInput('🧭'.repeat(120001),[]).allowed,false);
});
test('展示转换不改来源hash；关闭和开局路径无建议注入',()=>{
  const source='<p>原始正文</p>\n'+block(),on=compile(source),off=compile(source,false),opening=compile(source,true,{mode:'opening'});
  assert.equal(on.manifest.sourceHash,off.manifest.sourceHash);assert.notEqual(on.manifest.renderHash,off.manifest.renderHash);
  assert.ok(on.manifest.assistanceHash);assert.equal(off.manifest.suggestions,undefined);assert.equal(opening.manifest.suggestions,undefined);
  assert.equal(on.patch.bodyHtml.includes('检查信封'),false);assert.equal(off.patch.bodyHtml.includes('检查信封'),true);assert.deepEqual(on.manifest.suggestions,['检查信封','询问守灯人','环顾四周']);
});
test('无效块的诊断与原文展示并存；模型脚本继续删除',()=>{
  const source='<script>alert(1)</script>\n'+block(['一']),result=compile(source);assert.ok(result.manifest.diagnostics.some(d=>d.code==='SUGGESTIONS_INVALID'));
  assert.equal(result.patch.bodyHtml.includes('<script'),false);assert.equal(result.patch.bodyHtml,compile(source,false).patch.bodyHtml);assert.ok(result.patch.bodyHtml.includes('一'));assert.deepEqual(result.manifest.suggestions,[]);
});
test('服务端只接受作品建议设置；未保存快照可预览但不改草稿',async t=>{
  const {request,host}=await fixture(t),d=document();d.enhancements.suggestions=false;
  const id=(await request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  const body={workId:id,expectedRevision:0,document:structuredClone(d),mode:'reply',reply:block()};
  assert.equal((await request('/presentations/prepare',{...body,suggestions:true})).status,422);
  const off=await request('/presentations/prepare',body);assert.equal(off.value.manifest.suggestions,undefined);
  body.document.enhancements.suggestions=true;const on=await request('/presentations/prepare',body);assert.equal(on.status,200);assert.equal(on.value.manifest.suggestions.length,3);assert.equal(on.value.manifest.sourceHash,off.value.manifest.sourceHash);
  assert.deepEqual((await request('/works/'+id)).value.draft,d);
  for(const path of ['/input-assistance.mjs','/input-assistance-ui.mjs','/input-assistance-ui.css'])assert.equal((await fetch(host.origin+path)).status,200);
  assert.equal((await fetch(host.origin+'/rpg-app/api/authoring/send')).status,404);
});
test('保存刷新、重复操作和多标签冲突保留建议与屏蔽词原文',async t=>{
  const {request}=await fixture(t),d=document(),id=(await request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  d.enhancements.blockedWords.push('  两侧 🧭  ');const body={operationId:'save',expectedRevision:0,document:d};
  assert.equal((await request('/works/'+id+'/save',body)).status,200);assert.equal((await request('/works/'+id+'/save',body)).value.replayed,true);
  assert.equal((await request('/works/'+id+'/save',{...body,operationId:'other'})).status,409);assert.deepEqual((await request('/works/'+id)).value.draft,d);
  assert.ok(capabilityDeclaration(d).required.includes('suggested-inputs'));assert.ok(capabilityDeclaration(d).required.includes('blocked-inputs'));assert.doesNotThrow(()=>validateDocument(d));
});
test('旧发布版本、完整包新目录导入与以后草稿互不覆盖',async t=>{
  const a=await fixture(t),b=await fixture(t),d=document(),id=(await a.request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  const version=(await a.request('/works/'+id+'/publish',{operationId:'seal',expectedRevision:0})).value.result.versionId;
  const next=structuredClone(d);next.enhancements.suggestions=false;next.enhancements.blockedWords=[];await a.request('/works/'+id+'/save',{operationId:'edit',expectedRevision:1,document:next});
  const pack=(await a.request('/works/'+id+'/export?kind=package&version='+version)).value;assert.deepEqual(pack.configuration.document,d);
  const report=(await b.request('/imports/inspect',{payload:pack})).value,imported=(await b.request('/imports',{operationId:'import',expectedRevision:null,workId:null,payload:pack,expectedImportHash:report.payloadHash})).value;
  assert.deepEqual((await b.request('/works/'+imported.workId)).value.draft,d);
});
test('提交前失败保留旧值；提交后未知只核对原操作不重复落盘',async t=>{
  let phaseFailure=null,count=0;const {request}=await fixture(t,{fault:phase=>{if(phase==='afterCommit')count++;if(phase===phaseFailure){phaseFailure=null;throw Error('test fault');}}});
  const d=document(),id=(await request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;d.enhancements.blockedWords.push('later');
  phaseFailure='beforeCommit';assert.equal((await request('/works/'+id+'/save',{operationId:'failed',expectedRevision:0,document:d})).value.error.code,'AUTHORING_WRITE_FAILED');assert.equal((await request('/works/'+id)).value.draft.enhancements.blockedWords.includes('later'),false);
  phaseFailure='afterCommit';assert.equal((await request('/works/'+id+'/save',{operationId:'unknown',expectedRevision:0,document:d})).value.error.code,'AUTHORING_RESULT_UNKNOWN');
  const before=count;assert.equal((await request('/operations/recover',{operationId:'unknown',workId:id})).status,200);assert.equal(count,before);assert.deepEqual((await request('/works/'+id)).value.draft,d);
});


test('独立注释建议可提取；注释示例、属性与代码内仍被隔离',()=>{
 const b=block(),wrapped='<!--\n'+b+'\n-->';
 assert.deepEqual(projectSuggestions(wrapped,true).choices,['检查信封','询问守灯人','环顾四周']);
 assert.equal(projectSuggestions(wrapped,true).display,'<!--\n-->');
 assert.deepEqual(projectSuggestions(wrapped,false),{display:wrapped,choices:[],diagnostics:[]});
 for(const s of ['```html\n'+wrapped+'\n```','<pre>\n'+wrapped+'\n</pre>','<div title="'+wrapped+'">x</div>','<!--说明\n'+b+'\n-->','<!--\n'+b+'\n尾注-->','<!--\n'+b])assert.deepEqual(projectSuggestions(s,true).choices,[]);
 assert.deepEqual(projectSuggestions(wrapped.replaceAll('\n','\r\n'),true).choices,projectSuggestions(wrapped,true).choices);
});
