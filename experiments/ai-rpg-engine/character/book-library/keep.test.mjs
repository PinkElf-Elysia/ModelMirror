import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {keepHeader,keepPolicy,prepareKeepBook} from './keep.mjs';
import {depthPolicy} from './depth.mjs';
import {resolveFallback} from './fallback.mjs';
import {startKeepPreview} from './keep-preview.mjs';
import {BookWorkspace} from './store.mjs';
import {source} from '../revisions/test-support.mjs';
import {apply,copy,digest} from '../revisions/contracts.mjs';
import {assemble} from './assembly.mjs';
import {defaults} from '../long-context/planner.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';

async function directory(){
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();
  await mkdir(root,{recursive:true});return mkdtemp(join(root,'keep-'));
}
const raw='@@keep_activate_after_match\r\n@@scan_depth 1\r\n绳结在木桩上。';
const entry={keys:['蓝绳'],content:raw,enabled:true,insertion_order:0,extensions:{opaque:true}};
test('exact header, compatible combinations, conflicting once, branch-gated fallback and preserved V3 PNG',()=>{
  assert.equal(keepHeader(raw).remaining,'@@scan_depth 1\r\n绳结在木桩上。');
  for(const header of ['@@keep_activate_after_match 2','@@keep_activate_after_match\n@@keep_activate_after_match','@@keep_activate_after_match\n@@dont_activate_after_match','@@unknown\n@@keep_activate_after_match']){
    assert.equal(keepHeader(header+'\n正文').invalid,true);
  }
  const chain='@@keep_activate_after_match\n@@@scan_depth 1\n资料';
  assert.equal(resolveFallback(chain,{depth:true}).fallback[0].selected,1);
  assert.equal(resolveFallback(chain,{depth:true,keep:true}).fallback[0].selected,0);
  const book={entries:[entry],extensions:{}},card=source().cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book=book;assert.deepEqual(inspectImport(exportCard(card,{format:'png'}).bytes).card.data.character_book,book);
  const first=prepareKeepBook(book,'v1','actor',new Map()).traces.get(0),prior=new Map([[first.keepKey,'anchor']]);
  assert.equal(prepareKeepBook(book,'v1','actor',prior).book.entries[0].constant,true);
  assert.equal(prepareKeepBook(book,'v2','actor',prior).book.entries[0].constant,undefined);
  assert.equal(prepareKeepBook(book,'v1','other',prior).book.entries[0].constant,undefined);
});

test('active entry still obeys timing and budget; omission never resets the proven anchor',()=>{
  const s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book={entries:[{...entry,content:'@@activate_only_every 3\n'+raw}],extensions:{}};
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  function run(input,prior,bookTokens){
    delete s.sourceHash;s.sourceHash=digest(s);
    const f=apply(null,{action:'create',expectedRevision:null,operationId:'create',payload:{source:s}});
    return assemble(f,'test',{kind:'send',input,targetId:'actor',branchId:f.rootId},fixtureModel(),{...defaults(),bookTokens},[],{},false,null,true,true,false,prior).books.entries[0];
  }
  const first=run('蓝绳',new Map(),100);assert.equal(first.status,'triggered');
  const prior=new Map([[first.keepKey,'confirmed']]);assert.equal(run('继续',prior,0).status,'request-budget');
  assert.equal(run('继续',prior,100).status,'triggered');
  s.messages.pop();const delayed=run('继续',prior,100);assert.equal(delayed.status,'timing-interval');assert.equal(delayed.keepAnchor,'confirmed');
});

