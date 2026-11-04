import test from 'node:test';
import assert from 'node:assert/strict';
import {memoryRejectionMessage,createJointClient,jointTaskIntent,pluginIntent,memoryEditIntent,requirementsEditIntent,storyProblem} from './joint-client.mjs';
const sid='session-'+'a'.repeat(64),workId='work-a',hash='b'.repeat(64),clone=v=>structuredClone(v);
const storage=()=>{const s={};Object.defineProperties(s,{getItem:{value:k=>s[k]??null},setItem:{value:(k,v)=>{s[k]=v;}},removeItem:{value:k=>{delete s[k];}}});return s;};
const fixtureView=()=>({session:{id:sid,binding:{workId},revision:5,runs:[],models:{revision:3},summary:{snapshot:{revision:1}},memory:{snapshot:{revision:2,versions:[]}}},effectivePolicy:{revision:hash},busy:false,pending:false});
const run=(body,state='running')=>({operationId:body.operationId,intent:clone(body),state});
const failed=(code,httpStatus)=>Object.assign(Error(code),{code,...(httpStatus?{httpStatus}:{})});

test('失败剧情投影从可信回执区分未派发、已派发、未知和离线样例，保留原文',()=>{
 const old={state:'failed',operationId:'first',intent:{input:'中文\n原输入'},request:{id:'request-first'},diagnostic:null,
  receipt:{kind:'control-plane',control:{dispatched:false,error:'rpg_authoring_preflight_failed'},result:{text:''}}};
 const before=clone(old),p=storyProblem(old);
 assert.equal(p.dispatched,false);assert.equal(p.code,'rpg_authoring_preflight_failed');assert.equal(p.input,'中文\n原输入');assert.deepEqual(old,before);
 old.receipt.control.dispatched=true;old.receipt.control.error='rpg_authoring_upstream_http';old.receipt.result.text='保留的部分回复';
 assert.equal(storyProblem(old).dispatched,true);assert.equal(storyProblem(old).text,'保留的部分回复');
 const fixture={...old,receipt:{kind:'offline-fixture',result:{text:''}}};
 assert.equal(storyProblem(fixture).dispatched,null);assert.equal(storyProblem(fixture).fixture,true);
 assert.equal(storyProblem({state:'unknown',operationId:'unknown'}).dispatched,null);
 assert.equal(storyProblem({state:'not_dispatched',operationId:'blocked'}).dispatched,false);
 for(const state of ['completed','prepared','dispatching'])assert.equal(storyProblem({state}),null);
 assert.equal(storyProblem(null),null);
});

test('真实拒绝状态来自回执，旧 403 和无子类型的回执不能被解释为内容拦截',()=>{
 const control={status:'failed',dispatched:true,httpStatus:403,error:'rpg_authoring_upstream_http'};
 const operation={state:'failed',operationId:'denied',receipt:{kind:'control-plane',control}};
 const old=storyProblem(operation);
 assert.equal(old.httpStatus,403);
 assert.match(old.forbiddenReason,/没有记录具体原因/);
 for(const [code,reason] of [['permission_denied','权限'],['content_policy','内容策略'],['refusal','模型拒绝'],['guardrail','安全规则']]){
  control.error='rpg_authoring_upstream_'+code;
  assert.ok(storyProblem(operation).forbiddenReason.includes(reason));
 }
 for(const changed of [{httpStatus:401},{httpStatus:'403'},{dispatched:false},{status:'unknown'}]){
  assert.equal(storyProblem({...operation,receipt:{kind:'control-plane',control:{...control,...changed}}}).forbiddenReason,null);
 }
 assert.equal(storyProblem({...operation,state:'unknown'}).forbiddenReason,null);
 assert.equal(storyProblem({...operation,receipt:{kind:'offline-fixture',control}}).httpStatus,null);
});

