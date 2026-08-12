import { validateGlbBuffer } from "../../../scripts/lib/scene-pack-bundle-core.mjs";

const PNG_SIGNATURE = Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10);

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function ascii(bytes) {
  return String.fromCharCode(...bytes);
}

export function inspectPanoramaPng(bytes, limits) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 57 || bytes.byteLength > limits.panoramaBytes) {
    return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
  }
  for (let index = 0; index < PNG_SIGNATURE.length; index += 1) {
    if (bytes[index] !== PNG_SIGNATURE[index]) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 8;
  let width = null;
  let height = null;
  let sawIdat = false;
  let sawIend = false;
  let chunkIndex = 0;
  while (offset < bytes.byteLength) {
    if (offset + 12 > bytes.byteLength) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
    const length = view.getUint32(offset, false);
    if (length > limits.panoramaBytes || offset + 12 + length > bytes.byteLength) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
    const typeBytes = bytes.subarray(offset + 4, offset + 8);
    const type = ascii(typeBytes);
    if (!/^[A-Za-z]{4}$/u.test(type)) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
    const payload = bytes.subarray(offset + 8, offset + 8 + length);
    const crcInput = new Uint8Array(4 + length);
    crcInput.set(typeBytes, 0);
    crcInput.set(payload, 4);
    if (crc32(crcInput) !== view.getUint32(offset + 8 + length, false)) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
    if (chunkIndex === 0) {
      if (type !== "IHDR" || length !== 13) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
      width = view.getUint32(offset + 8, false);
      height = view.getUint32(offset + 12, false);
      const bitDepth = bytes[offset + 16];
      const colorType = bytes[offset + 17];
      const validDepth = new Set([0, 2, 3, 4, 6]);
      if (
        !validDepth.has(colorType) ||
        ![1, 2, 4, 8, 16].includes(bitDepth) ||
        (colorType === 2 && ![8, 16].includes(bitDepth)) ||
        (colorType === 3 && ![1, 2, 4, 8].includes(bitDepth)) ||
        ([4, 6].includes(colorType) && ![8, 16].includes(bitDepth)) ||
        bytes[offset + 18] !== 0 || bytes[offset + 19] !== 0 || bytes[offset + 20] !== 0
      ) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
    } else if (type === "IHDR") return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
    if (type === "IDAT") sawIdat = true;
    if (type === "IEND") {
      if (length !== 0 || !sawIdat) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_INVALID" });
      sawIend = true;
      offset += 12;
      break;
    }
    if (typeBytes[0] >= 65 && typeBytes[0] <= 90 && !["IHDR", "PLTE", "IDAT", "IEND"].includes(type)) {
      return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_FEATURE_UNSUPPORTED" });
    }
    offset += 12 + length;
    chunkIndex += 1;
  }
  if (
    !sawIend || offset !== bytes.byteLength ||
    !Number.isSafeInteger(width) || !Number.isSafeInteger(height) ||
    width < 2 || height < 1 || width > limits.panoramaWidth || height > limits.panoramaHeight || width !== height * 2
  ) return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_PANORAMA_DIMENSIONS_INVALID" });
  return Object.freeze({ ok: true, width, height });
}

export function inspectEnvironmentCollider(bytes, limits) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 20 || bytes.byteLength > limits.colliderBytes) {
    return Object.freeze({ ok: false, code: "PROTOTYPE_ENVIRONMENT_COLLIDER_INVALID" });
  }
  const inspected = validateGlbBuffer(bytes);
  if (!inspected.ok) return Object.freeze({ ok: false, code: inspected.code });
  return Object.freeze({
    ok: true,
    metrics: Object.freeze({
      nodeCount: inspected.summary.nodes,
      meshCount: inspected.summary.meshes,
      surfaceCount: inspected.summary.surfaces,
      triangleCount: inspected.summary.triangles,
    }),
  });
}
