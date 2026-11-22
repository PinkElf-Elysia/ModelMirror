import {BookWorkspace} from '../book-library/store.mjs';
import {exportCard} from '../exchange.mjs';
import {source} from '../revisions/test-support.mjs';
import {copy,digest} from '../revisions/contracts.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {renderVariables,variableView,setVariable,forkVariables,variablePolicy} from './variables.mjs';
import {render} from './date-choice.mjs';
import {startVariablesPreview} from './variables-preview.mjs';

test('missing, empty and nested conditions, skipped reads and stable choices',()=>{
  const v={shared:{值:'',天气:'晴'},private:{信号:'白桦'}};
  const expand=s=>renderVariables(s,'description',v,(token,offset)=>render(token,'description','s','2030-01-01',offset));
  const lookup=expand('甲{{var::private::未知}}乙{{var::shared::值}}');
  assert.equal(lookup.text,'甲乙');assert.deepEqual(lookup.items.map(i=>i.reason),['missing','read']);
  const cond=expand('{{if::shared::值::}}甲{{if::shared::未知::}}错误{{else}}乙{{/if}}{{else}}{{var::private::信号}}{{/if}}');
  assert.equal(cond.text,'甲乙');assert.equal(cond.items.length,2);
  assert.deepEqual(cond.items.map(i=>i.observed),['',null]);
  const start='{{if::shared::值}}',end='{{/if}}';
  assert.equal(expand(start.repeat(4)+'内'+end.repeat(4)).text,'内');
  assert.throws(()=>expand(start.repeat(5)+'内'+end.repeat(5)),/NESTING_LIMIT/);
  for(const bad of [start,start+'{{else}}{{else}}'+end,'{{else}}',end,'{{var::member::键}}','{{var::shared::值'])assert.throws(()=>expand(bad),/SYNTAX/);
  assert.throws(()=>expand('{{var::shared::值}}'.repeat(129)),/MACRO_FIELD_LIMIT/);
  assert.equal(expand('{{other::{{var::private::信号}}}}').text,'{{other::{{var::private::信号}}}}');
  const choice='{{pick::青::白}}',s=start+choice+end;
  assert.deepEqual(expand(s).items.at(-1),render(s,'description','s','2030-01-01').items.at(-1));
});

test('writes are explicit and bounded; private and shared names never shadow one another',()=>{
  const track=forkVariables(null,0,true,true);
  const put=(scope,memberId,key,value)=>setVariable(track,0,['a','b'],{scope,memberId,key,value});
  put('shared',null,'班号','早');put('private','a','班号','甲');put('private','b','班号','乙');
  assert.deepEqual(variableView(track,'a'),{shared:{班号:'早'},private:{班号:'甲'}});
  assert.throws(()=>put('private','x','班号','越界'),/VARIABLE_SCOPE/);
  for(const k of ['__proto__','a.b',''])assert.throws(()=>put('shared',null,k,'x'),/VARIABLE_INPUT/);
  for(const v of ['{{date}}','@@keep','<BOT>',{},'x'.repeat(257)])assert.throws(()=>put('shared',null,'文本',v),/VARIABLE_INPUT/);
  put('private','a','班号',null);assert.deepEqual(variableView(track,'a').private,{});
  assert.equal(variableView(track,'b').private.班号,'乙');
});

test('group HTTP projection, historical forks, restart and exact generation preserve original consulted values',async t=>{
  const root=process.env.CHARACTER_R5C_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const dir=await mkdtemp(join(root,'vars-'));
  let h=await startVariablesPreview(dir);t.after(()=>h.close());
  const {familyId,branchId}=h,loc={familyId,branchId};
  const view=(bid=branchId,targetId=null)=>h.api('view',{familyId,branchId:bid,targetId});
  const shelf=()=>readFile(join(dir,'studio/character/independent-books/library/records/shelf.json'));
  const baseline=await view(),[a,b]=baseline.members.map(m=>m.id);
  const preview=(op,targetId,bid=branchId,messageId=null)=>h.api('preview',{familyId,operationId:op,intent:{branchId:bid,targetId,messageId,input:messageId?null:'交班'}});
  function send(op,p,messageId=null){
    return h.api('send',{operationId:op,expectedRevision:p.revision,payload:{familyId,intent:{branchId,targetId:a,messageId,input:messageId?null:'交班'},previewHash:p.previewHash}});
  }
  const set=(scope,memberId,key,value,bid=branchId)=>h.write('set-variable',{familyId,branchId:bid,scope,memberId,key,value});
  const oldBytes=await shelf(),original=await preview('turn',a),other=await preview('other',b);
  for(const [p,yes,no] of [[original,'白桦','青竹'],[other,'青竹','白桦']]){
    assert.equal(p.macros.policy,variablePolicy);assert.match(p.messages[0].content,/晴。在桥头交班/);
    assert.ok(p.messages[0].content.includes(yes));assert.ok(!JSON.stringify(p).includes(no));
  }
  assert.deepEqual(await shelf(),oldBytes);
  const originalReply=await send('turn',original);
  const reply=(await view()).messages.at(-1).id;
  const stale=await preview('stale',a);
  await set('shared',null,'天气','雨');await set('private',a,'信号','灰羽');
  await assert.rejects(send('stale',stale),/REVISION_CONFLICT/);
  const fork=(title,point)=>h.write('fork',{...loc,point,title,session:null,player:null});
  const historical=await fork('回复当时',reply),latest=await fork('当前末尾',null),early=await fork('开场',baseline.messages[0].id);
  assert.equal((await view(early.branchId)).variables,undefined);
  assert.deepEqual((await view(historical.branchId,a)).variables,{shared:{天气:'晴'},private:{信号:'白桦'}});
  assert.deepEqual((await view(latest.branchId,a)).variables,{shared:{天气:'雨'},private:{信号:'灰羽'}});
  await set('private',a,'信号','子枝',latest.branchId);
  assert.deepEqual((await view()).variables,{shared:{天气:'雨'},private:{信号:'灰羽'}});
  assert.ok(!JSON.stringify(await view(branchId,b)).includes('灰羽'));
  const now=await preview('now',a);assert.match(now.messages[0].content,/在亭内交班/);assert.ok(!JSON.stringify(now.macros).includes('白桦'));
  await h.close();h=await startVariablesPreview(dir);
  const persisted=(await view()).requests.find(r=>r.id===originalReply.requestId);
  assert.deepEqual(persisted.macros,original.macros);
  const exact=await preview('replay',a,branchId,reply);
  assert.deepEqual(exact.messages,original.messages);assert.deepEqual(exact.macros,original.macros);
  const rerun=await send('replay',exact,reply);assert.deepEqual(await send('replay',exact,reply),rerun);
  const row=JSON.parse(await shelf()).data.families[familyId];
  assert.deepEqual(row.plans[rerun.requestId].macros,original.macros);
  assert.deepEqual(row.f.requests[rerun.requestId].context,row.f.requests[originalReply.requestId].context);
  assert.deepEqual((await preview('child-exact',a,historical.branchId,reply)).macros,original.macros);
  await assert.rejects(set('private','other-family','信号','x'),/VARIABLE_SCOPE/);
});

