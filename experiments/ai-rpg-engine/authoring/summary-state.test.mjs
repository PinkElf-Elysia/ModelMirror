import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {OfflineModelControl,emptyModelState,validateModelState} from './model-control.mjs';
import {AuthoringStore} from './store.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {OfflineSessionHost,OfflineFixtureProvider} from './offline-session.mjs';
import {COMPRESSION_INSTRUCTION as originalCompression} from '../studio/summary-compression.mjs';
import {SUMMARY_INSTRUCTION,SUMMARY_WRAPPER,COMPRESSION_INSTRUCTION,SUMMARY_LIMITS,
  requireSummarySession,initialAuthoringSummary,validateAuthoringSummary,summaryUpdateSource,
  createSummaryTask,inspectSummaryResult,createSummaryCompression,commitSummaryUpdate,reviseSummary} from './summary-state.mjs';
import {summaryContextPolicy,assembleSummaryContext,replaySummaryContext} from './summary-context.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6h-tests/',import.meta.url));
await mkdir(root,{recursive:true});
const digest=value=>sha256(canonical(value)),clone=value=>structuredClone(value);
const seal=value=>{const {sha256:ignored,...body}=value;return {...body,sha256:digest(body)};};
const rejects=(action,code)=>assert.throws(action,error=>error.code===code);
const structuredError=action=>assert.throws(action,error=>typeof error.code==='string');
const authorization={enabled:true,revision:sha256('summary grant 1')};
const config={turns:1,includeInitialCharacter:false};
const base={system:'  作者 SYSTEM\r\n<setting>原文</setting>',user:'前置\n当前玩家原文\n后置'};
const grants=(summary=true,history=true,memory=false)=>({
  history:{enabled:history,revision:sha256('M1:'+history)},
  summary:{...authorization,enabled:summary},
  memory:{enabled:memory,revision:sha256('M3:'+memory)},
});
const makeCatalog=(contextLength=128000)=>({catalogId:'generic-summary-fixture',models:['story','summary','other'].map(route=>({
  selectionId:route,selectionRevision:sha256(route),modelId:'fixture/'+route,name:'独立模型 '+route,available:true,
  contextLength,maxOutputTokens:16000,parameterPolicy:{kind:'editable',fields:{
    maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'},
  },exclusive:[]},
}))});
function choose(session,purpose='summary',route=purpose,control=new OfflineModelControl(makeCatalog())) {
  const c=control.catalog(),input=route===null?null:{catalogRevision:c.revision,selectionId:route,selectionRevision:sha256(route),parameters:{maxOutputTokens:4096}};
  const intent={sessionId:session.id,operationId:'choose-'+purpose+'-'+session.models.revision,expectedRevision:session.revision,
    expectedModelRevision:session.models.revision,purpose,choice:input};
  const choice=input===null?null:control.select(input);
  session.models.changes.push({revision:++session.models.revision,intent,intentHash:digest(intent),choice});
  session.models.current[purpose]=choice;session.revision++;validateModelState(session.models,session.id,session.revision);
  return session;
}
function addTurn(session,user,assistant) {
  const n=session.turns.length+1;
  user??='原始玩家 '+n;assistant??='原始回答 '+n+'\n<details>卡内记忆：海港🐟</details>';
  session.turns.push({id:'turn-'+n,number:n,operationId:'story-'+n,requestId:'request-'+n,complete:true,
    user,userMessage:'前置\n'+user+'\n后置',assistant,rawHash:sha256(assistant),messagesHash:sha256('story messages '+n)});
  session.revision+=3;return session;
}
function session(turns=3,{selected=true,contextLength=128000}={}) {
  const id='session-'+sha256('generic-work-session');
  const s={id,revision:0,binding:{format:'modelmirror.rpg.authored-session-binding/1',sessionId:id,
    workId:'work-'+sha256('自由作品'),versionId:'version-'+sha256('publication'),resourceHash:sha256('resources'),
    runtimeHash:sha256('runtime'),openingHash:sha256('原始开局'),openingText:'原始开局'},
    create:{operationId:'create-session'},models:emptyModelState(),examples:[],turns:[],operations:[]};
  choose(s,'story');if(selected)choose(s,'summary','summary',new OfflineModelControl(makeCatalog(contextLength)));
  while(s.turns.length<turns)addTurn(s);
  return s;
}
const task=(s,state,operationId='summary-update')=>createSummaryTask(s,state,{operationId,expectedSummaryRevision:state.revision,
  expectedModelRevision:s.models.revision,authorization});
