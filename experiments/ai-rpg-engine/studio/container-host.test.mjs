import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {mkdtemp,writeFile,readFile,rm,unlink,access} from 'node:fs/promises';
import {ownDirectory,preserveCatalogRevision} from './container-host.mjs';
import {createGodRealProfile,godRealPolicy,GOD_REAL_MODEL} from '../god-card/lib/real-profile.mjs';
import {sha,canonical} from '../plugins/catalog.mjs';

test('kernel lease rejects contenders, recovers killed holder, preserves unrelated legacy locks',{skip:process.platform!=='linux'},async t=>{
  const dir=await mkdtemp(join(tmpdir(),'rpg-owner-'));t.after(()=>rm(dir,{force:true,recursive:true}));
  const owner=join(dir,'owner.lock'),registry=join(dir,'registry.json');
  await writeFile(registry,'unchanged');
  const code=`import {ownDirectory} from ${JSON.stringify(new URL('./container-host.mjs',import.meta.url).href)};import {writeFile} from 'node:fs/promises';const lease=await ownDirectory(${JSON.stringify(dir)});await writeFile(${JSON.stringify(owner)},'marker',{flag:'wx'});await lease.remember();console.log('ready');setInterval(()=>void lease.fd,1000);`;
  const child=spawn(process.execPath,['--input-type=module','-e',code],{stdio:['ignore','pipe','inherit']});
  t.after(()=>child.kill('SIGKILL'));
  const exit=once(child,'exit');await Promise.race([once(child.stdout,'data'),exit.then(()=>{throw Error('child exited before lock');})]);
  await assert.rejects(ownDirectory(dir),/LOCK_UNAVAILABLE/);
  child.kill('SIGKILL');await exit;
  const recovered=await ownDirectory(dir);await assert.rejects(access(owner),{code:'ENOENT'});
  await writeFile(owner,'old');await recovered.remember();await recovered.fd.close();
  await unlink(owner);await writeFile(owner,'legacy owner');
  await assert.rejects(ownDirectory(dir),/OWNER_UNVERIFIED/);
  assert.equal(await readFile(owner,'utf8'),'legacy owner');assert.equal(await readFile(registry,'utf8'),'unchanged');
});

test('only catalog revision drift of the same verified God runtime is accepted',async()=>{
  const selection={model:GOD_REAL_MODEL,selectionId:'route',selectionRevision:'old',available:true};
  const old=await createGodRealProfile(selection),current=await createGodRealProfile({...selection,selectionRevision:'new'});
  const store={status:current.status};preserveCatalogRevision(store,current.initialSelection(),godRealPolicy);
  const s={mode:'real',cardId:'god',characterText:'fixture',params:{},world:'fixture'};s.runtime=old.bind(s);
  // Current profile already admits the exact same route after catalog refresh.
  assert.deepEqual(current.status(s),{compatible:true,code:null,hash:s.runtime.hash});
  const before=JSON.stringify(s);assert.deepEqual(store.status(s),{compatible:true,code:null,hash:s.runtime.hash});assert.equal(JSON.stringify(s),before);
  for(const mutate of [v=>v.runtime.descriptor.release='changed',v=>v.runtime.descriptor.models[0].parameters.temperature=0,v=>v.runtime.descriptor.models[0]=godRealPolicy.controlledChoice({...selection,selectionId:'other'})]){
    const v=structuredClone(s);mutate(v);v.runtime.hash=sha(canonical(v.runtime.descriptor));assert.equal(store.status(v).compatible,false);
  }
  assert.equal(store.status({...s,world:'tampered'}).compatible,false);
});
