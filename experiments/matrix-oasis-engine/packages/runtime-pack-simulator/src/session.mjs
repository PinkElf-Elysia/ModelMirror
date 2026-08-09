import {
  asOperationalError,
  RuntimeGamePackSimulatorOperationalError,
  runtimeFailure,
} from "./diagnostics.mjs";
import { getPreparedRuntimeData } from "./prepared.mjs";
import { canonicalScalar, deepFreeze } from "./safety.mjs";
import {
  captureSessionOptions,
  makeInitialVariables,
  makeSnapshot,
  validateSnapshot,
} from "./snapshot.mjs";

function cueDescriptor(data, cueIndex) {
  const cue = data.pack.cues[cueIndex];
  if (!cue) {
    throw new RuntimeGamePackSimulatorOperationalError();
  }
  return { id: cue.id, channel: cue.channel, intent: cue.intent };
}

function cueDescriptors(data, indexes) {
  return indexes.map((index) => cueDescriptor(data, index));
}

function entityIds(data, indexes) {
  return indexes.map((index) => {
    const entity = data.pack.entities[index];
    if (!entity) {
      throw new RuntimeGamePackSimulatorOperationalError();
    }
    return entity.id;
  });
}

export function evaluateRuntimeCondition(condition, variables) {
  if (condition === null) {
    return true;
  }
  switch (condition.op) {
    case "all":
      for (const child of condition.conditions) {
        if (!evaluateRuntimeCondition(child, variables)) {
          return false;
        }
      }
      return true;
    case "any":
      for (const child of condition.conditions) {
        if (evaluateRuntimeCondition(child, variables)) {
          return true;
        }
      }
      return false;
    case "not":
      return !evaluateRuntimeCondition(condition.condition, variables);
    case "eq":
      return variables[condition.variableIndex] === condition.value;
    case "ne":
      return variables[condition.variableIndex] !== condition.value;
    case "lt":
      return variables[condition.variableIndex] < condition.value;
    case "lte":
      return variables[condition.variableIndex] <= condition.value;
    case "gt":
      return variables[condition.variableIndex] > condition.value;
    case "gte":
      return variables[condition.variableIndex] >= condition.value;
    default:
      throw new RuntimeGamePackSimulatorOperationalError();
  }
}

function buildInspection(data, snapshot) {
  const active = snapshot.status === "active";
  const collection = active ? data.pack.nodes : data.pack.endings;
  const location = collection[snapshot.location.index];
  if (!location) {
    throw new RuntimeGamePackSimulatorOperationalError();
  }
  const canAdvance = active && snapshot.stepCount < snapshot.stepLimit;
  return {
    inspectionVersion: 1,
    pack: {
      ...snapshot.pack,
      language: data.pack.language,
      title: data.pack.title,
      summary: data.pack.summary,
    },
    status: snapshot.status,
    location: {
      kind: snapshot.location.kind,
      index: snapshot.location.index,
      id: location.id,
      title: location.title,
      text: location.text,
      entityIds: active ? entityIds(data, location.entityIndexes) : [],
    },
    variables: data.pack.variables.map((variable, index) => ({
      id: variable.id,
      type: variable.type,
      value: canonicalScalar(snapshot.variables[index]),
    })),
    actions: active
      ? location.actions.map((action) => ({
          id: action.id,
          label: action.label,
          entityIds: entityIds(data, action.entityIndexes),
          available: canAdvance &&
            evaluateRuntimeCondition(action.when, snapshot.variables),
        }))
      : [],
    stepCount: snapshot.stepCount,
    stepLimit: snapshot.stepLimit,
  };
}

export function createRuntimeGameSession(prepared, options) {
  try {
    const data = getPreparedRuntimeData(prepared);
    if (!data) {
      return runtimeFailure("PACK_RUNTIME_PREPARED_PACK_INVALID");
    }
    const optionResult = captureSessionOptions(options);
    if (!optionResult.ok) {
      return runtimeFailure("PACK_RUNTIME_OPTIONS_INVALID");
    }
    const variables = makeInitialVariables(data.pack);
    const snapshot = makeSnapshot({
      data,
      status: "active",
      location: { kind: "node", index: data.pack.entryNodeIndex },
      variables,
      stepCount: 0,
      stepLimit: optionResult.stepLimit,
    });
    const entryNode = data.pack.nodes[data.pack.entryNodeIndex];
    if (!entryNode) {
      throw new RuntimeGamePackSimulatorOperationalError();
    }
    return deepFreeze({
      ok: true,
      snapshot,
      inspection: buildInspection(data, snapshot),
      emittedCues: cueDescriptors(data, entryNode.entryCueIndexes),
    });
  } catch (error) {
    throw asOperationalError(error);
  }
}

