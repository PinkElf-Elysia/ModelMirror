import test from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile,readFile,readdir,symlink} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {once} from 'node:events';
import {acquirePluginOwner} from './owner-lock.mjs';
const dir=()=>mkdtemp(join(tmpdir(),'c2b-owner-'));
async function expired(){const p=spawn(process.execPath,['-e','process.exit(0)'],{windowsHide:true,stdio:'ignore'});await once(p,'exit');return p.pid;}
test('dead legacy owner is archived exactly and replaced once, live owner is not stolen',async()=>{
 const d=await dir(),bytes=JSON.stringify({pid:await expired()});await writeFile(join(d,'owner.lock'),bytes);
 await assert.rejects(()=>acquirePluginOwner(d),{code:'PLUGIN_OWNER_RECOVERY_REQUIRED'});
 const lock=await acquirePluginOwner(d,{recoverDeadOwner:true}),files=await readdir(d),archive=files.find(x=>x.startsWith('owner-dead-'));
 assert.equal(await readFile(join(d,archive),'utf8'),bytes);
 await assert.rejects(()=>acquirePluginOwner(d,{recoverDeadOwner:true}),{code:'PLUGIN_STORE_ALREADY_OWNED'});
 await lock.close();const next=await acquirePluginOwner(d);await next.close();
});
test('two simultaneous recoverers never both own the plugin registry',async()=>{
 const d=await dir();await writeFile(join(d,'owner.lock'),JSON.stringify({pid:await expired(),token:'old'}));
 const results=await Promise.allSettled([acquirePluginOwner(d,{recoverDeadOwner:true}),acquirePluginOwner(d,{recoverDeadOwner:true})]);
 assert.equal(results.filter(x=>x.status==='fulfilled').length,1);
 await results.find(x=>x.status==='fulfilled').value.close();
 assert.equal((await readdir(d)).filter(x=>x.startsWith('owner-dead-')).length,1);
});
test('invalid owner and ambiguous recovery claim remain untouched',async()=>{
 for(const bytes of ['broken','{"pid":0}','{"pid":-1}','{"pid":1,"key":"bad"}']){
  const d=await dir();await writeFile(join(d,'owner.lock'),bytes);
  await assert.rejects(()=>acquirePluginOwner(d,{recoverDeadOwner:true}),{code:'PLUGIN_OWNER_UNKNOWN'});
  assert.equal(await readFile(join(d,'owner.lock'),'utf8'),bytes);
 }
 const d=await dir();await writeFile(join(d,'owner-recovery.lock'),'uncertain');
 await assert.rejects(()=>acquirePluginOwner(d,{recoverDeadOwner:true}),{code:'PLUGIN_OWNER_RECOVERY_BUSY'});
});
test('symlink ancestor cannot create plugin files outside the designated directory',async()=>{
 const d=await dir(),target=await dir(),link=join(d,'redirect');await symlink(target,link,process.platform==='win32'?'junction':'dir');
 await assert.rejects(()=>acquirePluginOwner(join(link,'new'),{recoverDeadOwner:true}),{code:'PLUGIN_OWNER_PATH_UNSAFE'});
 assert.deepEqual(await readdir(target),[]);
});
