import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {once} from 'node:events';
import {mkdtemp,readFile,writeFile,readdir,link,symlink,lstat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {ownLocalDirectory} from './local-owner.mjs';
const windows={skip:process.platform!=='win32'};
async function launch(directory){
 const child=fork(new URL('./local-owner-child.mjs',import.meta.url),[directory],{silent:true,windowsHide:true});
 let buffer='';const result=await new Promise((ok,no)=>{const timer=setTimeout(()=>{child.kill('SIGKILL');no(Error('START_TIMEOUT'));},15000);child.stdout.on('data',b=>{buffer+=b;const line=buffer.split('\n')[0];try{const value=JSON.parse(line);clearTimeout(timer);ok(value);}catch{}});child.once('error',e=>{clearTimeout(timer);no(e);});});
 return {child,...result};
}
async function stop(child){if(child.exitCode!==null||child.signalCode!==null)return;const ended=once(child,'exit');child.kill('SIGKILL');await ended;}
test('kernel lease rejects a second live host; forced exit restores exact offline turns without removing history',windows,async t=>{
 const directory=await mkdtemp(join(tmpdir(),'hp-managed-'));let first=await launch(directory);t.after(()=>stop(first.child));assert(first.port);
 const root='http://127.0.0.1:'+first.port+'/rpg-app/harry-potter/chat/api/';
 const api=async(base,path,data)=>{const r=await fetch(base+path,data===undefined?{}:{method:'POST',headers:{Origin:new URL(base).origin,'Content-Type':'application/json'},body:JSON.stringify(data)});const v=await r.json();assert(r.ok,JSON.stringify(v));return v;};
 const s=await api(root,'sessions',{opening:'姓名：测试旅人；购杖。'}),path='sessions/'+s.id;
 const m=await api(root,path+'/memory-palace');await api(root,path+'/send',{operationId:randomUUID(),input:'在魔杖店询价。',expectedSessionRevision:m.sessionRevision,expectedMemoryRevision:m.memoryRevision,expectedContextPolicyRevision:m.effectivePolicy.revision});
 const before=await api(root,path),file=join(directory,'archive-chat/harry-potter/offline',s.id+'.json'),bytes=await readFile(file);
 const second=await launch(directory);t.after(()=>stop(second.child));assert.equal(second.error,'PLUGIN_DIRECTORY_LOCK_UNAVAILABLE');assert.deepEqual(await readFile(file),bytes);
 await stop(first.child);first=await launch(directory);assert(first.port);
 const after=await api('http://127.0.0.1:'+first.port+'/rpg-app/harry-potter/chat/api/',path);
 assert.deepEqual(after,before);assert.deepEqual(await readFile(file),bytes);
 for(const folder of ['plugins','archive-chat/harry-potter/offline/plugins'])assert.equal((await readdir(join(directory,folder))).filter(n=>n.startsWith('owner-dead-')).length,1);
});
test('unknown and replaced ownership markers remain untouched; no PID-only recovery',windows,async()=>{
 const directory=await mkdtemp(join(tmpdir(),'hp-unverified-')),owner=join(directory,'owner.lock'),bytes=Buffer.from(JSON.stringify({pid:2147483647,token:'unmanaged'}));await writeFile(owner,bytes);
 await assert.rejects(()=>ownLocalDirectory(directory),{code:'PLUGIN_STORE_OWNER_UNVERIFIED'});assert.deepEqual(await readFile(owner),bytes);
 await writeFile(join(directory,'local-owner.inode'),'different inode');await assert.rejects(()=>ownLocalDirectory(directory),{code:'PLUGIN_STORE_OWNER_UNVERIFIED'});assert.deepEqual(await readFile(owner),bytes);
 const orphan=await mkdtemp(join(tmpdir(),'hp-orphan-')),witness=join(orphan,'local-owner.inode');await writeFile(witness,'unknown');await assert.rejects(()=>ownLocalDirectory(orphan),{code:'PLUGIN_STORE_OWNER_UNVERIFIED'});assert.equal(await readFile(witness,'utf8'),'unknown');
});
test('matching hard link is insufficient while its owner is alive; unsafe directory links are rejected',windows,async()=>{
 const directory=await mkdtemp(join(tmpdir(),'hp-live-owner-')),owner=join(directory,'owner.lock');await writeFile(owner,JSON.stringify({pid:process.pid,token:'live-legacy'}));await link(owner,join(directory,'local-owner.inode'));
 await assert.rejects(()=>ownLocalDirectory(directory),{code:'PLUGIN_STORE_ALREADY_OWNED'});assert.equal((await lstat(owner)).nlink,2);
 const parent=await mkdtemp(join(tmpdir(),'hp-redirect-'));await symlink(directory,join(parent,'alias'),'junction');await assert.rejects(()=>ownLocalDirectory(join(parent,'alias')),{code:'PLUGIN_OWNER_PATH_UNSAFE'});
});
