// Windows local hosts: kernel lifetime exclusion plus an exact hard-link witness.
// Never adopt a legacy marker based only on its PID or age.
import net from 'node:net';
import {mkdir,lstat,realpath,link,unlink,rename,readFile} from 'node:fs/promises';
import {resolve,parse,join,sep} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
const fail=code=>Object.assign(Error(code),{code,status:409});
const stat=p=>lstat(p,{bigint:true}).catch(e=>{if(e.code==='ENOENT')return null;throw e;});
async function safe(root){
 let path=parse(root).root;
 for(const part of root.slice(path.length).split(sep).filter(Boolean)){
  path=join(path,part);const s=await stat(path);
  if(s&&(!s.isDirectory()||s.isSymbolicLink()))throw fail('PLUGIN_OWNER_PATH_UNSAFE');
 }
}
const same=(a,b)=>a?.isFile()&&b?.isFile()&&!a.isSymbolicLink()&&!b.isSymbolicLink()&&a.dev===b.dev&&a.ino===b.ino;
export async function ownLocalDirectory(directory){
 if(process.platform!=='win32')throw fail('LOCAL_OWNER_PLATFORM_UNSUPPORTED');
 const requested=resolve(directory);await safe(requested);await mkdir(requested,{recursive:true});await safe(requested);
 const root=await realpath(requested);
 if(!/^[a-z]:\\/i.test(root))throw fail('LOCAL_OWNER_REQUIRES_LOCAL_DRIVE');
 const key=createHash('sha256').update(root.toLowerCase()).digest('hex');
 const pipe=net.createServer(socket=>socket.destroy());
 const close=()=>new Promise((ok,no)=>pipe.close(e=>e&&e.code!=='ERR_SERVER_NOT_RUNNING'?no(e):ok()));
 try{await new Promise((ok,no)=>{pipe.once('error',no);pipe.listen({path:'\\\\.\\pipe\\modelmirror-rpg-owner-'+key,exclusive:true},()=>{pipe.removeListener('error',no);ok();});});}
 catch{throw fail('PLUGIN_DIRECTORY_LOCK_UNAVAILABLE');}
 const owner=join(root,'owner.lock'),witness=join(root,'local-owner.inode');
 try{
  const current=await stat(owner),previous=await stat(witness);
  if(!current&&previous)throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');
  if(current){
   if(!same(current,previous)||current.nlink!==2n||current.size>4096n)throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');
   const bytes=await readFile(owner);if(bytes.length>4096)throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');
   let record;try{record=JSON.parse(bytes);}catch{throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');}
   if(!Number.isSafeInteger(record.pid)||record.pid<1||typeof record.token!=='string')throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');
   // Also protect a live legacy writer that does not hold our pipe.
   try{process.kill(record.pid,0);throw fail('PLUGIN_STORE_ALREADY_OWNED');}catch(e){if(e.code!=='ESRCH')throw e;}
   if(!same(await stat(owner),current)||!same(await stat(witness),current))throw fail('PLUGIN_OWNER_CHANGED');
   await rename(owner,join(root,'owner-dead-'+randomUUID()+'.json'));
  }
  if(previous){if(!previous.isFile()||previous.isSymbolicLink())throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');await unlink(witness);}
  let closed=false;
  return {fd:{async close(){if(closed)return;closed=true;await close();}},async prepareClose(){
   // The original plugin owner verifies a single-link marker on orderly close.
   // Keep the kernel lease held while removing only our verified extra link.
   const current=await stat(owner),previous=await stat(witness);
   if(!previous)return;
   if(!same(current,previous))throw fail('PLUGIN_OWNER_CHANGED');
   await unlink(witness);
  },async remember(){
   const current=await stat(owner);
   if(!current?.isFile()||current.isSymbolicLink()||current.nlink!==1n)throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');
   const record=JSON.parse(await readFile(owner,'utf8'));
   if(record.pid!==process.pid||typeof record.token!=='string')throw fail('PLUGIN_STORE_OWNER_UNVERIFIED');
   await link(owner,witness);
   if(!same(await stat(owner),await stat(witness)))throw fail('PLUGIN_OWNER_CHANGED');
  }};
 }catch(e){await close();throw e;}
}
