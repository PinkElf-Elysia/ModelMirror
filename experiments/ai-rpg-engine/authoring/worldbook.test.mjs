import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir, mkdtemp, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {blankDocument, validateDocument, publicationIssues, canonical, sha256} from './contracts.mjs';
import {AuthoringStore} from './store.mjs';
import {buildWorldbookPlan, replayWorldbookPlan, normalizeWorldbookWeights, chooseWorldbookContent} from './worldbook.mjs';
import {evaluateRegexBatch} from './worldbook-regex.mjs';

const evidence = fileURLToPath(new URL('../.rpg04-work/authoring-b3a/', import.meta.url));
await mkdir(evidence, {recursive:true});
const runRoot = await mkdtemp(join(evidence, 'tests-'));
const entry = (id, changes = {}) => ({id, name:id, enabled:true, categoryId:null, roles:['user'], rule:'or', regex:false, keywords:['灯塔'], targets:['system'], order:0, depth:0, chance:100, mode:'regular', body:id, contents:[], ...changes});
const request = (entries = [], changes = {}) => {
  const document = blankDocument(); document.worldbook.entries = entries;
  return {operationId:'probe', revision:1, document, currentInput:'灯塔', turns:[], draws:[], capacity:{scanChars:10000,injectedChars:10000,systemChars:10000,userChars:10000}, ...changes};
};
const weights = (...values) => values.map((weight,i)=>({text:'正文'+i,weight}));
const errorCode = code => error => error?.code === code;
const rejected = (action,code) => assert.rejects(action,errorCode(code));
const history = (...pairs) => pairs.map(([user,assistant],i)=>({id:'turn-'+i,complete:true,user,assistant}));
const decision = (plan,id) => plan.decisions.find(item=>item.entryId===id);
const selected = plan => plan.decisions.filter(item=>item.targets.length).map(item=>item.entryId);

