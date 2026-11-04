import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {openingOptions,withOpeningOptions,moveOpening,newPreset,newShortcut,presetIssues,shortcutIssues,dialoguePreview} from './dialogue.mjs';
import {blankDocument,validateDocument,publicationIssues,capabilityDeclaration} from './contracts.mjs';
import {startAuthoringServer} from './server.mjs';

const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b5a/tests/',import.meta.url));
await mkdir(parent,{recursive:true});
function document(){const d=blankDocument();d.metadata.name='雾港增强';d.metadata.audience='female';d.sources.system='系统原文\n\n';d.sources.prefix='前置\n';d.sources.suffix='后置';d.enhancements.dialogue={mode:'presets',opening:'',openings:['  开局\n🧭  ','另一个选择'],presets:[{id:'a',title:'守灯人',messages:[{user:'我抵达。\n',assistant:'  灯亮了。\n\n'},{user:'检查门锁',assistant:'锁完好。'}],openings:['检查信封','']}]};d.enhancements.shortcuts=[{id:'q',name:'环境',text:'请描述眼前的环境。'}];return d;}
async function fixture(t,options={}){
  const host=await startAuthoringServer({directory:await mkdtemp(join(parent,'http-')),...options});t.after(()=>host.close());
  const request=async(path,body)=>{const r=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});return {status:r.status,value:await r.json()};};
  return {host,request};
}
test('旧开场白原样读取；显式列表转换保留空白、HTML与Unicode，不共享引用',()=>{
  const d=blankDocument();d.enhancements.dialogue.opening='  一\r\n\n<details>🧭</details>';
  const before=structuredClone(d),options=openingOptions(d.enhancements.dialogue);
  assert.equal(options[0],d.enhancements.dialogue.opening);assert.equal(Object.hasOwn(d.enhancements.dialogue,'openings'),false);
  const converted=withOpeningOptions(d.enhancements.dialogue,options);assert.equal(converted.opening,'');options[0]='mutated';
  assert.equal(converted.openings[0],before.enhancements.dialogue.opening);assert.deepEqual(d,before);validateDocument(d);validateDocument({...d,enhancements:{...d.enhancements,dialogue:converted}});
});
test('两份开场来源冲突、41项、超长和未知字段整体验证拒绝',()=>{
  for(const change of [d=>d.enhancements.dialogue.opening='不能隐藏旧内容',d=>d.enhancements.dialogue.openings=Array(41).fill('x'),d=>d.enhancements.dialogue.openings=['字'.repeat(200001)],d=>d.enhancements.dialogue.other='unknown']){const d=document();change(d);assert.throws(()=>validateDocument(d));}
  const d=document();d.enhancements.dialogue.openings=Array(40).fill('🧭'.repeat(20));assert.doesNotThrow(()=>validateDocument(d));
});
test('缺失或错误类型的对话块返回合同诊断，不能成为宿主异常',async t=>{
  const {request}=await fixture(t);
  for(const dialogue of [null,[],false,'text']){const d=document();d.enhancements.dialogue=dialogue;const result=await request('/works',{operationId:'invalid-'+String(dialogue),expectedRevision:null,document:d});assert.equal(result.status,422);assert.equal(result.value.error.code,'AUTHORING_OBJECT_REQUIRED');}
});
test('排序与无效位置不改变来源，重复正文仍作为独立选项',()=>{const options=['甲','乙','甲'];assert.deepEqual(moveOpening(options,0,2),['乙','甲','甲']);assert.deepEqual(moveOpening(options,-1,1),options);assert.deepEqual(moveOpening(options,0,9),options);assert.deepEqual(options,['甲','乙','甲']);});
test('快捷指令的新UI容量与旧存储容量区分；旧超长原文可保存导出但不能冒充有效',()=>{
  const d=document();d.enhancements.shortcuts[0]={id:'old',name:'长'.repeat(80),text:'原文'.repeat(10000)};validateDocument(d);
  assert.equal(shortcutIssues(d.enhancements.shortcuts[0]).length,2);assert.equal(dialoguePreview(d.enhancements).shortcuts.length,0);
  assert.ok(publicationIssues(d,[]).some(i=>i.code==='AUTHORING_SHORTCUT_INCOMPLETE'));
  assert.equal(shortcutIssues({name:'字'.repeat(15),text:'🧭'.repeat(100)}).length,0);
  assert.equal(shortcutIssues(newShortcut('new')).length,2);
});
test('空草稿可恢复，未完成预设不能成为可发送上下文；模式不删除内容',()=>{
  const d=document();d.enhancements.dialogue.presets=[newPreset('empty')];validateDocument(d);
  assert.ok(presetIssues(d.enhancements.dialogue.presets[0]).length);assert.deepEqual(dialoguePreview(d.enhancements,'empty').messages,[]);
  assert.ok(publicationIssues(d,[]).some(i=>i.code==='AUTHORING_DIALOGUE_INCOMPLETE'));
  d.enhancements.dialogue.mode='opening';assert.deepEqual(dialoguePreview(d.enhancements).choices,d.enhancements.dialogue.openings);
  assert.equal(d.enhancements.dialogue.presets.length,1);assert.equal(publicationIssues(d,[]).length,0);
});
test('预设只在明确选择后按user/assistant成对提供；填入选择与命令不带前后置词',()=>{
  const d=document(),original=structuredClone(d);assert.deepEqual(dialoguePreview(d.enhancements).messages,[]);
  const result=dialoguePreview(d.enhancements,'a');assert.deepEqual(result.messages,[{role:'user',content:'我抵达。\n'},{role:'assistant',content:'  灯亮了。\n\n'},{role:'user',content:'检查门锁'},{role:'assistant',content:'锁完好。'}]);
  assert.deepEqual(result.choices,['检查信封']);assert.equal(result.shortcuts[0].text,'请描述眼前的环境。');result.messages[0].content='changed';result.shortcuts[0].text='changed';assert.deepEqual(d,original);
  assert.deepEqual(dialoguePreview(d.enhancements,'removed-id').messages,[]);
});
test('新增列表参与能力声明；未使用的新字段不改变旧声明',()=>{const d=blankDocument(),before=capabilityDeclaration(d);d.enhancements.dialogue=withOpeningOptions(d.enhancements.dialogue,[]);assert.deepEqual(capabilityDeclaration(d),before);d.enhancements.dialogue.openings=['开场'];assert.ok(capabilityDeclaration(d).required.includes('dialogue-startup'));});
test('保存刷新、重复操作及多标签冲突保留完整增强字段和其他来源',async t=>{
  const {request}=await fixture(t),d=document(),id=(await request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  d.enhancements.dialogue.mode='opening';d.enhancements.dialogue.openings.reverse();
  const body={operationId:'save',expectedRevision:0,document:d};assert.equal((await request('/works/'+id+'/save',body)).status,200);assert.equal((await request('/works/'+id+'/save',body)).value.replayed,true);
  const conflict=await request('/works/'+id+'/save',{...body,operationId:'other'});assert.equal(conflict.status,409);assert.deepEqual((await request('/works/'+id)).value.draft,d);
  const bad=structuredClone(d);bad.enhancements.shortcuts.push({...bad.enhancements.shortcuts[0]});assert.equal((await request('/works/'+id+'/save',{operationId:'bad',expectedRevision:1,document:bad})).status,422);assert.deepEqual((await request('/works/'+id)).value.draft,d);
});
test('封存版本与新目录完整包导入保持增强内容；后续草稿不影响旧版',async t=>{
  const a=await fixture(t),b=await fixture(t),d=document(),id=(await a.request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  const version=(await a.request('/works/'+id+'/publish',{operationId:'seal',expectedRevision:0})).value.result.versionId;
  const next=structuredClone(d);next.enhancements.dialogue.openings=['后来'];await a.request('/works/'+id+'/save',{operationId:'edit',expectedRevision:1,document:next});
  const pack=(await a.request('/works/'+id+'/export?kind=package&version='+version)).value;assert.deepEqual(pack.configuration.document,d);
  const report=(await b.request('/imports/inspect',{payload:pack})).value;
  const imported=(await b.request('/imports',{operationId:'import',expectedRevision:null,workId:null,payload:pack,expectedImportHash:report.payloadHash})).value;
  assert.deepEqual((await b.request('/works/'+imported.workId)).value.draft,d);
});
test('未知结果恢复原操作，不重写增强配置',async t=>{
  let unknown=false,commits=0;const {request}=await fixture(t,{fault:phase=>{if(phase==='afterCommit'){commits++;if(unknown){unknown=false;throw Error('test');}}}});
  const d=document(),id=(await request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  d.enhancements.shortcuts[0].text='本次已落盘';unknown=true;
  assert.equal((await request('/works/'+id+'/save',{operationId:'unknown',expectedRevision:0,document:d})).value.error.code,'AUTHORING_RESULT_UNKNOWN');
  const before=commits;assert.equal((await request('/operations/recover',{operationId:'unknown',workId:id})).status,200);assert.equal(commits,before);assert.deepEqual((await request('/works/'+id)).value.draft,d);
});
test('提交前失败保留旧内容；原操作明确失败后的新保存才生效',async t=>{
  let broken=false;const {request}=await fixture(t,{fault:phase=>{if(broken&&phase==='beforeCommit')throw Error('test');}}),d=document();
  const id=(await request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId,next=structuredClone(d);next.enhancements.dialogue.openings=[];broken=true;
  assert.equal((await request('/works/'+id+'/save',{operationId:'failed',expectedRevision:0,document:next})).value.error.code,'AUTHORING_WRITE_FAILED');assert.deepEqual((await request('/works/'+id)).value.draft,d);
  broken=false;assert.equal((await request('/works/'+id+'/save',{operationId:'retry-explicit',expectedRevision:0,document:next})).status,200);assert.deepEqual((await request('/works/'+id)).value.draft,next);
});
test('候选只开放显式前端资源，不新增发送模型路由',async t=>{const {host}=await fixture(t);for(const path of ['/dialogue.mjs','/dialogue-ui.mjs','/dialogue-ui.css'])assert.equal((await fetch(host.origin+path)).status,200);assert.equal((await fetch(host.origin+'/contracts.mjs')).status,404);assert.equal((await fetch(host.origin+'/rpg-app/api/authoring/send')).status,404);});
