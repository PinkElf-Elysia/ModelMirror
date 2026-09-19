import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {ModelSessionStore} from './model-session-store.mjs';
import {VersionedSessionStore} from './versioned-store.mjs';
import {createPluginService} from '../plugins/host.mjs';
import {MODEL_SELECTOR_ID,PLUGIN_ID,canonical,sha} from '../plugins/catalog.mjs';
import {models} from './provider.mjs';
import {modelRuntime,LEGACY_RUNTIME_HASH} from './model-runtime.mjs';
import {earthRuntime} from './runtime-binding.mjs';

const root=fileURLToPath(new URL('../.rpg04-work/model-selector-b3/tests/',import.meta.url));await mkdir(root,{recursive:true});
const A={selectionId:'model-a',selectionRevision:'catalog-a',model:'synthetic/A',available:true};
const B={selectionId:'model-b',selectionRevision:'catalog-b',model:'synthetic/B',available:true};
async function fixture(t){
 const directory=await mkdtemp(join(root,'state-'));let options=[A,B],calls=0,wait=null;
 const control={catalog:async()=>{if(wait)await wait;return structuredClone(options);},generate:()=>assert.fail('no real calls in state batch')};
 const store=new ModelSessionStore(join(directory,'sessions'),async()=>{calls++;return '原文\n\t'+calls;},null,{control});await store.init();
 const plugins=await createPluginService({directory:join(directory,'plugins'),lookupSession:async id=>{const s=await store.read(id);return {id,cardId:'earth',revision:s.revision,completedTurns:s.turns.length,resourceHash:sha(s.runtime.hash),busy:store.running.has(id),pending:Object.values(s.requests).some(r=>r.status==='pending')};}});
 t.after(()=>plugins.close());
 const f={directory,store,plugins,control,get calls(){return calls;},setOptions(x){options=x;},setWait(x){wait=x;},async create(){return store.create({characterText:'姓名：虚构角色',world:'表世界',mode:'offline',params:{max_tokens:16384}});},async change(action,id=MODEL_SELECTOR_ID,s){const c=await plugins.catalog(),p=c.plugins.find(x=>x.id===id);return plugins.change(action,{operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async grant(s,id=MODEL_SELECTOR_ID){const p=(await plugins.catalog()).plugins.find(x=>x.id===id);if(!p.installed)await this.change('install',id);await this.change('enable',id,s);}};return f;
}
const operation=(s,model=A,operationId=randomUUID())=>({operationId,expectedSessionRevision:s.revision,selectionRevision:s.modelState.revision,selectionId:model.selectionId,catalogRevision:model.selectionRevision});
const send=(store,s,input='继续',requestId=randomUUID())=>store.send(s.id,{input,requestId,revision:s.revision,expectedSelectionRevision:s.modelState.revision});

test('zero-plugin v2 offline path, exact version binding and independent selection revision',async t=>{
 const f=await fixture(t);let s=await f.create();assert.equal(s.runtime.format,2);assert.equal(s.runtime.hash,modelRuntime.hash);assert.equal(s.modelState.revision,0);
 s=await send(f.store,s);assert.equal(s.turns.length,1);assert.equal(s.turns[0].model.requestedModel,models.earth.model);assert.equal(s.turns[0].model.actualModel,null);
 await assert.rejects(f.store.catalog(s.id,f.plugins),/PLUGIN_NOT_INSTALLED/);
 await f.change('install');await assert.rejects(f.store.select(s.id,operation(s),f.plugins),/PLUGIN_NOT_AUTHORIZED/);
 await f.grant(s);const selected=await f.store.select(s.id,operation(s),f.plugins);s=selected.session;
 assert.equal(s.revision,1);assert.equal(s.modelState.revision,1);assert.equal(s.modelState.current.model,A.model);assert.equal(f.calls,1);
 assert.equal(s.turns[0].model.requestedModel,models.earth.model);
});

test('selection atomic/idempotent after restart; changed operation and stale revision rejected',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);const op=operation(s,A,'operation');s=(await f.store.select(s.id,op,f.plugins)).session;
 await f.change('disable',MODEL_SELECTOR_ID,s);const replay=await f.store.select(s.id,op,f.plugins);assert.equal(replay.replayed,true);assert.equal(replay.operation.revision,1);
 const restarted=new ModelSessionStore(f.store.directory,async()=>'',null,{control:f.control});await restarted.init();
 assert.equal((await restarted.select(s.id,op,f.plugins)).replayed,true);
 await assert.rejects(restarted.select(s.id,{...op,selectionId:B.selectionId},f.plugins),/MODEL_OPERATION_COLLISION/);
 await f.grant(s);await assert.rejects(restarted.select(s.id,{...op,operationId:'stale'},f.plugins),/MODEL_SELECTION_CONFLICT/);
 assert.equal((await f.store.read(s.id)).modelState.current.model,A.model);
});

test('revocation during catalog wait prevents commit and leaves no half selection',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);let release,entered;const ready=new Promise(r=>entered=r);
 f.control.catalog=async()=>{entered();await new Promise(r=>release=r);return [A,B];};
 const selecting=f.store.select(s.id,operation(s),f.plugins);await ready;
 await assert.rejects(send(f.store,s),/SESSION_BUSY/);
 await f.change('disable',MODEL_SELECTOR_ID,s);release();await assert.rejects(selecting,/PLUGIN_NOT_AUTHORIZED|PLUGIN_RESULT_STALE/);
 const state=await f.store.read(s.id);assert.equal(state.modelState.revision,0);assert.deepEqual(state.modelState.operations,{});
 assert.ok(!(await readdir(f.store.directory)).some(x=>x.endsWith('.tmp')));
});

