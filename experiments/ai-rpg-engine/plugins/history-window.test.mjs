import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {selectHistory,DEFAULT_CONFIG,invoke} from './history-window.mjs';
import {loadReviewedCatalog,verifyPackage,HISTORY_WINDOW_ID as H,PLUGIN_ID as B,sha} from './catalog.mjs';
import {createPluginService} from './host.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/history-window-b1/plugins/',import.meta.url));await mkdir(root,{recursive:true});
const history=Array.from({length:102},(_,i)=>({role:i%2?'assistant':'user',content:i%2?'正文\n<LSM>原样\t'+i+'</LSM>':'前置\n输入 '+i+'\n后置'}));
const characterText='姓名：合成角色\n24岁（开局）';
test('whole turns at 0/below/equal/above/1/50; pure copies and zero-plugin byte equality',()=>{
 for(const T of [0,1,2,3,4,50,51])for(const N of [1,3,50])for(const enabled of [false,true]){
  const source=structuredClone(history.slice(0,T*2)),before=JSON.stringify(source);
  const result=selectHistory({history:source,characterText,enabled,config:{turns:N,includeInitialCharacter:false}});
  const start=enabled?Math.max(0,T-N):0;
  assert.deepEqual(result.history,source.slice(start*2));assert.deepEqual(result.record.selectedTurns,Array.from({length:T-start},(_,i)=>start+i+1));
  assert.equal(JSON.stringify(source),before);if(result.history.length){result.history[0].content='mutated copy';assert.equal(JSON.stringify(source),before);}
 }
});
test('initial snapshot exactly once only outside enabled window; newer state and LSM untouched',()=>{
 for(const enabled of [false,true])for(const includeInitialCharacter of [false,true])for(const T of [0,2,3,5]){
  const source=structuredClone(history.slice(0,T*2));if(T)source.at(-1).content='现在26岁\n<LSM>记忆原文</LSM>';
  const before=JSON.stringify(source),r=selectHistory({history:source,characterText,enabled,config:{turns:3,includeInitialCharacter}});
  const add=enabled&&includeInitialCharacter&&T>3;assert.equal(r.record.initialSnapshotAdded,add);
  if(add)assert.equal(r.history[0].content,'开局角色资料（原始快照）\n'+characterText+'\n\n'+source[(T-3)*2].content);
  if(T)assert.equal(r.history.at(-1).content,source.at(-1).content);assert.equal(JSON.stringify(source),before);
 }
});
test('strict configuration and incomplete turns reject; no card field assumptions',()=>{
 for(const turns of [0,51,-1,1.5,'3',null,NaN])assert.throws(()=>selectHistory({history:[],characterText,enabled:true,config:{turns,includeInitialCharacter:false}}),/HISTORY_CONFIGURATION_INVALID/);
 for(const config of [{...DEFAULT_CONFIG,extra:true},{turns:3,includeInitialCharacter:'false'}])assert.throws(()=>selectHistory({history:[],characterText,enabled:true,config}),/HISTORY_CONFIGURATION_INVALID/);
 assert.throws(()=>selectHistory({history:history.slice(0,3),characterText,enabled:true}),/HISTORY_INCOMPLETE/);
 assert.throws(()=>selectHistory({history:[{role:'assistant',content:'a'},{role:'user',content:'u'}],characterText,enabled:true}),/HISTORY_INCOMPLETE/);
 assert.deepEqual(selectHistory({history:[],characterText:'generic configuration',enabled:false}).history,[]);
 assert.throws(()=>invoke({capability:'network.fetch',session:{id:'s'},input:{}}));
});
async function fixture(t){
 const directory=await mkdtemp(join(root,'state-'));const sessions=new Map([['session',{id:'session',cardId:'earth',revision:0,completedTurns:5,resourceHash:sha('new'),historyWindowCompatible:true}],['old',{id:'old',cardId:'earth',revision:0,completedTurns:2,resourceHash:sha('old')}]]);
 const host=await createPluginService({directory,lookupSession:async id=>structuredClone(sessions.get(id))});t.after(()=>host.close());
 const change=async(action,sessionId,pluginId=H,override={})=>{const c=await host.catalog(),p=c.plugins.find(x=>x.id===pluginId);return host.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(sessionId?{sessionId,expectedSessionRevision:0}:{}),...(action==='enable'?{permissions:p.permissions}:{}),...override});};return {host,change,sessions};
}
test('third reviewed artifact hash, exact permission boundary and legacy incompatibility',async t=>{
 const entry=await loadReviewedCatalog(H);assert.equal(entry.manifest.network,'none');assert.equal(entry.manifest.modelAccess,false);
 assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify({...entry.manifest,artifactSha256:sha('wrong')})),Buffer.from('wrong bytes')),/PLUGIN_ARTIFACT_MISMATCH/);
 const f=await fixture(t);assert.equal((await f.host.catalog()).plugins.length,3);
 assert.equal((await f.host.historyAuthorization('session')).enabled,false);await f.change('install');
 await assert.rejects(f.host.invoke({pluginId:H,sessionId:'session',capability:'session.history.configure',input:DEFAULT_CONFIG}),/PLUGIN_NOT_AUTHORIZED/);
 await assert.rejects(f.change('enable','old'),/HISTORY_RUNTIME_INCOMPATIBLE/);
 await assert.rejects(f.change('enable','session',H,{permissions:[]}),/PLUGIN_PERMISSION_MISMATCH/);
 await assert.rejects(f.change('enable','session',H,{artifactSha256:sha('bad')}),/PLUGIN_VERSION_MISMATCH/);
 await f.change('enable','session');await assert.rejects(f.host.invoke({pluginId:H,sessionId:'session',capability:'model.catalog.read'}),/PLUGIN_CAPABILITY_DENIED/);
});
test('authorization token tracks revoke/reinstall; stale ticket cannot commit; other plugin unaffected',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','session');await f.change('install',null,B);await f.change('enable','session',B);
 const a=await f.host.historyAuthorization('session');const ticket=await f.host.invoke({pluginId:H,sessionId:'session',capability:'session.history.configure',input:DEFAULT_CONFIG});
 await f.change('disable','session');assert.notEqual((await f.host.historyAuthorization('session')).revision,a.revision);
 let commits=0;await assert.rejects(f.host.commitResult(ticket,()=>commits++),/PLUGIN_NOT_AUTHORIZED/);assert.equal(commits,0);
 await f.change('enable','session');await f.change('uninstall');assert.equal((await f.host.historyAuthorization('session')).enabled,false);assert.equal((await f.host.sessionStatus('session',B)).enabled,true);
 await f.change('install');assert.equal((await f.host.historyAuthorization('session')).enabled,false);await f.change('enable','session');
 const valid=await f.host.invoke({pluginId:H,sessionId:'session',capability:'session.history.configure',input:DEFAULT_CONFIG});await f.host.commitResult(valid,()=>commits++);assert.equal(commits,1);await assert.rejects(f.host.commitResult(valid,()=>commits++),/PLUGIN_RESULT_INVALID/);
 f.sessions.get('session').resourceHash=sha('changed');await assert.rejects(f.host.historyAuthorization('session'),/PLUGIN_NOT_AUTHORIZED/);
});
