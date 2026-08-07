import {
  validateAuthoringGamePack,
  validateAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-validator";
import {
  asOperationalError,
  GamePackSimulatorOperationalError,
} from "./diagnostics.mjs";
import { captureJsonValue, deepFreeze } from "./safety.mjs";

const preparedDataByHandle = new WeakMap();

function cloneValidationReport(report) {
  const captured = captureJsonValue(report);
  if (!captured.ok) {
    throw new GamePackSimulatorOperationalError();
  }
  return deepFreeze(captured.value);
}

function invalidPackResult(report) {
  return deepFreeze({
    ok: false,
    validationReport: cloneValidationReport(report),
  });
}

function makePrepared(pack) {
  deepFreeze(pack);
  const handle = Object.freeze(Object.create(null));
  const data = Object.freeze({
    pack,
    variables: new Map(pack.variables.map((variable) => [variable.id, variable])),
    cues: new Map(pack.cues.map((cue) => [cue.id, cue])),
    nodes: new Map(pack.nodes.map((node) => [node.id, node])),
    endings: new Map(pack.endings.map((ending) => [ending.id, ending])),
  });
  preparedDataByHandle.set(handle, data);
  return deepFreeze({ ok: true, prepared: handle });
}

function prepareCaptured(captured) {
  if (!captured.ok) {
    throw new GamePackSimulatorOperationalError();
  }
  const secondReport = validateAuthoringGamePack(captured.value);
  if (!secondReport.valid) {
    throw new GamePackSimulatorOperationalError();
  }
  return makePrepared(captured.value);
}

export function getPreparedData(prepared) {
  if (
    prepared === null ||
    (typeof prepared !== "object" && typeof prepared !== "function")
  ) {
    return undefined;
  }
  return preparedDataByHandle.get(prepared);
}

export function prepareAuthoringGamePack(value) {
  try {
    const report = validateAuthoringGamePack(value);
    if (!report.valid) {
      return invalidPackResult(report);
    }
    return prepareCaptured(captureJsonValue(value));
  } catch (error) {
    throw asOperationalError(error);
  }
}

export function prepareAuthoringGamePackJson(text) {
  try {
    const report = validateAuthoringGamePackJson(text);
    if (!report.valid) {
      return invalidPackResult(report);
    }
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new GamePackSimulatorOperationalError();
    }
    return prepareCaptured(captureJsonValue(parsed));
  } catch (error) {
    throw asOperationalError(error);
  }
}
