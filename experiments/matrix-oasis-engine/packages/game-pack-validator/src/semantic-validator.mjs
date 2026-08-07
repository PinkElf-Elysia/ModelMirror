import { appendPointer, makeDiagnostic } from "./diagnostics.mjs";

const MAX_CONDITION_DEPTH = 16;
const TOP_LEVEL_COLLECTIONS = Object.freeze([
  ["entities", "entity"],
  ["variables", "variable"],
  ["cues", "cue"],
  ["nodes", "node"],
  ["endings", "ending"],
]);

function addDiagnostic(diagnostics, code, path, message, relatedPath) {
  diagnostics.push(
    makeDiagnostic({
      phase: "semantic",
      code,
      path,
      message,
      relatedPath,
    }),
  );
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
      addDiagnostic(
        diagnostics,
        "PACK_ENUM_VALUE_NOT_ALLOWED",
        path,
        "Enum value is not declared by the referenced variable.",
      );
    }
    return;
  }
  if (!valueMatchesVariable(variable, value)) {
    addDiagnostic(
      diagnostics,
      context === "condition"
        ? "PACK_CONDITION_VALUE_TYPE_MISMATCH"
        : "PACK_EFFECT_VALUE_TYPE_MISMATCH",
      path,
      context === "condition"
        ? "Condition value does not match the referenced variable type."
        : "Effect value does not match the referenced variable type.",
    );
  }
}

function validateCondition({
  condition,
  path,
  depth,
  diagnostics,
  variablesById,
}) {
  if (depth > MAX_CONDITION_DEPTH) {
    addDiagnostic(
      diagnostics,
      "PACK_CONDITION_DEPTH_EXCEEDED",
      path,
      "Condition nesting exceeds the supported depth.",
    );
    return;
  }

  if (condition.op === "all" || condition.op === "any") {
    for (const [index, child] of condition.conditions.entries()) {
      validateCondition({
        condition: child,
        path: appendPointer(appendPointer(path, "conditions"), index),
        depth: depth + 1,
        diagnostics,
        variablesById,
      });
    }
    return;
  }
  if (condition.op === "not") {
    validateCondition({
      condition: condition.condition,
      path: appendPointer(path, "condition"),
      depth: depth + 1,
      diagnostics,
      variablesById,
    });
    return;
  }

  const variablePath = appendPointer(path, "variableId");
  const variable = variablesById.get(condition.variableId);
  if (!variable) {
    addDiagnostic(
      diagnostics,
      "PACK_VARIABLE_REFERENCE_UNKNOWN",
      variablePath,
      "Referenced variable is not declared.",
    );
    return;
  }

  if (["lt", "lte", "gt", "gte"].includes(condition.op)) {
    if (variable.type !== "integer") {
      addDiagnostic(
        diagnostics,
        "PACK_CONDITION_VARIABLE_TYPE_MISMATCH",
        variablePath,
        "Ordered comparison requires an integer variable.",
      );
    }
    return;
  }

  validateVariableValue({
    diagnostics,
    variable,
    value: condition.value,
    path: appendPointer(path, "value"),
    context: "condition",
  });
}

function validateEffect({ effect, path, diagnostics, variablesById, cuesById }) {
  if (effect.op === "emitCue") {
    if (!cuesById.has(effect.cueId)) {
      addDiagnostic(
        diagnostics,
        "PACK_CUE_REFERENCE_UNKNOWN",
        appendPointer(path, "cueId"),
        "Referenced cue is not declared.",
      );
    }
    return;
  }

  const variablePath = appendPointer(path, "variableId");
  const variable = variablesById.get(effect.variableId);
  if (!variable) {
    addDiagnostic(
      diagnostics,
      "PACK_VARIABLE_REFERENCE_UNKNOWN",
      variablePath,
      "Referenced variable is not declared.",
    );
    return;
  }

  if (effect.op === "add") {
    if (variable.type !== "integer") {
      addDiagnostic(
        diagnostics,
        "PACK_EFFECT_VARIABLE_TYPE_MISMATCH",
        variablePath,
        "Add effect requires an integer variable.",
      );
    }
    return;
  }

  validateVariableValue({
    diagnostics,
    variable,
    value: effect.value,
    path: appendPointer(path, "value"),
    context: "effect",
  });
}