test('failed publication preserves initial state and same operation can be retried once',async t=>{
 const f=await fixture(t),s=await f.create();await f.grant(s);const op=operation(s);const commit=f.plugins.commitResult;
 f.plugins.commitResult=async()=>{throw Error('synthetic persistence boundary failure');};
 await assert.rejects(f.store.select(s.id,op,f.plugins));assert.equal((await f.store.read(s.id)).modelState.revision,0);
 f.plugins.commitResult=commit;assert.equal((await f.store.select(s.id,op,f.plugins)).session.modelState.revision,1);
 assert.equal((await f.store.select(s.id,op,f.plugins)).replayed,true);
});

test('branch inherits completed-turn A while parent B and later choice remain isolated; grants never inherited',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=(await f.store.select(s.id,operation(s),f.plugins)).session;s=await send(f.store,s,'A轮');
 const prefix=structuredClone(s.history);s=(await f.store.select(s.id,operation(s,B),f.plugins)).session;s=await send(f.store,s,'父B轮');
 await f.grant(s,PLUGIN_ID);const b=await f.store.createBranch(s.id,{operationId:'branch-op',expectedSessionRevision:s.revision,turn:1,name:'新路线'},f.plugins);let child=b.session;
 assert.equal(child.modelState.current.model,A.model);assert.equal(child.modelState.revision,0);assert.deepEqual(child.modelState.operations,{});assert.deepEqual(child.history,prefix);assert.deepEqual(child.requests,{});
 assert.equal((await f.plugins.sessionStatus(child.id,MODEL_SELECTOR_ID)).enabled,false);
 const snapshot=(await f.store.archive.read(child.id)).snapshotHash;
 child=await send(f.store,child,'分支A续玩');assert.equal(child.turns.at(-1).model.requestedModel,A.model);
 assert.equal((await f.store.read(s.id)).modelState.current.model,B.model);
 await f.grant(child);child=(await f.store.select(child.id,operation(child,B),f.plugins)).session;
 assert.equal((await f.store.archive.read(child.id)).snapshotHash,snapshot);
 await assert.rejects(send(f.store,child,'不能重放',s.turns[0].requestId),/BRANCH_INHERITED_REQUEST_ID/);
});

test('uninstall retains selection and core chat; reinstall never restores grant; unavailable does not silently change',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=(await f.store.select(s.id,operation(s),f.plugins)).session;
 await f.change('uninstall');s=await send(f.store,s);assert.equal(s.turns.at(-1).model.requestedModel,A.model);
 await f.change('install');await assert.rejects(f.store.select(s.id,operation(s,B),f.plugins),/PLUGIN_NOT_AUTHORIZED/);
 await f.grant(s);f.setOptions([{...B,available:false}]);await assert.rejects(f.store.select(s.id,operation(s,B),f.plugins),/MODEL_SELECTION_UNAVAILABLE/);
 assert.equal((await f.store.read(s.id)).modelState.current.model,A.model);
});

test('unconfirmed requests and runtime tampering block switch/send/branch without reinterpretation',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s.requests.unknown={status:'pending'};await f.store.write(s);
 await assert.rejects(f.store.select(s.id,operation(s),f.plugins),/MODEL_UNCONFIRMED_REQUEST/);await assert.rejects(send(f.store,s),/MODEL_UNCONFIRMED_REQUEST/);
 delete s.requests.unknown;s.runtime.hash='bad';await f.store.write(s);
 await assert.rejects(send(f.store,s),/RUNTIME_BINDING_INVALID/);
 assert.equal((await f.store.read(s.id)).runtime.hash,'bad');
});

test('legacy verified runtime stays readable/continuable without v2 re-signing or model selection',async t=>{
 const f=await fixture(t);assert.equal(earthRuntime.hash,LEGACY_RUNTIME_HASH);
 const old=new VersionedSessionStore(f.store.directory,async()=>'旧会话原文');let s=await old.create({characterText:'姓名：旧测试',world:'表世界',mode:'offline',params:{}});
 const binding=canonical(s.runtime);s=await f.store.send(s.id,{revision:0,requestId:'old-request',input:'原配置继续'});
 assert.equal(s.turns.length,1);assert.equal(s.modelState,undefined);assert.equal(canonical(s.runtime),binding);
 await assert.rejects(f.store.select(s.id,{operationId:'old',expectedSessionRevision:1,selectionRevision:0,selectionId:A.selectionId,catalogRevision:A.selectionRevision},f.plugins),/MODEL_LEGACY_READ_ONLY/);
});

