import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {probabilityField,probabilityGate,probabilityPolicy} from './probability.mjs';
import {startProbabilityPreview} from './probability-preview.mjs';
import {BookWorkspace} from './store.mjs';
import {source} from '../revisions/test-support.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {assemble} from './assembly.mjs';
import {apply,copy,digest} from '../revisions/contracts.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';
import {selectV3,withMeter} from '../v3/selection.mjs';
import {selectDepthBook,parseDepthBook} from './depth.mjs';
import {oncePolicy} from './once.mjs';
import {keepPolicy} from './keep.mjs';
import {cooldownPolicy} from './cooldown.mjs';

const entry=(percent,extra={})=>({keys:['风'],content:'观察记号',enabled:true,insertion_order:0,
  extensions:{[probabilityField]:percent,opaque:{v:1}},...extra});
async function directory(){
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});return mkdtemp(join(root,'draw-'));
}
test('integer 0..100, inclusive percentile threshold, stable per turn, only the namespaced field and PNG round trip',()=>{
  const book={extensions:{},entries:[entry(0),entry(100),entry(50),entry(1,{extensions:{probability:1}}),...[-1,101,1.5,'50',null].map(n=>entry(n))]};
  const gate=probabilityGate(book,'turn','sealed');
  assert.equal(gate(0).status,'probability-miss');assert.equal(gate(1).status,undefined);assert.equal(gate(3),null);
  for(let i=4;i<9;i++)assert.equal(gate(i).status,'invalid-probability');
  const roll=gate(2).probability.roll;book.entries[2].extensions[probabilityField]=roll;
  assert.equal(gate(2).status,undefined);book.entries[2].extensions[probabilityField]=roll-1;assert.equal(gate(2).status,'probability-miss');
  book.entries[2].extensions[probabilityField]=50;
  const outcomes=new Set(Array.from({length:64},(_,i)=>Boolean(probabilityGate(book,i,'sealed')(2).status)));assert.equal(outcomes.size,2);
  const card=source().cards.actor.document.character.card;upgradeCard(card,()=>0);card.data.character_book=book;
  const imported=inspectImport(exportCard(card,{format:'png'}).bytes).card;assert.deepEqual(imported.data.character_book,book);
  assert.doesNotMatch(JSON.stringify(imported),/"roll"|stable-member-draw/);
});

test('gate precedes book budget and recursion, follows depth/regex checks, and never rolls an ineligible row',()=>{
  const s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  const book={recursive_scanning:true,token_budget:6,extensions:{},entries:[
    entry(0,{content:'支流',priority:100}),entry(100,{keys:['支流'],content:'幽行'}),
    entry(100,{content:'合法'}),entry(100,{keys:['/风/u'],use_regex:true,content:'回声'}),
    entry(0,{content:'@@scan_depth 1\n风痕',keys:['前文']}),entry(100,{content:'超'.repeat(20)})]};
  card.data.character_book=book;const parsed=parseDepthBook(book);card.data.character_book=parsed.book;
  const draws=[],g=probabilityGate(book,'turn','scope'),gate=i=>{draws.push(i);return g(i);};
  const r=withMeter(clock=>selectDepthBook(card,[{content:'前文'}],'风',{budget:100},clock,parsed.rules,gate));
  assert.deepEqual(r.trace.map(e=>e.status),['probability-miss','not-triggered','triggered','triggered','not-triggered','budget-excluded']);
  assert.deepEqual(draws,[0,2,3,5]);assert.doesNotMatch(JSON.stringify(r.after),/支流|幽行/);
  const off=selectV3(card,[],'风',{},null,g);assert.equal(off.trace[3].status,'unsupported-behavior');
});

