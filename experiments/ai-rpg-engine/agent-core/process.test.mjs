import {test} from 'node:test';import assert from 'node:assert/strict';import {fork} from 'node:child_process';import {once} from 'node:events';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {openRuntime} from './composition.mjs';
test('actual child process interruption retains query and final receipt without regeneration',{timeout:20000},async()=>{
 const directory=await mkdtemp(join(tmpdir(),'rpg-core-child-')),child=fork(new URL('./test-process.mjs',import.meta.url),[directory],{stdio:['ignore','ignore','pipe','ipc'],windowsHide:true});
 let runtime;
 try{
  const event=await Promise.race([once(child,'message'),once(child,'exit').then(()=>{throw Error('child exited before checkpoint');})]),id=event[0].id;
  const exited=once(child,'exit');child.kill();await exited;
  runtime=await openRuntime(directory,{offlinePreview:true});const h=runtime.get('lighthouse'),r=await h.recover(id);
  assert.equal(r.state.supplies[0].quantity,3);assert.equal(r.turns.length,1);assert.equal(r.operations['op-child-turn'].stages.length,2);assert.equal(h.provider.calls,0);
 }finally{if(child.exitCode===null&&child.signalCode===null)child.kill();if(runtime)await runtime.close();await rm(directory,{recursive:true,force:true});}
});
