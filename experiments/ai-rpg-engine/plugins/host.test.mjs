import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {resolve,join,dirname} from 'node:path';
import {randomUUID} from 'node:crypto';
import {createPluginService} from './host.mjs';
import {loadReviewedCatalog,sha} from './catalog.mjs';
import {SessionStore} from '../card-replica/lib/store.mjs';
const root=resolve(new URL('../.rpg04-work/plugin-b1-tests/',import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1'));
await mkdir(root,{recursive:true});
async function fixture(t,{invoke,timeoutMs=1000}={}){
 const directory=await mkdtemp(join(root,'host-'));
 const sessions=new Map([['one',{id:'one',cardId:'earth',revision:0,completedTurns:2,resourceHash:sha('one'),busy:false,pending:false}],['two',{id:'two',cardId:'earth',revision:0,completedTurns:1,resourceHash:sha('two'),busy:false,pending:false}]]);
 const entry=await loadReviewedCatalog();let override=invoke;
 const options={directory,lookupSession:async id=>structuredClone(sessions.get(id)),loadCatalog:async()=>({...entry,invoke:override||entry.invoke}),timeoutMs};
 let host=await createPluginService(options);
 t.after(async()=>{await host.close();assert.equal(dirname(directory),root);await rm(directory,{recursive:true,force:true});});
 const f={directory,sessions,get host(){return host;},async reopen(){await host.close();host=await createPluginService(options);},setInvoke(v){override=v;},async payload(action,sessionId='one'){
  const c=await host.catalog();return {operationId:randomUUID(),pluginId:entry.manifest.id,version:entry.manifest.version,artifactSha256:entry.artifactSha256,manifestSha256:entry.manifestSha256,expectedRegistryRevision:c.revision,...(['enable','disable'].includes(action)?{sessionId,expectedSessionRevision:sessions.get(sessionId).revision}:{}),...(action==='enable'?{permissions:entry.manifest.permissions.slice()}: {})};
 },async change(action,sessionId){return host.change(action,await f.payload(action,sessionId));},async ready(){await f.change('install');await f.change('enable');}};return f;
}
const action=host=>host.invoke({sessionId:'one',capability:'ui.message-action'});
test('installation is explicit, per-session enable is separate, restart restores exact grants',async t=>{
 const f=await fixture(t);assert.equal((await f.host.catalog()).plugins[0].installed,false);
 await assert.rejects(action(f.host),/PLUGIN_NOT_INSTALLED/);await f.change('install');assert.equal((await f.host.sessionStatus('one')).enabled,false);
 await assert.rejects(action(f.host),/PLUGIN_NOT_AUTHORIZED/);await f.change('enable');assert.equal((await action(f.host)).proposal.label,'分支');assert.equal((await f.host.sessionStatus('two')).enabled,false);
 await f.reopen();assert.equal((await f.host.sessionStatus('one')).enabled,true);
});
test('wrong version/hash, arbitrary address, unsupported card and incomplete permissions rejected',async t=>{
 const f=await fixture(t);const p=await f.payload('install');
 for(const patch of [{version:'2.0.0'},{artifactSha256:'a'.repeat(64)},{manifestSha256:'b'.repeat(64)},{url:'https://arbitrary.invalid'}])await assert.rejects(f.host.change('install',{...p,...patch}),/PLUGIN_VERSION_MISMATCH|PLUGIN_OPERATION_INVALID/);
 await f.change('install');await assert.rejects(f.host.change('enable',{...await f.payload('enable'),permissions:['ui.contribute']}),/PLUGIN_PERMISSION_MISMATCH/);
 f.sessions.get('one').cardId='rpg05';await assert.rejects(f.change('enable'),/PLUGIN_CARD_INCOMPATIBLE/);
});
test('operation replay is idempotent and cannot silently re-enable a revoked plugin',async t=>{
 const f=await fixture(t);await f.change('install');const p=await f.payload('enable');const r=await f.host.change('enable',p);assert.equal((await f.host.change('enable',p)).revision,r.revision);
 await assert.rejects(f.host.change('enable',{...p,sessionId:'two'}),/PLUGIN_OPERATION_COLLISION/);await f.change('disable');assert.equal((await f.host.change('enable',p)).replayed,true);assert.equal((await f.host.sessionStatus('one')).enabled,false);
});
test('concurrent mutations require latest registry revision',async t=>{
 const f=await fixture(t),p=await f.payload('install'),q={...p,operationId:randomUUID()};
 const result=await Promise.allSettled([f.host.change('install',p),f.host.change('install',q)]);assert.equal(result.filter(r=>r.status==='fulfilled').length,1);assert.match(result.find(r=>r.status==='rejected').reason.message,/PLUGIN_REGISTRY_CONFLICT/);
});
test('uninstall invalidates every grant; reinstall never silently restores consent',async t=>{
 const f=await fixture(t);await f.ready();await f.change('enable','two');const old=await action(f.host);
 await f.change('uninstall');assert.equal((await f.host.sessionStatus('one')).enabled,false);await assert.rejects(f.host.validateResult(old));
 await f.change('install');assert.equal((await f.host.sessionStatus('two')).enabled,false);await f.reopen();assert.equal((await f.host.sessionStatus('one')).enabled,false);
});
test('resource changes invalidate authorization; revision changes invalidate results',async t=>{
 const f=await fixture(t);await f.ready();const r=await action(f.host);f.sessions.get('one').revision++;await assert.rejects(f.host.validateResult(r),/PLUGIN_RESULT_STALE/);
 f.sessions.get('one').resourceHash=sha('changed');await assert.rejects(action(f.host),/PLUGIN_NOT_AUTHORIZED/);
});
test('revocation and re-enable invalidate old results; forged proposal rejected',async t=>{
 const f=await fixture(t);await f.ready();const r=await action(f.host);await assert.rejects(f.host.validateResult({...r,proposal:{...r.proposal,label:'forged'}}),/PLUGIN_RESULT_INVALID/);
 await f.change('disable');await f.change('enable');await assert.rejects(f.host.validateResult(r),/PLUGIN_RESULT_STALE/);
});
test('late adapter result after disable never becomes usable',async t=>{
 let release,began;const started=new Promise(r=>began=r);const f=await fixture(t,{invoke:async()=>{began();return new Promise(r=>release=r);}});await f.ready();
 const pending=action(f.host);await started;await f.change('disable');release({kind:'message-action',action:'branch',label:'分支',available:true});await assert.rejects(pending,/PLUGIN_NOT_AUTHORIZED/);
});
test('exceptions, invalid output and timeout isolate the plugin without leaking details',async t=>{
 const f=await fixture(t,{invoke:()=>{throw Error('private detail');}});await f.ready();await assert.rejects(action(f.host),/PLUGIN_EXECUTION_FAILED/);assert.equal((await f.host.sessionStatus('one')).lastError,'PLUGIN_EXECUTION_FAILED');
 const bad=await fixture(t,{invoke:()=>({kind:'message-action',action:'branch',label:'分支',available:true,script:'bad'})});await bad.ready();await assert.rejects(action(bad.host),/PLUGIN_OUTPUT_INVALID/);
 let release;const timeout=await fixture(t,{timeoutMs:10,invoke:()=>new Promise(r=>release=r)});await timeout.ready();await assert.rejects(action(timeout.host),/PLUGIN_TIMEOUT/);release({});assert.equal((await timeout.host.sessionStatus('one')).lastError,'PLUGIN_TIMEOUT');
});
test('busy and unresolved sessions block activation and invocation, but allow revocation',async t=>{
 const f=await fixture(t);await f.ready();f.sessions.get('one').busy=true;await assert.rejects(action(f.host),/PLUGIN_SESSION_BUSY/);await f.change('disable');await assert.rejects(f.change('enable'),/PLUGIN_SESSION_BUSY/);
 f.sessions.get('one').busy=false;f.sessions.get('one').pending=true;await assert.rejects(f.change('enable'),/PLUGIN_SESSION_BUSY/);
});
test('failed persistence grants nothing; second owner rejected; corrupt state fails closed',async t=>{
 const f=await fixture(t);await assert.rejects(createPluginService({directory:f.directory,lookupSession:()=>{}}),/PLUGIN_STORE_ALREADY_OWNED/);
 await mkdir(join(f.directory,'registry.json'));await assert.rejects(f.change('install'),/PLUGIN_PERSIST_FAILED/);assert.equal((await f.host.catalog()).plugins[0].installed,false);
 await rm(join(f.directory,'registry.json'),{recursive:true});await writeFile(join(f.directory,'registry.json'),'broken');await assert.rejects(f.reopen(),/PLUGIN_STORE_INVALID/);
});
test('zero-plugin and plugin failure leave raw core history and original assembly unchanged',async t=>{
 const f=await fixture(t,{invoke:()=>{throw Error('failure');}}),payloads=[];
 const store=new SessionStore(join(f.directory,'sessions'),async args=>{payloads.push(args.messages);return '原始正文\n<details>资料</details>';});await store.init();
 const s=await store.create({characterText:'姓名：虚构角色',world:'表世界',params:{},mode:'offline'});
 await store.send(s.id,{input:'开始',requestId:'first',revision:0});const before=await store.read(s.id);
 await f.ready();await assert.rejects(action(f.host),/PLUGIN_EXECUTION_FAILED/);assert.deepEqual(await store.read(s.id),before);
 await store.send(s.id,{input:'继续',requestId:'second',revision:1});const after=await store.read(s.id);assert.equal(after.turns.length,2);assert.deepEqual(payloads[1].slice(1,3),before.history);assert.equal(payloads[0].length,2);
});
