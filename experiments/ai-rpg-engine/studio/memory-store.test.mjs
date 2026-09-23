import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {VersionedSessionStore} from './versioned-store.mjs';
import {atomicJson} from './branch-archive.mjs';
import {initialMemoryState,requireMemoryState,memoryOperations,sealMemory} from './memory-state.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {MEMORY_PALACE_ID as M,ROLLING_SUMMARY_ID as R,loadReviewedPlugins,loadReviewedCatalog,verifyPackage,sha} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/memory-palace-b1/store/',import.meta.url));await mkdir(root,{recursive:true});
const value={name:'合成对象',keywords:['对象'],match:'any',roles:['user','assistant'],content:'只发生了会面，未兑现约定。',enabled:true};
// Only the trusted compatibility projection is a fixture. read, locks and atomic persistence are existing host code.
class FixtureStore extends VersionedSessionStore {
 async write(s){return atomicJson(this.path(s.id),s);}
 async pluginSession(id){const s=await this.read(id);return {id,cardId:'earth',revision:s.revision,completedTurns:s.turns.length,resourceHash:sha('B1-test-runtime'),memoryPalaceCompatible:!!s.memoryPalace,rollingSummaryCompatible:true,busy:this.running.has(id),pending:!!s.memoryPalace?.pending||Object.values(s.requests).some(x=>x.status==='pending')};}
}
async function fixture(t,{delayed=false}={}){
 const directory=await mkdtemp(join(root,'case-')),store=new FixtureStore(join(directory,'sessions'),()=>assert.fail('No dispatch allowed'));
 await store.init();const session={id:'new',revision:0,history:[],turns:[],requests:{},memoryPalace:initialMemoryState()};await store.write(session);await store.write({id:'old',revision:0,history:[],turns:[],requests:{}});
 let resume,started;const entered=new Promise(r=>started=r),gate=new Promise(r=>resume=r);
 const loader=async()=>{const entries=await loadReviewedPlugins({rollingSummary:true,memoryPalace:true});if(delayed){const m=entries.find(e=>e.manifest.id===M),original=m.invoke;m.invoke=async args=>{started();await gate;return original(args);};}return entries;};
 const host=await createPluginService({directory:join(directory,'plugins'),lookupSession:id=>store.pluginSession(id),loadCatalog:loader});t.after(()=>host.close());
 const change=async(action,sessionId,pluginId=M,extra={})=>{const c=await host.catalog(),p=c.plugins.find(x=>x.id===pluginId);return host.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(sessionId?{sessionId,expectedSessionRevision:(await store.read(sessionId)).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{}),...extra});};
 const operations=memoryOperations(store,host),input=async(change={action:'create',value})=>{const s=await store.read('new');return {operationId:randomUUID(),expectedSessionRevision:s.revision,expectedMemoryRevision:s.memoryPalace.revision,change};};
 return {directory,store,host,change,operations,input,entered,resume};
}
test('fifth plugin opt-in, package binding, install vs enable, permission and legacy rejection',async t=>{
 assert.equal((await loadReviewedPlugins()).length,3);assert.equal((await loadReviewedPlugins({rollingSummary:true})).length,4);
 const f=await fixture(t);assert.equal((await f.host.catalog()).plugins.length,5);const p=await loadReviewedCatalog(M);
 assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify(p.manifest)),Buffer.from('altered')),/PLUGIN_ARTIFACT_MISMATCH/);
 await f.change('install');assert.equal((await f.host.memoryAuthorization('new')).enabled,false);
 await assert.rejects(f.operations.save('new',await f.input()),/PLUGIN_NOT_AUTHORIZED/);
 await assert.rejects(f.change('enable','old'),/MEMORY_RUNTIME_INCOMPATIBLE/);
 await assert.rejects(f.change('enable','new',M,{permissions:[]}),/PLUGIN_PERMISSION_MISMATCH/);
 await assert.rejects(f.change('enable','new',M,{artifactSha256:sha('wrong')}),/PLUGIN_VERSION_MISMATCH/);
 await f.change('enable','new');await assert.rejects(f.host.invoke({pluginId:M,sessionId:'new',capability:'network.fetch'}),/PLUGIN_CAPABILITY_DENIED/);
});
test('persistent manual revision, duplicate replay, collision, restart and source retention',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','new');const input=await f.input();const first=await f.operations.save('new',input),saved=await readFile(f.store.path('new'));
 assert.equal(first.session.memoryPalace.entries[0].protected,true);assert.equal((await f.operations.save('new',input)).replayed,true);assert.deepEqual(await readFile(f.store.path('new')),saved);
 await assert.rejects(f.operations.save('new',{...input,change:{action:'create',value:{...value,name:'different'}}}),/MEMORY_OPERATION_COLLISION/);
 const reopened=new FixtureStore(f.store.directory,()=>assert.fail('dispatch'));const read=await reopened.read('new');requireMemoryState(read);assert.equal(read.memoryPalace.revision,1);assert.deepEqual(read.history,[]);
 const id=read.memoryPalace.entries[0].id;await f.operations.save('new',await f.input({action:'update',id,baseVersion:1,value:{...value,content:'仍然未兑现。'}}));const updated=await f.store.read('new');assert.equal(updated.memoryPalace.entries[0].revisions[0].value.content,value.content);
});
test('late proposal after revoke cannot publish; other plugin grant remains',async t=>{
 const f=await fixture(t,{delayed:true});for(const id of [M,R]){await f.change('install',null,id);await f.change('enable','new',id);}
 const promise=f.operations.save('new',await f.input());const rejected=assert.rejects(promise,/PLUGIN_NOT_AUTHORIZED/);await f.entered;await f.change('disable','new');f.resume();await rejected;
 assert.equal((await f.store.read('new')).memoryPalace.entries.length,0);assert.equal((await f.host.summaryAuthorization('new')).enabled,true);
});
test('uninstall retains entries, reinstall needs new grant, old ticket unusable',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','new');await f.operations.save('new',await f.input());const bytes=await readFile(f.store.path('new'));
 const ticket=await f.host.invoke({pluginId:M,sessionId:'new',capability:'session.memory.request'});await f.change('uninstall');await f.change('install');assert.equal((await f.host.memoryAuthorization('new')).enabled,false);await assert.rejects(f.host.commitResult(ticket,()=>assert.fail('stale commit')),/PLUGIN_NOT_AUTHORIZED/);assert.deepEqual(await readFile(f.store.path('new')),bytes);
});
test('intent persisted but final write fails: query recovers original operation without replay',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','new');const input=await f.input(),write=f.store.write.bind(f.store);let n=0;
 f.store.write=async s=>{if(++n===2)throw Error('injected disk failure');return write(s);};await assert.rejects(f.operations.save('new',input),/injected disk failure/);f.store.write=write;
 const pending=await f.store.read('new');assert.equal(pending.memoryPalace.entries.length,0);assert.equal(pending.memoryPalace.pending,input.operationId);requireMemoryState(pending);
 await assert.rejects(f.operations.save('new',await f.input()),/MEMORY_OPERATION_UNCONFIRMED/);
 assert.equal((await f.operations.save('new',input)).operation.status,'pending');assert.equal((await f.operations.recover('new',input.operationId)).status,'not-applied');assert.equal((await f.operations.save('new',input)).operation.status,'not-applied');assert.equal((await f.store.read('new')).revision,0);
});
test('write completed but response lost: same operation query discovers complete without duplicate',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','new');const input=await f.input(),write=f.store.write.bind(f.store);let n=0;
 f.store.write=async s=>{await write(s);if(++n===2)throw Error('response lost');};await assert.rejects(f.operations.save('new',input),/response lost/);f.store.write=write;
 assert.equal((await f.operations.recover('new',input.operationId)).status,'complete');assert.equal((await f.operations.save('new',input)).replayed,true);assert.equal((await f.store.read('new')).memoryPalace.entries.length,1);
});
test('multi-tab stale revision, shared locks, generation and unresolved request cannot modify memory',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','new');const stale=await f.input();await f.operations.save('new',await f.input());await assert.rejects(f.operations.save('new',stale),/MEMORY_CONFIGURATION_CONFLICT/);
 f.store.running.set('new',true);await assert.rejects(f.operations.save('new',await f.input()),/SESSION_BUSY/);f.store.running.clear();
 const s=await f.store.read('new');s.requests.unknown={status:'pending'};await f.store.write(s);await assert.rejects(f.operations.save('new',await f.input()),/MEMORY_UNCONFIRMED_REQUEST/);
});
test('corrupt state and unknown operation fields fail closed; no character field dependency',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','new');await assert.rejects(f.operations.save('new',{...await f.input(),system:'not accepted'}),/MEMORY_OPERATION_INVALID/);
 const s=await f.store.read('new');s.memoryPalace.processedThrough=1;s.memoryPalace=sealMemory(s.memoryPalace);await f.store.write(s);await assert.rejects(f.operations.save('new',await f.input()),/MEMORY_STATE_INVALID/);
});

test('simultaneous saves share the session lock; maximum Unicode payload is accepted',async t=>{
 const f=await fixture(t);await f.change('install');await f.change('enable','new');
 const max={...value,name:'😀'.repeat(120),content:'😀'.repeat(2000),keywords:Array.from({length:20},(_,i)=>'😀'.repeat(78)+i)};
 const a=await f.input({action:'create',value:max}),b=await f.input();const results=await Promise.allSettled([f.operations.save('new',a),f.operations.save('new',b)]);
 assert.equal(results.filter(x=>x.status==='fulfilled').length,1);assert.equal(results.find(x=>x.status==='rejected').reason.code,'SESSION_BUSY');assert.equal((await f.store.read('new')).memoryPalace.entries.length,1);
 await f.host.close();const reopened=await createPluginService({directory:join(f.directory,'plugins'),lookupSession:id=>f.store.pluginSession(id),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true,memoryPalace:true})});t.after(()=>reopened.close());assert.equal((await reopened.memoryAuthorization('new')).enabled,true);
});