test('generation locks selection and branch; cancelled late result does not enter history or reset choice',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=(await f.store.select(s.id,operation(s),f.plugins)).session;await f.grant(s,PLUGIN_ID);
 let release,entered;const ready=new Promise(r=>entered=r);f.store.generate=async()=>{entered();await new Promise(r=>release=r);return '取消后迟到原文';};
 const pending=send(f.store,s);await ready;await assert.rejects(f.store.select(s.id,operation(s,B),f.plugins),/SESSION_BUSY/);
 await assert.rejects(f.store.createBranch(s.id,{operationId:'busy-branch',expectedSessionRevision:0,turn:1,name:'分支'},f.plugins),/SESSION_BUSY/);
 f.store.cancel(s.id);release();const result=await pending;assert.equal(result.turns.length,0);assert.equal(Object.values(result.requests)[0].status,'cancelled');assert.equal(result.modelState.current.model,A.model);
});

test('two selection operations cannot share a revision; branch-of-branch inherits its own completed choice',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);
 const results=await Promise.allSettled([f.store.select(s.id,operation(s,A),f.plugins),f.store.select(s.id,operation(s,B),f.plugins)]);
 assert.equal(results.filter(x=>x.status==='fulfilled').length,1);s=await f.store.read(s.id);s=await send(f.store,s);await f.grant(s,PLUGIN_ID);
 let child=(await f.store.createBranch(s.id,{operationId:'child',expectedSessionRevision:s.revision,turn:1,name:'子路线'},f.plugins)).session;
 await f.grant(child);child=(await f.store.select(child.id,operation(child,B),f.plugins)).session;child=await send(f.store,child);await f.grant(child,PLUGIN_ID);
 const next=(await f.store.createBranch(child.id,{operationId:'grandchild',expectedSessionRevision:child.revision,turn:2,name:'孙路线'},f.plugins)).session;
 assert.equal(next.modelState.current.model,B.model);assert.equal(next.modelState.revision,0);assert.deepEqual(next.requests,{});assert.equal(next.provenance.requestOrigins.length,2);
});


test('tampered operation receipt or completed-turn choice cannot be used for further actions',async t=>{
 const f=await fixture(t);let s=await f.create();await f.grant(s);s=(await f.store.select(s.id,operation(s),f.plugins)).session;s=await send(f.store,s);
 const original=structuredClone(s);s.modelState.operations={};await f.store.write(s);await assert.rejects(send(f.store,s),/MODEL_STATE_INVALID/);
 s=structuredClone(original);s.turns[0].model.selectionRevision=99;await f.store.write(s);await assert.rejects(send(f.store,s),/MODEL_TURN_BINDING_INVALID/);
});


test('previous v1 host can enumerate and read v2 parent and branch, but refuses to continue them',async t=>{
 const f=await fixture(t);let s=await f.create();s=await send(f.store,s);await f.grant(s,PLUGIN_ID);
 const child=(await f.store.createBranch(s.id,{operationId:'rollback-read',expectedSessionRevision:s.revision,turn:1,name:'可读分支'},f.plugins)).session;
 const previous=new VersionedSessionStore(f.store.directory,()=>assert.fail('must not generate'));
 assert.equal((await previous.list()).length,2);
 for(const original of [s,child]){const bytes=await readFile(original.provenance?f.store.archive.path(original.id):f.store.path(original.id));assert.deepEqual((await previous.read(original.id)).history,original.history);await assert.rejects(previous.send(original.id,{input:'拒绝降级续玩',revision:original.revision,requestId:'rollback'}),/RUNTIME_BINDING_INVALID/);assert.deepEqual(await readFile(original.provenance?f.store.archive.path(original.id):f.store.path(original.id)),bytes);}
});


test('configured but disabled control does not authorize creation of a real session',async t=>{
 const f=await fixture(t);f.control.status=async()=>({enabled:false});
 await assert.rejects(f.store.create({characterText:'姓名：虚构',world:'表世界',mode:'real',params:{max_tokens:16384}}),/PROVIDER_DISABLED/);
 assert.equal((await f.store.list()).length,0);
});


test('approved Luna exception persists actual parameters and branches preserve them',async t=>{
 const f=await fixture(t),L={...A,model:'openai/gpt-5.6-luna'};f.setOptions([L,B]);let s=await f.create();await f.grant(s);s=(await f.store.select(s.id,operation(s,L),f.plugins)).session;
 assert.deepEqual(s.modelState.current.parameters,{max_tokens:16384});s=await send(f.store,s,'Luna合成回合');
 assert.deepEqual(s.turns[0].model.selection.parameters,{max_tokens:16384});
 s=(await f.store.select(s.id,operation(s,B),f.plugins)).session;assert.deepEqual(s.modelState.current.parameters,models.earth.parameters);
 await f.grant(s,PLUGIN_ID);const {session:child}=await f.store.createBranch(s.id,{operationId:'luna-branch',expectedSessionRevision:s.revision,turn:1,name:'Luna分支'},f.plugins);
 assert.deepEqual(child.modelState.current.parameters,{max_tokens:16384});assert.deepEqual(child.history,s.history);
});
