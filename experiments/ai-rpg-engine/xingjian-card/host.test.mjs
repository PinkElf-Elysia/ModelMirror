import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {start} from '../studio/server.mjs';
import {renderSetup} from './setup.mjs';

test('resource catalog retains world ownership and the complete reference pool',async()=>{
 const {worldDB,commonTalents}=JSON.parse(await readFile(new URL('./resources.json',import.meta.url)));
 assert.equal(worldDB.length,643);
 assert.equal(worldDB.flatMap(w=>w.identities).length,3222);
 assert.equal(worldDB.flatMap(w=>w.talents).length+commonTalents.length,11020);
 const world=worldDB.find(w=>w.name==='6.射雕英雄传');
 assert.ok(world.identities.some(i=>i.name.includes('侍卫')));
 assert.ok(world.talents.some(t=>t.name.includes('九阴真经')));
 const html=await renderSetup();assert.ok(!html.includes('__XINGJIAN_'));assert.ok(html.includes('openTalentPicker'));
});

test('five reviewed plugins configure and run on an isolated Xingjian session',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'xingjian-plugins-'));
 const {server}=await start({port:0,directory});t.after(()=>new Promise(resolve=>server.close(resolve)));
 const origin='http://127.0.0.1:'+server.address().port;
 const call=async(path,body)=>{
  const r=await fetch(origin+'/rpg-app/xingjian/api/'+path,body===undefined?{}:{method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(body)});
  const v=await r.json();assert.ok(r.ok,path+': '+JSON.stringify(v));return v;
 };
 const s=await call('sessions',{opening:'射雕英雄传 · 验收角色'}),base='sessions/'+s.id;
 for(const id of ['rpg.branch-save','rpg.model-selector','rpg.history-window','rpg.rolling-summary','rpg.memory-palace']){
  for(const action of ['install','enable']){
   const c=await call('plugins'),p=c.plugins.find(p=>p.id===id);
   const body={operationId:randomUUID(),pluginId:id,version:p.version,artifactSha256:p.artifactSha256,manifestSha256:p.manifestSha256,expectedRegistryRevision:c.revision};
   if(action==='enable')Object.assign(body,{sessionId:s.id,expectedSessionRevision:(await call(base)).revision,permissions:p.permissions});
   await call((action==='enable'?base+'/':'')+'plugins/'+id+'/'+action,body);
  }
 }
 const [model]=(await call(base+'/model-catalog')).models;
 const choice={selectionId:model.selectionId,catalogRevision:model.selectionRevision};
 let h=await call(base+'/history-window');
 await call(base+'/history-window',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedConfigRevision:h.configRevision,turns:1,includeInitialCharacter:false});
 h=await call(base+'/memory-palace');
 await call(base+'/memory-palace/settings',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedMemoryRevision:h.memoryRevision,...choice});
 h=await call(base+'/rolling-summary');
 await call(base+'/rolling-summary/settings',{operationId:randomUUID(),expectedSessionRevision:h.sessionRevision,expectedSummaryRevision:h.summaryRevision,timing:'after',...choice,text:null});
 const current=await call(base);
 await call(base+'/model-selection',{operationId:randomUUID(),expectedSessionRevision:current.revision,selectionRevision:current.modelSelection.revision,...choice});
 for(const [i,input] of [s.characterText,'去城门观察'].entries()){
  h=await call(base+'/memory-palace');
  await call(base+'/send',{operationId:randomUUID(),expectedMemoryRevision:h.memoryRevision,expectedSessionRevision:h.sessionRevision,expectedContextPolicyRevision:h.effectivePolicy.revision,input});
  for(let n=0;n<100;n++){h=await call(base+'/memory-palace');if(!h.busy&&h.processedThrough===i+1)break;await new Promise(r=>setTimeout(r,50));}
  assert.equal(h.processedThrough,i+1);
 }
 const end=await call(base);assert.equal(end.turns.length,2);
 const child=await call(base+'/branches',{operationId:randomUUID(),expectedSessionRevision:end.revision,turn:1,name:'分支'});
 assert.equal(child.session.turns.length,1);assert.notEqual(child.session.id,s.id);
});
