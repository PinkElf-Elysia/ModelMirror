import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {runInNewContext} from 'node:vm';
import {parse} from 'parse5';
import {compilePresentation,compilePresentationBounded,createPresentationCache,presentationAliases,PRESENTATION} from './presentation.mjs';
import {blankDocument,sha256} from './contracts.mjs';
import {startAuthoringServer} from './server.mjs';

const origin='http://127.0.0.1:18546',token='a'.repeat(48);
const compile=options=>compilePresentation({mode:'opening',html:'<h1>雾港</h1>',origin,token,...options});
const walk=node=>[node,...(node.childNodes||[]).flatMap(walk)];
const nodes=frame=>walk(parse(frame));
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=','base64');
const asset={alias:'beacon',bytes:png,mediaType:'image/png',sha256:sha256(png)};
test('解析Worker保留资源与结果；超时停止本次解析，不运行作者脚本',async()=>{
  const input={mode:'opening',html:'<img src="asset://beacon">',css:'',origin,token,resources:[asset],authorJs:'while(true){}'};
  const result=await compilePresentationBounded(input);
  assert.equal(result.manifest.resources[0].sha256,asset.sha256);assert.equal(result.manifest.authorScriptsExecuted,false);
  await assert.rejects(compilePresentationBounded(input,{timeoutMs:1}),{code:'AUTHORING_PRESENTATION_TIMEOUT'});
  assert.equal((await compilePresentationBounded(input)).manifest.sourceHash,result.manifest.sourceHash);
});
test('展示隔离保留任意类名、自定义标签、表单、折叠和CSS动画；原文不改写',()=>{
  const html='<body class="unrelated-author"><details><summary>灯塔</summary><microscopic class="ocean-17">\n\n  中文 🐟</microscopic></details><label><input type="checkbox" hidden>翻面</label><form id="opening"><input name="姓名" value="林舟"></form></body>';
  const css='.ocean-17{color:purple;animation:flow 6s linear infinite}@keyframes flow{to{transform:translateX(-50%)}}label:has(input:checked){background:blue}';
  const result=compile({html,css});
  assert.match(result.frame,/<body class="unrelated-author">/);assert.match(result.frame,/<microscopic class="ocean-17">\n\n  中文 🐟/);
  assert.ok(result.frame.includes(css));assert.match(result.frame,/<input type="checkbox" hidden="">/);
  assert.equal(result.manifest.authorScriptsExecuted,false);assert.deepEqual(result.manifest.diagnostics,[]);
});
test('作者和模型源码中的script、事件、嵌套文档及SVG脚本不能进入展示执行面',()=>{
  const html='<script>window.marker=1</script><img src="https://invalid.test/p" onerror="marker=2"><iframe srcdoc="&lt;script>marker=3&lt;/script>"></iframe><meta http-equiv="refresh" content="0;url=https://invalid.test"><svg><script>marker=4</script></svg><form action="https://invalid.test"><button formaction="https://invalid.test">确定</button></form><a href="javascript:marker=5" target="_top">跳转</a>';
  for(const mode of ['opening','reply']){
    const result=compile({mode,html,authorJs:'window.marker=6'}),tree=nodes(result.frame);
    assert.equal(tree.filter(n=>n.tagName==='script').length,1);
    assert.ok(tree.every(n=>!['iframe','object','base','link'].includes(n.tagName)));
    assert.ok(tree.filter(n=>n.tagName==='svg').every(n=>!n.childNodes.some(child=>child.tagName==='script')));
    assert.ok(tree.flatMap(n=>n.attrs||[]).every(a=>!a.name.startsWith('on')&&!['srcdoc','action','formaction','target'].includes(a.name)));
    assert.doesNotMatch(result.frame,/marker=|javascript:/);assert.match(result.csp,/script-src-attr 'none'/);
    assert.match(result.csp,/sandbox allow-scripts;/);assert.doesNotMatch(result.csp,/allow-same-origin|unsafe-eval|script-src 'unsafe-inline'/);
    assert.match(result.csp,/connect-src 'none'/);assert.match(result.csp,/frame-src 'none'/);
    assert.ok(result.manifest.diagnostics.some(d=>d.code==='SCRIPT_NOT_EXECUTED'));
  }
});

