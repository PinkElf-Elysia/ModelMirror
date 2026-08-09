const MAX_CAPTURE_DEPTH = 256;

export function canonicalScalar(value) {
  return typeof value === "number" && Object.is(value, -0) ? 0 : value;
}

function captureValue(input, active, depth) {
  if (depth > MAX_CAPTURE_DEPTH) {
    return { ok: false };
  }
  if (input === null || typeof input === "string" || typeof input === "boolean") {
    return { ok: true, value: input };
  }
  if (typeof input === "number") {
    return Number.isFinite(input)
      ? { ok: true, value: canonicalScalar(input) }
      : { ok: false };
  }
  if (typeof input !== "object" || active.has(input)) {
    return { ok: false };
  }

  active.add(input);
  try {
    const descriptors = Object.getOwnPropertyDescriptors(input);
    const descriptorKeys = Reflect.ownKeys(descriptors);
    if (descriptorKeys.some((key) => typeof key === "symbol")) {
      return { ok: false };
    }
    if (Array.isArray(input)) {
      const lengthDescriptor = descriptors.length;
      if (
        !lengthDescriptor ||
        !("value" in lengthDescriptor) ||
        !Number.isSafeInteger(lengthDescriptor.value) ||
        lengthDescriptor.value < 0
      ) {
        return { ok: false };
      }
      const length = lengthDescriptor.value;
      const keys = descriptorKeys.filter((key) => key !== "length");
      if (keys.length !== length) {
        return { ok: false };
      }
      const output = [];
      for (let index = 0; index < length; index += 1) {
        const descriptor = descriptors[String(index)];
        if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) {
          return { ok: false };
        }
        const captured = captureValue(descriptor.value, active, depth + 1);
        if (!captured.ok) {
          return captured;
        }
        output.push(captured.value);
      }
      return { ok: true, value: output };
    }

    const prototype = Object.getPrototypeOf(input);
    if (prototype !== Object.prototype && prototype !== null) {
      return { ok: false };
    }
    const output = Object.create(null);
    for (const key of descriptorKeys) {
      const descriptor = descriptors[key];
      if (!("value" in descriptor) || !descriptor.enumerable) {
        return { ok: false };
      }
      const captured = captureValue(descriptor.value, active, depth + 1);
      if (!captured.ok) {
        return captured;
      }
      output[key] = captured.value;
    }
    return { ok: true, value: output };
  } catch {
    return { ok: false };
  } finally {
    active.delete(input);
  }
}

export function captureJsonValue(input) {
  return captureValue(input, new WeakSet(), 1);
}

export function deepFreeze(value, seen = new WeakSet()) {
  if (value === null || typeof value !== "object" || seen.has(value)) {
    return value;
  }
  seen.add(value);
  for (const key of Reflect.ownKeys(value)) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor && "value" in descriptor) {
      deepFreeze(descriptor.value, seen);
    }
  }
  return Object.freeze(value);
}

export function hasExactKeys(value, expected) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) =>
    Object.hasOwn(value, key)
  );
}
