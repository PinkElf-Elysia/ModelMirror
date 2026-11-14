import test from 'node:test';import assert from 'node:assert/strict';
import {cp,appendFile,readFile} from 'node:fs/promises';import {join} from 'node:path';import {fileURLToPath} from 'node:url';
import {baselineFixture} from './archive-support.mjs';
test('new source drift rejects live operation and reopening without rewriting a record',async t=>{
 const {root}=await baselineFixture('0cb019df01642d71a5eef55eeee31d1086ab037d'),code=new URL('../character/conversation/',root);
 await cp(fileURLToPath(new URL('./',import.meta.url)),fileURLToPath(code),{recursive:true});
 const {fixture}=await import(new URL('test-support.mjs',code));const f=await fixture(t);await f.join('member');
 const record=join(f.directory,'conversations','records','records',f.created.conversationId+'.json'),before=await readFile(record);
 await appendFile(new URL('contracts.mjs',code),'\n// isolated source drift probe\n');
 await assert.rejects(()=>f.host.readOwner(f.created.conversationId),{code:'CONVERSATION_RUNTIME_CHANGED'});
 await f.reopen();await f.host.recover();await assert.rejects(()=>f.host.readOwner(f.created.conversationId),{code:'CONVERSATION_RUNTIME_MISMATCH'});
 assert.deepEqual(await readFile(record),before);
});

test('runtime manifest covers the transitive local implementation imports',async()=>{
 const {runtimePaths}=await import('./runtime.mjs');const {posix}=await import('node:path');const all=new Set(runtimePaths),root=new URL('../../',import.meta.url);
 for(const path of runtimePaths.filter(p=>p.endsWith('.mjs'))){
  const source=await readFile(new URL(path,root),'utf-8');
  for(const match of source.matchAll(/(?:from\s*|import\s*)['"](\.[^'"]+)['"]/g)){
   const dependency=posix.normalize(posix.join(posix.dirname(path),match[1]));assert.ok(all.has(dependency),path+' missing fingerprint dependency '+dependency);
  }
 }
});
