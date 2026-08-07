import { appendPointer, makeDiagnostic } from "./diagnostics.mjs";

const MAX_DOCUMENT_DEPTH = 256;

function invalidJsonValue(path) {
  return makeDiagnostic({
    phase: "schema",
    code: "PACK_SCHEMA_NON_JSON_VALUE",
    path,
    message: "Value is not representable as JSON.",
  });
}

function isArrayIndexKey(key, length) {
  if (!/^(?:0|[1-9][0-9]*)$/.test(key)) {
    return false;
  }
  const index = Number(key);
  return Number.isSafeInteger(index) && index >= 0 && index < length;
}

export function normalizeJsonValue(input) {
  const diagnostics = [];
  const active = new WeakSet();

  function clone(value, path, depth) {
    if (depth > MAX_DOCUMENT_DEPTH) {
      diagnostics.push(invalidJsonValue(path));
      return null;
    }
    if (value === null || typeof value === "string" || typeof value === "boolean") {
      return value;
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value)) {
        diagnostics.push(invalidJsonValue(path));
        return null;
      }
      return value;
    }
    if (typeof value !== "object") {
      diagnostics.push(invalidJsonValue(path));
      return null;
    }
    if (active.has(value)) {
      diagnostics.push(invalidJsonValue(path));
      return null;
    }

    active.add(value);
    try {
      const descriptors = Object.getOwnPropertyDescriptors(value);
      const symbolKeys = Object.getOwnPropertySymbols(value);
      if (symbolKeys.length > 0) {
        diagnostics.push(invalidJsonValue(path));
      }

      if (Array.isArray(value)) {
        const keys = Object.keys(descriptors).filter((key) => key !== "length");
        const indexKeys = keys.filter((key) => isArrayIndexKey(key, value.length));
        if (keys.length !== value.length || indexKeys.length !== value.length) {
          diagnostics.push(invalidJsonValue(path));
          return [];
        }
        const output = [];
        for (let index = 0; index < value.length; index += 1) {
          const descriptor = descriptors[index];
          if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) {
            diagnostics.push(invalidJsonValue(appendPointer(path, index)));
            output.push(null);
            continue;
          }
          output.push(clone(descriptor.value, appendPointer(path, index), depth + 1));
        }
        return output;
      }

      const prototype = Object.getPrototypeOf(value);
      if (prototype !== Object.prototype && prototype !== null) {
        diagnostics.push(invalidJsonValue(path));
        return Object.create(null);
      }
      const output = Object.create(null);
      for (const key of Object.keys(descriptors)) {
        const descriptor = descriptors[key];
        const childPath = appendPointer(path, key);
        if (!("value" in descriptor) || !descriptor.enumerable) {
          diagnostics.push(invalidJsonValue(childPath));
          continue;
        }
        output[key] = clone(descriptor.value, childPath, depth + 1);
      }
      return output;
    } finally {
      active.delete(value);
    }
  }

  const value = clone(input, "", 1);
  return { value, diagnostics };
}
