import {
  asOperationalError,
  GamePackSimulatorOperationalError,
  runtimeFailure,
} from "./diagnostics.mjs";
import { getPreparedData } from "./prepared.mjs";
import { canonicalScalar, deepFreeze, makeNullRecord } from "./safety.mjs";
import {
  captureSessionOptions,
  makeInitialVariables,
  makeSnapshot,
  validateSnapshot,
} from "./snapshot.mjs";

function cueDescriptor(data, cueId) {
  const cue = data.cues.get(cueId);
  if (!cue) {
    throw new GamePackSimulatorOperationalError();
  }
  return { id: cue.id, channel: cue.channel, intent: cue.intent };
}

function cueDescriptors(data, cueIds) {
  return cueIds.map((cueId) => cueDescriptor(data, cueId));
}

export function evaluateCondition(condition, variables) {
  if (condition === undefined) {
    return true;
  }
  switch (condition.op) {
    case "all":
      for (const child of condition.conditions) {
        if (!evaluateCondition(child, variables)) {
          return false;
        }
      }
      return true;
    case "any":
      for (const child of condition.conditions) {
        if (evaluateCondition(child, variables)) {
          return true;
        }
      }
      return false;
    case "not":
      return !evaluateCondition(condition.condition, variables);
    case "eq":
      return variables[condition.variableId] === condition.value;
    case "ne":
      return variables[condition.variableId] !== condition.value;
    case "lt":
      return variables[condition.variableId] < condition.value;
    case "lte":
      return variables[condition.variableId] <= condition.value;
    case "gt":
      return variables[condition.variableId] > condition.value;
    case "gte":
      return variables[condition.variableId] >= condition.value;
    default:
      throw new GamePackSimulatorOperationalError();
  }
}

function buildInspection(data, snapshot) {
  const isActive = snapshot.status === "active";
  const location = isActive
    ? data.nodes.get(snapshot.location.id)
    : data.endings.get(snapshot.location.id);
  if (!location) {
    throw new GamePackSimulatorOperationalError();
  }
  const canAdvance = isActive && snapshot.stepCount < snapshot.stepLimit;
  return {
    inspectionVersion: 1,
    pack: {
      format: data.pack.format,
      formatVersion: data.pack.formatVersion,
      id: data.pack.id,
      contentVersion: data.pack.contentVersion,
      language: data.pack.language,
      title: data.pack.title,
      summary: data.pack.summary ?? null,
    },
    status: snapshot.status,
    location: {
      kind: snapshot.location.kind,
      id: location.id,
      title: location.title,
      text: location.text ?? null,
      entityIds: isActive ? [...location.entityIds] : [],
    },
    variables: data.pack.variables.map((variable) => ({
      id: variable.id,
      type: variable.type,
      value: canonicalScalar(snapshot.variables[variable.id]),
    })),
    actions: isActive
      ? location.actions.map((action) => ({
          id: action.id,
          label: action.label,
          entityIds: [...(action.entityIds ?? [])],
          available: canAdvance && evaluateCondition(action.when, snapshot.variables),
        }))
      : [],
    stepCount: snapshot.stepCount,
    stepLimit: snapshot.stepLimit,
  };
}

function validatePrepared(prepared) {
  const data = getPreparedData(prepared);
  return data ? { ok: true, data } : { ok: false };
}

export function createGameSession(prepared, options) {
  try {
    const preparedResult = validatePrepared(prepared);
    if (!preparedResult.ok) {
      return runtimeFailure("PACK_RUNTIME_PREPARED_PACK_INVALID");
    }
    const optionResult = captureSessionOptions(options);
    if (!optionResult.ok) {
      return runtimeFailure("PACK_RUNTIME_OPTIONS_INVALID");
    }
    const { data } = preparedResult;
    const entryNode = data.nodes.get(data.pack.entryNodeId);
    if (!entryNode) {
      throw new GamePackSimulatorOperationalError();
    }
    const variables = makeInitialVariables(data.pack);
    const snapshot = makeSnapshot({
      data,
      status: "active",
      location: { kind: "node", id: entryNode.id },
      variables,
      stepCount: 0,
      stepLimit: optionResult.stepLimit,
    });
    return deepFreeze({
      ok: true,
      snapshot,
      inspection: buildInspection(data, snapshot),
      emittedCues: cueDescriptors(data, entryNode.entryCueIds),
    });
  } catch (error) {
    throw asOperationalError(error);
  }
}