function receipt(request,text='海港承诺尚未兑现。',finishReason='stop',status='completed') {
  return seal({kind:'offline-fixture',requestId:request.id,requestHash:digest(request),result:{status,text,finishReason}});
}
const result=(request,...args)=>inspectSummaryResult(request,receipt(request,...args));
const commit=(s,state,candidate,compression=null,auth=authorization)=>commitSummaryUpdate(s,state,{candidate,compression,authorization:auth});
function prepared(s=session(),text) {
  const empty=initialAuthoringSummary(s),request=task(s,empty),candidate=result(request,text);
  return {s,empty,request,candidate,...commit(s,empty,candidate)};
}
const edit=(s,state,text,operationId='manual')=>reviseSummary(s,state,{operationId,expectedSummaryRevision:state.revision,text,restoreVersionId:null});
function assemble(s,state,auth=grants(),historyConfig=config,input=base) {
  const policy=summaryContextPolicy(s,state,{authorization:auth,historyConfig});
  return assembleSummaryContext({session:s,state,authorization:auth,historyConfig,base:input,expectedPolicyRevision:policy.revision});
}

test('冻结指令与既有 M2 原字节相同；不加载 Earth 运行器',async()=>{
  const original=await readFile(new URL('../studio/summary-state.mjs',import.meta.url),'utf8');
  for(const [name,value] of Object.entries({SUMMARY_INSTRUCTION,SUMMARY_WRAPPER})) {
    const literal=original.match(new RegExp('export const '+name+' = ("(?:[^"\\\\]|\\\\.)*");'))?.[1];
    assert.ok(literal,name);assert.equal(value,JSON.parse(literal));
  }
  assert.equal(COMPRESSION_INSTRUCTION,originalCompression);
});

for(const count of [0,1])test(count+' 回合无需摘要任务或隐式选模型',()=>{
  const s=session(count,{selected:false}),state=initialAuthoringSummary(s),before=canonical(s);
  assert.equal(task(s,state).kind,'no-update');assert.equal(summaryUpdateSource(s,state).needsUpdate,false);
  assert.equal(canonical(s),before);assert.equal(state.revision,0);
});

test('首次 catch-up 使用所有待覆盖原始 user/assistant，排除最新回合与已装配前后词',()=>{
  const s=session(5),state=initialAuthoringSummary(s),request=task(s,state);
  assert.deepEqual(request.source.newTurns,[1,2,3,4]);assert.equal(request.source.targetThrough,4);
  assert.deepEqual(JSON.parse(request.messages[1].content).turns,s.turns.slice(0,4).flatMap(t=>[{role:'user',content:t.user},{role:'assistant',content:t.assistant}]));
  assert.equal(request.messages[1].content.includes('前置'),false);assert.equal(request.messages[1].content.includes('原始回答 5'),false);
  assert.ok(request.messages[1].content.includes('卡内记忆'));assert.equal(request.messages.filter(m=>m.role==='system').length,1);
  assert.equal(request.modelChoice.entry.modelId,'fixture/summary');assert.equal(request.messagesHash,digest(request.messages));
});

test('新覆盖范围只追加未处理原文；人工摘要成为下一份任务的上一摘要',()=>{
  const f=prepared(),revised=edit(f.s,f.state,'人工核实：承诺尚未兑现。').state;
  addTurn(f.s,'新的选择','后续完整原文');
  const next=task(f.s,revised,'summary-next'),payload=JSON.parse(next.messages[1].content);
  assert.deepEqual(next.source.newTurns,[3]);assert.equal(payload.previousSummary,'人工核实：承诺尚未兑现。');
  assert.deepEqual(payload.turns,[{role:'user',content:f.s.turns[2].user},{role:'assistant',content:f.s.turns[2].assistant}]);
});

