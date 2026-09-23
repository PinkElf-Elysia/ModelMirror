import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from './server.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/memory-palace-b4/http/',import.meta.url));await mkdir(root,{recursive:true});
async function fixture(t,memoryPalace=true){const directory=await mkdtemp(join(root,'f-')),h=await start({memoryPalace,rollingSummary:true,directory,port:0,limit:0});const origin='http://127.0.0.1:'+h.server.address().port;t.after(async()=>{await h.plugins.close();await new Promise(r=>h.server.close(r));});
 const f={h,async api(path,body,from=origin){const r=await fetch(origin+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json',Origin:from},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};},async create(){return (await f.api('earth/api/sessions',{characterText:'姓名：离线验收人',world:'表世界',mode:'offline',params:{max_tokens:16384}})).data;},async plugin(action,s,id='rpg.memory-palace'){const c=await h.plugins.catalog(),p=c.plugins.find(p=>p.id===id);return f.api(s?'earth/api/sessions/'+s.id+'/plugins/'+p.id+'/'+action:'api/plugins/'+p.id+'/'+action,{operationId:randomUUID(),pluginId:p.id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision,...(s?{sessionId:s.id,expectedSessionRevision:s.revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},async status(s){return (await f.api(base(s))).data;}};return f;}
const base=s=>'earth/api/sessions/'+s.id+'/memory-palace';
const settings=h=>({operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,selectionId:'offline-sample',catalogRevision:'offline-v1'});
const value={name:'离线手记',keywords:['离线'],match:'any',roles:['user','assistant'],content:'手工保留的离线资料。',enabled:true};
test('M3 HTTP lifecycle, protected editing, recovery and background results preserve story projection',async t=>{
 const f=await fixture(t);let s=await f.create();assert.ok(s.memoryPalace);let h=await f.status(s);assert.equal(h.enabled,false);
 assert.equal((await f.api(base(s)+'/settings',settings(h))).status,409);assert.equal((await f.plugin('install')).status,200);assert.equal((await f.plugin('enable',s)).status,200);h=await f.status(s);assert.equal(h.config.model,null);
 assert.equal((await f.api(base(s)+'/catalog')).data.models[0].name,'离线样例（不调用模型）');let body=settings(h),r=await f.api(base(s)+'/settings',body);assert.equal(r.status,200,JSON.stringify(r));assert.equal((await f.api(base(s)+'/settings',body)).data.replayed,true);
 h=await f.status(s);body={operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,change:{action:'create',value}};r=await f.api(base(s)+'/edit',body);assert.equal(r.status,200,JSON.stringify(r));assert.equal((await f.api(base(s)+'/edit',body)).data.replayed,true);
 h=await f.status(s);assert.equal(h.entries[0].protected,true);assert.equal(h.originalOutputs.length,0);assert.equal((await f.api(base(s)+'/recover',{operationId:body.operationId,expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision})).data.operation.status,'complete');
 const key=randomUUID();r=await f.api('earth/api/sessions/'+s.id+'/send',{operationId:key,expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input:'继续离线验收'});assert.equal(r.status,200,JSON.stringify(r));assert.equal(r.data.requests[key].status,'complete');await f.h.earth.memoryTasks.wait(s.id);
 h=await f.status(s);assert.equal(h.processedThrough,1);assert.equal(h.entries[0].value.content,value.content);assert.equal(h.originalOutputs.length,1);assert.ok(h.originalOutputs[0].raw);assert.equal(h.lastRecall.results[0].selected,true);assert.equal(JSON.stringify(h.operations).includes('messages'),false);
 const summary=await f.api('earth/api/sessions/'+s.id+'/rolling-summary');assert.equal(summary.status,200,JSON.stringify(summary));assert.equal(summary.data.taskState.operations[key].status,'complete');
 const saved=(await f.api('earth/api/sessions/'+s.id)).data;assert.equal(saved.requests[key].status,'complete');assert.equal(saved.turns.length,1);assert.equal((await f.api('earth/api/status')).data.budget.used,0);assert.equal((await f.api('earth/api/status')).data.providerEnabled,false);
 assert.equal((await f.plugin('disable',saved)).status,200);assert.equal((await f.status(s)).enabled,false);await f.plugin('uninstall');await f.plugin('install');h=await f.status(s);assert.equal(h.enabled,false);assert.equal(h.entries.length,1);
});
test('M3 HTTP rejects forged fields, wrong origins and stale revisions without dispatch',async t=>{
 const f=await fixture(t),s=await f.create();await f.plugin('install');await f.plugin('enable',s);let h=await f.status(s),body=settings(h);
 assert.equal((await f.api(base(s)+'/settings',body,'http://evil.invalid')).status,403);
 for(const b of [{...body,system:'forged'},{...body,selectionId:'https://example.invalid'}])assert.notEqual((await f.api(base(s)+'/settings',b)).status,200);
 assert.equal((await f.api(base(s)+'/settings',body)).status,200);assert.equal((await f.api(base(s)+'/settings',{...body,operationId:randomUUID()})).status,409);
 h=await f.status(s);assert.equal((await f.api(base(s)+'/edit',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,change:{action:'create',value:{...value,roles:[]}}})).status,400);
 const r=await f.api(base(s)+'/recover',{operationId:'never-dispatched',expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision});assert.equal(r.data.operation.status,'not-found');assert.equal(r.data.session.turns.length,0);assert.equal((await f.status(s)).entries.length,0);
});
test('M2 host still defaults to no M3 catalog and old sessions are read-only incompatible',async t=>{const f=await fixture(t,false),s=await f.create();assert.equal((await f.h.plugins.catalog()).plugins.some(p=>p.id==='rpg.memory-palace'),false);const next=await fixture(t);await next.h.earth.write(await f.h.earth.read(s.id));assert.equal((await next.status(s)).reason,'MEMORY_RUNTIME_INCOMPATIBLE');assert.equal((await next.api(base(s)+'/settings',settings({sessionRevision:s.revision,memoryRevision:0}))).status,409);});

test('M3 HTTP coordinates M2 background updates while polling and disabling retains M2',async t=>{
 const f=await fixture(t),s=await f.create();await f.plugin('install');await f.plugin('enable',s);await f.api(base(s)+'/settings',settings(await f.status(s)));
 await f.plugin('install',null,'rpg.rolling-summary');await f.plugin('enable',await f.h.earth.read(s.id),'rpg.rolling-summary');
 const summaryPath='earth/api/sessions/'+s.id+'/rolling-summary';let h=(await f.api(summaryPath)).data;
 let result=await f.api(summaryPath+'/settings',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedSummaryRevision:h.summaryRevision,timing:'after',selectionId:'offline-sample',catalogRevision:'offline-v1',text:null});assert.equal(result.status,200,JSON.stringify(result));
 for(let n=0;n<3;n++){h=await f.status(s);const operationId=randomUUID();const sending=f.api('earth/api/sessions/'+s.id+'/send',{operationId,expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input:'继续离线组合验收'});for(let k=0;k<4;k++){const states=await Promise.all([f.api(base(s)),f.api(summaryPath)]);for(const r of states)assert.equal(r.status,200,JSON.stringify(r));}result=await sending;assert.equal(result.status,200,JSON.stringify(result));assert.equal(result.data.requests[operationId].status,'complete');await f.h.earth.memoryTasks.wait(s.id);}
 h=await f.status(s);assert.equal(h.processedThrough,3);const summary=(await f.api(summaryPath)).data;assert.equal(summary.versions.at(-1).coveredThrough,2);assert.equal(summary.effectivePolicy.memoryEnabled,true);assert.equal(summary.taskState.blocked,null);
 await f.plugin('disable',await f.h.earth.read(s.id));h=await f.status(s);assert.equal(h.enabled,false);assert.equal((await f.api(summaryPath)).data.enabled,true);assert.equal(h.effectivePolicy.mode,'rolling-summary');assert.equal((await f.api('earth/api/status')).data.budget.used,0);
});
