import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {mountCharacterStudio,updateBookEntry,appendBookEntry} from './editor.mjs';
import {blankDocument} from '../authoring/contracts.mjs';
import {characterDocument} from './document.mjs';
const {JSDOM}=createRequire(new URL('../../../client/package.json',import.meta.url))('jsdom');
const html=await readFile(new URL('./editor.html',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){const d=characterDocument(blankDocument());Object.assign(d.character.card.data,{name:'中性旅人',description:'第一行\n第二行',personality:'耐心',first_mes:'你好，旅人。',tags:['原创'],alternate_greetings:['另一个开场'],character_book:{extensions:{vendor:{untouched:true}},entries:[{keys:['茶','桥'],content:'旧茶馆',enabled:true,insertion_order:0,extensions:{unknown:[1,2]},priority:8}]}});d.metadata.name='中性旅人';return d;}
async function page(t,{url='http://localhost/characters',onPost,session,storage={}}={}){
 const dom=new JSDOM(html,{url});t.after(()=>dom.window.close());const {window}=dom;window.confirm=()=>true;window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};window.HTMLDialogElement.prototype.close=function(){this.open=false;};
 for(const [key,value] of Object.entries(storage))window.localStorage.setItem(key,JSON.stringify(value));
 const work={id:'work-fixture',revision:0,draft:fixture(),versions:[{id:'version-1',number:1}]},published=structuredClone(work.draft),calls=[],streams=[];
 const state=session||{id:'session-1',revision:0,turns:0,binding:{reference:{workId:work.id,versionId:'version-1'},player:{name:'玩家'}},messages:[{role:'assistant',content:'你好，旅人。'}],operations:{},settings:{memoryEnabled:false,historyTurns:null},memories:[]};
 class Events{constructor(url){this.url=url;streams.push(this);}close(){this.closed=true;}}
 const fetch=async(path,init={})=>{const body=init.body?JSON.parse(init.body):null;calls.push({path,method:init.method,body});let value;
  if(body&&onPost){const result=await onPost(path,body);if(result)return result;}
  if(path.endsWith('/characters/works'))value=[{id:work.id,name:work.draft.character.card.data.name,tags:[],versions:work.versions.length}];
  else if(path.endsWith('/characters/navigation'))value={groupsUrl:'http://127.0.0.1:43133/groups'};
  else if(path.endsWith('/characters/blank'))value=fixture();
  else if(path.includes('/published?'))value={draft:published};
  else if(path.endsWith('/characters/preview'))value={assembly:{messages:[{role:'assistant',content:body.document.character.card.data.first_mes}]},report:{format:'chara_card_v2',warnings:[]}};
  else if(path.endsWith('/save')){assert.equal(body.expectedRevision,work.revision);work.draft=body.document;work.revision++;value={workId:work.id};}
  else if(path.endsWith('/publish')){work.versions.push({id:'version-2',number:2});value={result:{versionId:'version-2'}};}
  else if(path.endsWith('/characters/status'))value={mode:'offline-fixture'};
  else if(path.endsWith('/characters/models'))value={models:[]};
  else if(path.includes('/sessions'))value=state;
  else if(path.endsWith('/works')&&body){work.draft=body.document;value={workId:work.id};}
  else if(path.endsWith('/'+work.id))value=work;
  else throw Error('Unexpected '+path);
  return {ok:true,json:async()=>structuredClone(value)};
 };
 const app=await mountCharacterStudio({window,document:window.document,fetch,EventSource:Events});t.after(()=>app.close());const $=s=>window.document.querySelector(s),flush=async()=>{for(let i=0;i<6;i++)await tick();};const input=(s,v)=>{$(s).value=v;$(s).dispatchEvent(new window.Event('input',{bubbles:true}));};const change=(s,v)=>{$(s).value=v;$(s).dispatchEvent(new window.Event('change',{bubbles:true}));};return {$,window,calls,work,published,state,streams,input,change,flush};
}
test('library searches and opens a focused editor; advanced JSON starts collapsed',async t=>{
 const p=await page(t);assert.equal(p.window.document.body.dataset.screen,'library');assert.equal(p.$('#library-grid').children.length,1);p.input('#library-search','不存在');assert.equal(p.$('#library-grid').children.length,0);assert.equal(p.$('#library-empty').hidden,false);p.input('#library-search','原创');p.$('#library-grid button').click();await p.flush();assert.equal(p.window.document.body.dataset.screen,'edit');assert.equal(p.$('#basics').hidden,false);assert.equal(p.$('#definition').hidden,true);p.$('[data-section="definition"]').click();assert.equal(p.$('#definition').hidden,false);assert.equal(p.$('#basics').hidden,true);assert.equal(p.window.document.activeElement,p.$('#definition h2'));assert.equal(p.$('#book-json').closest('details').open,false);assert.equal(p.$('#context-preview').closest('details').open,false);
});
test('visual knowledge editing preserves unknown fields and multiline keywords',async t=>{
 const p=await page(t,{url:'http://localhost/characters?work=work-fixture'});p.$('[data-section="book"]').click();assert.equal(p.$('#book-entries textarea').value,'茶\n桥');p.input('#book-entries textarea','茶\n庭院');const fields=p.window.document.querySelectorAll('#book-entries textarea');fields[1].value='新的设定\n第二段';fields[1].dispatchEvent(new p.window.Event('input'));p.$('#save').click();await p.flush();assert.equal(p.$('#error').hidden,true);const b=p.work.draft.character.card.data.character_book;assert.deepEqual(b.extensions,{vendor:{untouched:true}});assert.deepEqual(b.entries[0].extensions,{unknown:[1,2]});assert.equal(b.entries[0].priority,8);assert.deepEqual(b.entries[0].keys,['茶','庭院']);assert.equal(b.entries[0].content,'新的设定\n第二段');
});
test('malformed advanced data blocks save without replacing the original card',async t=>{
 const p=await page(t,{url:'http://localhost/characters?work=work-fixture'});p.input('#book-json','{broken');p.change('#book-json','{broken');p.$('#save').click();await p.flush();assert.equal(p.$('#error').hidden,false);assert.equal(p.calls.filter(c=>c.path.endsWith('/save')).length,0);assert.equal(p.$('#book-json').value,'{broken');assert.equal(p.work.draft.character.card.data.character_book.entries[0].content,'旧茶馆');
});
test('draft save and version preview stay separate; saved version remains read only',async t=>{
 const p=await page(t,{url:'http://localhost/characters?work=work-fixture'});p.input('[data-field="description"]','修改后的草稿');p.input('#tag-input','中文，多行');p.$('#save').click();await p.flush();assert.equal(p.work.draft.character.card.data.description,'修改后的草稿');assert.deepEqual(p.work.draft.character.card.data.tags,['中文','多行']);assert.equal(p.published.character.card.data.description,'第一行\n第二行');p.change('#versions','version-1');await p.flush();assert.equal(p.$('[data-field="description"]').value,'第一行\n第二行');assert.equal(p.$('[data-field="description"]').readOnly,true);assert.equal(p.$('#add-book').disabled,true);assert.equal(p.$('#book-entries input').disabled,true);p.$('#show-preview').click();await p.flush();assert.equal(p.window.document.body.dataset.screen,'preview');assert.equal(p.$('#start-chat').disabled,false);assert.equal(p.calls.filter(c=>c.path.endsWith('/send')).length,0);
});
test('unknown request refresh never sends and recovery queries only its original operation',async t=>{
 const session={id:'session-1',revision:3,turns:0,binding:{reference:{workId:'work-fixture',versionId:'version-1'},player:{name:'玩家'}},messages:[{role:'assistant',content:'你好，旅人。'}],operations:{original:{state:'unknown',input:'保留的输入',canRecover:true,partial:'未完成文本'}},settings:{memoryEnabled:false,historyTurns:null},memories:[]};
 const p=await page(t,{url:'http://localhost/characters?session=session-1',session});assert.equal(p.window.document.body.dataset.screen,'chat');assert.equal(p.$('#send').disabled,true);assert.equal(p.$('#messages').textContent.includes('未完成文本'),false);assert.equal(p.$('#partial').textContent,'未完成文本');p.$('#refresh-chat').click();await p.flush();p.$('#recover').click();await p.flush();const sends=p.calls.filter(c=>c.path.endsWith('/send'));assert.equal(sends.length,0);assert.deepEqual(p.calls.find(c=>c.path.endsWith('/recover')).body,{operationId:'original'});assert.equal(p.$('#messages').querySelectorAll('.assistant').length,1);
});
test('save conflict and double click retain input and send only one write',async t=>{
 let release;const wait=new Promise(r=>release=r);const p=await page(t,{url:'http://localhost/characters?work=work-fixture',onPost:async(path)=>{if(path.endsWith('/save')){await wait;return {ok:false,json:async()=>({error:{code:'AUTHORING_REVISION_CONFLICT'}})};}}});p.input('[data-field="name"]','未保存的名字');p.$('#save').click();p.$('#save').click();assert.equal(p.$('#to-library').disabled,true);release();await p.flush();assert.equal(p.calls.filter(c=>c.path.endsWith('/save')).length,1);assert.equal(p.$('[data-field="name"]').value,'未保存的名字');assert.match(p.$('#error').textContent,/修订发生冲突/);assert.ok(p.window.localStorage.getItem('rpg-character-draft:http://localhost:work-fixture'));
});
test('library and preview treat character text as text, never markup',async t=>{
 const p=await page(t,{url:'http://localhost/characters?work=work-fixture'});p.input('[data-field="name"]','<img src=x onerror=alert(1)>');p.input('[data-field="description"]','<script>bad()</script>');p.$('#show-preview').click();await p.flush();assert.equal(p.$('#preview-name').children.length,0);assert.equal(p.$('#preview-description').children.length,0);assert.equal(p.$('#preview-description').textContent,'<script>bad()</script>');
});
test('book pure helpers preserve extension payloads and reject malformed structures',()=>{
 const book=JSON.stringify(fixture().character.card.data.character_book);const updated=JSON.parse(updateBookEntry(book,0,'enabled',false));assert.equal(updated.entries[0].enabled,false);assert.deepEqual(updated.entries[0].extensions,{unknown:[1,2]});assert.equal(JSON.parse(appendBookEntry(book)).entries.length,2);assert.throws(()=>appendBookEntry('[]'));assert.throws(()=>updateBookEntry(book,10,'content','x'));
});