test('独立摘要模型必须显式选择，不回退剧情模型；剧情选择变化不影响已冻结摘要',()=>{
  const s=session(3,{selected:false}),empty=initialAuthoringSummary(s);
  rejects(()=>task(s,empty),'SUMMARY_MODEL_REQUIRED');
  choose(s);const request=task(s,empty),candidate=result(request);choose(s,'story','other');
  assert.equal(commit(s,empty,candidate).version.effectiveText,'海港承诺尚未兑现。');
  assert.equal(request.modelChoice.entry.modelId,'fixture/summary');
});

test('模型输入容量不足整体拒绝，不裁剪来源或改写旧状态',()=>{
  const s=session(3,{contextLength:5000}),empty=initialAuthoringSummary(s),before=canonical({s,empty});
  rejects(()=>task(s,empty),'MODEL_CONTEXT_CAPACITY');assert.equal(canonical({s,empty}),before);
});

test('Unicode 正文边界按字符计数，10000 emoji 原样保存，不自动压缩',()=>{
  const s=session(),empty=initialAuthoringSummary(s),text='🐟'.repeat(10000),candidate=result(task(s,empty),text);
  assert.equal(candidate.status,'accepted');assert.equal(commit(s,empty,candidate).version.effectiveText,text);
});

for(const [name,text,finish,status,code] of [
  ['空白',' \r\n','stop','completed','SUMMARY_OUTPUT_EMPTY'],
  ['截断','截断原文🐟','length','completed','SUMMARY_OUTPUT_TRUNCATED'],
  ['失败','本地失败原文','fixture_failure','failed','SUMMARY_PROVIDER_FAILED'],
])test(name+'保留完整候选证据，不推进摘要版本',()=>{
  const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty),text,finish,status),before=canonical(empty);
  assert.equal(candidate.status,'rejected');assert.equal(candidate.reason,code);assert.equal(candidate.receipt.result.text,text);
  rejects(()=>commit(s,empty,candidate),code);assert.equal(canonical(empty),before);
  rejects(()=>createSummaryCompression(candidate,'compress'),'SUMMARY_COMPRESSION_INVALID');
});

test('超过10000需要一次显式压缩，保存原始长输出与独立压缩任务',()=>{
  const s=session(),empty=initialAuthoringSummary(s),raw='🐟'.repeat(10001),candidate=result(task(s,empty),raw);
  assert.equal(candidate.status,'requires-compression');rejects(()=>commit(s,empty,candidate),'SUMMARY_COMPRESSION_REQUIRED');
  const request=createSummaryCompression(candidate,'compress-summary'),compressed=result(request,'压'.repeat(3000));
  assert.equal(request.modelChoice.sha256,candidate.request.modelChoice.sha256);assert.notEqual(request.id,candidate.request.id);
  assert.equal(JSON.parse(request.messages[1].content).summary,raw);
  const done=commit(s,empty,candidate,compressed);validateAuthoringSummary(done.state,s);
  assert.equal(done.version.effectiveText.length,3000);assert.equal(done.version.result.candidate.receipt.result.text,raw);
  assert.equal(done.version.result.compression.request.operationId,'compress-summary');
  rejects(()=>createSummaryCompression(compressed,'again'),'SUMMARY_COMPRESSION_INVALID');
});

test('压缩超限或截断不生效，也不能再压缩；正常摘要拒绝额外压缩',()=>{
  const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty),'x'.repeat(10001));
  const request=createSummaryCompression(candidate,'compress');
  for(const [text,finish,code] of [['🐟'.repeat(3001),'stop','SUMMARY_COMPRESSION_TOO_LONG'],['短摘要','length','SUMMARY_OUTPUT_TRUNCATED']]) {
    const compression=result(request,text,finish);
    rejects(()=>commit(s,empty,candidate,compression),code);rejects(()=>createSummaryCompression(compression,'second'),'SUMMARY_COMPRESSION_INVALID');
  }
  rejects(()=>createSummaryCompression(candidate,candidate.request.operationId),'SUMMARY_COMPRESSION_INVALID');
  rejects(()=>commit(s,empty,result(candidate.request),result(request)),'SUMMARY_COMPRESSION_UNEXPECTED');
});

