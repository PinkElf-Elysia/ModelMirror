import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {mountDailyPage} from './page.mjs';
const {JSDOM}=createRequire(new URL('../../../../client/package.json',import.meta.url))('jsdom');
const html=await readFile(new URL('./page.html',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const profile={profileId:'profile-a',profileRevision:0,name:'旧称呼',description:'旧描述',avatar:'a'.repeat(64)};
async function page(t,{items=[profile],post,bitmap}={}){
 const dom=new JSDOM(html,{url:'http://localhost/characters?desk=profiles'});t.after(()=>dom.window.close());const w=dom.window,d=w.document,$=id=>d.getElementById(id);let confirms=0,accept=false,closed=0;const calls=[];
 w.confirm=()=>{confirms++;return accept;};w.createImageBitmap=bitmap??(async()=>({width:640,height:480,close(){closed++;}}));
 w.HTMLCanvasElement.prototype.getContext=function(){return {drawImage(){}};};w.HTMLCanvasElement.prototype.toDataURL=function(){assert.equal(this.width,512);assert.equal(this.height,384);return 'data:image/png;base64,cG5n';};
 const fetch=async(path,init)=>{calls.push({path,...init});if(init.method==='POST'){const body=JSON.parse(init.body);if(post)return post(path,body);return {ok:true,json:async()=>path.endsWith('/avatars')?{id:'b'.repeat(64)}:{...profile,...body.payload,profileRevision:1}};}return {ok:true,json:async()=>items};};
 const app=await mountDailyPage({document:d,window:w,fetch});
 const edit=(id,value)=>{$(id).value=value;$(id).dispatchEvent(new w.Event('input',{bubbles:true}));};
 const unload=()=>{const e=new w.Event('beforeunload',{cancelable:true});w.dispatchEvent(e);return e.defaultPrevented;};
 const upload=async(file)=>{Object.defineProperty($('avatar-file'),'value',{configurable:true,writable:true,value:file?'C:\\fakepath\\avatar.png':''});Object.defineProperty($('avatar-file'),'files',{configurable:true,value:file?[file]:[]});await $('avatar-file').onchange();};
 return {w,d,$,app,calls,edit,unload,upload,accept:v=>accept=v,confirms:()=>confirms,closed:()=>closed};
}
const file=(type='image/png',text='pixels',size=10)=>({type,size,arrayBuffer:async()=>Buffer.from(text)});

test('declining profile switch and navigation preserves unsaved name, description and avatar',async t=>{
 const p=await page(t);p.$('profile-list').querySelector('button').click();p.edit('profile-name','未保存的称呼');p.edit('profile-description','中文第一行\n第二行');p.$('remove-avatar').click();p.$('new-profile').click();assert.equal(p.confirms(),1);assert.equal(p.$('profile-name').value,'未保存的称呼');assert.equal(p.$('profile-description').value,'中文第一行\n第二行');assert.equal(p.$('avatar-preview').hidden,true);assert.ok(p.unload());
 p.accept(true);p.$('new-profile').click();assert.equal(p.$('profile-name').value,'');assert.equal(p.app.isDirty(),false);assert.equal(p.unload(),false);assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});

test('save in flight locks editable controls and navigation; save failure preserves the draft',async t=>{
 let release;const gate=new Promise(r=>release=r);const p=await page(t,{post:async()=>{await gate;return {ok:false,json:async()=>({error:{code:'DAILY_REVISION_CONFLICT'}})};}});p.edit('profile-name','未保存');const saving=p.$('profile-form').onsubmit({preventDefault(){}});await tick();assert.ok(p.$('profile-name').disabled);assert.ok(p.$('profile-description').disabled);assert.ok(p.$('avatar-file').disabled);assert.ok(p.unload());const nav=new p.w.MouseEvent('click',{bubbles:true,cancelable:true});p.$('nav-sessions').dispatchEvent(nav);assert.equal(nav.defaultPrevented,true);
 release();await saving;await tick();assert.equal(p.$('profile-name').value,'未保存');assert.ok(p.app.isDirty());assert.equal(p.$('profile-name').disabled,false);assert.match(p.$('daily-error').textContent,/另一页面/);assert.equal(p.calls.filter(c=>c.method==='POST').length,1);
});

test('successful save clears dirty state only after the authoritative response',async t=>{
 let release;const gate=new Promise(r=>release=r);const p=await page(t,{post:async(_path,b)=>{await gate;return {ok:true,json:async()=>({...profile,...b.payload,profileRevision:1})};}});p.$('profile-list').querySelector('button').click();p.edit('profile-description','新描述');p.$('profile-form').dispatchEvent(new p.w.Event('submit',{cancelable:true}));assert.ok(p.app.isDirty());release();await tick();await tick();assert.equal(p.app.isDirty(),false);assert.match(p.$('profile-revision').textContent,/修订 2/);assert.equal(p.unload(),false);
});

for(const type of ['image/png','image/jpeg','image/webp'])test('avatar '+type+' is normalized and only committed with profile save',async t=>{
 const p=await page(t);p.$('profile-list').querySelector('button').click();await p.upload(file(type));const writes=p.calls.filter(c=>c.method==='POST');assert.equal(writes.length,1);assert.ok(writes[0].path.endsWith('/avatars'));assert.equal(JSON.parse(writes[0].body).payload.mediaType,'image/png');assert.ok(p.app.isDirty());assert.match(p.$('avatar-preview').src,/b{64}$/);assert.equal(p.closed(),1);assert.equal(p.$('avatar-file').value,'');
 p.$('profile-form').dispatchEvent(new p.w.Event('submit',{cancelable:true}));await tick();assert.equal(JSON.parse(p.calls.filter(c=>c.method==='POST').at(-1).body).payload.avatar,'b'.repeat(64));
});

for(const bad of [file('image/svg+xml'),file('image/png','pixels',5*1024*1024+1),file('image/png','acTL'),file('image/webp','ANIM')])test('unsupported avatar preserves previous selection and never uploads',async t=>{
 const p=await page(t);p.$('profile-list').querySelector('button').click();await p.upload(bad);assert.match(p.$('avatar-preview').src,/a{64}$/);assert.equal(p.calls.filter(c=>c.method==='POST').length,0);assert.equal(p.$('daily-error').hidden,false);assert.equal(p.$('avatar-file').value,'');
});

test('decode failure reports an actionable error and retains prior avatar',async t=>{
 const p=await page(t,{bitmap:async()=>{throw Error('decoder failed');}});p.$('profile-list').querySelector('button').click();await p.upload(file());assert.match(p.$('daily-error').textContent,/无法读取.*图片/);assert.match(p.$('avatar-preview').src,/a{64}$/);assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});

test('pending avatar protects navigation even before decoding finishes; duplicate selection is ignored',async t=>{
 let release;const gate=new Promise(r=>release=r);const p=await page(t,{bitmap:async()=>{await gate;return {width:640,height:480,close(){}};}});const uploading=p.upload(file());await tick();assert.ok(p.unload());assert.ok(p.$('avatar-file').disabled);await p.upload(file('image/jpeg'));release();await uploading;assert.equal(p.calls.filter(c=>c.method==='POST').length,1);
});

test('oversized decoded image is closed without uploading; cancelled chooser changes nothing',async t=>{
 let closed=0;const p=await page(t,{bitmap:async()=>({width:5000,height:5000,close(){closed++;}})});await p.upload(null);assert.equal(p.app.isDirty(),false);await p.upload(file());assert.equal(closed,1);assert.match(p.$('daily-error').textContent,/尺寸过大/);assert.equal(p.calls.filter(c=>c.method==='POST').length,0);
});


test('avatar upload rejection preserves prior asset and unsaved text without saving profile',async t=>{
 const p=await page(t,{post:async()=>({ok:false,json:async()=>({error:{code:'DAILY_RECOVERY_REQUIRED'}})})});p.$('profile-list').querySelector('button').click();p.edit('profile-description','不能丢失的中文修改');await p.upload(file());assert.match(p.$('avatar-preview').src,/a{64}$/);assert.equal(p.$('profile-description').value,'不能丢失的中文修改');assert.ok(p.app.isDirty());assert.match(p.$('daily-error').textContent,/保存结果待核对/);assert.equal(p.calls.filter(c=>c.method==='POST').length,1);assert.equal(p.$('avatar-file').value,'');
});
