import {
  applyGameSessionAction,
  createGameSession,
  prepareAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-simulator";

const PROFILE_FORMAT = "matrix-oasis.prototype-acceptance-profile";
const PROFILE_VERSION = "0.1.0";
const ACCEPTANCE_ENVIRONMENT_PROMPT_MAX = 320;
const ACCEPTANCE_VISUAL_STYLE_PROMPT_MAX = 120;
const ACCEPTANCE_STATE_LIMIT = 10_000;

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

function semanticStateKey(inspection) {
  return JSON.stringify([
    inspection.location.kind,
    inspection.location.id,
    inspection.variables.map((variable) => [
      variable.id,
      variable.type,
      variable.value,
    ]),
  ]);
}

function graphEvidence(canonicalAuthoringJson) {
  const prepared = prepareAuthoringGamePackJson(canonicalAuthoringJson);
  if (!prepared.ok) {
    throw new Error("PROTOTYPE_ACCEPTANCE_SIMULATOR_INVARIANT");
  }
  const created = createGameSession(prepared.prepared, {
    stepLimit: ACCEPTANCE_STATE_LIMIT,
  });
  if (!created.ok) {
    throw new Error("PROTOTYPE_ACCEPTANCE_SIMULATOR_INVARIANT");
  }

  const reachableEndings = new Set();
  const seenStates = new Set([semanticStateKey(created.inspection)]);
  const pending = [
    { snapshot: created.snapshot, inspection: created.inspection },
  ];
  let hasReachableCycle = false;
  let hasReachableDeadlock = false;
  let explorationComplete = true;

  while (pending.length > 0) {
    const current = pending.shift();
    if (current.inspection.status === "ended") {
      reachableEndings.add(current.inspection.location.id);
      continue;
    }

    const availableActions = current.inspection.actions.filter(
      (action) => action.available === true,
    );
    if (availableActions.length === 0) {
      hasReachableDeadlock = true;
    }
    for (const action of availableActions) {
      const applied = applyGameSessionAction(
        prepared.prepared,
        current.snapshot,
        action.id,
      );
      if (!applied.ok) {
        if (applied.diagnostics.some((item) => item.code === "PACK_RUNTIME_STEP_LIMIT")) {
          explorationComplete = false;
          continue;
        }
        throw new Error("PROTOTYPE_ACCEPTANCE_SIMULATOR_INVARIANT");
      }
      if (applied.inspection.status === "ended") {
        reachableEndings.add(applied.inspection.location.id);
        continue;
      }
      const key = semanticStateKey(applied.inspection);
      if (seenStates.has(key)) {
        hasReachableCycle = true;
        continue;
      }
      if (seenStates.size >= ACCEPTANCE_STATE_LIMIT) {
        explorationComplete = false;
        continue;
      }
      seenStates.add(key);
      pending.push({ snapshot: applied.snapshot, inspection: applied.inspection });
    }
  }

  return {
    reachableEndings,
    hasReachableCycle,
    hasReachableDeadlock,
    explorationComplete,
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

  if (blueprint.scene.environmentPrompt.length > ACCEPTANCE_ENVIRONMENT_PROMPT_MAX) {
    diagnostics.push(
      diagnostic(
        "PROTOTYPE_ACCEPTANCE_ENVIRONMENT_PROMPT_LENGTH",
        "/sceneBlueprint/scene/environmentPrompt",
      ),
    );
  }
  if (blueprint.scene.visualStylePrompt.length > ACCEPTANCE_VISUAL_STYLE_PROMPT_MAX) {
    diagnostics.push(
      diagnostic(
        "PROTOTYPE_ACCEPTANCE_VISUAL_STYLE_PROMPT_LENGTH",
        "/sceneBlueprint/scene/visualStylePrompt",
      ),
    );
  }

  const graph = graphEvidence(preparedProposal.canonicalAuthoringJson);
  if (!graph.explorationComplete) {
    diagnostics.push(
      diagnostic("PROTOTYPE_ACCEPTANCE_STATE_SPACE_LIMIT", "/authoringGamePack"),
    );
  }
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
  if (profile.requireAllEndingsReachable && graph.hasReachableDeadlock) {
    diagnostics.push(
      diagnostic("PROTOTYPE_ACCEPTANCE_ACTIVE_DEADLOCK", "/authoringGamePack/nodes"),
    );
  }
  if (profile.requireAllNonEnvironmentBriefsBound) {
    const placementsByBrief = new Map();
    const visiblePlacementIds = new Set();
    for (const placement of blueprint.placements) {
      const list = placementsByBrief.get(placement.assetBriefId) ?? [];
      list.push(placement);
      placementsByBrief.set(placement.assetBriefId, list);
    }
    for (const binding of blueprint.nodeBindings) {
      for (const placementId of binding.visiblePlacementIds) {
        visiblePlacementIds.add(placementId);
      }
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
    if (
      blueprint.assetBriefs.some(
        (brief) =>
          brief.kind !== "environment" &&
          !(placementsByBrief.get(brief.id) ?? []).some(
            (placement) =>
              placement.entityId === brief.entityId &&
              visiblePlacementIds.has(placement.id),
          ),
      )
    ) {
      diagnostics.push(
        diagnostic(
          "PROTOTYPE_ACCEPTANCE_ASSET_VISIBILITY_REQUIRED",
          "/sceneBlueprint/nodeBindings",
        ),
      );
    }
  }
  return Object.freeze(diagnostics);
}
