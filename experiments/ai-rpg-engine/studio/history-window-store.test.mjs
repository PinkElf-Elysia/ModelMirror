import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {HistorySessionStore} from './history-session-store.mjs';
import {ModelSessionStore} from './model-session-store.mjs';
import {historyRuntime} from './history-runtime.mjs';
import {requireHistoryState,effectiveHistoryPolicy} from './history-state.mjs';
import {assembleHistoryRequest} from './history-assembly.mjs';
import {assemble} from '../card-replica/lib/assembly.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {HISTORY_WINDOW_ID as H,sha,canonical} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/history-window-b1/store/',import.meta.url));await mkdir(root,{recursive:true});
const input={characterText:'姓名：虚构许澄\n开局24岁',world:'表世界',mode:'offline',params:{max_tokens:16384}};
async function fixture(t){
 const directory=await mkdtemp(join(root,'state-'));
 const store=new HistorySessionStore(join(directory,'sessions'),()=>assert.fail('B1 never dispatches'));await store.init();
 const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id)});t.after(()=>plugins.close());
 const f={store,plugins,async create(){return store.create(input);},async change(action,s){const c=await plugins.catalog(),p=c.plugins.find(x=>x.id===H);return plugins.change(action,{operationId:randomUUID(),pluginId:H,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async grant(s){if(!(await plugins.catalog()).plugins.find(x=>x.id===H).installed)await f.change('install');await f.change('enable',s);},async policy(s){return (await store.historyWindowStatus(s.id,plugins)).historyPolicyRevision;}};return f;
}
const op=(s,turns=1,includeInitialCharacter=false,operationId=randomUUID())=>({operationId,expectedSessionRevision:s.revision,expectedConfigRevision:s.historyWindow.revision,turns,includeInitialCharacter});
async function seed(f,s,count=5){
 for(let i=0;i<count;i++){
  const requestId='synthetic-'+i,selection=structuredClone(s.modelState.current),raw='现在'+(24+i)+'岁\n<LongShortMemory>不改写\t'+i+'</LongShortMemory>';
  const a=assembleHistoryRequest(s,'输入 '+i,effectiveHistoryPolicy(s,await f.plugins.historyAuthorization(s.id)));
  s.history.push({role:'user',content:a.current},{role:'assistant',content:raw});s.turns.push({requestId,raw,rawHash:sha(raw),model:{selectionRevision:0,selection},historyPolicy:structuredClone(a.historyPolicy)});
  s.requests[requestId]={status:'complete',selectionRevision:0,selection,historyPolicy:structuredClone(a.historyPolicy),assemblyHash:a.historyPolicy.requestHash};s.revision++;
 }
 await f.store.write(s);return s;
}
test('new v3 binding, defaults off and zero-plugin request equal to existing assembly; no dispatch',async t=>{
 const f=await fixture(t),s=await f.create();assert.equal(s.runtime.format,3);assert.equal(s.runtime.hash,historyRuntime.hash);assert.deepEqual(s.historyWindow.config,{turns:3,includeInitialCharacter:false});
 const status=await f.store.historyWindowStatus(s.id,f.plugins);assert.equal(status.enabled,false);
 const result=await f.store.prepareHistoryRequest(s.id,{input:'开始',expectedHistoryPolicyRevision:status.historyPolicyRevision},f.plugins);
 assert.deepEqual(result.messages,assemble({...input,input:'开始',history:[]}).messages);assert.deepEqual(result.historyPolicy.selectedTurns,[]);
 await assert.rejects(f.store.send(s.id,{}),/HISTORY_SEND_INVALID/);await assert.rejects(f.store.createBranch(s.id,{},f.plugins),/BRANCH_OPERATION_INVALID/);
});
test('permission, strict fields, atomic revision/idempotence, stale tabs, preserved history and receipt after uninstall',async t=>{
 const f=await fixture(t);let s=await f.create();const change=op(s,3,true,'same-operation');
 await f.change('install');await assert.rejects(f.store.saveHistoryWindow(s.id,change,f.plugins),/PLUGIN_NOT_AUTHORIZED/);await f.grant(s);
 for(const invalid of [{...change,unexpected:1},{...change,turns:51},{...change,turns:1.1},{...change,includeInitialCharacter:1}])await assert.rejects(f.store.saveHistoryWindow(s.id,invalid,f.plugins),/HISTORY_OPERATION_INVALID/);
 const before=structuredClone(s);s=(await f.store.saveHistoryWindow(s.id,change,f.plugins)).session;
 assert.equal(s.revision,1);assert.equal(s.historyWindow.revision,1);assert.deepEqual(s.history,before.history);assert.deepEqual(s.modelState,before.modelState);assert.deepEqual(s.params,before.params);assert.equal(s.runtime.hash,before.runtime.hash);
 assert.equal((await f.store.saveHistoryWindow(s.id,change,f.plugins)).replayed,true);
 await assert.rejects(f.store.saveHistoryWindow(s.id,{...change,turns:2},f.plugins),/HISTORY_OPERATION_COLLISION/);
 await assert.rejects(f.store.saveHistoryWindow(s.id,{...change,operationId:'stale'},f.plugins),/HISTORY_CONFIGURATION_CONFLICT/);
 await f.change('uninstall');assert.equal((await f.store.saveHistoryWindow(s.id,change,f.plugins)).replayed,true);
 const restarted=new HistorySessionStore(f.store.directory,()=>assert.fail('not called'));await restarted.init();assert.equal((await restarted.recoverHistoryOperation(s.id,'same-operation')).status,'complete');
});
test('pending generation and shared exclusive lock reject settings; unknown fields cannot carry prompts',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);s.requests.unknown={status:'pending'};await f.store.write(s);
 await assert.rejects(f.store.saveHistoryWindow(s.id,op(s),f.plugins),/HISTORY_UNCONFIRMED_REQUEST/);
 delete s.requests.unknown;await f.store.write(s);
 await f.store.exclusive(s.id,async()=>{await assert.rejects(f.store.saveHistoryWindow(s.id,op(s),f.plugins),/SESSION_BUSY/);});
 await assert.rejects(f.store.saveHistoryWindow(s.id,{...op(s),system:'replacement'},f.plugins),/HISTORY_OPERATION_INVALID/);
});
test('revocation after proposal before publish prevents configuration commit',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);const invoke=f.plugins.invoke;let release,entered;const ready=new Promise(r=>entered=r);
 f.plugins.invoke=async x=>{const ticket=await invoke(x);entered();await new Promise(r=>release=r);return ticket;};
 const pending=f.store.saveHistoryWindow(s.id,op(s),f.plugins);await ready;await f.change('disable',s);release();await assert.rejects(pending,/PLUGIN_NOT_AUTHORIZED|PLUGIN_RESULT_STALE/);
 assert.deepEqual((await f.store.read(s.id)).historyWindow,s.historyWindow);
});
test('failed final write retains recoverable intent, blocks new IDs across restart and resolves only same operation',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);const operation=op(s,2,true,'failed-publish');const publish=f.store.publishHistoryConfiguration;
 f.store.publishHistoryConfiguration=async()=>{throw Error('synthetic disk failure');};
 await assert.rejects(f.store.saveHistoryWindow(s.id,operation,f.plugins),/synthetic disk failure/);
 let disk=await f.store.read(s.id);assert.equal(disk.historyWindow.pending,'failed-publish');assert.deepEqual(disk.historyWindow.config,s.historyWindow.config);
 const restarted=new HistorySessionStore(f.store.directory,()=>assert.fail('not called'));await restarted.init();
 await assert.rejects(restarted.saveHistoryWindow(s.id,op(s,4,false,'new-id'),f.plugins),/HISTORY_OPERATION_UNCONFIRMED/);
 await assert.rejects(restarted.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:await f.policy(s)},f.plugins),/HISTORY_OPERATION_UNCONFIRMED/);
 assert.equal((await restarted.recoverHistoryOperation(s.id,'wrong-id')).status,'not-found');assert.equal((await restarted.read(s.id)).historyWindow.pending,'failed-publish');
 assert.equal((await restarted.recoverHistoryOperation(s.id,'failed-publish')).status,'not-applied');
 assert.equal((await restarted.saveHistoryWindow(s.id,operation,f.plugins)).operation.status,'not-applied');
 f.store.publishHistoryConfiguration=publish;disk=await f.store.read(s.id);disk=(await f.store.saveHistoryWindow(s.id,op(disk,2,true),f.plugins)).session;assert.equal(disk.historyWindow.revision,1);
 assert.ok(!(await readdir(f.store.directory)).some(x=>x.endsWith('.tmp')));
});
test('lost result after successful publication is recovered without duplicate save',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);const operation=op(s,50,false,'lost-response'),publish=f.store.publishHistoryConfiguration.bind(f.store);
 f.store.publishHistoryConfiguration=async value=>{await publish(value);throw Error('synthetic lost response');};
 await assert.rejects(f.store.saveHistoryWindow(s.id,operation,f.plugins),/synthetic lost response/);
 assert.equal((await f.store.recoverHistoryOperation(s.id,'lost-response')).status,'complete');
 const result=await f.store.saveHistoryWindow(s.id,operation,f.plugins);assert.equal(result.replayed,true);assert.equal(result.session.historyWindow.revision,1);assert.equal(result.session.historyWindow.config.turns,50);
});
test('selected request copy preserves wrappers/raw history and actual progress; optional snapshot only once',async t=>{
 const f=await fixture(t);let s=await seed(f,await f.create());await f.grant(s);
 for(const include of [false,true]){
  s=(await f.store.saveHistoryWindow(s.id,op(s,3,include),f.plugins)).session;const before=await readFile(f.store.path(s.id));
  const a=await f.store.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:await f.policy(s)},f.plugins),original=assemble({characterText:s.characterText,input:'继续',history:s.history,world:s.world});
  assert.equal(a.messages.length,8);assert.deepEqual(a.messages[0],original.messages[0]);assert.deepEqual(a.messages.at(-1),original.messages.at(-1));assert.deepEqual(a.historyPolicy.selectedTurns,[3,4,5]);assert.deepEqual(a.historyPolicy.selectedRequestIds,['synthetic-2','synthetic-3','synthetic-4']);
  assert.equal(a.messages[1].content,(include?'开局角色资料（原始快照）\n'+s.characterText+'\n\n':'')+s.history[4].content);
  assert.deepEqual(a.messages.slice(2,-1),s.history.slice(5));assert.equal(a.historyPolicy.requestHash,sha(canonical(a.messages)));assert.deepEqual(await readFile(f.store.path(s.id)),before);
 }
 const previous=await f.policy(s);await f.change('disable',s);
 await assert.rejects(f.store.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:previous},f.plugins),/HISTORY_POLICY_CONFLICT/);
 const full=await f.store.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:await f.policy(s)},f.plugins);assert.deepEqual(full.messages,assemble({characterText:s.characterText,input:'继续',history:s.history,world:s.world}).messages);
 f.plugins.historyAuthorization=async()=>{throw Error('unknown permission state');};await assert.rejects(f.store.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:'unknown'},f.plugins),/unknown permission state/);
});
test('legacy stays byte-identical/readable without M1 upgrade; runtime/operation corruption refuses use',async t=>{
 const f=await fixture(t),legacy=new ModelSessionStore(f.store.directory,async()=>'old');const old=await legacy.create(input),bytes=await readFile(legacy.path(old.id));
 assert.deepEqual(await f.store.historyWindowStatus(old.id,f.plugins),{compatible:false,reason:'HISTORY_RUNTIME_INCOMPATIBLE',enabled:false});
 await assert.rejects(f.store.saveHistoryWindow(old.id,{operationId:'legacy',expectedSessionRevision:0,expectedConfigRevision:0,turns:3,includeInitialCharacter:false},f.plugins),/HISTORY_RUNTIME_INCOMPATIBLE/);assert.deepEqual(await readFile(legacy.path(old.id)),bytes);
 let s=await f.create();await f.grant(s);s=(await f.store.saveHistoryWindow(s.id,op(s),f.plugins)).session;
 const modified=structuredClone(s);modified.historyWindow.config.turns=4;assert.throws(()=>requireHistoryState(modified),/HISTORY_STATE_INVALID/);
 s.runtime.hash=sha('other runtime');await f.store.write(s);await assert.rejects(f.store.historyWindowStatus(s.id,f.plugins),/RUNTIME_BINDING_INVALID/);
});