function validateReferences(pack, diagnostics, indexes) {
  const {
    entitiesById,
    variablesById,
    cuesById,
    nodesById,
    endingsById,
  } = indexes;
  let entryInvalid = false;
  let targetInvalid = false;

  if (!nodesById.has(pack.entryNodeId)) {
    entryInvalid = true;
    addDiagnostic(
      diagnostics,
      "PACK_ENTRY_NODE_UNKNOWN",
      "/entryNodeId",
      "Entry node is not declared.",
    );
  }

  for (const [variableIndex, variable] of pack.variables.entries()) {
    if (
      variable.type === "enum" &&
      !variable.allowedValues.includes(variable.initial)
    ) {
      addDiagnostic(
        diagnostics,
        "PACK_ENUM_INITIAL_NOT_ALLOWED",
        `/variables/${variableIndex}/initial`,
        "Enum initial value is not declared by allowedValues.",
      );
    }
  }

  for (const [nodeIndex, node] of pack.nodes.entries()) {
    const nodePath = `/nodes/${nodeIndex}`;
    for (const [referenceIndex, entityId] of node.entityIds.entries()) {
      if (!entitiesById.has(entityId)) {
        addDiagnostic(
          diagnostics,
          "PACK_ENTITY_REFERENCE_UNKNOWN",
          `${nodePath}/entityIds/${referenceIndex}`,
          "Referenced entity is not declared.",
        );
      }
    }
    for (const [referenceIndex, cueId] of node.entryCueIds.entries()) {
      if (!cuesById.has(cueId)) {
        addDiagnostic(
          diagnostics,
          "PACK_CUE_REFERENCE_UNKNOWN",
          `${nodePath}/entryCueIds/${referenceIndex}`,
          "Referenced cue is not declared.",
        );
      }
    }

    for (const [actionIndex, action] of node.actions.entries()) {
      const actionPath = `${nodePath}/actions/${actionIndex}`;
      for (const [referenceIndex, entityId] of (action.entityIds ?? []).entries()) {
        if (!entitiesById.has(entityId)) {
          addDiagnostic(
            diagnostics,
            "PACK_ENTITY_REFERENCE_UNKNOWN",
            `${actionPath}/entityIds/${referenceIndex}`,
            "Referenced entity is not declared.",
          );
        }
      }
      if (action.when) {
        validateCondition({
          condition: action.when,
          path: `${actionPath}/when`,
          depth: 1,
          diagnostics,
          variablesById,
        });
      }
      for (const [effectIndex, effect] of action.effects.entries()) {
        validateEffect({
          effect,
          path: `${actionPath}/effects/${effectIndex}`,
          diagnostics,
          variablesById,
          cuesById,
        });
      }

      const targetCollection =
        action.target.kind === "node" ? nodesById : endingsById;
      if (!targetCollection.has(action.target.id)) {
        targetInvalid = true;
        addDiagnostic(
          diagnostics,
          "PACK_TARGET_REFERENCE_UNKNOWN",
          `${actionPath}/target/id`,
          "Action target is not declared in the selected target category.",
        );
      }
    }
  }

  for (const [endingIndex, ending] of pack.endings.entries()) {
    for (const [referenceIndex, cueId] of ending.cueIds.entries()) {
      if (!cuesById.has(cueId)) {
        addDiagnostic(
          diagnostics,
          "PACK_CUE_REFERENCE_UNKNOWN",
          `/endings/${endingIndex}/cueIds/${referenceIndex}`,
          "Referenced cue is not declared.",
        );
      }
    }
  }

  return { entryInvalid, targetInvalid };
}

