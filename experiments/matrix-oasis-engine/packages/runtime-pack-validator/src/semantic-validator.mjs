import { appendPointer, makeDiagnostic } from "./diagnostics.mjs";

const MAX_CONDITION_DEPTH = 16;
const TOP_LEVEL_COLLECTIONS = Object.freeze([
  ["entities", "entity"],
  ["variables", "variable"],
  ["cues", "cue"],
  ["nodes", "node"],
  ["endings", "ending"],
]);

function addDiagnostic(diagnostics, code, path, relatedPath) {
  diagnostics.push(
    makeDiagnostic({
      phase: "semantic",
      code,
      path,
      relatedPath,
    }),
  );
}

function isValidIndex(value, collection) {
  return Number.isSafeInteger(value) && value >= 0 && value < collection.length;
}

function validateIndexList({ diagnostics, values, collection, path, code }) {
  let invalid = false;
  for (const [index, value] of values.entries()) {
    if (!isValidIndex(value, collection)) {
      invalid = true;
      addDiagnostic(diagnostics, code, appendPointer(path, index));
    }
  }
  return invalid;
}

function valueMatchesVariable(variable, value) {
  if (variable.type === "boolean") {
    return typeof value === "boolean";
  }
  if (variable.type === "integer") {
    return Number.isSafeInteger(value);
  }
  return typeof value === "string" && variable.allowedValues.includes(value);
}

function validateVariableValue({ diagnostics, variable, value, path, context }) {
  if (variable.type === "enum" && typeof value === "string") {
    if (!variable.allowedValues.includes(value)) {
      addDiagnostic(diagnostics, "RUNTIME_PACK_ENUM_VALUE_NOT_ALLOWED", path);
    }
    return;
  }
  if (!valueMatchesVariable(variable, value)) {
    addDiagnostic(
      diagnostics,
      context === "condition"
        ? "RUNTIME_PACK_CONDITION_VALUE_TYPE_MISMATCH"
        : "RUNTIME_PACK_EFFECT_VALUE_TYPE_MISMATCH",
      path,
    );
  }
}

function validateCondition({
  condition,
  path,
  depth,
  diagnostics,
  variables,
}) {
  if (depth > MAX_CONDITION_DEPTH) {
    addDiagnostic(
      diagnostics,
      "RUNTIME_PACK_CONDITION_DEPTH_EXCEEDED",
      path,
    );
    return { invalidIndex: false };
  }

  if (condition.op === "all" || condition.op === "any") {
    let invalidIndex = false;
    for (const [index, child] of condition.conditions.entries()) {
      invalidIndex =
        validateCondition({
          condition: child,
          path: appendPointer(appendPointer(path, "conditions"), index),
          depth: depth + 1,
          diagnostics,
          variables,
        }).invalidIndex || invalidIndex;
    }
    return { invalidIndex };
  }
  if (condition.op === "not") {
    return validateCondition({
      condition: condition.condition,
      path: appendPointer(path, "condition"),
      depth: depth + 1,
      diagnostics,
      variables,
    });
  }

  const variablePath = appendPointer(path, "variableIndex");
  if (!isValidIndex(condition.variableIndex, variables)) {
    addDiagnostic(
      diagnostics,
      "RUNTIME_PACK_VARIABLE_INDEX_INVALID",
      variablePath,
    );
    return { invalidIndex: true };
  }

  const variable = variables[condition.variableIndex];
  if (["lt", "lte", "gt", "gte"].includes(condition.op)) {
    if (variable.type !== "integer") {
      addDiagnostic(
        diagnostics,
        "RUNTIME_PACK_CONDITION_VARIABLE_TYPE_MISMATCH",
        variablePath,
      );
    }
    return { invalidIndex: false };
  }

  validateVariableValue({
    diagnostics,
    variable,
    value: condition.value,
    path: appendPointer(path, "value"),
    context: "condition",
  });
  return { invalidIndex: false };
}

function validateEffect({ effect, path, diagnostics, variables, cues }) {
  if (effect.op === "emitCue") {
    if (!isValidIndex(effect.cueIndex, cues)) {
      addDiagnostic(
        diagnostics,
        "RUNTIME_PACK_CUE_INDEX_INVALID",
        appendPointer(path, "cueIndex"),
      );
      return true;
    }
    return false;
  }

  const variablePath = appendPointer(path, "variableIndex");
  if (!isValidIndex(effect.variableIndex, variables)) {
    addDiagnostic(
      diagnostics,
      "RUNTIME_PACK_VARIABLE_INDEX_INVALID",
      variablePath,
    );
    return true;
  }

  const variable = variables[effect.variableIndex];
  if (effect.op === "add") {
    if (variable.type !== "integer") {
      addDiagnostic(
        diagnostics,
        "RUNTIME_PACK_EFFECT_VARIABLE_TYPE_MISMATCH",
        variablePath,
      );
    }
    return false;
  }

  validateVariableValue({
    diagnostics,
    variable,
    value: effect.value,
    path: appendPointer(path, "value"),
    context: "effect",
  });
  return false;
}

