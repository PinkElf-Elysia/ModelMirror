export function invoke({capability, session, input}) {
  if (capability !== 'session.message.fork' || !Number.isSafeInteger(input.turn) ||
      input.turn < 1 || input.turn > session.completedTurns) throw Error('MESSAGE_TURN_INVALID');
  return {kind:'message-fork', sessionId:session.id, turn:input.turn};
}