function validateGraph(pack, diagnostics, nodesById) {
  const adjacency = new Map(pack.nodes.map((node) => [node.id, []]));
  const reverse = new Map(pack.nodes.map((node) => [node.id, []]));
  const canReachEnding = new Set();

  for (const node of pack.nodes) {
    for (const action of node.actions) {
      if (action.target.kind === "ending") {
        canReachEnding.add(node.id);
      } else {
        adjacency.get(node.id).push(action.target.id);
        reverse.get(action.target.id).push(node.id);
      }
    }
  }

  const reachable = new Set();
  const pendingReachable = [pack.entryNodeId];
  while (pendingReachable.length > 0) {
    const current = pendingReachable.shift();
    if (reachable.has(current)) {
      continue;
    }
    reachable.add(current);
    pendingReachable.push(...adjacency.get(current));
  }

  const pendingEnding = [...canReachEnding];
  while (pendingEnding.length > 0) {
    const current = pendingEnding.shift();
    for (const predecessor of reverse.get(current)) {
      if (!canReachEnding.has(predecessor)) {
        canReachEnding.add(predecessor);
        pendingEnding.push(predecessor);
      }
    }
  }

  for (const [nodeIndex, node] of pack.nodes.entries()) {
    const path = `/nodes/${nodeIndex}/id`;
    if (!reachable.has(node.id)) {
      addDiagnostic(
        diagnostics,
        "PACK_NODE_UNREACHABLE",
        path,
        "Node is not reachable from the entry node.",
      );
    } else if (!canReachEnding.has(node.id)) {
      addDiagnostic(
        diagnostics,
        "PACK_NODE_NO_ENDING_PATH",
        path,
        "Reachable node has no structural path to an ending.",
      );
    }
  }
}

export function validateSemantics(pack) {
  const diagnostics = [];
  const firstTopLevelId = new Map();
  let graphIdentityInvalid = false;

  const indexes = {
    entitiesById: new Map(),
    variablesById: new Map(),
    cuesById: new Map(),
    nodesById: new Map(),
    endingsById: new Map(),
  };

  for (const [collectionName, kind] of TOP_LEVEL_COLLECTIONS) {
    const collection = pack[collectionName];
    const categoryIndex = indexes[`${collectionName}ById`];
    for (const [index, item] of collection.entries()) {
      const path = `/${collectionName}/${index}/id`;
      const first = firstTopLevelId.get(item.id);
      if (first) {
        addDiagnostic(
          diagnostics,
          "PACK_TOP_LEVEL_ID_DUPLICATE",
          path,
          "Top-level identifier is already declared.",
          first.path,
        );
        if (["node", "ending"].includes(kind) || ["node", "ending"].includes(first.kind)) {
          graphIdentityInvalid = true;
        }
      } else {
        firstTopLevelId.set(item.id, { path, kind });
      }
      if (!categoryIndex.has(item.id)) {
        categoryIndex.set(item.id, item);
      }
    }
  }

  for (const [nodeIndex, node] of pack.nodes.entries()) {
    const firstActionId = new Map();
    for (const [actionIndex, action] of node.actions.entries()) {
      const path = `/nodes/${nodeIndex}/actions/${actionIndex}/id`;
      if (firstActionId.has(action.id)) {
        addDiagnostic(
          diagnostics,
          "PACK_ACTION_ID_DUPLICATE",
          path,
          "Action identifier is already declared in this node.",
          firstActionId.get(action.id),
        );
      } else {
        firstActionId.set(action.id, path);
      }
    }
  }

  const { entryInvalid, targetInvalid } = validateReferences(
    pack,
    diagnostics,
    indexes,
  );
  if (!graphIdentityInvalid && !entryInvalid && !targetInvalid) {
    validateGraph(pack, diagnostics, indexes.nodesById);
  }

  return diagnostics;
}
