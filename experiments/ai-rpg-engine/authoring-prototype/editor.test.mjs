import {mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import assert from 'node:assert/strict';
import {selection, editSource, SourceHistory} from './editor.mjs';
const tick = String.fromCharCode(96);
const selected = text => selection(text,0,text.length,'backward');

// Expected text and caret positions are captured from the original editor,
// including its "wrap" separator and insertion (not replacement) for heading/table.
test('inline tools wrap the exact selected source and collapse at the observed caret', () => {
  const source=selection('前🐟中文<u>x</u>\n\n后',1,6,'backward');
  for(const [tool,before,after] of [['bold','**','**'],['italic','*','*'],['underline','++','++'],['strike','~~','~~'],['code',tick,tick],['link','[',']()']]){
    const result=editSource(source,tool);
    assert.equal(result.text,'前'+before+'🐟中文<'+after+'u>x</u>\n\n后');
    assert.equal(result.start,1+before.length);assert.equal(result.end,result.start);
  }
  assert.deepEqual(source,selection('前🐟中文<u>x</u>\n\n后',1,6,'backward'));
});
for(const [tool,text,caret] of [
  ['bold','**甲乙\n丙丁**',2],['italic','*甲乙\n丙丁*',1],
  ['underline','++甲乙\n丙丁++',2],['strike','~~甲乙\n丙丁~~',2],
  ['code',tick+'甲乙\n丙丁'+tick,1],['link','[甲乙\n丙丁]()',1],
  ['quote','\n> 甲乙\n丙丁\n',3],['bullet','* 甲乙\n* 丙丁',9],
  ['ordered','1. 甲乙\n2. 丙丁',11],['wrap','---',3],
  ['block','\n'+tick.repeat(3)+'\n甲乙\n丙丁\n'+tick.repeat(3)+'\n',5],
]){
  test('original multiline selection fixture: '+tool,()=>{
    assert.deepEqual(editSource(selected('甲乙\n丙丁'),tool),selection(text,caret));
  });
}
test('empty selections insert syntax without adding placeholder content',()=>{
  for(const [tool,text,caret] of [
    ['bold','****',2],['italic','**',1],['underline','++++',2],['strike','~~~~',2],
    ['code',tick+tick,1],['link','[]()',1],['bullet','* ',2],['ordered','1. ',3],
    ['quote','\n> \n',3],['wrap','---',3],['block','\n'+tick.repeat(3)+'\n\n'+tick.repeat(3)+'\n',5],
  ])assert.deepEqual(editSource(selection(''),tool),selection(text,caret));
});
test('headings insert a stub before the selection and preserve all original text',()=>{
  for(let level=1;level<=6;level++){
    const prefix='\n'+'#'.repeat(level)+' ';
    assert.deepEqual(editSource(selected('## 甲\n乙'),'heading',{level}),selection(prefix+'\n## 甲\n乙',prefix.length));
  }
  assert.deepEqual(editSource(selected('甲乙\n丙丁'),'heading',{level:2}),selection('\n## \n甲乙\n丙丁',4));
  assert.deepEqual(editSource(selection('甲乙',2),'heading',{level:3}),selection('甲乙\n### \n',7));
  for(const level of [0,7,1.2,'2',null])assert.throws(()=>editSource(selected('甲'),'heading',{level}));
});
test('lists operate on the selection including blank lines with observed paragraph boundaries',()=>{
  for(const [tool,prefix,caret] of [['bullet','* ',5],['ordered','1. ',6]]){
    assert.deepEqual(editSource(selection('前中后',1,2),tool),selection('前\n'+prefix+'中\n\n后',caret));
    assert.deepEqual(editSource(selection('前\n中\n后',2,3),tool),selection('前\n'+prefix+'中\n\n后',caret));
  }
  assert.deepEqual(editSource(selected('甲\n\n乙'),'bullet'),selection('* 甲\n* \n* 乙',10));
  assert.deepEqual(editSource(selected('甲\n\n乙'),'ordered'),selection('1. 甲\n2. \n3. 乙',13));
  assert.deepEqual(editSource(selection('前\n\n中\n\n后',3,4),'bullet'),selection('前\n\n* 中\n\n后',6));
});
test('quote, block and link wrap the selection without inspecting author syntax',()=>{
  assert.deepEqual(editSource(selection('前中后',1,2),'quote'),selection('前\n> 中\n后',4));
  assert.deepEqual(editSource(selection('前中后',1,2),'block'),selection('前\n'+tick.repeat(3)+'\n中\n'+tick.repeat(3)+'\n后',6));
  assert.deepEqual(editSource(selection('前中后',1,2),'link'),selection('前[中]()后',2));
  assert.deepEqual(editSource(selected('**甲**'),'bold'),selection('****甲****',2));
  const embedded=tick.repeat(3)+'html\n<p>中文</p>\n'+tick.repeat(3);
  assert.equal(editSource(selected(embedded),'block').text,'\n'+tick.repeat(3)+'\n'+embedded+'\n'+tick.repeat(3)+'\n');
});
test('large and CRLF source is not parsed or silently normalized by the text core',()=>{
  const text=('x'+tick).repeat(100000);
  assert.equal(editSource(selected(text),'code').text,tick+text+tick);
  assert.equal(editSource(selection('前\r\n甲\r\n后',3,4),'bold').text,'前\r\n**甲**\r\n后');
  assert.equal(editSource(selected('甲\r\n\r\n乙'),'bullet').text,'* 甲\r\n* \r\n* 乙');
});
test('table grid inserts exact Head/Data cells without deleting the selection or moving the caret',()=>{
  const one='| Head |\n| --- |\n| Data |';
  assert.deepEqual(editSource(selection(''),'table',{columns:1,rows:1}),selection(one,0));
  assert.deepEqual(editSource(selection('前\n后',2),'table',{columns:1,rows:1}),selection('前\n'+one+'\n\n后',2));
  assert.deepEqual(editSource(selection('前\n中\n后',2,3),'table',{columns:1,rows:1}),selection('前\n'+one+'\n\n中\n后',2));
  const three='| Head | Head | Head |\n| --- | --- | --- |\n| Data | Data | Data |\n| Data | Data | Data |';
  assert.deepEqual(editSource(selection('前中后',1,2),'table',{columns:3,rows:2}),selection('前\n'+three+'\n\n中后',1));
  const maximum=editSource(selection(''),'table',{columns:6,rows:4}).text.split('\n');
  assert.equal(maximum.length,6);assert.equal(maximum[0].split('Head').length-1,6);
  for(const [columns,rows] of [[0,1],[7,1],[1,0],[1,5],[1.5,1],[1,NaN]])assert.throws(()=>editSource(selected('原文'),'table',{columns,rows}));
});
test('image aliases are local resource references and labels cannot break syntax',()=>{
  assert.equal(editSource(selection('前后',1),'image',{alias:'harbor.blue',label:'图[1]\\'}).text,'前![图\\[1\\]\\\\](asset://harbor.blue)后');
  assert.equal(editSource(selection('前后',1),'image',{alias:'image-example'}).text,'前![](asset://image-example)后');
  for(const alias of ['../x','x/y','Upper','1abc','a..b','a'.repeat(81),'harbor\n','https://host'])assert.throws(()=>editSource(selected('原文'),'image',{alias}));
  for(const label of ['坏\n名字','坏\r名字',42])assert.throws(()=>editSource(selected('原文'),'image',{alias:'image-example',label}));
});
test('clear is reversible and restores a backward selection', () => {
  const before=selection('甲🐟乙\n',1,4,'backward'),history=new SourceHistory(before);
  history.commit(before,editSource(before,'clear'));
  assert.deepEqual(history.undo(),before);
  assert.equal(history.redo().text,'');
});
test('typing replacement history captures the pre-input selection, not the post-input caret', () => {
  const before=selection('甲中文乙',1,3,'backward'),after=selection('甲新乙',2),history=new SourceHistory(before);
  history.commit(before,after);assert.deepEqual(history.undo(),before);assert.deepEqual(history.redo(),after);
});
test('sequential formatting, typing, undo and redo keep their own text and ranges', () => {
  const first=selected('中文'),history=new SourceHistory(first);
  const bold=editSource(first,'bold');history.commit(first,bold);
  const typed=selection('**新字**',4);history.commit(bold,typed);
  assert.deepEqual(history.undo(),bold);assert.deepEqual(history.undo(),first);
  assert.deepEqual(history.redo(),bold);assert.deepEqual(history.redo(),typed);
});
test('new edits after undo remove redo; unchanged operations do not create history', () => {
  const first=selection('甲',1),history=new SourceHistory(first);
  assert.equal(history.commit(first,first),false);
  history.commit(first,selection('甲乙',2));history.undo();
  history.commit(first,selection('甲丙',2));
  assert.equal(history.redo(),null);assert.equal(history.past.length,1);
});
test('composition has one transaction and cannot undo intermediate candidates', () => {
  const before=selection('前候选后',1,3,'backward'),history=new SourceHistory(before);
  history.startComposition(before);
  history.remember(selection('前pin后',4));
  assert.equal(history.undo(),null);assert.equal(history.redo(),null);assert.equal(history.past.length,0);
  history.finishComposition(selection('前拼音后',3));
  assert.equal(history.past.length,1);assert.deepEqual(history.undo(),before);
  assert.equal(history.redo().text,'前拼音后');
});
test('cancelled composition and duplicate final input are not extra undo steps', () => {
  const before=selection('中文',2),history=new SourceHistory(before);
  history.startComposition(before);assert.equal(history.finishComposition(before),false);
  history.startComposition(before);const after=selection('中文输入',4);
  history.finishComposition(after);history.commit(after,after);
  assert.equal(history.past.length,1);
});
test('history is per field and rejects a stale transaction', () => {
  const first=new SourceHistory(selected('甲')),second=new SourceHistory(selected('乙'));
  first.commit(first.current,editSource(first.current,'bold'));
  assert.equal(second.undo(),null);assert.equal(second.current.text,'乙');
  assert.throws(()=>first.commit(selected('过期'),selected('覆盖')));
});
test('history limits drop old transactions without changing current source', () => {
  const history=new SourceHistory(selection(''),{historySteps:3,historyBytes:64});
  for(let i=1;i<=20;i++)history.commit(history.current,selection('字'.repeat(i),i));
  assert.equal(history.current.text,'字'.repeat(20));assert.equal(history.past.length,1);
  assert.equal(history.undo().text,'字'.repeat(19));
  assert.equal(history.undo(),null);
});
test('selection bounds remain UTF-16 based while source text is untouched', () => {
  assert.deepEqual(selection('🐟中文',-3,99,'backward'),{text:'🐟中文',start:0,end:4,direction:'backward'});
  assert.equal(editSource(selection('🐟中文',0,2),'bold').text,'**🐟**中文');
  assert.throws(()=>editSource(selected('x'),'unsupported'));
});

// Optional browser event fixture; generated only on explicit local test invocation.
if (process.argv.includes('--write-browser-fixture')) {
  const directory=fileURLToPath(new URL('../.rpg04-work/authoring-b2a-repair/',import.meta.url));
  await mkdir(directory,{recursive:true});
  await writeFile(new URL('../.rpg04-work/authoring-b2a-repair/ime-fixture.mjs',import.meta.url),String.raw`
import {authoringEditor as editor} from './app.mjs';
const read = () => {
  const area = document.querySelector('#source-description'), toolbar = area.closest('.editor');
  return {text:area.value,start:area.selectionStart,end:area.selectionEnd,direction:area.selectionDirection,
    undoDisabled:toolbar.querySelector('[data-tool="undo"]').disabled,redoDisabled:toolbar.querySelector('[data-tool="redo"]').disabled,
    allToolsDisabled:[...toolbar.querySelectorAll('[data-tool]')].every(button=>button.disabled)};
};
const report = label => { document.querySelector('#fixture-result').textContent=label+'\n'+JSON.stringify(read()); };
document.querySelector('#fixture-start').onclick=()=>{
  editor.load({...editor.read(),description:'前候选后',cg:[]},[]);
  const area=document.querySelector('#source-description');
  area.focus();area.setSelectionRange(1,3,'backward');
  area.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:''}));
  area.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,inputType:'insertCompositionText',data:'pin',isComposing:true}));
  area.value='前pin后';area.setSelectionRange(4,4);
  area.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertCompositionText',data:'pin',isComposing:true}));
  report('组合输入中（合成事件）');
};
for(const [id,endFirst] of [['fixture-end-first',true],['fixture-input-first',false]]){
  document.querySelector('#'+id).onclick=()=>{
    const area=document.querySelector('#source-description');
    if(endFirst)area.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'拼音'}));
    area.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,inputType:'insertFromComposition',data:'拼音'}));
    area.value='前拼音后';area.setSelectionRange(3,3);
    area.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertFromComposition',data:'拼音'}));
    if(!endFirst)area.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'拼音'}));
    setTimeout(()=>report('组合输入已提交（合成事件）'),5);
  };
}
document.querySelector('#fixture-history').onclick=()=>{
  const area=document.querySelector('#source-description');
  area.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,cancelable:false,inputType:'historyUndo'}));
  area.value='浏览器另一条原生撤销历史';
  area.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'historyUndo'}));
  report('不可取消的原生撤销输入（合成事件）');
};
`,'utf8');
  await writeFile(new URL('../.rpg04-work/authoring-b2a-repair/ime-server.mjs',import.meta.url),String.raw`
import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
const root=process.argv[2], fixture=process.argv[3];
const markup='<section class="prototype-note"><strong>合成事件验收，不代表系统输入法</strong><button id="fixture-start">开始组合输入</button><button id="fixture-end-first">先结束事件再最终输入</button><button id="fixture-input-first">先最终输入再结束事件</button><button id="fixture-history">不可取消的原生撤销事件</button><output id="fixture-result">等待测试</output></section><script type="module" src="./fixture.mjs"></script>';
http.createServer(async(req,res)=>{
  try{
    const files={'/':'index.html','/app.mjs':'app.mjs','/editor.mjs':'editor.mjs','/styles.css':'styles.css'};
    if(req.method!=='GET'||req.headers.host!=='127.0.0.1:18534')throw Error();
    let text;
    if(req.url==='/fixture.mjs')text=await readFile(fixture,'utf8');
    else {if(!files[req.url])throw Error();text=await readFile(resolve(root,files[req.url]),'utf8');}
    if(req.url==='/')text=text.replace('<body>','<body>'+markup);
    res.writeHead(200,{'Content-Type':req.url==='/'?'text/html; charset=utf-8':req.url.endsWith('.css')?'text/css':'text/javascript; charset=utf-8','Cache-Control':'no-store'});res.end(text);
  }catch{res.writeHead(404);res.end('Fixture route unavailable');}
}).listen(18534,'127.0.0.1');
`,'utf8');
}
