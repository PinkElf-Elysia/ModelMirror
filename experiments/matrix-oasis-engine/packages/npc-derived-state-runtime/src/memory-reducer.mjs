export function createMemoryReducerState(scopeActorEntityIds) {
  return {
    actors: new Set(scopeActorEntityIds),
    episodes: [],
    scannedEntries: 0,
  };
}

export function reduceMemoryLedgerEntry(state, entry, actionEntityIdsByKey) {
  state.scannedEntries += 1;
  if (entry.decision.status !== "accepted" || !state.actors.has(entry.intent.actorEntityId) || !entry.transition) return;
  const interactionEntityIds = [...(actionEntityIdsByKey.get(`${entry.intent.nodeId}\0${entry.intent.actionId}`) ?? [])];
  state.episodes.push({
    episodeId: `episode-${String(entry.revision).padStart(5, "0")}-${entry.entrySha256.slice(7, 23)}`,
    actorEntityId: entry.intent.actorEntityId,
    intentId: entry.intent.id,
    revision: entry.revision,
    entrySha256: entry.entrySha256,
    beforeSnapshotSha256: entry.beforeSnapshotSha256,
    afterSnapshotSha256: entry.afterSnapshotSha256,
    interactionEntityIds,
    transition: {
      transitionVersion: entry.transition.transitionVersion,
      step: entry.transition.step,
      from: { ...entry.transition.from },
      actionId: entry.transition.actionId,
      to: { ...entry.transition.to },
    },
  });
}

export function finishMemoryReducerState(state) {
  return {
    episodes: state.episodes,
    scannedEntries: state.scannedEntries,
  };
}
