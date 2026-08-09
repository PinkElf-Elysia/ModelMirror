import { reportFromDiagnostics } from "./diagnostics.mjs";
import { validateIntegrity } from "./integrity-validator.mjs";
import {
  parseJsonDocument,
  RECEIPT_DOCUMENT,
  RUNTIME_PACK_DOCUMENT,
} from "./json-documents.mjs";
import { validateSemantics } from "./semantic-validator.mjs";
import { validateStructures } from "./structural-validator.mjs";

export class RuntimeGamePackValidatorOperationalError extends Error {
  constructor() {
    super("RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR");
    this.name = "RuntimeGamePackValidatorOperationalError";
    this.code = "RUNTIME_PACK_VALIDATOR_INTERNAL_ERROR";
  }
}

function operationalFailure(error) {
  if (error instanceof RuntimeGamePackValidatorOperationalError) {
    return error;
  }
  return new RuntimeGamePackValidatorOperationalError();
}

export async function validateRuntimeGamePackJson(runtimeText, receiptText) {
  try {
    const runtimeResult = parseJsonDocument(
      runtimeText,
      RUNTIME_PACK_DOCUMENT,
    );
    const receiptResult = parseJsonDocument(receiptText, RECEIPT_DOCUMENT);
    const parseDiagnostics = [
      ...(runtimeResult.diagnostics ?? []),
      ...(receiptResult.diagnostics ?? []),
    ];
    if (parseDiagnostics.length > 0) {
      return reportFromDiagnostics(parseDiagnostics);
    }

    const schemaDiagnostics = validateStructures(
      runtimeResult.value,
      receiptResult.value,
    );
    if (schemaDiagnostics.length > 0) {
      return reportFromDiagnostics(schemaDiagnostics);
    }

    const semanticDiagnostics = validateSemantics(runtimeResult.value);
    if (semanticDiagnostics.length > 0) {
      return reportFromDiagnostics(semanticDiagnostics);
    }

    return reportFromDiagnostics(
      await validateIntegrity({
        runtimeText,
        runtimePack: runtimeResult.value,
        receiptText,
        receipt: receiptResult.value,
      }),
    );
  } catch (error) {
    throw operationalFailure(error);
  }
}
