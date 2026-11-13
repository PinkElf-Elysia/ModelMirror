import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {createPluginService} from './host.mjs';
import {loadReviewedPlugins,sha} from './catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/plugin-upgrade/',import.meta.url));
await mkdir(root,{recursive:true});
async function fixture(t){
 const directory=await mkdtemp(join(root,'case-'));
 const latest=(await loadReviewedPlugins({messageTools:true})).find(e=>e.manifest.id==='rpg.message-tools');
 let entry={...latest,manifest:{...latest.manifest,version:'1.0.0',artifactSha256:sha('old-artifact'),permissions:['session.completed.read','session.message.fork','ui.contribute']},artifactSha256:sha('old-artifact'),manifestSha256:sha('old-manifest')};
 const sessions=new Map(['one','two'].map(id=>[id,{id,cardId:'earth',resourceHash:sha(id),revision:7,completedTurns:2,busy:false,pending:false}]));
 const options={directory,lookupSession:async id=>structuredClone(sessions.get(id)),loadCatalog:async()=>[entry]};
 let host=await createPluginService(options);
 t.after(async()=>{await host.close();await rm(directory,{recursive:true,force:true});});
 const f={directory,sessions,get host(){return host;},async reopen(){await host.close();host=await createPluginService(options);},async latest(){entry=latest;await f.reopen();},async payload(action,id='one'){
  const c=await host.catalog(),p=c.plugins[0];return {operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(['enable','disable'].includes(action)?{sessionId:id,expectedSessionRevision:7}:{}),...(['enable','upgrade'].includes(action)?{permissions:p.permissions}:{}),...(action==='upgrade'?{expectedInstalled:p.installedBinding}:{})};
 },async change(action,id){return host.change(action,await f.payload(action,id));},async state(){return JSON.parse(await readFile(join(directory,'registry.json'),'utf8'));}};
 await f.change('install');await f.change('enable');await f.change('enable','two');await f.change('disable','two');return f;
}
test('upgrade preserves independent switches, identity, sessions and survives restart; replay is idempotent',async t=>{
 const f=await fixture(t),sessions=structuredClone(f.sessions),old=await f.state();await f.latest();
 let c=(await f.host.catalog()).plugins[0];assert.equal(c.updateAvailable,true);assert.equal(c.installedVersion,'1.0.0');
 assert.equal((await f.host.sessionStatus('one',c.id)).reason,'PLUGIN_UPGRADE_REQUIRED');
 const p=await f.payload('upgrade'),r=await f.host.change('upgrade',p);
 assert.equal((await f.host.change('upgrade',p)).revision,r.revision);await f.reopen();
 c=(await f.host.catalog()).plugins[0];assert.equal(c.compatibleInstallation,true);assert.equal(c.updateAvailable,false);assert.equal(c.versionHistory.at(-1).action,'upgrade');
 assert.equal((await f.host.sessionStatus('one',c.id)).enabled,true);assert.equal((await f.host.sessionStatus('two',c.id)).enabled,false);
 assert.deepEqual(f.sessions,sessions);const now=await f.state();assert.equal(now.plugins[c.id].installed.installationId,old.plugins[c.id].installed.installationId);
 assert.deepEqual(now.plugins[c.id].grants.one.permissions,c.permissions);
 const result=await f.host.invoke({pluginId:c.id,sessionId:'one',capability:'session.message.edit',input:{turn:1}});assert.equal(result.proposal.kind,'message-edit');
 await assert.rejects(f.change('upgrade'),/PLUGIN_ALREADY_CURRENT/);
});
test('stale registry, source installation, permissions, package hashes and active requests reject without writes',async t=>{
 const f=await fixture(t);await f.latest();const before=await f.state(),p=await f.payload('upgrade');
 for(const [patch,code] of [[{expectedRegistryRevision:0},'PLUGIN_REGISTRY_CONFLICT'],[{expectedInstalled:{...p.expectedInstalled,installationId:randomUUID()}},'PLUGIN_INSTALLATION_CONFLICT'],[{permissions:[]},'PLUGIN_PERMISSION_MISMATCH'],[{artifactSha256:sha('tampered')},'PLUGIN_VERSION_MISMATCH']])await assert.rejects(f.host.change('upgrade',{...p,...patch}),new RegExp(code));
 for(const key of ['busy','pending']){f.sessions.get('one')[key]=true;await assert.rejects(f.change('upgrade'),/PLUGIN_UPGRADE_BUSY/);f.sessions.get('one')[key]=false;}
 f.sessions.delete('one');await assert.rejects(f.change('upgrade'),/PLUGIN_UPGRADE_SESSION_UNAVAILABLE/);assert.deepEqual(await f.state(),before);
});
test('old format-2 registry without history loads, stale installed package can be uninstalled using its exact binding',async t=>{
 const f=await fixture(t);await f.host.close();const s=await f.state();for(const p of Object.values(s.plugins))delete p.versions;await writeFile(join(f.directory,'registry.json'),JSON.stringify(s));await f.latest();
 const c=(await f.host.catalog()).plugins[0],body=await f.payload('uninstall');
 await assert.rejects(f.host.change('uninstall',body),/PLUGIN_VERSION_MISMATCH/);
 const {installationId,...binding}=c.installedBinding;await f.host.change('uninstall',{...body,...binding});assert.equal((await f.host.catalog()).plugins[0].installed,false);
});
test('failed upgrade persistence leaves original registry and grants intact',async t=>{
 const f=await fixture(t);await f.latest();const before=await f.state();const path=join(f.directory,'registry.json');await rm(path);await mkdir(path);
 await assert.rejects(f.change('upgrade'),/PLUGIN_PERSIST_FAILED/);assert.equal((await f.host.catalog()).plugins[0].installedVersion,'1.0.0');await rm(path,{recursive:true});await writeFile(path,JSON.stringify(before));await f.reopen();assert.deepEqual(await f.state(),before);
});
