import {
  validateAuthoringGamePack,
  validateAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-validator";
import {
  CANONICAL_JSON_PROFILE,
  GAME_PACK_COMPILER_ID,
  GAME_PACK_COMPILER_VERSION,
  RUNTIME_GAME_PACK_FORMAT,
  RUNTIME_GAME_PACK_FORMAT_VERSION,
  RUNTIME_GAME_PACK_RECEIPT_FORMAT,
  RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION,
  canonicalizeJsonValue,
} from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";

export class GamePackCompilerOperationalError extends Error {
  constructor() {
    super("PACK_COMPILER_INTERNAL_ERROR");
    this.name = "GamePackCompilerOperationalError";
    this.code = "PACK_COMPILER_INTERNAL_ERROR";
  }
}

function operationalFailure(error) {
  return error instanceof GamePackCompilerOperationalError
    ? error
    : new GamePackCompilerOperationalError();
}

function deepFreeze(value, visited = new WeakSet()) {
  if (!value || typeof value !== "object" || visited.has(value)) {
    return value;
  }
  visited.add(value);
  for (const child of Object.values(value)) {
    deepFreeze(child, visited);
  }
  return Object.freeze(value);
}

function validationFailure(validationReport) {
  return deepFreeze({ ok: false, validationReport });
}

function exactDataDescriptors(value, expectedKeys) {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (Object.getPrototypeOf(value) !== Object.prototype) {
    return null;
  }
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Reflect.ownKeys(descriptors);
  if (
    keys.length !== expectedKeys.length ||
    expectedKeys.some((key) => !keys.includes(key))
  ) {
    return null;
  }
  for (const key of expectedKeys) {
    const descriptor = descriptors[key];
    if (
      !descriptor ||
      !("value" in descriptor) ||
      descriptor.enumerable !== true
    ) {
      return null;
    }
  }
  return descriptors;
}

function isExactEmptyArray(value) {
  if (!Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype) {
    return false;
  }
  const descriptors = Object.getOwnPropertyDescriptors(value);
  const keys = Reflect.ownKeys(descriptors);
  return (
    keys.length === 1 &&
    keys[0] === "length" &&
    "value" in descriptors.length &&
    descriptors.length.value === 0 &&
    descriptors.length.enumerable === false
  );
}

function isExactSuccessfulRuntimeValidationReport(report) {
  const descriptors = exactDataDescriptors(report, [
    "reportVersion",
    "valid",
    "diagnostics",
  ]);
  return (
    descriptors !== null &&
    descriptors.reportVersion.value === 1 &&
    descriptors.valid.value === true &&
    isExactEmptyArray(descriptors.diagnostics.value)
  );
}

function normalizeNumber(value) {
  return typeof value === "number" && Object.is(value, -0) ? 0 : value;
}

function indexById(values) {
  return new Map(values.map((value, index) => [value.id, index]));
}

function requiredIndex(indexes, id) {
  if (!indexes.has(id)) {
    throw new GamePackCompilerOperationalError();
  }
  return indexes.get(id);
}

function mapCondition(condition, variableIndexes) {
  if (condition.op === "all" || condition.op === "any") {
    return {
      op: condition.op,
      conditions: condition.conditions.map((child) =>
        mapCondition(child, variableIndexes),
      ),
    };
  }
  if (condition.op === "not") {
    return {
      op: condition.op,
      condition: mapCondition(condition.condition, variableIndexes),
    };
  }
  if (["eq", "ne", "lt", "lte", "gt", "gte"].includes(condition.op)) {
    return {
      op: condition.op,
      variableIndex: requiredIndex(variableIndexes, condition.variableId),
      value: normalizeNumber(condition.value),
    };
  }
  throw new GamePackCompilerOperationalError();
}

function mapEffect(effect, variableIndexes, cueIndexes) {
  if (effect.op === "emitCue") {
    return {
      op: effect.op,
      cueIndex: requiredIndex(cueIndexes, effect.cueId),
    };
  }
  if (effect.op === "set" || effect.op === "add") {
    return {
      op: effect.op,
      variableIndex: requiredIndex(variableIndexes, effect.variableId),
      value: normalizeNumber(effect.value),
    };
  }
  throw new GamePackCompilerOperationalError();
}

function mapVariable(variable) {
  if (variable.type === "enum") {
    return {
      id: variable.id,
      type: variable.type,
      allowedValues: [...variable.allowedValues],
      initial: variable.initial,
    };
  }
  if (variable.type === "boolean" || variable.type === "integer") {
    return {
      id: variable.id,
      type: variable.type,
      initial: normalizeNumber(variable.initial),
    };
  }
  throw new GamePackCompilerOperationalError();
}

function mapTarget(target, nodeIndexes, endingIndexes) {
  if (target.kind === "node") {
    return { kind: "node", index: requiredIndex(nodeIndexes, target.id) };
  }
  if (target.kind === "ending") {
    return {
      kind: "ending",
      index: requiredIndex(endingIndexes, target.id),
    };
  }
  throw new GamePackCompilerOperationalError();
}

function buildRuntimePack(authoringPack, sourceCanonicalSha256) {
  const entityIndexes = indexById(authoringPack.entities);
  const variableIndexes = indexById(authoringPack.variables);
  const cueIndexes = indexById(authoringPack.cues);
  const nodeIndexes = indexById(authoringPack.nodes);
  const endingIndexes = indexById(authoringPack.endings);

  return {
    format: RUNTIME_GAME_PACK_FORMAT,
    formatVersion: RUNTIME_GAME_PACK_FORMAT_VERSION,
    canonicalization: CANONICAL_JSON_PROFILE,
    source: {
      format: authoringPack.format,
      formatVersion: authoringPack.formatVersion,
      id: authoringPack.id,
      contentVersion: authoringPack.contentVersion,
      canonicalSha256: sourceCanonicalSha256,
    },
    language: authoringPack.language,
    title: authoringPack.title,
    summary: authoringPack.summary ?? null,
    entryNodeIndex: requiredIndex(nodeIndexes, authoringPack.entryNodeId),
    entities: authoringPack.entities.map((entity) => ({
      id: entity.id,
      label: entity.label,
      description: entity.description ?? null,
    })),
    variables: authoringPack.variables.map(mapVariable),
    cues: authoringPack.cues.map((cue) => ({
      id: cue.id,
      channel: cue.channel,
      intent: cue.intent,
    })),
    nodes: authoringPack.nodes.map((node) => ({
      id: node.id,
      title: node.title,
      text: node.text ?? null,
      entityIndexes: node.entityIds.map((id) =>
        requiredIndex(entityIndexes, id),
      ),
      entryCueIndexes: node.entryCueIds.map((id) =>
        requiredIndex(cueIndexes, id),
      ),
      actions: node.actions.map((action) => ({
        id: action.id,
        label: action.label,
        entityIndexes: (action.entityIds ?? []).map((id) =>
          requiredIndex(entityIndexes, id),
        ),
        when:
          action.when === undefined
            ? null
            : mapCondition(action.when, variableIndexes),
        effects: action.effects.map((effect) =>
          mapEffect(effect, variableIndexes, cueIndexes),
        ),
        target: mapTarget(action.target, nodeIndexes, endingIndexes),
      })),
    })),
    endings: authoringPack.endings.map((ending) => ({
      id: ending.id,
      title: ending.title,
      text: ending.text ?? null,
      cueIndexes: ending.cueIds.map((id) => requiredIndex(cueIndexes, id)),
    })),
  };
}

function bytesToHex(bytes) {
  let value = "";
  for (const byte of bytes) {
    value += byte.toString(16).padStart(2, "0");
  }
  return value;
}

async function sha256Hex(text) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    throw new Error("WEB_CRYPTO_UNAVAILABLE");
  }
  const digest = await subtle.digest("SHA-256", new TextEncoder().encode(text));
  return bytesToHex(new Uint8Array(digest));
}

