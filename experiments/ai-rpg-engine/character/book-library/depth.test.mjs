import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {source} from '../revisions/test-support.mjs';
import {apply,digest,copy} from '../revisions/contracts.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {defaults} from '../long-context/planner.mjs';
import {assemble} from './assembly.mjs';
import {BookWorkspace} from './store.mjs';
import {depthHeader,depthPolicy} from './depth.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';

function entry(n,key,extra={}){
  return {keys:[key],content:(n===null?'':'@@scan_depth '+n+'\n')+'资料'+key,
    enabled:true,use_regex:false,extensions:{},insertion_order:0,...extra};
}
function fixture(entries,bookDepth=0){
  const s=source(),card=s.cards.actor.document.character.card;
  upgradeCard(card,()=>0);card.data.character_book={entries,scan_depth:bookDepth,extensions:{}};
  s.kind='group';
  for(const state of [s.current,s.initial,...s.messages.map(m=>m.boundary)])
    state.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  return s;
}
function family(s){
  delete s.sourceHash;s.sourceHash=digest(s);
  return apply(null,{action:'create',expectedRevision:null,operationId:'create',payload:{source:s}});
}
function run(f,input,{capacity=32000,depth=true,regex=false,once=null}={}){
  return assemble(f,'turn',{branchId:f.rootId,kind:'send',targetId:'actor',input},
    {...fixtureModel(),contextTokens:capacity},defaults(),[],{},regex,once,depth);
}
const statuses=r=>r.books.entries.map(e=>e.status);
const hit='triggered',miss='not-triggered';

test('depth overrides only its entry in either direction; current input takes one slot and continuation takes none',()=>{
  const rows=[entry(0,'此刻'),entry(1,'此刻'),entry(1,'末条'),entry(2,'末条'),entry(2,'前条'),entry(3,'前条'),entry(null,'末条')];
  const s=fixture(rows);s.messages[3].content='前条';s.messages[4].content='末条';const f=family(s);
  const r=run(f,'此刻');assert.deepEqual(statuses(r),[miss,hit,miss,hit,miss,hit,miss]);
  assert.deepEqual(r.books.entries[3].window,{limit:2,history:1,current:true});
  const c=run(f,null);assert.deepEqual(statuses(c),[miss,miss,hit,hit,hit,hit,miss]);
  assert.deepEqual(c.books.entries[2].window,{limit:1,history:1,current:false});
  s.cards.actor.document.character.card.data.character_book.scan_depth=4;
  assert.deepEqual(statuses(run(family(s),'此刻')),[miss,hit,miss,hit,miss,hit,hit]);
  assert.doesNotMatch(JSON.stringify(r.request.context),/@@scan_depth/);
});

test('invalid declarations fail closed, exact CRLF and values 0 and 200 survive card exchange',()=>{
  for(const n of ['-1','201','2.5','0x02','2 3','+1','']){
    assert.deepEqual(depthHeader('@@scan_depth '+n+'\n正文'),{invalid:true});
  }
  for(const body of ['@@scan_depth 2\n@@scan_depth 1\n正文','@@scan_depth 1\n@@future\n正文','@@scan_depth 1\n '])
    assert.deepEqual(depthHeader(body),{invalid:true});
  const s=fixture([entry(200,'水',{content:'@@scan_depth 200\r\n甲\r\n乙'}),entry(201,'水'),entry(0,'水',{constant:true})]);
  assert.equal(depthHeader(s.cards.actor.document.character.card.data.character_book.entries[0].content).body,'甲\r\n乙');
  assert.deepEqual(statuses(run(family(s),'水')),[hit,'invalid-scan-depth',hit]);
  const original=s.cards.actor.document.character.card;
  assert.deepEqual(inspectImport(exportCard(original,{format:'png'}).bytes).card.data.character_book,original.data.character_book);
});

test('primary, secondary and bounded regex share the override; recursive text is not history',()=>{
  const s=fixture([entry(1,'此刻',{selective:true,secondary_keys:['前文']}),entry(2,'此刻',{selective:true,secondary_keys:['前文']}),
    entry(1,'/前文$/',{use_regex:true}),entry(2,'/前文/m',{use_regex:true}),
    entry(null,'此刻',{content:'暗号'}),entry(2,'暗号'),entry(null,'暗号')]);
  s.cards.actor.document.character.card.data.character_book.recursive_scanning=true;s.messages.at(-1).content='前文';
  const f=family(s);assert.deepEqual(statuses(run(f,'此刻',{regex:true})),[miss,hit,miss,hit,hit,miss,hit]);
  assert.equal(statuses(run(f,'此刻'))[3],'unsupported-behavior');
});

