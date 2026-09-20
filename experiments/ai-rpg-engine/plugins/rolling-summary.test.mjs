import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {planCoverage,validText,invoke} from './rolling-summary.mjs';
import {loadReviewedPlugins,loadReviewedCatalog,verifyPackage,ROLLING_SUMMARY_ID as R,HISTORY_WINDOW_ID as H,sha} from './catalog.mjs';
import {createPluginService} from './host.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/rolling-summary-b1/plugins/',import.meta.url));await mkdir(root,{recursive:true});
const history=Array.from({length:12},(_,i)=>({role:i%2?'assistant':'user',content:'原文 '+i+'\n<LSM>保持原样</LSM>'}));
test('coverage at 0/1 and first plus two incremental updates; source copies cannot mutate history',()=>{
 for(let T=0;T<=6;T++)for(let covered=0;covered<=Math.max(0,T-1);covered++){
  const h=history.slice(0,T*2),before=structuredClone(h),p=planCoverage(h,covered);
  assert.equal(p.targetThrough,Math.max(0,T-1));assert.equal(p.needsUpdate,covered<p.targetThrough);
  assert.deepEqual(p.source,h.slice(covered*2,p.targetThrough*2));assert.deepEqual(p.recent,T?h.slice(-2):[]);
  if(p.source.length)p.source[0].content='changed';assert.deepEqual(h,before);
 }
 assert.throws(()=>planCoverage(history,6),/SUMMARY_COVERAGE_INVALID/);
 assert.throws(()=>planCoverage(history.slice(1)),/SUMMARY_HISTORY_INCOMPLETE/);
 assert.throws(()=>planCoverage([{role:'assistant',content:'a'},{role:'user',content:'u'}]),/SUMMARY_HISTORY_INCOMPLETE/);
});
test('Unicode limit, blank rejection and bounded proposals without model/network capability',()=>{
 assert.equal(validText('😀'.repeat(10000)),true);assert.equal(validText('😀'.repeat(10001)),false);
 for(const v of ['', ' \t\n',null,5])assert.equal(validText(v),false);
 assert.throws(()=>invoke({capability:'network.fetch',session:{id:'s'}}));
 assert.throws(()=>invoke({capability:'session.summary.configure',session:{id:'s'},input:{timing:'before',selectionId:'x',catalogRevision:'v',system:'injection'}}));
});
async function fixture(t){
 const sessions=new Map([['new',{id:'new',cardId:'earth',revision:0,completedTurns:3,resourceHash:sha('new'),historyWindowCompatible:true,rollingSummaryCompatible:true}],['old',{id:'old',cardId:'earth',revision:0,completedTurns:3,resourceHash:sha('old'),historyWindowCompatible:true}]]);
 const directory=await mkdtemp(join(root,'case-')),host=await createPluginService({directory,lookupSession:async id=>structuredClone(sessions.get(id)),loadCatalog:()=>loadReviewedPlugins({rollingSummary:true})});t.after(()=>host.close());
 async function change(action,sessionId,pluginId=R,extra={}){const c=await host.catalog(),p=c.plugins.find(x=>x.id===pluginId);return host.change(action,{operationId:randomUUID(),pluginId,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(sessionId?{sessionId,expectedSessionRevision:0}:{}),...(action==='enable'?{permissions:p.permissions}:{}),...extra});}
 return {host,change,sessions};
}
test('opt-in fourth artifact; install never enables; permissions, runtime and hash rejected',async t=>{
 assert.equal((await loadReviewedPlugins()).length,3);const f=await fixture(t);assert.equal((await f.host.catalog()).plugins.length,4);
 const p=await loadReviewedCatalog(R);assert.equal(p.manifest.version,'1.1.0');assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify({...p.manifest,version:'1.0.0'})),Buffer.from('not-used')),/PLUGIN_MANIFEST_INVALID/);assert.equal(p.manifest.network,'none');assert.equal(p.manifest.modelAccess,false);
 assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify(p.manifest)),Buffer.from('wrong')),/PLUGIN_ARTIFACT_MISMATCH/);
 await f.change('install');assert.equal((await f.host.summaryAuthorization('new')).enabled,false);
 await assert.rejects(f.host.invoke({pluginId:R,sessionId:'new',capability:'session.summary.request'}),/PLUGIN_NOT_AUTHORIZED/);
 await assert.rejects(f.change('enable','old'),/SUMMARY_RUNTIME_INCOMPATIBLE/);
 await assert.rejects(f.change('enable','new',R,{permissions:[]}),/PLUGIN_PERMISSION_MISMATCH/);
 await assert.rejects(f.change('enable','new',R,{artifactSha256:sha('wrong')}),/PLUGIN_VERSION_MISMATCH/);
 await f.change('enable','new');await assert.rejects(f.host.invoke({pluginId:R,sessionId:'new',capability:'provider.dispatch'}),/PLUGIN_CAPABILITY_DENIED/);
});
test('revoke invalidates proposal; reinstall does not restore grant; M1 grant remains',async t=>{
 const f=await fixture(t);for(const p of [R,H]){await f.change('install',null,p);await f.change('enable','new',p);}
 const a=await f.host.summaryAuthorization('new'),ticket=await f.host.invoke({pluginId:R,sessionId:'new',capability:'session.summary.request'});
 await f.change('disable','new');assert.notEqual((await f.host.summaryAuthorization('new')).revision,a.revision);
 await assert.rejects(f.host.commitResult(ticket,()=>assert.fail('revoked commit')),/PLUGIN_NOT_AUTHORIZED/);
 assert.equal((await f.host.historyAuthorization('new')).enabled,true);
 await f.change('enable','new');await f.change('uninstall');await f.change('install');assert.equal((await f.host.summaryAuthorization('new')).enabled,false);
 await f.change('enable','new');f.sessions.get('new').resourceHash=sha('tampered');await assert.rejects(f.host.summaryAuthorization('new'),/PLUGIN_NOT_AUTHORIZED/);
});
