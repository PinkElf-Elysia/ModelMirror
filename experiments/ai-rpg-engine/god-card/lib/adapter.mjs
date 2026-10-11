import {readFile} from 'node:fs/promises';
import {assemble,loadFrozen} from './assembly.mjs';
import {restoreSetup,serializeSetup} from './setup.mjs';
import {CardSessionStore} from '../../studio/card-session-store.mjs';
import {canonical,sha,fail} from '../../plugins/catalog.mjs';

const exact=(x,keys)=>x&&typeof x==='object'&&!Array.isArray(x)&&Object.keys(x).sort().join(',')===keys.slice().sort().join(',');
export const godAdapter=Object.freeze({
 create(input){
  if(!exact(input,['setup','mode'])||input.mode!=='offline')throw fail('GOD_OFFLINE_CONFIGURATION_REQUIRED',400);
  let setup;try{setup=restoreSetup(JSON.stringify(input.setup));}catch{throw fail('GOD_SETUP_INVALID',400);}
  const characterText=serializeSetup(setup);
  if(characterText.length>20000)throw fail('GOD_SETUP_TOO_LARGE',400);
  return {cardId:'god',name:(setup.user.姓名||'神临凡尘').slice(0,100),characterText,world:null,params:{},mode:'offline'};
 },
 validateSend(input){
  if(!exact(input,['input','requestId','revision']))throw fail('GOD_SEND_FIELDS_INVALID',400);
 },
 assemble(session,input){
  if(session.cardId!=='god'||session.world!==null||canonical(session.params)!=='{}')throw fail('GOD_SESSION_BINDING_INVALID');
  // First dispatch must be the server-serialized complete author setup, exactly once.
  if(!session.history.length&&input!==session.characterText)throw fail('GOD_OPENING_SNAPSHOT_REQUIRED',400);
  return assemble({input,history:session.history});
 }
});
const root=new URL('../../',import.meta.url);
const paths=['god-card/lib/plugins.mjs','god-card/resources/branch-save.manifest.json','plugins/branch-save.mjs','plugins/host.mjs','god-card/lib/assembly.mjs','god-card/lib/setup.mjs','god-card/lib/adapter.mjs','god-card/lib/http.mjs','studio/card-session-store.mjs','studio/server.mjs','studio/controlled-provider.mjs','god-card/resources/prompts.json','god-card/resources/MANIFEST.json','card-replica/lib/store.mjs','studio/versioned-store.mjs','studio/runtime-binding.mjs','studio/branch-archive.mjs'];
const artifacts=async()=>Object.fromEntries(await Promise.all(paths.map(async p=>[p,sha(await readFile(new URL(p,root)))])));
const loaded=await artifacts();
const descriptor={format:1,cardId:'god',artifacts:loaded,author:{system:loadFrozen().manifest.system.sha256,prefix:null,suffix:null,worldbook:null},model:{mode:'offline-only',selection:null,parameters:null},historyPolicy:'complete-raw-history'};
export const godRuntime={descriptor,hash:sha(canonical(descriptor)),async verify(){if(canonical(await artifacts())!==canonical(loaded))throw fail('RUNTIME_FILES_CHANGED_RESTART_REQUIRED');loadFrozen();}};
export function createGodStore(directory,generate){return new CardSessionStore(directory,generate,godRuntime,godAdapter);}
