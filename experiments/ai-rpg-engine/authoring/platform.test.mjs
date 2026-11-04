import test from 'node:test';
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {installDisplayPlatform} from './platform-browser.mjs';
import {validatePlatform} from './platform.mjs';
import {compilePresentation,createPresentationCompiler} from './presentation.mjs';
import {blankDocument,validateDocument,publicationIssues,sha256} from './contracts.mjs';
const batch=effects=>({clock:0,effects,layoutRequests:[],scheduled:false});
const compile=input=>compilePresentation({mode:'opening',html:'<p>原文</p>',origin:'http://127.0.0.1:1234',token:'b'.repeat(48),execution:{sourceHash:'c'.repeat(64),stateHash:'d'.repeat(64),sequence:0},...input});

test('平台命令只允许有限的画布、音效和局部布局操作',()=>{
  for(const command of [
    ['canvas','n1','drawImage','https://example.test'],['canvas','n1','constructor',1],['canvas','n1','set','__proto__',{}],
    ['canvas','n1','arc',0,0,Infinity,0,6],['audio',1,'node',2,'mediaStream'],['audio',1,'param',2,'gain','constructor',1],
    ['audio',1,'param',2,'gain','value',10],['audio',1,'connect',1.5,0],['audio',1,'start',2,-1],['audio',1,'param',2,'frequency','value',30000],
    ['media',1,'play','x',false,2,'media1'],['scroll','parent','top',1],['scrollIntoView','n1',{behavior:'eval',block:'start'}],
  ])assert.throws(()=>validatePlatform(batch([command])),{code:'AUTHORING_PLATFORM_LIMIT'});
  assert.throws(()=>validatePlatform(batch(new Array(12001).fill(['focus','n1']))),{code:'AUTHORING_PLATFORM_LIMIT'});
  assert.deepEqual(validatePlatform(batch([['canvas','n1','fillRect',0,0,10,10]])),batch([['canvas','n1','fillRect',0,0,10,10]]));
});

test('音频精确映射绑定本地字节，未映射来源不产生外网或自动下载',()=>{
  const source='https://example.test/music.mp3',bytes=Buffer.from('ID3 local fixture'),resource={alias:'theme',mediaType:'audio/mpeg',bytes,sha256:sha256(bytes)};
  const platform=batch([['media',1,'play',source,true,.25,'media2']]);
  const missing=compile({platform});assert.equal(missing.patch.platform.effects[0][3],'');assert.equal(missing.manifest.diagnostics[0].code,'AUDIO_RESOURCE_MISSING');
  const result=compile({platform,presentation:{scope:'standard',imageMappings:[],mediaMappings:[{source,alias:'theme'}]},resources:[resource]});
  assert.match(result.patch.platform.effects[0][3],/^data:audio\/mpeg;base64,/);assert.equal(platform.effects[0][3],source);
  assert.deepEqual(result.manifest.resources,[{alias:'theme',mediaType:'audio/mpeg',sha256:resource.sha256}]);assert.match(result.csp,/media-src data:/);assert.match(result.csp,/connect-src 'none'/);
  assert.throws(()=>compile({platform,resources:[{...resource,bytes:Buffer.from('changed')}]}),{code:'AUTHORING_PRESENTATION_RESOURCE'});
  assert.throws(()=>compile({mode:'reply',execution:null,platform}),{code:'AUTHORING_PRESENTATION_BINDING'});
});

test('音频映射是可选的版本字段，校验来源、重复和资源角色',()=>{
  const document=blankDocument();document.settings.presentation={scope:'standard',imageMappings:[],mediaMappings:[{source:'https://example.test/music.mp3',alias:'theme'}]};
  assert.deepEqual(validateDocument(document),document);
  assert.ok(publicationIssues(document,[]).some(i=>i.path.includes('mediaMappings')&&i.code==='AUTHORING_ASSET_MISSING'));
  assert.ok(publicationIssues(document,[{alias:'theme',mediaType:'image/png'}]).some(i=>i.path.includes('mediaMappings')&&i.code==='AUTHORING_ASSET_ROLE'));
  for(const source of ['file:///a','https://user:secret@example.test/x','https://example.test/x#part']){
    const changed=structuredClone(document);changed.settings.presentation.mediaMappings[0].source=source;assert.throws(()=>validateDocument(changed),{code:'AUTHORING_AUDIO_SOURCE'});
  }
});

