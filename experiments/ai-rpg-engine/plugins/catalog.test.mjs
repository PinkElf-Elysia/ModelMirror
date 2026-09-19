import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadReviewedCatalog,verifyPackage} from './catalog.mjs';
const manifest = await readFile(new URL('./branch-save.manifest.json',import.meta.url));
const artifact = await readFile(new URL('./branch-save.mjs',import.meta.url));
test('reviewed catalog binds exact identity, permissions and artifact bytes',async()=>{
 const e=await loadReviewedCatalog();assert.equal(e.manifest.id,'rpg.branch-save');assert.equal(e.manifest.network,'none');assert.equal(e.manifest.modelAccess,false);
 assert.deepEqual(e.invoke({capability:'ui.message-action',session:{completedTurns:0}}),{kind:'message-action',action:'branch',label:'分支',available:false});
});
test('artifact tampering and undeclared executable entry rejected',()=>{
 assert.throws(()=>verifyPackage(manifest,Buffer.from('changed')),/PLUGIN_ARTIFACT_MISMATCH/);
 const m=JSON.parse(manifest);m.entrypoint='arbitrary.mjs';assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify(m)),artifact),/PLUGIN_MANIFEST_INVALID/);
});
test('broader permissions, incompatible card or host are rejected',()=>{
 for(const patch of [{permissions:['network.request']},{hostVersion:'2.0.0'},{compatibleCards:['rpg05']},{modelAccess:true}]){
  assert.throws(()=>verifyPackage(Buffer.from(JSON.stringify({...JSON.parse(manifest),...patch})),artifact),/PLUGIN_MANIFEST_INVALID/);
 }
});
test('branch adapter emits bounded proposal only, never changes session',async()=>{
 const e=await loadReviewedCatalog(),session={id:'fictional',completedTurns:3},before=structuredClone(session);
 assert.deepEqual(e.invoke({capability:'session.branch.prepare',session,input:{turn:2,name:'  另一条路线  '}}),{kind:'branch-request',sessionId:'fictional',turn:2,name:'另一条路线'});
 assert.deepEqual(session,before);
 for(const input of [{turn:0,name:'x'},{turn:4,name:'x'},{turn:1,name:' '},{turn:1,name:'x'.repeat(81)}])assert.throws(()=>e.invoke({capability:'session.branch.prepare',session,input}),/INVALID_BRANCH_INPUT/);
});
