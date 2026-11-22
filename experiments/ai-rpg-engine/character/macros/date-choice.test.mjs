import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {macroPolicy,render} from './date-choice.mjs';
import {startMacroPreview} from './preview.mjs';
import {exportCard,inspectImport} from '../exchange.mjs';
import {source} from '../revisions/test-support.mjs';
import {copy,digest} from '../revisions/contracts.mjs';
import {BookWorkspace} from '../book-library/store.mjs';

async function directory(){
  const root=process.env.CHARACTER_R5C_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});return mkdtemp(join(root,'macro-'));
}
const before='2031-06-18T23:59:59.999Z',after='2031-06-19T00:00:00.000Z';
test('bounded literal macros, independent occurrences, inactive syntax and no recursive expansion',()=>{
  const plain='{{foo::{{date}}}} {{pick::单项}} {{pick::甲::::乙}} {{pick::a::<BOT>}} {{char}}';
  const text='{{DATE}} {{pick::甲::乙}} {{pick::甲::乙}} '+plain;
  const result=render(text,'description','neutral','2031-06-18');
  assert.equal(result.items.length,3);assert.equal(result.items[0].value,'2031-06-18');
  assert.ok(result.text.endsWith(plain));assert.ok(result.items[1].offset<result.items[2].offset);
  assert.deepEqual(render(text,'description','neutral','2031-06-19').items.slice(1),result.items.slice(1));
  assert.throws(()=>render('{{date}}'.repeat(129),'description','k','d'),/MACRO_FIELD_LIMIT/);
});

test('HTTP preview is read-only, midnight rejects old preview, prepared snapshot survives restart and exact regeneration',async t=>{
  let current=before;
  const dir=await directory(),clock=()=>new Date(current),open=()=>startMacroPreview(dir,0,0,clock);
  let h=await open();t.after(()=>h.close());
  const {familyId,branchId}=h,loc={familyId,branchId},view=()=>h.api('view',{...loc,targetId:null});
  const root=await view(),targetId=root.targetId;
  const bytes=()=>readFile(join(dir,'studio/character/independent-books/library/records/shelf.json'));
  async function preview(op,input='查班',bid=branchId,actor=targetId,messageId=null){
    const intent={branchId:bid,targetId:actor,messageId,input};
    const p=await h.api('preview',{familyId,intent,operationId:op});
    return {p,command:{operationId:op,expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}}};
  }
  const original=await bytes(),a=await preview('send'),otherOp=await preview('other-op','再查班');
  assert.deepEqual(a.p.macros,otherOp.p.macros);assert.equal(a.p.macros.items.length,2);
  const [date,pick]=a.p.macros.items;assert.equal(date.value,'2031-06-18');assert.match(pick.value,/^(蓝灯|白旗)$/);
  assert.ok(a.p.messages[0].content.includes('值日日期 '+date.value+'，班牌为'+pick.value));
  assert.deepEqual(await bytes(),original);
  current=after;
  await assert.rejects(h.api('send',a.command),/PREVIEW_CHANGED/);
  assert.deepEqual(await bytes(),original);
  const fresh=await preview('send');assert.equal(fresh.p.macros.items[0].value,'2031-06-19');assert.deepEqual(fresh.p.macros.items[1],pick);
  const sent=await h.api('send',fresh.command);assert.deepEqual(await h.api('send',fresh.command),sent);
  const stored=JSON.parse(await bytes()).data.families[familyId];
  assert.deepEqual(stored.f.requests[sent.requestId].context,fresh.p.messages);
  assert.deepEqual(stored.plans[sent.requestId].macros,fresh.p.macros);
  const reply=(await view()).messages.at(-1).id;
  await h.close();current='2031-06-21T15:00:00Z';h=await open();
  assert.deepEqual((await view()).requests.find(r=>r.id===sent.requestId).macros,fresh.p.macros);
  const exact=await preview('exact',null,branchId,targetId,reply);
  assert.deepEqual(exact.p.macros,fresh.p.macros);assert.deepEqual(exact.p.messages,fresh.p.messages);
  const regen=await h.api('send',exact.command);
  assert.deepEqual((await view()).requests.find(r=>r.id===regen.requestId).macros,fresh.p.macros);
  const ref=root.bindings.find(b=>b.title==='渡口值日日签').selected;
  const child=await h.write('fork',{...loc,point:reply,title:'私有日签',player:null,session:[{readers:[root.members[1].id],ref}]});
  assert.deepEqual((await preview('hidden','查班',child.branchId)).p.macros.items,[]);
  assert.deepEqual((await preview('absent','无匹配')).p.macros.items,[]);
});

