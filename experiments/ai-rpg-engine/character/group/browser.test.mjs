import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {mountGroupUI} from './browser.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');
const html=await readFile(new URL('./page.html',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const base=()=>({id:'group-abc123',revision:0,scene:'河边茶亭',members:[{id:'player',kind:'player',name:'玩家',active:true},{id:'one',kind:'character',name:'同名旅人',active:true,modelId:'offline-a'},{id:'two',kind:'character',name:'同名旅人',active:true,modelId:'offline-b'}],messages:[],requests:[],budget:{calls:8,usedCalls:0,microUsd:300000,usedMicroUsd:0}});
async function page(t,{mode='fixture',group=base(),cards=true,url='http://localhost/groups?id=group-abc123',onPost,storage=null}={}){
 const dom=new JSDOM(html,{url});t.after(()=>dom.window.close());const {window}=dom;window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};window.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new window.Event('close'));};
 if(storage)window.sessionStorage.setItem('rpg-group-setup:1',JSON.stringify(storage));
 const calls=[],data={csrf:'test',mode,models:[{id:'offline-a',label:'离线 A'},{id:'offline-b',label:'离线 B'}],cards:cards?[{name:'中性角色',version:2,reference:{workId:'work-fixture',versionId:'version-fixture'},openings:[{kind:'ordinary',texts:['你好，旅人。']},{kind:'group',texts:['大家好。']}]}]:[],groups:[group]};
 const fetch=async(path,init)=>{calls.push({path,...init});if(init.method==='POST'){const body=JSON.parse(init.body);if(onPost)return onPost(path,body,group);if(path.endsWith('/send')){if(body.payload.input!==null)group.messages.push({id:'player-'+group.messages.length,speakerId:'player',content:body.payload.input,source:{kind:'player'}});group.messages.push({id:'reply-'+group.messages.length,speakerId:body.payload.targetId,content:'离线回复',source:{kind:'model-result',modelId:'offline-a'}});group.revision++;}else if(path.endsWith('/leave'))group.members.find(m=>m.id===body.payload.memberId).active=false;return {ok:true,json:async()=>structuredClone(group)};}return {ok:true,json:async()=>structuredClone(path==='/api/bootstrap'?data:group)};};
 await mountGroupUI({document:window.document,window,fetch,location:window.location,history:window.history,crypto:window.crypto});const $=id=>window.document.getElementById(id);const change=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new window.Event('change'));};const input=text=>{$('input').value=text;$('input').dispatchEvent(new window.Event('input'));};const flush=async()=>{await tick();await tick();};return {$,window,calls,group,data,change,input,flush};
}
test('send and continue keep explicit responder; continue preserves unsent draft and caret',async t=>{
 const p=await page(t);p.change('target','two');p.input('还没发送的草稿');p.$('input').setSelectionRange(2,5);p.$('continue').click();await p.flush();const posts=p.calls.filter(x=>x.method==='POST');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(posts[0].body).payload,{targetId:'two',input:null});assert.equal(p.group.messages.length,1);assert.equal(p.$('input').value,'还没发送的草稿');assert.equal(p.$('input').selectionStart,2);assert.equal(p.$('target').value,'two');assert.equal(p.window.document.activeElement,p.$('input'));
 p.$('send-form').dispatchEvent(new p.window.Event('submit',{cancelable:true}));await p.flush();assert.equal(p.group.messages.length,3);assert.equal(p.$('input').value,'');assert.equal(p.$('send').disabled,true);
});
test('unknown request locks operations, retains budget and only queries original request',async t=>{
 const group=base();group.requests=[{id:'original',status:'unknown',targetId:'one',canRecover:true,partial:'未完成原文'}];group.budget.usedCalls=1;group.budget.usedMicroUsd=50000;const p=await page(t,{group});assert.ok(p.$('send').disabled);assert.ok(p.$('target').disabled);assert.ok(p.$('join').disabled);assert.match(p.$('budget').textContent,/剩余调用 7/);assert.match(p.$('request-panel').textContent,/结果未知/);assert.equal(p.$('messages').textContent.includes('未完成原文'),false);p.$('request-panel').querySelector('button').click();await p.flush();const posts=p.calls.filter(x=>x.method==='POST');assert.equal(posts.length,1);assert.ok(posts[0].path.endsWith('/recover'));assert.deepEqual(JSON.parse(posts[0].body),{requestId:'original'});assert.ok(p.$('send').disabled);
});
test('leaving selected member requires a new selection instead of silently changing responder',async t=>{
 const p=await page(t);p.change('target','one');p.$('member-list').querySelector('button').click();await p.flush();assert.equal(p.$('target').value,'');assert.equal(p.$('send').disabled,true);assert.equal(p.$('continue').disabled,true);assert.equal(p.calls.filter(x=>x.path.endsWith('/send')).length,0);
});
test('mobile member dialog closes with focus return; same-group refresh does not drop draft',async t=>{
 const p=await page(t);p.$('toggle-members').click();assert.equal(p.$('member-dialog').open,true);assert.equal(p.$('members').parentElement,p.$('member-dialog'));p.$('close-members').click();assert.equal(p.$('members').parentElement,p.$('member-dock'));assert.equal(p.window.document.activeElement,p.$('toggle-members'));p.input('保留草稿');p.$('refresh').click();await p.flush();assert.equal(p.$('input').value,'保留草稿');assert.equal(p.calls.filter(x=>x.method==='POST').length,0);
});
test('catalog creation shows pinned versions, group opening, batch model and home search',async t=>{
 const p=await page(t,{url:'http://localhost/groups'});assert.equal(p.$('welcome-history').hidden,false);p.$('search').value='不存在';p.$('search').dispatchEvent(new p.window.Event('input'));assert.match(p.$('group-list').textContent,/没有匹配/);p.$('start').click();await p.flush();assert.equal(p.$('setup').hidden,false);p.change('batch-model','offline-b');assert.equal(p.$('model-0').value,'offline-b');p.$('add-slot').click();assert.equal(p.$('model-2').value,'offline-b');assert.match(p.$('slots').textContent,/固定版本 2/);const option=[...p.$('opening').options].find(o=>o.text.includes('群聊开场'));p.change('opening',option.value);assert.equal(p.$('opening-preview').textContent,'大家好。');assert.match(p.$('create-summary').textContent,/3 位角色/);
});
test('empty catalog and disabled execution do not offer callable send actions',async t=>{
 const p=await page(t,{cards:false,url:'http://localhost/groups'});p.$('new').click();await p.flush();assert.ok(p.$('create').disabled);assert.match(p.$('error').textContent,/尚无可用/);
 const q=await page(t,{mode:'disabled'});q.change('target','one');q.input('你好');assert.ok(q.$('send').disabled);assert.ok(q.$('continue').disabled);assert.match(q.$('send-hint').textContent,/未开放发送/);
});
test('double click dispatches once, navigation locks while unresolved local send, failure retains draft',async t=>{
 let release;const waiting=new Promise(resolve=>release=resolve);const p=await page(t,{onPost:async()=>{await waiting;return {ok:false,json:async()=>({error:'GROUP_REVISION_CONFLICT'})};}});p.change('target','one');p.input('不能丢失');p.$('continue').click();p.$('continue').click();assert.ok(p.$('new').disabled);assert.ok(p.$('history').disabled);release();await p.flush();assert.equal(p.calls.filter(x=>x.method==='POST').length,1);assert.equal(p.$('input').value,'不能丢失');assert.match(p.$('error').textContent,/其他页面更新/);
});
test('malicious scene and response are text, failed requests visible, completed requests collapse',async t=>{
 const group=base();group.scene='<img src=x onerror=alert(1)>';group.messages=[{id:'one',speakerId:'one',content:'<script>alert(1)</script>',source:{kind:'model-result',modelId:'offline-a'}}];group.requests=[{id:'r1',status:'completed',targetId:'one'}];const p=await page(t,{group});assert.equal(p.$('scene-label').children.length,0);assert.equal(p.$('messages').querySelectorAll('script').length,0);assert.equal(p.$('request-panel').querySelector('details').open,false);
 group.requests[0].status='failed';const q=await page(t,{group});assert.equal(q.$('request-panel').querySelector('details'),null);assert.match(q.$('request-panel').textContent,/失败/);
});