test('offline HTTP: first adopted hit persists without keys across restart; members, cuts, exact requests and book versions remain isolated',async t=>{
  const dir=await directory();let h=await startKeepPreview(dir);t.after(()=>h.close());
  const {familyId,branchId}=h,root={familyId,branchId};
  const getView=()=>h.api('view',{...root,targetId:null});
  const [actor,other]=(await getView()).members;
  async function preview(input,op,extra={}){
    const intent={branchId,targetId:actor.id,input,messageId:null,...extra};
    const p=await h.api('preview',{familyId,intent,operationId:op});
    return {p,cmd:{expectedRevision:p.revision,operationId:op,payload:{familyId,intent,previewHash:p.previewHash}}};
  }
  const state=({p})=>p.entries.find(e=>e.keep);
  const before=await preview('继续','before');assert.equal(state(before).status,'not-triggered');
  const hit=await preview('蓝绳','hit');assert.equal(state(hit).status,'triggered');assert.equal(state(hit).keepAnchor,null);
  assert.deepEqual(await preview('蓝绳','hit'),hit);assert.deepEqual(await preview('继续','before'),before);
  const sent=await h.api('send',hit.cmd);assert.deepEqual(await h.api('send',hit.cmd),sent);
  const [input,reply]=(await getView()).messages.slice(-2);
  await h.close();h=await startKeepPreview(dir);
  const continued=await preview('继续','next');assert.equal(state(continued).keepAnchor,reply.id);
  assert.match(continued.p.messages[0].content,/第三根木桩/);assert.doesNotMatch(continued.p.messages[0].content,/本次标记|@@/);
  assert.equal(state(await preview('继续','other',{targetId:other.id})).keepAnchor,null);
  assert.equal(state(await preview('继续','other',{targetId:other.id})).status,'not-triggered');
  const exact=await preview(null,'exact',{messageId:reply.id});assert.deepEqual(exact.p.messages,hit.p.messages);
  await h.api('send',exact.cmd);
  assert.deepEqual((await preview('继续','after-exact')).p.books,continued.p.books);
  const fork=async(point,grants=null)=>h.write('fork',{...root,point,title:'系舟分支',session:grants,player:null});
  for(const [point,expected] of [[input.id,null],[reply.id,reply.id],[null,reply.id]]){
    const child=await fork(point);const r=await preview('继续','cut',{branchId:child.branchId});
    assert.equal(state(r).keepAnchor,expected);assert.equal(state(r).status,expected?'triggered':'not-triggered');
  }
  const binding=(await getView()).bindings.find(b=>b.title==='系舟手记').selected;
  const next=await h.write('seal',{bookId:binding.bookId});
  const changed=await fork(null,[{readers:'all',ref:next}]);
  assert.equal(state(await preview('继续','version',{branchId:changed.branchId})).status,'not-triggered');
  const privateChild=await fork(null,[{readers:[other.id],ref:binding}]);
  const denied=await preview('继续','private',{branchId:privateChild.branchId});
  assert.doesNotMatch(JSON.stringify(denied.p.books),/keepKey|绳结|系舟手记/);
  assert.doesNotMatch(denied.p.messages[0].content,/第三根木桩/);
  const fresh=await preview('继续','final');const second=await h.api('send',fresh.cmd);
  const shelf=JSON.parse(await readFile(join(dir,'studio/character/independent-books/library/records/shelf.json'))).data.families[familyId];
  assert.deepEqual(shelf.f.requests[second.requestId].context,fresh.p.messages);
  assert.equal(shelf.f.requests[second.requestId].contextHash,fresh.p.requestHash);
  assert.deepEqual(shelf.plans[second.requestId].books,fresh.p.books);
  assert.equal(shelf.plans[sent.requestId].keepKeys.length,1);assert.deepEqual(shelf.plans[second.requestId].keepKeys,[]);
});

test('unknown and failed receipts never activate a prepared match; budget-excluded hits have no eligible anchor',async t=>{
  const dir=await directory(),s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book={entries:[entry,{...entry,content:'@@keep_activate_after_match\n'+'长'.repeat(1300)}],extensions:{},token_budget:4096};
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  delete s.sourceHash;s.sourceHash=digest(s);
  const opts={directory:dir,sources:{read:async()=>copy(s),verify:async()=>copy(s)},fault:async phase=>{if(phase==='beforeReceipt')throw Error('offline-interruption');}};
  let desk=await BookWorkspace.open(opts);t.after(()=>desk.close());
  const {familyId,branchId}=await desk.command('open',{operationId:'open',expectedRevision:0,payload:{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[],keepPolicy,depthPolicy}});
  const intent={branchId,targetId:'actor',input:'蓝绳',messageId:null},hit=await desk.preview(familyId,intent,'lost');
  assert.deepEqual(hit.entries.map(e=>e.status),['triggered','request-budget']);
  const {requestId}=await desk.command('send',{operationId:'lost',expectedRevision:hit.revision,payload:{familyId,intent,previewHash:hit.previewHash}});
  await desk.close();desk=await BookWorkspace.open(opts);
  const read=()=>desk.preview(familyId,{...intent,input:'继续'},'later');
  assert.equal((await read()).entries[0].keepAnchor,null);await desk.recover(familyId,requestId);
  const shelf=JSON.parse(await readFile(join(dir,'library/records/shelf.json'))).data.families[familyId];
  const r=shelf.f.requests[requestId];assert.equal(r.status,'unknown');assert.equal(shelf.plans[requestId].keepKeys.length,1);
  const receipt={requestId,requestHash:r.hash,modelId:r.model.id,targetId:r.targetId,status:'failed',text:'部分',raw:{kind:'fixture'},dispatched:true,costMicroUsd:null};
  const records=desk.fixture.records,saved=await records.get(requestId);
  await records.put(requestId,saved.hash,{hash:r.hash,receipt:{...receipt,hash:digest(receipt)}});
  await desk.recover(familyId,requestId);await desk.recover(familyId,requestId);
  assert.equal((await read()).entries[0].status,'not-triggered');assert.equal(desk.fixture.dispatchCount,0);
});