test('明确预检失败是可保存的终态；刷新保留失败详情，既不恢复也不重新派发',async()=>{
 const v=fixtureView(),body=jointTaskIntent(v,'failed-send','保留输入');
 const operation={state:'failed',operationId:body.operationId,intent:body,receipt:{kind:'control-plane',control:{dispatched:false,error:'rpg_authoring_dns_failed'},result:{text:''}}};
 v.session.runs=[run(body,'paused')];v.session.operations=[operation];
 const f=fixture(p=>p.endsWith('/send')?{sessionId:sid,run:v.session.runs[0],operation}:v);
 const result=await f.client.submit('send',body);assert.equal(storyProblem(result.operation).code,'rpg_authoring_dns_failed');
 assert.equal(f.client.pending().length,0);
 for(let i=0;i<2;i++)assert.deepEqual(storyProblem((await f.client.view()).session.operations[0]),storyProblem(operation));
 assert.equal(f.calls.filter(c=>c.path.endsWith('/send')).length,1);
 assert.equal(f.calls.filter(c=>c.path.endsWith('/recover')).length,0);
});
const rejects=(p,code)=>assert.rejects(p,e=>e.code===code);
function fixture(api){const s=storage(),calls=[];const client=createJointClient({storage:s,sessionId:sid,workId,api:async(p,b)=>{calls.push({path:p,body:clone(b)});return api(p,b);}});return {s,calls,client};}