export function inspectRuntimeGameSession(prepared, snapshotInput) {
  try {
    const data = getPreparedRuntimeData(prepared);
    if (!data) {
      return runtimeFailure("PACK_RUNTIME_PREPARED_PACK_INVALID");
    }
    const snapshotResult = validateSnapshot(data, snapshotInput);
    if (!snapshotResult.ok) {
      return runtimeFailure(snapshotResult.code);
    }
    return deepFreeze({
      ok: true,
      inspection: buildInspection(data, snapshotResult.snapshot),
    });
  } catch (error) {
    throw asOperationalError(error);
  }
}

function applyEffects(data, action, sourceVariables) {
  const variables = sourceVariables.map((value) => canonicalScalar(value));
  const emittedCues = [];
  for (const effect of action.effects) {
    if (effect.op === "set") {
      variables[effect.variableIndex] = canonicalScalar(effect.value);
      continue;
    }
    if (effect.op === "add") {
      const nextValue = variables[effect.variableIndex] + effect.value;
      if (!Number.isSafeInteger(nextValue)) {
        return { ok: false };
      }
      variables[effect.variableIndex] = canonicalScalar(nextValue);
      continue;
    }
    if (effect.op === "emitCue") {
      emittedCues.push(cueDescriptor(data, effect.cueIndex));
      continue;
    }
    throw new RuntimeGamePackSimulatorOperationalError();
  }
  return { ok: true, variables, emittedCues };
}

export function applyRuntimeGameSessionAction(prepared, snapshotInput, actionId) {
  try {
    const data = getPreparedRuntimeData(prepared);
    if (!data) {
      return runtimeFailure("PACK_RUNTIME_PREPARED_PACK_INVALID");
    }
    const snapshotResult = validateSnapshot(data, snapshotInput);
    if (!snapshotResult.ok) {
      return runtimeFailure(snapshotResult.code);
    }
    const snapshot = snapshotResult.snapshot;
    if (snapshot.status === "ended") {
      return runtimeFailure("PACK_RUNTIME_SESSION_ENDED");
    }
    if (snapshot.stepCount >= snapshot.stepLimit) {
      return runtimeFailure("PACK_RUNTIME_STEP_LIMIT");
    }
    const sourceNode = data.pack.nodes[snapshot.location.index];
    if (!sourceNode) {
      throw new RuntimeGamePackSimulatorOperationalError();
    }
    const action = typeof actionId === "string"
      ? sourceNode.actions.find((candidate) => candidate.id === actionId)
      : undefined;
    if (!action) {
      return runtimeFailure("PACK_RUNTIME_ACTION_UNKNOWN");
    }
    if (!evaluateRuntimeCondition(action.when, snapshot.variables)) {
      return runtimeFailure("PACK_RUNTIME_ACTION_UNAVAILABLE");
    }
    const effectResult = applyEffects(data, action, snapshot.variables);
    if (!effectResult.ok) {
      return runtimeFailure("PACK_RUNTIME_INTEGER_OVERFLOW");
    }
    const targetCollection = action.target.kind === "node"
      ? data.pack.nodes
      : data.pack.endings;
    const target = targetCollection[action.target.index];
    if (!target) {
      throw new RuntimeGamePackSimulatorOperationalError();
    }
    const targetCueIndexes = action.target.kind === "node"
      ? target.entryCueIndexes
      : target.cueIndexes;
    const emittedCues = [
      ...effectResult.emittedCues,
      ...cueDescriptors(data, targetCueIndexes),
    ];
    const step = snapshot.stepCount + 1;
    const nextSnapshot = makeSnapshot({
      data,
      status: action.target.kind === "ending" ? "ended" : "active",
      location: { kind: action.target.kind, index: action.target.index },
      variables: effectResult.variables,
      stepCount: step,
      stepLimit: snapshot.stepLimit,
    });
    return deepFreeze({
      ok: true,
      snapshot: nextSnapshot,
      inspection: buildInspection(data, nextSnapshot),
      transition: {
        transitionVersion: 1,
        step,
        from: {
          kind: "node",
          index: snapshot.location.index,
          id: sourceNode.id,
        },
        actionId: action.id,
        to: {
          kind: action.target.kind,
          index: action.target.index,
          id: target.id,
        },
        emittedCues,
      },
    });
  } catch (error) {
    throw asOperationalError(error);
  }
}
