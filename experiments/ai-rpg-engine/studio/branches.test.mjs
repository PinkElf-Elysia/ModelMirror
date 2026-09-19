import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,rm,writeFile,readdir,readFile} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {VersionedSessionStore} from './versioned-store.mjs';
import {BranchArchive} from './branch-archive.mjs';
import {canonical,sha} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/plugin-b2-tests/',import.meta.url));await mkdir(root,{recursive:true});
const descriptor={format:1,cardId:'synthetic',version:'test-v1'};
const runtime={descriptor,hash:sha(canonical(descriptor)),verify:async()=>{}};
const body=n=>'原始内容 '+n+'\n\t<details><summary>资料</summary>甲 & 乙</details>';
async function fixture(t){const dir=await mkdtemp(join(root,'store-'));t.after(async()=>{assert.equal(dirname(dir),root.replace(/[\\/]$/,''));await rm(dir,{recursive:true,force:true});});let calls=0;const store=new VersionedSessionStore(dir,async()=>body(++calls),null,runtime);await store.init();let s=await store.create({characterText:'姓名：虚构验收者',world:'表世界',params:{},mode:'offline'});const plugins={invoke:async()=>({}),commitResult:async(_,publish)=>publish()};return {dir,store,s,plugins,get calls(){return calls;},async turn(input='开始'){s=await store.send(s.id,{input,requestId:'r-'+(s.revision+1),revision:s.revision});this.s=s;return s;},branch(turn=1,name='新路线',operationId='branch-one'){return store.createBranch(s.id,{turn,name,operationId,expectedSessionRevision:s.revision},plugins);}};}
test('complete prefix snapshot remains exact while parent and child continue independently; empty executable requests',async t=>{
 const f=await fixture(t);await f.turn();await f.turn('父路线后来发生的事');const parentBefore=await f.store.read(f.s.id),b=await f.branch();
 assert.equal(f.calls,2);assert.deepEqual(b.session.history,parentBefore.history.slice(0,2));assert.deepEqual(b.session.turns,parentBefore.turns.slice(0,1));assert.deepEqual(b.session.requests,{});assert.deepEqual(b.session.params,parentBefore.params);assert.deepEqual(b.session.runtime,parentBefore.runtime);
 assert.deepEqual(await f.store.read(f.s.id),parentBefore);assert.equal(b.session.provenance.requestOrigins[0].sessionId,f.s.id);
 await assert.rejects(f.store.send(b.session.id,{requestId:'r-1',revision:1,input:'继承ID'}),/BRANCH_INHERITED_REQUEST_ID/);
 const child=await f.store.send(b.session.id,{requestId:'child-new',revision:1,input:'走另一条路'});assert.equal(child.history.length,4);assert.ok(!child.history[2].content.includes('父路线后来发生的事'));
 const snapshot=(await f.store.archive.read(child.id)).snapshot;await f.turn('父路线再继续');assert.deepEqual((await f.store.archive.read(child.id)).snapshot,snapshot);assert.equal((await f.store.read(child.id)).history.length,4);
 const list=await f.store.list();assert.equal(list.length,2);assert.equal(list.find(s=>s.id===child.id).parentId,f.s.id);
});
test('duplicate operations recover after restart; changed input collides; receipts do not reexecute',async t=>{
 const f=await fixture(t);await f.turn();const first=await f.branch();const second=await f.branch();assert.equal(second.replayed,true);assert.equal(second.session.id,first.session.id);
 await assert.rejects(f.branch(1,'另一名字'),/BRANCH_OPERATION_COLLISION/);const next=new VersionedSessionStore(f.dir,async()=>body('new'),null,runtime);await next.init();
 const replay=await next.createBranch(f.s.id,{turn:1,name:'新路线',operationId:'branch-one',expectedSessionRevision:1},{invoke:()=>{throw Error('must not invoke');}});assert.equal(replay.replayed,true);assert.equal((await next.list()).length,2);
});
test('empty/incomplete/bad-hash/pending/stale revision and invalid turn reject without creating branch',async t=>{
 const f=await fixture(t);await assert.rejects(f.branch(),/BRANCH_TURN_INVALID/);await f.turn();await assert.rejects(f.branch(2),/BRANCH_TURN_INVALID/);
 await assert.rejects(f.store.createBranch(f.s.id,{operationId:'stale',turn:1,name:'x',expectedSessionRevision:0},f.plugins),/BRANCH_SESSION_CONFLICT/);
 for(const mutate of [s=>s.history.pop(),s=>s.turns[0].rawHash='bad',s=>s.requests.unknown={status:'pending'}]){const s=structuredClone(f.s);mutate(s);await f.store.write(s);await assert.rejects(f.branch(),/BRANCH_HISTORY_INVALID|BRANCH_UNCONFIRMED_REQUEST/);}
 assert.equal((await f.store.archive.list()).length,0);
});
test('legacy and incompatible versions remain readable but cannot continue or create branches',async t=>{
 const f=await fixture(t);await f.turn();const legacy=structuredClone(f.s);delete legacy.runtime;await f.store.write(legacy);assert.equal(f.store.status(await f.store.read(f.s.id)).code,'RUNTIME_UNVERIFIED_LEGACY');
 await assert.rejects(f.branch(),/RUNTIME_UNVERIFIED_LEGACY/);await assert.rejects(f.store.send(f.s.id,{requestId:'n',revision:1,input:'继续'}),/RUNTIME_UNVERIFIED_LEGACY/);
 await f.store.write(f.s);const other={descriptor:{format:1,version:'v2'},hash:sha(canonical({format:1,version:'v2'})),verify:async()=>{}};const next=new VersionedSessionStore(f.dir,async()=>'',null,other);await next.init();assert.equal((await next.read(f.s.id)).history.length,2);await assert.rejects(next.createBranch(f.s.id,{operationId:'n',turn:1,name:'x',expectedSessionRevision:1},f.plugins),/RUNTIME_VERSION_MISMATCH/);
 const tampered=structuredClone(f.s);tampered.params.temperature=0;await f.store.write(tampered);await assert.rejects(f.branch(),/RUNTIME_BINDING_INVALID/);
 await assert.rejects(f.store.create({characterText:'x',world:'表世界',mode:'offline',runtime:{}}),/SESSION_CONFIGURATION_INVALID/);
});
test('failed publication leaves no visible half branch, retry succeeds, staged crash leftovers ignored',async t=>{
 const f=await fixture(t);await f.turn();f.plugins.commitResult=async()=>{throw Error('injected publish failure');};await assert.rejects(f.branch(),/injected publish failure/);assert.equal((await f.store.list()).length,1);assert.deepEqual(await readdir(f.store.archive.directory),[]);
 await writeFile(join(f.store.archive.directory,'interrupted.tmp'),'partial');f.plugins.commitResult=async(_,publish)=>publish();const b=await f.branch();assert.equal(b.replayed,false);assert.equal((await f.store.list()).length,2);
 const raw=await readFile(f.store.archive.path(b.session.id),'utf8'),e=JSON.parse(raw);e.snapshot.history[0].content+='corrupt';await writeFile(f.store.archive.path(b.session.id),JSON.stringify(e));await assert.rejects(f.store.read(b.session.id),/BRANCH_ARCHIVE_INVALID/);
});
test('branch staging excludes concurrent branch/send; generation excludes branch and cancellation keeps history',async t=>{
 const f=await fixture(t);await f.turn();let release,entered;const started=new Promise(r=>entered=r);f.plugins.commitResult=async(_,publish)=>{entered();await new Promise(r=>release=r);return publish();};const work=f.branch();await started;
 await assert.rejects(f.branch(1,'second','second'),/SESSION_BUSY/);await assert.rejects(f.store.send(f.s.id,{requestId:'n',revision:1,input:'继续'}),/SESSION_BUSY/);release();await work;
 let finish,called;const began=new Promise(r=>called=r);f.store.generate=async()=>{called();await new Promise(r=>finish=r);return 'late';};const generation=f.store.send(f.s.id,{requestId:'new',revision:1,input:'继续'});await began;await assert.rejects(f.branch(1,'third','third'),/SESSION_BUSY/);f.store.cancel(f.s.id);finish();assert.equal((await generation).history.length,2);
});
test('generic snapshot preserves non-earth configuration with no fabricated role fields or budget state',async t=>{
 const f=await fixture(t),archive=new BranchArchive(join(f.dir,'generic'));await archive.init();const source={id:'synthetic',name:'Synthetic',created:'now',revision:1,mode:'mock',configuration:{spaceship:'Orion'},budget:{remaining:99},plugins:{enabled:true},runtime:{hash:'test'},history:[{role:'user',content:'\tLaunch\n'},{role:'assistant',content:'Orbit'}],turns:[{requestId:'a',raw:'Orbit',rawHash:sha('Orbit')}],requests:{a:{status:'complete'}}};
 const e=archive.prepare(source,{sessionId:source.id,operationId:'first',expectedSessionRevision:1,turn:1,name:'North'});await archive.publish(e,p=>p());const saved=await archive.read(e.session.id);assert.deepEqual(saved.session.configuration,{spaceship:'Orion'});assert.equal(saved.session.characterText,undefined);assert.equal(saved.session.budget,undefined);assert.equal(saved.session.plugins,undefined);assert.deepEqual(saved.session.requests,{});assert.deepEqual(saved.snapshot.history,source.history);
});