test('actual branch: repeated previews, other speaker, restart, exact request, pinned book, child scope and hidden diagnostics',async t=>{
  const dir=await directory();let h=await startProbabilityPreview(dir);t.after(()=>h.close());
  const {familyId,branchId}=h,root={familyId,branchId},view=()=>h.api('view',{...root,targetId:null});
  const [actor,other]=(await view()).members;
  const shelf=async()=>JSON.parse(await readFile(join(dir,'studio/character/independent-books/library/records/shelf.json'))).data.families[familyId];
  async function preview(op,extra={}){
    const intent={branchId,targetId:actor.id,input:'听风',messageId:null,...extra};
    const p=await h.api('preview',{familyId,intent,operationId:op});
    return {p,cmd:{expectedRevision:p.revision,operationId:op,payload:{familyId,intent,previewHash:p.previewHash}}};
  }
  const draw=r=>r.p.entries.filter(e=>e.probability).map(e=>e.probability);
  const a=await preview('one');assert.deepEqual(draw(await preview('new-id',{input:'再听风'})),draw(a));
  assert.deepEqual(draw(a).map(e=>e.percent),[100,50,0]);assert.match(a.p.messages[0].content,/风旗/);assert.doesNotMatch(a.p.messages[0].content,/库房的铜钟/);
  assert.equal(a.p.messages[0].content.includes('一声风铃'),draw(a)[1].roll<=50);
  const otherTurn=await preview('other',{targetId:other.id});await h.api('send',otherTurn.cmd);
  const b=await preview('adopt');assert.deepEqual(draw(b),draw(a));
  const sent=await h.api('send',b.cmd);assert.deepEqual(await h.api('send',b.cmd),sent);
  const f=await shelf(),reply=f.f.branches[branchId].messages.at(-1);assert.deepEqual(f.plans[sent.requestId].books,b.p.books);
  assert.deepEqual(f.f.requests[sent.requestId].context,b.p.messages);assert.equal(f.f.requests[sent.requestId].contextHash,b.p.requestHash);
  const next=await preview('next');await h.close();h=await startProbabilityPreview(dir);assert.deepEqual(await preview('next'),next);
  const exact=await preview('exact',{messageId:reply.id,input:null});assert.deepEqual(exact.p.messages,b.p.messages);
  assert.deepEqual(exact.p.books.original,b.p.books);
  const binding=(await view()).bindings.find(b=>b.title==='渡口风声簿').selected;
  const {book}=await h.api('draft',{bookId:binding.bookId});
  const expectRolls=bid=>book.entries.map((_,i)=>probabilityGate(book,[familyId,bid,actor.id,reply.revisionId],binding)(i).probability);
  assert.deepEqual(draw(next),expectRolls(branchId));
  const fork=async readers=>h.write('fork',{...root,point:reply.id,title:'风声分支',player:null,session:[{readers,ref:binding}]});
  const child=await fork('all');assert.deepEqual(draw(await preview('child',{branchId:child.branchId})),expectRolls(child.branchId));
  const hidden=await fork([other.id]);assert.doesNotMatch(JSON.stringify((await preview('hidden',{branchId:hidden.branchId})).p.books),/"roll"|风声簿/);
});

test('lost receipt lookup reuses the prepared draw; misses never claim keep, cooldown or once',async t=>{
  const s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book={extensions:{},entries:[entry(0,{content:'@@keep_activate_after_match\n记号',extensions:{[probabilityField]:0,'modelmirror.cooldown':2}}),entry(0,{content:'@@dont_activate_after_match\n单次'}),entry(50)]};
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  delete s.sourceHash;s.sourceHash=digest(s);
  const dir=await directory(),opts={directory:dir,sources:{read:async()=>copy(s),verify:async()=>copy(s)},fault:async phase=>{if(phase==='afterReceipt')throw Error('offline interruption');}};
  let desk=await BookWorkspace.open(opts);t.after(()=>desk.close());
  const {familyId,branchId}=await desk.command('open',{operationId:'open',expectedRevision:0,payload:{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[],keepPolicy,oncePolicy,cooldownPolicy,probabilityPolicy}});
  const intent={branchId,targetId:'actor',messageId:null,input:'风'},first=await desk.preview(familyId,intent,'draw');
  const sent=await desk.command('send',{operationId:'draw',expectedRevision:first.revision,payload:{familyId,intent,previewHash:first.previewHash}});
  await desk.close();desk=await BookWorkspace.open(opts);
  const pending=await desk.preview(familyId,intent,'again');assert.deepEqual(pending.books.entries.map(e=>e.probability),first.books.entries.map(e=>e.probability));
  await desk.recover(familyId,sent.requestId);await desk.recover(familyId,sent.requestId);assert.equal(desk.fixture.dispatchCount,0);
  const shelf=JSON.parse(await readFile(join(dir,'library/records/shelf.json'))).data.families[familyId],evidence=shelf.plans[sent.requestId];
  assert.deepEqual(evidence.books,first.books);assert.deepEqual([evidence.cooldownKeys,evidence.onceKeys,evidence.keepKeys],[[],[],[]]);
});

test('capacity eviction drops the draw for a lost keyword and keeps the same draw for the current input',()=>{
  const s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  s.kind='group';
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  card.data.character_book={extensions:{},entries:[
    entry(100,{keys:['旧尺'],content:'@@scan_depth 6\n木架有四格。'}),entry(50,{content:'@@scan_depth 1\n风声记号'})]};
  s.messages[1].content='旧尺'+'潮'.repeat(12000);delete s.sourceHash;s.sourceHash=digest(s);
  const f=apply(null,{action:'create',expectedRevision:null,operationId:'create',payload:{source:s}});
  const run=capacity=>assemble(f,'draw',{branchId:f.rootId,kind:'send',targetId:'actor',input:'风'},
    {...fixtureModel(),contextTokens:capacity},undefined,[],{},false,null,true,false,false,null,null,true);
  const wide=run(64000),narrow=run(32000);
  assert.equal(wide.books.entries[0].probability.percent,100);
  assert.equal(narrow.books.entries[0].status,'not-triggered');assert.equal(narrow.books.entries[0].probability,undefined);
  assert.doesNotMatch(JSON.stringify(narrow.request.context),/旧尺|木架有四格/);
  assert.deepEqual(narrow.books.entries[1].probability,wide.books.entries[1].probability);
});
