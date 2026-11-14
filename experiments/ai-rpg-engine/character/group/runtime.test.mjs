import test from 'node:test';import assert from 'node:assert/strict';import {cp,appendFile,readFile} from 'node:fs/promises';import {join,posix} from 'node:path';import {fileURLToPath} from 'node:url';
import {baselineFixture} from '../conversation/archive-support.mjs';import {runtimePaths} from './runtime.mjs';
test('group runtime covers transitive imports and leaves R1 descriptor unchanged',async()=>{
 const all=new Set(runtimePaths),root=new URL('../../',import.meta.url);for(const path of runtimePaths.filter(p=>p.endsWith('.mjs'))){const source=await readFile(new URL(path,root),'utf8');for(const match of source.matchAll(/(?:from\s*|import\s*)['"](\.[^'"]+)['"]/g)){const dependency=posix.normalize(posix.join(posix.dirname(path),match[1]));assert.ok(all.has(dependency),path+' missing '+dependency);}}
 const r1=await readFile(new URL('../conversation/runtime.mjs',import.meta.url),'utf8');assert.doesNotMatch(r1,/character\/group/);
});
test('group source drift rejects live access and restart, original ledger bytes unchanged',async t=>{
 const {root}=await baselineFixture('0cb019df01642d71a5eef55eeee31d1086ab037d'),code=new URL('../character/group/',root),foundation=new URL('../character/conversation/',root);
 await cp(fileURLToPath(new URL('../conversation/',import.meta.url)),fileURLToPath(foundation),{recursive:true});await cp(fileURLToPath(new URL('./',import.meta.url)),fileURLToPath(code),{recursive:true});
 const {fixture}=await import(new URL('test-support.mjs',code)),f=await fixture(t),record=join(f.directory,'groups','ledger','records','groups.json'),before=await readFile(record);
 await appendFile(new URL('context.mjs',code),'\n// isolated source drift probe\n');await assert.rejects(f.store.read(f.id),{code:'GROUP_RUNTIME_CHANGED'});await assert.rejects(f.reopen(),{code:'GROUP_RUNTIME_MISMATCH'});assert.deepEqual(await readFile(record),before);
});