test('preview creates a local pinned version before opening a session',async t=>{
 const p=await page(t,{url:'http://localhost/characters?work=work-fixture'});p.$('#show-preview').click();await p.flush();assert.equal(p.$('#start-chat').disabled,true);assert.equal(p.$('#make-version').hidden,false);p.$('#make-version').click();await p.flush();assert.equal(p.window.document.body.dataset.screen,'preview');assert.equal(p.$('#make-version').hidden,true);assert.equal(p.$('#start-chat').disabled,false);assert.equal(p.calls.filter(c=>c.path.endsWith('/publish')).length,1);assert.equal(p.calls.filter(c=>c.path.endsWith('/send')).length,0);
});
test('invalid visual entry data is retained and reported instead of crashing the editor',async t=>{
 const p=await page(t,{url:'http://localhost/characters?work=work-fixture'});p.change('#book-json','{"entries":[null]}');assert.match(p.$('#book-entries').textContent,/原文已保留/);p.$('#save').click();await p.flush();assert.equal(p.$('#book-json').value,'{"entries":[null]}');assert.match(p.$('#error').textContent,/知识条目格式不正确/);assert.equal(p.calls.filter(c=>c.path.endsWith('/save')).length,0);
});

test('studio navigation links the selected immutable version to group creation',async t=>{
 const p=await page(t,{url:'http://localhost/characters?work=work-fixture'});
 assert.equal(p.$('#groups-navigation').href,'http://127.0.0.1:43133/groups');assert.equal(p.$('#create-group').hidden,true);
 p.$('#publish').click();await p.flush();const target=new URL(p.$('#create-group').href);
 assert.equal(p.$('#create-group').hidden,false);assert.equal(target.searchParams.get('work'),'work-fixture');assert.equal(target.searchParams.get('version'),'version-2');assert.equal(target.searchParams.get('create'),'1');
 assert.equal(p.calls.filter(c=>c.path.endsWith('/send')).length,0);
});

test('recovered unsaved drafts report unsaved, including a never-saved new character',async t=>{
 for(const id of ['work-fixture','new']){
  const document=fixture();document.character.card.data.name='未保存的恢复夹具';const backup={revision:id==='new'?null:0,document,book:JSON.stringify(document.character.card.data.character_book),tags:JSON.stringify(document.character.card.data.tags)};
  const p=await page(t,{url:'http://localhost/characters?section=basics'+(id==='new'?'':'&work='+id),storage:{['rpg-character-draft:http://localhost:'+id]:backup}});
  assert.equal(p.$('[data-field=name]').value,'未保存的恢复夹具');assert.equal(p.$('#save-state').textContent,'未保存');assert.equal(p.window.document.body.dataset.screen,'edit');assert.equal(p.$('#mobile-groups').href,p.$('#groups-navigation').href);
  const event=new p.window.Event('beforeunload',{cancelable:true});p.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);assert.equal(p.calls.filter(c=>c.body&&c.path.endsWith('/save')).length,0);
 }
});
