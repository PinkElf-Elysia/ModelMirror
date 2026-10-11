import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile,readdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from '../../studio/server.mjs';
import {newSetup} from '../lib/setup.mjs';
import {loadFrozen} from '../lib/assembly.mjs';
import {createGodStore} from '../lib/adapter.mjs';
import {canonical} from '../../plugins/catalog.mjs';
const root=fileURLToPath(new URL('../../.rpg04-work/god-card/b3-http/',import.meta.url));await mkdir(root,{recursive:true});
const ids=['rpg.branch-save','rpg.model-selector','rpg.history-window','rpg.rolling-summary','rpg.memory-palace'];
const base=s=>'god/api/sessions/'+s.id;
async function fixture(t,{features=true,generate=async()=>'<details><summary>钟楼</summary>修复尚未完成。</details>'}={}){
 const directory=await mkdtemp(join(root,'case-'));let host;
 const f={directory,async boot(){host=await start({port:0,directory,godCard:true,godPlugins:features,limit:0,godGenerate:generate});f.host=host;},async close(){await host.plugins.close();await new Promise(r=>host.server.close(r));},
 async api(path,body,origin=null){const url='http://127.0.0.1:'+host.server.address().port;const r=await fetch(url+'/rpg-app/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin||url,'Content-Type':'application/json'},body:JSON.stringify(body)});return {status:r.status,data:await r.json()};},
 async ok(path,body){const r=await f.api(path,body);assert.ok(r.status>=200&&r.status<300,JSON.stringify(r));return r.data;},
 async create(){const setup=newSetup();setup.user.姓名='虚构神明';setup.opening='初临钟楼';return f.ok('god/api/sessions',{setup,mode:'offline'});},
 async change(action,s,id){const catalog=await f.ok('api/plugins'),p=catalog.plugins.find(p=>p.id===id);return f.ok(s?base(s)+'/plugins/'+id+'/'+action:'api/plugins/'+id+'/'+action,{operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:catalog.revision,...(s?{sessionId:s.id,expectedSessionRevision:(await f.ok(base(s))).revision}:{}),...(action==='enable'?{permissions:p.permissions}:{})});},
 async state(s){return f.ok(base(s)+'/memory-palace');},
 async send(s,input){const h=await f.state(s),current=await f.ok(base(s)),body={operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input:input??(current.turns.length?'再访钟楼':current.characterText)};const result=await f.ok(base(s)+'/send',body);await host.god.memoryTasks.wait(s.id);await host.god.summaryTasks.wait(s.id);return {body,result};}
 };
 await f.boot();t.after(()=>f.close());return f;
}
test('God production HTTP opt-in: five plugin lifecycle, configuration, actual requests, branch and restart',async t=>{
 const f=await fixture(t),s=await f.create();assert.equal(s.runtime.compatible,true);assert.equal((await f.state(s)).enabled,false);
 await f.send(s); // zero plugin path before any install
 for(const id of ids){await f.change('install',null,id);assert.equal((await f.ok(base(s)+'/plugins/'+id)).enabled,false);await f.change('enable',s,id);}
 const options=await f.ok(base(s)+'/model-catalog');assert.equal(options.models.length,2);
 let current=await f.ok(base(s));let h=await f.ok(base(s)+'/history-window');
 await f.ok(base(s)+'/history-window',{operationId:randomUUID(),expectedSessionRevision:current.revision,expectedConfigRevision:h.configRevision,turns:1,includeInitialCharacter:false});
 h=await f.state(s);const memorySetting={operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,selectionId:'god-offline-b',catalogRevision:'offline-v1'};
 await f.ok(base(s)+'/memory-palace/settings',memorySetting);assert.equal((await f.ok(base(s)+'/memory-palace/settings',memorySetting)).replayed,true);
 h=await f.ok(base(s)+'/rolling-summary');await f.ok(base(s)+'/rolling-summary/settings',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedSummaryRevision:h.summaryRevision,timing:'after',selectionId:'god-offline-a',catalogRevision:'offline-v1',text:null});
 h=await f.state(s);await f.ok(base(s)+'/memory-palace/edit',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,change:{action:'create',value:{name:'钟楼旧约',keywords:['钟楼'],match:'any',roles:['user','assistant'],content:'旧约仍待履行。',enabled:true}}});
 current=await f.ok(base(s));await f.ok(base(s)+'/model-selection',{operationId:randomUUID(),expectedSessionRevision:current.revision,selectionRevision:current.modelSelection.revision,selectionId:'god-offline-b',catalogRevision:'offline-v1'});
 const before=await f.host.god.read(s.id),original=canonical(before.history);const sent=await f.send(s);await f.send(s);
 let disk=await f.host.god.read(s.id);assert.equal(canonical(disk.history.slice(0,2)),original);assert.equal(disk.memoryPalace.processedThrough,3);assert.equal(disk.rollingSummary.versions.at(-1).coveredThrough,2);
 assert.equal(disk.modelState.current.model,'synthetic/god-offline-b');assert.equal(disk.rollingSummary.config.model.model,'synthetic/god-offline-a');assert.equal(disk.memoryTasks.config.model.model,'synthetic/god-offline-b');
 assert.equal((await f.ok(base(s)+'/history-window')).temporarilyOverridden,true);
 const branchBody={operationId:randomUUID(),expectedSessionRevision:disk.revision,turn:2,name:'另一条路线'};
 const branch=await f.ok(base(s)+'/branches',branchBody);assert.equal((await f.ok(base(s)+'/branches',branchBody)).replayed,true);
 const child=branch.session;assert.equal(child.turns.length,2);const rawChild=await f.host.god.read(child.id);assert.deepEqual(rawChild.history,disk.history.slice(0,4));assert.deepEqual(rawChild.requests,{});assert.equal(rawChild.memoryPalace.processedThrough,2);
 for(const id of ids)assert.equal((await f.ok(base(child)+'/plugins/'+id)).enabled,false);
 assert.equal((await f.state(child)).effectivePolicy.mode,'full-history');await f.send(child,'分支继续观察钟楼');
 assert.equal((await f.host.god.read(s.id)).turns.length,3);
 const summary=await f.ok(base(s)+'/rolling-summary');await f.ok(base(s)+'/rolling-summary/edit',{operationId:randomUUID(),expectedSessionRevision:summary.sessionRevision,expectedSummaryRevision:summary.summaryRevision,text:'父路线后来才确认的摘要。'});
 assert.ok(!JSON.stringify(await f.host.god.read(child.id)).includes('父路线后来'));
 await f.change('disable',s,'rpg.rolling-summary');assert.equal((await f.state(s)).effectivePolicy.mode,'history-window');
 await f.send(s,'窗口路径钟楼');const windowed=await f.host.god.read(s.id);assert.deepEqual(windowed.turns.at(-1).contextPolicy.selectedTurns,[3]);
 await f.change('uninstall',null,'rpg.memory-palace');await f.change('install',null,'rpg.memory-palace');assert.equal((await f.state(s)).enabled,false);assert.equal((await f.state(s)).entries.length,1);
 const ledger=join(f.directory,'god','offline-plugin-dispatches','god'),slots=(await readdir(ledger)).filter(n=>/^slot-/.test(n)),wires=[];
 for(const slot of slots){const path=join(ledger,slot,'request.json');try{wires.push(JSON.parse(await readFile(path,'utf8')));}catch(e){if(e.code!=='ENOENT')throw e;}}
 const stories=wires.filter(w=>w.messages[0].content.startsWith(loadFrozen().system));assert.equal(stories.length,5);
 for(const w of stories){assert.equal(w.messages.filter(m=>m.role==='system').length,1);assert.ok(w.messages[0].content.startsWith(loadFrozen().system));}
 const second=disk.turns[1],third=disk.turns[2];assert.equal(third.contextPolicy.coveredThrough,1);assert.deepEqual(third.contextPolicy.selectedTurns,[2]);assert.equal(second.memoryPolicy.recall.results[0].selected,true);
 const saved=canonical((await f.host.god.read(child.id)).history),used=(await f.ok('god/api/status')).offlineBudget.used;
 assert.equal((await f.ok('god/api/status')).used,0);assert.equal((await f.ok('god/api/status')).providerEnabled,false);
 await f.close();await f.boot();assert.equal(canonical((await f.host.god.read(child.id)).history),saved);assert.equal((await f.ok('god/api/status')).offlineBudget.used,used);
 assert.equal((await f.ok(base(s)+'/send',sent.body)).operation.status,'complete');assert.equal((await f.ok('god/api/status')).offlineBudget.used,used);
 await writeFile(join(f.directory,'http-wire-proof.json'),JSON.stringify({realProviderCalls:0,wires,source:s.id,branch:child.id,simulatedUsed:used},null,2));
});
test('God HTTP rejects foreign origin, forged data, stale settings, cross-card lookup and real mode',async t=>{
 const f=await fixture(t),s=await f.create(),setup=newSetup();
 assert.equal((await f.api('god/api/sessions',{setup,mode:'real'})).status,400);
 assert.equal((await f.api('god/api/sessions',{setup,mode:'offline',system:'forged'})).status,400);
 assert.equal((await f.api('god/api/sessions',{setup,mode:'offline'},'https://foreign.invalid')).status,403);
 assert.equal((await f.api('earth/api/sessions/'+s.id)).status,404);
 for(const id of ['rpg.history-window','rpg.memory-palace']){await f.change('install',null,id);await f.change('enable',s,id);}
 let h=await f.ok(base(s)+'/history-window');const body={operationId:randomUUID(),expectedSessionRevision:s.revision,expectedConfigRevision:h.configRevision,turns:1,includeInitialCharacter:false};
 assert.equal((await f.api(base(s)+'/history-window',{...body,system:'bad'})).status,400);await f.ok(base(s)+'/history-window',body);
 assert.equal((await f.api(base(s)+'/history-window',{...body,operationId:randomUUID()})).status,409);
 assert.equal((await f.api(base(s)+'/history-window?operationId=a&operationId=b')).status,400);
 h=await f.state(s);assert.equal((await f.api(base(s)+'/memory-palace/settings',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,selectionId:'god-offline-a',catalogRevision:'offline-v1',url:'https://invalid'})).status,400);
 assert.equal((await f.ok('god/api/status')).offlineBudget.used,0);
});
test('old God sessions remain readable and unmodified, while plugin host rejects their settings',async t=>{
 const f=await fixture(t),legacy=createGodStore(join(f.directory,'legacy-fixture'),async()=> 'old');await legacy.init();
 const old=await legacy.create({setup:newSetup(),mode:'offline'}),bytes=await readFile(legacy.path(old.id));
 await writeFile(f.host.god.path(old.id),bytes);
 const view=await f.ok(base(old));assert.equal(view.runtime.compatible,false);
 assert.equal((await f.ok(base(old)+'/memory-palace')).compatible,false);
 assert.equal((await f.api(base(old)+'/model-selection',{})).status,400);
 assert.deepEqual(await readFile(f.host.god.path(old.id)),bytes);
});

test('unknown God HTTP operation survives restart; recovery and new IDs do not redispatch',async t=>{
 let attempts=0;const f=await fixture(t,{generate:async()=>{attempts++;throw Error('synthetic offline result lost');}}),s=await f.create();
 const {body,result}=await f.send(s);assert.equal(result.operation.status,'unknown');assert.equal(attempts,1);
 await f.close();await f.boot();
 let h=await f.state(s);const recovered=await f.ok(base(s)+'/memory-palace/recover',{operationId:body.operationId,expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision});
 assert.equal(recovered.operation.status,'unknown');assert.equal(attempts,1);
 const retry=await f.api(base(s)+'/send',{...body,operationId:randomUUID()});assert.equal(retry.status,409);
 assert.equal(attempts,1);assert.equal((await f.ok('god/api/status')).offlineBudget.used,1);assert.equal((await f.ok(base(s))).turns.length,0);
});
