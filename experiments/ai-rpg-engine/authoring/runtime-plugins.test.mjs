import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {AuthoringStore} from './store.mjs';
import {blankDocument,canonical,sha256} from './contracts.mjs';
import {openPublishedRuntime,PLUGIN_CONTRACT_KEY} from './runtime-binding.mjs';
import {createAuthoringPluginBridge,loadAuthoringPluginPackages} from './runtime-plugins.mjs';
import {loadReviewedCatalog,PLUGIN_ID,MODEL_SELECTOR_ID,HISTORY_WINDOW_ID,ROLLING_SUMMARY_ID,MEMORY_PALACE_ID} from '../plugins/catalog.mjs';
import {loadGodPluginPackages} from '../god-card/lib/plugins.mjs';

const IDS = [PLUGIN_ID,MODEL_SELECTOR_ID,HISTORY_WINDOW_ID,ROLLING_SUMMARY_ID,MEMORY_PALACE_ID];
const [B,M,H,S,P] = IDS;
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6a-tests/plugins/',import.meta.url));
await mkdir(root,{recursive:true});
async function fixture(t) {
  const directory=await mkdtemp(join(root,'run-')), store=await AuthoringStore.open(join(directory,'works'));
  t.after(()=>store.close());
  const runtimes=new Map(), sessions=new Map();
  for(const name of ['灯塔档案','Amber Atlas']) {
    const document=blankDocument(); document.metadata.name=name; document.metadata.audience='male'; document.sources.system='作者正文 '+name;
    const made=await store.create({operationId:randomUUID(),expectedRevision:null,document});
    const v=await store.publish(made.workId,{operationId:randomUUID(),expectedRevision:0});
    const runtime=await openPublishedRuntime(store,{workId:made.workId,versionId:v.result.versionId});
    runtimes.set(made.workId,runtime);
    const id=sessions.size?'second':'first';
    sessions.set(id,{id,revision:2,binding:runtime.bindSession({sessionId:id,openingText:'原始玩家开局 '+name}),completedTurns:1,busy:false,pending:false});
  }
  const first=sessions.get('first'), runtime=runtimes.get(first.binding.workId);
  sessions.set('child',{...structuredClone(first),id:'child',binding:runtime.bindSession({sessionId:'child',openingText:first.binding.openingText})});
  const ledger=join(directory,'authorization-ledger.json');await writeFile(ledger,canonical({authorized:7,used:7,unknown:['prior-operation']}));
  const options={directory:join(directory,'plugins'),lookupSession:async id=>structuredClone(sessions.get(id)),resolveRuntime:async({workId})=>runtimes.get(workId)};
  let bridge=await createAuthoringPluginBridge(options);t.after(()=>bridge.pluginService.close());
  const f={directory,store,sessions,runtimes,ledger,get bridge(){return bridge;},get host(){return bridge.pluginService;},
    async reopen(){await bridge.pluginService.close();bridge=await createAuthoringPluginBridge(options);},
    async payload(action,pluginId,id='first'){
      const catalog=await f.host.catalog(),p=catalog.plugins.find(p=>p.id===pluginId);
      return {operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:catalog.revision,
        ...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:sessions.get(id).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})};
    },
    async change(action,pluginId,id){return f.host.change(action,await f.payload(action,pluginId,id));},
    async ready(pluginId,id){await f.change('install',pluginId);await f.change('enable',pluginId,id);},
    invoke(pluginId,capability,input={},sessionId='first'){return f.host.invoke({pluginId,sessionId,capability,input});},
  };return f;
}
const requests = new Map([
  [B,[['ui.message-action',{}],['session.branch.prepare',{turn:1,name:'分支草案'}]]],
  [M,[['ui.model-action',{}],['model.catalog.read',{}],['session.model.select',{selectionId:'story',selectionRevision:2}]]],
  [H,[['ui.history-action',{}],['session.history.configure',{turns:1,includeInitialCharacter:false}]]],
  [S,[['ui.summary-action',{}],['session.summary.configure',{timing:'after',selectionId:'summary',catalogRevision:'rev1'}],['session.summary.revise',{textHash:sha256('手工摘要'),characters:4}],['session.summary.request',{}]]],
  [P,[['ui.memory-action',{}],['session.memory.configure',{selectionId:'memory',catalogRevision:'rev2'}],['session.memory.revise',{action:'create',value:{name:'灯塔',keywords:['灯塔'],match:'any',roles:['user','assistant'],content:'码头外的灯塔。',enabled:true}}],['session.memory.request',{}]]],
]);

