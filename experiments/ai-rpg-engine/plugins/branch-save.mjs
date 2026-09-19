// Reviewed first-party adapter. Proposals only; the host owns all persistence.
export function invoke({capability, session, input = {}}) {
  if (capability === 'ui.message-action') {
    return {kind: 'message-action', action: 'branch', label: '分支', available: session.completedTurns > 0};
  }
  if (capability === 'session.branch.prepare') {
    if (!Number.isInteger(input.turn) || input.turn < 1 || input.turn > session.completedTurns ||
        typeof input.name !== 'string' || !input.name.trim() || input.name.trim().length > 80) {
      throw Error('INVALID_BRANCH_INPUT');
    }
    return {kind: 'branch-request', sessionId: session.id, turn: input.turn, name: input.name.trim()};
  }
  throw Error('CAPABILITY_UNAVAILABLE');
}
