export function invoke({capability, session, input}) {
  if (!['session.message.fork','session.message.edit'].includes(capability) || !Number.isSafeInteger(input.turn) ||
      input.turn < 1 || input.turn > session.completedTurns) throw Error('MESSAGE_TURN_INVALID');
  return {kind:capability==='session.message.edit'?'message-edit':'message-fork', sessionId:session.id, turn:input.turn};
}
