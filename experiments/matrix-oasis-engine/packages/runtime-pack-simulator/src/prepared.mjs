import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";
import {
  asOperationalError,
  RuntimeGamePackSimulatorOperationalError,
} from "./diagnostics.mjs";
import { captureJsonValue, deepFreeze, hasExactKeys } from "./safety.mjs";

const preparedDataByHandle = new WeakMap();
const REPORT_KEYS = Object.freeze(["reportVersion", "valid", "diagnostics"]);

function captureValidationReport(report) {
  const captured = captureJsonValue(report);
  if (
    !captured.ok ||
    !hasExactKeys(captured.value, REPORT_KEYS) ||
    captured.value.reportVersion !== 1 ||
    typeof captured.value.valid !== "boolean" ||
    !Array.isArray(captured.value.diagnostics) ||
    (captured.value.valid && captured.value.diagnostics.length !== 0)
  ) {
    throw new RuntimeGamePackSimulatorOperationalError();
  }
  return deepFreeze(captured.value);
}

function parseValidatedJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    throw new RuntimeGamePackSimulatorOperationalError();
  }
}

function makePrepared(pack, receipt) {
  deepFreeze(pack);
  deepFreeze(receipt);
  const handle = Object.freeze(Object.create(null));
  preparedDataByHandle.set(handle, Object.freeze({ pack, receipt }));
  return deepFreeze({ ok: true, prepared: handle });
}

export function getPreparedRuntimeData(prepared) {
  if (
    prepared === null ||
    (typeof prepared !== "object" && typeof prepared !== "function")
  ) {
    return undefined;
  }
  return preparedDataByHandle.get(prepared);
}

export async function prepareRuntimeGamePackJson(runtimeText, receiptText) {
  try {
    const rawReport = await validateRuntimeGamePackJson(runtimeText, receiptText);
    const report = captureValidationReport(rawReport);
    if (!report.valid) {
      return deepFreeze({ ok: false, validationReport: report });
    }
    return makePrepared(
      parseValidatedJson(runtimeText),
      parseValidatedJson(receiptText),
    );
  } catch (error) {
    throw asOperationalError(error);
  }
}
