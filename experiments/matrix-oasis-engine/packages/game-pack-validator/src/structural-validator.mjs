import Ajv2020 from "ajv/dist/2020.js";
import { AUTHORING_GAME_PACK_SCHEMA } from "@matrix-oasis/game-pack-contracts";
import { appendPointer, makeDiagnostic } from "./diagnostics.mjs";

const KEYWORD_MAPPING = Object.freeze({
  required: ["PACK_SCHEMA_REQUIRED", "Required property is missing."],
  type: ["PACK_SCHEMA_TYPE", "Value has an invalid JSON type."],
  const: ["PACK_SCHEMA_CONST", "Value does not match the required constant."],
  enum: ["PACK_SCHEMA_ENUM", "Value is not one of the allowed options."],
  additionalProperties: [
    "PACK_SCHEMA_UNKNOWN_PROPERTY",
    "Property is not allowed.",
  ],
  unevaluatedProperties: [
    "PACK_SCHEMA_UNKNOWN_PROPERTY",
    "Property is not allowed.",
  ],
  minItems: ["PACK_SCHEMA_MIN_ITEMS", "Array has too few items."],
  maxItems: ["PACK_SCHEMA_MAX_ITEMS", "Array has too many items."],
  uniqueItems: ["PACK_SCHEMA_DUPLICATE_ITEM", "Array items must be unique."],
  minLength: [
    "PACK_SCHEMA_STRING_CONSTRAINT",
    "String violates its length constraint.",
  ],
  maxLength: [
    "PACK_SCHEMA_STRING_CONSTRAINT",
    "String violates its length constraint.",
  ],
  pattern: [
    "PACK_SCHEMA_STRING_CONSTRAINT",
    "String violates its format constraint.",
  ],
  minimum: [
    "PACK_SCHEMA_NUMBER_CONSTRAINT",
    "Number violates its range constraint.",
  ],
  maximum: [
    "PACK_SCHEMA_NUMBER_CONSTRAINT",
    "Number violates its range constraint.",
  ],
  not: ["PACK_SCHEMA_FORBIDDEN_VALUE", "Value matches a forbidden shape."],
  oneOf: [
    "PACK_SCHEMA_SHAPE",
    "Value must match exactly one allowed shape.",
  ],
});

let compiledValidator;

function getValidator() {
  if (!compiledValidator) {
    const ajv = new Ajv2020({
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
    compiledValidator = ajv.compile(structuredClone(AUTHORING_GAME_PACK_SCHEMA));
  }
  return compiledValidator;
}

function errorPath(error) {
  if (error.keyword === "required") {
    return appendPointer(error.instancePath, error.params.missingProperty);
  }
  if (
    error.keyword === "additionalProperties" ||
    error.keyword === "unevaluatedProperties"
  ) {
    return error.instancePath;
  }
  if (error.keyword === "uniqueItems") {
    const index = Math.max(error.params.i ?? 0, error.params.j ?? 0);
    return appendPointer(error.instancePath, index);
  }
  return error.instancePath;
}

export function validateStructure(value) {
  const validator = getValidator();
  if (validator(value)) {
    return [];
  }
  return (validator.errors ?? []).map((error) => {
    const [code, message] = KEYWORD_MAPPING[error.keyword] ?? [
      "PACK_SCHEMA_INVALID",
      "Value violates the Authoring Game Pack schema.",
    ];
    return makeDiagnostic({
      phase: "schema",
      code,
      path: errorPath(error),
      message,
    });
  });
}
