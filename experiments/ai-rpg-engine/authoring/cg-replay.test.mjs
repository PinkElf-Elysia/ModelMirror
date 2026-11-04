import test from 'node:test';
import {runInNewContext} from 'node:vm';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {createCGUI} from './cg-ui.mjs';
import {parseFragment} from 'parse5';
import {createCGReplayCache,CG_REPLAY_LIMITS} from './cg-replay.mjs';
import {compilePresentation} from './presentation.mjs';
import {startAuthoringServer} from './server.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=','base64');
const asset=alias=>({alias,fileName:alias+'.png',size:png.length,sha256:sha256(png),mediaType:'image/png'});
const entry=(extra={})=>({id:'chat',name:'雾港插图',categoryId:null,enabled:true,roles:['assistant'],rule:'or',keywords:['雾港'],target:'chat',mode:'weighted',images:[{alias:'a',weight:1},{alias:'b',weight:1}],...extra});
function sample(extra={}){const document=blankDocument();document.enhancements.cg.entries=[entry(),entry({id:'bg',name:'夜景',target:'background',mode:'regular',images:[{alias:'b',weight:100}]})];return {current:{id:'work-one',revision:0,assets:[asset('a'),asset('b')]},document,reply:'雾港的灯亮了',request:{action:'create',operationId:'sample-one',previousOperationId:null,user:'询问信使',device:'desktop'},...extra};}
const error=code=>({code});
const render=view=>compilePresentation({mode:'reply',html:'雾港的灯亮了',origin:'http://127.0.0.1:18572',token:'a'.repeat(48),resources:[{...asset('a'),bytes:png},{...asset('b'),bytes:png}],cgView:view});