test('Markdown与原始HTML共存；代码块、表格、列表和换行只改变显示副本',()=>{
  const html='# 星港\n\n- **航标**\n- _潮汐_\n\n|名称|位置|\n|-|-|\n|港口|北|\n\n<details><summary>记录</summary>\n\n```python\n<svg onload="never()">原文</svg>\n```\n\n</details>\n\n第一行\n第二行';
  const result=compile({mode:'reply',html}),tree=nodes(result.frame);
  for(const tag of ['h1','ul','strong','em','table','details','pre','code','br'])assert.ok(tree.some(n=>n.tagName===tag),tag);
  assert.equal(tree.filter(n=>n.tagName==='svg').length,0);
  assert.match(result.patch.bodyHtml,/&lt;svg onload="never\(\)"&gt;/);
  assert.equal(result.manifest.sourceHash,compile({mode:'opening',html}).manifest.sourceHash);
});

test('通用作用域保留平台CSS及任意作者类名；没有平台配置不注入专用根',()=>{
  const html='<div class="third-party-wave">正文</div>',css='#installedBuiltInCss .markdown-body .third-party-wave{animation:tide 2s infinite}@keyframes tide{to{opacity:.5}}';
  const plain=compile({mode:'reply',html,css});
  const mapped=compile({mode:'reply',html,css,presentation:{scope:'afengy',imageMappings:[]}});
  assert.doesNotMatch(plain.patch.bodyHtml,/installedBuiltInCss/);
  for(const id of ['installedBuiltInCss','ai-chat-answer','customized-answer-content','customized-answer-content-markdown'])assert.match(mapped.patch.bodyHtml,new RegExp('id="'+id+'"'));
  assert.ok(mapped.patch.styleText.includes(css));assert.match(mapped.patch.bodyHtml,/class="third-party-wave"/);
  assert.equal(mapped.manifest.sourceHash,plain.manifest.sourceHash);assert.notEqual(mapped.manifest.presentationHash,plain.manifest.presentationHash);
  assert.notEqual(mapped.manifest.renderHash,plain.manifest.renderHash);
});

