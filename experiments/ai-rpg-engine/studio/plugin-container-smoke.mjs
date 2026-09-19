// Explicit offline packaging check. Run only with a new, disposable data directory.
import assert from 'node:assert/strict';
import {mkdir,readdir,readFile,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {earthRuntime} from './runtime-binding.mjs';
const directory=resolve(process.argv[2]||'/data/plugin-b4-smoke');
await mkdir(directory,{recursive:true});
assert.deepEqual(await readdir(directory),[],'Use a NEW empty smoke directory; existing data is never overwritten.');
const expected=process.argv[3];if(expected)assert.equal(earthRuntime.hash,expected);
let host;
async function boot(){host=await start({port:0,directory,enabled:false,limit:0});return 'http://127.0.0.1:'+host.server.address().port;}
async function close(){if(host){await host.plugins?.close();await new Promise(r=>host.server.close(r));host=null;}}
let origin=await boot();
async function api(path,body,status=200){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});assert.equal(r.status,status,path);return r.json();}
async function change(action,s){const c=await api('api/plugins'),p=c.plugins[0];return api(s?'earth/api/sessions/'+s.id+'/plugins/rpg.branch-save/'+action:'api/plugins/rpg.branch-save/'+action,{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});}
try{
 for(const card of ['earth','rpg05']){const r=await fetch(origin+'/rpg-app/'+card+'/');assert.equal(r.status,200);const html=await r.text();assert.match(html,/<div id="root"/);for(const m of html.matchAll(/(?:src|href)="([^"]*assets\/[^"]+)"/g)){assert.equal((await fetch(new URL(m[1],origin))).status,200);}}
 const root=await api('earth/api/sessions',{characterText:'姓名：容器离线旅人（虚构）',world:'表世界',params:{max_tokens:16384},mode:'offline'},201);
 const parent=await api('earth/api/sessions/'+root.id+'/send',{requestId:randomUUID(),revision:root.revision,input:'离线开始'});
 assert.equal(parent.turns.length,1);assert.equal(parent.runtime.compatible,true);
 await change('install');assert.equal((await api('earth/api/sessions/'+parent.id+'/plugins/rpg.branch-save')).enabled,false);
 await change('enable',parent);
 const command={operationId:randomUUID(),expectedSessionRevision:parent.revision,turn:1,name:'容器中的新路线'};
 const created=await api('earth/api/sessions/'+parent.id+'/branches',command,201),child=created.session;
 assert.equal((await api('earth/api/sessions/'+parent.id+'/branches',command)).session.id,child.id);
 assert.equal(child.turns[0].raw,parent.turns[0].raw);assert.deepEqual(child.requests,{});
 assert.equal((await api('earth/api/sessions/'+child.id+'/plugins/rpg.branch-save')).enabled,false);
 const files=await readdir(join(directory,'earth','branches'));assert.equal(files.length,1);
 const snapshot=JSON.parse(await readFile(join(directory,'earth','branches',files[0]),'utf8'));
 await change('uninstall');await close();origin=await boot();
 const restored=await api('earth/api/sessions/'+child.id);assert.equal(restored.runtime.compatible,true);assert.equal(restored.turns[0].raw,parent.turns[0].raw);
 const continued=await api('earth/api/sessions/'+child.id+'/send',{requestId:randomUUID(),revision:restored.revision,input:'卸载并重启后的离线续玩'});assert.equal(continued.turns.length,2);
 assert.equal((await api('earth/api/sessions/'+parent.id)).turns.length,1);
 assert.deepEqual(JSON.parse(await readFile(join(directory,'earth','branches',files[0]),'utf8')).snapshot,snapshot.snapshot);
 await change('install');assert.equal((await api('earth/api/sessions/'+parent.id+'/plugins/rpg.branch-save')).enabled,false);
 const status=await api('api/status');for(const c of status.cards){assert.equal(c.used,0);assert.equal(c.enabled,false);assert.equal(c.limit,0);}
 const result={kind:'offline-container-smoke',runtimeHash:earthRuntime.hash,cardsAssets:'passed',installNotEnable:'passed',idempotentBranch:'passed',uninstallRestartReadAndContinue:'passed',snapshotAndParentPreserved:'passed',reinstallNoGrant:'passed',realProviderCalls:0,budget:0};
 await writeFile(join(directory,'SMOKE-RESULT.json'),JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(result));
}finally{await close();}