test('新宿主只扩展协议兼容声明，五个执行器、权限与旧 Earth/God 清单不变',async()=>{
  const entries=await loadAuthoringPluginPackages(), godBefore=await loadGodPluginPackages();
  assert.deepEqual(entries.map(e=>e.manifest.id),IDS);
  for(const entry of entries){
    const old=await loadReviewedCatalog(entry.manifest.id);
    assert.equal(entry.invoke,old.invoke); assert.equal(entry.artifactSha256,old.artifactSha256);
    assert.deepEqual(entry.manifest.permissions,old.manifest.permissions);
    assert.deepEqual(entry.manifest.capabilities,old.manifest.capabilities);
    assert.deepEqual(entry.manifest.compatibleCards,[PLUGIN_CONTRACT_KEY]);
    assert.deepEqual(old.manifest.compatibleCards,['earth']);
    assert.notEqual(entry.manifestSha256,old.manifestSha256);
    assert.equal(entry.manifest.network,'none'); assert.equal(entry.manifest.modelAccess,false);
    assert.equal(entry.manifestSha256,sha256(canonical(entry.manifest)));
  }
  assert.deepEqual((await loadGodPluginPackages()).map(e=>e.manifest),godBefore.map(e=>e.manifest));
  assert.ok(godBefore.every(e=>canonical(e.manifest.compatibleCards)===canonical(['earth','god'])));
});

test('五插件分别安装、逐会话授权；两个任意作品可使用全部提案协议，无模型或额度副作用',async t=>{
  const f=await fixture(t),ledger=await readFile(f.ledger),before=structuredClone([...f.sessions]);
  assert.equal(f.bridge.runtimeReady,false);assert.equal(f.bridge.stage,'proposals-only');
  for(const id of IDS){
    const [capability,input]=requests.get(id)[0];
    await assert.rejects(f.invoke(id,capability,input),/PLUGIN_NOT_INSTALLED/);
    await f.change('install',id);
    await assert.rejects(f.invoke(id,capability,input),/PLUGIN_NOT_AUTHORIZED/);
    await f.change('enable',id);
    assert.equal((await f.host.sessionStatus('second',id)).enabled,false);
    assert.equal((await f.host.sessionStatus('child',id)).enabled,false);
    await f.change('enable',id,'second');
    for(const sessionId of ['first','second']) for(const [capability,input] of requests.get(id)) {
      const ticket=await f.invoke(id,capability,input,sessionId);
      assert.equal(ticket.sessionId,sessionId);assert.equal(ticket.pluginId,id);
      await f.host.validateResult(ticket);
      assert.equal(JSON.stringify(ticket).includes('原始玩家开局'),false);
    }
  }
  assert.deepEqual(await readFile(f.ledger),ledger);assert.deepEqual([...f.sessions],before);
});

test('显式授权持久化；重开和重复操作不重新启用已停用插件',async t=>{
  const f=await fixture(t);await f.change('install',P);
  const enable=await f.payload('enable',P);await f.host.change('enable',enable);
  await f.reopen();assert.equal((await f.host.sessionStatus('first',P)).enabled,true);
  await f.change('disable',P);const repeated=await f.host.change('enable',enable);
  assert.equal(repeated.replayed,true);assert.equal((await f.host.sessionStatus('first',P)).enabled,false);
  assert.equal((await f.host.sessionStatus('child',P)).enabled,false);
  await assert.rejects(f.host.change('disable',{...await f.payload('disable',P),operationId:enable.operationId}),/PLUGIN_OPERATION_COLLISION/);
});

test('撤权/卸载/同名重装后旧提案不能提交，迟到结果不跨授权 epoch',async t=>{
  const f=await fixture(t);await f.ready(P);
  const ticket=await f.invoke(P,'session.memory.request');await f.change('disable',P);
  await assert.rejects(f.host.commitResult(ticket,()=>assert.fail('must not apply')),/PLUGIN_NOT_AUTHORIZED/);
  await f.change('enable',P);await assert.rejects(f.host.validateResult(ticket),/PLUGIN_RESULT_STALE/);
  const active=await f.invoke(P,'session.memory.request');await f.change('uninstall',P);
  await f.change('install',P);assert.equal((await f.host.sessionStatus('first',P)).enabled,false);
  await f.change('enable',P);await assert.rejects(f.host.validateResult(active),/PLUGIN_RESULT_STALE/);
});