test('story completed while background running keeps parent intent; final observation releases it',async()=>{
 const v=fixtureView(),body=jointTaskIntent(v,'send','原始输入'),f=fixture(()=>({sessionId:sid,run:run(body),operation:{state:'completed'}}));
 await f.client.submit('send',body);assert.equal(f.client.pending().length,1);
 v.session.runs=[run(body,'unknown')];f.client.observe(v);assert.equal(f.client.pending().length,1);
 v.session.runs=[run(body,'completed')];f.client.observe(v);assert.equal(f.client.pending().length,0);assert.equal(f.calls.length,1);
});
test('write-ahead failure prevents request; unknown network response retains original ID',async()=>{
 const v=fixtureView(),body=jointTaskIntent(v,'send','测试'),f=fixture(()=>{throw failed('NETWORK_RESULT_UNKNOWN');});
 await rejects(f.client.submit('send',body),'NETWORK_RESULT_UNKNOWN');assert.equal(f.client.pending()[0].body.operationId,'send');
 await rejects(f.client.submit('send',{...body,operationId:'new'}),'JOINT_RESULT_PENDING');assert.equal(f.calls.length,1);
 let called=false;const c=createJointClient({sessionId:sid,workId,storage:{getItem:()=>null,setItem:()=>{throw Error('disk');}},api:()=>{called=true;}});
 await rejects(c.submit('send',body),'JOINT_LOCAL_STORAGE_UNAVAILABLE');assert.equal(called,false);
});
test('refresh never calls recovery or send; wrong work and altered intent do not clear journal',async()=>{
 const body=jointTaskIntent(fixtureView(),'send','输入'),v=fixtureView(),f=fixture(p=>p.endsWith('/send')?{sessionId:sid,run:run(body)}:v);
 await f.client.submit('send',body);v.session.runs=[run({...body,input:'tampered'},'completed')];
 assert.throws(()=>f.client.observe(v),e=>e.code==='JOINT_RECEIPT_MISMATCH');assert.equal(f.client.pending().length,1);
 v.session.runs=[];v.session.binding.workId='other';await rejects(f.client.view(),'JOINT_RECEIPT_MISMATCH');assert.equal(f.calls.filter(x=>/recover|send$/.test(x.path)).length,1);
});
test('original recovery after unknown checks only exact parent ID with current revisions',async()=>{
 const v=fixtureView(),body=jointTaskIntent(v,'send','输入');v.session.runs=[run(body,'unknown')];
 const f=fixture(p=>p.endsWith('/send')?Promise.reject(failed('UNKNOWN')):p.endsWith('/recover')?{sessionId:sid,run:run(body,'paused')}:v);
 await rejects(f.client.submit('send',body),'UNKNOWN');const result=await f.client.recover(f.client.pending()[0]);
 assert.equal(result.state,'settled');assert.equal(f.client.pending().length,0);assert.deepEqual(f.calls.map(x=>x.path),['/joint/send','/joint/reconcile','/joint/sessions/'+sid,'/joint/recover']);
 assert.deepEqual(f.calls.at(-1).body,{sessionId:sid,operationId:'send',expectedRevision:5,expectedSummaryRevision:1,expectedMemoryRevision:2});
});
test('running and absent requests remain pending; recovery cannot auto-dispatch',async()=>{
 const v=fixtureView(),body=jointTaskIntent(v,'send','输入'),f=fixture(p=>p.endsWith('/send')?Promise.reject(failed('UNKNOWN')):v);
 await rejects(f.client.submit('send',body),'UNKNOWN');let r=await f.client.recover(f.client.pending()[0]);assert.equal(r.state,'unknown');
 v.session.runs=[run(body)];v.busy=true;r=await f.client.recover(f.client.pending()[0]);assert.equal(r.state,'running');assert.equal(f.client.pending().length,1);
 assert.equal(f.calls.filter(x=>x.path.endsWith('/send')).length,1);assert.equal(f.calls.filter(x=>x.path.endsWith('/recover')).length,0);
});
test('terminal local validation response clears record; server storage uncertainty retains it',async()=>{
 const b=jointTaskIntent(fixtureView(),'send','输入'),f=fixture(()=>{throw failed('MEMORY_REVISION_CONFLICT',409);});await rejects(f.client.submit('send',b),'MEMORY_REVISION_CONFLICT');assert.equal(f.client.pending().length,0);
 const g=fixture(()=>{throw failed('OFFLINE_WRITE_UNKNOWN',409);});await rejects(g.client.submit('send',b),'OFFLINE_WRITE_UNKNOWN');assert.equal(g.client.pending().length,1);
});
test('unconfirmed setting can only retry same intent or discard after original no-apply check',async()=>{
 const body={sessionId:sid,operationId:'setting'},f=fixture(p=>p.endsWith('/change')?Promise.reject(failed('UNKNOWN')):{state:'not_applied',intent:body,settings:{sessionId:sid},receipt:null});
 await rejects(f.client.submit('settings',body),'UNKNOWN');const record=f.client.pending()[0];const r=await f.client.recover(record);assert.equal(r.state,'not_applied');assert.equal(f.client.pending().length,1);
 await rejects(f.client.submit('settings',{...body,action:'changed'},true),'JOINT_RESULT_PENDING');await f.client.discardConfirmed(record);assert.equal(f.client.pending().length,0);
});
test('manual edit only clears after its exact immutable version is observed',async()=>{
 const v=fixtureView(),body=memoryEditIntent(v,'edit',{action:'create',value:{name:'人工条目'}}),f=fixture(()=>{throw failed('UNKNOWN');});await rejects(f.client.submit('edit',body),'UNKNOWN');
 const version={operationId:'edit',kind:'manual',edit:{expectedRevision:5,expectedMemoryRevision:2,change:body.change}};
 v.session.memory.snapshot.versions=[{...version,edit:{...version.edit,expectedMemoryRevision:1}}];assert.throws(()=>f.client.observe(v),e=>e.code==='JOINT_RECEIPT_MISMATCH');assert.equal(f.client.pending().length,1);
 v.session.memory.snapshot.versions=[version];f.client.observe(v);assert.equal(f.client.pending().length,0);
});
test('intent builders bind all independent revisions and only the selected plugin permissions',()=>{
 const s={sessionId:sid,sessionRevision:7,modelRevision:2,historyRevision:1,summaryRevision:3,memoryRevision:4,registryRevision:5,effectivePolicy:{revision:hash},plugins:{memory:{permissions:['memory.read']}}};
 const i=pluginIntent(s,'memory','enable','grant');assert.equal(i.expectedMemoryRevision,4);assert.equal(i.expectedSummaryRevision,3);assert.deepEqual(i.permissions,['memory.read']);
 assert.equal(jointTaskIntent(fixtureView(),'update').kind,'update');assert.equal(jointTaskIntent(fixtureView(),'send','text').kind,'send');
});
test('malformed local journal cannot be silently discarded or transmitted',async()=>{
 const f=fixture(()=>{});f.s['modelmirror.authoring.joint-pending/'+sid+'/bad']='invalid';assert.throws(()=>f.client.pending(),e=>e.code==='JOINT_LOCAL_RECORD_INVALID');
 await rejects(f.client.submit('send',jointTaskIntent(fixtureView(),'send','text')),'JOINT_LOCAL_RECORD_INVALID');assert.equal(f.calls.length,0);
});