test('history windows count only participating and addressed-visible messages; hidden text and ids stay out of diagnostics',()=>{
  const s=fixture([entry(3,'蓝纸')]);
  s.current.members[1].intervals=[{from:2,until:3},{from:5,until:null}];
  const texts=['缺席甲蓝纸','曾经看过','私信乙蓝纸','缺席丙蓝纸','已回来'];
  s.messages.forEach((m,i)=>{m.content=texts[i];m.id='visible-test-'+i;});
  s.messages[2].visibility={kind:'restricted',memberIds:['player']};
  const r=run(family(s),'现在');assert.deepEqual(statuses(r),[miss]);
  assert.deepEqual(r.books.entries[0].window,{limit:3,history:2,current:true});
  assert.doesNotMatch(JSON.stringify([r.plan,r.books,r.request.context]),/缺席甲|私信乙|缺席丙|visible-test-[023]/);
  s.messages[1].content='曾经看过蓝纸';
  assert.deepEqual(statuses(run(family(s),'现在')),[hit]);
});

test('byte-capacity eviction removes a prior hit; preview, saved fixture request, exact regeneration and restart agree',async t=>{
  const s=fixture([entry(6,'旧尺',{content:'@@scan_depth 6\n木架有四格。'}),entry(1,'眼前')]);
  s.messages[1].content='旧尺'+'潮'.repeat(12000);
  const f=family(s);
  assert.deepEqual(statuses(run(f,'眼前',{capacity:64000})),[hit,hit]);
  const narrow=run(f,'眼前');assert.deepEqual(statuses(narrow),[miss,hit]);
  assert.equal(narrow.plan.trace.find(t=>t.id===s.messages[1].id).status,'omitted');
  assert.doesNotMatch(JSON.stringify(narrow.request.context),/旧尺|木架有四格/);
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const directory=await mkdtemp(join(root,'depth-'));
  const opts={directory,sources:{read:async()=>copy(s),verify:async()=>copy(s)}};
  let store=await BookWorkspace.open(opts);t.after(()=>store.close());
  const write=async(action,payload)=>store.command(action,{operationId:crypto.randomUUID(),expectedRevision:(await store.list()).revision,payload});
  const parent=await write('open',{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[]});
  const peek=(where,operationId,messageId=null)=>store.preview(where.familyId,{branchId:where.branchId,targetId:'actor',messageId,input:messageId?null:'眼前'},operationId);
  const old=await peek(parent,'same');
  const where=await write('fork',{...parent,point:null,title:'深度',player:null,session:null,depthPolicy});
  assert.equal((await peek(parent,'same')).previewHash,old.previewHash);
  const p=await peek(where,'send-depth');assert.deepEqual(p.entries.map(e=>e.status),[miss,hit]);
  const intent={branchId:where.branchId,targetId:'actor',input:'眼前',messageId:null};
  const cmd={operationId:'send-depth',expectedRevision:p.revision,payload:{familyId:where.familyId,intent,previewHash:p.previewHash}};
  const sent=await store.command('send',cmd);
  await store.close();store=await BookWorkspace.open(opts);
  assert.deepEqual(await store.command('send',cmd),sent);
  const shelf=JSON.parse(await readFile(join(directory,'library/records/shelf.json'))).data;
  const row=shelf.families[where.familyId],request=row.f.requests[sent.requestId];
  assert.equal(request.status,'completed');assert.deepEqual(request.context,p.messages);
  assert.equal(request.contextHash,p.requestHash);assert.equal(digest(request.context),p.requestHash);
  assert.deepEqual(row.plans[sent.requestId].books,p.books);
  const reply=row.f.branches[where.branchId].messages.at(-1);
  assert.deepEqual((await peek(where,'regen',reply.id)).messages,p.messages);
  const child=await write('fork',{...where,point:reply.id,title:'继承',player:null,session:null});
  assert.equal((await store.view(child.familyId,child.branchId)).depthPolicy,depthPolicy);
  const earlier=await write('fork',{...where,point:'m4',title:'之前',player:null,session:null});
  assert.equal((await store.view(earlier.familyId,earlier.branchId)).depthPolicy,undefined);
  assert.equal((await peek(parent,'same')).previewHash,old.previewHash);
});

test('single-use composition requires both policies in either header order and respects previous claims',()=>{
  const rows=['@@scan_depth 1\n@@dont_activate_after_match','@@dont_activate_after_match\r\n@@scan_depth 2']
    .map(h=>entry(1,'水',{content:h+'\n中性说明'}));
  const f=family(fixture(rows)),once=new Map();
  for(const options of [{depth:false,once},{depth:true}]){
    const skipped=run(f,'水',options);
    assert.deepEqual(skipped.plan.trace.find(t=>t.kind==='book').entries.map(e=>e.status),['unsupported-behavior','unsupported-behavior']);
    assert.doesNotMatch(JSON.stringify(skipped.request.context),/中性说明/);
  }
  const r=run(f,'水',{once});assert.deepEqual(statuses(r),[hit,hit]);assert.doesNotMatch(JSON.stringify(r.request.context),/@@/);
  for(const e of r.books.entries)once.set(e.onceKey,{status:'once-used',messageId:'old-reply'});
  assert.deepEqual(statuses(run(f,'水',{once})),['once-used','once-used']);
});
