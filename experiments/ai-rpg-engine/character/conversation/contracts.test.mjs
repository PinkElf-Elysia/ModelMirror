import test from 'node:test';import assert from 'node:assert/strict';
import {FORMAT,idFor,replay,appendEvent,projectMember,intention} from './contracts.mjs';
const ref={workId:'work-'+ 'a'.repeat(64),versionId:'version-'+ 'b'.repeat(64)};
function fixture(){let record={format:FORMAT,id:idFor('conversation','create'),mode:'fixture',runtimeHash:'c'.repeat(64),events:[intention('create',{operationId:'create',expectedRevision:null,payload:{player:{id:null,name:'同名'}}})]};
 const run=(action,id,payload)=>{const result=appendEvent(record,intention(action,{operationId:id,expectedRevision:record.events.length-1,payload}));record=result.record;return result.receipt;};
 const a=run('join','a',{reference:ref,configurationHash:'d'.repeat(64),name:'同名'}).memberId;
 const p=replay(record).state.members[0].id;
 const say=(id,text,extra={})=>run('append',id,{speakerId:p,content:text,visibility:{kind:'public'},addressedTo:[],source:{kind:'player-input',reference:null},...extra});
 return {run,say,a,p,get record(){return record;},get state(){return replay(record).state;}};}
test('presence intervals exclude before join and absence; rejoin retains old participation',()=>{
 const f=fixture();f.say('one','past');const b=f.run('join','b',{reference:ref,configurationHash:'d'.repeat(64),name:'同名'}).memberId;
 f.say('two','together');f.run('leave','leave',{memberId:b});f.say('three','absence');assert.throws(()=>projectMember(f.state,b),{code:'CONVERSATION_MEMBER_INACTIVE'});f.run('rejoin','return',{memberId:b});f.say('four','returned');
 assert.deepEqual(projectMember(f.state,b).messages.map(m=>m.content),['together','returned']);assert.notEqual(f.a,b);
});
test('addressing is not visibility; private content and references never enter sibling view',()=>{
 const f=fixture(),b=f.run('join','b',{reference:ref,configurationHash:'d'.repeat(64),name:'同名'}).memberId;
 f.say('public','addressed publicly',{addressedTo:[f.a]});f.say('private','SECRET_BODY',{visibility:{kind:'members',memberIds:[f.p,f.a]},source:{kind:'player-input',reference:'SECRET_REFERENCE'}});
 f.run('private','note',{ownerId:f.a,content:'PRIVATE_NOTE',source:{kind:'manual',reference:'PRIVATE_SOURCE'}});
 const view=projectMember(f.state,b);assert.equal(view.messages.length,1);assert.ok(!JSON.stringify(view).includes('SECRET'));assert.ok(!JSON.stringify(view).includes('PRIVATE_'));assert.equal(view.privateRecords.length,0);assert.ok(!('binding' in view.participants[0]));assert.ok(!('revision' in view));
});
test('stable receipts, strict duplicate intent, stale revision and source/identity rejection',()=>{
 const f=fixture();const r=f.say('one','hello'),e=f.record.events.at(-1),again=appendEvent(f.record,e);assert.equal(again.changed,false);assert.deepEqual(again.receipt,r);
 assert.throws(()=>appendEvent(f.record,{...e,payload:{...e.payload,content:'changed'}}),{code:'CONVERSATION_OPERATION_COLLISION'});
 assert.throws(()=>appendEvent(f.record,{...e,operationId:'new'}),{code:'CONVERSATION_REVISION_CONFLICT'});
 assert.throws(()=>f.say('spoof','x',{source:{kind:'model-result',reference:null}}),{code:'CONVERSATION_SOURCE_UNSUPPORTED'});
 assert.throws(()=>f.say('foreign','x',{speakerId:idFor('member','foreign')}),{code:'CONVERSATION_MEMBER_NOT_FOUND'});
 const corrupt=structuredClone(f.record);corrupt.format='future';assert.throws(()=>replay(corrupt),{code:'CONVERSATION_FORMAT_UNSUPPORTED'});
});
test('empty participation interval does not retroactively grant history; opening only once',()=>{
 const f=fixture();f.say('old','old');f.run('leave','leave',{memberId:f.a});f.run('rejoin','join',{memberId:f.a});
 f.run('opening','opening',{speakerId:f.a,content:'hi',openingIndex:0});assert.throws(()=>f.run('opening','again',{speakerId:f.a,content:'hi',openingIndex:0}),{code:'CONVERSATION_OPENING_EXISTS'});
 assert.equal(projectMember(f.state,f.a).messages.length,2);
});

test('malformed events, limits and extra permissions fail before state changes',()=>{
 const f=fixture();const before=structuredClone(f.record);
 for(const value of [null,{},[]])assert.throws(()=>replay({...f.record,events:[value]}));
 assert.throws(()=>f.say('large','x'.repeat(200001)),{code:'CONVERSATION_TEXT_INVALID'});
 assert.throws(()=>f.say('permissions','x',{permissions:['tool']}),{code:'AUTHORING_UNKNOWN_FIELD'});
 assert.throws(()=>f.say('private-empty','x',{visibility:{kind:'members',memberIds:[]}}),{code:'CONVERSATION_RECIPIENT_INVALID'});
 assert.deepEqual(f.record,before);
 f.say('reply','fixture reply',{speakerId:f.a,source:{kind:'fixture',reference:null}});
 assert.throws(()=>f.run('opening','late-opening',{speakerId:f.a,content:'hi',openingIndex:0}),{code:'CONVERSATION_OPENING_EXISTS'});
});

test('member projection hides internal intervals but retains visible historical speaker identity',()=>{
 const f=fixture(),b=f.run('join','b',{reference:ref,configurationHash:'d'.repeat(64),name:'Reader'}).memberId;
 f.say('spoken','old speech',{speakerId:f.a,source:{kind:'fixture',reference:null}});f.run('leave','leave',{memberId:f.a});
 const view=projectMember(f.state,b);assert.equal(view.messages[0].speakerId,f.a);assert.ok(view.participants.some(m=>m.id===f.a));assert.ok(!('intervals' in view.member));assert.ok(!('sequence' in view.messages[0]));
});
