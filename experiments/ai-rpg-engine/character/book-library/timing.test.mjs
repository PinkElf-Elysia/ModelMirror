import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {source} from '../revisions/test-support.mjs';
import {apply,digest,copy} from '../revisions/contracts.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';
import {defaults} from '../long-context/planner.mjs';
import {assemble} from './assembly.mjs';
import {timingHeader,prepareTimedBook,timingPolicy} from './timing.mjs';
import {startTimingPreview} from './timing-preview.mjs';

const row=(rule,extra={})=>({keys:['钟'],content:rule+'\n门侧挂着钟牌。',enabled:true,insertion_order:0,extensions:{},...extra});
function sample(rows){
  const s=source();s.kind='group';
  const card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book={entries:rows,extensions:{}};
  for(const state of [s.initial,s.current,...s.messages.map(m=>m.boundary)])state.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  return s;
}
function project(s,input='钟',options={}){
  delete s.sourceHash;s.sourceHash=digest(s);
  const f=apply(null,{action:'create',expectedRevision:null,operationId:'create',payload:{source:s}});
  return assemble(f,'preview',{branchId:f.rootId,targetId:'actor',kind:'send',input},
    {...fixtureModel(),contextTokens:options.capacity??32000},defaults(),[],{},false,options.once??null,true,options.enabled??true);
}
test('delay and interval boundaries include zero, combine as AND, malformed rules fail closed and export retains bytes of content',()=>{
  const rules=['@@activate_only_after 2','@@activate_only_every 2','@@activate_only_after 3\r\n@@activate_only_every 2'];
  const book={entries:rules.map(r=>row(r)),extensions:{}};
  const status=count=>prepareTimedBook(book,count).book.entries.map(e=>e.enabled);
  assert.deepEqual([0,1,2,3,4].map(status),[[false,true,false],[false,false,false],[true,true,false],[true,false,false],[true,true,true]]);
  for(const h of ['@@activate_only_every 0','@@activate_only_after -1','@@activate_only_after 1.5','@@activate_only_after 9007199254740992','@@activate_only_after 2\n@@activate_only_after 3','@@activate_only_every 2\n@@@fallback 1']){
    assert.equal(timingHeader(h+'\n正文').invalid,true);
    assert.equal(prepareTimedBook({entries:[row(h)]},4).traces.get(0).status,'invalid-timing');
  }
  const card=sample(book.entries).cards.actor.document.character.card;
  assert.deepEqual(inspectImport(exportCard(card,{format:'png'}).bytes).card.data.character_book,book);
});
test('count visible character speakers including opening and other roles, independent of capacity, filtered before counting',()=>{
  const s=sample([row('@@activate_only_after 3')]);
  const other={...copy(s.current.members[1]),id:'other'};for(const state of [s.current,s.initial,...s.messages.map(m=>m.boundary)])state.members.push(copy(other));
  s.cards.other=copy(s.cards.actor);
  s.messages[2].speakerId='other';s.messages[2].content='波'.repeat(12000);
  for(const capacity of [32000,64000]){
    const r=project(s,'钟',{capacity});assert.equal(r.books.entries[0].timing.count,3);assert.equal(r.books.entries[0].status,'triggered');
    assert.equal(r.plan.trace.find(e=>e.id==='m2').status,capacity===32000?'omitted':'included');
  }
  s.current.members[1].intervals=[{from:2,until:3},{from:5,until:null}];
  s.messages[2].visibility={kind:'restricted',memberIds:['other']};
  const r=project(s);assert.deepEqual(r.books.entries[0].timing,{count:1,after:3});
  assert.equal(r.books.entries[0].status,'timing-delay');
  assert.doesNotMatch(JSON.stringify([r.plan,r.books,r.request.context]),/波|门侧挂着钟牌/);
});
test('timing is an eligibility gate, composes with depth and once, disabled and stale-policy branches retain prior behavior',()=>{
  const s=sample([row('@@activate_only_after 3\n@@scan_depth 1\n@@dont_activate_after_match'),row('@@activate_only_every 3',{enabled:false})]);
  const once=new Map(),p=project(s,'钟',{once});assert.equal(p.books.entries[0].status,'triggered');
  assert.equal(p.books.entries[1].status,'disabled');assert.doesNotMatch(JSON.stringify(p.request.context),/@@/);
  assert.equal(project(s,null,{once}).books.entries[0].status,'not-triggered');
  once.set(p.books.entries[0].onceKey,{status:'once-used',messageId:'prior'});
  assert.equal(project(s,'钟',{once}).books.entries[0].status,'once-used');
  assert.doesNotMatch(JSON.stringify(project(s,'钟',{enabled:false}).request.context),/门侧挂着钟牌/);
});
test('offline HTTP host: repeat previews, reply, restart, exact regeneration and forks use the correct message position',async t=>{
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const directory=await mkdtemp(join(root,'timing-'));let h=await startTimingPreview(directory);t.after(()=>h.close());
  const where={familyId:h.familyId,branchId:h.branchId};
  const view=()=>h.api('view',{...where,targetId:null});
  const targetId=(await view()).targetId;
  async function preview(op,branch=where.branchId,messageId=null){
    const intent={branchId:branch,targetId,input:messageId?null:'报时',messageId};
    const p=await h.api('preview',{familyId:where.familyId,intent,operationId:op});
    return {p,cmd:{operationId:op,expectedRevision:p.revision,payload:{familyId:where.familyId,intent,previewHash:p.previewHash}}};
  }
  const timed=p=>p.entries.filter(e=>e.timing).map(e=>[e.timing.count,e.status]);
  const a=await preview('first');assert.deepEqual(timed(a.p),[[1,'timing-delay'],[1,'timing-interval']]);
  assert.deepEqual(await preview('first'),a);
  const sent=await h.api('send',a.cmd);assert.deepEqual(await h.api('send',a.cmd),sent);
  const next=await preview('next');assert.deepEqual(timed(next.p),[[2,'triggered'],[2,'triggered']]);
  await h.close();h=await startTimingPreview(directory);assert.deepEqual(await preview('next'),next);
  const [input,reply]=(await view()).messages.slice(-2);
  const regen=await preview('exact',where.branchId,reply.id);assert.deepEqual(regen.p.messages,a.p.messages);
  await h.api('send',regen.cmd);assert.deepEqual(timed((await preview('after-exact')).p),timed(next.p));
  for(const [point,count] of [[input.id,1],[reply.id,2]]){
    const b=await h.write('fork',{...where,point,title:'分支',session:null,player:null});
    assert.equal((await preview('check',b.branchId)).p.entries.find(e=>e.timing).timing.count,count);
  }
  const b=await preview('advance');const result=await h.api('send',b.cmd);
  const shelf=JSON.parse(await readFile(join(directory,'studio/character/independent-books/library/records/shelf.json')));
  const family=shelf.data.families[where.familyId];assert.deepEqual(family.f.requests[result.requestId].context,b.p.messages);
  assert.equal(family.f.requests[result.requestId].contextHash,b.p.requestHash);
  assert.deepEqual(family.plans[result.requestId].books,b.p.books);
  assert.deepEqual(timed((await preview('third')).p),[[3,'triggered'],[3,'timing-interval']]);
  assert.equal((await view()).timingPolicy,timingPolicy);
});
