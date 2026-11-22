import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {BookWorkspace} from './store.mjs';
import {onceBody,oncePolicy} from './once.mjs';
import {source} from '../revisions/test-support.mjs';
import {copy,digest} from '../revisions/contracts.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';

const entry=(name,body,extra={})=>({id:7,name,keys:['登记簿'],enabled:true,insertion_order:0,extensions:{},content:'@@dont_activate_after_match\r\n'+body,...extra});
const normal=entry('钥匙','钥匙放在蓝盒中。');
async function setup(t,{fault,book,cardBook}={}){
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();
  await mkdir(root,{recursive:true});const directory=await mkdtemp(join(root,'once-')),s=source();
  const card=s.cards.actor.document.character.card;upgradeCard(card);
  if(cardBook)card.data.character_book=cardBook;
  s.kind='group';s.cards.other=copy(s.cards.actor);
  for(const state of [s.initial,s.current,...s.messages.map(m=>m.boundary)]){
    state.members[1].binding={reference:{workId:'work',versionId:'sealed'}};
    state.members.push({...copy(state.members[1]),id:'other'});
  }
  s.budget.calls=80;s.budget.microUsd=2000000;delete s.sourceHash;s.sourceHash=digest(s);
  const args={directory,sources:{read:async()=>copy(s),verify:async()=>copy(s)},fault};
  let store=await BookWorkspace.open(args);
  t.after(()=>store.close());
  const write=async(action,payload)=>store.command(action,{operationId:crypto.randomUUID(),expectedRevision:(await store.list()).revision,payload});
  const save=await write('save',{bookId:null,title:'渡口笔记',book:book??{entries:[normal],extensions:{}}}),ref=await write('seal',save);
  const grants=[{ref,readers:['actor']}];
  const open=(policy=true)=>write('open',{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,session:grants,player:grants,...(policy?{oncePolicy}:{})});
  const branch=await open(),familyId=branch.familyId;
  async function preview({where=branch,op=crypto.randomUUID(),actor='actor',input='登记簿',messageId=null}={}){
    const intent={branchId:where.branchId,targetId:actor,input:messageId?null:input,messageId};
    const p=await store.preview(where.familyId,intent,op);
    return {...p,cmd:{operationId:op,expectedRevision:p.revision,payload:{familyId:where.familyId,intent,previewHash:p.previewHash}}};
  }
  const send=p=>store.command('send',p.cmd);
  const fork=(point,base=branch,overrides={})=>write('fork',{...base,point,title:'分支',session:null,player:null,...overrides});
  const bytes=()=>readFile(join(directory,'library','records','shelf.json'));
  const shelf=async()=>JSON.parse(await bytes()).data;
  return {branch,familyId,ref,card,write,open,preview,send,fork,bytes,shelf,get store(){return store;},
    async restart(){await store.close();store=await BookWorkspace.open(args);}};
}

test('directive is exact; additional behaviors, fallback and malformed or empty entries stay preserved',()=>{
  assert.equal(onceBody(normal.content),'钥匙放在蓝盒中。');
  for(const content of ['@@dont_activate_after_match 2\n正文','@@dont_activate_after_match\n@@unknown\n正文','@@@dont_activate_after_match\n正文','@@dont_activate_after_match\n '])assert.equal(onceBody(content),null);
});

test('preview is read-only; receipt anchors one use, exact regeneration preserves context and grants branch at the reply',async t=>{
  const x=await setup(t),before=await x.bytes(),p=await x.preview({op:'first'});
  for(let i=0;i<3;i++){const again=await x.preview({op:'first'});assert.equal(again.previewHash,p.previewHash);assert.equal(again.entries.length,1);}
  assert.deepEqual(await x.bytes(),before);assert.equal(p.entries[0].status,'triggered');assert.doesNotMatch(JSON.stringify(p.messages),/@@dont_/);
  const hidden=await x.preview({actor:'other'});assert.doesNotMatch(JSON.stringify(hidden),/钥匙|渡口笔记|onceKey/);
  const {requestId}=await x.send(p);assert.deepEqual(await x.send(p),{...x.branch,requestId});
  await x.restart();const used=(await x.preview()).entries[0];assert.equal(used.status,'once-used');
  const view=await x.store.view(x.familyId,x.branch.branchId,'actor'),reply=view.messages.at(-1),input=view.messages.at(-2);
  assert.equal(used.messageId,reply.id);const row=(await x.shelf()).families[x.familyId];
  assert.equal(row.tracks[x.branch.branchId].once.uses.length,1);assert.deepEqual(row.plans[requestId].onceKeys,[p.books.entries[0].onceKey]);
  assert.deepEqual(row.f.requests[requestId].context,p.messages);
  const regen=await x.preview({messageId:reply.id});assert.equal(regen.requestHash,p.requestHash);
  await x.send(regen);assert.deepEqual((await x.shelf()).families[x.familyId].tracks[x.branch.branchId].once,row.tracks[x.branch.branchId].once);
  await x.send(await x.preview({input:'继续'}));
  for(const [point,expected] of [[input.id,'triggered'],[reply.id,'once-used'],[null,'once-used']]){
    const where=await x.fork(point);assert.equal((await x.preview({where})).entries[0].status,expected);
    if(expected==='triggered'){await x.send(await x.preview({where}));assert.equal((await x.preview({where})).entries[0].status,'once-used');}
  }
  const detached=await x.fork(null,x.branch,{session:[],player:[]});assert.equal((await x.preview({where:detached})).entries.length,0);
  const rebound=await x.fork(null,detached,{session:[{ref:x.ref,readers:['actor']}]});assert.equal((await x.preview({where:rebound})).entries[0].status,'once-used');
  const next=await x.write('seal',{bookId:x.ref.bookId}),newBook=await x.fork(null,x.branch,{session:[{ref:next,readers:['actor']}]});
  assert.equal((await x.preview({where:newBook})).entries[0].status,'triggered');assert.equal((await x.preview()).entries[0].messageId,reply.id);
  assert.equal((await x.preview({where:await x.open()})).entries[0].status,'triggered');
  const legacy=await x.open(false);assert.equal((await x.preview({where:legacy})).entries[0].status,'unsupported-behavior');
});

