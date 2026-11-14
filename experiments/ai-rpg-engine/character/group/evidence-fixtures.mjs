/** Test materials only. Never imported by a production runtime or model gateway. */
export const evidenceCases=Object.freeze({
 proposalOnly:Object.freeze({id:'proposal-only-unexecuted-v1',text:'我提议稍后去看守门口。此刻我仍坐在茶歇桌旁，还没有离开座位，也没有开始守门；目前只是提出这个方案。',question:'截至这条发言，发言者关于守门说了什么？哪些状态有明确依据？',expected:'explicitly-not-started-as-reported'}),
 unknownAction:Object.freeze({id:'intention-without-status-v1',text:'我愿意稍后帮忙看守门口。',question:'只根据这条发言，能否确定发言者现在已经开始守门，或者确定尚未开始？',expected:'execution-status-unknown'}),
 reportedAction:Object.freeze({id:'self-reported-action-v1',text:'我已经离开茶歇桌，走到门边并开始看守入口。这是我对自己当前动作的陈述。',question:'这条记录提供了什么行动证据？这是发言者的陈述，还是宿主独立核实的状态？',expected:'actor-reported-start-not-host-verification'}),
 ambiguousAction:Object.freeze({id:'mixed-intention-status-v1',text:'我可以去看门。那我先照看着，你说一声我再调整。',question:'这段话是否构成明确的“仅提议、尚未执行”前提？',expected:'reject-pure-proposal-premise'}),
 visibleFact:Object.freeze({id:'visible-first-marker-v1',text:'这是本群第一条集合标记：石榴灯-406。此前没有约定过其他集合标记。',question:'本群最初明确说出的集合标记是什么？',expected:'recall-explicit-visible-marker'})
});
const fail=code=>{const e=Error(code);e.code=code;throw e;};
/** Input admission only; never grade or repair a model answer, or infer arbitrary actions. */
export function checkFixtureInput({fixture,state,context,sourceMessageId,sourceSpeakerId}){
 const source=state.messages.find(m=>m.id===sourceMessageId);
 if(!source||source.source.kind!=='opening'||source.content!==fixture.text)fail('FIXTURE_SOURCE_CHANGED');
 if(source.speakerId!==sourceSpeakerId)fail('FIXTURE_SPEAKER_CHANGED');
 const index=context.trace.visibleMessageIds.indexOf(sourceMessageId);
 if(index<0)fail(context.trace.omittedMessageIds.includes(sourceMessageId)?'FIXTURE_EVIDENCE_TRIMMED':'FIXTURE_EVIDENCE_NOT_VISIBLE');
 const attributed=context.messages.flatMap(m=>{try{const v=JSON.parse(m.content);return v.speaker?[{role:m.role,...v}]:[];}catch{return [];}});
 if(attributed[index]?.speaker.id!==sourceSpeakerId||attributed[index]?.text!==fixture.text||attributed[index]?.role!==(context.trace.targetId===sourceSpeakerId?'assistant':'user'))fail('FIXTURE_WIRE_ATTRIBUTION_CHANGED');
 return {scope:'input-only',ready:true,fixtureId:fixture.id,sourceMessageId,sourceSpeakerId};
}