test('new and refreshed creation catalogs preserve version selection by reference, not list position',async t=>{
 const p=await page(t,{url:'http://localhost/groups'});p.$('new').click();await p.flush();
 p.$('scene').value='保留场景';p.change('card-0','0');p.change('model-0','offline-b');
 const old=structuredClone(p.data.cards[0]);p.data.cards.unshift({...structuredClone(old),version:3,reference:{...old.reference,versionId:'new-version'}});
 p.$('refresh').click();await p.flush();assert.equal(p.$('card-0').selectedOptions[0].textContent,'中性角色 · 版本 2');assert.equal(p.$('model-0').value,'offline-b');assert.equal(p.$('scene').value,'保留场景');assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
 p.$('cancel-create').click();p.$('new').click();await p.flush();assert.match(p.$('card-0').selectedOptions[0].textContent,/版本 3/);
});
test('studio deep link preselects exact version; missing version cannot silently create with another',async t=>{
 const p=await page(t,{url:'http://localhost/groups?create=1&work=work-fixture&version=version-fixture'});assert.equal(p.$('setup').hidden,false);assert.match(p.$('card-0').selectedOptions[0].textContent,/版本 2/);
 const q=await page(t,{url:'http://localhost/groups?create=1&work=work-fixture&version=missing'});assert.equal(q.$('card-0').value,'');assert.equal(q.$('create').disabled,true);assert.match(q.$('error').textContent,/指定的角色版本/);assert.equal(q.calls.filter(c=>c.method==='POST').length,0);q.$('refresh').click();await q.flush();assert.equal(q.$('card-0').value,'');assert.equal(q.$('create').disabled,true);
});