test('权限不能跨作品、版本、开局或 session ID；复制/分支只携带绑定不携带授权',async t=>{
  const f=await fixture(t);await f.ready(H);
  const first=f.sessions.get('first'), runtime=f.runtimes.get(first.binding.workId), old=structuredClone(first.binding);
  first.binding=runtime.bindSession({sessionId:'first',openingText:'更改过的开局'});
  assert.equal((await f.host.sessionStatus('first',H)).enabled,false);
  first.binding=old;
  const work=await f.store.read(old.workId);work.draft.sources.system+=' 新版';
  await f.store.save(old.workId,{operationId:'edit',expectedRevision:1,document:work.draft});
  const v=await f.store.publish(old.workId,{operationId:'seal',expectedRevision:2});
  const newer=await openPublishedRuntime(f.store,{workId:old.workId,versionId:v.result.versionId});
  f.runtimes.set(old.workId,newer);first.binding=newer.bindSession({sessionId:'first',openingText:old.openingText});
  assert.equal((await f.host.sessionStatus('first',H)).enabled,false);
  first.binding=old;
  await assert.rejects(f.host.sessionStatus('first',H),/AUTHORING_RUNTIME_BINDING_MISMATCH/);
  f.runtimes.set(old.workId,runtime);assert.equal((await f.host.sessionStatus('first',H)).enabled,true);
  const second=f.sessions.get('second');second.binding=old;
  await assert.rejects(f.host.sessionStatus('second',H),/AUTHORING_RUNTIME_SESSION_INVALID/);
  assert.equal((await f.host.sessionStatus('child',H)).enabled,false);
});

test('pending/busy 阻止新授权和提案；停用不能消除未确认状态',async t=>{
  const f=await fixture(t);await f.ready(S);const s=f.sessions.get('first');
  const ticket=await f.invoke(S,'session.summary.request');s.pending=true;
  await assert.rejects(f.change('enable',S),/PLUGIN_SESSION_BUSY/);
  await assert.rejects(f.invoke(S,'session.summary.request'),/PLUGIN_SESSION_BUSY/);
  await assert.rejects(f.host.validateResult(ticket),/PLUGIN_RESULT_STALE/);
  await f.change('disable',S);assert.equal(s.pending,true);s.pending=false;s.busy=true;
  await assert.rejects(f.change('enable',S),/PLUGIN_SESSION_BUSY/);
});

test('旧 revision、多标签并发、越权输入与被改写提案明确拒绝',async t=>{
  const f=await fixture(t),p=await f.payload('install',B),q=await f.payload('install',M);
  const results=await Promise.allSettled([f.host.change('install',p),f.host.change('install',q)]);
  assert.equal(results.filter(r=>r.status==='fulfilled').length,1);
  assert.match(results.find(r=>r.status==='rejected').reason.code,/PLUGIN_REGISTRY_CONFLICT/);
  if(!(await f.host.sessionStatus('first',M)).installed)await f.change('install',M);
  const grant=await f.payload('enable',M);
  await assert.rejects(f.host.change('enable',{...grant,permissions:['network.request']}),/PLUGIN_PERMISSION_MISMATCH/);
  await assert.rejects(f.host.change('enable',{...grant,expectedSessionRevision:1}),/PLUGIN_SESSION_CONFLICT/);
  await f.host.change('enable',grant);
  await assert.rejects(f.invoke(M,'session.model.select',{selectionId:'story',selectionRevision:2,baseURL:'https://provider.invalid'}),/PLUGIN_EXECUTION_FAILED/);
  await assert.rejects(f.invoke(M,'network.request'),/PLUGIN_CAPABILITY_DENIED/);
  const ticket=await f.invoke(M,'session.model.select',{selectionId:'story',selectionRevision:2});
  await assert.rejects(f.host.validateResult({...ticket,proposal:{...ticket.proposal,selectionId:'other'}}),/PLUGIN_RESULT_INVALID/);
  f.sessions.get('first').revision++;
  await assert.rejects(f.host.validateResult(ticket),/PLUGIN_RESULT_STALE/);
});
