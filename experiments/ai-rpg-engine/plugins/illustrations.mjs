export function invoke({capability,session,input}){
  if(!['session.illustration.generate','session.illustration.rewrite'].includes(capability)||Object.keys(input).length)throw Error('ILLUSTRATION_ACTION_INVALID');
  return {kind:capability,sessionId:session.id};
}
