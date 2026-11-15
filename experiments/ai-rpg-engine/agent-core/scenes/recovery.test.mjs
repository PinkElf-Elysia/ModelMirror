import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,mkdir} from 'node:fs/promises';import {join} from 'node:path';import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {openScene} from './bootstrap.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/r9-tests/',import.meta.url));
async function setup(t){await mkdir(root,{recursive:true});const path=await mkdtemp(join(root,'recovery-'));let app=await openScene(path,{offlinePreview:true,delayMs:0});
 t.after(()=>app?.close());return {path,get app(){return app;},async stop(){await app.close();app=null;},async reopen(){if(app)await app.close();app=await openScene(path,{offlinePreview:true,delayMs:0});}};}
const talk=(app,key,op,fault=null,input='你好')=>app.submit(key,{operationId:op,ownerId:'participant-keeper',input,fault});
test('unknown lookup survives process exit, preserves request count and does not adopt automatically',async t=>{
 const f=await setup(t),v=await f.app.create({operationId:'start',adapterId:'lighthouse'});
 let a=await talk(f.app,v.id,'unknown','unknown');assert.equal(a.tasks[0].adoption,'unknown');await f.stop();
 const code=`import {openScene} from ${JSON.stringify(new URL('./bootstrap.mjs',import.meta.url).href)};const a=await openScene(process.argv[1],{offlinePreview:true,delayMs:0});const v=await a.recover(process.argv[2],process.argv[3]);console.log(JSON.stringify({calls:v.budget.calls,status:v.tasks[0].adoption,entries:v.scene.entries.length}));await a.close();`;
 const result=await new Promise((ok,no)=>{const p=spawn(process.execPath,['--input-type=module','-e',code,f.path,v.id,a.tasks[0].id],{windowsHide:true});let out='',err='';p.stdout.on('data',c=>out+=c);p.stderr.on('data',c=>err+=c);p.on('error',no);p.on('close',n=>n?no(Error(err)):ok(JSON.parse(out)));});
 assert.deepEqual(result,{calls:1,status:'awaiting-adoption',entries:0});
 await f.reopen();a=await f.app.adopt(v.id,a.tasks[0].id);assert.equal(a.scene.entries.length,1);assert.equal(a.budget.calls,1);
});
test('failed, truncated, write and invalid tools are not accepted; continuation does not retry',async t=>{
 const f=await setup(t);
 for(const [fault,input,expected,calls] of [['failed','你好','failed',1],['truncated','你好','rejected',1],['write','你好','rejected',1],['foreign-tool','查询库存','rejected',1],['continuation-failed','查询库存','failed',2]]){
  let v=await f.app.create({operationId:fault,adapterId:'lighthouse'});const before=v.budget.calls;
  v=await talk(f.app,v.id,'call',fault,input);assert.equal(v.tasks[0].adoption,expected,fault);assert.equal(v.scene.entries.length,0);assert.equal(v.budget.calls-before,calls);
  const count=v.budget.calls;v=await f.app.recover(v.id,v.tasks[0].id);assert.equal(v.budget.calls,count);
 }
});
test('adoption interruption completes projection once; stale and discarded responses do not become history',async t=>{
 const f=await setup(t);let v=await f.app.create({operationId:'start',adapterId:'lighthouse'});
 v=await talk(f.app,v.id,'one');const task=v.tasks[0].id,real=f.app.ledger.adopted.bind(f.app.ledger);let fail=true;
 f.app.ledger.adopted=async(...args)=>{if(fail){fail=false;throw Error('injected projection failure');}return real(...args);};
 await assert.rejects(f.app.adopt(v.id,task),/injected/);assert.equal((await f.app.view(v.id)).scene.entries.length,1);
 v=await f.app.adopt(v.id,task);assert.equal(v.scene.entries.length,1);assert.equal(v.tasks[0].adoption,'completed');
 v=await talk(f.app,v.id,'stale');const run=await f.app.run(v.id);
 await f.app.port(run).store.update(run.worldId,w=>{w.testOnlyExplicitRevision='changed';});
 v=await f.app.adopt(v.id,v.tasks.at(-1).id);assert.equal(v.tasks.at(-1).adoption,'stale');assert.equal(v.scene.entries.length,1);
 v=await talk(f.app,v.id,'discard');v=await f.app.dismiss(v.id,v.tasks.at(-1).id);assert.equal(v.scene.entries.length,1);
 const calls=v.budget.calls;v=await talk(f.app,v.id,'discard');assert.equal(v.budget.calls,calls);
 await assert.rejects(talk(f.app,v.id,'discard',null,'different'),/COLLISION/);
 await f.reopen();v=await f.app.view(v.id);assert.equal(v.scene.entries.length,1);assert.equal(v.budget.calls,calls);
});
test('sticky unknown blocks further tasks; visitor query never obtains resources',async t=>{
 const f=await setup(t);let v=await f.app.create({operationId:'unknown',adapterId:'lighthouse'});
 v=await talk(f.app,v.id,'one','unknown-sticky');v=await f.app.recover(v.id,v.tasks[0].id);
 assert.equal(v.tasks[0].adoption,'unknown');assert.equal(v.budget.calls,1);
 await assert.rejects(talk(f.app,v.id,'two'),/RECOVERY/);
 let b=await f.app.create({operationId:'other',adapterId:'lighthouse'});
 b=await f.app.submit(b.id,{operationId:'query',ownerId:'participant-visitor',input:'查询库存'});
 assert.match(b.tasks[0].body,/不知道/);assert.equal(b.tasks[0].stages[1].queryResults[0].result.status,'unavailable');
 const full=(await f.app.ledger.read()).tasks[b.tasks[0].id];
 assert.ok(!JSON.stringify(full.stages[0].request).includes('沈砚'));
 assert.deepEqual(full.observation.resources,[]);assert.deepEqual(full.observation.events,[]);
});