test('single-card template remains portable; missing values and excessive depth are checked before dispatch',async t=>{
  const root=process.env.CHARACTER_R5C_TEST_ROOT??tmpdir();await mkdir(root,{recursive:true});
  const s=source(),card=s.cards.actor.document.character.card;
  const block='{{if::private::旗}}',end='{{/if}}';
  card.data.description='{{char}}守桥。{{var::private::旗}}'+block.repeat(4)+'已挂旗'+end.repeat(4);
  for(const b of [s.current,s.initial,...s.messages.map(m=>m.boundary)])b.members[1].binding={reference:{workId:'role',versionId:'v1'}};
  const raw=exportCard(card,{format:'json'}).bytes;
  async function deskFor(s){
    delete s.sourceHash;s.sourceHash=digest(s);
    const d=await BookWorkspace.open({directory:await mkdtemp(join(root,'single-')),sources:{read:async()=>copy(s),verify:async()=>copy(s)}});
    t.after(()=>d.close());return d;
  }
  const open=(d,op,rev,policy)=>d.command('open',{operationId:op,expectedRevision:rev,payload:{kind:s.kind,sourceId:s.id,sourceHash:s.sourceHash,session:[],player:[],...policy}});
  const intent=loc=>({branchId:loc.branchId,targetId:'actor',input:'字面 {{var::private::旗}}',messageId:null});
  const d=await deskFor(s),old=await open(d,'old',0,{}),current=await open(d,'new',1,{variablePolicy});
  const legacy=await d.preview(old.familyId,intent(old),'old-preview');
  assert.equal(legacy.macros,undefined);assert.ok(legacy.messages[0].content.includes(block));
  const missing=await d.preview(current.familyId,intent(current),'missing');
  assert.ok(missing.macros.items.some(i=>i.reason==='missing'));assert.ok(!missing.messages[0].content.includes('已挂旗'));
  assert.ok(missing.messages.some(m=>m.content===intent(current).input));
  const command={operationId:'set',expectedRevision:2,payload:{...current,scope:'private',memberId:'actor',key:'旗',value:'银色'}};
  await d.command('set-variable',command);await d.command('set-variable',command);
  await assert.rejects(d.command('set-variable',{...command,payload:{...command.payload,value:'新值'}}),/COLLISION/);
  const after=await d.preview(current.familyId,intent(current),'new-preview');
  assert.match(after.messages[0].content,/银色已挂旗/);
  assert.deepEqual(exportCard(card,{format:'json'}).bytes,raw);assert.deepEqual((await d.preview(old.familyId,intent(old),'old-preview')).messages,legacy.messages);
  const bad=copy(s);bad.cards.actor.document.character.card.data.description=block.repeat(5)+'文字'+end.repeat(5);
  const badDesk=await deskFor(bad);
  const loc=await badDesk.command('open',{operationId:'invalid-branch',expectedRevision:0,payload:{kind:bad.kind,sourceId:bad.id,sourceHash:bad.sourceHash,session:[],player:[],variablePolicy}});
  await assert.rejects(badDesk.preview(loc.familyId,intent(loc),'invalid'),/VARIABLE_NESTING_LIMIT/);
  await assert.rejects(badDesk.command('send',{operationId:'invalid',expectedRevision:1,payload:{familyId:loc.familyId,intent:intent(loc),previewHash:'untrusted'}}),/VARIABLE_NESTING_LIMIT/);
  assert.equal(badDesk.fixture.dispatchCount,0);assert.equal((await badDesk.list()).revision,1);
});
