import { reportFromDiagnostics } from "./diagnostics.mjs";
import { parseAuthoringGamePackJson } from "./json-document.mjs";
import { normalizeJsonValue } from "./json-value.mjs";
import { validateSemantics } from "./semantic-validator.mjs";
import { validateStructure } from "./structural-validator.mjs";

function validateValue(value) {
  const normalized = normalizeJsonValue(value);
  if (normalized.diagnostics.length > 0) {
    return reportFromDiagnostics(normalized.diagnostics);
  }

  const structuralDiagnostics = validateStructure(normalized.value);
  if (structuralDiagnostics.length > 0) {
    return reportFromDiagnostics(structuralDiagnostics);
  }

  return reportFromDiagnostics(validateSemantics(normalized.value));
}

export class AuthoringGamePackOperationalError extends Error {
  constructor() {
    super("PACK_VALIDATOR_INTERNAL_ERROR");
    this.name = "AuthoringGamePackOperationalError";
    this.code = "PACK_VALIDATOR_INTERNAL_ERROR";
  }
}

function operationalFailure(error) {
  if (error instanceof AuthoringGamePackOperationalError) {
    return error;
  }
  return new AuthoringGamePackOperationalError();
}

export function validateAuthoringGamePack(value) {
  try {
    return validateValue(value);
  } catch (error) {
    throw operationalFailure(error);
  }
}

export function validateAuthoringGamePackJson(text) {
  try {
    const parsed = parseAuthoringGamePackJson(text);
    if (!parsed.ok) {
      return reportFromDiagnostics(parsed.diagnostics);
    }
    return validateValue(parsed.value);
  } catch (error) {
    throw operationalFailure(error);
  }
}
