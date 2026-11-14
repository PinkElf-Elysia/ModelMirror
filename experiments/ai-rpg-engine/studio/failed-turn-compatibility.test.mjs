import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {summaryRuntime,summaryRuntimeStatus,bindSummaryRuntime} from './summary-runtime.mjs';
import {memoryRuntime,memoryRuntimeStatus,bindMemoryRuntime} from './memory-runtime.mjs';
import {preserveOutputEditRuntime} from './output-edit-compatibility.mjs';
import {sha,canonical} from '../plugins/catalog.mjs';
const baseline=JSON.parse(await readFile(new URL('./failed-turn-baseline.json',import.meta.url)));
const {runtimes:reviewedRuntimes}=JSON.parse(await readFile(new URL('./shared-runtime-fixtures.json',import.meta.url)));
for(const [name,runtime,bind,status,index] of [['summary',summaryRuntime,bindSummaryRuntime,summaryRuntimeStatus,2],['memory',memoryRuntime,bindMemoryRuntime,memoryRuntimeStatus,3]])test(name+' main save keeps exact binding and rejects tampered content/setup',()=>{
 const source={characterText:'成年旅人',world:'表世界',params:{},mode:'offline'};
 source.runtime=bind(source);const saved=structuredClone(source);
 // Use the complete frozen descriptor. Mixing current artifacts with a few old
 // digests stops representing this release whenever unrelated host code changes.
 const reviewed=reviewedRuntimes.find(r=>r.hash===baseline.baseHashes[index+1]);
 assert.ok(reviewed,'the original frozen runtime must be present');
 saved.runtime.descriptor=structuredClone(reviewed.descriptor);
 saved.runtime.hash=sha(canonical(saved.runtime.descriptor));
 assert.notEqual(saved.runtime.hash,runtime.hash);assert.equal(saved.runtime.hash,baseline.baseHashes[index+1]);
 const store={status};assert.equal(store.status(saved).compatible,false);preserveOutputEditRuntime(store);
 assert.equal(store.status(saved).compatible,true);assert.equal(store.status(saved).hash,saved.runtime.hash);
 const bytes=JSON.stringify(saved);store.status(saved);assert.equal(JSON.stringify(saved),bytes);
 const modified=structuredClone(saved);modified.runtime.descriptor.content.author.system='0'.repeat(64);modified.runtime.hash=sha(canonical(modified.runtime.descriptor));assert.equal(store.status(modified).compatible,false);
 assert.equal(store.status({...saved,characterText:'篡改设定'}).compatible,false);
 const badHash=structuredClone(saved);badHash.runtime.hash='0'.repeat(64);assert.equal(store.status(badHash).compatible,false);
});