function validateIdentities(pack, diagnostics) {
  const firstTopLevelId = new Map();
  let graphIdentityInvalid = false;

  for (const [collectionName, kind] of TOP_LEVEL_COLLECTIONS) {
    for (const [index, item] of pack[collectionName].entries()) {
      const path = `/runtimePack/${collectionName}/${index}/id`;
      const first = firstTopLevelId.get(item.id);
      if (first) {
        addDiagnostic(
          diagnostics,
          "RUNTIME_PACK_TOP_LEVEL_ID_DUPLICATE",
          path,
          first.path,
        );
        if (
          ["node", "ending"].includes(kind) ||
          ["node", "ending"].includes(first.kind)
        ) {
          graphIdentityInvalid = true;
        }
      } else {
        firstTopLevelId.set(item.id, { path, kind });
      }
    }
  }

  for (const [nodeIndex, node] of pack.nodes.entries()) {
    const firstActionId = new Map();
    for (const [actionIndex, action] of node.actions.entries()) {
      const path = `/runtimePack/nodes/${nodeIndex}/actions/${actionIndex}/id`;
      if (firstActionId.has(action.id)) {
        addDiagnostic(
          diagnostics,
          "RUNTIME_PACK_ACTION_ID_DUPLICATE",
          path,
          firstActionId.get(action.id),
        );
      } else {
        firstActionId.set(action.id, path);
      }
    }
  }

  return graphIdentityInvalid;
}

function validateReferences(pack, diagnostics) {
  let graphReferenceInvalid = false;

  if (!isValidIndex(pack.entryNodeIndex, pack.nodes)) {
    graphReferenceInvalid = true;
    addDiagnostic(
      diagnostics,
      "RUNTIME_PACK_ENTRY_NODE_INDEX_INVALID",
      "/runtimePack/entryNodeIndex",
    );
  }

  for (const [variableIndex, variable] of pack.variables.entries()) {
    if (
      variable.type === "enum" &&
      !variable.allowedValues.includes(variable.initial)
    ) {
      addDiagnostic(
        diagnostics,
        "RUNTIME_PACK_ENUM_INITIAL_NOT_ALLOWED",
        `/runtimePack/variables/${variableIndex}/initial`,
      );
    }
  }

  for (const [nodeIndex, node] of pack.nodes.entries()) {
    const nodePath = `/runtimePack/nodes/${nodeIndex}`;
    validateIndexList({
      diagnostics,
      values: node.entityIndexes,
      collection: pack.entities,
      path: `${nodePath}/entityIndexes`,
      code: "RUNTIME_PACK_ENTITY_INDEX_INVALID",
    });
    validateIndexList({
      diagnostics,
      values: node.entryCueIndexes,
      collection: pack.cues,
      path: `${nodePath}/entryCueIndexes`,
      code: "RUNTIME_PACK_CUE_INDEX_INVALID",
    });

    for (const [actionIndex, action] of node.actions.entries()) {
      const actionPath = `${nodePath}/actions/${actionIndex}`;
      validateIndexList({
        diagnostics,
        values: action.entityIndexes,
        collection: pack.entities,
        path: `${actionPath}/entityIndexes`,
        code: "RUNTIME_PACK_ENTITY_INDEX_INVALID",
      });
      if (action.when !== null) {
        validateCondition({
          condition: action.when,
          path: `${actionPath}/when`,
          depth: 1,
          diagnostics,
          variables: pack.variables,
        });
      }
      for (const [effectIndex, effect] of action.effects.entries()) {
        validateEffect({
          effect,
          path: `${actionPath}/effects/${effectIndex}`,
          diagnostics,
          variables: pack.variables,
          cues: pack.cues,
        });
      }

      const targetCollection =
        action.target.kind === "node" ? pack.nodes : pack.endings;
      if (!isValidIndex(action.target.index, targetCollection)) {
        graphReferenceInvalid = true;
        addDiagnostic(
          diagnostics,
          "RUNTIME_PACK_TARGET_INDEX_INVALID",
          `${actionPath}/target/index`,
        );
      }
    }
  }

  for (const [endingIndex, ending] of pack.endings.entries()) {
    validateIndexList({
      diagnostics,
      values: ending.cueIndexes,
      collection: pack.cues,
      path: `/runtimePack/endings/${endingIndex}/cueIndexes`,
      code: "RUNTIME_PACK_CUE_INDEX_INVALID",
    });
  }

  return graphReferenceInvalid;
}

function validateGraph(pack, diagnostics) {
  const adjacency = pack.nodes.map(() => []);
  const reverse = pack.nodes.map(() => []);
  const canReachEnding = new Set();

  for (const [nodeIndex, node] of pack.nodes.entries()) {
    for (const action of node.actions) {
      if (action.target.kind === "ending") {
        canReachEnding.add(nodeIndex);
      } else {
        adjacency[nodeIndex].push(action.target.index);
        reverse[action.target.index].push(nodeIndex);
      }
    }
  }

  const reachable = new Set();
  const pendingReachable = [pack.entryNodeIndex];
  while (pendingReachable.length > 0) {
    const current = pendingReachable.shift();
    if (reachable.has(current)) {
      continue;
    }
    reachable.add(current);
    pendingReachable.push(...adjacency[current]);
  }

  const pendingEnding = [...canReachEnding];
  while (pendingEnding.length > 0) {
    const current = pendingEnding.shift();
    for (const predecessor of reverse[current]) {
      if (!canReachEnding.has(predecessor)) {
        canReachEnding.add(predecessor);
        pendingEnding.push(predecessor);
      }
    }
  }

  for (const [nodeIndex] of pack.nodes.entries()) {
    const path = `/runtimePack/nodes/${nodeIndex}/id`;
    if (!reachable.has(nodeIndex)) {
      addDiagnostic(diagnostics, "RUNTIME_PACK_NODE_UNREACHABLE", path);
    } else if (!canReachEnding.has(nodeIndex)) {
      addDiagnostic(diagnostics, "RUNTIME_PACK_NODE_NO_ENDING_PATH", path);
    }
  }
}

export function validateSemantics(pack) {
  const diagnostics = [];
  const graphIdentityInvalid = validateIdentities(pack, diagnostics);
  const graphReferenceInvalid = validateReferences(pack, diagnostics);
  if (!graphIdentityInvalid && !graphReferenceInvalid) {
    validateGraph(pack, diagnostics);
  }
  return diagnostics;
}
