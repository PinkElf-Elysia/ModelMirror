import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {canonical,sha256} from './contracts.mjs';
import {OfflineModelControl,emptyModelState,validateModelState} from './model-control.mjs';
import {initialAuthoringSummary,createSummaryTask,inspectSummaryResult,commitSummaryUpdate} from './summary-state.mjs';
import {summaryContextPolicy,assembleSummaryContext} from './summary-context.mjs';
import {MEMORY_INSTRUCTION,initialAuthoringMemory,validateAuthoringMemory,memoryUpdateSource,createMemoryTask,inspectMemoryResult,commitMemoryUpdate,reviseMemory} from './memory-state.mjs';
import {MEMORY_WRAPPER,memoryContextPolicy,assembleMemoryContext,replayMemoryContext} from './memory-context.mjs';
const digest=v=>sha256(canonical(v)),clone=v=>structuredClone(v),seal=v=>{const {sha256:ignored,...body}=v;return {...body,sha256:digest(body)};};
const bad=(f,code)=>assert.throws(f,e=>e.code===code);
const authorization={enabled:true,revision:sha256('memory grant')};
const grants=(m1=false,m2=false,m3=true)=>({history:{enabled:m1,revision:sha256('history '+m1)},summary:{enabled:m2,revision:sha256('summary '+m2)},memory:{...authorization,enabled:m3}});
const config={turns:1,includeInitialCharacter:false};
const catalog=(contextLength=128000)=>({catalogId:'memory-fixture',models:['story','summary','memory','other'].map(id=>({selectionId:id,selectionRevision:sha256(id),modelId:'fixture/'+id,name:id,available:true,contextLength,maxOutputTokens:16000,
 parameterPolicy:{kind:'editable',fields:{maxOutputTokens:{minimum:1,maximum:16000,defaultValue:4096,required:true,wireName:'max_tokens'}},exclusive:[]}}))});
function choose(s,purpose,route=purpose,control=new OfflineModelControl(catalog())){
 const c=control.catalog(),choice=route===null?null:{catalogRevision:c.revision,selectionId:route,selectionRevision:sha256(route),parameters:{maxOutputTokens:4096}},selected=choice===null?null:control.select(choice);
 const intent={sessionId:s.id,operationId:'model-'+s.models.revision,expectedRevision:s.revision,expectedModelRevision:s.models.revision,purpose,choice};
 s.models.changes.push({revision:++s.models.revision,intent,intentHash:digest(intent),choice:selected});s.models.current[purpose]=selected;s.revision++;
 validateModelState(s.models,s.id,s.revision);return s;
}
function addTurn(s,user,assistant){const n=s.turns.length+1;user??='原始玩家 '+n;assistant??='回答 '+n+'\n<details>卡内记忆：海港</details>';
 s.turns.push({id:'turn-'+n,number:n,operationId:'story-'+n,requestId:'request-'+n,complete:true,user,userMessage:'前置\n'+user+'\n后置',assistant,rawHash:sha256(assistant),messagesHash:sha256('messages '+n)});s.revision+=3;return s;}
function session(n=1,selected=true){const id='session-'+sha256('unrelated custom work');const s={id,revision:0,binding:{format:'modelmirror.rpg.authored-session-binding/1',sessionId:id,workId:'work-'+sha256('custom'),versionId:'version-'+sha256('v1'),resourceHash:sha256('resources'),runtimeHash:sha256('runtime'),openingHash:sha256('开局'),openingText:'开局'},create:{operationId:'create'},models:emptyModelState(),examples:[],turns:[],operations:[]};
 choose(s,'story');choose(s,'summary');if(selected)choose(s,'memory');while(s.turns.length<n)addTurn(s);return s;}
