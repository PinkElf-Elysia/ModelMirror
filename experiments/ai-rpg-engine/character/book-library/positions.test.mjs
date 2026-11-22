import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {source} from '../revisions/test-support.mjs';
import {apply,digest} from '../revisions/contracts.mjs';
import {fixtureModel} from '../revisions/sources.mjs';
import {defaults,count,cost} from '../long-context/planner.mjs';
import {upgradeCard} from '../v3/metadata.mjs';
import {assemble} from './assembly.mjs';
import {startPositionsPreview} from './positions-preview.mjs';
import {resolveFallback} from './fallback.mjs';

const entry=(content,insertion_order=0,extra={})=>({keys:[],constant:true,enabled:true,extensions:{},content,insertion_order,...extra});
function fixture(entries,configure=()=>{}){
  const s=source(),card=s.cards.actor.document.character.card;
  upgradeCard(card,()=>0);
  Object.assign(card.data,{description:'描述',personality:'性格',scenario:'场景',mes_example:'示例',post_history_instructions:'末指令',character_book:{entries,extensions:{}}});
  for(const st of [s.current,s.initial,...s.messages.map(m=>m.boundary)])st.members[1].binding={reference:{workId:'neutral',versionId:'v1'}};
  configure(s,card);delete s.sourceHash;s.sourceHash=digest(s);
  return apply(null,{action:'create',operationId:'neutral',expectedRevision:null,payload:{source:s}});
}
function run(f,settings=defaults(),model=fixtureModel(),positions=true){
  return assemble(f,'turn',{branchId:f.rootId,kind:'send',targetId:'actor',input:'本轮'},model,settings,[],{},false,null,false,false,false,null,null,false,false,null,positions);
}

test('four named anchors, same-slot order, ordinary book positions and post-history instruction',()=>{
  const f=fixture([entry('普通前',0,{position:'before_char'}),entry('@@position before_desc\n标甲'),
    entry('@@position after_desc\n同文乙',4),entry('@@position after_desc\n同文甲',1),
    entry('@@position personality\n性格注'),entry('@@position scenario\n场景注'),entry('普通后')]);
  const r=run(f),wire=r.request.context;
  assert.ok(wire[0].content.endsWith(['普通前','标甲','描述','同文甲','同文乙','性格','性格注','场景','场景注','普通后','【对话示例，非真实经历】\n示例'].join('\n\n')));
  assert.equal(wire.at(-1).content,'末指令');assert.equal(r.books.placements.length,5);
  const old=run(f,defaults(),fixtureModel(),false);
  assert.doesNotMatch(old.request.context[0].content,/同文甲|标甲/);
  const dup=run(fixture([entry('@@position after_desc\n描述'),entry('@@position before_desc\n描述')]));
  assert.match(dup.request.context[0].content,/描述\n\n描述\n\n描述\n\n性格/);
});

test('only available valid sections are admitted; unknown roles and unenabled fallback never enter the wire',()=>{
  const f=fixture([entry('@@position missing\n不应出现'),entry('@@position personality\n不应出现'),
    entry('@@depth 201\n不应出现'),entry('@@role assistant\n@@depth 0\n不应出现'),
    entry('@@depth 0\n@@position scenario\n唯一场景')],(s,c)=>{c.data.personality='';});
  const r=run(f);
  assert.deepEqual(r.books.entries.map(x=>x.status),['invalid-position','position-section-absent','invalid-position','unsupported-behavior','triggered']);
  assert.doesNotMatch(JSON.stringify(r.request.context),/不应出现/);assert.equal(r.books.placements[0].slot,'scenario');
  assert.equal(resolveFallback('@@unknown\n@@@position scenario\n正文',{positions:true}).content,'@@position scenario\n正文');
});

