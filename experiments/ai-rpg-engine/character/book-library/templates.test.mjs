import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {startTemplatePreview} from './template-preview.mjs';
import {planSwitch} from './templates.mjs';
import {digest,copy} from '../revisions/contracts.mjs';

for(const kind of ['single','group'])test(kind+' · sealed replacement at a historical anchor',async t=>{
  const dir=await mkdtemp(join(process.env.CHARACTER_R5A_TEST_ROOT??tmpdir(),'template-'));
  let h=await startTemplatePreview(dir);t.after(()=>h.close());
  const root=h.roots[kind],{v1,v2,workId}=h;
  const read=(where,targetId=null)=>h.api('view',{...where,targetId});
  const first=await read(root),targetId=first.targetId;
  const preview=(where,input,operationId,messageId=null,actor=targetId)=>h.api('preview',{familyId:where.familyId,operationId,intent:{branchId:where.branchId,targetId:actor,messageId,input}});
  async function send(where,input,op){
    const p=await preview(where,input,op);
    await h.api('send',{operationId:op,expectedRevision:p.revision,payload:{familyId:where.familyId,previewHash:p.previewHash,intent:{branchId:where.branchId,targetId,messageId:null,input}}});
    return p;
  }
  const changes=await h.api('compare-templates',{workId,from:v1,to:v2});
  assert.ok(changes.some(c=>c.key==='description'&&c.before==='使用石板记录。'&&c.after==='使用铜牌记录。'));
  if(kind==='group'){
    const {RoundHost}=await import('../group/round-host.mjs');
    const rounds=await RoundHost.open({directory:join(dir,'groups','rounds'),store:h.store,provider:{mode:'fixture',dispatch:()=>assert.fail()}});
    try{
      const g=await h.store.read(h.groupId);
      const rules={expectedRevision:g.revision,mode:'fixed',order:first.members.map(m=>m.id),muted:[],text:''};
      await rounds.start(g.id,{operationId:'pause-here',previousRoundId:null,rules,message:'整轮尚未发出'});
      const blocked={...root,point:null,title:'等待整轮',player:null,session:null,template:{memberId:targetId,versionId:v2}};
      await assert.rejects(h.write('fork',blocked),/ROUND_UNKNOWN/);
      h.workspace.groupRoundsEnabled=true;h.workspace.groupRoundStatus=gid=>rounds.view(gid);
      await assert.rejects(h.write('fork',blocked),/ROUND_PENDING/);
      await rounds.stop(g.id,{roundId:'pause-here'});
      // The owning round reader can now verify the terminal state.
      assert.ok((await h.api('inspect-fork',{expectedRevision:(await h.api('catalog')).revision,operationId:'terminal',payload:blocked})).template);
      // Keep the read-only status fixture available for the remainder of this test.
      const stopped=await rounds.view(g.id);h.workspace.groupRoundStatus=async()=>stopped;
    }finally{await rounds.close();}
  }
  const before=await send(root,'钟楼','adopt'),anchor=(await read(root)).messages.at(-1);
  await send(root,'锚点以后的字条','later');
  const parent=await read(root),parentPreview=await preview(root,'钟楼','parent-probe');
  const request={...root,point:anchor.id,title:'抄录本',player:null,session:null,template:{memberId:targetId,versionId:v2}};
  const revision=(await h.api('catalog')).revision,cmd={expectedRevision:revision,operationId:'switch',payload:request};
  const inspect=await h.api('inspect-fork',cmd);
  assert.deepEqual(inspect.template,{matches:[{from:1,to:0},{from:0,to:2}],reset:[1],retired:[2]});
  const result=await h.api('fork',cmd);assert.deepEqual(await h.api('fork',cmd),result);
  const child={familyId:root.familyId,branchId:result.branchId};
  assert.deepEqual((await read(child)).messages,parent.messages.slice(0,first.messages.length+2));
  const next=await preview(child,'钟楼','new');
  assert.match(next.messages[0].content,/使用铜牌记录/);assert.doesNotMatch(JSON.stringify(next.messages),/锚点以后的字条/);
  assert.deepEqual(next.entries.map(e=>e.status),['cooldown-wait','triggered','once-used']);
  assert.equal(next.entries[0].keepAnchor,anchor.id);assert.equal(next.entries[2].messageId,anchor.id);
  assert.match(next.messages[0].content,kind==='group'?/公共记录室/:/新钟楼/);
  if(kind==='group'){
    const peer=first.members.find(m=>m.id!==targetId);
    const other=await preview(child,'钟楼','peer',null,peer.id);
    assert.match(other.messages[0].content,/使用石板记录/);assert.doesNotMatch(other.messages[0].content,/使用铜牌记录/);
    assert.equal(other.entries[0].status,'triggered');
  }
  const precise=await preview(child,null,'exact',anchor.id);
  assert.deepEqual(precise.messages,before.messages);assert.equal(precise.requestHash,before.requestHash);
  await h.api('send',{operationId:'exact',expectedRevision:precise.revision,payload:{familyId:child.familyId,previewHash:precise.previewHash,intent:{branchId:child.branchId,targetId,messageId:anchor.id,input:null}}});
  assert.deepEqual((await read(root)).messages,parent.messages);
  assert.equal((await preview(root,'钟楼','parent-probe')).previewHash,parentPreview.previewHash);
  const priorPoint=await h.write('fork',{...child,point:anchor.id,title:'换版之前',player:null,session:null});
  assert.match((await preview(priorPoint,'钟楼','old')).messages[0].content,/使用石板记录/);
  await assert.rejects(h.write('fork',{...request,template:{memberId:targetId,versionId:null}}),/SEALED_REQUIRED/);
  await assert.rejects(h.write('fork',{...request,template:{memberId:'other',versionId:v2}}),/ANCHOR_HIDDEN/);
  await send(child,'钟楼','adopt-new');
  const backResult=await h.write('fork',{...child,template:{memberId:targetId,versionId:v1},point:null,title:'再选第一版',player:null,session:null});
  const back={familyId:root.familyId,branchId:backResult.branchId},returned=await preview(back,'钟楼','back');
  assert.equal(returned.entries[0].status,'once-used');assert.equal(returned.entries[2].status,'triggered');
  await h.close();h=await startTemplatePreview(dir);
  assert.equal((await preview(back,'钟楼','back')).previewHash,returned.previewHash);
  assert.deepEqual((await preview(child,null,'exact-readable',anchor.id)).messages,before.messages);
  assert.deepEqual((await read(root)).messages,parent.messages);
});

