import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {cooldownField,cooldownPolicy,prepareCooldownBook} from './cooldown.mjs';
import {startCooldownPreview} from './cooldown-preview.mjs';
import {BookWorkspace} from './store.mjs';
import {source} from '../revisions/test-support.mjs';
import {copy,digest} from '../revisions/contracts.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';

async function directory(){
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});return mkdtemp(join(root,'cooldown-'));
}
const entry=(rounds,extra={})=>({keys:['点灯'],content:'青灯挂在东窗。',enabled:true,insertion_order:0,
  extensions:{[cooldownField]:rounds,unknown:{v:7}},...extra});

test('only the namespaced integer field runs; exact 0, 1, 1000, rejected values, actual V3 PNG round trip',()=>{
  const foreign=entry(1,{extensions:{cooldown:9}}),book={entries:[foreign,...[0,1,1000,-1,1.5,'2',null,1001].map(n=>entry(n))],extensions:{}};
  const state={replies:5,last:new Map()},trial=()=>prepareCooldownBook(book,'v1','member',state);
  const r=trial();assert.deepEqual([...r.traces.keys()],[2,3,4,5,6,7,8]);
  for(const n of [4,5,6,7,8])assert.equal(r.traces.get(n).status,'invalid-cooldown');
  const key=r.traces.get(2).cooldownKey;state.last.set(key,{at:5,messageId:'reply'});
  assert.equal(trial().book.entries[2].enabled,false);state.replies++;assert.equal(trial().book.entries[2].enabled,true);
  const card=source().cards.actor.document.character.card;upgradeCard(card,()=>0);card.data.character_book=book;
  const imported=inspectImport(exportCard(card,{format:'png'}).bytes).card;
  assert.deepEqual(imported.data.character_book,book);assert.doesNotMatch(JSON.stringify(imported),/cooldownKey|member-adopted-replies|messageId/);
});

test('cooldown 2 blocks exactly two adopted replies, renews after inclusion, isolates branches, members and versions',async t=>{
  const dir=await directory();let h=await startCooldownPreview(dir);t.after(()=>h.close());
  const {familyId,branchId}=h,root={familyId,branchId},view=()=>h.api('view',{...root,targetId:null});
  const [actor,other]=(await view()).members;
  async function preview(input,operationId,extra={}){
    const intent={branchId,targetId:actor.id,input,messageId:null,...extra},p=await h.api('preview',{familyId,intent,operationId});
    return {p,cmd:{expectedRevision:p.revision,operationId,payload:{familyId,intent,previewHash:p.previewHash}}};
  }
  const send=r=>h.api('send',r.cmd),row=r=>r.p.entries.find(e=>e.cooldown);
  const hit=await preview('点灯','first');assert.equal(row(hit).status,'triggered');assert.equal(row(hit).cooldown.remaining,0);
  assert.deepEqual(await preview('点灯','first'),hit);const first=await send(hit);assert.deepEqual(await send(hit),first);
  const [input,reply]=(await view()).messages.slice(-2);
  const blocked=await preview('点灯','next');assert.equal(row(blocked).status,'cooldown-wait');assert.equal(row(blocked).cooldown.remaining,2);
  assert.equal(row(blocked).keepAnchor,reply.id);assert.doesNotMatch(blocked.p.messages[0].content,/青灯/);
  await h.close();h=await startCooldownPreview(dir);assert.deepEqual(await preview('点灯','next'),blocked);
  const otherHit=await preview('点灯','other',{targetId:other.id});assert.equal(row(otherHit).cooldown.remaining,0);await send(otherHit);
  const exact=await preview(null,'exact',{messageId:reply.id});assert.deepEqual(exact.p.messages,hit.p.messages);await send(exact);
  assert.equal(row(await preview('继续','still')).cooldown.remaining,2);
  const fork=(point,session=null)=>h.write('fork',{...root,point,title:'灯号分支',player:null,session});
  for(const [point,left] of [[input.id,0],[reply.id,2]]){
    const child=await fork(point);assert.equal(row(await preview('点灯','child',{branchId:child.branchId})).cooldown.remaining,left);
  }
  const binding=(await view()).bindings.find(b=>b.title==='渡口灯号簿').selected;
  const v2=await h.write('seal',{bookId:binding.bookId}),newBook=await fork(null,[{readers:'all',ref:v2}]);
  assert.equal(row(await preview('点灯','v2',{branchId:newBook.branchId})).cooldown.remaining,0);
  const denied=await fork(null,[{readers:[other.id],ref:binding}]);
  assert.doesNotMatch(JSON.stringify((await preview('点灯','private',{branchId:denied.branchId})).p.books),/cooldownKey|窗边灯号/);
  for(const n of [2,1]){
    const r=await preview('继续','wait-'+n);assert.equal(row(r).cooldown.remaining,n);await send(r);
  }
  const ready=await preview('继续','ready');assert.equal(row(ready).status,'triggered');assert.equal(row(ready).cooldown.remaining,0);
  assert.match(ready.p.messages[0].content,/青灯/);const renewed=await send(ready);
  const next=row(await preview('继续','renewed'));assert.equal(next.cooldown.remaining,2);assert.notEqual(next.cooldown.messageId,reply.id);
  const f=JSON.parse(await readFile(join(dir,'studio/character/independent-books/library/records/shelf.json'))).data.families[familyId];
  assert.deepEqual(f.f.requests[renewed.requestId].context,ready.p.messages);assert.equal(f.f.requests[renewed.requestId].contextHash,ready.p.requestHash);
  assert.deepEqual(f.plans[renewed.requestId].books,ready.p.books);
});