const value=(overrides={})=>({name:'海港守卫',keywords:['守卫','guard'],match:'any',roles:['user','assistant'],content:'守卫承诺明早在海港见面，尚未兑现。',enabled:true,...overrides});
const create=(overrides={})=>{const {enabled,...v}=value(overrides);return {type:'create',...v,sourceTurns:[1]};};
function task(s,m,operationId='memory-task'){return createMemoryTask(s,m,{operationId,expectedRevision:s.revision,expectedMemoryRevision:m.revision,expectedModelRevision:s.models.revision,authorization});}
const receipt=(request,text,status='completed',finishReason='stop')=>seal({kind:'offline-fixture',requestId:request.id,requestHash:digest(request),result:{status,text,finishReason}});
const candidate=(t,operations=[create()])=>inspectMemoryResult(t,receipt(t,JSON.stringify({operations})));
const commit=(s,m,c,a=authorization)=>commitMemoryUpdate(s,m,{candidate:c,authorization:a});
const edit=(s,m,change,operationId='edit-'+m.revision)=>reviseMemory(s,m,{operationId,expectedRevision:s.revision,expectedMemoryRevision:m.revision,change});
function prepared(s=session(),ops=[create()]){const initial=initialAuthoringMemory(s),request=task(s,initial),c=candidate(request,ops);return {s,initial,request,c,...commit(s,initial,c)};}
function summary(s){let state=initialAuthoringSummary(s);if(s.turns.length>1){const request=createSummaryTask(s,state,{operationId:'summary-task',expectedSummaryRevision:0,expectedModelRevision:s.models.revision,authorization:grants(false,true).summary});state=commitSummaryUpdate(s,state,{candidate:inspectSummaryResult(request,receipt(request,'历史里有灯塔与一份尚未兑现的承诺。')),compression:null,authorization:grants(false,true).summary}).state;}return state;}
function context(s,m,{auth=grants(),input='守卫，早安',base={system:'作者原 system\r\n保持原字节',user:'前置\n守卫，早安\n后置'},summ=initialAuthoringSummary(s)}={}){
 const policy=memoryContextPolicy(s,summ,m,{authorization:auth,historyConfig:config});return assembleMemoryContext({session:s,summary:summ,memory:m,authorization:auth,historyConfig:config,base,input,expectedPolicyRevision:policy.revision});}

