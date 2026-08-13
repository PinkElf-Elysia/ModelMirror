const PROFILE_FORMAT = "matrix-oasis.prototype-acceptance-profile";
const PROFILE_VERSION = "0.1.0";

function diagnostic(code, path) {
  return Object.freeze({
    phase: "semantic",
    severity: "error",
    code,
    path,
    message: code,
  });
}

function descriptorsOf(value) {
  if (!value || typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
    return null;
  }
  try {
    return Object.getOwnPropertyDescriptors(value);
  } catch {
    return null;
  }
}

function exactRecord(value, expectedKeys) {
  const descriptors = descriptorsOf(value);
  if (!descriptors) {
    return null;
  }
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.length !== expectedKeys.length ||
    keys.some(
      (key) =>
        typeof key !== "string" ||
        !expectedKeys.includes(key) ||
        !descriptors[key].enumerable ||
        !("value" in descriptors[key]),
    )
  ) {
    return null;
  }
  return Object.fromEntries(expectedKeys.map((key) => [key, descriptors[key].value]));
}

function normalizeRange(value, maximum) {
  const range = exactRecord(value, ["min", "max"]);
  if (
    !range ||
    !Number.isSafeInteger(range.min) ||
    !Number.isSafeInteger(range.max) ||
    range.min < 0 ||
    range.max < range.min ||
    range.max > maximum
  ) {
    return null;
  }
  return Object.freeze({ min: range.min, max: range.max });
}

export function normalizeAcceptanceOptions(value) {
  if (value === undefined) {
    return Object.freeze({ ok: true, profile: null });
  }
  const options = exactRecord(value, ["acceptanceProfile"]);
  if (!options) {
    return Object.freeze({ ok: false });
  }
  const profile = exactRecord(options.acceptanceProfile, [
    "format",
    "formatVersion",
    "nodes",
    "endings",
    "actions",
    "zones",
    "props",
    "characterPlaceholders",
    "requireReachableCycle",
    "requireAllEndingsReachable",
    "requireAllNonEnvironmentBriefsBound",
  ]);
  if (
    !profile ||
    profile.format !== PROFILE_FORMAT ||
    profile.formatVersion !== PROFILE_VERSION ||
    typeof profile.requireReachableCycle !== "boolean" ||
    typeof profile.requireAllEndingsReachable !== "boolean" ||
    typeof profile.requireAllNonEnvironmentBriefsBound !== "boolean"
  ) {
    return Object.freeze({ ok: false });
  }
  const normalized = {
    format: PROFILE_FORMAT,
    formatVersion: PROFILE_VERSION,
    nodes: normalizeRange(profile.nodes, 4096),
    endings: normalizeRange(profile.endings, 4096),
    actions: normalizeRange(profile.actions, 262144),
    zones: normalizeRange(profile.zones, 16),
    props: normalizeRange(profile.props, 16),
    characterPlaceholders: normalizeRange(profile.characterPlaceholders, 16),
    requireReachableCycle: profile.requireReachableCycle,
    requireAllEndingsReachable: profile.requireAllEndingsReachable,
    requireAllNonEnvironmentBriefsBound:
      profile.requireAllNonEnvironmentBriefsBound,
  };
  if (
    !normalized.nodes ||
    !normalized.endings ||
    !normalized.actions ||
    !normalized.zones ||
    !normalized.props ||
    !normalized.characterPlaceholders
  ) {
    return Object.freeze({ ok: false });
  }
  return Object.freeze({ ok: true, profile: Object.freeze(normalized) });
}

function countDiagnostic(range, count, path, code) {
  return count < range.min || count > range.max ? diagnostic(code, path) : null;
}