test('压缩必须绑定同一份长输出和原操作，另一任务的有效回执也不能代用',()=>{
  const s=session(),empty=initialAuthoringSummary(s),a=result(task(s,empty),'a'.repeat(10001)),b=result(task(s,empty,'different'),'b'.repeat(10001));
  rejects(()=>commit(s,empty,a,result(createSummaryCompression(b,'compress'))),'SUMMARY_COMPRESSION_INVALID');
});

test('任务、原始回执、候选状态任何一项篡改均被拒绝',()=>{
  const s=session(),empty=initialAuthoringSummary(s),request=task(s,empty),r=receipt(request);
  const wrong=seal({...r,requestHash:sha256('other')});rejects(()=>inspectSummaryResult(request,wrong),'SUMMARY_RECEIPT_INVALID');
  const invalidTask=clone(request);invalidTask.messages[0].content='FORGED';rejects(()=>inspectSummaryResult(invalidTask,r),'SUMMARY_TASK_INVALID');
  const candidate=result(request,' \n');candidate.status='accepted';candidate.reason=null;
  rejects(()=>commit(s,empty,candidate),'SUMMARY_CANDIDATE_INVALID');
  rejects(()=>inspectSummaryResult(request,receipt(request,'x'.repeat(200001))),'SUMMARY_RECEIPT_INVALID');
});

test('人工修订/同覆盖恢复新增版本，不覆盖生成原文；同操作可恢复',()=>{
  const f=prepared(),raw=clone(f.state.versions[0]),input={operationId:'manual',expectedSummaryRevision:1,text:'人工校正',restoreVersionId:null};
  const revised=reviseSummary(f.s,f.state,input),restored=reviseSummary(f.s,revised.state,{operationId:'restore',expectedSummaryRevision:2,text:null,restoreVersionId:raw.id});
  assert.deepEqual(restored.state.versions[0],raw);assert.equal(restored.version.effectiveText,raw.effectiveText);assert.equal(restored.state.revision,3);
  assert.equal(reviseSummary(f.s,restored.state,input).replayed,true);validateAuthoringSummary(restored.state,f.s);
  rejects(()=>reviseSummary(f.s,restored.state,{...input,text:'另一份内容'}),'SUMMARY_OPERATION_COLLISION');
});

test('恢复旧覆盖范围不能抹掉后来已纳入的历史；空或超长人工修订拒绝',()=>{
  const f=prepared();addTurn(f.s);const updated=commit(f.s,f.state,result(task(f.s,f.state,'next')));
  rejects(()=>reviseSummary(f.s,updated.state,{operationId:'restore-old',expectedSummaryRevision:2,text:null,restoreVersionId:f.version.id}),'SUMMARY_RESTORE_COVERAGE_MISMATCH');
  for(const bad of ['', ' ', '🐟'.repeat(10001)])rejects(()=>edit(f.s,updated.state,bad),'SUMMARY_TEXT_INVALID');
  const fresh=session();rejects(()=>edit(fresh,initialAuthoringSummary(fresh),'内容'),'SUMMARY_NOT_AVAILABLE');
});

test('并发人工修订使旧更新失效，旧摘要与候选均保留',()=>{
  const f=prepared();addTurn(f.s);const candidate=result(task(f.s,f.state,'pending')),revised=edit(f.s,f.state,'人工修订').state,before=canonical(revised);
  rejects(()=>commit(f.s,revised,candidate),'SUMMARY_REVISION_CONFLICT');assert.equal(canonical(revised),before);
  rejects(()=>createSummaryTask(f.s,revised,{operationId:'stale',expectedSummaryRevision:1,expectedModelRevision:f.s.models.revision,authorization}),'SUMMARY_REVISION_CONFLICT');
});

