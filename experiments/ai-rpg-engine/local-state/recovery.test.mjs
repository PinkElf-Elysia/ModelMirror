import test from 'node:test';import assert from 'node:assert/strict';import {spawn} from 'node:child_process';import {mkdtemp,rm} from 'node:fs/promises';import {join} from 'node:path';import {tmpdir} from 'node:os';
import {LocalStateHost} from './host.mjs';import {exampleState} from './example.mjs';
test('process exit after dispatch and before publication recovers without another simulation',async t=>{
 const dir=await mkdtemp(join(tmpdir(),'rpg-state-process-'));let h=await LocalStateHost.open({directory:dir,enabled:true});
 const r=await h.create({operationId:'init',initial:exampleState(),confirmed:true});await h.close();
 const hostURL=new URL('./host.mjs',import.meta.url).href;
 const code="import {LocalStateHost} from "+JSON.stringify(hostURL)+"; const h=await LocalStateHost.open({directory:"+JSON.stringify(dir)+",enabled:true}); await h.turn("+JSON.stringify(r.id)+",{operationId:'interrupted',revision:0,input:'服用两份养息丹'},{fault:'unknown'}); process.exit(0);";
 const child=spawn(process.execPath,['--input-type=module','-e',code],{stdio:['ignore','ignore','pipe'],windowsHide:true});let error='';child.stderr.on('data',c=>error+=c);
 const exit=await new Promise((ok,no)=>{child.once('error',no);child.once('exit',ok);});assert.equal(exit,0,error);
 h=await LocalStateHost.open({directory:dir,enabled:true});
 t.after(async()=>{await h.close();assert.ok(dir.startsWith(join(tmpdir(),'rpg-state-process-')));await rm(dir,{recursive:true,force:true});});
 const done=await h.recover(r.id);assert.equal(done.state.items[0].quantity,3);assert.equal(done.turns.length,1);assert.equal(h.provider.calls,0);
});