test('two tabs and another session mutation cannot commit while settings publication is pending',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);let release,entered;const ready=new Promise(r=>entered=r),publish=f.store.publishHistoryConfiguration.bind(f.store);
 f.store.publishHistoryConfiguration=async value=>{entered();await new Promise(r=>release=r);return publish(value);};
 const first=f.store.saveHistoryWindow(s.id,op(s,4,false,'first'),f.plugins);await ready;
 await assert.rejects(f.store.saveHistoryWindow(s.id,op(s,5,false,'second'),f.plugins),/SESSION_BUSY/);
 await assert.rejects(f.store.exclusive(s.id,()=>assert.fail('another mutation entered')),/SESSION_BUSY/);
 release();const result=await first;assert.equal(result.session.historyWindow.config.turns,4);assert.equal(Object.keys(result.session.historyWindow.operations).length,1);
 await assert.rejects(f.store.saveHistoryWindow(s.id,op(s,5,false,'second'),f.plugins),/HISTORY_CONFIGURATION_CONFLICT/);
});
test('input is snapshotted before async work and aborted/unfinished history cannot be selected',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);const operation=op(s,3,false,'snapshot');
 const saving=f.store.saveHistoryWindow(s.id,operation,f.plugins);operation.turns=50;s=(await saving).session;assert.equal(s.historyWindow.config.turns,3);
 s=await seed(f,s,2);const baseline=await f.store.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:await f.policy(s)},f.plugins);
 s.requests.cancelled={status:'cancelled'};await f.store.write(s);const after=await f.store.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:await f.policy(s)},f.plugins);assert.deepEqual(after.messages,baseline.messages);
 s.history.push({role:'user',content:'未完成回合'});await f.store.write(s);await assert.rejects(f.store.prepareHistoryRequest(s.id,{input:'继续',expectedHistoryPolicyRevision:await f.policy(s)},f.plugins),/BRANCH_HISTORY_INVALID/);
});