test('unknown and failed do not start or advance; lookup of an original complete receipt advances only once; clipped entries are not reserved',async t=>{
  const dir=await directory(),s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book={entries:[entry(2),entry(3,{content:'潮'.repeat(1400)})],extensions:{},token_budget:4096};
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  s.budget.calls=12;delete s.sourceHash;s.sourceHash=digest(s);
  let failure='afterReceipt';
  const opts={directory:dir,sources:{read:async()=>copy(s),verify:async()=>copy(s)},fault:async phase=>{if(phase===failure)throw Error('offline interruption');}};
  let desk=await BookWorkspace.open(opts);t.after(()=>desk.close());
  const {familyId,branchId}=await desk.command('open',{operationId:'open',expectedRevision:0,payload:{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[],cooldownPolicy}});
  const intent={branchId,targetId:'actor',messageId:null,input:'点灯'},peek=op=>desk.preview(familyId,intent,op);
  const send=(op,p)=>desk.command('send',{operationId:op,expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}});
  const first=await peek('first');assert.deepEqual(first.entries.map(e=>e.status),['triggered','request-budget']);
  const lost=await send('first',first);await desk.close();desk=await BookWorkspace.open(opts);
  assert.equal((await peek('pending')).entries[0].cooldown.remaining,0);
  await desk.recover(familyId,lost.requestId);await desk.recover(familyId,lost.requestId);
  const blocked=await peek('blocked');assert.deepEqual(blocked.entries.map(e=>e.cooldown.remaining),[2,0]);
  failure='beforeReceipt';const unknown=await send('blocked',blocked);await desk.recover(familyId,unknown.requestId);
  assert.equal((await peek('pending-again')).entries[0].cooldown.remaining,2);
  const shelf=JSON.parse(await readFile(join(dir,'library/records/shelf.json'))).data.families[familyId],r=shelf.f.requests[unknown.requestId];
  const receipt={requestId:r.id,requestHash:r.hash,modelId:r.model.id,targetId:r.targetId,status:'failed',text:'部分结果',raw:{kind:'fixture'},dispatched:true,costMicroUsd:null};
  const records=desk.fixture.records,record=await records.get(r.id);await records.put(r.id,record.hash,{hash:r.hash,receipt:{...receipt,hash:digest(receipt)}});
  await desk.recover(familyId,r.id);assert.equal((await peek('after-failed')).entries[0].cooldown.remaining,2);
  failure=null;await send('resume',await peek('resume'));assert.equal((await peek('after-success')).entries[0].cooldown.remaining,1);
});
