import test from 'node:test';
import assert from 'node:assert/strict';
import {createAuthorSandbox,validateScriptEvent} from './sandbox.mjs';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {startAuthoringServer} from './server.mjs';
import {blankDocument} from './contracts.mjs';
const event=(target,controls=[],type='click')=>({type,target,key:'',controls});
const key=(html,id)=>new RegExp('<[^>]*id="'+id+'"[^>]*data-author-node="(n[0-9]+)"').exec(html)?.[1];
async function fixture(t,html,js=''){const sandbox=await createAuthorSandbox({html,js});t.after(()=>sandbox.close());return sandbox;}

test('普通 JS、内联事件、表单与脚本顺序在独立引擎中运行',async t=>{
  const runtime=await fixture(t,'<form id="f"><input id="name" name="name" value="初始"><select id="city" name="city"><option value="a">甲</option><option value="b">乙</option></select><input name="ready" type="checkbox" checked><button id="go" onclick="start()">开局</button></form><p id="result"></p><script>const label="来信";</script>',
    'function start(){const data=new FormData(document.getElementById("f"));document.getElementById("result").textContent=label+":"+data.get("name")+":"+data.get("city")+":"+data.get("ready");rpg.setInput(document.getElementById("result").textContent);}');
  const html=runtime.initial.html;
  const result=await runtime.step(event(key(html,'go'),[{key:key(html,'name'),value:'中文𠮷'}, {key:key(html,'city'),value:'b'}]));
  assert.match(result.html,/来信:中文𠮷:b:on/);assert.deepEqual(result.proposals,[{type:'input',payload:'来信:中文𠮷:b:on'}]);assert.doesNotMatch(result.html,/<script|onclick=/);
});
test('查询、类名、样式、新节点、innerHTML、初始化及闭包状态',async t=>{
  const runtime=await fixture(t,'<section id="box"><button id="add" data-action="add">新增</button><div id="list"></div><output id="count"></output></section>',
    'document.addEventListener("DOMContentLoaded",()=>{let count=0;document.querySelector("#box > button[data-action=add]").onclick=()=>{const item=document.createElement("p");item.classList.add("item");item.style.color="blue";item.innerHTML="<b>第"+(++count)+"项</b>";document.getElementById("list").append(item);document.getElementById("count").textContent=document.querySelectorAll("#box .item").length;};});');
  const button=key(runtime.initial.html,'add');await runtime.step(event(button));const result=await runtime.step(event(button));
  assert.match(result.html,/第2项/);assert.match(result.html,/color:blue/);assert.match(result.html,/<output[^>]*>2<\/output>/);
});
test('表单按钮、内部文字节点和回车桥接提交；preventDefault 可以取消默认提交',async t=>{
  const runtime=await fixture(t,'<form id="f"><input id="name"><button id="go"><span id="caption">开局</span></button><button id="cancel"><span id="cancel-caption">取消提交</span></button></form>',
    'document.getElementById("f").onsubmit=e=>{e.preventDefault();rpg.setInput("提交");};document.getElementById("cancel").onclick=e=>e.preventDefault();');
  assert.equal((await runtime.step(event(key(runtime.initial.html,'go')))).proposals[0].payload,'提交');
  assert.equal((await runtime.step(event(key(runtime.initial.html,'caption')))).proposals[0].payload,'提交');
  assert.equal((await runtime.step({...event(key(runtime.initial.html,'name'),[],'keydown'),key:'Enter'})).proposals[0].payload,'提交');
  assert.deepEqual((await runtime.step(event(key(runtime.initial.html,'cancel')))).proposals,[]);
  assert.deepEqual((await runtime.step(event(key(runtime.initial.html,'cancel-caption')))).proposals,[]);
});
test('串行复用与不同实例隔离；故障关闭，不提交半份状态',async t=>{
  const a=await fixture(t,'<button id="go">测试</button><p id="n">0</p>','let n=0;document.getElementById("go").onclick=()=>{document.getElementById("n").textContent=++n;if(n===2)throw new Error("fail after mutation");};');
  const b=await fixture(t,'<p id="n">独立</p>','if(typeof n!=="undefined")throw new Error("leak");');
  const first=await a.step(event(key(a.initial.html,'go')));assert.match(first.html,/>1<\/p>/);
  await assert.rejects(a.step(event(key(a.initial.html,'go'))),{code:'AUTHORING_SCRIPT_EXCEPTION'});
  await assert.rejects(a.step(event(key(a.initial.html,'go'))),{code:'AUTHORING_SCRIPT_CLOSED'});assert.match(b.initial.html,/独立/);
});
test('返回的异步拒绝使步骤失败；eval、生成器源码及模块仍拒绝',async()=>{
  for(const js of ['(function*(){}).constructor("yield 1")()','eval("1")'])await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js}),{code:'AUTHORING_SCRIPT_ASYNC_UNSUPPORTED'});
  await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js:'(async()=>{throw new Error("async failure")})()'}),{code:'AUTHORING_SCRIPT_EXCEPTION'});
  await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js:'import("node:fs")'}),{code:'AUTHORING_SCRIPT_SOURCE_UNSUPPORTED'});
  await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js:'Promise.reject(new Error("detached"))'}),{code:'AUTHORING_SCRIPT_EXCEPTION'});
});
test('初始化没有玩家手势，不能提出输入或复制候选',async()=>{
  for(const js of ['rpg.setInput("自动")','rpg.copyText("自动")'])await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js}),{code:'AUTHORING_SCRIPT_GESTURE_REQUIRED'});
  await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js:'__authorStep({type:"click",target:"n3",controls:[]});rpg.setInput("伪造")'}),{code:'AUTHORING_SCRIPT_EXCEPTION'});
});
test('没有网络、宿主 DOM、磁盘、进程、模块、存储；反射仍停留客体',async t=>{
  const forbidden=['fetch','XMLHttpRequest','WebSocket','Worker','localStorage','sessionStorage','location','parent','process','require','std','os','WebAssembly','__authorStep','__authorTake','__authorTrack','__parseFragment','__verifySource'];
  const runtime=await fixture(t,'<p id="n"></p>',`const names=${JSON.stringify(forbidden)};for(const name of names)if(typeof globalThis[name]!=='undefined')throw new Error(name);const other=Function('return globalThis')();if(other!==window||typeof other.process!=='undefined')throw new Error('escape');document.getElementById('n').textContent='隔离';`);
  assert.match(runtime.initial.html,/隔离/);assert.equal(Object.prototype.polluted,undefined);
});
test('async复制等待宿主真实回执；取消不触发成功或绕过回退',async t=>{
  const runtime=await fixture(t,'<button id="go">复制</button><p id="state">等待</p>', 'document.getElementById("go").onclick=async()=>{await navigator.clipboard.writeText("原文");document.getElementById("state").textContent="完成";};');
  const first=await runtime.step(event(key(runtime.initial.html,'go')));assert.match(first.html,/等待/);assert.equal(first.proposals[0].payload,'原文');
  const cancel=await runtime.step({...event('n1',[],'feedback'),feedback:[{id:first.proposals[0].id,ok:false}]});assert.doesNotMatch(cancel.html,/完成/);
  const second=await runtime.step(event(key(runtime.initial.html,'go')));
  const success=await runtime.step({...event('n1',[],'feedback'),feedback:[{id:second.proposals[0].id,ok:true}]});assert.match(success.html,/完成/);
  assert.deepEqual(success.proposals,[]);
});
test('定时器、动画帧、清除、异步等待及平台事件不能伪造玩家交接',async t=>{
  const runtime=await fixture(t,'<p id="state"></p>', 'let n=0;const id=setInterval(()=>document.getElementById("state").textContent=++n,50);setTimeout(()=>clearInterval(id),125);requestAnimationFrame(t=>document.body.dataset.frame=t);');
  let result=await runtime.step({...event('n1',[],'tick'),elapsed:60});assert.match(result.html,/>1<\/p>/);assert.match(result.html,/data-frame="60"/);
  await runtime.step({...event('n1',[],'tick'),elapsed:60});result=await runtime.step({...event('n1',[],'tick'),elapsed:60});assert.match(result.html,/>3<\/p>/);assert.equal(result.platform.scheduled,false);
  const bad=await fixture(t,'<p/>','setTimeout(()=>rpg.setInput("自动"),1);');await assert.rejects(bad.step({...event('n1',[],'tick'),elapsed:10}),{code:'AUTHORING_SCRIPT_GESTURE_REQUIRED'});
});
test('几何只读快照、SVG及Canvas命令与显示节点绑定，数量有界',async t=>{
  const runtime=await fixture(t,'<canvas id="c"/><svg><circle id="eye"/></svg><button id="go">绘制</button>', 'const c=document.getElementById("c");const ctx=c.getContext("2d");document.getElementById("go").onclick=()=>{const rect=c.getBoundingClientRect();document.getElementById("eye").setAttribute("transform","translate("+rect.width+",0)");ctx.fillStyle="blue";ctx.fillRect(0,0,rect.width,20);};');
  const result=await runtime.step({...event(key(runtime.initial.html,'go')),layouts:[{key:key(runtime.initial.html,'c'),width:300}]});
  assert.match(result.html,/translate\(300,0\)/);assert.equal(result.platform.effects[1][2],'fillRect');assert.equal(result.platform.effects[1][5],300);
  await assert.rejects(createAuthorSandbox({html:'<canvas id="c"></canvas>',js:'const c=document.getElementById("c").getContext("2d");for(let i=0;i<12001;i++)c.fill();'}),error=>['AUTHORING_PLATFORM_LIMIT','AUTHORING_SCRIPT_TIMEOUT'].includes(error.code));
});
test('不支持的 DOM、外部脚本、危险 innerHTML 与非法控制事件明确拒绝',async t=>{
  await assert.rejects(createAuthorSandbox({html:'<script src="https://example.test/a.js"></script>'}),{code:'AUTHORING_SCRIPT_SOURCE_UNSUPPORTED'});
  await assert.rejects(createAuthorSandbox({html:'<div></div>',js:'document.cookie="a=b";'}),{code:'AUTHORING_DOM_UNSUPPORTED'});
  await assert.rejects(createAuthorSandbox({html:'<div></div>',js:'document.body.innerHTML="<script>1</script>";'}),{code:'AUTHORING_SCRIPT_SOURCE_UNSUPPORTED'});
  const runtime=await fixture(t,'<p id="n">safe</p>');
  await assert.rejects(runtime.step(event('n999')), {code:'AUTHORING_SCRIPT_TARGET'});
  assert.throws(()=>validateScriptEvent(event('n1',[{key:'n1',value:7}])),{code:'AUTHORING_SCRIPT_SIZE'});
  assert.throws(()=>validateScriptEvent({...event('n1'),type:'network'}),{code:'AUTHORING_SCRIPT_EVENT'});
});
test('死循环、递归与堆耗尽中止；新实例仍可执行',async t=>{
  for(const js of ['while(true){}','function f(){return f()}f()','new ArrayBuffer(128*1024*1024)'])await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js}),error=>['AUTHORING_SCRIPT_TIMEOUT','AUTHORING_SCRIPT_RESOURCE_LIMIT'].includes(error.code));
  const runtime=await fixture(t,'<p id="n"></p>','document.getElementById("n").textContent="仍可用";');assert.match(runtime.initial.html,/仍可用/);
});
test('Worker 总时限可以在引擎初始化前强制收束',async()=>{
  await assert.rejects(createAuthorSandbox({html:'<p>safe</p>',js:'while(true){}'},{wallMs:1}),{code:'AUTHORING_SCRIPT_TIMEOUT'});
});

