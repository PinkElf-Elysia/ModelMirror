import { createHash } from "node:crypto";
import { PrototypeSpatialEnvironmentOperationalError } from "./operational.mjs";

export function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value) || value instanceof Uint8Array) return value;
  let values;
  try {
    values = Object.values(value);
  } catch {
    throw new PrototypeSpatialEnvironmentOperationalError();
  }
  for (const child of values) deepFreeze(child);
  return Object.freeze(value);
}

export function captureRecord(value, required) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  let descriptors;
  let prototype;
  try {
    descriptors = Object.getOwnPropertyDescriptors(value);
    prototype = Object.getPrototypeOf(value);
  } catch {
    throw new PrototypeSpatialEnvironmentOperationalError();
  }
  if (prototype !== Object.prototype && prototype !== null) return null;
  const keys = Reflect.ownKeys(descriptors);
  if (keys.length !== required.length || keys.some((key) => typeof key !== "string" || !required.includes(key))) return null;
  const output = Object.create(null);
  for (const key of required) {
    const descriptor = descriptors[key];
    if (!descriptor || !descriptor.enumerable || descriptor.get !== undefined || descriptor.set !== undefined || !Object.hasOwn(descriptor, "value")) return null;
    output[key] = descriptor.value;
  }
  return output;
}

export function captureIntegerVector(value, { minimum, maximum }) {
  if (!Array.isArray(value)) return null;
  let descriptors;
  try {
    descriptors = Object.getOwnPropertyDescriptors(value);
  } catch {
    throw new PrototypeSpatialEnvironmentOperationalError();
  }
  if (descriptors.length?.value !== 3 || Reflect.ownKeys(descriptors).some((key) => !["0", "1", "2", "length"].includes(String(key)))) return null;
  const output = [];
  for (const key of ["0", "1", "2"]) {
    const descriptor = descriptors[key];
    const scalar = descriptor?.value;
    if (!descriptor?.enumerable || descriptor.get !== undefined || descriptor.set !== undefined || !Number.isSafeInteger(scalar) || scalar < minimum || scalar > maximum) return null;
    output.push(Object.is(scalar, -0) ? 0 : scalar);
  }
  return output;
}

export function copyBytes(value, maximum) {
  if (!(value instanceof Uint8Array)) return null;
  let copied;
  try {
    copied = Uint8Array.prototype.slice.call(value);
  } catch {
    throw new PrototypeSpatialEnvironmentOperationalError();
  }
  return copied.byteLength >= 1 && copied.byteLength <= maximum ? copied : null;
}

export function copyFiles(value) {
  if (!(value instanceof Map)) return null;
  const output = new Map();
  try {
    for (const [key, bytes] of Map.prototype.entries.call(value)) {
      if (typeof key !== "string" || !(bytes instanceof Uint8Array) || output.has(key)) return null;
      output.set(key, Uint8Array.prototype.slice.call(bytes));
    }
  } catch {
    throw new PrototypeSpatialEnvironmentOperationalError();
  }
  return output;
}

export function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

export function parseCanonical(text, maximum, canonicalizeJsonValue) {
  if (typeof text !== "string" || new TextEncoder().encode(text).byteLength > maximum) return null;
  try {
    const value = JSON.parse(text);
    return canonicalizeJsonValue(value) === text ? value : null;
  } catch {
    return null;
  }
}
