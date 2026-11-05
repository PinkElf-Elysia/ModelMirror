import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {buildCGPlan,replayCGPlan,chooseCGImage,normalizeCGWeights,CG_LIMITS} from './cg.mjs';
import {blankDocument,validateCG,validateDocument,publicationIssues,canonical,sha256} from './contracts.mjs';
import {startAuthoringServer} from './server.mjs';

const parent=fileURLToPath(new URL('../.rpg04-work/authoring-b5d/tests/',import.meta.url));await mkdir(parent,{recursive:true});
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l4sAAAAASUVORK5CYII=','base64');
const asset=(alias='lamp',extra={})=>({alias,fileName:alias+'.png',mediaType:'image/png',size:png.length,sha256:sha256(png),...extra});
const entry=(extra={})=>({id:'cg-one',name:'雾港灯塔',enabled:true,categoryId:null,roles:['assistant'],rule:'or',keywords:['雾港'],target:'chat',mode:'regular',images:[{alias:'lamp',weight:100}],...extra});
const request=(extra={})=>({workId:'work-one',versionId:'version-one',revision:3,operationId:'operation-one',cg:{categories:[],entries:[entry()]},turn:{id:'turn-one',complete:true,user:'询问信使',assistant:'雾港的灯塔亮了'},assets:[asset()],draws:[],capacity:{scanUnits:CG_LIMITS.scanUnits,images:CG_LIMITS.images,bytes:CG_LIMITS.bytes},...extra});
const code=value=>error=>error.code===value;
const failure=(input,value)=>assert.throws(()=>buildCGPlan(input),code(value));
const expected=plan=>({binding:plan.binding,sha256:plan.sha256});
const weights=values=>values.map((weight,i)=>({alias:'image-'+i,weight}));
function document(cg={categories:[],entries:[entry()]}){const d=blankDocument();d.metadata.name='普通 CG 核验';d.metadata.audience='female';d.sources.system='作者system\r\n  原样';d.sources.suffix='后置词\n原样';d.enhancements.cg=cg;return d;}
async function fixture(t,options={}){
  const host=await startAuthoringServer({directory:await mkdtemp(join(parent,'http-')),...options});t.after(()=>host.close());
  const call=async(path,body)=>{const response=await fetch(host.origin+'/rpg-app/api/authoring'+path,body===undefined?{}:{method:'POST',headers:{Origin:host.origin,'Content-Type':'application/json','X-Authoring-Request':'1'},body:JSON.stringify(body)});return {status:response.status,value:await response.json()};};
  return {host,call};
}

test('常规只取首图、不依赖权重、不会改写输入；结果深冻结',()=>{
  const r=request();r.cg.entries[0].images.push({alias:null,weight:99});r.cg.entries[0].images[0].weight=0;const before=canonical(r);
  const p=buildCGPlan(r);assert.equal(p.chat[0].alias,'lamp');assert.equal(p.decisions[0].draw,null);assert.deepEqual(p.background,{action:'keep'});assert.equal(canonical(r),before);assert.equal(p.binding.sourceHash,sha256(canonical(r.turn)));assert.throws(()=>p.chat.push({}),TypeError);assert.throws(()=>{p.decisions[0].hits[0].role='user';},TypeError);
});

test('中文、英文别名、大小写与字面正则符号；原文空格保留',()=>{
  for(const [keyword,text,hit] of [['LightHouse','a LIGHTHOUSE appears',true],['雾港','雾港内',true],['a.b','aXb',false],['a.b','a.b',true],[' 灯 ','灯',false],[' 灯 ',' 灯 ',true],['🧭','🧭向北',true]]){
    const r=request();r.cg.entries[0].keywords=[keyword];r.turn.assistant=text;assert.equal(buildCGPlan(r).chat.length,hit?1:0);
  }
});

test('与条件可跨本轮选择的角色，用户过滤和本轮消息边界明确',()=>{
  const r=request();r.cg.entries[0].roles=['user','assistant'];r.cg.entries[0].rule='and';r.cg.entries[0].keywords=['信使','灯塔'];
  const p=buildCGPlan(r);assert.equal(p.chat.length,1);assert.deepEqual(p.decisions[0].hits,[{keywordIndex:0,role:'user'},{keywordIndex:1,role:'assistant'}]);
  r.cg.entries[0].roles=['assistant'];assert.equal(buildCGPlan(r).chat.length,0);
  r.cg.entries[0].roles=['user'];r.cg.entries[0].keywords=['信使'];assert.equal(buildCGPlan(r).chat.length,1);
  r.cg.entries[0].roles=['user','assistant'];r.cg.entries[0].keywords=['信使雾港'];assert.equal(buildCGPlan(r).chat.length,0);
});

