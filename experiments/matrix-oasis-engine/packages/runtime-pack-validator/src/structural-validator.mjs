import Ajv2020 from "ajv/dist/2020.js";
import {
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA,
  RUNTIME_GAME_PACK_SCHEMA,
} from "@matrix-oasis/runtime-pack-contracts";
import { appendPointer, makeDiagnostic } from "./diagnostics.mjs";

const KEYWORD_SUFFIX = Object.freeze({
  required: "REQUIRED",
  type: "TYPE",
  const: "CONST",
  enum: "ENUM",
  additionalProperties: "UNKNOWN_PROPERTY",
  unevaluatedProperties: "UNKNOWN_PROPERTY",
  minItems: "MIN_ITEMS",
  maxItems: "MAX_ITEMS",
  uniqueItems: "DUPLICATE_ITEM",
  minLength: "STRING_CONSTRAINT",
  maxLength: "STRING_CONSTRAINT",
  pattern: "STRING_CONSTRAINT",
  minimum: "NUMBER_CONSTRAINT",
  maximum: "NUMBER_CONSTRAINT",
  not: "FORBIDDEN_VALUE",
  oneOf: "SHAPE",
});

const AJV_OPTIONS = Object.freeze({
  allErrors: true,
  strict: true,
  validateSchema: true,
  validateFormats: true,
  messages: false,
  verbose: false,
  coerceTypes: false,
  useDefaults: false,
  removeAdditional: false,
  ownProperties: true,
  allowUnionTypes: false,
  discriminator: false,
});

const MAX_DOCUMENT_DEPTH = 256;

let runtimePackValidator;
let receiptValidator;

function compileSchema(schema) {
  const ajv = new Ajv2020(AJV_OPTIONS);
  return ajv.compile(structuredClone(schema));
}

function getRuntimePackValidator() {
  runtimePackValidator ??= compileSchema(RUNTIME_GAME_PACK_SCHEMA);
  return runtimePackValidator;
}

function getReceiptValidator() {
  receiptValidator ??= compileSchema(RUNTIME_GAME_PACK_RECEIPT_SCHEMA);
  return receiptValidator;
}

function errorPath(error, rootPath) {
  const basePath = `${rootPath}${error.instancePath}`;
  if (error.keyword === "required") {
    return appendPointer(basePath, error.params.missingProperty);
  }
  if (error.keyword === "uniqueItems") {
    const index = Math.max(error.params.i ?? 0, error.params.j ?? 0);
    return appendPointer(basePath, index);
  }
  // Never expose an undeclared property name in a validation report.
  return basePath;
}

function validateDocument({ validator, value, rootPath, codePrefix }) {
  if (validator(value)) {
    return [];
  }
  return (validator.errors ?? []).map((error) => {
    const suffix = KEYWORD_SUFFIX[error.keyword] ?? "INVALID";
    return makeDiagnostic({
      phase: "schema",
      code: `${codePrefix}_${suffix}`,
      path: errorPath(error, rootPath),
    });
  });
}

function exceedsMaximumDepth(value) {
  const pending = [{ value, depth: 0 }];
  while (pending.length > 0) {
    const current = pending.pop();
    if (current.depth > MAX_DOCUMENT_DEPTH) {
      return true;
    }
    if (!current.value || typeof current.value !== "object") {
      continue;
    }
    const childDepth = current.depth + 1;
    for (const child of Object.values(current.value)) {
      pending.push({ value: child, depth: childDepth });
    }
  }
  return false;
}

function validateDocumentWithinDepth({
  getValidator,
  value,
  rootPath,
  codePrefix,
}) {
  if (exceedsMaximumDepth(value)) {
    return [
      makeDiagnostic({
        phase: "schema",
        code: `${codePrefix}_INVALID`,
        path: rootPath,
      }),
    ];
  }
  return validateDocument({
    validator: getValidator(),
    value,
    rootPath,
    codePrefix,
  });
}

export function validateStructures(runtimePack, receipt) {
  return [
    ...validateDocumentWithinDepth({
      getValidator: getRuntimePackValidator,
      value: runtimePack,
      rootPath: "/runtimePack",
      codePrefix: "RUNTIME_PACK_SCHEMA",
    }),
    ...validateDocumentWithinDepth({
      getValidator: getReceiptValidator,
      value: receipt,
      rootPath: "/receipt",
      codePrefix: "RUNTIME_RECEIPT_SCHEMA",
    }),
  ];
}