test('新增完整回合、原始历史变化、摘要模型变化使迟到任务失效',()=>{
  for(const mutation of [s=>addTurn(s),s=>{s.turns[0].user='修改了原始玩家文本';}]) {
    const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty));mutation(s);
    rejects(()=>commit(s,empty,candidate),'SUMMARY_SOURCE_CHANGED');
  }
  const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty));choose(s,'summary','other');
  rejects(()=>commit(s,empty,candidate),'SUMMARY_MODEL_CHANGED');
});

test('撤权、卸载后重装产生新授权 revision，迟到结果不应用；已应用原操作只读恢复',()=>{
  const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty));
  for(const auth of [{...authorization,enabled:false},{enabled:true,revision:sha256('reinstall')}])
    rejects(()=>commit(s,empty,candidate,null,auth),'SUMMARY_AUTHORIZATION_CHANGED');
  const done=commit(s,empty,candidate);assert.equal(commit(s,done.state,candidate,null,{...authorization,enabled:false}).replayed,true);
  assert.equal(done.state.revision,1);
});

test('重复提交不新增版本，相同操作不同输出拒绝；输入对象不被修改',()=>{
  const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty)),before=canonical({s,empty,candidate});
  const done=commit(s,empty,candidate);assert.equal(canonical({s,empty,candidate}),before);
  const replay=commit(s,done.state,candidate);assert.equal(replay.replayed,true);assert.deepEqual(replay.state,done.state);
  rejects(()=>commit(s,done.state,result(candidate.request,'另一份输出')),'SUMMARY_OPERATION_COLLISION');
  rejects(()=>task(s,done.state),'SUMMARY_OPERATION_COLLISION');
});

test('摘要更新和压缩操作不能复用剧情、模型配置或创建操作 ID',()=>{
  const s=session(),empty=initialAuthoringSummary(s);
  for(const id of ['story-1','choose-summary-1','create-session'])rejects(()=>task(s,empty,id),'SUMMARY_OPERATION_COLLISION');
  const c=result(task(s,empty),'x'.repeat(10001));
  rejects(()=>commit(s,empty,c,result(createSummaryCompression(c,'story-1'))),'SUMMARY_OPERATION_COLLISION');
  const f=prepared(s);rejects(()=>edit(s,f.state,'新内容','story-1'),'SUMMARY_OPERATION_COLLISION');
});

test('显式模型 revision 过期与禁用授权拒绝，不能靠新 ID 绕过',()=>{
  const s=session(),empty=initialAuthoringSummary(s),input={operationId:'new-id',expectedSummaryRevision:0,expectedModelRevision:0,authorization};
  rejects(()=>createSummaryTask(s,empty,input),'MODEL_REVISION_CONFLICT');
  rejects(()=>createSummaryTask(s,empty,{...input,expectedModelRevision:s.models.revision,authorization:{...authorization,enabled:false}}),'SUMMARY_AUTHORIZATION_REQUIRED');
});

test('跨会话/作品版本隔离；重算外层 hash 不能伪造来源和有效正文',()=>{
  const f=prepared(),other=clone(f.s);other.binding.versionId='version-'+sha256('later');
  rejects(()=>validateAuthoringSummary(f.state,other),'SUMMARY_STATE_INVALID');
  for(const mutate of [v=>v.effectiveText='伪造摘要',v=>v.sourceHash=sha256('foreign'),v=>v.coveredThrough=1]) {
    let state=clone(f.state);const v=state.versions[0];mutate(v);const {id,...body}=v;v.id=digest(body);state.activeVersionId=v.id;state=seal(state);
    structuredError(()=>validateAuthoringSummary(state,f.s));
  }
});

test('畸形持久候选返回结构诊断，不抛 TypeError',()=>{
  const f=prepared();let state=clone(f.state);const v=state.versions[0];v.result.candidate=null;
  const {id,...body}=v;v.id=digest(body);state.activeVersionId=v.id;state=seal(state);
  structuredError(()=>validateAuthoringSummary(state,f.s));
});

