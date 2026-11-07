import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {VersionedSessionStore} from './versioned-store.mjs';
import {models} from './provider.mjs';
import {attachSessionLifecycle,publishVersion} from './session-lifecycle.mjs';
import {start} from './server.mjs';
import {createGodStore} from '../god-card/lib/adapter.mjs';
import {sha,canonical} from '../plugins/catalog.mjs';

async function fixture(){
 const dir=await mkdtemp(join(tmpdir(),'rpg-lifecycle-'));
 let calls=0;const store=new VersionedSessionStore(dir,async()=>'<p>离线回复 '+(++calls)+'</p>');await store.init();
 const s=await store.create({characterText:'姓名：旅人',world:'表世界',params:models.earth.parameters,mode:'offline'});
 for(let i=0;i<3;i++)await store.send(s.id,{input:'观察 '+i,requestId:randomUUID(),revision:i});
 return {store,dir,id:s.id,calls:()=>calls};
}
test('same root/branch ID rewinds, regenerates and restores without changing original files or creating sessions',async()=>{
 const f=await fixture(),root=await f.store.read(f.id);
 const envelope=f.store.archive.prepare(root,{sessionId:f.id,operationId:randomUUID(),expectedSessionRevision:root.revision,turn:2,name:'原分支'});
 await f.store.archive.publish(envelope,publish=>publish());
 const api=attachSessionLifecycle(f.store);
 for(const id of [f.id,envelope.session.id]){
  const before=await f.store.read(id),path=id===f.id?f.store.path(id):f.store.archive.path(id),bytes=await readFile(path,'utf8');
  const op={action:'rewind',operationId:randomUUID(),expectedRevision:before.revision,turn:1};
  const result=await api.change(id,op);assert.equal(result.id,id);assert.equal((await f.store.read(id)).turns.length,0);
  assert.equal((await api.change(id,op)).replayed,true);
  const active=await f.store.read(id),sent=await f.store.send(id,{input:'新路线',requestId:randomUUID(),revision:active.revision});
  assert.equal(sent.id,id);assert.equal(sent.turns.length,1);assert.equal((await f.store.list()).length,2);
  await api.change(id,{action:'restore-version',versionId:op.operationId,operationId:randomUUID(),expectedRevision:sent.revision});
  const restored=await f.store.read(id);assert.deepEqual(restored.turns,before.turns);assert.ok(restored.revision>sent.revision);
  assert.equal(await readFile(path,'utf8'),bytes);assert.equal((await api.inspect(id)).versions.length,2);
  const restarted=new VersionedSessionStore(f.dir,async()=>{throw Error('NO_REPLAY');});await restarted.init();attachSessionLifecycle(restarted);
  assert.deepEqual((await restarted.read(id)).turns,before.turns);
 }
 assert.equal(f.calls(),5);
});
test('trash is recoverable and idempotent; pending, stale and corrupt operations fail closed',async()=>{
 const f=await fixture(),api=attachSessionLifecycle(f.store),s=await f.store.read(f.id);
 const op={action:'delete',operationId:randomUUID(),expectedRevision:s.revision};
 await assert.rejects(api.change(f.id,{...op,expectedRevision:s.revision-1}),/SESSION_REVISION_CHANGED/);
 s.requests[ randomUUID()]={status:'pending'};await f.store.write(s);
 await assert.rejects(api.change(f.id,op),/SESSION_OPERATION_UNCONFIRMED/);
 for(const [id,r] of Object.entries(s.requests))if(r.status==='pending')delete s.requests[id];await f.store.write(s);
 const deleted=await api.change(f.id,op);
 assert.equal((await f.store.list()).length,0);assert.equal((await api.trash()).length,1);
 await assert.rejects(f.store.read(f.id),/SESSION_IN_TRASH/);
 assert.equal((await api.change(f.id,op)).replayed,true);
 await assert.rejects(api.change(f.id,{...op,action:'restore-trash'}),/SESSION_OPERATION_COLLISION/);
 await api.change(f.id,{action:'restore-trash',operationId:randomUUID(),expectedRevision:deleted.revision});
 assert.equal((await f.store.list()).length,1);assert.deepEqual((await f.store.read(f.id)).turns,s.turns);
 const path=join(f.dir,'session-versions',f.id+'.json'),e=JSON.parse(await readFile(path,'utf8'));e.session.name='corrupted';await writeFile(path,JSON.stringify(e));
 await assert.rejects(f.store.read(f.id),/SESSION_VERSION_INVALID/);
});
test('HTTP rewind restores selected-turn memory and summary v2 snapshots, keeps URL and plugin grants, and can send again',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'rpg-lifecycle-memory-'));
 const host=await start({port:0,directory,messageTools:true,autoSummaryV2:true});
 t.after(async()=>{await new Promise(r=>host.server.close(r));await host.plugins.close();});
 const origin='http://127.0.0.1:'+host.server.address().port;
 const call=async(path,body)=>{const r=await fetch(origin+'/rpg-app/xingjian/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();assert.ok(r.ok,path+': '+JSON.stringify(v));return v;};
 const s=await call('sessions',{opening:'中性旅人'}),base='sessions/'+s.id;
 for(const id of ['rpg.model-selector','rpg.rolling-summary','rpg.memory-palace','rpg.message-tools'])for(const action of ['install','enable']){
  const c=await call('plugins'),p=c.plugins.find(p=>p.id===id),body={operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision};
  if(action==='enable')Object.assign(body,{sessionId:s.id,expectedSessionRevision:(await call(base)).revision,permissions:p.permissions});
  await call((action==='enable'?base+'/':'')+'plugins/'+id+'/'+action,body);
 }
 const [model]=(await call(base+'/model-catalog')).models;
 let h=await call(base+'/memory-palace');
 await call(base+'/memory-palace/settings',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,selectionId:model.selectionId,catalogRevision:model.selectionRevision});
 async function send(input){
  const h=await call(base+'/memory-palace'),turn=(await call(base)).turns.length+1;
  await call(base+'/send',{operationId:randomUUID(),expectedMemoryRevision:h.memoryRevision,expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input});
  for(const deadline=Date.now()+15000;Date.now()<deadline;){const status=await call(base+'/memory-palace');if(!status.busy&&status.processedThrough===turn)return call(base);await new Promise(r=>setTimeout(r,100));}
  throw Error('MEMORY_DID_NOT_SETTLE');
 }
 await send('抵达港口');await send('寻找书院');
 const before=await call(base);assert.equal(before.turns.length,2);
 const op={action:'rewind',operationId:randomUUID(),expectedRevision:before.revision,turn:2};
 await call(base+'/lifecycle',op);
 const state=await call(base+'/memory-palace');assert.equal(state.compatible,true);
 const after=await send('改往山间');assert.equal(after.id,s.id);assert.equal(after.turns.length,2);assert.equal(after.turns[1].input,'改往山间');
 assert.equal((await call('sessions')).length,1);
 await call(base+'/lifecycle',{action:'restore-version',operationId:randomUUID(),expectedRevision:after.revision,versionId:op.operationId});
 assert.equal((await call(base)).turns[1].raw,before.turns[1].raw);
 assert.equal((await call(base+'/plugins/rpg.message-tools')).enabled,true);
 const restored=await call(base);await call(base+'/lifecycle',{action:'rewind',operationId:randomUUID(),expectedRevision:restored.revision,turn:1});
 await send('从第一轮重开');assert.equal((await call(base)).turns.length,1);
});

