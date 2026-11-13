import {constants} from 'node:fs';
import {open,lstat,mkdir,rename,unlink} from 'node:fs/promises';
import {resolve,parse,join,sep} from 'node:path';
import {randomUUID} from 'node:crypto';
import {fail} from './catalog.mjs';
async function safe(directory){
 let current=parse(directory).root;
 for(const part of directory.slice(current.length).split(sep).filter(Boolean)){
  current=join(current,part);let s;try{s=await lstat(current);}catch(e){if(e.code==='ENOENT')return;throw e;}if(s.isSymbolicLink()||!s.isDirectory())throw fail('PLUGIN_OWNER_PATH_UNSAFE');
 }
}
async function read(file){
 const s=await lstat(file);if(!s.isFile()||s.isSymbolicLink()||s.nlink!==1||s.size>4096)throw fail('PLUGIN_OWNER_UNKNOWN');
 const h=await open(file,constants.O_RDONLY|(constants.O_NOFOLLOW??0));
 try{const a=await h.stat(),b=await lstat(file);if(a.ino!==s.ino||a.dev!==s.dev||a.nlink!==1||b.ino!==s.ino||b.dev!==s.dev)throw fail('PLUGIN_OWNER_CHANGED');const bytes=await h.readFile();if(bytes.length>4096)throw fail('PLUGIN_OWNER_UNKNOWN');return bytes;}finally{await h.close();}
}
async function create(file,bytes){const h=await open(file,'wx',0o600);try{await h.writeFile(bytes);await h.sync();}finally{await h.close();}}
function dead(bytes){
 let v;try{v=JSON.parse(bytes);}catch{throw fail('PLUGIN_OWNER_UNKNOWN');}
 if(!v||!Number.isSafeInteger(v.pid)||v.pid<1||Object.keys(v).some(k=>!['pid','token'].includes(k))||v.token!==undefined&&typeof v.token!=='string')throw fail('PLUGIN_OWNER_UNKNOWN');
 try{process.kill(v.pid,0);}catch(e){if(e.code==='ESRCH')return;throw fail('PLUGIN_OWNER_UNKNOWN');}
 throw fail('PLUGIN_STORE_ALREADY_OWNED');
}
export async function acquirePluginOwner(directory,{recoverDeadOwner=false}={}){
 const root=resolve(directory);await safe(root);await mkdir(root,{recursive:true});await safe(root);
 const file=join(root,'owner.lock'),claim=join(root,'owner-recovery.lock'),bytes=Buffer.from(JSON.stringify({pid:process.pid,token:randomUUID()}));
 // Serialize recovery contenders; a surviving ambiguous claim never permits takeover.
 try{await lstat(claim);throw fail('PLUGIN_OWNER_RECOVERY_BUSY');}catch(e){if(e.code!=='ENOENT')throw e;}
 try{await create(file,bytes);}catch(e){
  if(e.code!=='EEXIST')throw e;
  const prior=await read(file);dead(prior);if(!recoverDeadOwner)throw fail('PLUGIN_OWNER_RECOVERY_REQUIRED');
  try{await create(claim,bytes);}catch{throw fail('PLUGIN_OWNER_RECOVERY_BUSY');}
  try{if(!(await read(file)).equals(prior))throw fail('PLUGIN_OWNER_CHANGED');dead(prior);
   await rename(file,join(root,'owner-dead-'+randomUUID()+'.json'));await create(file,bytes);
  }finally{if((await read(claim)).equals(bytes))await unlink(claim);}
 }
 // A contender may have created a recovery claim before this exclusive create.
 try{await lstat(claim);if((await read(file)).equals(bytes))await unlink(file);throw fail('PLUGIN_OWNER_RECOVERY_BUSY');}catch(e){if(e.code!=='ENOENT')throw e;}
 return {async close(){if(!(await read(file)).equals(bytes))throw fail('PLUGIN_OWNER_CHANGED');await unlink(file);}};
}