test('不完整/重复回合和不良 Unicode 拒绝，版本数/原始历史超限明确阻断',()=>{
  for(const mutate of [s=>s.turns[0].complete=false,s=>s.turns[1].requestId=s.turns[0].requestId,s=>s.turns[0].rawHash=sha256('wrong')]) {
    const s=session();mutate(s);rejects(()=>initialAuthoringSummary(s),'SUMMARY_HISTORY_INVALID');
  }
  const s=session();s.turns[0].user='\ud800';rejects(()=>initialAuthoringSummary(s),'AUTHORING_UNICODE_INVALID');
  s.turns[0].user='a'.repeat(2000001);rejects(()=>initialAuthoringSummary(s),'SUMMARY_SOURCE_CAPACITY');
  const f=prepared();const state=seal({...f.state,revision:1001,versions:Array(1001).fill(f.version)});
  rejects(()=>validateAuthoringSummary(state,f.s),'SUMMARY_STATE_CAPACITY');
  assert.equal(SUMMARY_LIMITS.versions,1000);
});

test('M2 单 system + 摘要数据段 + 最新完整回合；示例和前后词仅一次',()=>{
  const s=session();s.examples=[{role:'user',content:'预设问题'},{role:'assistant',content:'预设回答'}];
  const f=prepared(s,'摘要含 $& 与 {summary} 均为原文字面值'),out=assemble(s,f.state);
  assert.deepEqual(out.messages.map(m=>m.role),['system','user','assistant','user','assistant','user']);
  assert.equal(out.messages[0].content,base.system+SUMMARY_WRAPPER.replace('{summary}',()=>f.version.effectiveText));
  assert.deepEqual(out.messages.slice(1,3),s.examples);assert.equal(out.messages[3].content,s.turns[2].userMessage);
  assert.equal(out.messages[4].content,s.turns[2].assistant);assert.equal(out.messages.at(-1).content,base.user);
  assert.equal(out.messages.some(m=>m.content.includes('原始回答 1')),false);
  assert.deepEqual(out.evidence.selectedRequestIds,['request-3']);assert.equal(out.evidence.messagesHash,digest(out.messages));
  assert.equal(out.evidence.policy.runtimeReady,false);assert.equal(out.evidence.policy.billedCalls,0);
});

for(const [m1,m2,expected,mode] of [
  [false,false,[1,2,3],'full-history'],[true,false,[3],'history-window'],
  [false,true,[3],'rolling-summary'],[true,true,[3],'rolling-summary'],
])test('M1='+m1+' / M2='+m2+' 组合保留明确历史策略',()=>{
  const f=prepared(),out=assemble(f.s,f.state,grants(m2,m1));
  assert.deepEqual(out.evidence.selection.selectedTurns,expected);assert.equal(out.evidence.policy.mode,mode);
  assert.equal(out.messages[0].content.includes('【历史摘要 · 数据】'),m2);assert.equal(f.state.revision,1);
});

test('M2 尚未补齐必须阻断；M2 禁用恢复 M1/全历史，初轮不会凭空创建摘要',()=>{
  const s=session(),state=initialAuthoringSummary(s);rejects(()=>assemble(s,state),'SUMMARY_UPDATE_REQUIRED');
  assert.equal(assemble(s,state,grants(false)).evidence.policy.mode,'history-window');
  for(const count of [0,1]) {const s=session(count),out=assemble(s,initialAuthoringSummary(s));assert.equal(out.messages[0].content,base.system);assert.equal(out.messages.length,2+count*2);}
});

test('M2 开启须独立模型，M3 未接执行时明确阻断；M1 初始快照不叠加到 M2',()=>{
  const missing=session(0,{selected:false});rejects(()=>assemble(missing,initialAuthoringSummary(missing)),'SUMMARY_MODEL_REQUIRED');
  const f=prepared();rejects(()=>assemble(f.s,f.state,grants(true,true,true)),'AUTHORING_MEMORY_EXECUTION_UNAVAILABLE');
  const withOpening={turns:1,includeInitialCharacter:true};
  assert.equal(assemble(f.s,f.state,grants(),withOpening).evidence.selection.initialSnapshotAdded,false);
  const m1=assemble(f.s,f.state,grants(false),withOpening);assert.equal(m1.evidence.selection.initialSnapshotAdded,true);
  assert.ok(m1.messages[1].content.includes('开局角色资料（原始快照）\n原始开局'));
  assert.equal(f.s.turns[2].userMessage.includes('原始开局'),false);
});