test('分支回复丢失只核对原意图，完成路由落盘后才清除待确认记录',async()=>{
 const body={sessionId:sid,operationId:'fork',turn:1,name:'分支'},checkpointId='branch-'+'c'.repeat(64),child='session-'+'d'.repeat(64);
 const result={intent:body,checkpointId,session:{id:child,binding:{workId},origin:{parentSessionId:sid,turn:1,checkpointId}}};
 const f=fixture(p=>p==='/joint/branch'?Promise.reject(failed('UNKNOWN')):{...result,state:'completed'});
 await rejects(f.client.submit('branch',body),'UNKNOWN');const r=await f.client.recover(f.client.pending()[0]);
 assert.equal(r.state,'settled');assert.equal(f.client.pending().length,0);assert.deepEqual(f.client.branches(),[{sessionId:child,parentSessionId:sid,name:'分支'}]);
 assert.deepEqual(f.calls.map(r=>r.path),['/joint/branch','/joint/branch/reconcile']);
});

test('仅封存已完成时刷新和核对不自动物化子会话；不得清除另一意图',async()=>{
 const body={sessionId:sid,operationId:'fork',turn:1,name:'分支'},f=fixture(p=>p==='/joint/branch'?Promise.reject(failed('UNKNOWN')):{intent:body,state:'checkpoint_saved',session:null});
 await rejects(f.client.submit('branch',body),'UNKNOWN');assert.equal((await f.client.recover(f.client.pending()[0])).state,'checkpoint_saved');assert.equal(f.client.pending().length,1);
 await rejects(f.client.discardConfirmed(f.client.pending()[0]),'JOINT_RESULT_PENDING');assert.equal(f.client.pending().length,1);
 const g=fixture(()=>({intent:{...body,name:'错'},checkpointId:'branch-'+'b'.repeat(64),session:{}}));await rejects(g.client.submit('branch',body),'JOINT_RECEIPT_MISMATCH');assert.equal(g.client.pending().length,1);
});


test('已确认设置后读取状态失败不会变成未知写入，也不能自动再次提交',async()=>{
 const body={sessionId:sid,operationId:'applied-settings'},f=fixture(p=>{
  if(p.endsWith('/change'))return {state:'applied',intent:body,receipt:{operationId:body.operationId},settings:{sessionId:sid}};
  throw failed('NETWORK_RESULT_UNKNOWN');
 });
 assert.equal((await f.client.submit('settings',body)).state,'applied');
 await rejects(f.client.view(),'NETWORK_RESULT_UNKNOWN');assert.equal(f.client.pending().length,0);
 assert.equal(f.calls.filter(x=>x.path.endsWith('/change')).length,1);
});


test('持续要求断线保留同一ID；只读观察与明确核对不重发',async()=>{
 const v=fixtureView();v.session.requirements={revision:0,text:'',changes:[]};const body=requirementsEditIntent(v,'requirements','小说关闭');
 const f=fixture(p=>{if(p==='/joint/requirements')throw failed('NETWORK_RESULT_UNKNOWN');return v;});
 await rejects(f.client.submit('requirements',body),'NETWORK_RESULT_UNKNOWN');assert.equal(f.client.pending().length,1);
 assert.equal((await f.client.recover(f.client.pending()[0])).state,'not_applied');assert.equal(f.client.pending().length,1);
 v.session.requirements={revision:1,text:body.text,changes:[{revision:1,intent:body}]};
 assert.equal((await f.client.recover(f.client.pending()[0])).state,'settled');assert.equal(f.client.pending().length,0);
 assert.equal(f.calls.filter(c=>c.path==='/joint/requirements').length,1);
});
test('持续要求错配回执不能清除恢复记录',async()=>{
 const v=fixtureView(),body=requirementsEditIntent(v,'req','A'),f=fixture(()=>({sessionId:sid,change:{revision:1,intent:{...body,text:'B'}}}));
 await rejects(f.client.submit('requirements',body),'JOINT_RECEIPT_MISMATCH');assert.equal(f.client.pending().length,1);
});

test('only attributes an explicit stale update to the model when the frozen entry confirms it',()=>{
 const job={state:'rejected',diagnostic:'MEMORY_PROPOSAL_FORBIDDEN',request:{source:{entries:[{id:'a',version:2,name:'灯号',protected:false}]}},
  receipt:{result:{text:JSON.stringify({operations:[{type:'update',id:'a',baseVersion:1}]})}}};
 assert.match(memoryRejectionMessage(job),/灯号.*第 1 版.*第 2 版/);
 assert.equal(memoryRejectionMessage({...job,state:'unknown'}),null);
 job.request.source.entries[0].protected=true;assert.equal(memoryRejectionMessage(job),null);
 job.receipt.result.text='invalid';assert.equal(memoryRejectionMessage(job),null);
});
