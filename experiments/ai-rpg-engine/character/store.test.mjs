import {openPublishedRuntime} from '../authoring/runtime-binding.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {AuthoringStore} from '../authoring/store.mjs';import {blankDocument,validateDocument} from '../authoring/contracts.mjs';import {characterDocument} from './document.mjs';
const root=fileURLToPath(new URL('../.rpg04-work/character-tests/',import.meta.url));await mkdir(root,{recursive:true});
test('same authoring store: immutable character version, copy and world compatibility',async t=>{
 const path=await mkdtemp(root+'store-'),store=await AuthoringStore.open(path);t.after(()=>store.close());
 const doc=characterDocument(blankDocument());doc.character.card.data.name='测试夹具';doc.metadata.name='测试夹具';doc.character.card.data.first_mes='你好';doc.character.card.data.extensions={unknown:{keep:'原文'}};
 const made=await store.create({operationId:'create',expectedRevision:null,document:doc});
 const pub=await store.publish(made.workId,{operationId:'publish',expectedRevision:0});
 const ref=pub.result.versionId;await assert.rejects(()=>openPublishedRuntime(store,{workId:made.workId,versionId:ref}),{code:'CHARACTER_RUNTIME_REQUIRED'});doc.character.card.data.description='新的草稿';
 await store.save(made.workId,{operationId:'edit',expectedRevision:1,document:doc});
 assert.equal((await store.exportConfiguration(made.workId,ref)).document.character.card.data.description,'');
 assert.equal((await store.read(made.workId)).draft.character.card.data.description,'新的草稿');
 const w=blankDocument();w.metadata.name='世界';w.metadata.audience='female';w.sources.system='原始世界';
 const other=await store.create({operationId:'world',expectedRevision:null,document:w});assert.deepEqual((await store.read(other.workId)).draft,w);
 const copied=await store.copy(made.workId,{operationId:'copy-character',expectedRevision:2,name:'另一个测试夹具'});const cloned=await store.read(copied.workId);assert.equal(cloned.draft.character.card.data.name,'另一个测试夹具');assert.equal(cloned.draft.character.card.data.extensions.unknown.keep,'原文');assert.notEqual(copied.workId,made.workId);
 await store.close();const reopened=await AuthoringStore.open(path);t.after(()=>reopened.close());assert.equal((await reopened.list()).length,3);assert.deepEqual((await reopened.exportConfiguration(made.workId,ref)).document.character.card.data.extensions,{unknown:{keep:'原文'}});
});

test('complete character document shares host size and JSON safety validation',()=>{
 const doc=characterDocument(blankDocument());doc.character.card.data.description='a'.repeat(16*1024*1024);
 assert.throws(()=>validateDocument(doc),{code:'AUTHORING_DOCUMENT_SIZE'});
 doc.character.card.data.description='';doc.character.card.data.extensions=JSON.parse('{"constructor":"unsafe"}');assert.throws(()=>validateDocument(doc),{code:'AUTHORING_JSON_KEY'});
});
