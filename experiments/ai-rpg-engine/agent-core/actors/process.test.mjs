import test from 'node:test';import assert from 'node:assert/strict';
import {fork} from 'node:child_process';import {once} from 'node:events';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openRuntime} from './composition.mjs';
test('actual process interruption: dead-owner recovery uses saved actor receipt, not new generation',{timeout:30000},async()=>{
 const root=fileURLToPath(new URL('../../.rpg03-work/actors-r6-tests/',import.meta.url));await mkdir(root,{recursive:true});
 const directory=await mkdtemp(root+'process-'),child=fork(new URL('./test-process.mjs',import.meta.url),[directory],{stdio:['ignore','ignore','pipe','ipc'],windowsHide:true});
 let runtime;let stderr='';child.stderr.on('data',b=>{stderr+=b.toString();});
 try{
  const [data]=await Promise.race([once(child,'message'),once(child,'exit').then(()=>{throw Error('checkpoint missing: '+stderr);})]);
  const exited=once(child,'exit');child.kill();await exited;
  runtime=await openRuntime(directory,{offlinePreview:true});const r=await runtime.host.recover(data.id,'actor-unknown');
  assert.equal(r.tasks['actor-unknown'].status,'completed');assert.equal(runtime.host.provider.calls,0);
  assert.equal((await runtime.host.world.store.read(r.worldId)).state.items[0].quantity,3);
  assert.equal(r.actors.witness.responses.length,1);
 }finally{if(child.exitCode===null&&child.signalCode===null)child.kill();if(runtime)await runtime.close();}
});