test('same-template members, duplicate author IDs, card entries, frozen source and exported definition remain separate',async t=>{
  const cardBook={entries:[normal,entry('旁注','登记须用蓝墨。')],extensions:{opaque:true}};
  const x=await setup(t,{cardBook});const p=await x.preview();
  assert.equal(new Set(p.books.entries.map(e=>e.onceKey)).size,3);
  await x.send(p);assert.ok((await x.preview()).entries.every(e=>e.status==='once-used'));
  const other=await x.preview({actor:'other'});assert.equal(other.entries.length,2);assert.ok(other.entries.every(e=>e.status==='triggered'));
  const wire=exportCard(x.card,{format:'png'}),round=inspectImport(wire.bytes).card;
  assert.deepEqual(round.data.character_book,cardBook);assert.doesNotMatch(JSON.stringify(round),/onceKeys|once-used|once-on-history|requestId/);
});

test('final request and book budgets exclude matches without spending; stale preview rejected',async t=>{
  const large=entry('长记录','潮'.repeat(1400)),short=entry('附页','记号',{priority:1});
  const x=await setup(t,{book:{entries:[large,short],extensions:{},token_budget:4096}});
  const p=await x.preview(),stale=await x.preview();assert.deepEqual(p.entries.map(e=>e.status),['request-budget','triggered']);
  await x.send(p);await assert.rejects(x.send(stale),/REVISION_CONFLICT/);
  assert.deepEqual((await x.preview()).entries.map(e=>e.status),['request-budget','once-used']);
  const z=await setup(t,{book:{entries:[normal],extensions:{},token_budget:0}});await z.send(await z.preview());
  assert.equal((await z.preview()).entries[0].status,'book-budget');
});

test('pending dispatch and unknown outcome retain claims across reload; verified failed receipt releases only item state',async t=>{
  let entered,finish;const started=new Promise(r=>entered=r),release=new Promise(r=>finish=r);
  const x=await setup(t,{fault:async phase=>{if(phase==='beforeReceipt'){entered();await release;throw Error('simulated response loss');}}});
  const p=await x.preview();const live=x.send(p);
  await started;
  try{assert.equal((await x.preview()).entries[0].status,'once-held');await assert.rejects(x.fork(null),/REQUEST_PENDING/);}
  finally{finish();}
  const {requestId}=await live;await x.restart();
  const recover=()=>x.store.recover(x.familyId,requestId);
  await recover();await recover();assert.equal((await x.preview()).entries[0].status,'once-held');
  await assert.rejects(x.send(await x.preview()),/REQUEST_PENDING/);
  const row=(await x.shelf()).families[x.familyId],r=row.f.requests[requestId];
  assert.equal(r.status,'unknown');assert.equal(row.tracks[x.branch.branchId].once.uses.length,0);
  const receipt={requestId,requestHash:r.hash,modelId:r.model.id,targetId:'actor',status:'failed',text:'部分',raw:{kind:'fixture'},dispatched:true,costMicroUsd:null};
  const records=x.store.fixture.records,saved=await records.get(requestId);
  const invalid={...receipt,targetId:'other'};await records.put(requestId,saved.hash,{hash:r.hash,receipt:{...invalid,hash:digest(invalid)}});
  await assert.rejects(recover(),/RECEIPT_MISMATCH/);assert.equal((await x.preview()).entries[0].status,'once-held');
  await records.put(requestId,(await records.get(requestId)).hash,{hash:r.hash,receipt:{...receipt,hash:digest(receipt)}});
  await recover();await recover();assert.equal((await x.preview()).entries[0].status,'triggered');
  const after=(await x.shelf()).families[x.familyId];assert.equal(after.f.usedMicroUsd,row.f.usedMicroUsd);assert.equal(after.f.usedCalls,1);
  assert.ok(!after.f.branches[x.branch.branchId].messages.some(m=>m.content==='部分'));assert.equal(x.store.fixture.dispatchCount,0);
});

