import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {exclusiveField,exclusivePolicy,exclusiveResolver} from './exclusive.mjs';
import {startExclusivePreview} from './exclusive-preview.mjs';
import {probabilityField,probabilityGate} from './probability.mjs';
import {selectV3,withMeter} from '../v3/selection.mjs';
import {source} from '../revisions/test-support.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';
import {BookWorkspace} from './store.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {assemble} from './assembly.mjs';
import {apply,copy,digest} from '../revisions/contracts.mjs';
import {oncePolicy} from './once.mjs';
import {keepPolicy} from './keep.mjs';
import {cooldownPolicy} from './cooldown.mjs';

async function directory(){
  const root=process.env.CHARACTER_R5B_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});return mkdtemp(join(root,'exclusive-'));
}
const row=(group,priority,body,extra={})=>({keys:['信号'],enabled:true,content:body,priority,insertion_order:0,
  extensions:{[exclusiveField]:group,opaque:{note:'原文'}},...extra});
function pick(entries,options={}){
  const card=source().cards.actor.document.character.card;upgradeCard(card,()=>0);
  const book={entries,extensions:{},recursive_scanning:true,...options};card.data.character_book=book;
  return withMeter(clock=>selectV3(card,[],'信号',{},clock,probabilityGate(book,'turn','book'),exclusiveResolver(book)));
}
const statuses=r=>r.trace.map(e=>e.status);
test('one per case-sensitive group, stable priority tie, blank and foreign entries remain independent, invalid values round trip',()=>{
  const entries=[row(' 北门 ',1,'甲'),row('北门',2,'乙'),row('北门',2,'丙'),row('南门',0,'丁'),row('',0,'戊'),
    row(null,1,'己'),row('界'.repeat(65),1,'庚'),row('A',0,'辛'),row('a',0,'壬'),row(null,0,'癸',{extensions:{group:'北门'}})];
  const r=pick(entries);assert.deepEqual(statuses(r),['exclusive-suppressed','triggered','exclusive-suppressed','triggered','triggered','invalid-exclusive','invalid-exclusive','triggered','triggered','triggered']);
  assert.deepEqual(r.trace[0].exclusive,{name:'北门',winner:1});
  const card=source().cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book={entries,extensions:{}};
  assert.deepEqual(inspectImport(exportCard(card,{format:'png'}).bytes).card.data.character_book,card.data.character_book);
});

test('only eligible winners seed recursion; later passes cannot displace them, and budget rejection does not promote a loser',()=>{
  const entries=[row('班',1,'黄铜令牌'),row('班',2,'蓝色旗子'),row('班',9,'递归争位',{keys:['蓝色旗子']}),
    row('',0,'不应出现',{keys:['黄铜令牌']}),row('',0,'瞭望记录',{keys:['蓝色旗子']}),
    row('班',10,'概率拦截',{extensions:{[exclusiveField]:'班',[probabilityField]:0}}),row('班',11,'已停用',{enabled:false})];
  assert.deepEqual(statuses(pick(entries)),['exclusive-suppressed','triggered','exclusive-suppressed','not-triggered','triggered','probability-miss','disabled']);
  assert.deepEqual(statuses(pick([row('班',1,'短'),row('班',2,'长'.repeat(100))],{token_budget:3})),['exclusive-suppressed','budget-excluded']);
});

