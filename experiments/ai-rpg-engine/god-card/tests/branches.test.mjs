import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {start} from '../../studio/server.mjs';
import {createPluginService} from '../../plugins/host.mjs';
import {loadReviewedPlugins,PLUGIN_ID,canonical,sha} from '../../plugins/catalog.mjs';
import {loadGodPlugins} from '../lib/plugins.mjs';
import {newSetup} from '../lib/setup.mjs';
import {loadFrozen} from '../lib/assembly.mjs';
const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b3-branches/',import.meta.url));
await mkdir(root,{recursive:true});
let serial=0;
async function fixture(generate){
 const directory=await mkdtemp(join(root,'case-')),wires=[];
 const host=await start({port:0,directory,godCard:true,godBranches:true,memoryPalace:true,limit:0,key:'',enabled:false,godGenerate:async a=>{
  wires.push(structuredClone(a.messages));return generate?generate(a):'离线回复 '+wires.length+'\n<details><summary>资料</summary>原始内容</details>';
 }});
 const origin='http://127.0.0.1:'+host.server.address().port;
 const request=async(path,data,from=origin)=>{
  const r=await fetch(origin+'/rpg-app/'+path,data===undefined?{}:{method:'POST',headers:{Origin:from,'Content-Type':'application/json'},body:JSON.stringify(data)});
  return {status:r.status,data:await r.json()};
 };
 const api=(path,data,from)=>request('god/api/'+path,data,from);
 const session=async(name='虚构神明')=>{const setup=newSetup();setup.user.姓名=name;const r=await api('sessions',{setup,mode:'offline'});assert.equal(r.status,201);return r.data;};
 const send=async(s,input=s.characterText)=>{const r=await api('sessions/'+s.id+'/send',{input,requestId:'turn-'+ ++serial,revision:s.revision});assert.equal(r.status,200,JSON.stringify(r.data));return r.data;};
 const change=async(action,s,pluginId=PLUGIN_ID)=>{
  const c=(await request('api/plugins')).data,m=c.plugins.find(p=>p.id===pluginId);
  const input={operationId:'plugin-'+ ++serial,pluginId,version:m.version,artifactSha256:m.artifactSha256,manifestSha256:m.manifestSha256,expectedRegistryRevision:c.revision,
   ...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:m.permissions}:{})};
  return request(s?'god/api/sessions/'+s.id+'/plugins/'+pluginId+'/'+action:'api/plugins/'+pluginId+'/'+action,input);
 };
 const branch=(s,turn=1,name='新路线',operationId='branch-'+ ++serial)=>({operationId,expectedSessionRevision:s.revision,turn,name});
 const close=async()=>{await host.plugins?.close();await new Promise(r=>host.server.close(r));};
 return {directory,host,wires,request,api,session,send,change,branch,close};
}
test('cross-card branch lifecycle: independent history, actual continuation, replay and retained data',async()=>{
 const f=await fixture();
 try{
  let s=await f.session();
  assert.equal((await f.api('sessions/'+s.id+'/branches',f.branch(s))).status,409);
  assert.equal((await f.change('install')).status,200);
  assert.equal((await f.api('sessions/'+s.id+'/plugins/'+PLUGIN_ID)).data.enabled,false);
  assert.equal((await f.change('enable',s)).status,200);
  const empty=await f.api('sessions/'+s.id+'/branches',f.branch(s));assert.equal(empty.status,409);
  s=await f.send(s);const atOne=structuredClone(s);
  s=await f.send(s,'父路线后续秘密');
  const input=f.branch(s,1);
  const r=await f.api('sessions/'+s.id+'/branches',input);assert.equal(r.status,201,JSON.stringify(r.data));
  const child=r.data.session,stored=await f.host.god.read(child.id);
  assert.deepEqual(stored.history,(await f.host.god.read(s.id)).history.slice(0,2));
  assert.equal(stored.cardId,'god');assert.equal(stored.world,null);assert.deepEqual(stored.params,{});
  assert.deepEqual(stored.requests,{});assert.equal('budget' in stored,false);assert.equal('ledger' in stored,false);
  assert.equal(stored.characterText,s.characterText);assert.equal(child.parentId,s.id);
  assert.equal((await f.api('sessions/'+child.id+'/plugins/'+PLUGIN_ID)).data.enabled,false);
  assert.equal((await f.api('sessions/'+s.id+'/branches',input)).status,200);
  assert.equal((await f.api('sessions/'+s.id+'/branches',{...input,name:'碰撞'})).data.error,'BRANCH_OPERATION_COLLISION');
  assert.equal((await f.api('sessions/'+child.id+'/send',{input:'不能重用',requestId:atOne.turns[0].requestId,revision:1})).data.error,'BRANCH_INHERITED_REQUEST_ID');
  const continued=await f.send(child,'子路线独立选择');
  const rawParent=await f.host.god.read(s.id);
  assert.deepEqual(f.wires.at(-1),[{role:'system',content:loadFrozen().system},...rawParent.history.slice(0,2),{role:'user',content:'子路线独立选择'}]);
  assert.ok(!JSON.stringify(f.wires.at(-1)).includes('父路线后续秘密'));
  assert.equal(rawParent.turns.length,2);
  assert.equal((await f.api('sessions/'+s.id+'/plugins/'+PLUGIN_ID+'/nodes')).data.length,1);
  assert.equal((await f.api('sessions')).data.filter(x=>x.parentId===s.id).length,1);
  assert.equal((await f.change('disable',s)).status,200);
  assert.equal((await f.api('sessions/'+s.id+'/branches',f.branch(s))).status,403);
  assert.equal((await f.api('sessions/'+s.id+'/branches',input)).data.replayed,true);
  assert.equal((await f.change('uninstall')).status,200);
  await f.send(continued,'卸载后普通续玩');
  assert.equal((await f.change('install')).status,200);
  assert.equal((await f.api('sessions/'+s.id+'/plugins/'+PLUGIN_ID)).data.enabled,false);
  assert.equal((await f.api('status')).data.used,0);
  assert.equal((await f.host.provider.status('earth')).used,0);
  const envelope=await f.host.god.archive.read(child.id);
  assert.equal(envelope.snapshotHash,sha(canonical(envelope.snapshot)));
  await writeFile(join(f.directory,'branch-wire-proof.json'),JSON.stringify({kind:'offline-only',calls:0,parentId:s.id,childId:child.id,snapshotHash:envelope.snapshotHash,messages:f.wires,branchHistory:envelope.snapshot.history},null,2));
 }finally{await f.close();}
});
test('scoped routes reject Earth sessions, cross-origin writes and unvalidated plugins',async()=>{
 const f=await fixture();
 try{
  const s=await f.send(await f.session());await f.change('install');await f.change('enable',s);
  const earth=(await f.request('earth/api/sessions',{characterText:'姓名：对照',world:'表世界',params:{},mode:'offline'})).data;
  assert.equal((await f.api('sessions/'+earth.id+'/branches',f.branch(earth))).status,404);
  assert.equal((await f.request('earth/api/sessions/'+s.id+'/plugins/'+PLUGIN_ID)).status,404);
  assert.equal((await f.request('earth/api/sessions/'+s.id+'/branches',f.branch(s))).status,404);
  assert.equal((await f.request('earth/api/sessions/'+s.id+'/plugins/'+PLUGIN_ID+'/nodes')).status,404);
  assert.equal((await f.api('sessions/'+s.id+'/branches',f.branch(s),'https://foreign.invalid')).status,403);
  for(const id of ['rpg.model-selector','rpg.history-window','rpg.rolling-summary','rpg.memory-palace']){
   assert.equal((await f.api('sessions/'+s.id+'/plugins/'+id)).data.error,'PLUGIN_CARD_INCOMPATIBLE');
   await f.change('install',null,id);
   assert.equal((await f.change('enable',s,id)).data.error,'PLUGIN_CARD_INCOMPATIBLE');
  }
  assert.equal((await f.api('sessions/'+s.id+'/branches',{...f.branch(s),history:[]})).status,400);
  assert.equal((await f.api('sessions/'+s.id+'/branches',{...f.branch(s),expectedSessionRevision:0})).data.error,'BRANCH_SESSION_CONFLICT');
  const original=await loadReviewedPlugins();
  assert.deepEqual(original.find(p=>p.manifest.id===PLUGIN_ID).manifest.compatibleCards,['earth']);
  assert.equal(original.find(p=>p.manifest.id===PLUGIN_ID).manifest.version,'1.0.0');
 }finally{await f.close();}
});
test('revocation fences final branch publication; lost response recovers same operation without duplicate',async()=>{
 const f=await fixture();
 try{
  const s=await f.send(await f.session());await f.change('install');await f.change('enable',s);
  let release,entered;const hold=new Promise(r=>release=r),ready=new Promise(r=>entered=r);
  const publish=f.host.god.archive.publish.bind(f.host.god.archive);
  f.host.god.archive.publish=async(...args)=>{entered();await hold;return publish(...args);};
  const input=f.branch(s),pending=f.api('sessions/'+s.id+'/branches',input);
  await ready;
  assert.equal((await f.api('sessions/'+s.id+'/branches',f.branch(s))).data.error,'SESSION_BUSY');
  await f.change('disable',s);release();
  assert.equal((await pending).status,403);
  assert.equal((await f.host.god.archive.list()).length,0);
  await f.change('enable',s);
  f.host.god.archive.publish=async(...args)=>{await publish(...args);throw Error('simulated lost response after durable publication');};
  const uncertain=f.branch(s);
  assert.equal((await f.api('sessions/'+s.id+'/branches',uncertain)).status,400);
  f.host.god.archive.publish=publish;
  const recovered=await f.api('sessions/'+s.id+'/branches',uncertain);
  assert.equal(recovered.status,200);assert.equal(recovered.data.replayed,true);
  assert.equal((await f.host.god.archive.list()).length,1);
  const e=await f.host.god.archive.read(recovered.data.session.id);
  assert.deepEqual(e.session.requests,{});
 }finally{await f.close();}
});
test('generation, unknown requests and unfinished turns cannot be branched',async()=>{
 let release,entered;const hold=new Promise(r=>release=r),ready=new Promise(r=>entered=r);
 const f=await fixture(async()=>{entered();await hold;throw Error('synthetic unknown result');});
 try{
  const s=await f.session();await f.change('install');await f.change('enable',s);
  const sending=f.api('sessions/'+s.id+'/send',{input:s.characterText,requestId:'unknown',revision:0});
  await ready;
  assert.equal((await f.api('sessions/'+s.id+'/branches',f.branch(s))).data.error,'SESSION_BUSY');
  release();await sending;
  assert.equal((await f.api('sessions/'+s.id+'/branches',f.branch(s))).data.error,'BRANCH_UNCONFIRMED_REQUEST');
  assert.equal((await f.host.god.archive.list()).length,0);
 }finally{release();await f.close();}
});
test('new reviewed branch release never upgrades existing installation or grants silently',async()=>{
 const directory=await mkdtemp(join(root,'registry-'));
 const lookupSession=async id=>({id,cardId:'earth',revision:0,completedTurns:1,resourceHash:sha('fixture'),busy:false,pending:false});
 let plugins=await createPluginService({directory,lookupSession});
 const c=await plugins.catalog(),m=c.plugins.find(p=>p.id===PLUGIN_ID);
 const binding={pluginId:PLUGIN_ID,version:m.version,artifactSha256:m.artifactSha256,manifestSha256:m.manifestSha256};
 await plugins.change('install',{...binding,operationId:'install-old',expectedRegistryRevision:0});
 await plugins.change('enable',{...binding,operationId:'enable-old',expectedRegistryRevision:1,sessionId:'earth-fixture',expectedSessionRevision:0,permissions:m.permissions});
 await plugins.close();
 plugins=await createPluginService({directory,lookupSession,loadCatalog:()=>loadGodPlugins({})});
 try{
  const entry=(await plugins.catalog()).plugins.find(p=>p.id===PLUGIN_ID);
  assert.equal(entry.version,'1.1.0');assert.equal(entry.installedVersion,'1.0.0');assert.equal(entry.compatibleInstallation,false);
  assert.equal((await plugins.sessionStatus('earth-fixture')).enabled,false);
  await assert.rejects(plugins.invoke({sessionId:'earth-fixture',capability:'ui.message-action'}),/PLUGIN_NOT_INSTALLED/);
  assert.equal(JSON.parse(await readFile(join(directory,'registry.json'),'utf8')).plugins[PLUGIN_ID].installed.version,'1.0.0');
 }finally{await plugins.close();}
});