function graphEvidence(authoring) {
  const nodeById = new Map(authoring.nodes.map((node) => [node.id, node]));
  const reachableNodes = new Set();
  const reachableEndings = new Set();
  const adjacency = new Map();
  const pending = [authoring.entryNodeId];
  while (pending.length > 0) {
    const nodeId = pending.shift();
    if (reachableNodes.has(nodeId)) {
      continue;
    }
    reachableNodes.add(nodeId);
    const node = nodeById.get(nodeId);
    const targets = [];
    for (const action of node.actions) {
      if (action.target.kind === "node") {
        targets.push(action.target.id);
        if (!reachableNodes.has(action.target.id)) {
          pending.push(action.target.id);
        }
      } else {
        reachableEndings.add(action.target.id);
      }
    }
    adjacency.set(nodeId, targets);
  }

  const state = new Map();
  let hasReachableCycle = false;
  for (const rootId of reachableNodes) {
    if (state.get(rootId) === 2) {
      continue;
    }
    const stack = [{ nodeId: rootId, targetIndex: 0 }];
    state.set(rootId, 1);
    while (stack.length > 0 && !hasReachableCycle) {
      const frame = stack[stack.length - 1];
      const targets = adjacency.get(frame.nodeId) ?? [];
      if (frame.targetIndex >= targets.length) {
        state.set(frame.nodeId, 2);
        stack.pop();
        continue;
      }
      const targetId = targets[frame.targetIndex];
      frame.targetIndex += 1;
      if (!reachableNodes.has(targetId)) {
        continue;
      }
      const targetState = state.get(targetId) ?? 0;
      if (targetState === 1) {
        hasReachableCycle = true;
      } else if (targetState === 0) {
        state.set(targetId, 1);
        stack.push({ nodeId: targetId, targetIndex: 0 });
      }
    }
    if (hasReachableCycle) {
      break;
    }
  }

  return {
    reachableEndings,
    hasReachableCycle,
  };
}

export function evaluateAcceptanceProfile(preparedProposal, profile) {
  if (profile === null) {
    return Object.freeze([]);
  }
  const authoring = preparedProposal.value.authoringGamePack;
  const blueprint = preparedProposal.value.sceneBlueprint;
  const actionCount = authoring.nodes.reduce(
    (total, node) => total + node.actions.length,
    0,
  );
  const propCount = blueprint.assetBriefs.filter((brief) => brief.kind === "prop").length;
  const characterCount = blueprint.assetBriefs.filter(
    (brief) => brief.kind === "character-placeholder",
  ).length;
  const diagnostics = [];
  for (const [range, count, path, code] of [
    [profile.nodes, authoring.nodes.length, "/authoringGamePack/nodes", "PROTOTYPE_ACCEPTANCE_NODE_COUNT"],
    [profile.endings, authoring.endings.length, "/authoringGamePack/endings", "PROTOTYPE_ACCEPTANCE_ENDING_COUNT"],
    [profile.actions, actionCount, "/authoringGamePack/nodes", "PROTOTYPE_ACCEPTANCE_ACTION_COUNT"],
    [profile.zones, blueprint.zones.length, "/sceneBlueprint/zones", "PROTOTYPE_ACCEPTANCE_ZONE_COUNT"],
    [profile.props, propCount, "/sceneBlueprint/assetBriefs", "PROTOTYPE_ACCEPTANCE_PROP_COUNT"],
    [profile.characterPlaceholders, characterCount, "/sceneBlueprint/assetBriefs", "PROTOTYPE_ACCEPTANCE_CHARACTER_COUNT"],
  ]) {
    const item = countDiagnostic(range, count, path, code);
    if (item) {
      diagnostics.push(item);
    }
  }

  const graph = graphEvidence(authoring);
  if (profile.requireReachableCycle && !graph.hasReachableCycle) {
    diagnostics.push(
      diagnostic("PROTOTYPE_ACCEPTANCE_REACHABLE_CYCLE_REQUIRED", "/authoringGamePack/nodes"),
    );
  }
  if (
    profile.requireAllEndingsReachable &&
    authoring.endings.some((ending) => !graph.reachableEndings.has(ending.id))
  ) {
    diagnostics.push(
      diagnostic("PROTOTYPE_ACCEPTANCE_ENDING_UNREACHABLE", "/authoringGamePack/endings"),
    );
  }
  if (profile.requireAllNonEnvironmentBriefsBound) {
    const placementsByBrief = new Map();
    for (const placement of blueprint.placements) {
      const list = placementsByBrief.get(placement.assetBriefId) ?? [];
      list.push(placement);
      placementsByBrief.set(placement.assetBriefId, list);
    }
    if (
      blueprint.assetBriefs.some(
        (brief) =>
          brief.kind !== "environment" &&
          (brief.entityId === null ||
            !(placementsByBrief.get(brief.id) ?? []).some(
              (placement) => placement.entityId === brief.entityId,
            )),
      )
    ) {
      diagnostics.push(
        diagnostic("PROTOTYPE_ACCEPTANCE_ASSET_BINDING_REQUIRED", "/sceneBlueprint/assetBriefs"),
      );
    }
  }
  return Object.freeze(diagnostics);
}
