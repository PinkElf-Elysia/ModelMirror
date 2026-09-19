import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,rm,writeFile,readdir} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
import {models} from './provider.mjs';
import {sha} from '../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/plugin-b2-http/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t){
 const directory=await mkdtemp(join(root,'http-'));let calls=0;const payloads=[];
 const host=await start({port:0,directory,key:'test-only-key',enabled:true,limit:2,fetcher:async(url,options)=>{calls++;payloads.push(JSON.parse(options.body));return new Response('data: '+JSON.stringify({model:models.earth.model,provider:models.earth.provider,choices:[{delta:{content:'虚构故事原文 '+calls+'\n\t尾行'},finish_reason:'stop'}]})+'\n\ndata: [DONE]\n\n',{headers:{'content-type':'text/event-stream'}});}});
 const origin='http://127.0.0.1:'+host.server.address().port;
 t.after(async()=>{await host.plugins?.close();await new Promise(r=>host.server.close(r));assert.equal(dirname(directory),root.replace(/[\\/]$/,''));await rm(directory,{recursive:true,force:true});});
 const f={host,directory,payloads,get calls(){return calls;},async api(path,body,from=origin){const res=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:from,'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:res.status,data:await res.json()};},async change(action,s){const cat=await host.plugins.catalog(),p=cat.plugins[0];return host.plugins.change(action,{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:cat.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async session(){const created=await this.api('earth/api/sessions',{characterText:'姓名：虚构测试旅人',world:'表世界',params:{max_tokens:16384},mode:'real'});assert.equal(created.status,201);const sent=await this.api('earth/api/sessions/'+created.data.id+'/send',{requestId:randomUUID(),revision:0,input:'开始'});assert.equal(sent.status,200);return sent.data;}};
 return f;
}
const command=(s,extra={})=>({operationId:'branch-operation',expectedSessionRevision:s.revision,turn:1,name:'向北走',...extra});
test('HTTP branch needs explicit grant, preserves raw prefix, appears in ordinary list; uninstall keeps core usable and budget shared',async t=>{
 const f=await fixture(t),s=await f.session(),path='earth/api/sessions/'+s.id+'/branches';assert.equal(f.calls,1);
 assert.equal((await f.api(path,command(s))).status,409);await f.change('install');assert.equal((await f.api(path,command(s))).status,403);await f.change('enable',s);
 assert.equal((await f.api(path,command(s),'https://foreign.invalid')).status,403);assert.equal((await f.api(path,{...command(s),system:'malicious'})).status,400);
 const made=await f.api(path,command(s));assert.equal(made.status,201);const child=made.data.session;
 assert.equal(child.parentId,s.id);assert.equal(child.branchTurn,1);assert.equal(child.runtime.compatible,true);assert.equal(child.turns[0].raw,s.turns[0].raw);assert.deepEqual(child.requests,{});assert.equal(f.calls,1);
 assert.equal((await f.host.plugins.sessionStatus(child.id)).enabled,false);assert.equal((await f.api('earth/api/sessions')).data.length,2);
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/plugins/rpg.branch-save/nodes')).data[0].id,child.id);
 assert.equal((await f.api(path,command(s))).status,200);assert.equal((await f.api(path,command(s,{name:'另一名'}))).data.error,'BRANCH_OPERATION_COLLISION');
 await f.change('uninstall');assert.equal((await f.api('earth/api/sessions/'+child.id)).status,200);
 const next=await f.api('earth/api/sessions/'+child.id+'/send',{requestId:'child-new',revision:child.revision,input:'沿河向北走'});assert.equal(next.status,200);assert.equal(next.data.turns.length,2);assert.equal(f.calls,2);
 assert.equal((await f.host.provider.status('earth')).remaining,0);const parent=await f.api('earth/api/sessions/'+s.id+'/send',{requestId:'parent-new',revision:s.revision,input:'原路继续'});assert.equal(parent.data.requests['parent-new'].status,'failed');assert.equal(parent.data.turns.length,1);assert.equal(f.calls,2);
 await f.change('install');assert.equal((await f.host.plugins.sessionStatus(s.id)).enabled,false);await f.change('enable',s);
 const noBudget=await f.api(path,command(s,{operationId:'save-with-no-budget',name:'额度耗尽仍可分支'}));assert.equal(noBudget.status,201);assert.equal(f.calls,2);assert.equal((await f.host.provider.status('earth')).used,2);
 // Dispatch receipts remain at the original shared location, with original session IDs.
 assert.equal((await readdir(join(f.directory,'dispatches/earth'))).length,2);
});
test('revocation during staged publication invalidates the prepared result; enable again cannot revive it',async t=>{
 const f=await fixture(t),s=await f.session();await f.change('install');await f.change('enable',s);
 let release,entered;const enteredStage=new Promise(r=>entered=r),publish=f.host.earth.archive.publish.bind(f.host.earth.archive);
 f.host.earth.archive.publish=(envelope,commit)=>publish(envelope,async doPublish=>{entered();await new Promise(r=>release=r);return commit(doPublish);});
 const pending=f.api('earth/api/sessions/'+s.id+'/branches',command(s));await enteredStage;
 await f.change('disable',s);await f.change('enable',s);release();const result=await pending;assert.equal(result.status,409);assert.equal(result.data.error,'PLUGIN_RESULT_STALE');assert.equal((await f.host.earth.list()).length,1);
 assert.deepEqual(await readdir(f.host.earth.archive.directory),[]);assert.equal(f.calls,1);
});
test('commit linearization and disable are serialized; consumed ticket cannot publish twice',async t=>{
 const f=await fixture(t),s=await f.session();await f.change('install');await f.change('enable',s);
 const result=await f.host.plugins.invoke({sessionId:s.id,capability:'session.branch.prepare',input:{turn:1,name:'test'}});let release,entered;const began=new Promise(r=>entered=r);
 const commit=f.host.plugins.commitResult(result,async()=>{entered();await new Promise(r=>release=r);return 'published';});await began;let revoked=false;const disable=f.change('disable',s).then(()=>revoked=true);await new Promise(r=>setImmediate(r));assert.equal(revoked,false);release();assert.equal(await commit,'published');await disable;
 await assert.rejects(f.host.plugins.commitResult(result,()=>{throw Error('must not run');}),/PLUGIN_RESULT_INVALID/);
});
test('legacy evidence cannot prove frozen assembler; source stays readable, no dispatch or branch',async t=>{
 const f=await fixture(t),s=await f.session(),raw=await f.host.earth.read(s.id);delete raw.runtime;await f.host.earth.write(raw);
 const view=await f.api('earth/api/sessions/'+s.id);assert.equal(view.status,200);assert.equal(view.data.runtime.code,'RUNTIME_UNVERIFIED_LEGACY');assert.equal(view.data.turns[0].rawHash,sha(view.data.turns[0].raw));
 await f.change('install');await f.change('enable',s);assert.equal((await f.api('earth/api/sessions/'+s.id+'/branches',command(s))).data.error,'RUNTIME_UNVERIFIED_LEGACY');
 assert.equal((await f.api('earth/api/sessions/'+s.id+'/send',{input:'继续',requestId:'new',revision:1})).data.error,'RUNTIME_UNVERIFIED_LEGACY');assert.equal(f.calls,1);
});
test('branch-of-branch keeps ancestor request references; plugin-disabled reader can restore both branches',async t=>{
 const f=await fixture(t),s=await f.session();await f.change('install');await f.change('enable',s);
 const one=(await f.api('earth/api/sessions/'+s.id+'/branches',command(s))).data.session;await f.change('enable',one);
 const two=(await f.api('earth/api/sessions/'+one.id+'/branches',command(one))).data.session;assert.equal(two.parentId,one.id);
 const raw=await f.host.earth.read(two.id);assert.equal(raw.provenance.requestOrigins[0].sessionId,s.id);assert.deepEqual(raw.requests,{});
 await f.change('uninstall');await f.host.plugins.close();await writeFile(join(f.directory,'plugins/registry.json'),'corrupt');
 const reader=await start({port:0,directory:f.directory});try{assert.equal(reader.plugins,null);assert.equal((await reader.earth.list()).length,3);assert.deepEqual((await reader.earth.read(two.id)).history,raw.history);assert.equal((await reader.provider.status('earth')).used,1);}finally{await new Promise(r=>reader.server.close(r));}
});