test('上下文绑定授权/设置/摘要 revision，旧策略不能发送新组合',()=>{
  const f=prepared(),old=summaryContextPolicy(f.s,f.state,{authorization:grants(),historyConfig:config});
  rejects(()=>assembleSummaryContext({session:f.s,state:f.state,authorization:grants(false),historyConfig:config,base,expectedPolicyRevision:old.revision}),'SUMMARY_POLICY_CONFLICT');
  const revised=edit(f.s,f.state,'新摘要').state;
  rejects(()=>assembleSummaryContext({session:f.s,state:revised,authorization:grants(),historyConfig:config,base,expectedPolicyRevision:old.revision}),'SUMMARY_POLICY_CONFLICT');
});

test('回放使用封存摘要与模型，后续人工修订、新回合和模型更换不污染旧请求',()=>{
  const f=prepared(),out=assemble(f.s,f.state),revised=edit(f.s,f.state,'未来摘要').state;
  addTurn(f.s,'未来输入','未来回答');choose(f.s,'summary','other');
  assert.equal(revised.versions.at(-1).effectiveText,'未来摘要');
  assert.deepEqual(replaySummaryContext(f.s,base,out.evidence),out.messages);
});

test('回放拒绝换作品版本、改原始历史、改已携带前后词、消息 hash 或作者输入',()=>{
  const f=prepared(),out=assemble(f.s,f.state);
  for(const mutate of [s=>s.binding.versionId='version-'+sha256('other'),s=>s.turns[0].user='更改源文',s=>s.turns[2].userMessage='重复前置']) {
    const s=clone(f.s);mutate(s);structuredError(()=>replaySummaryContext(s,base,out.evidence));
  }
  rejects(()=>replaySummaryContext(f.s,{...base,user:'另一份输入'},out.evidence),'SUMMARY_CONTEXT_EVIDENCE_INVALID');
  const evidence=seal({...out.evidence,messagesHash:sha256('fake')});
  rejects(()=>replaySummaryContext(f.s,base,evidence),'SUMMARY_CONTEXT_EVIDENCE_INVALID');
});

test('非法预设 role、未知 base 字段与装配超限拒绝，不静默丢字段或截断',()=>{
  const f=prepared();f.s.examples=[{role:'system',content:'嵌入'},{role:'assistant',content:'回答'}];
  rejects(()=>assemble(f.s,f.state),'SUMMARY_EXAMPLES_INVALID');f.s.examples=[];
  rejects(()=>assemble(f.s,f.state,grants(),config,{...base,providerUrl:'untrusted'}),'AUTHORING_UNKNOWN_FIELD');
  rejects(()=>assemble(f.s,f.state,grants(),config,{...base,user:'字'.repeat(2000001)}),'SUMMARY_CONTEXT_CAPACITY');
});

