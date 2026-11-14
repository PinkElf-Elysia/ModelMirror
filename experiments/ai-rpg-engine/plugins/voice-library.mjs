export function invoke({capability,session,input}) {
 if(capability!=='session.voice.manage'||Object.keys(input).length)throw Error('VOICE_ACTION_INVALID');
 return {kind:capability,sessionId:session.id};
}