const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b4b/tests/',import.meta.url));
await mkdir(parent,{recursive:true});
async function httpFixture(t,js='let count=0;document.getElementById("go").onclick=()=>{document.getElementById("n").textContent=++count;rpg.setInput("第"+count+"次");};'){
  const directory=await mkdtemp(join(parent,'http-')),host=await startAuthoringServer({directory});t.after(()=>host.close());
  const api='/rpg-app/api/authoring';
  const post=async(path,body,headers={})=>{const response=await fetch(host.origin+api+path,{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:response.status,value:await response.json()};};
  const document=blankDocument();document.metadata.name='通用脚本';document.sources.system='不可进入脚本的主提示词';document.sources.startupHtml='<button id="go">运行</button><output id="n">0</output>';document.sources.startupJs=js;
  const created=await post('/works',{operationId:'create',expectedRevision:null,document}),workId=created.value.workId;
  const prepare=overrides=>post('/presentations/prepare',{workId,expectedRevision:0,document,mode:'opening',reply:'',...overrides});
  const prepared=await prepare();assert.equal(prepared.status,200);
  const frame=await(await fetch(host.origin+prepared.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).text();
  const request={token:prepared.value.token,operationId:'event-1',sequence:1,event:event(key(frame,'go'))};
  return {host,post,document,workId,prepared:prepared.value,frame,request,prepare};
}
test('HTTP事件重复返回同一结果，不重复随机/计数；原文hash稳定且未落盘',async t=>{
  const {post,request,prepared,host,workId}=await httpFixture(t);
  assert.equal(prepared.manifest.authorScriptsExecuted,true);
  const first=await post('/presentations/event',request);assert.equal(first.status,200);assert.match(first.value.patch.bodyHtml,/>1<\/output>/);
  const replay=await post('/presentations/event',request);assert.equal(replay.value.replayed,true);assert.deepEqual(replay.value.proposals,first.value.proposals);assert.equal(replay.value.manifest.renderHash,first.value.manifest.renderHash);
  const conflict=await post('/presentations/event',{...request,sequence:2});assert.equal(conflict.status,409);
  const second=await post('/presentations/event',{...request,operationId:'event-2',sequence:2});assert.equal(second.status,200);assert.match(second.value.patch.bodyHtml,/>2<\/output>/);
  assert.equal(second.value.manifest.sourceHash,prepared.manifest.sourceHash);assert.notEqual(second.value.manifest.scriptStateHash,prepared.manifest.scriptStateHash);
  const old=await post('/presentations/event',request);assert.equal(old.value.error.code,'AUTHORING_SCRIPT_SEQUENCE_CONFLICT');
  const saved=await(await fetch(host.origin+'/rpg-app/api/authoring/works/'+workId)).json();assert.equal(saved.revision,0);assert.equal(saved.draft.sources.startupHtml,'<button id="go">运行</button><output id="n">0</output>');
});
test('事件要求同源和合法序号；停止、版本变化后不能应用或重新执行',async t=>{
  const {post,request,document,workId}=await httpFixture(t);
  assert.equal((await post('/presentations/event',request,{Origin:'null'})).status,403);
  assert.equal((await post('/presentations/event',{...request,sequence:2})).value.error.code,'AUTHORING_SCRIPT_SEQUENCE_CONFLICT');
  const saved=await post('/works/'+workId+'/save',{operationId:'new-version',expectedRevision:0,document});assert.equal(saved.status,200);
  assert.equal((await post('/presentations/event',request)).value.error.code,'AUTHORING_REVISION_CONFLICT');
  assert.equal((await post('/presentations/event',request)).value.error.code,'AUTHORING_SCRIPT_CLOSED');
  const other=await httpFixture(t);assert.equal((await other.post('/presentations/stop',{token:other.prepared.token})).value.stopped,true);
  assert.equal((await other.post('/presentations/event',other.request)).value.error.code,'AUTHORING_SCRIPT_CLOSED');
  assert.equal((await fetch(other.host.origin+other.prepared.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).status,200);
});
test('脚本异常只保留最后完整画面；回复模式完全不执行作者/模型脚本',async t=>{
  const {post,request,frame,host,prepared,prepare,document}=await httpFixture(t,'document.getElementById("go").onclick=()=>{document.getElementById("n").textContent="半份状态";throw new Error("故意失败");};');
  assert.equal((await post('/presentations/event',request)).value.error.code,'AUTHORING_SCRIPT_EXCEPTION');
  const after=await(await fetch(host.origin+prepared.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).text();assert.equal(after,frame);assert.doesNotMatch(after,/半份状态/);
  document.sources.startupJs='while(true){}';
  const reply=await prepare({mode:'reply',reply:'<p>静态原文</p><script>while(true){}</script><button onclick="fetch(1)">普通按钮</button>'});
  assert.equal(reply.status,200);assert.equal(reply.value.manifest.authorScriptsExecuted,false);assert.ok(reply.value.manifest.diagnostics.some(d=>d.code==='SCRIPT_NOT_EXECUTED'));
});
test('最多4份活动脚本预览，释放后恢复容量，初始化失败不会占满槽位',async t=>{
  const {post,prepare,prepared,document}=await httpFixture(t);
  for(let i=0;i<3;i++)assert.equal((await prepare()).status,200);
  assert.equal((await prepare()).value.error.code,'AUTHORING_SCRIPT_CAPACITY');
  await post('/presentations/release',{token:prepared.token});
  const bad=structuredClone(document);bad.sources.startupJs='throw new Error("no")';
  for(let i=0;i<2;i++)assert.equal((await prepare({document:bad})).value.error.code,'AUTHORING_SCRIPT_EXCEPTION');
  assert.equal((await prepare()).status,200);
});
