import {test} from 'node:test';import assert from 'node:assert/strict';import {mkdir,mkdtemp} from 'node:fs/promises';import {fileURLToPath} from 'node:url';import {join} from 'node:path';
import {exampleState} from '../../local-state/example.mjs';import {DraftStore} from './store.mjs';import {MockProvider} from './provider.mjs';import {createBuilder} from './context.mjs';
const builder=await createBuilder();
async function setup(t){
 const root=fileURLToPath(new URL('../../.rpg03-work/',import.meta.url));await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'e3-test-'));
 let w={session:'e3-test',branch:'e3-test',version:1,revision:1,enabled:true,pending:null,state:exampleState()};
 const source={read:async()=>structuredClone(w)},provider=new MockProvider(),store=await DraftStore.open(directory,{source,builder,provider});t.after(()=>store.close());
 let count=0;const send=async(kind,extra={})=>store.command({kind,operationId:'test-'+(++count),expectedHash:(await store.view()).recordHash,...extra});
 return {store,send,provider,directory,source,set:value=>{w=value;},get:()=>w};
}
async function complete(s){for(const text of ['给那人一些丹药','周禾','养息丹，两份；如果他不收就算了'])await s.send('reply',{text});}
test('continuous draft persists; accept, edit, export and refresh never execute',async t=>{
 const s=await setup(t),before=structuredClone(s.get());await complete(s);let v=await s.send('accept');assert.equal(v.draft.current.status,'accepted');assert.equal((await s.store.export()).executionStatus,'not_executed');
 v=await s.send('edit',{changes:[{field:'quantity',value:1}]});assert.equal(v.draft.current.status,'review');assert.equal(v.draft.current.intent.target.value,'zhou');assert.equal(s.provider.calls,3);await assert.rejects(s.store.export());
 await s.store.close();const reopened=await DraftStore.open(s.directory,{source:s.source,builder,provider:s.provider});try{assert.equal((await reopened.view()).draft.current.intent.quantity.value,1);}finally{await reopened.close();}
 assert.deepEqual(s.get(),before);
});
test('operation replay is idempotent and same ID with changed payload rejects',async t=>{
 const s=await setup(t),cmd={kind:'reply',text:'周禾',operationId:'once',expectedHash:null};const v=await s.store.command(cmd);assert.equal((await s.store.command(cmd)).recordHash,v.recordHash);assert.equal(s.provider.calls,1);
 await assert.rejects(s.store.command({...cmd,text:'算了'}),{code:'OPERATION_REUSED'});
 await assert.rejects(s.store.command({...cmd,operationId:'new-id'}),{code:'DRAFT_CONFLICT'});
});
test('failed or truncated response preserves previous values and invalidates acceptance',async t=>{
 const s=await setup(t);await complete(s);await s.send('accept');s.provider.generate=async()=>({status:'completed',raw:'{'});
 const v=await s.send('reply',{text:'改成一份'});assert.equal(v.draft.current.intent.quantity.value,2);assert.equal(v.draft.current.confirmation,null);assert.equal(v.draft.pending,null);assert.equal(Object.values(v.draft.operations).at(-1).status,'failed');
});
test('unknown provider outcome stays pending after reopen; cannot dispatch or export',async t=>{
 const s=await setup(t);s.provider.generate=async()=>({status:'unknown'});let v=await s.send('reply',{text:'周禾'});assert(v.draft.pending);
 await assert.rejects(s.send('reply',{text:'周禾'}),{code:'REQUEST_UNKNOWN'});await assert.rejects(s.store.export());await s.store.close();
 const reopened=await DraftStore.open(s.directory,{source:s.source,builder,provider:s.provider});try{v=await reopened.reconcile();assert(v.draft.pending);}finally{await reopened.close();}
});
test('source changes during generation do not publish; refresh is explicit',async t=>{
 const s=await setup(t);await complete(s);const generate=s.provider.generate.bind(s.provider);s.provider.generate=async r=>{s.set({...s.get(),version:2});return generate(r);};
 let v=await s.send('reply',{text:'改成一份，先问他需不需要'});assert.equal(v.draft.current.intent.quantity.value,2);assert(v.stale);await assert.rejects(s.send('accept'),{code:'SOURCE_STALE'});
 v=await s.send('refresh');assert(!v.stale);assert.equal(v.draft.current.confirmation,null);
});
test('cancel requires explicit operation; new plan gets new identity',async t=>{
 const s=await setup(t);await complete(s);let v=await s.send('reply',{text:'算了'});const id=v.draft.current.id;assert.equal(v.draft.current.status,'clarification');
 v=await s.send('cancel');assert.equal(v.draft.current.status,'cancelled');await assert.rejects(s.send('reply',{text:'周禾'}),{code:'DRAFT_CANCELLED'});v=await s.send('new');assert.notEqual(v.draft.current.id,id);assert.equal(v.draft.current.intent.target,null);
});

test('unsupported goal cannot silently become a gift after a partial reply',async t=>{const s=await setup(t);await s.send('reply',{text:'用未登记的神通永久保护药圃'});const calls=s.provider.calls;await assert.rejects(s.send('reply',{text:'周禾'}),{code:'UNSUPPORTED_PLAN'});assert.equal(s.provider.calls,calls);await s.send('new');assert.equal((await s.store.view()).draft.current.unsupported,false);});