const DEFAULT_SERVICES = Object.freeze({
  validateAuthoringValue: validateAuthoringGamePack,
  validateAuthoringJson: validateAuthoringGamePackJson,
  canonicalize: canonicalizeJsonValue,
  validateRuntimeJson: validateRuntimeGamePackJson,
});

export function createGamePackCompiler(serviceOverrides = {}) {
  const services = { ...DEFAULT_SERVICES, ...serviceOverrides };

  async function compileCanonicalAuthoring(canonicalAuthoringJson) {
    const capturedValidation = services.validateAuthoringJson(
      canonicalAuthoringJson,
    );
    if (!capturedValidation.valid) {
      return validationFailure(capturedValidation);
    }

    const authoringPack = JSON.parse(canonicalAuthoringJson);
    const sourceSha256 = await sha256Hex(canonicalAuthoringJson);
    const runtimePack = buildRuntimePack(authoringPack, sourceSha256);
    const canonicalJson = services.canonicalize(runtimePack);
    const runtimeBytes = new TextEncoder().encode(canonicalJson);
    const receipt = {
      format: RUNTIME_GAME_PACK_RECEIPT_FORMAT,
      formatVersion: RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION,
      canonicalization: CANONICAL_JSON_PROFILE,
      compiler: {
        id: GAME_PACK_COMPILER_ID,
        version: GAME_PACK_COMPILER_VERSION,
      },
      artifact: {
        format: RUNTIME_GAME_PACK_FORMAT,
        formatVersion: RUNTIME_GAME_PACK_FORMAT_VERSION,
        sha256: await sha256Hex(canonicalJson),
        byteLength: runtimeBytes.byteLength,
      },
    };
    const canonicalReceiptJson = services.canonicalize(receipt);
    const runtimeValidation = await services.validateRuntimeJson(
      canonicalJson,
      canonicalReceiptJson,
    );
    if (!isExactSuccessfulRuntimeValidationReport(runtimeValidation)) {
      throw new GamePackCompilerOperationalError();
    }

    return deepFreeze({ ok: true, runtimePack, canonicalJson, receipt });
  }

  async function compileAuthoringGamePackWithServices(value) {
    try {
      let canonicalAuthoringJson;
      try {
        canonicalAuthoringJson = services.canonicalize(value);
      } catch {
        const validation = services.validateAuthoringValue(value);
        if (!validation.valid) {
          return validationFailure(validation);
        }
        throw new GamePackCompilerOperationalError();
      }
      return await compileCanonicalAuthoring(canonicalAuthoringJson);
    } catch (error) {
      throw operationalFailure(error);
    }
  }

  async function compileAuthoringGamePackJsonWithServices(text) {
    try {
      const initialValidation = services.validateAuthoringJson(text);
      if (!initialValidation.valid) {
        return validationFailure(initialValidation);
      }
      const parsed = JSON.parse(text);
      const canonicalAuthoringJson = services.canonicalize(parsed);
      return await compileCanonicalAuthoring(canonicalAuthoringJson);
    } catch (error) {
      throw operationalFailure(error);
    }
  }

  return Object.freeze({
    compileAuthoringGamePack: compileAuthoringGamePackWithServices,
    compileAuthoringGamePackJson: compileAuthoringGamePackJsonWithServices,
  });
}

const compiler = createGamePackCompiler();

export const compileAuthoringGamePack = compiler.compileAuthoringGamePack;
export const compileAuthoringGamePackJson =
  compiler.compileAuthoringGamePackJson;