// Keep the two reproduced failures as lasting regressions.
test('操作标识在异步匹配前冻结', async () => {
  const input = request([entry('e', {regex:true})]);
  const pending = buildWorldbookPlan(input);
  input.operationId = 'changed'; input.revision = 99;
  const plan = await pending;
  assert.equal(plan.operationId, 'probe'); assert.equal(plan.revision, 1);
});
test('未选中的历史和角色不占扫描容量', async () => {
  const input = request([entry('e', {roles:['assistant'],depth:1,keywords:['灯塔']})], {
    currentInput:'很长的玩家文字'.repeat(100),
    turns:[{id:'old',complete:true,user:'很长的旧玩家文字'.repeat(100),assistant:'旧回复'.repeat(100)}, {id:'last',complete:true,user:'新玩家文字'.repeat(100),assistant:'灯塔'}],
    capacity:{scanChars:2,injectedChars:1000,systemChars:1000,userChars:1000},
  });
  const plan = await buildWorldbookPlan(input);
  assert.equal(plan.assembly.system, 'e'); assert.equal(plan.scan.system.chars,2);
  assert.deepEqual(plan.scan.system.sources,[{source:'last',role:'assistant'}]);
});
test('归一化按最大余数法，保持文字和原输入，零权重不分配余数', () => {
  const original = weights(1,1,1), copy = structuredClone(original);
  assert.deepEqual(normalizeWorldbookWeights(original).map(item=>item.weight),[33.34,33.33,33.33]);
  assert.deepEqual(original,copy);
  const normalized=normalizeWorldbookWeights(weights(0,1,2));
  assert.deepEqual(normalized,[{text:'正文0',weight:0},{text:'正文1',weight:33.33},{text:'正文2',weight:66.67}]);
});
test('极大、极小有限权重及小数比例不溢出，结果恰为一万个百分比基点', () => {
  for(const values of [[Number.MAX_VALUE,Number.MAX_VALUE],[Number.MIN_VALUE,Number.MIN_VALUE],[1e-300,2e-300],[Number.MIN_VALUE,Number.MAX_VALUE],[0.1,0.2,0.3]]) {
    const result=normalizeWorldbookWeights(weights(...values));
    assert.equal(result.reduce((sum,item)=>sum+Math.round(item.weight*100),0),10000);
    assert(result.every(item=>Number.isFinite(item.weight)&&item.weight>=0));
  }
  assert.deepEqual(normalizeWorldbookWeights(weights(Number.MAX_VALUE,Number.MAX_VALUE)).map(item=>item.weight),[50,50]);
  assert.deepEqual(normalizeWorldbookWeights(weights(Number.MIN_VALUE,Number.MAX_VALUE)).map(item=>item.weight),[0,100]);
  for(let count=1;count<=200;count++) {
    const result=normalizeWorldbookWeights(weights(...Array.from({length:count},(_,i)=>(i+1)%5)));
    assert.equal(result.reduce((sum,item)=>sum+Math.round(item.weight*100),0),10000);
  }
});
test('负数、非有限值、空列表、全零权重明确拒绝', () => {
  for(const invalid of [-1,NaN,Infinity,-Infinity,'1']) assert.throws(()=>normalizeWorldbookWeights(weights(invalid)),errorCode('AUTHORING_WORLDBOOK_WEIGHTS'));
  assert.throws(()=>normalizeWorldbookWeights([]),errorCode('AUTHORING_WORLDBOOK_WEIGHTS'));
  assert.throws(()=>normalizeWorldbookWeights(weights(0,0)),errorCode('AUTHORING_WORLDBOOK_ZERO_WEIGHT'));
});
test('加权抽取边界准确，零权重永不被选中，随机值必须显式且小于1', () => {
  const bodies=weights(0,0.1,0.2,0.7);
  assert.equal(chooseWorldbookContent(bodies,0),1);
  assert.equal(chooseWorldbookContent(bodies,0.1),2);
  assert.equal(chooseWorldbookContent(bodies,0.3),3);
  assert.equal(chooseWorldbookContent(bodies,0.9999999999999999),3);
  assert.equal(chooseWorldbookContent(weights(Number.MAX_VALUE,Number.MAX_VALUE),0.5),1);
  for(const bad of [undefined,null,-1,1,NaN,Infinity]) assert.throws(()=>chooseWorldbookContent(bodies,bad),errorCode('AUTHORING_WORLDBOOK_DRAW_REQUIRED'));
});
test('最大有限权重经实际存储、封存、导出、新目录导入及重启不变', async t => {
  const directory=await mkdtemp(join(runRoot,'store-')), dest=await mkdtemp(join(runRoot,'import-'));
  const a=await AuthoringStore.open(directory),b=await AuthoringStore.open(dest);
  t.after(()=>a.close());t.after(()=>b.close());
  const doc=blankDocument();doc.metadata.name='权重往返';doc.metadata.audience='male';doc.sources.system='原始系统提示词';
  doc.worldbook.entries=[entry('extremes',{mode:'weighted',contents:weights(Number.MAX_VALUE,Number.MIN_VALUE,0)})];
  assert.deepEqual(validateDocument(doc),doc);assert.deepEqual(publicationIssues(doc,[]),[]);
  const created=await a.create({operationId:'create',expectedRevision:null,document:doc});
  const version=await a.publish(created.workId,{operationId:'publish',expectedRevision:0});
  const payload=await a.exportPackage(created.workId,version.result.versionId);
  const inspect=await b.inspectImport(payload);assert.equal(inspect.valid,true);
  const imported=await b.importPackage({operationId:'import',workId:null,expectedRevision:null,expectedImportHash:inspect.payloadHash,payload});
  assert.deepEqual((await b.read(imported.workId)).draft,doc);
  await b.close();const c=await AuthoringStore.open(dest);t.after(()=>c.close());
  assert.deepEqual((await c.read(imported.workId)).draft,doc);
  doc.worldbook.entries[0].contents=weights(0,0);
  assert(publicationIssues(doc,[]).some(item=>item.code==='AUTHORING_WEIGHT_TOTAL_ZERO'));
});
test('空世界书只组装一次前后置词，保留源码空白，不扫描或改写历史', async () => {
  const input=request([], {turns:history(['旧玩家','旧助手'])});
  input.document.sources.system='  SYSTEM\r\n';input.document.sources.prefix='\n<pre>\n';input.document.sources.suffix='\r\n尾词  ';
  input.capacity.scanChars=0;const original=structuredClone(input);
  const plan=await buildWorldbookPlan(input);
  assert.equal(plan.assembly.system,original.document.sources.system);
  assert.equal(plan.assembly.user,'\n<pre>\n\n\n灯塔\n\n\r\n尾词  ');
  assert.deepEqual(plan.scan,{wrappers:{chars:0,sources:[]},system:{chars:0,sources:[]}});
  assert.deepEqual(input,original);assert.deepEqual(plan.decisions,[]);
});
test('扫描深度0仅本轮玩家；1仅最近完整回合；未完成尾部不参与', async () => {
  const turns=history(['旧玩家灯塔','旧回复'],['新玩家','新回复']);
  turns.push({id:'partial',complete:false,user:'灯塔',assistant:'灯塔'});
  const entries=[entry('zero'),entry('one',{depth:1}),entry('two',{depth:2}),entry('ai-zero',{roles:['assistant'],depth:0})];
  const plan=await buildWorldbookPlan(request(entries,{turns,currentInput:'现在'}));
  assert.deepEqual(selected(plan),['two']);
  assert.deepEqual(decision(plan,'two').phases[0].hits,[{keywordIndex:0,source:'turn-0',role:'user'}]);
});
test('深度20保留正确窗口，20之外和未来未完成回合均不泄漏', async () => {
  const turns=Array.from({length:22},(_,i)=>({id:'turn-'+i,complete:true,user:i===0?'遥远灯塔':i===2?'窗口灯塔':'无',assistant:'无'}));
  turns.push({id:'future',complete:false,user:'未来灯塔',assistant:'未来灯塔'});
  const plan=await buildWorldbookPlan(request([entry('old',{depth:20,keywords:['遥远灯塔']}),entry('in',{depth:20,keywords:['窗口灯塔']}),entry('future',{depth:20,keywords:['未来灯塔']})],{turns,currentInput:'无'}));
  assert.deepEqual(selected(plan),['in']);assert.equal(decision(plan,'in').phases[0].hits[0].source,'turn-2');
});
test('中英文与字面标点、同名别名可匹配；与条件跨消息满足', async () => {
  const entries=[entry('alias',{keywords:['灯塔','LIGHTHOUSE'],rule:'or'}),entry('cross',{keywords:['船长','Alice'],rule:'and',roles:['user','assistant'],depth:1}),entry('literal',{keywords:['a+b[1]']}),entry('miss',{keywords:['a.*b'],body:'不该注入'})];
  const plan=await buildWorldbookPlan(request(entries,{currentInput:'lIgHtHoUsE 船长 a+b[1]',turns:history(['无','ALICE'])}));
  assert.deepEqual(selected(plan),['alias','cross','literal']);
  assert.deepEqual(decision(plan,'cross').phases[0].hits.map(item=>[item.source,item.role]),[['current-with-wrappers','user'],['turn-0','assistant']]);
  assert(!plan.assembly.system.includes('不该注入'));
});
test('用户与AI角色过滤分别生效', async () => {
  const entries=[entry('user',{depth:1,roles:['user']}),entry('ai',{depth:1,roles:['assistant']}),entry('both',{depth:1,roles:['user','assistant']})];
  const plan=await buildWorldbookPlan(request(entries,{currentInput:'无',turns:history(['无','灯塔'])}));
  assert.deepEqual(selected(plan),['ai','both']);
});
test('按插入顺序、保存列表稳定排序，同序源码在条目前', async () => {
  const input=request([entry('z',{order:10}),entry('a',{order:10}),entry('first',{order:0}),entry('last',{order:9999})]);
  input.document.sources.system='主词';input.document.settings.orders.system=10;
  const plan=await buildWorldbookPlan(input);
  assert.equal(plan.assembly.system,'first\n\n主词\n\nz\n\na\n\nlast');
  assert.deepEqual(plan.segments.system.map(item=>item.id),['first','source:system','z','a','last']);
});
test('前后置世界书及静态前后置词先完成，再触发主词世界书；不递归', async () => {
  const input=request([
    entry('prefix',{targets:['prefix'],body:'红钥匙'}),entry('suffix',{targets:['suffix'],body:'蓝钥匙'}),
    entry('main',{rule:'and',keywords:['红钥匙','蓝钥匙','静态'],body:'金钥匙'}),
    entry('no-prefix-recursion',{targets:['prefix'],keywords:['红钥匙'],body:'递归一'}),
    entry('no-system-recursion',{keywords:['金钥匙'],body:'递归二'}),
  ]);input.document.sources.prefix='静态前词';
  const plan=await buildWorldbookPlan(input);
  assert.equal(plan.assembly.system,'金钥匙');assert.equal(plan.assembly.user,'静态前词\n\n红钥匙\n\n灯塔\n\n蓝钥匙');
  assert.deepEqual(selected(plan),['prefix','suffix','main']);
});
test('同条目跨三目标只用一次概率和正文选择，各次注入独立计容量', async () => {
  const plan=await buildWorldbookPlan(request([entry('multi',{targets:['system','prefix','suffix'],chance:50,mode:'weighted',contents:[{text:'甲',weight:1},{text:'乙',weight:3}]})],{draws:[{id:'multi',trigger:0.49,content:0.25}]}));
  assert.equal(plan.assembly.system,'乙');assert.equal(plan.assembly.prefix,'乙');assert.equal(plan.assembly.suffix,'乙');
  assert.deepEqual(decision(plan,'multi').trigger,{chance:50,draw:0.49,pass:true});
  assert.deepEqual(decision(plan,'multi').targets,['prefix','suffix','system']);
  assert.equal(decision(plan,'multi').content.index,1);assert.equal(plan.injectedChars,3);
});
test('触发概率与内容权重分离；失败不抽正文、后续阶段不重判', async () => {
  const plan=await buildWorldbookPlan(request([entry('half',{targets:['prefix','system'],chance:50,mode:'weighted',contents:weights(1,3)})],{draws:[{id:'half',trigger:0.5,content:null}]}));
  const result=decision(plan,'half');assert.deepEqual(result.trigger,{chance:50,draw:0.5,pass:false});
  assert.equal(result.phases.length,2);assert.equal(result.content,null);assert.deepEqual(selected(plan),[]);
});
test('0和100触发概率无需随机数；小数概率比较保留边界', async () => {
  const plan=await buildWorldbookPlan(request([entry('never',{chance:0}),entry('always'),entry('under',{chance:0.1}),entry('boundary',{chance:0.1})],{draws:[{id:'under',trigger:0.000999,content:null},{id:'boundary',trigger:0.001,content:null}]}));
  assert.deepEqual(selected(plan),['always','under']);
  assert.equal(decision(plan,'never').trigger.draw,null);assert.equal(decision(plan,'always').trigger.draw,null);
});
test('只有命中并需要概率时要求固定随机数，不自动调用随机源', async () => {
  await rejected(()=>buildWorldbookPlan(request([entry('e',{chance:25})])),'AUTHORING_WORLDBOOK_DRAW_REQUIRED');
  await rejected(()=>buildWorldbookPlan(request([entry('e',{mode:'weighted',contents:weights(1)})])),'AUTHORING_WORLDBOOK_DRAW_REQUIRED');
  assert.deepEqual(selected(await buildWorldbookPlan(request([entry('e',{chance:25,keywords:['不存在']})]))),[]);
});
test('空关键词、全零加权、重复抽签和未知字段均拒绝，停用条目不触发', async () => {
  for(const keywords of [[],[''],['  ']]) await rejected(()=>buildWorldbookPlan(request([entry('e',{keywords})])),'AUTHORING_WORLDBOOK_KEYWORDS');
  await rejected(()=>buildWorldbookPlan(request([entry('e',{mode:'weighted',contents:weights(0)})])),'AUTHORING_WORLDBOOK_ZERO_WEIGHT');
  const input=request([entry('off',{enabled:false,keywords:[],mode:'weighted',contents:[]})]);
  assert.deepEqual((await buildWorldbookPlan(input)).decisions,[]);
  await rejected(()=>buildWorldbookPlan(request([entry('e')],{draws:[{id:'e',trigger:0,content:0},{id:'e',trigger:0,content:0}]})),'AUTHORING_WORLDBOOK_DRAW_REQUIRED');
  for(const field of ['summary','memory','sentHistory']) await rejected(()=>buildWorldbookPlan({...request(),[field]:'不可把注入历史作为来源'}),'AUTHORING_UNKNOWN_FIELD');
});
test('隔离正则支持Unicode和大小写，与条件可跨消息但单条正则不跨消息拼接', async () => {
  const input=request([entry('regex',{regex:true,roles:['user','assistant'],depth:1,rule:'and',keywords:['^ALICE$', '灯塔\\s+北']}),entry('cross-regex',{regex:true,roles:['user','assistant'],depth:1,keywords:['ALICE.*灯塔']})],{currentInput:'Alice',turns:history(['无','灯塔 北门'])});
  const plan=await buildWorldbookPlan(input);assert.deepEqual(selected(plan),['regex']);
  assert.deepEqual(decision(plan,'regex').phases[0].hits,[{keywordIndex:0,source:'current-with-wrappers',role:'user'},{keywordIndex:1,source:'turn-0',role:'assistant'}]);
});
test('正则仅接收被选中的消息，无关大文本不占Worker预算', async () => {
  const input=request([entry('regex',{regex:true,roles:['assistant'],depth:1})],{currentInput:'无'.repeat(2000001),turns:history(['忽略','灯塔'])});
  input.capacity.scanChars=2;input.capacity.userChars=2000000;
  await rejected(()=>buildWorldbookPlan(input),'AUTHORING_WORLDBOOK_OUTPUT_CAPACITY');
  input.currentInput='无';input.turns[0].user='无'.repeat(2000001);
  assert.deepEqual(selected(await buildWorldbookPlan(input)),['regex']);
});
test('正则语法错误定位条目与关键词，不回显原始输入', async () => {
  await assert.rejects(()=>buildWorldbookPlan(request([entry('broken',{regex:true,keywords:['[']})],{currentInput:'私人原文'})),error=>{
    assert.equal(error.code,'AUTHORING_WORLDBOOK_REGEX_SYNTAX');
    assert.deepEqual(error.details,{entryId:'broken',keywordIndex:0});assert(!error.message.includes('私人原文'));return true;
  });
});
test('灾难性正则超时终止且主线程仍运行，后续任务可正常执行', {timeout:5000}, async () => {
  let heartbeats=0;const timer=setInterval(()=>heartbeats++,10);
  try {
    await rejected(()=>evaluateRegexBatch([{id:'slow',patterns:['(a+)+$'],messageIndexes:[0]}],['a'.repeat(40)+'!'],{timeoutMs:300}),'AUTHORING_WORLDBOOK_REGEX_TIMEOUT');
  } finally {clearInterval(timer);}
  assert(heartbeats>0);
  const result=await evaluateRegexBatch([{id:'next',patterns:['灯塔'],messageIndexes:[0]}],['灯塔']);
  assert.deepEqual(result,[{id:'next',hits:[{keywordIndex:0,messageIndex:0,offset:0}]}]);
});
test('正则输入索引、重复ID、重复消息与时间上限受校验', async () => {
  const job={id:'e',patterns:['x'],messageIndexes:[0]};
  for(const jobs of [[{...job,messageIndexes:[1]}],[job,job],[{...job,messageIndexes:[0,0]}],[{...job,patterns:[]}]]) await rejected(()=>evaluateRegexBatch(jobs,['x']),'AUTHORING_WORLDBOOK_REGEX_INPUT');
  await rejected(()=>evaluateRegexBatch([job],['x'],{timeoutMs:2001}),'AUTHORING_WORLDBOOK_REGEX_TIMEOUT_CONFIG');
  await rejected(()=>evaluateRegexBatch([job],['x'.repeat(2000001)]),'AUTHORING_WORLDBOOK_REGEX_CAPACITY');
});
test('扫描、注入、最终输出容量分别阻断，不返回截断计划；Unicode按码点计数', async () => {
  const input=request([entry('e',{body:'🗝️'})]);
  input.capacity.scanChars=1;await rejected(()=>buildWorldbookPlan(input),'AUTHORING_WORLDBOOK_SCAN_CAPACITY');
  input.capacity.scanChars=2;input.capacity.injectedChars=1;await rejected(()=>buildWorldbookPlan(input),'AUTHORING_WORLDBOOK_INJECTION_CAPACITY');
  input.capacity.injectedChars=2;input.capacity.systemChars=1;await rejected(()=>buildWorldbookPlan(input),'AUTHORING_WORLDBOOK_OUTPUT_CAPACITY');
  input.capacity.systemChars=2;const plan=await buildWorldbookPlan(input);assert.equal(plan.assembly.system,'🗝️');assert.equal(plan.injectedChars,2);
});
test('工作量超限在匹配前拒绝，前后置扩展后再次检查扫描预算', async () => {
  const heavy=request([entry('e',{keywords:Array(200).fill('x')})],{currentInput:'x'.repeat(110000)});
  heavy.capacity.scanChars=200000;await rejected(()=>buildWorldbookPlan(heavy),'AUTHORING_WORLDBOOK_MATCH_CAPACITY');
  const input=request([entry('prefix',{targets:['prefix'],body:'长前词'}),entry('main')]);
  input.capacity.scanChars=2;await rejected(()=>buildWorldbookPlan(input),'AUTHORING_WORLDBOOK_SCAN_CAPACITY');
});
test('深度、顺序、概率、来源、容量与历史ID非法时明确拒绝', async () => {
  for(const change of [{depth:21},{order:-1},{order:10000},{chance:101}]) await rejected(()=>buildWorldbookPlan(request([entry('e',change)])),'AUTHORING_NUMBER_INVALID');
  await rejected(()=>buildWorldbookPlan(request([],{currentInput:'\ud800'})),'AUTHORING_WORLDBOOK_SOURCE');
  await rejected(()=>buildWorldbookPlan(request([],{capacity:{scanChars:Infinity,injectedChars:1,systemChars:1,userChars:1}})),'AUTHORING_WORLDBOOK_CAPACITY_CONFIG');
  await rejected(()=>buildWorldbookPlan(request([],{turns:[{id:'a',complete:true,user:'x',assistant:'x'},{id:'a',complete:true,user:'x',assistant:'x'}]})),'AUTHORING_WORLDBOOK_HISTORY');
});
test('同输入与固定随机数计划一致且深冻结，持久化副本可离线重放', async () => {
  const input=request([entry('e',{mode:'weighted',contents:weights(1,2,3),chance:80})],{draws:[{id:'e',trigger:0.1,content:0.75}]});
  const a=await buildWorldbookPlan(input),b=await buildWorldbookPlan(structuredClone(input));assert.deepEqual(a,b);
  assert(Object.isFrozen(a)&&Object.isFrozen(a.assembly)&&Object.isFrozen(a.decisions[0].phases));
  assert.throws(()=>{a.assembly.system='改写';},TypeError);
  const file=join(runRoot,'frozen-plan.json');await writeFile(file,JSON.stringify(a));
  const saved=JSON.parse(await readFile(file,'utf8'));
  assert.deepEqual(replayWorldbookPlan(saved,{operationId:a.operationId,revision:a.revision,contextHash:a.contextHash}),a);
  const {sha256:hash,...payload}=a;assert.equal(hash,sha256(canonical(payload)));
  await writeFile(join(runRoot,'acceptance-assembly.json'),JSON.stringify({input,plan:a},null,2));
});
test('操作、revision、来源hash或持久化内容改变时拒绝重放', async () => {
  const plan=await buildWorldbookPlan(request([entry('e')])),identity={operationId:plan.operationId,revision:plan.revision,contextHash:plan.contextHash};
  for(const change of [{operationId:'other'},{revision:2},{contextHash:'0'.repeat(64)}]) assert.throws(()=>replayWorldbookPlan(plan,{...identity,...change}),errorCode('AUTHORING_WORLDBOOK_PLAN_MISMATCH'));
  const edited=structuredClone(plan);edited.assembly.system='tampered';assert.throws(()=>replayWorldbookPlan(edited,identity),errorCode('AUTHORING_WORLDBOOK_PLAN_MISMATCH'));
});
test('等待Worker时修改编辑器文档、历史、随机数或容量不改变当前快照', async () => {
  const input=request([entry('e',{regex:true,depth:1,mode:'weighted',contents:weights(1,1)})],{turns:history(['灯塔','原历史']),draws:[{id:'e',trigger:null,content:0.2}]});
  const original=structuredClone(input),pending=buildWorldbookPlan(input);
  input.document.worldbook.entries[0].contents[0].text='后来的内容';input.turns[0].assistant='后来的历史';input.draws[0].content=0.9;input.capacity.injectedChars=0;
  assert.deepEqual(await pending,await buildWorldbookPlan(original));
});
