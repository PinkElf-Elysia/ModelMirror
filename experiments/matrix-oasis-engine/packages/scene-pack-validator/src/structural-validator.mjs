import Ajv2020 from "ajv/dist/2020.js";
import { SCENE_PACK_SCHEMA } from "@matrix-oasis/scene-pack-contracts";
import { diagnostic, pointer } from "./diagnostics.mjs";

const ajv = new Ajv2020({allErrors: true, strict: true, validateSchema: true, messages: false, verbose: false, coerceTypes: false, useDefaults: false, removeAdditional: false, ownProperties: true, allowUnionTypes: false});
const validate = ajv.compile(structuredClone(SCENE_PACK_SCHEMA));
const SUFFIX = Object.freeze({required: "REQUIRED", type: "TYPE", const: "CONST", enum: "ENUM", additionalProperties: "UNKNOWN_PROPERTY", minItems: "MIN_ITEMS", maxItems: "MAX_ITEMS", uniqueItems: "DUPLICATE_ITEM", minLength: "STRING_CONSTRAINT", maxLength: "STRING_CONSTRAINT", pattern: "STRING_CONSTRAINT", minimum: "NUMBER_CONSTRAINT", maximum: "NUMBER_CONSTRAINT", oneOf: "SHAPE"});

export function validateSceneStructure(value) {
  if (validate(value)) return [];
  return (validate.errors ?? []).map((error) => {
    let path = `/scenePack${error.instancePath}`;
    if (error.keyword === "required") path = pointer(path, error.params.missingProperty);
    else if (error.keyword === "uniqueItems") path = pointer(path, Math.max(error.params.i ?? 0, error.params.j ?? 0));
    return diagnostic("schema", `SCENE_PACK_SCHEMA_${SUFFIX[error.keyword] ?? "INVALID"}`, path);
  });
}
