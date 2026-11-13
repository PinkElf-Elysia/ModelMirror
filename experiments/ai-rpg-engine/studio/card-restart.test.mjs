import test from 'node:test';
import assert from 'node:assert/strict';
import {fork} from 'node:child_process';
import {once} from 'node:events';
import {mkdir,mkdtemp,readdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
import {formalComparisonControl} from '../authoring/comparison-test-support.mjs';
import {createGenerationCards,GENERATION_CARDS} from './generation-cards.mjs';

test('C2-B actual six-card process crash archives locks and preserves unknown batches without replay',{timeout:180000},async t=>{
 const remote=await formalComparisonControl(t),root=fileURLToPath(new URL('../.rpg04-work/c2b-crash/',import.meta.url));await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'run-'));
 const config={directory:join(directory,'sessions'),budgetDirectory:join(directory,'budget'),origin:remote.origin,serviceToken:remote.token,comparison:true};
 await remote.fault({dropTasks:12,blockLookup:true});
 const child=fork(fileURLToPath(new URL('./card-restart-fixture.mjs',import.meta.url)),[],{windowsHide:true,stdio:['ignore','ignore','pipe','ipc']});let stderr='';child.stderr.on('data',b=>stderr+=b);t.after(()=>{if(child.exitCode===null)child.kill();});
 const ready=new Promise((yes,no)=>{child.once('message',yes);child.once('error',no);child.once('exit',()=>no(Error('fixture exited '+stderr)));});child.send(config);const data=await ready;assert.ok(!data.error,data.error);
 assert.equal(data.records.length,6);assert.equal((await remote.evidence()).calls.length,12);
 for(const r of data.records){assert.equal(r.session.turns.length,0);assert.ok(r.batch.tasks.some(t=>t.state==='unknown'));assert.equal(r.budget.used,2);}
 const pid=child.pid,exit=once(child,'exit');child.kill('SIGKILL');await exit;
 const host=await createGenerationCards({...config,defaultModel:data.defaultModel,limits:Object.fromEntries(Object.keys(GENERATION_CARDS).map(k=>[k,12]))});t.after(()=>host.close());
 assert.deepEqual(await host.plugins.catalog(),data.plugins);
 const archived=(await readdir(join(config.directory,'plugins'))).filter(n=>n.startsWith('owner-dead-'));assert.equal(archived.length,1);
 assert.equal(JSON.parse(await readFile(join(config.directory,'plugins',archived[0]))).pid,pid);
 for(const r of data.records){const h=host.hosts.get(r.card);assert.deepEqual(await h.store.read(r.id),r.session);assert.deepEqual(await h.io.budget.status(),r.budget);}
 assert.equal((await remote.evidence()).calls.length,12,'startup cannot dispatch or reconcile unknown operations');
 await remote.fault({blockLookup:false});
 for(const r of data.records){const h=host.hosts.get(r.card),b=await h.comparison.reconcile(r.id,r.batchId);assert.equal(b.status,'awaiting-choice',JSON.stringify(b.events));
  const adopted=await h.comparison.adopt(r.id,r.batchId,{operationId:randomUUID(),expectedContextRevision:b.context.contextRevision,candidateId:'a'});assert.equal(adopted.status,'settled');assert.equal((await h.store.read(r.id)).turns.length,1);assert.equal((await h.io.budget.status()).used,2);}
 assert.equal((await remote.evidence()).calls.length,12);
});