test('switching role books retains the anchored session and explicit player readers',async t=>{
  const dir=await mkdtemp(join(process.env.CHARACTER_R5A_TEST_ROOT??tmpdir(),'bindings-'));
  const h=await startTemplatePreview(dir);t.after(()=>h.close());
  const catalog=await h.api('catalog'),role=catalog.roles.find(r=>r.id===h.workId);
  const pin=v=>({bookId:v.bookId,bookVersionId:v.bookVersionId,hash:v.hash});
  const byName=name=>pin(catalog.books.find(b=>b.title===name).versions[0]);
  const playerBook=byName('行旅笔记'),publicBook=byName('渡口手册');
  const {versionId}=await h.write('role',{workId:role.id,workRevision:role.revision,refs:[publicBook]});
  const info=await h.api('source',{kind:'group',sourceId:h.groupId}),[a,b]=info.members;
  const root=await h.write('open',{kind:'group',sourceId:h.groupId,sourceHash:info.sourceHash,
    player:[{ref:playerBook,readers:[b.id]}],session:[]});
  const changed=await h.write('fork',{...root,point:null,title:'角色绑定',player:null,session:null,template:{memberId:a.id,versionId}});
  const location={familyId:root.familyId,branchId:changed.branchId};
  const bindings=async(loc,member)=>(await h.api('view',{...loc,targetId:member.id})).bindings.map(r=>r.selected);
  assert.deepEqual(await bindings(location,a),[publicBook]);assert.deepEqual(await bindings(root,a),[]);
  assert.deepEqual(await bindings(location,b),[playerBook]);assert.deepEqual(await bindings(root,b),[playerBook]);
  assert.ok((await h.api('compare-templates',{workId:h.workId,from:h.v1,to:versionId})).some(c=>c.section==='独立角色书'));
});

test('ambiguous rules and changed book settings reset even if entry bytes match',()=>{
  const entry={content:'same',enabled:true},cfg=entries=>({document:{character:{card:{spec:'chara_card_v3',data:{character_book:{entries}}}}}});
  const before=cfg([entry,entry]),member={id:'owner',binding:{reference:{workId:'role',versionId:'first'},configurationHash:digest(before)}};
  const args={member,before,after:cfg([entry]),versionId:'next',position:2,operationId:'op'};
  assert.deepEqual(planSwitch(args).report.reset,[0]);
  args.before=cfg([{...entry,id:4},{content:'different',id:4}]);args.after=cfg([{...entry,id:4}]);
  member.binding.configurationHash=digest(args.before);assert.deepEqual(planSwitch(args).report.reset,[0]);
  args.before=cfg([entry]);member.binding.configurationHash=digest(args.before);
  args.after=copy(args.before);args.after.document.character.card.data.character_book.scan_depth=8;
  assert.deepEqual(planSwitch(args).report.reset,[0]);
});
