import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/rolling-summary-b4/http/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t){const directory=await mkdtemp(join(root,'f-')),h=await start({rollingSummary:true,directory,port:0,limit:0});const origin='http://127.0.0.1:'+h.server.address().port;t.after(async()=>{await h.plugins.close();await new Promise(r=>h.server.close(r));});
 const f={h,async api(path,body,from=origin){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json',Origin:from},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};},async create(){return (await f.api('earth/api/sessions',{characterText:'姓名：离线验收人',world:'表世界',mode:'offline',params:{max_tokens:16384}})).data;},async plugin(action,s){const c=await h.plugins.catalog(),p=c.plugins.find(p=>p.id==='rpg.rolling-summary');return f.api(s?'earth/api/sessions/'+s.id+'/plugins/'+p.id+'/'+action:'api/plugins/'+p.id+'/'+action,{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async status(s){return (await f.api('earth/api/sessions/'+s.id+'/rolling-summary')).data;},async send(s){const x=await f.status(s);return f.api('earth/api/sessions/'+s.id+'/send',{operationId:randomUUID(),expectedSessionRevision:x.sessionRevision,expectedContextPolicyRevision:x.effectivePolicy.revision,input:'继续离线验收'});}};return f;}
const base=s=>'earth/api/sessions/'+s.id+'/rolling-summary';
const settings=h=>({operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedSummaryRevision:h.summaryRevision,timing:'before',selectionId:'offline-sample',catalogRevision:'offline-v1',text:null});
test('M2 HTTP lifecycle, explicit model, atomic settings+edit, raw versions and no real quota',async t=>{
 const f=await fixture(t);let s=await f.create();assert.ok(s.rollingSummary);let h=await f.status(s);assert.equal(h.enabled,false);
 assert.equal((await f.api(base(s)+'/settings',settings(h))).status,403);await f.plugin('install');await f.plugin('enable',s);h=await f.status(s);assert.equal(h.config.model,null);
 const listed=await f.api(base(s)+'/catalog');assert.equal(listed.status,200,JSON.stringify(listed));assert.equal(listed.data.models[0].name,'离线样例（不调用模型）');let body=settings(h),saved=await f.api(base(s)+'/settings',body);assert.equal(saved.status,200,JSON.stringify(saved));s=saved.data.session;assert.equal((await f.api(base(s)+'/settings',body)).data.replayed,true);
 for(let i=0;i<3;i++){const r=await f.send(s);assert.equal(r.status,200,JSON.stringify(r));assert.equal(r.data.operation.status,'complete',JSON.stringify(r));s=r.data;}
 assert.equal(s.turns.length,3);h=await f.status(s);assert.equal(h.versions.length,1);assert.equal(h.versions[0].coveredThrough,1);assert.equal(JSON.stringify(h.taskState).includes('messages'),false);
 const before=h.versions[0];saved=await f.api(base(s)+'/settings',{...settings(h),timing:'after',text:'人工确认的离线修订'});assert.equal(saved.status,200,JSON.stringify(saved));s=saved.data.session;h=await f.status(s);assert.equal(h.config.timing,'after');assert.deepEqual(h.versions[0],before);assert.equal(h.versions.at(-1).effectiveText,'人工确认的离线修订');
 assert.equal((await f.api('earth/api/status')).data.budget.used,0);assert.equal((await f.api('earth/api/status')).data.providerEnabled,false);
 await f.plugin('disable',s);assert.equal((await f.status(s)).effectivePolicy.mode,'full-history');await f.plugin('uninstall');await f.plugin('install');assert.equal((await f.status(s)).enabled,false);
});
test('M2 HTTP strict fields, same-origin, stale revision and recovery do not generate',async t=>{
 const f=await fixture(t),s=await f.create();await f.plugin('install');await f.plugin('enable',s);const h=await f.status(s),body=settings(h);
 assert.equal((await f.api(base(s)+'/settings',body,'http://evil.invalid')).status,403);
 for(const b of [{...body,system:'arbitrary'},{...body,text:''},{...body,timing:'unknown'},{...body,selectionId:'arbitrary-url'}])assert.notEqual((await f.api(base(s)+'/settings',b)).status,200);
 const saved=await f.api(base(s)+'/settings',body);assert.equal(saved.status,200,JSON.stringify(saved));assert.equal((await f.api(base(s)+'/settings',{...body,operationId:randomUUID()})).status,409);
 const current=await f.status(s),r=await f.api(base(s)+'/recover',{operationId:body.operationId,expectedSessionRevision:current.sessionRevision,expectedSummaryRevision:current.summaryRevision});assert.equal(r.data.operation.status,'complete');assert.equal(r.data.session.turns.length,0);
 const unknown=await f.api(base(s)+'/recover',{operationId:'never-dispatched',expectedSessionRevision:current.sessionRevision,expectedSummaryRevision:current.summaryRevision});assert.equal(unknown.data.operation.status,'not-found');assert.equal((await f.status(s)).versions.length,0);
});

test('M2 HTTP send remains atomic with M1 enabled and concurrent UI status polling',async t=>{
 const f=await fixture(t);let s=await f.create();await f.plugin('install');await f.plugin('enable',s);await f.api(base(s)+'/settings',settings(await f.status(s)));
 let c=await f.h.plugins.catalog(),p=c.plugins.find(x=>x.id==='rpg.history-window');await f.h.plugins.change('install',{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision});
 c=await f.h.plugins.catalog();s=await f.h.earth.read(s.id);await f.h.plugins.change('enable',{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,sessionId:s.id,expectedSessionRevision:s.revision,permissions:p.permissions});
 const failures=[],original=f.h.earth.write.bind(f.h.earth);f.h.earth.write=async value=>{try{return await original(value);}catch(e){failures.push({code:e.code,message:e.message});throw e;}};
 const polling=(async()=>{for(let i=0;i<24;i++){await Promise.all([f.status(s),f.api('earth/api/sessions/'+s.id),f.api('earth/api/sessions/'+s.id+'/history-window')]);await new Promise(resolve=>setTimeout(resolve,50));}})();const r=await f.send(s);await polling;
 assert.equal(r.status,200,JSON.stringify({r,failures}));assert.equal(r.data.operation.status,'complete',JSON.stringify({r,failures}));assert.equal(r.data.turns.length,1);assert.deepEqual(failures,[]);
});
