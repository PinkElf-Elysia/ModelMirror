import test from 'node:test';import assert from 'node:assert/strict';
import {fixture} from './test-support.mjs';import {evidenceCases,checkFixtureInput} from './evidence-fixtures.mjs';
import {assembleGroup} from './context.mjs';import {GroupHost} from './host.mjs';import {digest,requestBody,idFor} from './contracts.mjs';
async function setup(t,item){
 const f=await fixture(t),doc=structuredClone(f.doc);Object.assign(doc.character.card.data,{name:'中性观察员（测试夹具）',description:'原创中性角色，仅用于离线消息证据测试。',first_mes:item.text,mes_example:''});doc.metadata.name=doc.character.card.data.name;
 const w=await f.works.create({operationId:'evidence-card',expectedRevision:null,document:doc}),v=await f.works.publish(w.workId,{operationId:'evidence-version',expectedRevision:0}),reference={workId:w.workId,versionId:v.result.versionId};
 const created=await f.store.create({operationId:'evidence-group',expectedRevision:null,payload:{...f.input.payload,members:[{reference,modelId:'fixture'},{reference,modelId:'fixture'}],opening:{memberIndex:1,kind:'ordinary',index:0}}});
 const state=await f.store.read(created.id);return {f,doc,id:created.id,state,targetId:state.members[1].id,sourceMessageId:state.messages[0].id,sourceSpeakerId:state.members[2].id,item};
}
const gate=(s,state,context)=>checkFixtureInput({fixture:s.item,state,context,sourceMessageId:s.sourceMessageId,sourceSpeakerId:s.sourceSpeakerId});
for(const [name,item] of Object.entries(evidenceCases))test('fixed evidence source and speaker reach persisted request exactly: '+name,async t=>{
 const s=await setup(t,item);await s.f.store.prepare(s.id,{operationId:'question',expectedRevision:s.state.revision,payload:{targetId:s.targetId,input:item.question}});
 const state=await s.f.store.read(s.id),r=state.requests[0],result=gate(s,state,r);
 assert.equal(result.ready,true);assert.equal(result.scope,'input-only');assert.ok(!('passed' in result));
 assert.ok(!JSON.stringify(r.messages).includes(item.expected));assert.equal(state.messages[0].source.kind,'opening');assert.equal(state.messages.filter(m=>m.source.kind==='model-result').length,0);
 const original=structuredClone(state);await s.f.reopen();assert.deepEqual(await s.f.store.read(s.id),original);
});
test('ambiguous, changed or misattributed source cannot stand in for pure proposal',async t=>{
 const s=await setup(t,evidenceCases.proposalOnly),a=assembleGroup(s.state,s.targetId,s.doc.character.card);
 const changed=structuredClone(s.state);changed.messages[0].content=evidenceCases.ambiguousAction.text;assert.throws(()=>gate(s,changed,a),{code:'FIXTURE_SOURCE_CHANGED'});
 const wrong=structuredClone(a),entry=wrong.messages.find(m=>m.role==='user'&&JSON.parse(m.content).speaker);const v=JSON.parse(entry.content);v.speaker.id=s.targetId;entry.content=JSON.stringify(v);assert.throws(()=>gate(s,s.state,wrong),{code:'FIXTURE_WIRE_ATTRIBUTION_CHANGED'});
 assert.throws(()=>checkFixtureInput({fixture:s.item,state:s.state,context:a,sourceMessageId:s.sourceMessageId,sourceSpeakerId:s.targetId}),{code:'FIXTURE_SPEAKER_CHANGED'});
});
test('visible fact survives absence and reopen; hidden facts never enter request',async t=>{
 const s=await setup(t,evidenceCases.visibleFact);const change=async action=>s.f.store.change(s.id,action,{operationId:action,expectedRevision:(await s.f.store.read(s.id)).revision,payload:{memberId:s.targetId}});
 await change('leave');let calls=0;const host=new GroupHost(s.f.store,{mode:'fixture',async dispatch(r){calls++;return {format:'modelmirror.rpg.group-receipt/1',requestId:r.id,requestHash:digest(requestBody(r)),targetId:r.targetId,modelHash:digest(r.model),status:'completed',dispatched:true,actualModel:'fixture',content:'缺席期间记录：隐藏标记-S7。',partial:'',costMicroUsd:0,evidenceHash:digest('neutral-fixture')};}});
 await host.send(s.id,{operationId:'hidden',expectedRevision:(await s.f.store.read(s.id)).revision,payload:{targetId:s.sourceSpeakerId,input:'隐藏标记-S7'}});await change('rejoin');await s.f.reopen();
 const before=await s.f.store.read(s.id);await s.f.store.prepare(s.id,{operationId:'recall',expectedRevision:before.revision,payload:{targetId:s.targetId,input:s.item.question}});const state=await s.f.store.read(s.id),r=state.requests.at(-1);
 assert.equal(gate(s,state,r).ready,true);assert.equal(r.trace.participation.hasRejoined,true);assert.equal(r.trace.omittedMessageIds.length,0);assert.doesNotMatch(JSON.stringify(r.messages),/隐藏标记-S7/);assert.equal(calls,1);
 // An admitted input is not a passed model answer: the gate does not inspect/repair outputs.
 assert.deepEqual(Object.keys(gate(s,state,r)),['scope','ready','fixtureId','sourceMessageId','sourceSpeakerId']);
});
test('trimmed source blocks attribution instead of blaming recall on the model',async t=>{
 const s=await setup(t,evidenceCases.visibleFact),state=structuredClone(s.state);state.members[1].model.contextTokens=5000;
 for(const [i,text] of ['填充'.repeat(6000),s.item.question].entries())state.messages.push({id:idFor('message','padding',i),sequence:i+2,speakerId:state.members[0].id,content:text,source:{kind:'player-input',reference:null},visibility:{kind:'public'},addressedTo:[s.targetId]});
 const a=assembleGroup(state,s.targetId,s.doc.character.card);assert.ok(a.trace.omittedMessageIds.includes(s.sourceMessageId));assert.throws(()=>gate(s,state,a),{code:'FIXTURE_EVIDENCE_TRIMMED'});
});
test('later entrant cannot use an earlier opening as visible evidence',async t=>{
 const s=await setup(t,evidenceCases.proposalOnly),reference=s.state.members[1].binding.reference;
 await s.f.store.change(s.id,'join',{operationId:'late',expectedRevision:s.state.revision,payload:{reference,modelId:'fixture'}});const state=await s.f.store.read(s.id),a=assembleGroup(state,state.members.at(-1).id,s.doc.character.card);
 assert.doesNotMatch(JSON.stringify(a.messages),/目前只是提出这个方案/);assert.throws(()=>gate(s,state,a),{code:'FIXTURE_EVIDENCE_NOT_VISIBLE'});
});