test('短音效结束后保留可复用的主增益，连续操作可再次连接并停止全部音频',()=>{
  const created=[],errors=[];
  class Node {constructor(type){this.type=type;this.gain={value:1};this.frequency={value:440};this.connections=[];created.push(this);}connect(other){this.connections.push(other);}disconnect(){this.connections=[];}start(){}stop(){this.onended?.();}}
  class AudioContext {constructor(){this.state='running';this.currentTime=0;this.destination=new Node('destination');}createGain(){return new Node('gain');}createOscillator(){return new Node('oscillator');}close(){this.state='closed';return Promise.resolve();}}
  const document={documentElement:{dataset:{}},addEventListener(){},querySelector(){return null;}};
  const adapter=runInNewContext('('+installDisplayPlatform.toString()+')(()=>{},error=>errors.push(error))',{window:{AudioContext,addEventListener(){}},document,errors,console});
  const shared=[['audio',1,'context'],['audio',1,'node',2,'gain'],['audio',1,'connect',2,0]];
  const tone=(osc,gain)=>[['audio',1,'node',osc,'oscillator'],['audio',1,'node',gain,'gain'],['audio',1,'connect',osc,gain],['audio',1,'connect',gain,2],['audio',1,'start',osc,0],['audio',1,'stop',osc,.1]];
  adapter.apply(batch([...shared,...tone(3,4)]));adapter.apply(batch(tone(5,6)));adapter.apply(batch(tone(7,8)));
  assert.deepEqual(errors,[]);assert.equal(created.filter(n=>n.type==='oscillator').length,3);adapter.stop();
});

test('重排的中间样式也经资源清理，SVG保持同一节点身份',()=>{
  const result=compile({html:'<svg data-author-node="n1"><circle data-author-node="n2" cx="2"/></svg>',platform:batch([['reflow','n1','visible','background:url(https://example.test/track)']])});
  assert.match(result.patch.bodyHtml,/<svg data-author-node="n1">/);assert.match(result.patch.bodyHtml,/circle data-author-node="n2"/);
  assert.equal(result.patch.platform.effects[0][3],'background:none');
});

test('动画替换后只沿点击时保存的祖先链回退，已消失的表单操作不跳向其他控件',()=>{
  const alive=new Set(['n2','n3']);
  const document={addEventListener(){},querySelector(selector){return alive.has(/"(n\d+)"/.exec(selector)?.[1])?{}:null;}};
  const adapter=runInNewContext('('+installDisplayPlatform.toString()+')(()=>{},()=>{})',{window:{addEventListener(){}},document});
  assert.equal(adapter.eventTarget('n3',['n2']),'n3');alive.delete('n3');
  assert.equal(adapter.eventTarget('n3',['n2']),'n2');
  assert.equal(adapter.eventTarget('n3',[]),null);
  assert.equal(adapter.eventTarget('n7',['n8']),null);
});

test('复用的有界解析器连续清理完整回放并支持关闭与超时收束',async()=>{
  const compiler=createPresentationCompiler();
  try{
    for(let i=0;i<3;i++){const result=await compiler.compile({mode:'reply',html:'<p>原文'+i+'</p><script>throw 1</script>',origin:'http://127.0.0.1:1234',token:'a'.repeat(48)});assert.match(result.patch.bodyHtml,new RegExp('原文'+i));assert.doesNotMatch(result.patch.bodyHtml,/script/);}
  }finally{compiler.close();}
  await assert.rejects(compiler.compile({mode:'reply',html:'x'}),{code:'AUTHORING_PRESENTATION_CLOSED'});
  const slow=createPresentationCompiler();
  await assert.rejects(slow.compile({mode:'reply',html:'x'}, {timeoutMs:1}),{code:'AUTHORING_PRESENTATION_TIMEOUT'});
  await assert.rejects(slow.compile({mode:'reply',html:'x'}),{code:'AUTHORING_PRESENTATION_CLOSED'});
});