for(const [phase,commit] of [['prepared',false],['prepared',true],['completed',false],['completed',true]]){
  test('atomic '+phase+' write, interrupted '+(commit?'after':'before')+' commit',async t=>{
    const x=await setup(t),p=await x.preview(),before=await x.bytes(),put=x.store.records.put.bind(x.store.records);
    x.store.records.put=async(key,hash,data)=>{
      if(!Object.values(data.families[x.familyId].f.requests).some(r=>r.status===phase))return put(key,hash,data);
      if(commit)await put(key,hash,data);throw Error('storage interruption');
    };
    await assert.rejects(x.send(p),/storage interruption/);
    if(phase==='prepared'&&!commit){assert.deepEqual(await x.bytes(),before);return;}
    await x.restart();
    const [{id:rid}]=Object.values((await x.shelf()).families[x.familyId].f.requests);
    await x.store.recover(x.familyId,rid);await x.store.recover(x.familyId,rid);
    assert.equal((await x.preview()).entries[0].status,phase==='prepared'?'once-held':'once-used');
    const row=(await x.shelf()).families[x.familyId];assert.equal(row.tracks[x.branch.branchId].once.uses.length,phase==='completed'?1:0);
    assert.equal(row.f.branches[x.branch.branchId].messages.filter(m=>m.generation?.requestId===rid).length,phase==='completed'?1:0);
    assert.equal(x.store.fixture.dispatchCount,0);
  });
}

test('request-budget exclusion in a successful send leaves the key available on the next turn',async t=>{
  const filler={...normal,keys:['长页'],priority:10,content:'册'.repeat(1023)};
  const x=await setup(t,{book:{token_budget:4096,scan_depth:0,entries:[normal,filler],extensions:{}}});
  const full=await x.preview({input:'登记簿 长页'});assert.equal(full.entries[0].status,'request-budget');
  await x.send(full);assert.equal((await x.preview()).entries[0].status,'triggered');
});
test('receipt stored before disconnect: lookup commits original reply and uses once without redispatch',async t=>{
  const x=await setup(t,{fault:async phase=>{if(phase==='afterReceipt')throw Error('simulated disconnect');}});
  const p=await x.preview(),r=await x.send(p);assert.equal((await x.preview()).entries[0].status,'once-held');
  await x.restart();await x.store.recover(x.familyId,r.requestId);await x.store.recover(x.familyId,r.requestId);
  const row=(await x.shelf()).families[x.familyId];
  assert.equal((await x.preview()).entries[0].status,'once-used');assert.equal(row.tracks[x.branch.branchId].once.uses.length,1);
  assert.equal(row.f.branches[x.branch.branchId].messages.filter(m=>m.generation?.requestId===r.requestId).length,1);
  assert.equal(x.store.fixture.dispatchCount,0);
});
test('author trial is opt-in, strips only the supported directive and never writes state',async t=>{
  const x=await setup(t),bytes=await x.bytes(),book={entries:[normal],extensions:{}};
  assert.equal((await x.store.trial('原文',book,'登记簿')).after.length,0);
  assert.deepEqual((await x.store.trial('原文',book,'登记簿',oncePolicy)).after,['钥匙放在蓝盒中。']);
  await assert.rejects(x.store.trial('原文',book,'登记簿','future'),/BOOK_POLICY/);
  assert.deepEqual(await x.bytes(),bytes);
});

test('card-only path, opt-in activation boundary and sibling history are independent',async t=>{
  const x=await setup(t,{cardBook:{entries:[normal],extensions:{}}}),old=await x.open(false);
  const root=await x.fork(null,old,{session:[],player:[],oncePolicy});
  assert.equal((await x.preview({where:root})).entries.length,1);
  const {requestId}=await x.send(await x.preview({where:root}));
  const messages=(await x.store.view(root.familyId,root.branchId,'actor')).messages;
  const earlier=await x.fork(messages.at(-3).id,root);
  assert.equal((await x.preview({where:earlier})).entries[0].status,'unsupported-behavior');
  const middle=await x.fork(messages.at(-2).id,root);assert.equal((await x.preview({where:middle})).entries[0].status,'triggered');
  const l=(await x.shelf()).families[root.familyId];assert.equal(l.tracks[root.branchId].once.uses[0].requestId,requestId);
  assert.ok((await x.preview({where:old})).entries.every(e=>e.status==='unsupported-behavior'));
});
