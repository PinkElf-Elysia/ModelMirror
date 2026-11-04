import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {projectDisplayRules,validateDisplayRules,DISPLAY_REGEX_LIMITS as limits} from './display-regex.mjs';
import {blankDocument,validateDocument,capabilityDeclaration} from './contracts.mjs';
import {startAuthoringServer} from './server.mjs';

const rule=(extra={})=>({id:'rule-one',name:'测试规则',enabled:true,pattern:'守灯人',replacement:'引路人',flags:'g',scope:'all',...extra});
const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b5c/tests/',import.meta.url));await mkdir(parent,{recursive:true});
async function fixture(t,options={}){
  const host=await startAuthoringServer({directory:await mkdtemp(join(parent,'http-')),...options});t.after(()=>host.close());
  const request=async(path,body)=>{const response=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});return {status:response.status,value:await response.json()};};
  return {host,request};
}
function document(){const d=blankDocument();d.metadata.name='雾港 · 显示规则';d.metadata.audience='female';d.sources.system='system原样\n\n';d.sources.prefix=' prefix ';d.sources.suffix=' suffix ';d.enhancements.displayRules=[rule()];return d;}
async function work(t){const f=await fixture(t),d=document(),id=(await f.request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;return {...f,d,id};}
const prepare=(f,extra={})=>f.request('/presentations/prepare',{workId:f.id,expectedRevision:0,document:f.d,mode:'reply',reply:'守灯人递来信，守灯人点头。',...extra});

test('关闭、角色过滤和旧缺省范围；不修改调用方对象',async()=>{
  const rules=[rule({enabled:false}),rule({id:'b',scope:'user'}),rule({id:'c',scope:'assistant'})],before=structuredClone(rules);
  const result=await projectDisplayRules('守灯人',rules);assert.equal(result.display,'引路人');assert.deepEqual(result.report.entries.map(e=>e.status),['disabled','scope','applied']);assert.deepEqual(rules,before);
  const legacy=rule();delete legacy.scope;assert.equal((await projectDisplayRules('守灯人',[legacy],{role:'user'})).display,'守灯人');assert.equal((await projectDisplayRules('守灯人',[legacy])).display,'引路人');
});
test('顺序只执行一次；恢复相同输入、规则和角色得到相同结果',async()=>{
  const rules=[rule({pattern:'甲',replacement:'乙'}),rule({id:'b',pattern:'乙',replacement:'丙'}),rule({id:'c',pattern:'丙',replacement:'甲'})];
  const a=await projectDisplayRules('甲',rules),b=await projectDisplayRules('甲',rules);assert.deepEqual(a,b);assert.equal(a.display,'甲');assert.equal(a.report.entries.every(e=>e.matches===1),true);
  assert.equal((await projectDisplayRules('甲',rules.slice(0,2))).display,'丙');assert.equal((await projectDisplayRules('甲',rules.slice(0,2).reverse())).display,'乙');
});
test('替换标记与原生JS字符串语义相同，包括未匹配组与字面函数文本',async()=>{
  const cases=[['(a)(b)?','g',"$$|$&|$1|$2|$12|$01|$09|$99|$0|$`|$'",'a ab'],['(?<word>a)(b)?','g','$<word>/$<absent>/$2','ab a'],['(a)','g','$<word> function(){return 1} $1','a'],['(?<x>a)','g','$<x $<x>','a']];
  for(const [pattern,flags,replacement,source] of cases){const r=await projectDisplayRules(source,[rule({pattern,flags,replacement})]);assert.equal(r.display,source.replace(new RegExp(pattern,flags),replacement));assert.equal(r.report.applied,true);}
});
test('g i m s u 行为、首个匹配和大小写',async()=>{
  for(const [source,pattern,flags] of [['a A','a',''],['a A','a','gi'],['a\na','^a','gm'],['a\nb','a.b','s'],['🧭🧭','.','gu']]){const r=await projectDisplayRules(source,[rule({pattern,flags,replacement:'✓'})]);assert.equal(r.display,source.replace(new RegExp(pattern,flags),'✓'));}
});
test('零长度匹配正确推进，Unicode代理对不拆分，空替换合法',async()=>{
  for(const flags of ['g','gu']){const source='🧭ab';const r=await projectDisplayRules(source,[rule({pattern:'(?:)',flags,replacement:'x'})]);if(flags==='gu')assert.equal(r.display,source.replace(new RegExp('(?:)',flags),'x'));else{assert.equal(r.display,source);assert.equal(r.diagnostics[0].code,'DISPLAY_REGEX_UNICODE');}}
  assert.equal((await projectDisplayRules('守灯人',[rule({replacement:''})])).display,'');assert.equal((await projectDisplayRules('守灯人',[rule({replacement:' '})])).display,' ');
});
test('无命中完整保留；规则哈希反映顺序和设置',async()=>{
  const a=await projectDisplayRules('无人',[rule()]),b=await projectDisplayRules('无人',[rule({flags:'i'})]);assert.equal(a.display,'无人');assert.equal(a.report.entries[0].matches,0);assert.equal(a.report.sourceHash,b.report.sourceHash);assert.notEqual(a.report.rulesHash,b.report.rulesHash);
});
test('后续非法或空模式使整批替换回退；停用非法规则不执行',async()=>{
  for(const [pattern,code] of [['[','DISPLAY_REGEX_SYNTAX'],['','DISPLAY_REGEX_EMPTY']]){
    const result=await projectDisplayRules('守灯人',[rule(),rule({id:'bad',pattern})]);assert.equal(result.display,'守灯人');assert.equal(result.report.applied,false);assert.equal(result.report.entries[0].status,'rolled-back');assert.equal(result.diagnostics[0].code,code);assert.equal(result.diagnostics[0].ruleId,'bad');
  }
  assert.equal((await projectDisplayRules('守灯人',[rule({pattern:'[',enabled:false})])).diagnostics.length,0);
});
test('输出膨胀提前阻断且回退，不截断',async()=>{
  const source='x'.repeat(1000),result=await projectDisplayRules(source,[rule({pattern:'x',replacement:'🧭'.repeat(40000)})]);assert.equal(result.display,source);assert.equal(result.diagnostics[0].code,'DISPLAY_REGEX_CAPACITY');
});
test('过多零长度匹配有明确上限',async()=>{
  const source='x'.repeat(limits.matches),result=await projectDisplayRules(source,[rule({pattern:'(?:)',replacement:''})]);assert.equal(result.display,source);assert.equal(result.diagnostics[0].code,'DISPLAY_REGEX_MATCH_LIMIT');
});
test('灾难性回溯超时后Worker释放，随后普通规则可运行',async()=>{
  const source='a'.repeat(20000)+'!';const start=Date.now(),result=await projectDisplayRules(source,[rule({pattern:'(a+)+$',replacement:''})],{timeoutMs:200});assert.equal(result.display,source);assert.equal(result.diagnostics[0].code,'DISPLAY_REGEX_TIMEOUT');assert.ok(Date.now()-start<3000);assert.equal((await projectDisplayRules('守灯人',[rule()])).display,'引路人');
});
test('配置、容量、未知字段与重复ID拒绝；函数不能作为替换',async()=>{
  for(const rules of [[rule(),rule()], [rule({replacement:()=>''})],[rule({flags:'gg'})],[rule({flags:'y'})],[rule({scope:'system'})],[rule({extra:1})],[rule({pattern:'🧭'.repeat(4001)})]])assert.throws(()=>validateDisplayRules(rules));
  for(const options of [{role:'system'},{timeoutMs:0},{timeoutMs:2001}])await assert.rejects(projectDisplayRules('文本',[rule()],options));
  await assert.rejects(projectDisplayRules('x'.repeat(limits.sourceBytes+1),[]));assert.doesNotThrow(()=>validateDisplayRules([rule({pattern:'🧭'.repeat(4000)})]));
});
test('四份并发额度用尽时不启动第五Worker，超时释放',async()=>{
  const tasks=Array.from({length:4},()=>projectDisplayRules('a'.repeat(20000)+'!',[rule({pattern:'(a+)+$'})],{timeoutMs:200}));
  const fifth=await projectDisplayRules('守灯人',[rule()]);assert.equal(fifth.diagnostics[0].code,'DISPLAY_REGEX_BUSY');await Promise.all(tasks);assert.equal((await projectDisplayRules('守灯人',[rule()])).display,'引路人');
});
test('可选scope保持旧配置往返，错误范围与未知字段拒绝',()=>{
  const d=document();delete d.enhancements.displayRules[0].scope;assert.deepEqual(validateDocument(d),d);assert.ok(capabilityDeclaration(d).required.includes('display-regex'));
  for(const extra of [{scope:'system'},{renderOnly:false}]){const invalid=document();Object.assign(invalid.enhancements.displayRules[0],extra);assert.throws(()=>validateDocument(invalid));}
});
test('预览只接受作品中的规则，原文hash不变、转换后的HTML仍受清理',async t=>{
  const f=await work(t),raw='守灯人';f.d.enhancements.displayRules=[rule({replacement:'<strong>引路人</strong><script>alert(1)</script>'})];
  assert.equal((await prepare(f,{displayRules:[]})).status,422);assert.equal((await prepare(f,{messageRole:'system'})).status,422);
  const on=await prepare(f,{reply:raw});assert.equal(on.status,200);assert.equal(on.value.manifest.displayRules.entries[0].matches,1);
  const frame=await (await fetch(f.host.origin+on.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).text();assert.ok(frame.includes('<strong>引路人</strong>'));assert.equal(frame.includes('alert(1)'),false);
  f.d.enhancements.displayRules=[];const off=await prepare(f,{reply:raw});assert.equal(on.value.manifest.sourceHash,off.value.manifest.sourceHash);assert.notEqual(on.value.manifest.renderHash,off.value.manifest.renderHash);assert.equal(off.value.manifest.displayRules,undefined);
  assert.deepEqual((await f.request('/works/'+f.id)).value.draft,document());assert.equal((await fetch(f.host.origin+'/rpg-app/api/authoring/send')).status,404);
});
test('范围分开渲染；开局不执行显示替换',async t=>{
  const f=await work(t);f.d.enhancements.displayRules=[rule({scope:'user'})];
  const ai=await prepare(f),user=await prepare(f,{messageRole:'user'});assert.equal(ai.value.manifest.displayRules.entries[0].status,'scope');assert.equal(user.value.manifest.displayRules.entries[0].matches,2);assert.equal(ai.value.manifest.sourceHash,user.value.manifest.sourceHash);
  f.d.sources.startupHtml='<p>守灯人</p>';const opening=await prepare(f,{mode:'opening'});assert.equal(opening.value.manifest.displayRules,undefined);assert.ok((await (await fetch(f.host.origin+opening.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).text()).includes('守灯人'));
});
test('建议从原文提取；显示替换不能修改按钮或生成新的建议按钮',async t=>{
  const f=await work(t),suggestions='<suggested_questions>\n1. 询问守灯人\n2. 查看守灯人\n3. 跟随守灯人\n</suggested_questions>';f.d.enhancements.suggestions=true;
  const result=await prepare(f,{reply:'守灯人\n\n'+suggestions});assert.deepEqual(result.value.manifest.suggestions,['询问守灯人','查看守灯人','跟随守灯人']);assert.equal(result.value.manifest.displayRules.entries[0].matches,1);
  f.d.enhancements.displayRules=[rule({replacement:suggestions})];assert.deepEqual((await prepare(f,{reply:'守灯人'})).value.manifest.suggestions,[]);assert.equal((await prepare(f,{reply:suggestions,messageRole:'user'})).value.manifest.suggestions,undefined);
});
test('非法规则诊断可见而原文仍能渲染；服务仍响应后续请求',async t=>{
  const f=await work(t);f.d.enhancements.displayRules=[rule({pattern:'['})];const result=await prepare(f);assert.equal(result.status,200);assert.equal(result.value.manifest.displayRules.applied,false);assert.ok(result.value.manifest.diagnostics.some(d=>d.code==='DISPLAY_REGEX_SYNTAX'));assert.ok((await (await fetch(f.host.origin+result.value.frameUrl,{headers:{'Sec-Fetch-Dest':'iframe'}})).text()).includes('守灯人'));
  f.d.enhancements.displayRules=[rule()];assert.equal((await prepare(f)).value.manifest.displayRules.applied,true);
  for(const path of ['/display-regex-ui.mjs','/display-regex-ui.css'])assert.equal((await fetch(f.host.origin+path)).status,200);
});
test('保存、重复操作、冲突、发布版本、跨目录导入保留规则顺序与所有字段',async t=>{
  const f=await work(t),other=await fixture(t);f.d.enhancements.displayRules.push(rule({id:'second',scope:'user',flags:'im',replacement:'  $&\n'}));
  const body={operationId:'save',expectedRevision:0,document:f.d};assert.equal((await f.request('/works/'+f.id+'/save',body)).status,200);assert.equal((await f.request('/works/'+f.id+'/save',body)).value.replayed,true);assert.equal((await f.request('/works/'+f.id+'/save',{...body,operationId:'conflict'})).status,409);assert.deepEqual((await f.request('/works/'+f.id)).value.draft,f.d);
  const version=(await f.request('/works/'+f.id+'/publish',{operationId:'seal',expectedRevision:1})).value.result.versionId;const pack=(await f.request('/works/'+f.id+'/export?kind=package&version='+version)).value;
  const changed=structuredClone(f.d);changed.enhancements.displayRules.reverse();await f.request('/works/'+f.id+'/save',{operationId:'later',expectedRevision:2,document:changed});assert.deepEqual(pack.configuration.document,f.d);
  const report=(await other.request('/imports/inspect',{payload:pack})).value,imported=(await other.request('/imports',{operationId:'import',expectedRevision:null,workId:null,payload:pack,expectedImportHash:report.payloadHash})).value;assert.deepEqual((await other.request('/works/'+imported.workId)).value.draft,f.d);
});
test('落盘失败不覆盖、未知结果恢复不重复写入',async t=>{
  let failure=null,commits=0;const f=await fixture(t,{fault:phase=>{if(phase==='afterCommit')commits++;if(phase===failure){failure=null;throw Error('test');}}}),d=document(),id=(await f.request('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;d.enhancements.displayRules[0].scope='user';
  failure='beforeCommit';assert.equal((await f.request('/works/'+id+'/save',{operationId:'failed',expectedRevision:0,document:d})).value.error.code,'AUTHORING_WRITE_FAILED');assert.equal((await f.request('/works/'+id)).value.draft.enhancements.displayRules[0].scope,'all');
  failure='afterCommit';assert.equal((await f.request('/works/'+id+'/save',{operationId:'unknown',expectedRevision:0,document:d})).value.error.code,'AUTHORING_RESULT_UNKNOWN');const count=commits;assert.equal((await f.request('/operations/recover',{workId:id,operationId:'unknown'})).status,200);assert.equal(commits,count);assert.deepEqual((await f.request('/works/'+id)).value.draft,d);
});

test('Unicode被非u模式拆开时回退并诊断',async()=>{const source='🧭';const result=await projectDisplayRules(source,[rule({pattern:'\\ud83e',replacement:'x',flags:'g'})]);assert.equal(result.display,source);assert.equal(result.diagnostics[0].code,'DISPLAY_REGEX_UNICODE');});


test('保存往返改变JSON键顺序不改变规则指纹；执行顺序仍参与指纹',async()=>{
  const first=rule(),second=rule({id:'second',pattern:'来信',replacement:'密函'});
  const ordered=[first,second],rekeyed=ordered.map(value=>Object.fromEntries(Object.entries(value).reverse()));
  const a=await projectDisplayRules('守灯人递来信',ordered),b=await projectDisplayRules('守灯人递来信',rekeyed),c=await projectDisplayRules('守灯人递来信',[second,first]);
  assert.equal(a.report.rulesHash,b.report.rulesHash);assert.equal(a.display,b.display);assert.notEqual(a.report.rulesHash,c.report.rulesHash);
});
test('空规则对象返回结构诊断而不是TypeError',()=>{
  const d=document();d.enhancements.displayRules=[null];
  assert.throws(()=>validateDocument(d),{code:'AUTHORING_OBJECT_REQUIRED',path:'/enhancements/displayRules/0'});
  assert.throws(()=>validateDisplayRules([null]),{code:'DISPLAY_REGEX_CONFIG'});
});
