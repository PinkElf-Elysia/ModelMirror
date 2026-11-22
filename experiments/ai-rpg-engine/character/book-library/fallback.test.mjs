import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {resolveFallback,fallbackPolicy} from './fallback.mjs';
import {startFallbackPreview} from './fallback-preview.mjs';
import {source} from '../revisions/test-support.mjs';
import {apply,digest} from '../revisions/contracts.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {defaults} from '../long-context/planner.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';
import {assemble} from './assembly.mjs';

const support={depth:true,once:true,timing:true};
test('ordered alternatives: unknown, disabled, invalid and first available; primary recognition wins',()=>{
  const content='@@unknown\r\n@@@activate_only_every 2\r\n@@@scan_depth 999\r\n@@@scan_depth 2\r\n@@@dont_activate_after_match\r\n原文\r\n第二行';
  const r=resolveFallback(content,{depth:true});
  assert.deepEqual(r.fallback[0].candidates.map(c=>c.reason),['unknown','off','invalid','available']);
  assert.equal(r.fallback[0].selected,3);assert.equal(r.content,'@@scan_depth 2\n原文\r\n第二行');
  assert.equal(resolveFallback(content,support).fallback[0].selected,1);
  assert.equal(resolveFallback('@@scan_depth 0\n@@@scan_depth 1\n正文',support).content,'@@scan_depth 0\n正文');
  const m=resolveFallback('@@unknown\n@@@scan_depth 2\n@@activate_only_after 3\n@@@scan_depth 1\n正文',support);
  assert.deepEqual(m.fallback.map(c=>c.selected),[1,0]);assert.match(m.content,/^@@scan_depth 2\n@@activate_only_after 3\n/);
});

test('eight consecutive fallback levels, orphan markers, malformed, missing choices and group limits',()=>{
  const levels=n=>['@@unknown',...Array(n-1).fill('@@@unknown'),'@@@scan_depth 1','正文'].join('\n');
  assert.equal(resolveFallback(levels(8),support).fallback[0].selected,8);
  assert.equal(resolveFallback(levels(9),support).status,'fallback-limit');
  for(const s of ['@@@scan_depth 2\n正文','@@unknown\n@@@@scan_depth 2\n正文','@@unknown\n\n@@@scan_depth 2\n正文','@@unknown\n@@@scan_depth 1\n '])
    assert.equal(resolveFallback(s,support).status,'invalid-fallback');
  assert.equal(resolveFallback('@@unknown\n@@@absent\n正文',support).status,'fallback-unavailable');
  assert.equal(resolveFallback('@@scan_depth 1\n'.repeat(17)+'@@@unknown\n正文',support).status,'fallback-limit');
});

test('card: missed keys, spent once claims and exhausted budget never choose the next decorator; unchanged V3 PNG',()=>{
  const s=source(),card=s.cards.actor.document.character.card;
  upgradeCard(card,()=>0);
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'sealed'}};
  const raw='@@dont_activate_after_match\r\n@@@scan_depth 2\r\n灯房的登记册。';
  card.data.character_book={entries:[{content:raw,extensions:{neutral:1},enabled:true,insertion_order:0,keys:['灯'],use_regex:false}],extensions:{}};
  delete s.sourceHash;s.sourceHash=digest(s);const f=apply(null,{action:'create',expectedRevision:null,operationId:'create',payload:{source:s}});
  const once=new Map();
  const run=(input,budget,enabled=true)=>assemble(f,'op',{branchId:f.rootId,targetId:'actor',kind:'send',input},fixtureModel(),{...defaults(),bookTokens:budget},[],{},false,once,true,false,enabled);
  const hit=run('查灯',100).books.entries[0];assert.equal(hit.status,'triggered');assert.equal(hit.fallback[0].selected,0);
  assert.equal(run('继续',100).books.entries[0].status,'not-triggered');
  const limited=run('查灯',0).books.entries[0];assert.equal(limited.status,'request-budget');assert.equal(limited.fallback[0].selected,0);
  once.set(hit.onceKey,{status:'once-used',messageId:'used'});const used=run('查灯',100);
  assert.equal(used.books.entries[0].status,'once-used');assert.equal(used.books.entries[0].fallback[0].selected,0);
  assert.doesNotMatch(JSON.stringify(used.request.context),/灯房的登记册|@@/);
  assert.doesNotMatch(JSON.stringify(run('查灯',100,false).request.context),/灯房的登记册/);
  assert.deepEqual(inspectImport(exportCard(card,{format:'png'}).bytes).card.data.character_book,card.data.character_book);
});

test('HTTP branch preview, member isolation, fixture receipt, restart, exact regeneration and fork',async t=>{
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const dir=await mkdtemp(join(root,'fallback-'));let h=await startFallbackPreview(dir);t.after(()=>h.close());
  const {familyId,branchId}=h;
  const view=await h.api('view',{familyId,branchId,targetId:null});
  const [target,other]=view.members;
  const preview=(operationId,options={})=>h.api('preview',{familyId,operationId,intent:{branchId,targetId:target.id,input:'对灯',messageId:null,...options}});
  const a=await preview('lamp');assert.deepEqual(await preview('lamp'),a);
  assert.deepEqual(a.entries.filter(e=>e.fallback).map(e=>[e.status,e.fallback[0].selected]),[['triggered',1],['timing-delay',0]]);
  assert.doesNotMatch(JSON.stringify(await preview('hidden',{targetId:other.id})),/fixture_lamp|灯房名录|灯芯收在/);
  const cmd={operationId:'lamp',expectedRevision:a.revision,payload:{familyId,previewHash:a.previewHash,intent:{branchId,targetId:target.id,input:'对灯',messageId:null}}};
  const sent=await h.api('send',cmd);assert.deepEqual(await h.api('send',cmd),sent);
  const shelf=JSON.parse(await readFile(join(dir,'studio/character/independent-books/library/records/shelf.json'))).data.families[familyId];
  assert.deepEqual(shelf.f.requests[sent.requestId].context,a.messages);assert.equal(shelf.f.requests[sent.requestId].contextHash,a.requestHash);
  assert.deepEqual(shelf.plans[sent.requestId].books,a.books);
  await h.close();h=await startFallbackPreview(dir);
  const b=await preview('next');assert.deepEqual(b.entries.filter(e=>e.fallback).map(e=>e.status),['triggered','triggered']);
  const reply=shelf.f.branches[branchId].messages.at(-1).id;
  assert.deepEqual((await preview('exact',{messageId:reply,input:null})).messages,a.messages);
  const fork=await h.write('fork',{familyId,branchId,point:reply,title:'灯房分支',session:null,player:null});
  assert.equal((await h.api('view',{...fork,targetId:target.id})).fallbackPolicy,fallbackPolicy);
  assert.deepEqual((await preview('child',{branchId:fork.branchId})).entries,b.entries);
});