test('宿主只抽中一次，同操作重试和显式重放不重抽；返回值不可反改缓存',()=>{
 let draws=0;const c=createCGReplayCache({draw:()=>{draws++;return .8;}}),r=sample(),one=c.prepare(r);assert.equal(draws,1);assert.equal(one.plan.chat[0].alias,'b');
 one.plan.chat[0].alias='forged';const two=c.prepare(r);assert.equal(two.plan.chat[0].alias,'b');r.request.action='replay';assert.deepEqual(c.prepare(r),two);assert.equal(draws,1);
});
test('未命中不携带抽样结果；禁用和无效规则不抽图；失败不会重试',()=>{
 let draws=0;const c=createCGReplayCache({draw:()=>{draws++;return .3;}}),r=sample();r.reply='无事发生';const miss=c.prepare(r);assert.equal(miss.plan.chat.length,0);assert.ok(miss.plan.decisions.every(d=>d.draw===null));
 r.request.operationId='disabled';r.document.enhancements.cg.entries.forEach(e=>e.enabled=false);const before=draws;c.prepare(r);assert.equal(draws,before);
 r.request.operationId='bad';r.document.enhancements.cg.entries[0].enabled=true;r.document.enhancements.cg.entries[0].images=[];assert.throws(()=>c.prepare(r));assert.equal(draws,before);assert.throws(()=>c.prepare(r));assert.equal(draws,before);
});
test('容量按实际抽中图片计算，不用第一候选的体积误阻断',()=>{
 const r=sample();r.current.assets[0].size=5*1024*1024;r.current.assets[0].sha256='a'.repeat(64);r.current.assets.push({...asset('large'),size:5*1024*1024,sha256:'c'.repeat(64)});
 r.document.enhancements.cg.entries=[entry(),entry({id:'second',images:[{alias:'large',weight:1},{alias:'b',weight:1}]})];
 const view=createCGReplayCache({draw:()=>.8}).prepare(r);assert.equal(view.plan.chat.length,2);assert.equal(view.plan.usage.bytes,png.length);
});
test('同操作的原文、玩家输入、配置、revision、资源、背景链和设备变化均冲突',()=>{
 const c=createCGReplayCache(),r=sample();c.prepare(r);
 for(const change of [q=>q.reply+='!',q=>q.request.user+='!',q=>q.document.sources.suffix='后置词',q=>q.current.revision++,q=>q.current.assets[0].sha256='a'.repeat(64),q=>q.request.previousOperationId='future',q=>q.request.device='mobile']){const q=structuredClone(r);change(q);assert.throws(()=>c.prepare(q),error('AUTHORING_CG_REPLAY_CONFLICT'));}
});
test('过期留墓碑，重启/跨作品的重放不创建记录；容量不悄悄淘汰',()=>{
 let now=1,draws=0;const c=createCGReplayCache({now:()=>now,draw:()=>{draws++;return 0;}}),r=sample();c.prepare(r);now+=CG_REPLAY_LIMITS.ttl;
 assert.throws(()=>c.prepare(r),error('AUTHORING_CG_REPLAY_EXPIRED'));assert.equal(draws,1);r.request.action='replay';assert.throws(()=>createCGReplayCache().prepare(r),error('AUTHORING_CG_REPLAY_NOT_FOUND'));
 const other=structuredClone(r);other.current.id='other';assert.throws(()=>c.prepare(other),error('AUTHORING_CG_REPLAY_NOT_FOUND'));
 for(let i=1;i<CG_REPLAY_LIMITS.records;i++)c.prepare({...r,reply:'未命中',request:{...r.request,action:'create',operationId:'id-'+i}});
 assert.throws(()=>c.prepare({...r,request:{...r.request,action:'create',operationId:'too-many'}}),error('AUTHORING_CG_REPLAY_CAPACITY'));
});
test('连续回放只继承封存背景；分叉、无命中和默认桌面/手机背景',()=>{
 const c=createCGReplayCache(),r=sample(),first=c.prepare(r);assert.equal(first.backgroundSource,'selected');
 const next={...r,reply:'继续闲谈',request:{...r.request,operationId:'second',previousOperationId:'sample-one'}};assert.equal(c.prepare(next).backgroundSource,'inherited');assert.equal(c.prepare(next).background.alias,'b');
 const branch={...next,request:{...next.request,operationId:'branch',previousOperationId:'sample-one'}};assert.equal(c.prepare(branch).background.alias,'b');
 const initial=sample({reply:'无匹配'});initial.document.settings.media.desktop='a';initial.document.settings.media.mobile='b';initial.request.operationId='default';initial.request.device='mobile';assert.equal(c.prepare(initial).background.alias,'b');
 initial.request.operationId='desktop';initial.request.device='desktop';assert.equal(c.prepare(initial).background.alias,'a');
 next.request.operationId='future';next.document=structuredClone(next.document);next.document.sources.system='future';assert.throws(()=>c.prepare(next),error('AUTHORING_CG_REPLAY_PARENT'));
});
test('继承背景仍受本轮资源总上限约束，不静默丢图',()=>{
 const c=createCGReplayCache(),r=sample();r.document.enhancements.cg.entries=[r.document.enhancements.cg.entries[1],...Array.from({length:32},(_,i)=>entry({id:'many-'+i,mode:'regular',keywords:['下一轮']}))];c.prepare(r);r.request.previousOperationId=r.request.operationId;r.reply='下一轮';r.request.operationId='overflow';assert.throws(()=>c.prepare(r),error('AUTHORING_CG_OUTPUT_CAPACITY'));
});
test('展示加入完整插图和背景，原文hash不变；HTML标签文本不能经CG标签注入脚本',()=>{
 const r=sample();r.document.enhancements.cg.entries[0].name='<script>alert(1)</script>"';const view=createCGReplayCache({draw:()=>.8}).prepare(r),result=render(view),plain=render(null);
 assert.equal(result.manifest.sourceHash,plain.manifest.sourceHash);assert.notEqual(result.manifest.renderHash,plain.manifest.renderHash);assert.match(result.patch.bodyHtml,/data-cg-entry="chat"/);assert.match(result.patch.bodyHtml,/&lt;script&gt;/);assert.match(result.patch.styleText,/background-image:url\("data:image\/png/);
 assert.equal(result.manifest.authorScriptsExecuted,false);assert.equal(result.manifest.resources.length,1);assert.equal(plain.manifest.cg,undefined);assert.doesNotMatch(result.frame,/<script>alert/);
 assert.throws(()=>compilePresentation({mode:'opening',html:'',origin:'http://127.0.0.1:18572',token:'a'.repeat(48),cgView:view}),error('AUTHORING_PRESENTATION_MODE'));
});
test('选中资源sha或类型变化拒绝；未解析资源只在发现阶段给出缺失诊断',()=>{
 const view=createCGReplayCache().prepare(sample());assert.throws(()=>compilePresentation({mode:'reply',html:'',origin:'http://127.0.0.1:18572',token:'a'.repeat(48),cgView:view,resources:[{...asset('b'),sha256:'0'.repeat(64),bytes:png}]}));
 const discovery=compilePresentation({mode:'reply',html:'',origin:'http://127.0.0.1:18572',token:'a'.repeat(48),cgView:view});assert.ok(discovery.manifest.diagnostics.some(d=>d.code==='RESOURCE_MISSING'));
});
const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b5dc/tests/',import.meta.url));await mkdir(parent,{recursive:true});
async function fixture(t){const host=await startAuthoringServer({directory:await mkdtemp(join(parent,'http-'))});t.after(()=>host.close());const call=async(path,body)=>{const r=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};const r=sample();r.document.metadata.name='CG离线核验';const id=(await call('/works',{operationId:'create',expectedRevision:null,document:r.document})).value.workId;for(const [i,alias]of ['a','b'].entries())assert.equal((await call('/works/'+id+'/assets',{operationId:'upload-'+alias,expectedRevision:i,alias,fileName:alias+'.png',mediaType:'image/png',base64:png.toString('base64')})).status,200);return {host,call,body:{workId:id,expectedRevision:2,document:r.document,mode:'reply',messageRole:'assistant',reply:r.reply,cg:r.request}};}
test('HTTP实际展示用原文匹配，正则/建议/CG插图不递归；不修改作品',async t=>{
 const {host,call,body}=await fixture(t);const before=(await call('/works/'+body.workId)).value;
 body.document.enhancements.displayRules=[{id:'hide',name:'只改显示',enabled:true,pattern:'雾港',replacement:'普通城市',flags:'g',scope:'assistant'}];
 const result=await call('/presentations/prepare',body);assert.equal(result.status,200,JSON.stringify(result.value));assert.equal(result.value.manifest.cg.plan.chat.length,1);assert.equal(result.value.manifest.cg.backgroundSource,'selected');
 const frame=await(await fetch(host.origin+result.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).text();assert.match(frame,/普通城市/);assert.match(frame,/data-cg-entry="chat"/);assert.match(frame,/background-image/);
 assert.deepEqual((await call('/works/'+body.workId)).value,before);
 await call('/presentations/release',{token:result.value.token});body.cg.action='replay';const replay=await call('/presentations/prepare',body);assert.equal(replay.status,200);assert.deepEqual(replay.value.manifest.cg,result.value.manifest.cg);assert.equal(replay.value.manifest.renderHash,result.value.manifest.renderHash);
});
test('HTTP同操作并发稳定，变更原文冲突；旧画面仍可查看',async t=>{
 const {host,call,body}=await fixture(t);const results=await Promise.all([call('/presentations/prepare',body),call('/presentations/prepare',body)]);assert.ok(results.every(r=>r.status===200));assert.deepEqual(results[0].value.manifest.cg,results[1].value.manifest.cg);
 const changed=await call('/presentations/prepare',{...body,reply:'其他原文'});assert.equal(changed.status,409);assert.equal(changed.value.error.code,'AUTHORING_CG_REPLAY_CONFLICT');assert.equal((await fetch(host.origin+results[0].value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).status,200);
});
test('HTTP禁止客户端随机数、计划或不完整消息角色，旧无CG路径仍可用',async t=>{
 const {call,body}=await fixture(t);for(const cg of [{...body.cg,draws:[]},{...body.cg,plan:{}},{...body.cg,complete:false}])assert.equal((await call('/presentations/prepare',{...body,cg})).value.error.code,'AUTHORING_UNKNOWN_FIELD');
 assert.equal((await call('/presentations/prepare',{...body,mode:'opening'})).value.error.code,'AUTHORING_PRESENTATION_ROLE');
 assert.equal((await call('/presentations/prepare',{...body,messageRole:'user'})).value.error.code,'AUTHORING_CG_REPLAY_ROLE');
 const {cg,...plain}=body;const result=await call('/presentations/prepare',plain);assert.equal(result.status,200);assert.equal(result.value.manifest.cg,undefined);
 const missing=await call('/presentations/prepare',{...body,cg:{...cg,action:'replay',operationId:'missing'}});assert.equal(missing.status,404);
});
test('浏览器仅显式按钮创建，pagehide释放画面不释放CG记录；引用按作品保存',async()=>{
 const source=await readFile(new URL('./browser.mjs',import.meta.url),'utf8');assert.match(source,/create-cg-replay.*onclick=\(\)=>renderPreview\('reply',true\)/);assert.match(source,/else if\(previewCG\)cgRequest=.*action:'replay'/);assert.match(source,/sessionStorage.setItem\(cgReplayKey/);assert.match(source,/modelmirror.authoring.cg-replay\/.*work\?\.id/);
});

test('浏览器背景链上下文按内容比较，保存刷新后的JSON键顺序不产生假变化',async()=>{
 const source=await readFile(new URL('./browser.mjs',import.meta.url),'utf8');const fn=source.match(/export function cgReplayContext\(value\)\{[^\n]+/)[0].replace('export ','');const context=runInNewContext('('+fn+')');
 const a={workId:'a',revision:2,document:{cg:{images:[{alias:'x',weight:1}],target:'chat'},media:{mobile:null,desktop:'blue'}}};const b={document:{media:{desktop:'blue',mobile:null},cg:{target:'chat',images:[{weight:1,alias:'x'}]}},revision:2,workId:'a'};
 assert.equal(context(a),context(b));b.document.cg.images[0].weight=2;assert.notEqual(context(a),context(b));b.revision++;assert.notEqual(context(a),context(b));
});

test('CG实际渲染的控件不触发旧原型委托；关键词回车只更新当前模型',()=>{
 const saved=globalThis.document;globalThis.document={getElementById:()=>({})};let dirty=0,prevented=false;
 const r=sample(),root={innerHTML:'',querySelectorAll:()=>[],querySelector:()=>({value:'新关键词',focus(){}})};
 const ui=createCGUI({assets:()=>r.current.assets,assetUrl:()=>'',changed:()=>dirty++,toast:()=>{}});
 try{ui.load(r.document.enhancements.cg,'work-one');ui.render(root);
 const walk=n=>[n,...(n.childNodes||[]).flatMap(walk)];const attrs=walk(parseFragment(root.innerHTML)).flatMap(n=>n.attrs||[]).map(a=>a.name);
 assert.ok(attrs.includes('data-author-cg-action'));assert.ok(attrs.includes('data-author-cg-keyword'));assert.ok(!attrs.includes('data-cg-action'));assert.ok(!attrs.includes('data-cg-keyword'));
 root.onkeydown({key:'Enter',isComposing:false,preventDefault(){prevented=true;},target:{matches:s=>s==='[data-author-cg-keyword]',closest:()=>({dataset:{cgId:'chat'}})}});
 assert.ok(prevented);assert.equal(dirty,1);assert.ok(ui.read().entries[0].keywords.includes('新关键词'));
 }finally{if(saved===undefined)delete globalThis.document;else globalThis.document=saved;}
});
