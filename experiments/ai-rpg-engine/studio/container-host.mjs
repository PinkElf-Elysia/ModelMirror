// Linux container entrypoint. Keep the session-fingerprinted host files unchanged.
import {mkdir,open,lstat,link,unlink,readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {canonical,sha} from '../plugins/catalog.mjs';

const stat = async path => lstat(path,{bigint:true}).catch(e=>{if(e.code!=='ENOENT')throw e;return null;});
export async function ownDirectory(directory){
  await mkdir(directory,{recursive:true});
  const fd=await open(directory,'r');
  const owner=join(directory,'owner.lock'), witness=join(directory,'container-owner.inode');
  try {
    const result=spawnSync('flock',['-n','3'],{stdio:['ignore','ignore','ignore',fd.fd]});
    if(result.status!==0)throw Error('PLUGIN_DIRECTORY_LOCK_UNAVAILABLE');
    const previous=await stat(witness),current=await stat(owner);
    if(current){
      // Only our hard link can prove the legacy marker belonged to a managed
      // process. flock excludes any live managed owner; PID and age cannot.
      if(!previous?.isFile()||!current.isFile()||previous.dev!==current.dev||previous.ino!==current.ino)
        throw Error('PLUGIN_STORE_OWNER_UNVERIFIED');
      await unlink(owner);
    }
    if(previous)await unlink(witness);
    return {fd, async remember(){await link(owner,witness);}};
  }catch(e){await fd.close();throw e;}
}

// A catalog refresh may change only the revision of the same controlled choice.
// Ask the original validator to check both bindings; never rewrite a session,
// a grant, its chosen revision for dispatch, or a provider receipt.
export function preserveCatalogRevision(store,current,policy){
  const original=store.status.bind(store);
  store.status=s=>{
    const result=original(s);
    if(result.code!=='RUNTIME_VERSION_MISMATCH')return result;
    const choices=s.runtime?.descriptor?.models;
    const saved=choices?.[0];
    if(choices?.length!==1||!policy.validChoice(saved)||saved.kind!=='controlled'||
       saved.model!==current.model||saved.selectionId!==current.selectionId||
       saved.selectionRevision===current.selectionRevision||
       canonical(saved.parameters)!==canonical(current.parameters))return result;
    const descriptor={...s.runtime.descriptor,models:[current]};
    const probe=original({...s,runtime:{...s.runtime,descriptor,hash:sha(canonical(descriptor))}});
    return probe.compatible?{compatible:true,code:null,hash:s.runtime.hash}:result;
  };
}

async function run(env){
  const directory=resolve(env.RPG_DATA_DIR||'/data');
  const lease=await ownDirectory(join(directory,'plugins'));
  const yes=k=>env[k]==='true',limit=Number(env.RPG_CALL_LIMIT_PER_CARD||1);
  const control={baseURL:env.RPG_CONTROL_URL,serviceToken:env.RPG_S2S_TOKEN,enabled:yes('RPG_ENABLED')};
  let key=env.OPENROUTER_API_KEY||'';
  if(!key&&env.RPG_CREDENTIAL_FILE){
    const line=(await readFile(env.RPG_CREDENTIAL_FILE,'utf8')).split(/\r?\n/).find(x=>/^OPENROUTER_API_KEY\s*=/.test(x));
    key=(line?.slice(line.indexOf('=')+1)||'').trim().replace(/^['"]|['"]$/g,'');
  }
  const {start,archiveChatOptions}=await import('./server.mjs');
  const {server,plugins,god}=await start({...archiveChatOptions(env),ownArchiveDirectory:ownDirectory,directory,key,limit,enabled:yes('RPG_ENABLED'),port:Number(env.PORT||18420),
    host:env.RPG_BIND||'127.0.0.1',origins:(env.RPG_PUBLIC_ORIGINS||'').split(',').filter(Boolean),
    transcript:env.RPG_TRANSCRIPT_ENABLED!=='false',
    messageTools:env.RPG_MESSAGE_TOOLS_ENABLED!=='false',
    godCard:yes('RPG_GOD_CARD_ENABLED'),godBranches:yes('RPG_GOD_BRANCHES_ENABLED'),godPlugins:yes('RPG_GOD_PLUGINS_ENABLED'),
    historyWindow:yes('RPG_HISTORY_WINDOW_ENABLED'),rollingSummary:yes('RPG_ROLLING_SUMMARY_ENABLED'),memoryPalace:yes('RPG_MEMORY_PALACE_ENABLED'),
    modelControl:yes('RPG_MODEL_SELECTION_ENABLED')?control:null,godControl:yes('RPG_GOD_REAL_ENABLED')?{...control,limit}:null,
    cultivationControl:yes('RPG_CULTIVATION_ENABLED')?{...control,enabled:true,limit:Number(env.RPG_CULTIVATION_CALL_LIMIT||0)}:null});
  if(!plugins)throw Error('PLUGIN_HOST_UNAVAILABLE');
  await lease.remember();
  if(yes('RPG_GOD_REAL_ENABLED')){
    const {godRealPolicy,GOD_REAL_MODEL}=await import('../god-card/lib/real-profile.mjs');
    const active=(await god.control.catalog()).find(m=>m.available&&m.model===GOD_REAL_MODEL);
    if(active)preserveCatalogRevision(god,godRealPolicy.controlledChoice(active),godRealPolicy);
  }
  // Keep the kernel lease alive until process exit, including async close.
  process.once('exit',()=>void lease.fd);
  for(const signal of ['SIGTERM','SIGINT'])process.once(signal,()=>server.close());
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url))
  run(process.env).catch(e=>{console.error('RPG container startup failed:',e.code||e.message);process.exit(1);});
