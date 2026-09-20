import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {createPluginService} from './host.mjs';
import {loadReviewedPlugins,loadReviewedCatalog,verifyPackage,PLUGIN_ID as B,MODEL_SELECTOR_ID as M,HISTORY_WINDOW_ID as H,sha} from './catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/model-selector-b1-tests/',import.meta.url));
await mkdir(root,{recursive:true});
async function fixture(t,{override={},timeoutMs=1000}={}){
 const directory=await mkdtemp(join(root,'host-')),entries=await loadReviewedPlugins();
 const sessions=new Map(['one','two'].map(id=>[id,{id,cardId:'earth',revision:2,completedTurns:2,resourceHash:sha(id),busy:false,pending:false}]));
 const options={directory,timeoutMs,lookupSession:async id=>structuredClone(sessions.get(id)),loadCatalog:async()=>entries.map(e=>({...e,...override[e.manifest.id]}))};
 let host=await createPluginService(options);
 t.after(async()=>{await host.close();assert.equal(dirname(directory),root.replace(/[\\/]$/,''));await rm(directory,{recursive:true,force:true});});
 const f={directory,entries,sessions,get host(){return host;},async reopen(){await host.close();host=await createPluginService(options);},
  async payload(action,pluginId,sessionId='one') {const e=entries.find(e=>e.manifest.id===pluginId);return {operationId:randomUUID(),pluginId,version:e.manifest.version,artifactSha256:e.artifactSha256,manifestSha256:e.manifestSha256,expectedRegistryRevision:(await host.catalog()).revision,
   ...(['enable','disable'].includes(action)?{sessionId,expectedSessionRevision:sessions.get(sessionId).revision}:{}),...(action==='enable'?{permissions:e.manifest.permissions}:{})};},
  async change(action,pluginId,sessionId){return host.change(action,await f.payload(action,pluginId,sessionId));},
  async ready(pluginId){await f.change('install',pluginId);await f.change('enable',pluginId);},
  invoke(pluginId,capability=pluginId===B?'session.branch.prepare':'session.model.select',input=pluginId===B?{turn:1,name:'分支'}:{selectionId:'catalog-entry-1',selectionRevision:0}){return host.invoke({pluginId,sessionId:'one',capability,input});},
 };return f;
}
test('second package binds its exact capabilities, bytes and proposal-only boundary',async()=>{
 const entries=await loadReviewedPlugins();assert.deepEqual(entries.map(e=>e.manifest.id),[B,M,H]);
 const e=await loadReviewedCatalog(M);assert.equal(e.manifest.network,'none');assert.equal(e.manifest.modelAccess,false);
 assert.deepEqual(e.manifest.permissions,['model.catalog.read','session.model.select','ui.contribute']);
 const m=await readFile(new URL('./model-selector.manifest.json',import.meta.url)),a=await readFile(new URL('./model-selector.mjs',import.meta.url));
 assert.equal(verifyPackage(m,a).artifactSha256,e.artifactSha256);
 assert.throws(()=>verifyPackage(m,Buffer.from('tampered')),/PLUGIN_ARTIFACT_MISMATCH/);
 assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify({...JSON.parse(m),permissions:['network.request']})),a),/PLUGIN_MANIFEST_INVALID/);
 const session={id:'one'};for(const input of [{selectionId:'https://provider.invalid',selectionRevision:0},{selectionId:'x',selectionRevision:-1},{selectionId:'x',selectionRevision:0,system:'injected'}])assert.throws(()=>e.invoke({capability:'session.model.select',session,input}),/INVALID_MODEL_SELECTION_REQUEST/);
});
test('install/consent are separate for each plugin and session, and survive restart',async t=>{
 const f=await fixture(t);await f.ready(B);await assert.rejects(f.invoke(M),/PLUGIN_NOT_INSTALLED/);
 await f.change('install',M);await assert.rejects(f.invoke(M),/PLUGIN_NOT_AUTHORIZED/);
 const consent=await f.payload('enable',M);await assert.rejects(f.host.change('enable',{...consent,permissions:f.entries[0].manifest.permissions}),/PLUGIN_PERMISSION_MISMATCH/);
 await f.host.change('enable',consent);await f.reopen();
 for(const p of [B,M]){assert.equal((await f.host.sessionStatus('one',p)).enabled,true);assert.equal((await f.host.sessionStatus('two',p)).enabled,false);}
 assert.equal((await f.invoke(M)).proposal.kind,'model-selection-request');
 for(const [p,c] of [[B,'session.model.select'],[M,'session.branch.prepare'],[M,'network.request'],[M,'model.dispatch']])await assert.rejects(f.invoke(p,c),/PLUGIN_CAPABILITY_DENIED/);
});
test('uninstall/reinstall revokes only its own grants and tickets; new consent required',async t=>{
 const f=await fixture(t);await f.ready(B);await f.ready(M);const branch=await f.invoke(B),model=await f.invoke(M);
 await f.change('uninstall',M);assert.equal((await f.host.sessionStatus('one',B)).enabled,true);await f.host.validateResult(branch);await assert.rejects(f.host.validateResult(model),/PLUGIN_NOT_INSTALLED/);
 await f.change('install',M);assert.equal((await f.host.sessionStatus('one',M)).enabled,false);await assert.rejects(f.invoke(M),/PLUGIN_NOT_AUTHORIZED/);
 await f.change('enable',M);await assert.rejects(f.host.validateResult(model),/PLUGIN_RESULT_STALE/);await f.host.validateResult(branch);
});
test('late model result after revocation cannot commit; branch remains usable',async t=>{
 let entered,release;const started=new Promise(r=>entered=r);
 const f=await fixture(t,{override:{[M]:{invoke:async args=>{entered();await new Promise(r=>release=r);return {kind:'model-selection-request',sessionId:args.session.id,...args.input};}}}});
 await f.ready(B);await f.ready(M);const pending=f.invoke(M);await started;
 assert.equal((await f.invoke(B)).proposal.kind,'branch-request');await f.change('disable',M);release();await assert.rejects(pending,/PLUGIN_NOT_AUTHORIZED/);
 assert.equal((await f.host.sessionStatus('one',B)).lastError,null);
});
test('revocation before commit and one-shot commit prevent stale/duplicate persistence',async t=>{
 const f=await fixture(t);await f.ready(M);const r=await f.invoke(M);await f.change('disable',M);let writes=0;
 await assert.rejects(f.host.commitResult(r,()=>++writes),/PLUGIN_NOT_AUTHORIZED/);assert.equal(writes,0);
 await f.change('enable',M);const fresh=await f.invoke(M);assert.equal(await f.host.commitResult(fresh,()=>++writes),1);
 await assert.rejects(f.host.commitResult(fresh,()=>++writes),/PLUGIN_RESULT_INVALID/);assert.equal(writes,1);
});
test('failure, timeout and forged outputs remain isolated by plugin',async t=>{
 for(const invoke of [()=>{throw Error('private detail');},()=>({kind:'model-action',action:'select-model',label:'选择模型',key:'secret'}),()=>new Promise(()=>{})]){
  const f=await fixture(t,{timeoutMs:20,override:{[M]:{invoke}}});await f.ready(B);await f.ready(M);
  await assert.rejects(f.invoke(M,'ui.model-action',{}),/PLUGIN_EXECUTION_FAILED|PLUGIN_OUTPUT_INVALID|PLUGIN_TIMEOUT/);
  assert.equal((await f.host.sessionStatus('one',B)).lastError,null);assert.equal((await f.invoke(B)).proposal.kind,'branch-request');
 }
});
test('unavailable release quarantines that package but permits the other',async t=>{
 const f=await fixture(t,{override:{[M]:{error:'PLUGIN_ARTIFACT_MISMATCH'}}});await f.ready(B);
 assert.equal((await f.host.catalog()).plugins.find(p=>p.id===M).lastError,'PLUGIN_ARTIFACT_MISMATCH');
 await assert.rejects(f.change('install',M),/PLUGIN_PACKAGE_UNAVAILABLE/);assert.equal((await f.invoke(B)).proposal.kind,'branch-request');
});
test('global revision serializes mutations and operation IDs cannot cross plugins',async t=>{
 const f=await fixture(t),p=await f.payload('install',B),q=await f.payload('install',M);
 const results=await Promise.allSettled([f.host.change('install',p),f.host.change('install',q)]);
 assert.equal(results.filter(r=>r.status==='fulfilled').length,1);assert.match(results.find(r=>r.status==='rejected').reason.code,/PLUGIN_REGISTRY_CONFLICT/);
 await assert.rejects(f.host.change('install',{...await f.payload('install',M),operationId:p.operationId}),/PLUGIN_OPERATION_COLLISION/);
 await f.change('install',M);const grant=await f.payload('enable',M);await f.host.change('enable',grant);await f.change('disable',M);
 assert.equal((await f.host.change('enable',grant)).replayed,true);assert.equal((await f.host.sessionStatus('one',M)).enabled,false);
});
test('legacy read preserves bytes/grants/receipts; first write keeps exact v1 backup',async t=>{
 const f=await fixture(t);await f.ready(B);await f.host.close();const current=JSON.parse(await readFile(join(f.directory,'registry.json'),'utf8'));
 const legacy={format:1,revision:current.revision,...current.plugins[B],operations:current.operations},bytes=JSON.stringify(legacy,null,1)+'\n';
 await writeFile(join(f.directory,'registry.json'),bytes);await f.reopen();assert.equal((await f.host.sessionStatus('one',B)).enabled,true);
 assert.equal(await readFile(join(f.directory,'registry.json'),'utf8'),bytes);assert.equal((await f.host.sessionStatus('one',M)).enabled,false);
 await f.change('install',M);assert.equal(await readFile(join(f.directory,'registry.v1.json'),'utf8'),bytes);
 const updated=JSON.parse(await readFile(join(f.directory,'registry.json'),'utf8'));assert.equal(updated.format,2);assert.deepEqual(updated.plugins[B],current.plugins[B]);
 for(const [op,value] of Object.entries(legacy.operations))assert.deepEqual(updated.operations[op],value);
 await f.reopen();assert.equal((await f.host.sessionStatus('one',B)).enabled,true);assert.equal((await f.host.sessionStatus('one',M)).enabled,false);
});
test('legacy backup conflict fails closed without changing bytes or other grants',async t=>{
 const f=await fixture(t);await f.ready(B);await f.host.close();const c=JSON.parse(await readFile(join(f.directory,'registry.json'),'utf8'));
 const bytes=JSON.stringify({format:1,revision:c.revision,...c.plugins[B],operations:c.operations});await writeFile(join(f.directory,'registry.json'),bytes);await writeFile(join(f.directory,'registry.v1.json'),'different snapshot');await f.reopen();
 await assert.rejects(f.change('install',M),/PLUGIN_PERSIST_FAILED/);assert.equal(await readFile(join(f.directory,'registry.json'),'utf8'),bytes);assert.equal((await f.host.sessionStatus('one',B)).enabled,true);assert.equal((await f.host.sessionStatus('one',M)).installed,false);
});
test('model proposals bind exact input/revision/session; no raw history exposed',async t=>{
 let received;const adapter=await loadReviewedCatalog(M),f=await fixture(t,{override:{[M]:{invoke:args=>{received=args;return adapter.invoke(args);}}}});await f.ready(M);
 const result=await f.invoke(M);assert.deepEqual(Object.keys(received.session).sort(),['completedTurns','id','revision']);
 await assert.rejects(f.host.validateResult({...result,pluginId:B}),/PLUGIN_RESULT_INVALID/);
 f.sessions.get('one').revision++;await assert.rejects(f.host.commitResult(result,()=>assert.fail('must not persist')),/PLUGIN_RESULT_STALE/);
 f.sessions.get('one').pending=true;await assert.rejects(f.invoke(M),/PLUGIN_SESSION_BUSY/);await f.change('disable',M);
});