export function inspectGameSession(prepared, snapshotInput) {
  try {
    const preparedResult = validatePrepared(prepared);
    if (!preparedResult.ok) {
      return runtimeFailure("PACK_RUNTIME_PREPARED_PACK_INVALID");
    }
    const snapshotResult = validateSnapshot(preparedResult.data, snapshotInput);
    if (!snapshotResult.ok) {
      return runtimeFailure(snapshotResult.code);
    }
    return deepFreeze({
      ok: true,
      inspection: buildInspection(preparedResult.data, snapshotResult.snapshot),
    });
  } catch (error) {
    throw asOperationalError(error);
  }
}

function applyEffects(data, action, sourceVariables) {
  const variables = makeNullRecord(
    data.pack.variables.map((variable) => [variable.id, sourceVariables[variable.id]]),
  );
  const emittedCues = [];
  for (const effect of action.effects) {
    if (effect.op === "set") {
      variables[effect.variableId] = canonicalScalar(effect.value);
      continue;
    }
    if (effect.op === "add") {
      const nextValue = variables[effect.variableId] + effect.value;
      if (!Number.isSafeInteger(nextValue)) {
        return { ok: false };
      }
      variables[effect.variableId] = canonicalScalar(nextValue);
      continue;
    }
    if (effect.op === "emitCue") {
      emittedCues.push(cueDescriptor(data, effect.cueId));
      continue;
    }
    throw new GamePackSimulatorOperationalError();
  }
  return { ok: true, variables, emittedCues };
}

export function applyGameSessionAction(prepared, snapshotInput, actionId) {
  try {
    const preparedResult = validatePrepared(prepared);
    if (!preparedResult.ok) {
      return runtimeFailure("PACK_RUNTIME_PREPARED_PACK_INVALID");
    }
    const { data } = preparedResult;
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
    const sourceNode = data.nodes.get(snapshot.location.id);
    if (!sourceNode) {
      throw new GamePackSimulatorOperationalError();
    }
    const action = typeof actionId === "string"
      ? sourceNode.actions.find((candidate) => candidate.id === actionId)
      : undefined;
    if (!action) {
      return runtimeFailure("PACK_RUNTIME_ACTION_UNKNOWN");
    }
    if (!evaluateCondition(action.when, snapshot.variables)) {
      return runtimeFailure("PACK_RUNTIME_ACTION_UNAVAILABLE");
    }

    const effectResult = applyEffects(data, action, snapshot.variables);
    if (!effectResult.ok) {
      return runtimeFailure("PACK_RUNTIME_INTEGER_OVERFLOW");
    }
    const target = action.target.kind === "node"
      ? data.nodes.get(action.target.id)
      : data.endings.get(action.target.id);
    if (!target) {
      throw new GamePackSimulatorOperationalError();
    }
    const targetCueIds = action.target.kind === "node" ? target.entryCueIds : target.cueIds;
    const emittedCues = [
      ...effectResult.emittedCues,
      ...cueDescriptors(data, targetCueIds),
    ];
    const step = snapshot.stepCount + 1;
    const nextSnapshot = makeSnapshot({
      data,
      status: action.target.kind === "ending" ? "ended" : "active",
      location: action.target,
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
        from: { kind: "node", id: sourceNode.id },
        actionId: action.id,
        to: { kind: action.target.kind, id: target.id },
        emittedCues,
      },
    });
  } catch (error) {
    throw asOperationalError(error);
  }
}
