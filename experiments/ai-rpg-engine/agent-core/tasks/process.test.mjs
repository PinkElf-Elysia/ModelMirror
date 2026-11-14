import test from 'node:test';import assert from 'node:assert/strict';
import {fork} from 'node:child_process';import {once} from 'node:events';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openRuntime} from './composition.mjs';
test('process killed after unknown receipt: reopen reconciles owner without another dispatch',{timeout:45000},async()=>{
 const root=fileURLToPath(new URL('../../.rpg03-work/r7-validation/',import.meta.url));await mkdir(root,{recursive:true});const directory=await mkdtemp(root+'process-');
 const child=fork(new URL('./test-process.mjs',import.meta.url),[directory],{stdio:['ignore','ignore','pipe','ipc'],windowsHide:true});let rt,stderr='';child.stderr.on('data',b=>stderr+=b.toString());
 try{
  const [data]=await Promise.race([once(child,'message'),once(child,'exit').then(()=>{throw Error('No checkpoint: '+stderr);})]);
  const exited=once(child,'exit');child.kill();await exited;
  rt=await openRuntime(directory,{offlinePreview:true});const v=await rt.recover(data.id,data.taskId);
  assert.equal(v.tasks[0].adoption,'completed');assert.equal(v.budget.calls,data.calls);
  assert.equal([...rt.transports.values()].reduce((n,p)=>n+p.calls,0),0);
  assert.equal(v.world.resources[0].quantity,3);assert.equal(v.world.events.filter(e=>e.kind==='action').length,1);
 }finally{if(child.exitCode===null&&child.signalCode===null)child.kill();await rt?.close();}
});
