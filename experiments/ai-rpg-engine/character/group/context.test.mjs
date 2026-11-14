import test from 'node:test';import assert from 'node:assert/strict';import {fixture} from './test-support.mjs';import {assembleGroup} from './context.mjs';
test('V3 group opening uses nickname/user and is shared once by three initial members',async t=>{
 const f=await fixture(t),doc=structuredClone(f.doc);doc.character.card.spec='chara_card_v3';doc.character.card.spec_version='3.0';doc.character.card.data.nickname='昵称';doc.character.card.data.group_only_greetings=['{{CHAR}} 向 <USER> 和大家问好'];
 const w=await f.works.create({operationId:'v3-work',expectedRevision:null,document:doc}),v=await f.works.publish(w.workId,{operationId:'v3-publish',expectedRevision:0}),reference={workId:w.workId,versionId:v.result.versionId};
 const input=structuredClone(f.input);input.operationId='v3-group';input.payload.members=[{reference,modelId:'fixture'},...input.payload.members];input.payload.opening={memberIndex:0,kind:'group',index:0};const c=await f.store.create(input),s=await f.store.read(c.id);assert.equal(s.messages[0].content,'昵称 向 测试玩家 和大家问好');for(const m of s.members)assert.equal((await f.store.memberView(c.id,m.id)).messages.length,1);
 await f.reopen();assert.equal((await f.store.read(c.id)).messages.length,1);
});
test('book scans only visible history, preserves positions and excludes metadata/examples from triggers',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),card=structuredClone(f.doc.character.card);card.data.creator_notes='AUTHOR_TRIGGER';card.data.mes_example='EXAMPLE_TRIGGER';card.data.character_book={extensions:{},entries:[
 {keys:['你好'],content:'BEFORE_DEFINITION',enabled:true,insertion_order:0,extensions:{},position:'before_char'},
 {keys:['AUTHOR_TRIGGER','EXAMPLE_TRIGGER'],content:'MUST_NOT_TRIGGER',enabled:true,insertion_order:1,extensions:{},position:'after_char'}]};
 const a=assembleGroup(s,s.members[1].id,card),system=a.messages[0].content;assert.ok(system.indexOf('BEFORE_DEFINITION')<system.indexOf(card.data.description));assert.doesNotMatch(system,/MUST_NOT_TRIGGER|AUTHOR_TRIGGER/);assert.equal(a.trace.book[1].status,'not-triggered');
});
test('context trims examples before visible history and fails if essential definition cannot fit',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),card=structuredClone(f.doc.character.card);card.data.mes_example='x'.repeat(40000);const a=assembleGroup(s,s.members[1].id,card);assert.equal(a.trace.examples,'capacity-excluded');assert.equal(a.trace.omittedMessageIds.length,0);card.data.description='x'.repeat(40000);assert.throws(()=>assembleGroup(s,s.members[1].id,card),{code:'GROUP_CONTEXT_CAPACITY'});
});
import {GroupHost} from './host.mjs';
import {GroupFixtureProvider} from './fixture-provider.mjs';
import {join} from 'node:path';
test('snapshot identity preserves own participation facts without exposing absence contents',async t=>{
 const f=await fixture(t),provider=await GroupFixtureProvider.open(join(f.directory,'provider'));t.after(()=>provider.close());const host=new GroupHost(f.store,provider),s=await f.store.read(f.id),target=s.members[1].id,other=s.members[2].id;
 const card=structuredClone(f.doc.character.card);card.data.description='角色来自事件发生前的原作快照。';card.data.character_book={extensions:{},entries:[{keys:['ABSENCE_SECRET'],content:'HIDDEN_BOOK_RESULT',enabled:true,insertion_order:0,extensions:{}}]};
 const initial=assembleGroup(s,target,card);assert.deepEqual(initial.trace.participation,{currentlyPresent:true,hasRejoined:false});
 await f.store.change(f.id,'leave',{operationId:'out',expectedRevision:s.revision,payload:{memberId:target}});
 await assert.rejects(f.store.preview(f.id,target),{code:'CONVERSATION_MEMBER_INACTIVE'});
 let current=await f.store.read(f.id);await host.send(f.id,{operationId:'while-away',expectedRevision:current.revision,payload:{targetId:other,input:'ABSENCE_SECRET'}});
 current=await f.store.read(f.id);await f.store.change(f.id,'private',{operationId:'private-other',expectedRevision:current.revision,payload:{ownerId:other,content:'PRIVATE_SECRET'}});
 current=await f.store.read(f.id);await f.store.change(f.id,'rejoin',{operationId:'back',expectedRevision:current.revision,payload:{memberId:target}});
 current=await f.store.read(f.id);const before=JSON.stringify(current),a=assembleGroup(current,target,card);
 assert.equal(JSON.stringify(current),before);assert.deepEqual(a.trace.participation,{currentlyPresent:true,hasRejoined:true});
 assert.doesNotMatch(JSON.stringify(a),/ABSENCE_SECRET|PRIVATE_SECRET|HIDDEN_BOOK_RESULT/);
 assert.match(a.messages[0].content,/快照限定原作知识，不冻结本群新发生的互动/);assert.match(a.messages[0].content,/不能据此否认宿主记录/);
 const envelope=JSON.parse(a.messages[0].content.split('\n\n').find(x=>x.startsWith('{"target"')));assert.equal(envelope.target.id,target);assert.deepEqual(envelope.participation,a.trace.participation);assert.ok(!Object.hasOwn(envelope,'intervals'));
 assert.deepEqual(a.trace.visibleMessageIds,[s.messages[0].id]);assert.equal(a.trace.book[0].status,'not-triggered');
});
test('other member proposal remains attributed quoted history, never a host-confirmed action',async t=>{
 const f=await fixture(t),s=await f.store.read(f.id),other=s.members[2],proposal='我愿意看守门口，但还没开始。';
 s.messages.push({id:'message-'+'d'.repeat(64),sequence:2,speakerId:other.id,content:proposal,source:{kind:'fixture',reference:null},visibility:{kind:'public'},addressedTo:[]});
 const before=structuredClone(s),a=assembleGroup(s,s.members[1].id,f.doc.character.card);
 const quote=a.messages.map(m=>{try{return JSON.parse(m.content);}catch{return null;}}).find(m=>m?.text===proposal);
 assert.equal(quote.speaker.id,other.id);assert.equal(quote.text,proposal);assert.deepEqual(s,before);
 assert.match(a.messages[0].content,/只表示提议或意图，不等于已执行/);assert.match(a.messages[0].content,/保留条件语气/);assert.match(a.messages[0].content,/不沿用先前回复中未经支持的代写行动/);
 assert.equal(a.trace.narrativePolicy,'group-participation-and-actions/1');
});