test('M3 instruction and historical-data wrapper match frozen plugin strings without importing card hosts',async()=>{
 const source=await readFile(new URL('../studio/memory-task-state.mjs',import.meta.url),'utf8');
 const literal=source.match(/export const MEMORY_INSTRUCTION=("(?:[^"\\]|\\.)*");/)[1];assert.equal(MEMORY_INSTRUCTION,JSON.parse(literal));
 const assembly=await readFile(new URL('../studio/memory-assembly.mjs',import.meta.url),'utf8');
 // The wrapper is a trusted first-party literal, read as text, never evaluated.
 const wrapper=assembly.match(/export const MEMORY_WRAPPER='([^']*)';/)[1].replaceAll('\\n','\n');assert.equal(MEMORY_WRAPPER,wrapper);
});
test('empty session has no automatic task and does not silently select a memory model',()=>{const s=session(0,false),m=initialAuthoringMemory(s);assert.equal(task(s,m).kind,'no-update');assert.equal(s.models.current.memory,null);assert.equal(memoryUpdateSource(s,m).needsUpdate,false);addTurn(s);bad(()=>task(s,m),'MEMORY_MODEL_REQUIRED');});
test('task requires explicit authorization and current session/memory/model revisions',()=>{const s=session(),m=initialAuthoringMemory(s),v={operationId:'check',expectedRevision:s.revision,expectedMemoryRevision:0,expectedModelRevision:s.models.revision,authorization};
 for(const [key,val,code]of [['expectedRevision',0,'MEMORY_SESSION_CONFLICT'],['expectedMemoryRevision',1,'MEMORY_REVISION_CONFLICT'],['expectedModelRevision',0,'MODEL_REVISION_CONFLICT'],['authorization',{...authorization,enabled:false},'MEMORY_AUTHORIZATION_REQUIRED']])bad(()=>createMemoryTask(s,m,{...v,[key]:val}),code);
});
test('first update includes every raw turn including latest and embedded card memory; excludes wrappers and M2 text',()=>{const s=session(4),m=initialAuthoringMemory(s),t=task(s,m),payload=JSON.parse(t.messages[1].content);
 assert.deepEqual(payload.turns,s.turns.map(t=>({number:t.number,user:t.user,assistant:t.assistant})));assert.equal(payload.turns.length,4);assert.ok(payload.turns[3].assistant.includes('卡内记忆'));assert.ok(!t.messages[1].content.includes('前置'));assert.equal(t.modelChoice.entry.modelId,'fixture/memory');assert.equal(t.messages.filter(m=>m.role==='system').length,1);assert.equal(t.messagesHash,digest(t.messages));
});
test('manual entries are protected; edit/delete/restore retain original model output and source revisions',()=>{const f=prepared(),e=f.state.entries[0],raw=f.c.receipt.result.text;
 let m=edit(f.s,f.state,{action:'update',id:e.id,baseVersion:1,value:value({content:'人工核实：约定改为后天。'})}).state;assert.equal(m.entries[0].protected,true);assert.equal(m.entries[0].revisions.length,2);
 m=edit(f.s,m,{action:'delete',id:e.id,baseVersion:2}).state;assert.equal(m.entries[0].deleted,true);
 m=edit(f.s,m,{action:'restore',id:e.id,baseVersion:3,version:1}).state;assert.equal(m.entries[0].deleted,false);assert.equal(m.entries[0].protected,true);assert.equal(m.entries[0].value.content,e.value.content);assert.equal(m.versions[0].result.receipt.result.text,raw);validateAuthoringMemory(m,f.s);
 const manual=edit(f.s,m,{action:'create',value:value({name:'同名的另一位守卫'})}).state;assert.equal(manual.entries[1].origin,'manual');assert.equal(manual.entries[1].protected,true);
});
test('next task includes all existing enabled and disabled entries with explicit protection and only unprocessed turns',()=>{const f=prepared();let m=edit(f.s,f.state,{action:'create',value:value({name:'停用参考',enabled:false})}).state;addTurn(f.s,'下一回合','完整新原文');const t=task(f.s,m,'later');
 assert.equal(t.source.entries.length,2);assert.ok(t.source.entries.some(e=>!e.enabled&&e.protected));assert.deepEqual(t.source.turns,[{number:2,user:'下一回合',assistant:'完整新原文'}]);assert.equal(t.source.from,2);
});
test('legal no-change result advances coverage, has exact raw receipt, and replay never creates a second version',()=>{const s=session(3),m=initialAuthoringMemory(s),t=task(s,m),c=candidate(t,[]),saved=commit(s,m,c);assert.equal(saved.state.processedThrough,3);assert.deepEqual(saved.state.entries,[]);assert.equal(saved.state.revision,1);assert.equal(commit(s,saved.state,c).replayed,true);assert.equal(task(s,saved.state,'current').kind,'no-update');validateAuthoringMemory(saved.state,s);
});
for(const [name,raw,finish,reason]of [
 ['empty','','stop','MEMORY_OUTPUT_EMPTY'],['truncated','{"operations":[]}','length','MEMORY_OUTPUT_TRUNCATED'],['markdown','```json\n{"operations":[]}\n```','stop','MEMORY_PROPOSAL_INVALID'],['duplicate key','{"operations":[],"operati\\u006fns":[]}','stop','MEMORY_PROPOSAL_INVALID'],['trailing','{"operations":[]} text','stop','MEMORY_PROPOSAL_INVALID'],['unknown field','{"operations":[],"permission":true}','stop','MEMORY_PROPOSAL_INVALID'],['nonfinite','{"operations":[1e999]}','stop','MEMORY_PROPOSAL_INVALID'],
])test('reject '+name+' and retain unmodified output without advancing state',()=>{const s=session(),m=initialAuthoringMemory(s),before=canonical(m),t=task(s,m),c=inspectMemoryResult(t,receipt(t,raw,'completed',finish));assert.equal(c.status,'rejected');assert.equal(c.reason,reason);assert.equal(c.receipt.result.text,raw);bad(()=>commit(s,m,c),reason);assert.equal(canonical(m),before);});
test('one invalid reference rejects an otherwise valid full batch without partial commit',()=>{const s=session(),m=initialAuthoringMemory(s),before=canonical(m),t=task(s,m),c=candidate(t,[create(),create({name:'second'})]);c.operations[1].sourceTurns=[2];const forged=inspectMemoryResult(t,receipt(t,JSON.stringify({operations:c.operations})));assert.equal(forged.status,'rejected');bad(()=>commit(s,m,forged),'MEMORY_PROPOSAL_INVALID');assert.equal(canonical(m),before);});
test('model cannot change protected entries, delete, unprotect, or inject enabling fields',()=>{const s=session(),initial=initialAuthoringMemory(s),m=edit(s,initial,{action:'create',value:value()}).state,e=m.entries[0],t=task(s,m);
 const update={...create(),type:'update',id:e.id,baseVersion:1};bad(()=>commit(s,m,candidate(t,[create(),update])),'MEMORY_PROPOSAL_FORBIDDEN');
 for(const op of [{type:'delete',id:e.id,baseVersion:1},{...update,protected:false},{...update,enabled:true}])assert.equal(candidate(t,[op]).status,'rejected');assert.equal(m.entries.length,1);
});
test('manual unprotect permits later updates but model cannot reactivate disabled entries',()=>{const f=prepared(),e=f.state.entries[0];let m=edit(f.s,f.state,{action:'update',id:e.id,baseVersion:1,value:value({enabled:false})}).state;m=edit(f.s,m,{action:'protect',id:e.id,baseVersion:2,protected:false}).state;addTurn(f.s);
 const op={...create({content:'新增事实'}),type:'update',id:e.id,baseVersion:3,sourceTurns:[2]};m=commit(f.s,m,candidate(task(f.s,m,'next'),[op])).state;assert.equal(m.entries[0].value.enabled,false);assert.equal(m.entries[0].protected,false);
});
test('duplicate update and stale entry versions reject atomically',()=>{const f=prepared(),e=f.state.entries[0];addTurn(f.s);const t=task(f.s,f.state,'next'),op={...create(),type:'update',id:e.id,baseVersion:1,sourceTurns:[2]};
 bad(()=>commit(f.s,f.state,candidate(t,[op,op])),'MEMORY_PROPOSAL_FORBIDDEN');bad(()=>commit(f.s,f.state,candidate(t,[{...op,baseVersion:2}])),'MEMORY_PROPOSAL_FORBIDDEN');assert.equal(f.state.entries[0].version,1);
});
test('capacity and Unicode limits block complete proposals; no truncation or compression',()=>{const s=session(),m=initialAuthoringMemory(s),t=task(s,m);assert.equal(candidate(t,[create({content:'🐟'.repeat(2001)})]).status,'rejected');
 const good=commit(s,m,candidate(t,[create({content:'🐟'.repeat(2000)})])).state;assert.equal([...good.entries[0].value.content].length,2000);
 bad(()=>commit(s,m,candidate(t,Array.from({length:31},(_,i)=>create({name:'entry'+i,content:'文'.repeat(2000)})))),'MEMORY_CAPACITY_EXCEEDED');
 const full=commit(s,m,candidate(t,Array.from({length:100},(_,i)=>create({name:'entry'+i})))).state;addTurn(s);bad(()=>task(s,full,'full'),'MEMORY_CAPACITY_FULL');
});
test('insufficient model input capacity blocks before a request exists',()=>{const s=session();choose(s,'memory','memory',new OfflineModelControl(catalog(5000)));bad(()=>task(s,initialAuthoringMemory(s)),'MODEL_CONTEXT_CAPACITY');});
test('model switch is independent; memory switch away and back invalidates pending task',()=>{const s=session(),m=initialAuthoringMemory(s),c=candidate(task(s,m));choose(s,'story','other');choose(s,'summary','other');assert.equal(commit(s,m,c).state.processedThrough,1);choose(s,'memory','other');choose(s,'memory','memory');bad(()=>commit(s,m,c),'MEMORY_MODEL_CHANGED');});
test('revocation and regrant invalidate delayed result; manual changes and new raw turns invalidate source',()=>{const s=session(),m=initialAuthoringMemory(s),c=candidate(task(s,m));
 for(const auth of [{...authorization,enabled:false},{enabled:true,revision:sha256('regrant')}])bad(()=>commit(s,m,c,auth),'MEMORY_AUTHORIZATION_CHANGED');
 const revised=edit(s,m,{action:'create',value:value()}).state;bad(()=>commit(s,revised,c),'MEMORY_REVISION_CONFLICT');addTurn(s);bad(()=>commit(s,m,c),'MEMORY_SOURCE_CHANGED');
});
test('manual original operation is idempotent; collisions with story/model/summary and altered payload rejected',()=>{const f=prepared(),input={operationId:'manual',expectedRevision:f.s.revision,expectedMemoryRevision:f.state.revision,change:{action:'create',value:value()}};const result=reviseMemory(f.s,f.state,input);assert.equal(reviseMemory(f.s,result.state,input).replayed,true);
 bad(()=>reviseMemory(f.s,result.state,{...input,change:{action:'create',value:value({name:'different'})}}),'MEMORY_OPERATION_COLLISION');bad(()=>edit(f.s,f.state,input.change,'story-1'),'MEMORY_OPERATION_COLLISION');bad(()=>task(f.s,f.state,'model-0'),'MEMORY_OPERATION_COLLISION');
 f.s.summary={snapshot:{versions:[{operationId:'m2-op'}]}};bad(()=>task(f.s,f.state,'m2-op'),'MEMORY_OPERATION_COLLISION');
});
test('resealed current entries cannot override original proposals, histories or frozen binding',()=>{const f=prepared(),changed=clone(f.state);changed.entries[0].value.content='伪造事实';changed.entries[0].revisions[0].value.content='伪造事实';assert.throws(()=>validateAuthoringMemory(seal(changed),f.s));
 const other=clone(f.s);other.binding.workId='work-'+sha256('other');bad(()=>validateAuthoringMemory(f.state,other),'MEMORY_STATE_INVALID');
 const wrong=clone(f.c.receipt);wrong.requestHash=sha256('wrong');bad(()=>inspectMemoryResult(f.request,seal(wrong)),'MEMORY_RECEIPT_INVALID');
});
test('case-insensitive aliases, same-name independent entries, across-message AND and role filters',()=>{const s=session(0);addTurn(s,'Alpha 进港','NPC称自己叫贝塔');let m=initialAuthoringMemory(s);
 for(const v of [value({name:'守卫',keywords:['alpha','贝塔'],match:'all'}),value({name:'守卫',keywords:['Alpha'],roles:['assistant']}),value({keywords:['GUARD'],roles:['user']})])m=edit(s,m,{action:'create',value:v}).state;
 const a=context(s,m,{input:'Guard 你好'});assert.equal(a.evidence.recall.results.length,2);assert.equal(a.evidence.recall.results[0].current,true);assert.equal(a.evidence.recall.results[1].hits.length,2);
 assert.equal(a.messages.filter(m=>m.role==='system').length,1);assert.ok(a.messages[0].content.includes(MEMORY_WRAPPER.split('{entries}')[0]));assert.ok(!a.messages[0].content.includes('memory-'));
});
test('recall scans raw input plus ten full turns independently of M1; does not scan wrappers, summary, or injected memory',()=>{const s=session(0);addTurn(s,'早期密码珊瑚','早期内容');for(let i=0;i<9;i++)addTurn(s,'日常','完整原文');let m=initialAuthoringMemory(s);
 for(const v of [value({keywords:['珊瑚']}),value({name:'wrapper only',keywords:['前置']}),value({name:'recursive only',keywords:['守卫']}),value({name:'summary only',keywords:['灯塔']})])m=edit(s,m,{action:'create',value:v}).state;
 const a=context(s,m,{auth:grants(true,false,true),input:'继续'});assert.equal(a.evidence.recall.results.length,1);assert.deepEqual(a.evidence.baseEvidence.selection.selectedTurns,[10]);assert.ok(!a.messages.slice(1).some(m=>m.content.includes('珊瑚')));
 const b=context(s,m,{auth:grants(true,true,true),input:'继续',summ:summary(s)});assert.equal(b.evidence.recall.results.length,1);
 addTurn(s,'第十一回合','无关');assert.equal(context(s,m,{auth:grants(true),input:'继续'}).evidence.recall.results.length,0);
});
test('capacity chooses whole entries deterministically by current hit, latest raw history and stable ID',()=>{const s=session(0);addTurn(s,'old','earlier');addTurn(s,'recent','latest');let m=initialAuthoringMemory(s);
 for(let i=0;i<6;i++)m=edit(s,m,{action:'create',value:value({name:'item'+i,keywords:[i<2?'old':i<4?'recent':'now'],content:String(i).repeat(2000)})}).state;
 const a=context(s,m,{input:'now'}),r=a.evidence.recall;assert.equal(r.bodyCharacters,8000);assert.equal(r.results.filter(x=>x.selected).length,4);assert.ok(r.results.slice(0,2).every(x=>x.current));assert.ok(r.results.slice(4).every(x=>x.reason==='body-limit'));assert.deepEqual(context(s,m,{input:'now'}),a);
});
for(const m1 of [false,true])for(const m2 of [false,true])for(const m3 of [false,true])test('history and system order M1='+m1+' M2='+m2+' M3='+m3,()=>{const f=prepared(session(3)),sm=summary(f.s),auth=grants(m1,m2,m3),base={system:'作者\r\n原文',user:'前置\n守卫\n后置'},a=context(f.s,f.state,{auth,summ:sm,input:'守卫',base});
 assert.equal(a.messages[0].role,'system');assert.ok(a.messages[0].content.startsWith(base.system));assert.equal(a.messages.at(-1).content,base.user);assert.equal(a.messages.length,2+(m1||m2?2:6));
 if(m2&&m3)assert.ok(a.messages[0].content.indexOf('【历史摘要 · 数据】')<a.messages[0].content.indexOf('【记忆宫殿·历史资料】'));
 if(!m3){const policy=summaryContextPolicy(f.s,sm,{authorization:auth,historyConfig:config});assert.deepEqual(a.messages,assembleSummaryContext({session:f.s,state:sm,authorization:auth,historyConfig:config,base,expectedPolicyRevision:policy.revision}).messages);assert.equal(a.evidence.recall,null);}
 assert.equal(a.evidence.policy.authorization.memory.enabled,m3);
});
test('nonmatching, disabled and deleted entries inject no section; enabled M3 requires selected independent model',()=>{const f=prepared();let m=edit(f.s,f.state,{action:'update',id:f.state.entries[0].id,baseVersion:1,value:value({enabled:false})}).state;assert.ok(!context(f.s,m).messages[0].content.includes('记忆宫殿'));
 m=edit(f.s,m,{action:'delete',id:m.entries[0].id,baseVersion:2}).state;assert.ok(!context(f.s,m).messages[0].content.includes('记忆宫殿'));choose(f.s,'memory',null);bad(()=>context(f.s,m),'MEMORY_MODEL_REQUIRED');
});
test('frozen request replays after future parent edits and model changes, with no future information',()=>{const f=prepared(),base={system:'作者',user:'前置\n守卫\n后置'},input='守卫',a=context(f.s,f.state,{base,input});
 addTurn(f.s,'未来玩家','未来原文');choose(f.s,'memory','other');const later=edit(f.s,f.state,{action:'update',id:f.state.entries[0].id,baseVersion:1,value:value({content:'父路线后来改写'})}).state;assert.notEqual(later.sha256,f.state.sha256);
 assert.deepEqual(replayMemoryContext(f.s,base,input,a.evidence),a.messages);assert.ok(!canonical(a.messages).includes('未来'));assert.ok(!canonical(a.messages).includes('后来'));
 const forged=clone(a.evidence);forged.recall.results[0].version++;assert.throws(()=>replayMemoryContext(f.s,base,input,seal(forged)));bad(()=>replayMemoryContext(f.s,base,'changed',a.evidence),'MEMORY_CONTEXT_EVIDENCE_INVALID');
});

