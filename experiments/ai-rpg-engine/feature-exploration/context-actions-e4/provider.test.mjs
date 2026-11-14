import test from 'node:test';import assert from 'node:assert/strict';
import {parse} from './provider.mjs';
import {openSources} from './source.mjs';import {DraftStore} from './store.mjs';
import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {hash} from '../../agent-core/contracts.mjs';
const value={action:'use_medicine',changes:[{field:'item',value:'medicine',quote:'养息丹'},{field:'quantity',value:2,quote:'两份'}],questions:[],unsupported:false};
const json=JSON.stringify(value),wrap=s=>'```json\n'+s+'\n```';
test('bare JSON and one complete labeled fence use the same strict parser',()=>{
 for(const raw of [json,wrap(json),' \n'+wrap(json)+'\n ',wrap(JSON.stringify(value,null,2)).replaceAll('\n','\r\n')])assert.deepEqual(parse(raw),value);
});
test('prose, multiple fences, truncated or other language fences are not extracted',()=>{
 for(const raw of ['说明\n'+wrap(json),wrap(json)+'\n说明',wrap(json)+'\n'+wrap(json),'```json\n'+json,
  '```\n'+json+'\n```','```javascript\n'+json+'\n```','```json '+json+'```','````json\n'+json+'\n````',wrap(json+'\n'+json),wrap('{"action":'),null]){
  assert.throws(()=>parse(raw),{code:'E4_REPLY_JSON'});
 }
});
test('fences cannot bypass top-level fields, action types or change shape validation',()=>{
 for(const raw of [wrap(JSON.stringify({...value,extra:true})),wrap(JSON.stringify({changes:[],questions:[],unsupported:false}))])assert.throws(()=>parse(raw),{code:'E4_REPLY_FIELDS'});
 assert.throws(()=>parse(wrap(JSON.stringify({...value,action:123}))),{code:'E4_REPLY_ACTION'});
 for(const changes of [null,{},'anything'])assert.throws(()=>parse(wrap(JSON.stringify({...value,changes}))),{code:'REPLY_SHAPE'});
 assert.throws(()=>parse(wrap(JSON.stringify({...value,unsupported:'false'}))),{code:'REPLY_SHAPE'});
});
test('stored fenced receipt stays exact; rule preflight is readonly and invalid delta is rejected',async t=>{
 const root=fileURLToPath(new URL('../../.rpg03-work/e4-tests/',import.meta.url));await mkdir(root,{recursive:true});const dir=await mkdtemp(root+'fence-');
 const sources=await openSources(dir+'/sources'),source=sources.get('cultivation'),before=hash((await source.read()).record),raw=wrap(JSON.stringify(value,null,2));
 let reply=raw;const provider={async generate(){return {status:'completed',raw:reply,provider:'offline-fixture'};}};
 let store=await DraftStore.open(dir+'/drafts',sources,{provider});t.after(()=>store.close());
 let v=await store.command({adapterId:'cultivation',kind:'reply',operationId:'fenced',expectedHash:null,text:'我想服用两份养息丹，先拟方案，不要执行。'});
 assert.equal(v.draft.operations.fenced.status,'completed');assert.equal(v.draft.operations.fenced.receipt.raw,raw);
 assert.equal(v.draft.current.preflight.expected[0].after,3);assert.equal(v.draft.current.confirmation,null);
 await store.close();store=await DraftStore.open(dir+'/drafts',sources,{provider});v=await store.view('cultivation');
 assert.equal(v.draft.operations.fenced.receipt.raw,raw);
 reply=wrap(JSON.stringify({...value,changes:[{field:'quantity',value:1,quote:'一些',unexpected:true}]}));
 v=await store.command({adapterId:'cultivation',kind:'reply',operationId:'invalid',expectedHash:v.recordHash,text:'改成一些'});
 assert.equal(v.draft.operations.invalid.status,'failed');assert.equal(v.draft.operations.invalid.error,'E4_FIELD');
 assert.equal(v.draft.operations.invalid.receipt.raw,reply);assert.equal(v.draft.current.intent.quantity.value,2);
 assert.equal(hash((await source.read()).record),before);
});