test('停用、非命中不选图、不需要抽样；无历史或摘要入口',()=>{
  const r=request();r.cg.entries=[entry({enabled:false,keywords:[],images:[]}),entry({id:'miss',mode:'weighted',keywords:['未出现'],images:[{alias:'lamp',weight:1}]})];
  assert.deepEqual(buildCGPlan(r).decisions.map(d=>d.status),['disabled','miss']);
  for(const key of ['summary','memory','history','displayText'])failure({...r,[key]:'伪造来源'},'AUTHORING_UNKNOWN_FIELD');
});

test('常规、概率切换保留图片列表；零权重及空占位不参与概率抽取',()=>{
  const r=request();r.cg.entries[0].images=[{alias:'lamp',weight:0},{alias:'second',weight:1},{alias:null,weight:0}];r.assets.push(asset('second'));
  assert.equal(buildCGPlan(r).chat[0].alias,'lamp');r.cg.entries[0].mode='weighted';r.draws=[{id:'cg-one',value:0}];assert.equal(buildCGPlan(r).chat[0].alias,'second');
  r.cg.entries[0].mode='regular';assert.equal(buildCGPlan(r).chat[0].alias,'lamp');assert.equal(r.cg.entries[0].images.length,3);
});

test('精确加权边界左闭右开；零权重不抽中',()=>{
  const values=weights([0,1,1,2,0]);
  for(const [draw,index] of [[0,1],[.249999,1],[.25,2],[.499999,2],[.5,3],[.9999999999999999,3]])assert.equal(chooseCGImage(values,draw),index);
  for(const draw of [-1,1,NaN,Infinity,undefined,'0'])assert.throws(()=>chooseCGImage(values,draw),code('AUTHORING_CG_DRAW_REQUIRED'));
});

test('极大与极小有限权重不会溢出或下溢',()=>{
  for(const values of [[Number.MAX_VALUE,Number.MAX_VALUE],[Number.MIN_VALUE,Number.MIN_VALUE],[1e-200,1e-200]]){assert.equal(chooseCGImage(weights(values),0),0);assert.equal(chooseCGImage(weights(values),.5),1);assert.deepEqual(normalizeCGWeights(weights(values)).map(x=>x.weight),[50,50]);}
  assert.equal(chooseCGImage(weights([Number.MIN_VALUE,Number.MAX_VALUE]),0),0);assert.equal(chooseCGImage(weights([Number.MIN_VALUE,Number.MAX_VALUE]),.1),1);
});

test('CG整数归一化与世界书两位小数规则分开，原数组可用于撤销',()=>{
  for(const [input,output] of [[[1,1,1],[33,33,34]],[[0,1,1,1,0],[0,33,33,34,0]],[[1,2,3],[16,33,51]],[[0,3],[0,100]]]){const rows=weights(input),before=structuredClone(rows),result=normalizeCGWeights(rows);assert.deepEqual(result.map(x=>x.weight),output);assert.equal(result.reduce((s,x)=>s+x.weight,0),100);assert.deepEqual(rows,before);}
});

test('零总权重、负数、非有限值及过多候选拒绝',()=>{
  for(const fn of [normalizeCGWeights,rows=>chooseCGImage(rows,0)]){
    for(const values of [[],[-1],[NaN],[Infinity],['1'],Array(201).fill(1)])assert.throws(()=>fn(weights(values)),code('AUTHORING_CG_WEIGHTS'));
    assert.throws(()=>fn(weights([0,0])),code('AUTHORING_WEIGHT_TOTAL_ZERO'));
  }
});

test('固定随机数必填且不能重复、未知ID或额外字段',()=>{
  const r=request();r.cg.entries[0].mode='weighted';failure(r,'AUTHORING_CG_DRAW_REQUIRED');
  for(const draws of [[{id:'missing',value:.5}],[{id:'cg-one',value:.5},{id:'cg-one',value:.8}],[{id:'cg-one',value:1}],[{id:'cg-one',value:NaN}]])failure({...r,draws},'AUTHORING_CG_DRAW_REQUIRED');
  failure({...r,draws:[{id:'cg-one',value:.5,other:true}]},'AUTHORING_UNKNOWN_FIELD');
  assert.equal(buildCGPlan({...r,draws:[{id:'cg-one',value:.75}]}).decisions[0].draw,.75);
});