test('精确URL映射经HTML实体、Markdown引用及CSS解码后绑定同一本地图片',()=>{
  const source='https://example.test/art?a=1&b=2',presentation={scope:'standard',imageMappings:[{source,alias:'beacon'}]};
  const html='<img src="https://example.test/art?a=1&amp;b=2">\n\n![航标][pic]\n\n[pic]: <'+source+'>\n\n<img src="https://example.test/art?a=1&amp;b=3" alt="不同查询">';
  const css='.unrelated{background:url("'+source+'")}';
  const missing=compile({mode:'reply',html,css,presentation});assert.deepEqual(missing.manifest.requestedAliases,['beacon']);
  const result=compile({mode:'reply',html,css,presentation,resources:[asset]});
  assert.equal(result.manifest.resources.length,1);assert.equal(nodes(result.frame).filter(n=>n.tagName==='img').length,2);
  assert.match(result.patch.styleText,/background:url\("data:image\/png;base64,/);
  assert.match(result.patch.bodyHtml,/缺少本地资源：不同查询/);
  assert.ok(result.manifest.diagnostics.some(d=>d.code==='RESOURCE_REFERENCE_BLOCKED'));
  assert.equal(result.manifest.sourceHash,missing.manifest.sourceHash);
  assert.notEqual(result.manifest.renderHash,missing.manifest.renderHash);
});

test('静态SVG保留几何、渐变与本地use，移除脚本、foreignObject、SMIL、外链和事件',()=>{
  const html='<svg viewBox="0 0 24 24" onload="evil()"><defs><linearGradient id="tone"><stop offset="0" stop-color="blue"/></linearGradient><path id="icon" d="M0 0L20 20"/></defs><rect fill="url(#tone)" width="20" height="20"/><use href="#icon"/><use href="https://invalid.test/p"/><path stroke="url(https://invalid.test/p)" d="M0 0"/><script>evil()</script><foreignObject><div onload="evil()">hidden</div></foreignObject><animate attributeName="href" values="javascript:evil()"/><image href="https://invalid.test/image"/><g style="fill:url(https://invalid.test/p)"><circle cx="5" cy="5" r="2"/></g></svg>';
  const result=compile({mode:'reply',html}),tree=nodes(result.frame);
  for(const tag of ['svg','defs','linearGradient','stop','path','rect','circle','use'])assert.ok(tree.some(n=>n.tagName===tag),tag);
  assert.match(result.patch.bodyHtml,/viewBox="0 0 24 24"/);assert.match(result.patch.bodyHtml,/fill="url\(#tone\)"/);assert.match(result.patch.bodyHtml,/href="#icon"/);
  for(const tag of ['script','foreignObject','animate','image'])assert.ok(!nodes(result.patch.bodyHtml).some(n=>n.tagName===tag));
  assert.doesNotMatch(result.patch.bodyHtml,/evil\(\)|https:|onload/);
  assert.equal(tree.filter(n=>n.tagName==='script').length,1);
  assert.ok(result.manifest.diagnostics.some(d=>d.code==='SCRIPT_NOT_EXECUTED'));
});

test('CSS内嵌data SVG不绕过静态SVG边界，诊断不冒充外链缺图',()=>{
  const result=compile({mode:'reply',html:'<p>原文</p>',css:'.loading::before{background:url("data:image/svg+xml,%3Csvg%3E%3Canimate/%3E%3C/svg%3E")}'});
  assert.match(result.patch.styleText,/\.loading::before\{background:none\}/);
  assert.deepEqual(result.manifest.diagnostics,[{code:'RESOURCE_INLINE_IMAGE_BLOCKED',detail:'',count:1}]);
  assert.deepEqual(result.manifest.resources,[]);
});
test('HTML解析歧义、样式闭合和转义不能构成第二个脚本',()=>{
  const payloads=['<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=alert(1)>">','<noscript><p title="</noscript><img src=x onerror=alert(1)>">','<template><script>alert(1)</script></template>','<style>p:before{content:"</style><script>alert(1)</script>"}</style>','<svg><foreignObject><style><img src=x onerror=alert(1)></style><script>alert(2)</script></foreignObject></svg>','<svg><desc><math><mtext><img src=x onerror=alert(1)></mtext></math></desc><use xlink:href="javascript:alert(2)"></use></svg>'];
  for(const mode of ['opening','reply'])for(const html of payloads){
    const result=compile({mode,html,css:'x{content:"</style><script>alert(9)</script>"}'}),tree=nodes(result.frame);
    assert.equal(tree.filter(n=>n.tagName==='script').length,1);
    assert.ok(tree.flatMap(n=>n.attrs||[]).every(a=>!a.name.startsWith('on')));
  }
});
test('作品资源按字节hash冻结；HTML和CSS引用复用相同别名，缺失有明确占位',()=>{
  const result=compile({html:'<img src="asset://beacon" alt="灯塔"><img src="asset://missing" alt="缺图">',css:'.scene{background:url("asset://beacon")}',resources:[asset]});
  assert.equal(result.manifest.resources.length,1);assert.equal(result.manifest.resources[0].sha256,asset.sha256);
  assert.match(result.frame,/data:image\/png;base64,/);assert.match(result.frame,/缺少本地资源：缺图/);
  assert.ok(result.manifest.diagnostics.some(d=>d.code==='RESOURCE_MISSING'&&d.detail==='missing'));
  assert.throws(()=>compile({resources:[{...asset,sha256:'0'.repeat(64)}]}),{code:'AUTHORING_PRESENTATION_RESOURCE'});
});
test('样式和原始来源分别核对；模型不能借声明式属性提出开局交接',()=>{
  const html='<button data-rpg-input="指令">采用</button>',opening=compile({html}),reply=compile({mode:'reply',html});
  assert.match(opening.frame,/data-rpg-input="指令"/);assert.doesNotMatch(reply.frame,/data-rpg-input="指令"/);
  assert.notEqual(compile({html,css:'body{color:red}'}).manifest.sourceHash,opening.manifest.sourceHash);
  assert.equal(compile({html}).manifest.sourceHash,opening.manifest.sourceHash);
});
test('宿主引导脚本只接受可信点击、受限文字，并把动作作为绑定父来源的候选',()=>{
  const result=compile({html:'<button data-rpg-input="你好">采用</button>'});
  const source=nodes(result.frame).find(n=>n.tagName==='script').childNodes[0].value,events={},messages=[];
  const target={tagName:'BUTTON',hasAttribute:name=>name==='data-rpg-input',getAttribute:()=> '你好'};
  const document={body:{},documentElement:{scrollHeight:100},addEventListener:(name,handler)=>events[name]=handler,getElementById:()=>null};
  runInNewContext(source,{parent:{postMessage:(message,to)=>messages.push({message,to})},document,window:{addEventListener(){}},performance:{now:()=>0},ResizeObserver:class{observe(){}},requestAnimationFrame:()=>{}});
  events.click({target:{closest:()=>target},isTrusted:false});assert.equal(messages.length,1);
  events.click({target:{closest:()=>target},isTrusted:true,preventDefault(){}});
  assert.deepEqual(JSON.parse(JSON.stringify(messages.at(-1))),{message:{protocol:PRESENTATION.format,token,type:'input',payload:'你好'},to:origin});
});
test('限制源码、深度和快照生命周期；不会静默截断或覆盖活跃快照',()=>{
  assert.throws(()=>compile({html:'x'.repeat(PRESENTATION.textBytes+1)}),{code:'AUTHORING_PRESENTATION_SIZE'});
  assert.throws(()=>presentationAliases('x'.repeat(PRESENTATION.textBytes+1),''),{code:'AUTHORING_PRESENTATION_SIZE'});
  assert.throws(()=>compile({html:'<div>'.repeat(140)+'x'+'</div>'.repeat(140)}),{code:'AUTHORING_PRESENTATION_COMPLEX'});
  let time=0;const cache=createPresentationCache({now:()=>time}),compiled=compile({});
  for(let i=0;i<PRESENTATION.count;i++)cache.put(compiled,String(i));
  assert.throws(()=>cache.put(compiled,'full'),{code:'AUTHORING_PRESENTATION_CAPACITY'});
  assert.equal(cache.get('0').frame,compiled.frame);assert.equal(cache.release('0'),true);assert.equal(cache.release('0'),false);
  cache.put(compiled,'replacement');time=PRESENTATION.ttl;
  assert.throws(()=>cache.get('replacement'),{code:'AUTHORING_PRESENTATION_NOT_FOUND'});cache.put(compiled,'new');
});
const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b4a/tests/',import.meta.url));
await mkdir(parent,{recursive:true});
async function fixture(t){
  const directory=await mkdtemp(join(parent,'http-')),host=await startAuthoringServer({directory});t.after(()=>host.close());
  const api='/rpg-app/api/authoring';
  const post=async(path,body,headers={})=>{const response=await fetch(host.origin+api+path,{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1',...headers},body:JSON.stringify(body)});return {status:response.status,value:await response.json()};};
  const document=blankDocument();document.metadata.name='任意星港';document.sources.system='不进入展示的system';document.sources.startupHtml='<h1>UI 开局</h1>';
  const created=await post('/works',{operationId:'create',expectedRevision:null,document});assert.equal(created.status,200);
  const work=await(await fetch(host.origin+api+'/works/'+created.value.workId)).json();
  return {host,post,document,work};
}
test('预览接口有同源、revision和只读边界；直接导航不可取得嵌入文档',async t=>{
  const {host,post,document,work}=await fixture(t);
  const request={workId:work.id,expectedRevision:work.revision,document,mode:'opening',reply:''};
  assert.equal((await post('/presentations/prepare',request,{Origin:'null'})).status,403);
  assert.equal((await post('/presentations/prepare',{...request,expectedRevision:99})).status,409);
  assert.equal((await post('/presentations/prepare',{...request,unknown:1})).status,422);
  const result=await post('/presentations/prepare',request);assert.equal(result.status,200);
  assert.equal((await fetch(host.origin+result.value.frameUrl)).status,404);
  const frame=await fetch(host.origin+result.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}});
  assert.equal(frame.status,200);assert.doesNotMatch(await frame.text(),/不进入展示的system/);
  assert.ok(frame.headers.get('content-security-policy').includes('frame-ancestors '+host.origin));
  const current=await(await fetch(host.origin+'/rpg-app/api/authoring/works/'+work.id)).json();assert.deepEqual(current,work);
  assert.equal((await post('/presentations/release',{token:result.value.token})).value.released,true);
  assert.equal((await fetch(host.origin+result.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).status,404);
});
test('未保存预览与已保存草稿隔离；后来资源和源码修改不改变旧快照',async t=>{
  const {host,post,document,work}=await fixture(t);
  const upload=await post('/works/'+work.id+'/assets',{operationId:'image',expectedRevision:work.revision,alias:'beacon',fileName:'beacon.png',mediaType:'image/png',base64:png.toString('base64')});assert.equal(upload.status,200);
  const draft=structuredClone(document);draft.sources.startupHtml='<p>尚未保存</p><img src="asset://beacon">';
  const result=await post('/presentations/prepare',{workId:work.id,expectedRevision:upload.value.revision,document:draft,mode:'opening',reply:''});assert.equal(result.status,200);
  const readFrame=()=>fetch(host.origin+result.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}}).then(r=>r.text());
  const frozen=await readFrame();assert.match(frozen,/尚未保存/);assert.equal(result.value.manifest.resources.length,1);
  const current=await(await fetch(host.origin+'/rpg-app/api/authoring/works/'+work.id)).json();assert.equal(current.draft.sources.startupHtml,document.sources.startupHtml);
  const changed=structuredClone(document);changed.sources.startupHtml='<p>后来修订</p>';
  assert.equal((await post('/works/'+work.id+'/save',{operationId:'save',expectedRevision:current.revision,document:changed})).status,200);
  assert.equal(await readFrame(),frozen);
});

test('服务按解析后精确映射加载资源；未用映射不加载，后来编辑不改变旧预览',async t=>{
  const {host,post,document,work}=await fixture(t);
  const upload=await post('/works/'+work.id+'/assets',{operationId:'mapped-image',expectedRevision:work.revision,alias:'beacon',fileName:'beacon.png',mediaType:'image/png',base64:png.toString('base64')});assert.equal(upload.status,200);
  const draft=structuredClone(document);draft.settings.presentation={scope:'afengy',imageMappings:[{source:'https://example.test/p?x=1&y=2',alias:'beacon'},{source:'https://example.test/unused',alias:'not-uploaded'}]};
  const reply='<img src="https://example.test/p?x=1&amp;y=2">\n\n```text\nkeep\n```';
  const result=await post('/presentations/prepare',{workId:work.id,expectedRevision:upload.value.revision,document:draft,mode:'reply',reply});assert.equal(result.status,200);
  assert.deepEqual(result.value.manifest.requestedAliases,['beacon']);assert.equal(result.value.manifest.resources[0].sha256,asset.sha256);
  assert.deepEqual(result.value.manifest.diagnostics,[]);
  const readFrame=()=>fetch(host.origin+result.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}}).then(r=>r.text());
  const frozen=await readFrame();assert.match(frozen,/id="installedBuiltInCss"/);assert.match(frozen,/<pre><code/);assert.match(frozen,/data:image\/png/);
  const current=await(await fetch(host.origin+'/rpg-app/api/authoring/works/'+work.id)).json();
  assert.equal((await post('/works/'+work.id+'/save',{operationId:'later-map',expectedRevision:current.revision,document:draft})).status,200);
  assert.equal(await readFrame(),frozen);
});
