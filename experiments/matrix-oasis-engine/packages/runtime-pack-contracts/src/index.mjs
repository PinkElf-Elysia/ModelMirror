import runtimeGamePackSchema from "../schemas/0.1.0/runtime-game-pack.schema.json" with {
  type: "json",
};
import runtimeGamePackReceiptSchema from "../schemas/0.1.0/runtime-game-pack-receipt.schema.json" with {
  type: "json",
};

const MAX_CANONICAL_DEPTH = 256;

function freezeTree(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  for (const child of Object.values(value)) {
    freezeTree(child);
  }
  return Object.freeze(value);
}

export const RUNTIME_GAME_PACK_SCHEMA = freezeTree(runtimeGamePackSchema);
export const RUNTIME_GAME_PACK_FORMAT =
  RUNTIME_GAME_PACK_SCHEMA.properties.format.const;
export const RUNTIME_GAME_PACK_FORMAT_VERSION =
  RUNTIME_GAME_PACK_SCHEMA.properties.formatVersion.const;
export const RUNTIME_GAME_PACK_SCHEMA_ID = RUNTIME_GAME_PACK_SCHEMA.$id;

export const RUNTIME_GAME_PACK_RECEIPT_SCHEMA = freezeTree(
  runtimeGamePackReceiptSchema,
);
export const RUNTIME_GAME_PACK_RECEIPT_FORMAT =
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA.properties.format.const;
export const RUNTIME_GAME_PACK_RECEIPT_FORMAT_VERSION =
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA.properties.formatVersion.const;
export const RUNTIME_GAME_PACK_RECEIPT_SCHEMA_ID =
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA.$id;

export const CANONICAL_JSON_PROFILE =
  RUNTIME_GAME_PACK_SCHEMA.properties.canonicalization.const;
export const GAME_PACK_COMPILER_ID =
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA.$defs.compiler.properties.id.const;
export const GAME_PACK_COMPILER_VERSION =
  RUNTIME_GAME_PACK_RECEIPT_SCHEMA.$defs.compiler.properties.version.const;

export class CanonicalJsonValueError extends TypeError {
  constructor() {
    super("CANONICAL_JSON_VALUE_INVALID");
    this.name = "CanonicalJsonValueError";
    this.code = "CANONICAL_JSON_VALUE_INVALID";
  }
}

export class CanonicalJsonOperationalError extends Error {
  constructor() {
    super("CANONICAL_JSON_INTERNAL_ERROR");
    this.name = "CanonicalJsonOperationalError";
    this.code = "CANONICAL_JSON_INTERNAL_ERROR";
  }
}

function invalidValue() {
  throw new CanonicalJsonValueError();
}

function hasWellFormedUtf16(value) {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const following = value.charCodeAt(index + 1);
      if (!(following >= 0xdc00 && following <= 0xdfff)) {
        return false;
      }
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function quoteString(value) {
  if (!hasWellFormedUtf16(value)) {
    invalidValue();
  }
  const encoded = JSON.stringify(value);
  if (typeof encoded !== "string") {
    throw new CanonicalJsonOperationalError();
  }
  return encoded;
}

function inspectObject(value) {
  let prototype;
  let descriptors;
  let descriptorKeys;
  try {
    prototype = Object.getPrototypeOf(value);
    descriptors = Object.getOwnPropertyDescriptors(value);
    descriptorKeys = Reflect.ownKeys(descriptors);
  } catch {
    throw new CanonicalJsonOperationalError();
  }
  if (descriptorKeys.some((key) => typeof key === "symbol")) {
    invalidValue();
  }
  return { descriptorKeys, descriptors, prototype };
}

function assertDataDescriptor(descriptor, enumerable) {
  if (
    !descriptor ||
    !("value" in descriptor) ||
    descriptor.enumerable !== enumerable
  ) {
    invalidValue();
  }
}

function compareUtf16CodeUnits(left, right) {
  if (left === right) {
    return 0;
  }
  return left < right ? -1 : 1;
}

function serializeArray(value, depth, activeValues) {
  const { descriptorKeys, descriptors, prototype } = inspectObject(value);
  if (prototype !== Array.prototype) {
    invalidValue();
  }

  const lengthDescriptor = descriptors.length;
  assertDataDescriptor(lengthDescriptor, false);
  const length = lengthDescriptor.value;
  if (
    !Number.isSafeInteger(length) ||
    length < 0 ||
    descriptorKeys.length !== length + 1
  ) {
    invalidValue();
  }

  const parts = new Array(length);
  for (let index = 0; index < length; index += 1) {
    const descriptor = descriptors[String(index)];
    assertDataDescriptor(descriptor, true);
    parts[index] = serializeValue(
      descriptor.value,
      depth + 1,
      activeValues,
    );
  }
  return `[${parts.join(",")}]`;
}

function serializeRecord(value, depth, activeValues) {
  const { descriptorKeys, descriptors, prototype } = inspectObject(value);
  if (prototype !== Object.prototype && prototype !== null) {
    invalidValue();
  }

  const keys = descriptorKeys.sort(compareUtf16CodeUnits);
  const parts = [];
  for (const key of keys) {
    const descriptor = descriptors[key];
    assertDataDescriptor(descriptor, true);
    parts.push(
      `${quoteString(key)}:${serializeValue(
        descriptor.value,
        depth + 1,
        activeValues,
      )}`,
    );
  }
  return `{${parts.join(",")}}`;
}

function serializeValue(value, depth, activeValues) {
  if (depth > MAX_CANONICAL_DEPTH) {
    invalidValue();
  }
  if (value === null) {
    return "null";
  }

  switch (typeof value) {
    case "boolean":
      return value ? "true" : "false";
    case "number":
      if (!Number.isSafeInteger(value)) {
        invalidValue();
      }
      return Object.is(value, -0) ? "0" : String(value);
    case "string":
      return quoteString(value);
    case "object":
      break;
    default:
      invalidValue();
  }

  if (activeValues.has(value)) {
    invalidValue();
  }
  activeValues.add(value);
  try {
    return Array.isArray(value)
      ? serializeArray(value, depth, activeValues)
      : serializeRecord(value, depth, activeValues);
  } finally {
    activeValues.delete(value);
  }
}

export function canonicalizeJsonValue(value) {
  try {
    return serializeValue(value, 0, new WeakSet());
  } catch (error) {
    if (
      error instanceof CanonicalJsonValueError ||
      error instanceof CanonicalJsonOperationalError
    ) {
      throw error;
    }
    throw new CanonicalJsonOperationalError();
  }
}