test('聊天列表稳定；背景最后命中胜出并记录覆盖，无命中保持既有背景',()=>{
  const r=request();r.cg.entries=[entry({id:'bg1',target:'background'}),entry({id:'chat1'}),entry({id:'bg2',target:'background'}),entry({id:'chat2'})];
  const p=buildCGPlan(r);assert.deepEqual(p.chat.map(x=>x.entryId),['chat1','chat2']);assert.equal(p.background.image.entryId,'bg2');assert.equal(p.decisions[0].status,'superseded');assert.equal(p.decisions[0].supersededBy,'bg2');assert.equal(p.usage.images,3);assert.equal(p.usage.bytes,png.length);
  r.turn.assistant='无匹配';assert.deepEqual(buildCGPlan(r).background,{action:'keep'});
});

test('未完成或损坏原始回合及超限来源拒绝',()=>{
  const r=request();for(const value of [false,undefined,1,'true'])failure({...r,turn:{...r.turn,complete:value}},'AUTHORING_CG_COMPLETE_TURN_REQUIRED');
  failure({...r,turn:{...r.turn,assistant:'\ud800'}},'AUTHORING_CG_SOURCE');
  failure({...r,turn:{...r.turn,assistant:'a'.repeat(CG_LIMITS.sourceBytes+1)}},'AUTHORING_CG_SOURCE_SIZE');
  failure({...r,turn:{...r.turn,summary:'不能替代'}},'AUTHORING_UNKNOWN_FIELD');
});

test('资源必须存在且为图片；失败不返回部分选择',()=>{
  for(const alias of [null,'missing']){const r=request();r.cg.entries.push(entry({id:'invalid',images:[{alias,weight:1}]}));failure(r,'AUTHORING_ASSET_MISSING');}
  failure(request({assets:[asset('lamp',{mediaType:'audio/mpeg',fileName:'x.mp3'})]}),'AUTHORING_ASSET_ROLE');
  const r=request();r.cg.entries[0].keywords=['不会命中'];r.cg.entries[0].images=[];failure(r,'AUTHORING_CG_IMAGE_REQUIRED');
});

test('扫描和完整图片容量原子阻断，不自动截断',()=>{
  const r=request();failure({...r,capacity:{...r.capacity,scanUnits:0}},'AUTHORING_CG_SCAN_CAPACITY');failure({...r,capacity:{...r.capacity,images:0}},'AUTHORING_CG_OUTPUT_CAPACITY');failure({...r,capacity:{...r.capacity,bytes:png.length-1}},'AUTHORING_CG_OUTPUT_CAPACITY');
  for(const capacity of [{...r.capacity,bytes:Infinity},{...r.capacity,images:33},{...r.capacity,scanUnits:-1}])failure({...r,capacity},'AUTHORING_CG_CAPACITY_CONFIG');
  assert.equal(buildCGPlan({...r,capacity:{...r.capacity,images:1,bytes:png.length}}).chat.length,1);
});

test('只按最终背景计展示容量；同图不同插图计数量、字节去重',()=>{
  const r=request();r.cg.entries=[entry({target:'background'}),entry({id:'second',target:'background'})];r.capacity.images=1;assert.equal(buildCGPlan(r).usage.images,1);
  r.cg.entries.forEach(x=>x.target='chat');failure(r,'AUTHORING_CG_OUTPUT_CAPACITY');r.capacity.images=2;r.capacity.bytes=png.length;assert.equal(buildCGPlan(r).usage.bytes,png.length);
});

test('最终32幅边界与33幅拒绝；非命中规则不消耗展示额度',()=>{
  const r=request();r.cg.entries=Array.from({length:32},(_,i)=>entry({id:'item-'+i}));assert.equal(buildCGPlan(r).usage.images,32);
  r.cg.entries.push(entry({id:'overflow'}));failure(r,'AUTHORING_CG_OUTPUT_CAPACITY');
  r.cg.entries.at(-1).keywords=['不会命中'];assert.equal(buildCGPlan(r).usage.images,32);
});

test('原始HTML中的文字仍可匹配，不解释标签或做语义替换',()=>{
  const r=request();r.turn.assistant='<details><summary>状态</summary><p data-place="雾港">灯塔</p></details>';assert.equal(buildCGPlan(r).chat.length,1);
  r.turn.assistant='显示副本已经被正则替换成别处';assert.equal(buildCGPlan(r).chat.length,0);
});