test('legacy task replays unchanged and a new update echoes the exact current baseVersion',()=>{
 const f=prepared(),entry=f.state.entries[0];addTurn(f.s);
 const intent={operationId:'legacy-second',expectedRevision:f.s.revision,expectedMemoryRevision:f.state.revision,expectedModelRevision:f.s.models.revision,authorization};
 const old=createMemoryTask(f.s,f.state,intent,'modelmirror.rpg.authored-memory-task/1');
 assert.deepEqual(JSON.parse(old.messages[1].content).entries,old.source.entries);
 const update={...create({content:'守卫改约后天。'}),type:'update',id:entry.id,baseVersion:1,sourceTurns:[2]};
 const second=commit(f.s,f.state,candidate(old,[update])).state;
 addTurn(f.s);const current=task(f.s,second,'third');
 const payload=JSON.parse(current.messages[1].content);
 assert.equal(current.format,'modelmirror.rpg.authored-memory-task/2');
 assert.equal(current.messages[0].content,old.messages[0].content);
 assert.equal(current.source.entries[0].version,2);
 assert.equal(payload.entries[0].id,entry.id);
 assert.equal(payload.entries[0].baseVersion,2);
 assert.equal(Object.hasOwn(payload.entries[0],'version'),false);
 const stale={...update,sourceTurns:[3]};
 bad(()=>commit(f.s,second,candidate(current,[{...create(),sourceTurns:[3]},stale])),'MEMORY_PROPOSAL_FORBIDDEN');
 assert.equal(second.processedThrough,2);assert.equal(second.entries.length,1);
 const latest=commit(f.s,second,candidate(current,[{...stale,baseVersion:payload.entries[0].baseVersion}])).state;
 assert.equal(latest.processedThrough,3);assert.equal(latest.entries[0].version,3);
 validateAuthoringMemory(latest,f.s);
});
