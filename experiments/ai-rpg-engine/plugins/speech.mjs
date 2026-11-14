export function invoke({capability,session,input}) {
  if(capability!=='session.speech.generate'||Object.keys(input).length)throw Error('SPEECH_ACTION_INVALID');
  return {kind:capability,sessionId:session.id};
}