test('runtime files drift fails before dispatch and before creating new sessions',async t=>{
 const f=await fixture(t);await f.turn();const runtimeBefore=f.store.runtime;f.store.runtime={...runtimeBefore,verify:async()=>{throw Error('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');}};
 await assert.rejects(f.store.send(f.s.id,{requestId:'fresh',revision:1,input:'继续'}),/RUNTIME_FILES_CHANGED_RESTART_REQUIRED/);
 await assert.rejects(f.branch(),/RUNTIME_FILES_CHANGED_RESTART_REQUIRED/);
 await assert.rejects(f.store.create({characterText:'新测试',world:'表世界',params:{},mode:'offline'}),/RUNTIME_FILES_CHANGED_RESTART_REQUIRED/);assert.equal(f.calls,1);
});

test('lost response after atomic publication replays the single committed branch after restart',async t=>{
 const f=await fixture(t);await f.turn();f.plugins.commitResult=async(_,publish)=>{await publish();throw Error('injected lost reply after commit');};
 await assert.rejects(f.branch(),/injected lost reply/);assert.equal((await f.store.list()).length,2);
 const next=new VersionedSessionStore(f.dir,async()=>'',null,runtime);await next.init();const recovered=await next.createBranch(f.s.id,{operationId:'branch-one',expectedSessionRevision:1,turn:1,name:'新路线'},{invoke:()=>{throw Error('must not reexecute');}});
 assert.equal(recovered.replayed,true);assert.equal((await next.list()).length,2);assert.equal(f.calls,1);
});