test('preferred character is paired with a distinct default character and navigation returns to studio',async t=>{
 const p=await page(t,{url:'http://localhost/groups'});p.data.charactersUrl='http://127.0.0.1:43132/characters';p.data.cards.push({...structuredClone(p.data.cards[0]),name:'另一个角色',reference:{workId:'work-another',versionId:'version-another'}});
 p.$('new').click();await p.flush();assert.notEqual(p.$('card-0').value,p.$('card-1').value);assert.equal(p.$('character-library').hidden,false);assert.equal(p.$('character-library').href,p.data.charactersUrl);
});

test('creation draft restores references and fields in the tab, guards name-only edits and consumes deep link',async t=>{
 const p=await page(t,{url:'http://localhost/groups?create=1&work=work-fixture&version=version-fixture'});assert.equal(new URL(p.window.location.href).search,'?create=1');
 p.$('player').value='临时称呼';p.$('player').dispatchEvent(new p.window.Event('input',{bubbles:true}));
 const leave=new p.window.Event('beforeunload',{cancelable:true});p.window.dispatchEvent(leave);assert.ok(leave.defaultPrevented);
 p.$('scene').value='未提交场景';p.$('scene').dispatchEvent(new p.window.Event('input',{bubbles:true}));p.change('card-0','0');p.change('model-0','offline-b');p.$('create-form').dispatchEvent(new p.window.Event('change',{bubbles:true}));
 const saved=JSON.parse(p.window.sessionStorage.getItem('rpg-group-setup:1'));assert.equal(saved.selected[0].key,'work-fixture/version-fixture');assert.equal(saved.fields.scene,'未提交场景');
 const q=await page(t,{url:'http://localhost/groups?create=1',storage:saved});assert.equal(q.$('scene').value,'未提交场景');assert.equal(q.$('player').value,'临时称呼');assert.equal(q.$('model-0').value,'offline-b');assert.match(q.$('card-0').selectedOptions[0].textContent,/版本 2/);assert.equal(q.calls.filter(c=>c.method==='POST').length,0);
 saved.selected[0].key='work-fixture/removed-version';const missing=await page(t,{url:'http://localhost/groups?create=1',storage:saved});assert.equal(missing.$('card-0').value,'');assert.ok(missing.$('create').disabled);
});
