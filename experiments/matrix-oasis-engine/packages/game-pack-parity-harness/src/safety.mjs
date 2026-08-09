const MAX_CAPTURE_DEPTH = 256;

function captureValue(input, active, depth) {
  if (depth > MAX_CAPTURE_DEPTH) {
    return { ok: false };
  }
  if (
    input === null ||
    typeof input === "string" ||
    typeof input === "boolean" ||
    (typeof input === "number" && Number.isFinite(input))
  ) {
    return {
      ok: true,
      value: typeof input === "number" && Object.is(input, -0) ? 0 : input,
    };
  }
  if (typeof input !== "object" || active.has(input)) {
    return { ok: false };
  }
  active.add(input);
  try {
    const descriptors = Object.getOwnPropertyDescriptors(input);
    const keys = Reflect.ownKeys(descriptors);
    if (keys.some((key) => typeof key === "symbol")) {
      return { ok: false };
    }
    if (Array.isArray(input)) {
      const lengthDescriptor = descriptors.length;
      if (
        !lengthDescriptor ||
        !("value" in lengthDescriptor) ||
        !Number.isSafeInteger(lengthDescriptor.value) ||
        lengthDescriptor.value < 0 ||
        keys.filter((key) => key !== "length").length !== lengthDescriptor.value
      ) {
        return { ok: false };
      }
      const output = [];
      for (let index = 0; index < lengthDescriptor.value; index += 1) {
        const descriptor = descriptors[String(index)];
        if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) {
          return { ok: false };
        }
        const child = captureValue(descriptor.value, active, depth + 1);
        if (!child.ok) {
          return child;
        }
        output.push(child.value);
      }
      return { ok: true, value: output };
    }
    const prototype = Object.getPrototypeOf(input);
    if (prototype !== Object.prototype && prototype !== null) {
      return { ok: false };
    }
    const output = Object.create(null);
    for (const key of keys) {
      const descriptor = descriptors[key];
      if (!("value" in descriptor) || !descriptor.enumerable) {
        return { ok: false };
      }
      const child = captureValue(descriptor.value, active, depth + 1);
      if (!child.ok) {
        return child;
      }
      output[key] = child.value;
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

export function cloneFrozen(value) {
  const captured = captureJsonValue(value);
  return captured.ok ? deepFreeze(captured.value) : undefined;
}

export function jsonEqual(left, right) {
  const capturedLeft = captureJsonValue(left);
  const capturedRight = captureJsonValue(right);
  return capturedLeft.ok &&
    capturedRight.ok &&
    JSON.stringify(capturedLeft.value) === JSON.stringify(capturedRight.value);
}