test('通用发布作品的真实离线存储链路：独立摘要请求回执可恢复，捕获 single-system 消息',async t=>{
  const dir=await mkdtemp(join(root,'integration-')),store=await AuthoringStore.open(join(dir,'works'));t.after(()=>store.close());
  const document=blankDocument();document.metadata.name='海港档案';document.metadata.audience='male';
  document.sources.system=base.system;document.sources.prefix='前置';document.sources.suffix='后置';
  const created=await store.create({operationId:'create-work',expectedRevision:null,document});
  const published=await store.publish(created.workId,{operationId:'publish-work',expectedRevision:0});
  const replies=Object.fromEntries(['one','two','three','summary-capture'].map((id,i)=>[id,{status:'completed',text:i===3?'海港承诺仍未兑现。':'完整回答 '+i+'\n<details>卡内记忆：第'+i+'轮🐟</details>',finishReason:'stop'}]));
  let provider=await OfflineFixtureProvider.open(join(dir,'provider'),{replies});t.after(()=>provider.close());
  const control=new OfflineModelControl(makeCatalog()),host=await OfflineSessionHost.open({directory:join(dir,'sessions'),provider,modelControl:control,resolveRuntime:ref=>openPublishedRuntime(store,ref)});t.after(()=>host.close());
  const ref={workId:created.workId,versionId:published.result.versionId};
  const id=(await host.create({operationId:'create-session',expectedRevision:null,reference:ref,openingText:'我要前往海港',presetId:null})).session.id;
  for(const purpose of ['story','summary']) {
    const s=(await host.read(id)).session,c=control.catalog();
    await host.configureModel({sessionId:id,operationId:'configure-'+purpose,expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,
      choice:{catalogRevision:c.revision,selectionId:purpose,selectionRevision:sha256(purpose),parameters:{maxOutputTokens:4096}}});
  }
  assert.equal(provider.dispatchCount,0);
  for(const [i,operationId] of ['one','two','three'].entries()) {
    const s=(await host.read(id)).session;
    const sent=await host.send({sessionId:id,operationId,expectedRevision:s.revision,expectedModelRevision:s.models.revision,input:i===0?'我要前往海港':'继续 '+i});
    assert.equal(sent.operation.state,'completed');
  }
  const s=(await host.read(id)).session,before=canonical(s),empty=initialAuthoringSummary(s),request=task(s,empty,'summary-capture');
  assert.equal(provider.dispatchCount,3);
  const actual=await provider.dispatch(request);const done=commit(s,empty,inspectSummaryResult(request,actual));
  assert.equal(provider.dispatchCount,4);assert.equal(canonical((await host.read(id)).session),before);
  await provider.close();provider=await OfflineFixtureProvider.open(join(dir,'provider'),{replies});
  const recovered=await provider.lookup(request);assert.deepEqual(recovered,actual);assert.equal(provider.dispatchCount,0);
  const stateFile=join(dir,'summary-snapshot.json');await writeFile(stateFile,canonical(done.state));
  const loaded=JSON.parse(await readFile(stateFile,'utf8'));validateAuthoringSummary(loaded,s);
  const out=assemble(s,loaded);assert.equal(out.messages[0].content,base.system+SUMMARY_WRAPPER.replace('{summary}',done.version.effectiveText));
  assert.deepEqual(replaySummaryContext(s,base,out.evidence),out.messages);
  assert.equal(request.messages[1].content.includes('前置'),false);
  const captured={kind:'offline-fixture-only',runtimeReady:false,realProviderCalls:0,reference:ref,sessionId:id,
    sourceRequest:request,sourceReceipt:actual,summarySnapshot:loaded,storyAssembly:out,providerLookupDispatches:provider.dispatchCount};
  await writeFile(join(dir,'captured-summary-and-context.json'),JSON.stringify(captured,null,2));
  const archive=fileURLToPath(new URL('../.rpg04-work/authoring-b6h/',import.meta.url));await mkdir(archive,{recursive:true});
  await writeFile(join(archive,'latest-capture.json'),JSON.stringify({directory:dir,evidence:join(dir,'captured-summary-and-context.json'),sha256:digest(captured)},null,2));
});


test('摘要模型切换后再选回原值也不能使旧任务重新有效',()=>{
  const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty));
  choose(s,'summary','other');choose(s,'summary','summary');
  rejects(()=>commit(s,empty,candidate),'SUMMARY_MODEL_CHANGED');
});

test('压缩及人工版本持久化往返保持全部原始证据，未来变化不改旧摘要',()=>{
  const s=session(),empty=initialAuthoringSummary(s),candidate=result(task(s,empty),'🐟'.repeat(10001));
  const compression=result(createSummaryCompression(candidate,'compress'),'压缩后仍有未兑现承诺。');
  const done=commit(s,empty,candidate,compression),revised=edit(s,done.state,'人工补充：时间未知。').state;
  const restored=JSON.parse(canonical(revised));validateAuthoringSummary(restored,s);
  assert.deepEqual(restored,revised);assert.equal(restored.versions[0].result.candidate.receipt.result.text,'🐟'.repeat(10001));
  assert.deepEqual(replaySummaryContext(s,base,JSON.parse(canonical(assemble(s,restored).evidence))),assemble(s,restored).messages);
});