test('only eligible target fields expand; original card, history, player input, sibling and old branch remain independent',async t=>{
  const dir=await directory(),s=source(),card=s.cards.actor.document.character.card;
  Object.assign(card.data,{description:'{{char}}登记{{date}}',scenario:'亭前 {{pick::晴::雨}}',creator_notes:'隐藏 {{date}}',
    post_history_instructions:'{{original}} 收班 {{date}}',mes_example:'示例 {{pick::一::二}}'});
  s.messages[0].content='开场 {{date}}';
  card.data.character_book={extensions:{},entries:[{keys:['值班'],enabled:true,insertion_order:0,extensions:{},content:'{{date}} 已挂牌'}]};
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  delete s.sourceHash;s.sourceHash=digest(s);
  const raw=exportCard(card,{format:'json'}).bytes;
  assert.ok(inspectImport(raw).report.warnings.some(w=>w.path==='/data/description'&&w.message.includes('日期与选择宏')));
  const expected=copy(s),sourceOpts={read:async()=>copy(s),verify:async()=>copy(s)};
  const options={directory:dir,sources:sourceOpts,now:()=>new Date(before)};
  let desk=await BookWorkspace.open(options);t.after(()=>desk.close());
  let rev=0;
  const openBranch=async(op,policy={})=>desk.command('open',{operationId:op,expectedRevision:rev++,payload:{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,player:[],session:[],...policy}});
  const old=await openBranch('old'),branch=await openBranch('new',{macroPolicy});
  const preview=b=>desk.preview(b.familyId,{branchId:b.branchId,targetId:'actor',messageId:null,input:'值班 {{date}}'},'sample');
  const legacy=await preview(old),now=await preview(branch);
  assert.equal(legacy.macros,undefined);assert.match(legacy.messages[0].content,/旅人登记\{\{date\}\}/);
  assert.match(now.messages[0].content,/旅人登记2031-06-18/);assert.match(now.messages[0].content,/2031-06-18 已挂牌/);
  assert.ok(now.messages.some(m=>m.content==='开场 {{date}}'));assert.ok(now.messages.some(m=>m.content==='值班 {{date}}'));
  assert.doesNotMatch(JSON.stringify(now.macros),/隐藏|creator_notes|first_mes/);
  assert.equal(now.messages.at(-1).content.trim(),'收班 2031-06-18');assert.deepEqual(s,expected);
  assert.deepEqual(await preview(old),legacy);
  assert.deepEqual(exportCard(card,{format:'json'}).bytes,raw);
});

test('lookup after interrupted receipt uses the saved request without consulting the clock or dispatching again',async t=>{
  const dir=await directory(),s=source();s.cards.actor.document.character.card.data.description='日期 {{date}}';
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  delete s.sourceHash;s.sourceHash=digest(s);
  const options={directory:dir,sources:{read:async()=>copy(s),verify:async()=>copy(s)},now:()=>new Date(before),fault:phase=>{if(phase==='afterReceipt')throw Error('fixture interruption');}};
  let desk=await BookWorkspace.open(options);t.after(()=>desk.close());
  const {familyId,branchId}=await desk.command('open',{operationId:'open',expectedRevision:0,payload:{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,session:[],player:[],macroPolicy}});
  const intent={branchId,targetId:'actor',input:'核对',messageId:null},p=await desk.preview(familyId,intent,'send');
  const {requestId}=await desk.command('send',{operationId:'send',expectedRevision:p.revision,payload:{familyId,intent,previewHash:p.previewHash}});
  assert.equal((await desk.view(familyId,branchId,'actor')).requests[0].status,'unknown');
  await desk.close();desk=await BookWorkspace.open({...options,now:()=>{throw Error('unexpected clock read');}});
  await desk.recover(familyId,requestId);await desk.recover(familyId,requestId);
  const result=await desk.view(familyId,branchId,'actor');assert.equal(result.requests[0].status,'completed');
  assert.deepEqual(result.requests[0].macros,p.macros);assert.equal(desk.fixture.dispatchCount,0);
});