test('只信任完整且固定的作品、版本、回合和资源绑定',()=>{
  const r=request(),base=buildCGPlan(r);
  for(const change of [{revision:4},{operationId:'next'},{workId:'other'},{versionId:'other'},{turn:{...r.turn,id:'other'}},{turn:{...r.turn,assistant:'雾港的新原文'}},{assets:[asset('lamp',{sha256:'a'.repeat(64)})]}])assert.notEqual(buildCGPlan({...r,...change}).sha256,base.sha256);
  const cg=structuredClone(r.cg);cg.entries[0].keywords.push('第二关键词');assert.notEqual(buildCGPlan({...r,cg}).binding.rulesHash,base.binding.rulesHash);
  for(const change of [{operationId:''},{revision:-1},{revision:.5},{versionId:null}])failure({...r,...change},'AUTHORING_CG_BINDING');
});

test('重排JSON键不改变计划；资源清单顺序不改变绑定',()=>{
  const r=request();r.assets.push(asset('unused'));const p=buildCGPlan(r),reordered=JSON.parse(canonical(r));reordered.assets.reverse();assert.deepEqual(buildCGPlan(reordered),p);
});

test('序列化后重放不访问新配置或随机源，旧分支快照隔离未来修订',async()=>{
  const r=request();r.cg.entries[0].mode='weighted';r.cg.entries[0].images.push({alias:'second',weight:100});r.assets.push(asset('second'));r.draws=[{id:'cg-one',value:.75}];
  const p=buildCGPlan(r),file=join(await mkdtemp(join(parent,'frozen-')),'plan.json');await writeFile(file,JSON.stringify(p));
  r.cg.entries[0].images.reverse();r.draws[0].value=0;r.revision=4;r.versionId='future';const later=buildCGPlan(r);assert.notEqual(later.sha256,p.sha256);
  const old=JSON.parse(await readFile(file,'utf8'));assert.deepEqual(replayCGPlan(old,expected(p)),p);assert.equal(p.chat[0].alias,'second');assert.equal(Object.isFrozen(replayCGPlan(old,expected(p)).background),true);
});

test('计划篡改、错误操作/版本/hash与未知字段均拒绝重放',()=>{
  const p=buildCGPlan(request());for(const edit of [q=>q.chat[0].alias='wrong',q=>q.binding.revision++,q=>q.decisions[0].hits=[],q=>q.sha256='a'.repeat(64)]){const modified=structuredClone(p);edit(modified);assert.throws(()=>replayCGPlan(modified,expected(p)),code('AUTHORING_CG_PLAN_MISMATCH'));}
  assert.throws(()=>replayCGPlan(p,{...expected(p),binding:{...p.binding,operationId:'other'}}),code('AUTHORING_CG_PLAN_MISMATCH'));
  assert.throws(()=>replayCGPlan({...p,extra:1},expected(p)),code('AUTHORING_UNKNOWN_FIELD'));
});

test('没有原文、system、前后置词、替换显示副本进入结果',()=>{
  const r=request(),p=buildCGPlan(r),json=JSON.stringify(p);assert.equal(json.includes(r.turn.assistant),false);assert.equal(json.includes(r.turn.user),false);assert.equal(p.binding.sourceHash,sha256(canonical(r.turn)));
  const d=document(r.cg),before=canonical(d);buildCGPlan(r);assert.equal(canonical(d),before);
});

test('旧CG配置原样往返；defaults可选，null占位可存草稿且阻止发布',()=>{
  const d=document(),old=structuredClone(d);assert.deepEqual(validateDocument(d),old);assert.equal('defaults'in d.enhancements.cg,false);
  d.enhancements.cg.defaults={roles:['assistant'],rule:'and',target:'background'};d.enhancements.cg.entries[0].images[0].alias=null;assert.deepEqual(validateDocument(d),d);assert.ok(publicationIssues(d,[]).some(i=>i.code==='AUTHORING_ASSET_MISSING'));
  d.enhancements.cg.entries[0].enabled=false;assert.equal(publicationIssues(d,[]).length,0);
});

test('只有生效图片影响发布；大数权重合法，非法字段仍拒绝',()=>{
  const d=document();d.enhancements.cg.entries[0].images=[{alias:'lamp',weight:0},{alias:null,weight:Number.MAX_VALUE}];assert.doesNotThrow(()=>validateDocument(d));assert.equal(publicationIssues(d,[asset()]).length,0);
  d.enhancements.cg.entries[0].mode='weighted';assert.ok(publicationIssues(d,[asset()]).some(i=>i.code==='AUTHORING_ASSET_MISSING'));
  for(const edit of [x=>x.defaults={roles:['assistant'],rule:'or',target:'wrong'},x=>x.extra=1,x=>x.entries[0].special=true,x=>x.entries[0].roles=[],x=>x.entries.push(structuredClone(x.entries[0])),x=>x.entries[0].images[0].weight=-1]){const cg=structuredClone(document().enhancements.cg);edit(cg);assert.throws(()=>validateCG(cg));}
});

