import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {AuthoringStore} from './store.mjs';
import {blankDocument,sha256} from './contracts.mjs';
import {openPublishedRuntime} from './runtime-binding.mjs';
import {createAuthoringPluginBridge} from './runtime-plugins.mjs';
import {MODEL_PLUGIN,commitPluginModel,validateModelPluginReceipt,checkPluginCatalog} from './model-plugin.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/authoring-b6ae-tests/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t){
 const dir=await mkdtemp(join(root,'run-')),store=await AuthoringStore.open(join(dir,'works'));
 const d=blankDocument();d.metadata.name='第五插件独立作品';d.metadata.audience='female';d.sources.system='完整作者内容';
 const w=await store.create({operationId:'new',expectedRevision:null,document:d}),v=await store.publish(w.workId,{operationId:'publish',expectedRevision:0});
 const runtime=await openPublishedRuntime(store,{workId:w.workId,versionId:v.result.versionId}),id='session',binding=runtime.bindSession({sessionId:id,openingText:'开局'});
 const session={id,revision:2,binding,completedTurns:0,busy:false,pending:false};
 const bridge=await createAuthoringPluginBridge({directory:join(dir,'plugins'),lookupSession:async()=>structuredClone(session),resolveRuntime:async()=>runtime}),service=bridge.pluginService;
 t.after(async()=>{await service.close();await store.close();});
 const change={revision:1,intent:{sessionId:id,operationId:'choose',purpose:'story',expectedRevision:2,expectedModelRevision:0,choice:{selectionId:'catalog-item',selectionRevision:sha256('provider')}},choice:{entry:{selectionId:'catalog-item'}}};
 let saved=null;
 async function action(action){const c=await service.catalog(),p=c.plugins.find(p=>p.id===MODEL_PLUGIN);return service.change(action,{operationId:randomUUID(),pluginId:MODEL_PLUGIN,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,
 ...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:session.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});}
 return {change,service,session,action,get saved(){return saved;},run:extra=>commitPluginModel({service,change,resourceHash:binding.resourceHash,commit:async c=>{saved=c;return c;},...extra})};
}
test('模型插件安装与授权独立；未授权不能提交配置',async t=>{const f=await fixture(t);await assert.rejects(f.run(),/PLUGIN_NOT_INSTALLED/);await f.action('install');await assert.rejects(f.run(),/PLUGIN_NOT_AUTHORIZED/);assert.equal(f.saved,null);});
test('受信提案执行后保存完整回执；目录读取不产生配置更改',async t=>{const f=await fixture(t);await f.action('install');await f.action('enable');await checkPluginCatalog(f.service,f.session.id);assert.equal(f.saved,null);const c=await f.run();assert.equal(c.pluginReceipt.proposal.selectionRevision,0);assert.equal(c.pluginReceipt.proposal.selectionId,'catalog-item');assert.ok(!Object.hasOwn(c.pluginReceipt,'ticket'));assert.deepEqual(c.intent,f.change.intent);});
test('提案后撤权阻断保存，不使用迟到模型配置',async t=>{const f=await fixture(t);await f.action('install');await f.action('enable');await assert.rejects(f.run({beforeCommit:()=>f.action('disable')}),/PLUGIN_NOT_AUTHORIZED|PLUGIN_RESULT_STALE/);assert.equal(f.saved,null);});
test('提案后会话修订变化与重装均使原票据失效',async t=>{const f=await fixture(t);await f.action('install');await f.action('enable');await assert.rejects(f.run({beforeCommit:async()=>{f.session.revision++;}}),/PLUGIN_RESULT_STALE/);f.session.revision--;await assert.rejects(f.run({beforeCommit:async()=>{await f.action('uninstall');await f.action('install');await f.action('enable');}}),/PLUGIN_RESULT_STALE/);assert.equal(f.saved,null);});
test('持久回执不能替换作品、模型、用途或修订',async t=>{const f=await fixture(t);await f.action('install');await f.action('enable');const c=await f.run();for(const mutate of [r=>r.resourceHash=sha256('other'),r=>r.proposal.selectionId='other',r=>r.sessionRevision++,r=>r.proposal.selectionRevision++]){const r=structuredClone(c.pluginReceipt);mutate(r);assert.throws(()=>validateModelPluginReceipt(r,c,f.session.binding.resourceHash));}assert.throws(()=>validateModelPluginReceipt(c.pluginReceipt,{...c,intent:{...c.intent,purpose:'memory'}},f.session.binding.resourceHash));});