test('depth counts only retained visible messages, leaves host instructions last, charges final message framing',()=>{
  const hidden='隔离的原话',f=fixture([entry('@@depth 200\n最早'),entry('@@depth 1\n输入前'),entry('@@depth 0\n输入后')],
    s=>{s.messages[2].visibility={kind:'restricted',memberIds:['player']};s.messages[2].content=hidden;});
  const policy={...defaults(),historyLimit:0,pinOpening:false},r=run(f,policy),m=r.request.context;
  assert.deepEqual(m.slice(1).map(v=>v.content),['最早','输入前','本轮','输入后','末指令']);
  assert.doesNotMatch(JSON.stringify(r.books)+JSON.stringify(m),new RegExp(hidden));
  assert.deepEqual(r.plan.counts,count(m,fixtureModel()));assert.deepEqual(r.request.reservation,cost(m,fixtureModel()));
  assert.equal(r.request.contextHash,digest(m));
  const long=run(f);assert.ok(long.books.placements.find(v=>v.depth===0).afterMessages>1);
  const narrow={...fixtureModel(),contextTokens:r.plan.counts.inputBound+r.plan.counts.outputReserved-1};
  const reduced=run(f,policy,narrow);assert.ok(reduced.plan.counts.inputBound+narrow.outputTokens<=narrow.contextTokens);
  assert.equal(reduced.books.entries.at(-1).status,'request-budget');
});

test('group preview equals persisted request; fork with different bindings and restarted exact generation use original layout',async t=>{
  const root=process.env.CHARACTER_R5C_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const dir=await mkdtemp(join(root,'position-'));let h=await startPositionsPreview(dir);t.after(()=>h.close());
  const {familyId,branchId}=h,loc={familyId,branchId},view=await h.api('view',{...loc,targetId:null}),targetId=view.targetId;
  const intent=(bid,messageId=null)=>({branchId:bid,targetId,messageId,input:messageId?null:'巡查'});
  const preview=(operationId,i)=>h.api('preview',{familyId,operationId,intent:i});
  const send=(operationId,i,p)=>h.api('send',{operationId,expectedRevision:p.revision,payload:{familyId,intent:i,previewHash:p.previewHash}});
  const row=async()=>JSON.parse(await readFile(join(dir,'studio/character/independent-books/library/records/shelf.json'),'utf8')).data.families[familyId];
  const i=intent(branchId),p=await preview('first',i);assert.deepEqual(await preview('first',i),p);
  assert.match(p.messages[0].content,/先核对巡查牌。\n\n原创中性验收夹具。\n\n携带木尺。/);
  assert.match(p.messages[0].content,/渡口茶亭\n\n亭旁有一盏灯。/);
  assert.deepEqual(p.messages.slice(-4).map(m=>m.role),['system','user','system','user']);
  assert.equal(p.messages.at(-4).content,'请留意水线。');assert.ok(p.messages.at(-1).content.includes('hostInstruction'));
  const {requestId}=await send('first',i,p),original=await row();assert.deepEqual(original.f.requests[requestId].context,p.messages);
  assert.equal(original.f.requests[requestId].contextHash,p.requestHash);assert.deepEqual(original.plans[requestId].books,p.books);
  const reply=original.f.branches[branchId].messages.at(-1).id;
  const ref=view.bindings.find(v=>v.title==='渡口巡查手册').selected;
  const child=await h.write('fork',{...loc,point:reply,title:'交接后',player:null,session:[{ref,readers:[view.members[1].id]}]});
  await h.close();h=await startPositionsPreview(dir);
  const fresh=await preview('new-layout',intent(child.branchId));assert.doesNotMatch(JSON.stringify(fresh),/先核对巡查牌|亭旁有一盏灯/);
  const exactIntent=intent(child.branchId,reply),exact=await preview('exact',exactIntent);
  assert.deepEqual(exact.messages,p.messages);assert.deepEqual(exact.books.original,p.books);assert.equal(exact.requestHash,p.requestHash);
  const regen=await send('exact',exactIntent,exact);assert.deepEqual(await send('exact',exactIntent,exact),regen);
  const final=await row();assert.deepEqual(final.f.requests[regen.requestId].context,p.messages);
  assert.deepEqual(final.f.branches[branchId],original.f.branches[branchId]);
});
