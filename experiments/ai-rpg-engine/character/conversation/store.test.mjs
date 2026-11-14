import test from 'node:test';import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';import {join} from 'node:path';
import {fixture} from './test-support.mjs';import {digest,idFor} from './contracts.mjs';import {blankDocument} from '../../authoring/contracts.mjs';
test('same-name instances, frozen publication, opening, private data and restart',async t=>{
 const f=await fixture(t),a=await f.join('a'),b=await f.join('b');assert.notEqual(a,b);
 await f.op('appendOpening','opening',{speakerId:a,openingIndex:1});await f.op('addPrivate','secret',{ownerId:a,content:'PRIVATE_A',source:{kind:'manual',reference:null}});await f.say('hello','消息');
 const before=await f.host.readOwner(f.created.conversationId);f.doc.character.card.data.name='新名字';f.doc.metadata.name='新名字';f.doc.character.card.data.first_mes='新开场';
 await f.works.save(f.reference.workId,{operationId:'edit',expectedRevision:1,document:f.doc});await f.works.publish(f.reference.workId,{operationId:'v2',expectedRevision:2});
 await f.reopen();await assert.rejects(()=>f.host.readOwner(before.id),{code:'CONVERSATION_RECOVERY_REQUIRED'});await f.host.recover();assert.deepEqual(await f.host.readOwner(before.id),before);
 const view=await f.host.readMember(before.id,b);assert.ok(!JSON.stringify(view).includes('PRIVATE_A'));assert.ok(!JSON.stringify(view).includes('EXAMPLE_NOT_HISTORY'));
 assert.equal((await f.host.readMember(before.id,a)).privateRecords.length,1);assert.equal(before.messages[0].content,'另一开场');
 await assert.rejects(()=>f.op('appendOpening','another',{speakerId:a,openingIndex:1}),{code:'CONVERSATION_OPENING_EXISTS'});
 const config=await f.works.exportConfiguration(f.reference.workId,f.reference.versionId);assert.ok(!JSON.stringify(config).includes('PRIVATE_A'));
});
test('exact duplicate returns original receipt, stale concurrent write and foreign member denied',async t=>{
 const f=await fixture(t);await f.join('a');const state=await f.host.readOwner(f.created.conversationId),input={operationId:'one',expectedRevision:state.revision,payload:{speakerId:f.created.memberId,content:'one',visibility:{kind:'public'},addressedTo:[],source:{kind:'player-input',reference:null}}};
 const results=await Promise.allSettled([f.host.append(state.id,input),f.host.append(state.id,{...input,operationId:'two'})]);assert.equal(results[0].status,'fulfilled');assert.equal(results[1].reason.code,'CONVERSATION_REVISION_CONFLICT');
 await f.say('next','later');assert.deepEqual(await f.host.append(state.id,input),results[0].value);
 await assert.rejects(()=>f.host.append(state.id,{...input,payload:{...input.payload,content:'changed'}}),{code:'CONVERSATION_OPERATION_COLLISION'});
 const other=await f.host.create({operationId:'other',expectedRevision:null,payload:{player:{id:null,name:'玩家'}}});await assert.rejects(()=>f.say('foreign','x',{speakerId:other.memberId}),{code:'CONVERSATION_MEMBER_NOT_FOUND'});
});
for(const phase of ['beforeCommit','afterCommit'])test('explicit recovery for '+phase+' never duplicates a message',async t=>{
 let armed=false;const f=await fixture(t,{fault:async p=>{if(armed&&p===phase){armed=false;throw Error('fixture failure');}}});await f.join('a');
 const s=await f.host.readOwner(f.created.conversationId),input={operationId:'fault',expectedRevision:s.revision,payload:{speakerId:f.created.memberId,content:'保留',visibility:{kind:'public'},addressedTo:[],source:{kind:'player-input',reference:null}}};armed=true;
 await assert.rejects(()=>f.host.append(s.id,input),{code:phase==='beforeCommit'?'OFFLINE_WRITE_FAILED':'OFFLINE_WRITE_UNKNOWN'});
 await assert.rejects(()=>f.host.readOwner(s.id),{code:'CONVERSATION_RECOVERY_REQUIRED'});await f.reopen();await f.host.recover(s.id);const after=await f.host.readOwner(s.id);assert.equal(after.messages.length,phase==='beforeCommit'?0:1);
 await f.host.append(s.id,input);assert.equal((await f.host.readOwner(s.id)).messages.length,1);
});
test('foundation mode rejects fixture/model messages; world and missing versions rejected',async t=>{
 const f=await fixture(t,{fixtureMode:false}),a=await f.join('a');
 for(const kind of ['fixture','model-result','world-event'])await assert.rejects(()=>f.say(kind,'x',{speakerId:a,source:{kind,reference:null}}),{code:'CONVERSATION_SOURCE_UNSUPPORTED'});
 const d=blankDocument();d.metadata.name='world fixture';d.metadata.audience='male';d.sources.system='world';const w=await f.works.create({operationId:'world',expectedRevision:null,document:d}),v=await f.works.publish(w.workId,{operationId:'worldv',expectedRevision:0});
 await assert.rejects(()=>f.op('join','world',{reference:{workId:w.workId,versionId:v.result.versionId}}),{code:'CONVERSATION_CHARACTER_REQUIRED'});
 await assert.rejects(()=>f.op('join','missing',{reference:{...f.reference,versionId:'version-'+ '0'.repeat(64)}}));
});
test('persisted participation and private visibility survive restart',async t=>{
 const f=await fixture(t),a=await f.join('a');await f.say('before','before');const b=await f.join('b');await f.say('during','during');await f.op('leave','leave',{memberId:b});await f.say('away','away');await f.op('rejoin','back',{memberId:b});await f.say('after','after');
 await f.say('private','SECRET',{visibility:{kind:'members',memberIds:[f.created.memberId,a]},source:{kind:'player-input',reference:'SECRET_SOURCE'}});
 await f.reopen();await f.host.recover();const view=await f.host.readMember(f.created.conversationId,b);assert.deepEqual(view.messages.map(m=>m.content),['during','after']);assert.ok(!JSON.stringify(view).includes('SECRET'));
});
test('tampered runtime and unknown format rejected even with a valid storage checksum',async t=>{
 const f=await fixture(t);const id=f.created.conversationId,path=join(f.directory,'conversations','records','records',id+'.json');await f.host.close();
 const original=JSON.parse(await readFile(path));for(const patch of [{runtimeHash:'0'.repeat(64)},{format:'future'}]){
  const envelope=structuredClone(original);Object.assign(envelope.data,patch);envelope.sha256=digest(envelope.data);await writeFile(path,JSON.stringify(envelope));
  await f.reopen();await f.host.recover();await assert.rejects(()=>f.host.readOwner(id),{code:patch.runtimeHash?'CONVERSATION_RUNTIME_MISMATCH':'CONVERSATION_FORMAT_UNSUPPORTED'});await f.host.close();
 }
});

test('unknown write blocks sibling operations until exact-target reconciliation',async t=>{
 let armed=false;const f=await fixture(t,{fault:async phase=>{if(armed&&phase==='afterCommit'){armed=false;throw Error('commit acknowledgement lost');}}});
 const other=await f.host.create({operationId:'other',expectedRevision:null,payload:{player:{id:null,name:'Player'}}});armed=true;
 await assert.rejects(()=>f.say('pending','retained'),{code:'OFFLINE_WRITE_UNKNOWN'});
 await assert.rejects(()=>f.host.recover(other.conversationId),{code:'OFFLINE_RECOVERY_TARGET_CONFLICT'});
 await assert.rejects(()=>f.host.readOwner(other.conversationId),{code:'CONVERSATION_RECOVERY_REQUIRED'});
 assert.equal((await f.host.recover(f.created.conversationId)).status,'committed');
 assert.equal((await f.host.readOwner(other.conversationId)).messages.length,0);assert.equal((await f.host.readOwner(f.created.conversationId)).messages.length,1);
});
