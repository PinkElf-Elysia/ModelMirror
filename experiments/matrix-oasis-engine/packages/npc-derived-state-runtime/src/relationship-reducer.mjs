function edgeKey(rule) {
  return `${rule.sourceActorEntityId}\0${rule.targetEntityId}\0${rule.dimensionId}`;
}

export function createRelationshipReducerState(rules) {
  const rulesByAction = new Map();
  const edges = new Map();
  for (const rule of rules) {
    const actionKey = `${rule.sourceActorEntityId}\0${rule.nodeId}\0${rule.actionId}`;
    const indexed = rulesByAction.get(actionKey) ?? [];
    indexed.push(rule);
    rulesByAction.set(actionKey, indexed);
    const key = edgeKey(rule);
    if (!edges.has(key)) {
      edges.set(key, {
        sourceActorEntityId: rule.sourceActorEntityId,
        targetEntityId: rule.targetEntityId,
        dimensionId: rule.dimensionId,
        value: 0,
        contributions: [],
      });
    }
  }
  return {
    rulesByAction,
    edges,
    appliedRuleIds: new Set(),
    scannedEntries: 0,
    ruleLookups: 0,
  };
}

export function reduceRelationshipLedgerEntry(state, entry) {
  state.scannedEntries += 1;
  if (entry.decision.status !== "accepted") return;
  const actionKey = `${entry.intent.actorEntityId}\0${entry.intent.nodeId}\0${entry.intent.actionId}`;
  const rules = state.rulesByAction.get(actionKey) ?? [];
  state.ruleLookups += rules.length;
  for (const rule of rules) {
    if (state.appliedRuleIds.has(rule.ruleId)) continue;
    state.appliedRuleIds.add(rule.ruleId);
    const edge = state.edges.get(edgeKey(rule));
    edge.value += rule.delta;
    edge.contributions.push({
      ruleId: rule.ruleId,
      revision: entry.revision,
      entrySha256: entry.entrySha256,
      delta: rule.delta,
    });
  }
}

export function finishRelationshipReducerState(state) {
  const relationships = [...state.edges.values()].sort((left, right) => {
    const leftKey = `${left.sourceActorEntityId}\0${left.targetEntityId}\0${left.dimensionId}`;
    const rightKey = `${right.sourceActorEntityId}\0${right.targetEntityId}\0${right.dimensionId}`;
    return leftKey < rightKey ? -1 : leftKey > rightKey ? 1 : 0;
  });
  return {
    relationships,
    scannedEntries: state.scannedEntries,
    ruleLookups: state.ruleLookups,
    contributions: relationships.reduce((total, edge) => total + edge.contributions.length, 0),
  };
}