test('pinned books compete independently, restricted readers see no private group diagnostics; preview, send, exact rebuild and restart agree',async t=>{
  const dir=await directory();let h=await startExclusivePreview(dir);t.after(()=>h.close());
  const {familyId,branchId}=h,root={familyId,branchId},view=()=>h.api('view',{...root,targetId:null});
  const [actor,other]=(await view()).members;
  async function preview(op,targetId=actor.id,bid=branchId,messageId=null){
    const intent={branchId:bid,targetId,messageId,input:messageId?null:'看信号'};
    const p=await h.api('preview',{familyId,intent,operationId:op});
    return {p,cmd:{operationId:op,expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}}};
  }
  const first=await preview('send'),st=first.p.entries.filter(e=>e.title==='渡口信号簿').map(e=>e.status);
  assert.deepEqual(st,['exclusive-suppressed','triggered','probability-miss']);
  assert.match(first.p.messages[0].content,/升起一面蓝旗/);assert.doesNotMatch(first.p.messages[0].content,/亮起一盏白灯|响了三声/);
  assert.deepEqual(first,await preview('send'));
  const sent=await h.api('send',first.cmd);assert.deepEqual(await h.api('send',first.cmd),sent);
  const reply=(await view()).messages.at(-1);
  await h.close();h=await startExclusivePreview(dir);
  const exact=await preview('regen',actor.id,branchId,reply.id);
  assert.deepEqual(exact.p.books.original,first.p.books);assert.deepEqual(exact.p.messages,first.p.messages);
  const old=(await view()).bindings.find(b=>b.title==='渡口信号簿').selected;
  const {bookId}=await h.write('save',{bookId:null,title:'内湾值班簿',book:{entries:[
    row('渡口信号',20,'内湾暗记',{constant:true})],extensions:{}}});
  const ref=await h.write('seal',{bookId});
  const child=await h.write('fork',{...root,point:null,title:'内湾',player:null,session:[{ref:old,readers:'all'},{ref,readers:[actor.id]}]});
  const mine=await preview('both',actor.id,child.branchId),theirs=await preview('one',other.id,child.branchId);
  assert.match(mine.p.messages[0].content,/内湾暗记/);assert.match(mine.p.messages[0].content,/升起一面蓝旗/);
  assert.doesNotMatch(JSON.stringify(theirs.p),/内湾暗记|内湾值班簿/);
});

test('interrupted local adoption restores only the winner, losing entries acquire no once, keep or cooldown claim',async t=>{
  const s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  card.data.character_book={extensions:{},entries:[row('值守',1,'@@dont_activate_after_match\n一次'),
    row('值守',2,'@@keep_activate_after_match\n持续',{extensions:{[exclusiveField]:'值守','modelmirror.cooldown':2}}),row('值守',8,'记录已核对')]};
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  delete s.sourceHash;s.sourceHash=digest(s);
  const dir=await directory(),options={directory:dir,sources:{read:async()=>copy(s),verify:async()=>copy(s)},fault:async phase=>{if(phase==='afterReceipt')throw Error('local interruption');}};
  let desk=await BookWorkspace.open(options);t.after(()=>desk.close());
  const {familyId,branchId}=await desk.command('open',{operationId:'open',expectedRevision:0,payload:{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[],oncePolicy,keepPolicy,cooldownPolicy,exclusivePolicy}});
  const intent={branchId,targetId:'actor',input:'信号',messageId:null},p=await desk.preview(familyId,intent,'send');
  assert.deepEqual(p.entries.map(e=>e.status),['exclusive-suppressed','exclusive-suppressed','triggered']);
  const {requestId}=await desk.command('send',{operationId:'send',expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}});
  await desk.close();desk=await BookWorkspace.open(options);await desk.recover(familyId,requestId);await desk.recover(familyId,requestId);
  const f=JSON.parse(await readFile(join(dir,'library/records/shelf.json'))).data.families[familyId],plan=f.plans[requestId];
  assert.equal(desk.fixture.dispatchCount,0);assert.deepEqual(f.f.requests[requestId].context,p.messages);assert.deepEqual(plan.books,p.books);
  assert.deepEqual([plan.onceKeys,plan.keepKeys,plan.cooldownKeys],[[],[],[]]);
});

test('eviction recomputes same-book winner from remaining visible history and input',()=>{
  const s=source(),card=s.cards.actor.document.character.card;upgradeCard(card,()=>0);
  s.kind='group';
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  card.data.character_book={extensions:{},entries:[
    row('记录',8,'@@scan_depth 6\n木架四格',{keys:['旧尺']}),row('记录',1,'@@scan_depth 1\n当前信号')]};
  s.messages[1].content='旧尺'+'潮'.repeat(12000);delete s.sourceHash;s.sourceHash=digest(s);
  const f=apply(null,{action:'create',expectedRevision:null,operationId:'new',payload:{source:s}});
  const run=capacity=>assemble(f,'turn',{branchId:f.rootId,kind:'send',targetId:'actor',input:'信号'},
    {...fixtureModel(),contextTokens:capacity},undefined,[],{},false,null,true,false,false,null,null,false,true);
  assert.deepEqual(run(64000).books.entries.map(e=>e.status),['triggered','exclusive-suppressed']);
  const narrow=run(32000);assert.deepEqual(narrow.books.entries.map(e=>e.status),['not-triggered','triggered']);
  assert.match(JSON.stringify(narrow.request.context),/当前信号/);assert.doesNotMatch(JSON.stringify(narrow.request.context),/旧尺|木架四格/);
});
