import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {join} from 'node:path';
import {TaskLedger} from '../tasks/ledger.mjs';import {openPreview} from './bootstrap.mjs';import {exampleCatalog} from './catalog.mjs';
test('registration is durable at creation and old mode cannot reinterpret it',async t=>{
 const d=await mkdtemp(join(tmpdir(),'r8-binding-'));t.after(()=>rm(d,{recursive:true,force:true}));
 let l=await TaskLedger.open(d,undefined,{version:'task-owners/8',purposes:['new-consumer']});assert.equal((await l.read()).registration.purposes[0],'new-consumer');await l.close();
 await assert.rejects(TaskLedger.open(d),/REGISTRATION_CHANGED/);
 l=await TaskLedger.open(d,undefined,{version:'task-owners/8',purposes:['new-consumer']});await l.close();
 await assert.rejects(TaskLedger.open(d,undefined,{version:'task-owners/8',purposes:['different']}),/REGISTRATION_CHANGED/);
});
test('assembly binding rejects changed capabilities, original configuration remains readable',async t=>{
 const d=await mkdtemp(join(tmpdir(),'r8-binding-'));t.after(()=>rm(d,{recursive:true,force:true}));
 let rt=await openPreview(d,{offlinePreview:true});const v=await rt.create({operationId:'start',adapterId:'lighthouse'});await rt.close();
 const c=await exampleCatalog();c.worlds[0].adapter.binding.adapterVersion='different';
 await assert.rejects(openPreview(d,{offlinePreview:true,catalog:c}),/ASSEMBLY_CHANGED/);
 rt=await openPreview(d,{offlinePreview:true});assert.equal((await rt.view(v.id)).world.resources[0].quantity,5);await rt.close();
});
