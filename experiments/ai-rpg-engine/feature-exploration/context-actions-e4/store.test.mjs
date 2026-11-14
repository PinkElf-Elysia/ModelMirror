import test from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';
import {openSources,sourceFromReader} from './source.mjs';import {DraftStore} from './store.mjs';import {Simulator} from './provider.mjs';import {hash} from '../../agent-core/contracts.mjs';
const root=fileURLToPath(new URL('../../.rpg03-work/e4-tests/',import.meta.url));await mkdir(root,{recursive:true});
const c=(field,value)=>({field,value,quote:'界面明确修改'});
async function setup(t,provider){const dir=await mkdtemp(root+'store-'),sources=await openSources(dir+'/sources');let store=await DraftStore.open(dir+'/drafts',sources,provider?{provider}:{});
 t.after(()=>store.close());let seq=0;return {dir,sources,get store(){return store;},async reopen(){await store.close();store=await DraftStore.open(dir+'/drafts',sources,provider?{provider}:{});},async cmd(kind,extra={}){const v=await store.view('cultivation');return store.command({adapterId:'cultivation',kind,operationId:'op-'+(++seq),expectedHash:v.recordHash,...extra});}};
}
test('complete plan, accept, revision, gift, cancellation, reopen and export never write source',async t=>{
 const f=await setup(t),before=hash((await f.sources.get('cultivation').read()).record);
 let v=await f.cmd('select',{action:'use_medicine',changes:[c('item','medicine')]});assert.equal(v.draft.current.status,'clarification');
 v=await f.cmd('reply',{text:'两份'});assert.equal(v.draft.current.preflight.expected[0].after,3);
 await f.cmd('accept');assert.equal((await f.store.export('cultivation')).capability,'rule-supported');
 await f.reopen();v=await f.cmd('reply',{text:'改成一份'});assert.equal(v.draft.current.confirmation,null);assert.equal(v.draft.current.preflight.expected[0].after,4);
 v=await f.cmd('reply',{text:'改为赠送'});assert.equal(v.draft.current.intent.item??null,null);assert.equal(v.draft.current.preflight,null);
 await f.cmd('edit',{changes:[c('item','medicine'),c('quantity',1),c('target','zhou')]});
 v=await f.cmd('accept');assert.equal((await f.store.export('cultivation')).capability,'intent-only');
 v=await f.cmd('reply',{text:'不是周禾'});assert.equal(v.draft.current.intent.target,null);assert.equal(v.draft.current.confirmation,null);
 await f.cmd('cancel');await assert.rejects(()=>f.store.export('cultivation'),{code:'E4_NOT_ACCEPTED'});
 const switched=await f.cmd('select',{action:'use_medicine'});assert.ok(switched.draft.history.at(-1).diff.some(x=>x.field==='item'&&x.before==='medicine'&&x.after===null));assert.equal(hash((await f.sources.get('cultivation').read()).record),before);
 assert.equal(v.realCalls,0);
});
test('duplicate operations and stale clients do not reinterpret or lose edits',async t=>{
 const f=await setup(t);const cmd={adapterId:'cultivation',kind:'reply',operationId:'same',expectedHash:null,text:'我想服用两份养息丹'};
 const v=await f.store.command(cmd);await f.store.command(cmd);assert.equal(f.store.provider.calls,1);
 await assert.rejects(()=>f.store.command({...cmd,text:'改成一份'}),{code:'E4_OPERATION_REUSED'});
 await assert.rejects(()=>f.store.command({...cmd,operationId:'new',text:'改成一份'}),{code:'E4_DRAFT_CONFLICT'});
 assert.equal((await f.store.view('cultivation')).recordHash,v.recordHash);
});
test('unknown results remain pending over restart; viewing cannot dispatch',async t=>{
 const provider={calls:0,async generate(){this.calls++;return {status:'unknown',raw:''};}},f=await setup(t,provider);
 const v=await f.cmd('reply',{text:'我想服用两份养息丹'});assert.ok(v.draft.pending);await f.reopen();
 await assert.rejects(()=>f.cmd('select',{action:'use_medicine'}),{code:'E4_REQUEST_UNKNOWN'});
 await assert.rejects(()=>f.store.export('cultivation'),{code:'E4_EXPORT_PENDING'});await f.store.view('cultivation');assert.equal(provider.calls,1);
});
test('known malformed response preserves draft, raw response and no acceptance',async t=>{
 const p=new Simulator(),f=await setup(t,p);await f.cmd('reply',{text:'我想服用两份养息丹'});await f.cmd('accept');
 p.generate=async()=>({status:'completed',raw:'{"action":'});
 const v=await f.cmd('reply',{text:'改成一份'});assert.equal(v.draft.pending,null);assert.equal(v.draft.current.intent.quantity.value,2);assert.equal(v.draft.current.confirmation,null);
 assert.equal(Object.values(v.draft.operations).at(-1).receipt.raw,'{"action":');
});
test('world changes invalidate estimates but allow intent edits; generation cannot rebase secretly',async t=>{
 const f=await setup(t),s=f.sources.get('cultivation');let r=(await s.read()).record;
 f.sources.set('cultivation',sourceFromReader(s.adapter,async()=>structuredClone(r)));
 await f.cmd('reply',{text:'我想服用两份养息丹'});await f.cmd('accept');
 r.state.items[0].quantity=1;r.stateRevision++;r.revision++;
 assert.equal((await f.store.view('cultivation')).stale,true);await assert.rejects(()=>f.store.export('cultivation'),{code:'E4_SOURCE_STALE'});
 let v=await f.cmd('edit',{changes:[c('quantity',1)]});assert.equal(v.draft.current.status,'stale');assert.equal(v.draft.current.preflight,null);
 v=await f.cmd('refresh');assert.equal(v.draft.current.preflight.expected[0].after,0);
 const generate=f.store.provider.generate.bind(f.store.provider);f.store.provider.generate=async req=>{r.stateRevision++;r.revision++;return generate(req);};
 v=await f.cmd('reply',{text:'改成一份'});assert.equal(Object.values(v.draft.operations).at(-1).error,'E4_SOURCE_CHANGED');
});
