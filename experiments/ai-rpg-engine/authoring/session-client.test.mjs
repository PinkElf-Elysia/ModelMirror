import test from 'node:test';
import assert from 'node:assert/strict';
import {createSessionClient,sessionPolicyText,sessionPending,storyIntent,summaryTaskIntent,summarySettingsIntent} from './session-client.mjs';
const sid='session-'+ 'a'.repeat(64),wid='work-a';
function storage(){const s={};Object.defineProperties(s,{getItem:{value:k=>s[k]??null},setItem:{value:(k,v)=>{s[k]=v;}},removeItem:{value:k=>{delete s[k];}}});return s;}
const view=()=>({session:{id:sid,binding:{workId:wid},revision:4,models:{revision:2},summary:{snapshot:{revision:1},jobs:[]},operations:[]},effectivePolicy:{revision:'policy',mode:'rolling-summary'}});
const input=()=>storyIntent(view(),'op','原始输入');
const receipt=(b,state='completed')=>({sessionId:sid,operation:{intent:b,state}});
function make(api,s=storage()){return {c:createSessionClient({api,storage:s,sessionId:sid,workId:wid}),s};}
test('发送前保存完整原意图；成功后只清除该操作',async()=>{
 let f,calls=0;f=make(async(path,b)=>{calls++;assert.equal(f.c.pending()[0].body.input,'原始输入');assert.equal(path,'/sessions/send');return receipt(b);});
 await f.c.submit('send',input());assert.equal(calls,1);assert.deepEqual(f.c.pending(),[]);
});
test('本地写盘失败与损坏记录均阻止发送',async()=>{
 let calls=0;const s=storage();Object.freeze(s);const f=make(async()=>{calls++;},s);await assert.rejects(f.c.submit('send',input()),e=>e.code==='SESSION_LOCAL_STORAGE_UNAVAILABLE');assert.equal(calls,0);
 const t=storage();t['modelmirror.authoring.session-pending/'+sid+'/x']='bad';assert.throws(()=>sessionPending(t,sid,wid),e=>e.code==='SESSION_LOCAL_RECORD_INVALID');
});
test('断线保留记录，刷新只读；同会话不能以新编号绕过未知',async()=>{
 let calls=0;const f=make(async()=>{calls++;throw Error('network');});await assert.rejects(f.c.submit('send',input()));assert.equal(f.c.pending().length,1);
 const g=make(async()=>{calls++;},f.s);assert.equal(g.c.pending().length,1);await assert.rejects(g.c.submit('send',{...input(),operationId:'new'}),e=>e.code==='SESSION_RESULT_PENDING');assert.equal(calls,1);
});
for(const code of ['OFFLINE_REVISION_CONFLICT','SUMMARY_RESULT_PENDING','SUMMARY_SETTINGS_RESULT_PENDING','OFFLINE_RESULT_PENDING'])test(code+' 为明确前置拒绝，不留下不存在的新请求',async()=>{
 const f=make(async()=>{throw {code,httpStatus:409};});await assert.rejects(f.c.submit('send',input()));assert.equal(f.c.pending().length,0);
});
for(const code of ['SUMMARY_SETTINGS_RESULT_UNKNOWN','AUTHORING_STORAGE_UNAVAILABLE'])test(code+' 保留原操作',async()=>{
 const f=make(async()=>{throw {code,httpStatus:503};});await assert.rejects(f.c.submit('send',input()));assert.equal(f.c.pending().length,1);
});
test('恢复查询原请求，不生成 ID，不重复发送；其它作品记录不受影响',async()=>{
 let calls=[],v=view(),phase=0;const f=make(async(path,b)=>{calls.push(path);if(path==='/sessions/send'){phase++;throw Error('lost');}if(path==='/sessions/reconcile')return v;if(path==='/sessions/'+sid)return v;if(path==='/sessions/recover')return receipt(input());throw Error(path);});
 await assert.rejects(f.c.submit('send',input()));v.session.operations=[{operationId:'op',intent:input(),state:'unknown'}];f.s['unrelated']='keep';await f.c.recover(f.c.pending()[0]);assert.equal(phase,1);assert.equal(f.s.unrelated,'keep');assert.deepEqual(f.c.pending(),[]);assert.deepEqual(calls,['/sessions/send','/sessions/reconcile','/sessions/'+sid,'/sessions/recover']);
});
test('原请求尚无记录，不推断失败、不自动重发',async()=>{
 const f=make(async(path)=>{if(path==='/sessions/send')throw Error('lost');return view();});await assert.rejects(f.c.submit('send',input()));const r=await f.c.recover(f.c.pending()[0]);assert.equal(r.state,'unknown');assert.equal(f.c.pending().length,1);
});
test('错误会话、错误意图回执保持阻断',async()=>{
 const f=make(async(path,b)=>receipt({...b,input:'伪造'}));await assert.rejects(f.c.submit('send',input()),e=>e.code==='SESSION_RECEIPT_MISMATCH');assert.equal(f.c.pending().length,1);
 const g=make(async()=>({...view(),session:{...view().session,binding:{workId:'other'}}}));await assert.rejects(g.c.view(),e=>e.code==='SESSION_RECEIPT_MISMATCH');
});
test('确认未生效的设置只能显式以同编号再试或放弃',async()=>{
 const b={action:'install',sessionId:sid,operationId:'install'},calls=[];let succeed=false;const f=make(async(path,body)=>{calls.push([path,body.operationId]);if(path==='/summary-settings/change'){if(!succeed)throw Error('lost');return {state:'applied',intent:body,settings:{sessionId:sid},receipt:{operationId:body.operationId}};}return {state:'not_applied',intent:body,settings:{sessionId:sid}};});
 await assert.rejects(f.c.submit('settings',b));const r=await f.c.recover(f.c.pending()[0]);assert.equal(r.state,'not_applied');assert.equal(f.c.pending().length,1);succeed=true;await f.c.submit('settings',b,true);assert.equal(f.c.pending().length,0);assert.ok(calls.every(([,id])=>id==='install'));
});
test('恢复出需压缩摘要只解除原操作日志，不自动派发压缩',async()=>{
 const b=summaryTaskIntent(view(),'summary','update'),v=view(),calls=[];v.session.summary.jobs=[{operationId:'summary',intent:b,state:'unknown'}];const f=make(async(path)=>{calls.push(path);if(path==='/sessions/summary/update')throw Error('lost');if(path==='/sessions/summary/recover')return {sessionId:sid,job:{operationId:'summary',intent:b,state:'awaiting_compression'}};return v;});
 await assert.rejects(f.c.submit('update',b));assert.equal((await f.c.recover(f.c.pending()[0])).state,'settled');assert.equal(f.c.pending().length,0);assert.ok(!calls.includes('/sessions/summary/continue'));
});
test('M2 优先显示实际策略；配置和发送分开绑定版本',()=>{
 assert.equal(sessionPolicyText(view()),'历史摘要 + 最近 1 回合');assert.equal(sessionPolicyText({effectivePolicy:{mode:'history-window',historyConfig:{turns:2,includeInitialCharacter:true}}}),'最近 2 回合 + 开局原文');
 const s={sessionId:sid,sessionRevision:4,summaryRevision:1,modelRevision:2,effectivePolicy:{revision:'policy'},registryRevision:8,plugin:{permissions:['x']}};assert.deepEqual(summarySettingsIntent(s,'enable','g').permissions,['x']);assert.equal(summaryTaskIntent(view(),'u','update').expectedContextPolicyRevision,'policy');assert.ok(!Object.hasOwn(summaryTaskIntent(view(),'u','recover'),'expectedModelRevision'));
});