test('空词、纯空白词和零总权重不能发布；停用保留',()=>{
  for(const keywords of [[],[''],[' \n ']]){const d=document();d.enhancements.cg.entries[0].keywords=keywords;assert.ok(publicationIssues(d,[asset()]).some(i=>i.code==='AUTHORING_CG_KEYWORDS'));failure(request({cg:d.enhancements.cg}),'AUTHORING_CG_KEYWORDS');}
  const d=document();d.enhancements.cg.entries[0].mode='weighted';d.enhancements.cg.entries[0].images[0].weight=0;assert.ok(publicationIssues(d,[asset()]).some(i=>i.code==='AUTHORING_WEIGHT_TOTAL_ZERO'));
});

test('保存重复操作、冲突、不可变发布和跨目录导入保留CG字段和图片',async t=>{
  const f=await fixture(t),g=await fixture(t),d=document();d.enhancements.cg.defaults={roles:['assistant'],rule:'and',target:'background'};d.enhancements.cg.categories=[{id:'places',name:'地点'}];d.enhancements.cg.entries[0].categoryId='places';
  const id=(await f.call('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  assert.equal((await f.call('/works/'+id+'/assets',{operationId:'asset',expectedRevision:0,alias:'lamp',fileName:'灯塔.png',mediaType:'image/png',base64:png.toString('base64')})).status,200);
  d.enhancements.cg.entries[0].special={enabled:true,showNameWhenLocked:false,hintEnabled:true,hint:'雾散时登上灯塔。'};
  d.enhancements.cg.entries[0].mode='weighted';d.enhancements.cg.entries[0].images[0].weight=1e300;
  const save={operationId:'save',expectedRevision:1,document:d};assert.equal((await f.call('/works/'+id+'/save',save)).status,200);assert.equal((await f.call('/works/'+id+'/save',save)).value.replayed,true);assert.equal((await f.call('/works/'+id+'/save',{...save,operationId:'conflict'})).status,409);
  const version=(await f.call('/works/'+id+'/publish',{operationId:'publish',expectedRevision:2})).value.result.versionId;
  const changed=structuredClone(d);changed.enhancements.cg.entries[0].enabled=false;await f.call('/works/'+id+'/save',{operationId:'later',expectedRevision:3,document:changed});
  const pack=(await f.call('/works/'+id+'/export?kind=package&version='+version)).value;assert.deepEqual(pack.configuration.document,d);
  const inspected=(await g.call('/imports/inspect',{payload:pack})).value;const imported=(await g.call('/imports',{operationId:'import',expectedRevision:null,workId:null,payload:pack,expectedImportHash:inspected.payloadHash})).value;
  const target=(await g.call('/works/'+imported.workId)).value;assert.deepEqual(target.draft,d);assert.equal(target.assets[0].sha256,sha256(png));assert.equal((await g.call('/works/'+imported.workId+'/validation')).value.issues.length,0);
});

test('落盘失败与未知结果只核对原操作，CG配置不重复写入',async t=>{
  let phase=null,commits=0;const f=await fixture(t,{fault:p=>{if(p==='afterCommit')commits++;if(p===phase){phase=null;throw Error('test failure');}}}),d=document({categories:[],entries:[]});const id=(await f.call('/works',{operationId:'create',expectedRevision:null,document:d})).value.workId;
  d.enhancements.cg.entries=[entry({images:[{alias:null,weight:100}]})];phase='beforeCommit';assert.equal((await f.call('/works/'+id+'/save',{operationId:'failed',expectedRevision:0,document:d})).value.error.code,'AUTHORING_WRITE_FAILED');assert.equal((await f.call('/works/'+id)).value.draft.enhancements.cg.entries.length,0);
  phase='afterCommit';assert.equal((await f.call('/works/'+id+'/save',{operationId:'unknown',expectedRevision:0,document:d})).value.error.code,'AUTHORING_RESULT_UNKNOWN');const count=commits;assert.equal((await f.call('/operations/recover',{workId:id,operationId:'unknown'})).value.status,'committed');assert.equal(commits,count);assert.deepEqual((await f.call('/works/'+id)).value.draft,d);
});
