import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {start} from '../studio/server.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/plugin-b1-http/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t,corrupt=false){
 const dir=await mkdtemp(join(root,'http-'));if(corrupt){await mkdir(join(dir,'plugins'));await writeFile(join(dir,'plugins/registry.json'),'not-json');}
 const host=await start({port:0,directory:dir}),origin='http://127.0.0.1:'+host.server.address().port;
 t.after(async()=>{await host.plugins?.close();await new Promise(r=>host.server.close(r));assert.equal(dirname(dir),root.replace(/[\\/]$/,''));await rm(dir,{recursive:true,force:true});});
 const f={host,async api(path,body,from=origin){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:from,'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};},async payload(action,s){const {data}=await f.api('api/plugins'),p=data.plugins[0];return {operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:data.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}: {})};}};return f;
}
test('HTTP catalog/install/enable/disable/uninstall enforce origin, exact consent and session binding',async t=>{
 const f=await fixture(t),install=await f.payload('install');assert.equal((await f.api('api/plugins/rpg.branch-save/install',install,'https://foreign.invalid')).status,403);
 assert.equal((await f.api('api/plugins/unknown/install',install)).status,404);assert.equal((await f.api('api/plugins/rpg.branch-save/install',install)).status,200);
 const s=(await f.api('earth/api/sessions',{characterText:'姓名：虚构插件验收者',world:'表世界',params:{},mode:'offline'})).data;
 const path='earth/api/sessions/'+s.id+'/plugins/rpg.branch-save';assert.equal((await f.api(path)).data.enabled,false);
 const consent=await f.payload('enable',s);assert.equal((await f.api(path+'/enable',{...consent,permissions:[]})).status,403);
 assert.equal((await f.api(path+'/enable',{...consent,sessionId:'other'})).status,400);
 assert.equal((await f.api(path+'/enable',consent)).status,200);assert.equal((await f.api(path)).data.enabled,true);
 const before=await f.host.earth.read(s.id);assert.equal((await f.api(path+'/disable',await f.payload('disable',s))).status,200);assert.equal((await f.api(path)).data.enabled,false);
 assert.equal((await f.api('api/plugins/rpg.branch-save/uninstall',await f.payload('uninstall'))).status,200);assert.deepEqual(await f.host.earth.read(s.id),before);
 assert.equal((await f.host.provider.status('earth')).used,0);assert.equal((await f.api('rpg05/api/sessions/'+s.id+'/plugins/rpg.branch-save')).status,404);
});
test('corrupt plugin registry disables plugin endpoints without preventing core creation or reads',async t=>{
 const f=await fixture(t,true);assert.deepEqual(await f.api('api/plugins'),{status:503,data:{error:'PLUGIN_STORE_INVALID'}});
 const create=await f.api('earth/api/sessions',{characterText:'姓名：独立故事',world:'表世界',params:{},mode:'offline'});assert.equal(create.status,201);
 assert.equal((await f.api('earth/api/sessions/'+create.data.id)).status,200);assert.equal((await f.api('api/status')).status,200);assert.equal((await f.host.provider.status('earth')).used,0);
});

test('two plugin HTTP lifecycles isolate consent and reject cross-ID requests without dispatch',async t=>{
 const f=await fixture(t),B='rpg.branch-save',M='rpg.model-selector';
 async function payload(action,id,s){const {data}=await f.api('api/plugins'),p=data.plugins.find(p=>p.id===id);return {operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:data.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})};}
 const cat=(await f.api('api/plugins')).data;assert.deepEqual(cat.plugins.map(p=>p.id),[B,M,'rpg.history-window']);assert.ok(cat.plugins.every(p=>!p.installed));
 const install=await payload('install',M);assert.equal((await f.api('api/plugins/'+B+'/install',install)).status,400);
 assert.equal((await f.api('api/plugins/'+M+'/install',install,'https://foreign.invalid')).status,403);
 for(const p of [B,M])assert.equal((await f.api('api/plugins/'+p+'/install',await payload('install',p))).status,200);
 const s=(await f.api('earth/api/sessions',{characterText:'姓名：虚构双插件玩家',world:'表世界',params:{},mode:'offline'})).data;
 const prefix='earth/api/sessions/'+s.id+'/plugins/';const before=await f.host.earth.read(s.id);
 assert.equal((await f.api(prefix+M)).data.enabled,false);assert.equal((await f.api(prefix+'unknown')).status,404);
 const consent=await payload('enable',M,s);assert.equal((await f.api(prefix+B+'/enable',consent)).status,400);
 for(const p of [B,M])assert.equal((await f.api(prefix+p+'/enable',await payload('enable',p,s))).status,200);
 assert.equal((await f.api(prefix+M+'/disable',await payload('disable',M,s))).status,200);
 assert.equal((await f.api(prefix+M)).data.enabled,false);assert.equal((await f.api(prefix+B)).data.enabled,true);
 assert.equal((await f.api('api/plugins/'+M+'/uninstall',await payload('uninstall',M))).status,200);
 assert.equal((await f.api('api/plugins/'+M+'/install',await payload('install',M))).status,200);
 assert.equal((await f.api(prefix+M)).data.enabled,false);assert.equal((await f.api(prefix+B)).data.enabled,true);
 assert.equal((await f.api('rpg05/api/sessions/'+s.id+'/plugins/'+M)).status,404);
 // Lifecycle does not expose a generic invoke/dispatch API or mutate core history/config.
 assert.equal((await f.api(prefix+M+'/invoke',{capability:'model.dispatch'})).status,404);
 assert.deepEqual(await f.host.earth.read(s.id),before);assert.equal((await f.host.provider.status('earth')).used,0);
});