test('brief rename contention retries the same publication, while persistent and other I/O failures remain errors',async()=>{
 let attempts=0;const sleeps=[];
 assert.equal(await publishVersion(async()=>{if(++attempts<3)throw Object.assign(Error('busy'),{code:'EPERM'});return 'published';},async ms=>sleeps.push(ms)),'published');
 assert.equal(attempts,3);assert.equal(sleeps.length,2);
 await assert.rejects(publishVersion(async()=>{throw Object.assign(Error('disk full'),{code:'ENOSPC'});},async()=>assert.fail('must not retry')),/disk full/);
 attempts=0;await assert.rejects(publishVersion(async()=>{attempts++;throw Object.assign(Error('locked'),{code:'EPERM'});},async()=>{}),/locked/);assert.equal(attempts,6);
});

test('only the reviewed old God server binding is admitted; author and setup drift still fail',async()=>{
 const store=createGodStore(await mkdtemp(join(tmpdir(),'rpg-lifecycle-god-')),async()=>'<p>离线港口</p>');await store.init();
 const s=await store.create({mode:'offline',setup:null});
 s.runtime.descriptor.artifacts['studio/server.mjs']='a982e9f51f75d50b9e378d6bd74ef77e4a8c17b687f421ec159a17be42cc7c14';s.runtime.hash=sha(canonical(s.runtime.descriptor));
 assert.equal(store.status(s).compatible,false);attachSessionLifecycle(store);assert.equal(store.status(s).compatible,true);
 await store.requireRuntime(s);
 const altered=structuredClone(s);altered.runtime.descriptor.author.system='changed';altered.runtime.hash=sha(canonical(altered.runtime.descriptor));assert.equal(store.status(altered).compatible,false);
 assert.equal(store.status({...s,characterText:'changed'}).compatible,false);
});

test('completed legacy bridge does not block trash or rewind when auxiliary plugins are disabled',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'rpg-lifecycle-legacy-'));
 const host=await start({port:0,directory,messageTools:true});
 t.after(async()=>{await new Promise(r=>host.server.close(r));await host.plugins.close();});
 const origin='http://127.0.0.1:'+host.server.address().port;
 const call=async(path,body)=>{const r=await fetch(origin+'/rpg-app/xingjian/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});const v=await r.json();assert.ok(r.ok,path+': '+JSON.stringify(v));return v;};
 const s=await call('sessions',{opening:'成年旅人观察茶馆'}),base='sessions/'+s.id,h=await call(base+'/memory-palace'),operationId=randomUUID();
 await call(base+'/send',{operationId,expectedMemoryRevision:h.memoryRevision,expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input:'询问观景路线'});
 let disk;
 for(const deadline=Date.now()+10000;Date.now()<deadline;){disk=JSON.parse(await readFile(join(directory,'xingjian/offline',s.id+'.json'),'utf8'));if(disk.summaryTasks?.operations?.[operationId]?.status==='complete')break;await new Promise(r=>setTimeout(r,100));}
 assert.equal(disk.memoryLegacyInputs[operationId].status,'prepared');assert.equal(disk.summaryTasks.operations[operationId].status,'complete');
 const deleted=await call(base+'/lifecycle',{action:'delete',operationId:randomUUID(),expectedRevision:disk.revision});assert.equal((await call('session-trash')).length,1);
 const restored=await call(base+'/lifecycle',{action:'restore-trash',operationId:randomUUID(),expectedRevision:deleted.revision});
 await call(base+'/lifecycle',{action:'rewind',operationId:randomUUID(),expectedRevision:restored.revision,turn:1});assert.equal((await call(base)).turns.length,0);
});
