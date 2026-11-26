import test from 'node:test';
import assert from 'node:assert/strict';
import {join} from 'node:path';
import {fixture} from './test-support.mjs';
import {RoundHost} from './round-host.mjs';
import {GroupFixtureProvider} from './fixture-provider.mjs';
import {GroupHost} from './host.mjs';

test('manual narration freezes its audience and source without creating a turn, world event or budget entry',async t=>{
  const f=await fixture(t,{narration:true}),start=await f.store.read(f.id);
  const provider=await GroupFixtureProvider.open(join(f.directory,'provider'));
  t.after(()=>provider.close());
  const calls=[];const transport={mode:'fixture',dispatch:r=>{calls.push(r);return provider.dispatch(r);},lookup:()=>assert.fail('unexpected lookup')};
  const rounds=await RoundHost.open({directory:join(f.directory,'rounds'),store:f.store,provider:transport});
  t.after(()=>rounds.close());
  const [player,a,b]=start.members;
  const write=async(op,content)=>{
    const v=await f.store.view(f.id),body={operationId:op,expectedRevision:v.revision,expectedNarrationRevision:v.narrationRevision,content};
    return [await rounds.narrate(f.id,body),body];
  };
  const [view,body]=await write('first','潮尺上有白线'),msg=view.messages.at(-1);
  assert.equal(msg.source.kind,'manual-narration');assert.equal(msg.source.reference,'first');
  assert.equal(msg.source.authorId,player.id);assert.ok(!view.members.some(m=>m.id===msg.speakerId));
  assert.deepEqual(await rounds.narrate(f.id,body),view);
  await assert.rejects(rounds.narrate(f.id,{...body,content:'改写'}),{code:'GROUP_OPERATION_COLLISION'});
  await assert.rejects(rounds.narrate(f.id,{...body,operationId:'stale'}),{code:'GROUP_REVISION_CONFLICT'});
  await assert.rejects(rounds.narrate(f.id,{...body,source:'model-result'}));
  const {notes,...unchanged}=await f.store.read(f.id),{notes:empty,...baseline}=start;
  assert.deepEqual(unchanged,baseline);assert.equal(start.worldInstanceRef,null);
  assert.deepEqual(await rounds.history(f.id),{revision:0,items:[],before:null});assert.deepEqual(calls,[]);
  const change=async(action,payload)=>f.store.change(f.id,action,{operationId:'do-'+action,expectedRevision:(await f.store.view(f.id)).revision,payload});
  await change('join',{reference:f.reference,modelId:'fixture'});const late=(await f.store.view(f.id)).members.at(-1);
  await change('leave',{memberId:b.id});const [during]=await write('absence','仅在席的成员看见方形标记');
  const hidden=during.messages.at(-1);
  await assert.rejects(f.store.memberView(f.id,b.id));await change('rejoin',{memberId:b.id});
  await write('back','灯光转为绿色');
  const all=await f.store.memberView(f.id,a.id),returned=await f.store.memberView(f.id,b.id),later=await f.store.memberView(f.id,late.id);
  const notesOf=v=>v.messages.filter(m=>m.source.kind==='manual-narration').map(m=>m.content);
  assert.deepEqual(notesOf(all),['潮尺上有白线',hidden.content,'灯光转为绿色']);assert.deepEqual(notesOf(returned),['潮尺上有白线','灯光转为绿色']);assert.deepEqual(notesOf(later),[hidden.content,'灯光转为绿色']);
  const preview=await f.store.preview(f.id,b.id);for(const v of [returned,preview]){
    assert.ok(!JSON.stringify(v).includes(hidden.id));assert.ok(!JSON.stringify(v).includes(hidden.content));
  }
  await new GroupHost(f.store,transport).send(f.id,{operationId:'manual',expectedRevision:(await f.store.view(f.id)).revision,payload:{targetId:b.id,input:'查看潮尺'}});
  assert.equal(calls.length,1);
  const material=calls[0].messages.map(m=>m.content);assert.ok(material.some(s=>s.includes(msg.speakerId)&&s.includes(msg.content)));
  assert.ok(!material.some(s=>s.includes(hidden.content)));await write('after','关闭记录册');
  const saved=await f.store.view(f.id);await f.reopen();assert.deepEqual(await f.store.view(f.id),saved);
  assert.deepEqual(saved.messages.map(m=>m.source.kind),['opening','manual-narration','manual-narration','manual-narration','player-input','model-result','manual-narration']);
});
test('pre-existing manual group rejects narrator writes; active rounds and pending sends lock narration',async t=>{
  const old=await fixture(t);
  const body={operationId:'note',expectedRevision:0,expectedNarrationRevision:0,content:'桌上有一张纸'};
  await assert.rejects(old.store.narrate(old.id,body),{code:'GROUP_NARRATION_UNAVAILABLE'});
  const f=await fixture(t,{narration:true}),view=await f.store.view(f.id);
  const rounds=await RoundHost.open({directory:join(f.directory,'rounds'),store:f.store,provider:{mode:'fixture',dispatch:()=>assert.fail()}});
  t.after(()=>rounds.close());const rule={mode:'fixed',expectedRevision:0,order:view.members.slice(1).map(m=>m.id),muted:[],text:''};
  await rounds.start(f.id,{operationId:'one',previousRoundId:null,rules:rule,message:'不应当作已发送'});
  await assert.rejects(rounds.narrate(f.id,body),{code:'GROUP_ROUND_ACTIVE'});await rounds.stop(f.id,{roundId:'one'});
  const archive=await rounds.history(f.id);assert.equal(archive.items[0].input,null);assert.equal(archive.items[0].status,'stopped');
  await f.store.prepare(f.id,{operationId:'request',expectedRevision:0,payload:{targetId:view.members[1].id,input:'玩家发言'}});
  await assert.rejects(rounds.narrate(f.id,{...body,expectedRevision:1}),{code:'GROUP_REQUEST_PENDING'});
});
