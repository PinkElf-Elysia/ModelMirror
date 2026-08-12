import { GLB_BUFFER, Logger, NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import {
  getBounds,
  getGLPrimitiveCount,
  prune,
  simplify,
  weld,
} from "@gltf-transform/functions";
import { getNodeValue, parseTree } from "jsonc-parser";
import { MeshoptSimplifier } from "meshoptimizer";
import sharp from "sharp";

const GLB_MAGIC = 0x46546c67;
const JSON_CHUNK = 0x4e4f534a;
const BIN_CHUNK = 0x004e4942;
const MAX_RAW_BYTES = 128 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 32 * 1024 * 1024;
const MAX_RAW_TRIANGLES = 1_000_000;
const MAX_NODES = 256;
const MAX_MESHES = 64;
const MAX_SURFACES = 128;
const MAX_TEXTURE_PIXELS = 16 * 1024 * 1024;
const MAX_TEXTURE_DIMENSION = 2048;
const ALLOWED_EXTENSIONS = new Set([
  "KHR_materials_anisotropy",
  "KHR_materials_clearcoat",
  "KHR_materials_diffuse_transmission",
  "KHR_materials_dispersion",
  "KHR_materials_emissive_strength",
  "KHR_materials_ior",
  "KHR_materials_iridescence",
  "KHR_materials_pbrSpecularGlossiness",
  "KHR_materials_sheen",
  "KHR_materials_specular",
  "KHR_materials_transmission",
  "KHR_materials_unlit",
  "KHR_materials_variants",
  "KHR_materials_volume",
  "KHR_mesh_quantization",
  "KHR_texture_transform",
]);

sharp.cache(false);
sharp.concurrency(1);

function resultError(code) {
  return Object.freeze({ ok: false, code });
}

function rawDepthWithinLimit(text, maximum = 256) {
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (const character of text) {
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') inString = true;
    else if (character === "{" || character === "[") {
      depth += 1;
      if (depth > maximum) return false;
    } else if (character === "}" || character === "]") depth -= 1;
  }
  return depth === 0 && !inString;
}

function hasDuplicateKeys(root) {
  const pending = [root];
  while (pending.length > 0) {
    const node = pending.pop();
    if (node.type === "object") {
      const seen = new Set();
      for (const property of node.children ?? []) {
        const key = property.children?.[0]?.value;
        if (typeof key !== "string" || seen.has(key)) return true;
        seen.add(key);
      }
    }
    for (const child of node.children ?? []) pending.push(child);
  }
  return false;
}

function parseJsonChunk(chunk) {
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(chunk);
  } catch {
    return null;
  }
  if (/\u0000/u.test(text)) return null;
  text = text.replace(/ +$/u, "");
  if (!rawDepthWithinLimit(text)) return null;
  const errors = [];
  const tree = parseTree(text, errors, {
    allowEmptyContent: false,
    allowTrailingComma: false,
    disallowComments: true,
  });
  if (!tree || errors.length > 0 || hasDuplicateKeys(tree)) return null;
  return getNodeValue(tree);
}

function inspectUris(value, allowedExternalResources) {
  const pending = [value];
  while (pending.length > 0) {
    const current = pending.pop();
    if (!current || typeof current !== "object") continue;
    if (Object.hasOwn(current, "uri")) {
      if (
        typeof current.uri !== "string" ||
        !allowedExternalResources.has(current.uri)
      ) {
        return false;
      }
    }
    for (const child of Object.values(current)) pending.push(child);
  }
  return true;
}

function inspectJson(json, binaryLength, allowedExternalResources) {
  if (!json || typeof json !== "object" || Array.isArray(json)) {
    return resultError("PROTOTYPE_ASSET_GLB_INVALID");
  }
  if (json.asset?.version !== "2.0") {
    return resultError("PROTOTYPE_ASSET_GLB_INVALID");
  }
  if (
    (json.animations?.length ?? 0) > 0 ||
    (json.skins?.length ?? 0) > 0 ||
    (json.cameras?.length ?? 0) > 0 ||
    (json.extensionsRequired?.length ?? 0) > 0 ||
    json.extensions?.KHR_lights_punctual ||
    (json.nodes ?? []).some((node) =>
      Object.hasOwn(node, "camera") ||
      Object.hasOwn(node, "skin") ||
      node.extensions?.KHR_lights_punctual,
    )
  ) {
    return resultError("PROTOTYPE_ASSET_GLB_FEATURE_UNSUPPORTED");
  }
  if (
    !Array.isArray(json.extensionsUsed ?? []) ||
    (json.extensionsUsed ?? []).some((name) => !ALLOWED_EXTENSIONS.has(name))
  ) {
    return resultError("PROTOTYPE_ASSET_GLB_EXTENSION_UNSUPPORTED");
  }
  if (!inspectUris(json, allowedExternalResources)) {
    return resultError("PROTOTYPE_ASSET_GLB_EXTERNAL_URI");
  }
  const buffers = json.buffers ?? [];
  if (
    !Array.isArray(buffers) ||
    buffers.length !== 1 ||
    Object.hasOwn(buffers[0], "uri") ||
    !Number.isSafeInteger(buffers[0].byteLength) ||
    buffers[0].byteLength < 1 ||
    binaryLength < buffers[0].byteLength ||
    binaryLength - buffers[0].byteLength > 3
  ) {
    return resultError("PROTOTYPE_ASSET_GLB_INVALID");
  }
  const nodes = json.nodes ?? [];
  const meshes = json.meshes ?? [];
  const accessors = json.accessors ?? [];
  if (!Array.isArray(nodes) || !Array.isArray(meshes) || !Array.isArray(accessors)) {
    return resultError("PROTOTYPE_ASSET_GLB_INVALID");
  }
  let surfaces = 0;
  let triangles = 0;
  for (const mesh of meshes) {
    if (!Array.isArray(mesh?.primitives)) {
      return resultError("PROTOTYPE_ASSET_GLB_INVALID");
    }
    for (const primitive of mesh.primitives) {
      surfaces += 1;
      if ((primitive.mode ?? 4) !== 4) {
        return resultError("PROTOTYPE_ASSET_GLB_FEATURE_UNSUPPORTED");
      }
      const accessorIndex = primitive.indices ?? primitive.attributes?.POSITION;
      const count = Number.isSafeInteger(accessorIndex)
        ? accessors[accessorIndex]?.count
        : null;
      if (!Number.isSafeInteger(count) || count < 3 || count % 3 !== 0) {
        return resultError("PROTOTYPE_ASSET_GLB_INVALID");
      }
      triangles += count / 3;
    }
  }
  if (
    nodes.length < 1 ||
    meshes.length < 1 ||
    surfaces < 1 ||
    nodes.length > MAX_NODES ||
    meshes.length > MAX_MESHES ||
    surfaces > MAX_SURFACES ||
    triangles > MAX_RAW_TRIANGLES
  ) {
    return resultError("PROTOTYPE_ASSET_GLB_COMPLEXITY_LIMIT");
  }
  if (
    !Array.isArray(json.scenes) ||
    json.scenes.length !== 1 ||
    !Number.isSafeInteger(json.scene ?? 0)
  ) {
    return resultError("PROTOTYPE_ASSET_GLB_SCENE_UNSUPPORTED");
  }
  for (const image of json.images ?? []) {
    const embedded = Number.isSafeInteger(image?.bufferView) &&
      ["image/png", "image/jpeg"].includes(image.mimeType);
    const approvedExternal = typeof image?.uri === "string" &&
      allowedExternalResources.has(image.uri);
    if (
      !image ||
      typeof image !== "object" ||
      (!embedded && !approvedExternal)
    ) {
      return resultError("PROTOTYPE_ASSET_GLB_TEXTURE_UNSUPPORTED");
    }
  }
  return Object.freeze({ ok: true, json, triangles, surfaces });
}

function parsePrototypeGlb(bytes, externalResources = new Map()) {
  if (
    !(bytes instanceof Uint8Array) ||
    bytes.byteLength < 20 ||
    bytes.byteLength > MAX_RAW_BYTES ||
    !(externalResources instanceof Map)
  ) {
    return resultError("PROTOTYPE_ASSET_GLB_INVALID");
  }
  for (const [uri, resource] of externalResources) {
    if (typeof uri !== "string" || !(resource instanceof Uint8Array)) {
      return resultError("PROTOTYPE_ASSET_GLB_INVALID");
    }
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (
    view.getUint32(0, true) !== GLB_MAGIC ||
    view.getUint32(4, true) !== 2 ||
    view.getUint32(8, true) !== bytes.byteLength
  ) {
    return resultError("PROTOTYPE_ASSET_GLB_INVALID");
  }
  let offset = 12;
  let json = null;
  let binaryLength = null;
  let binary = null;
  while (offset < bytes.byteLength) {
    if (offset + 8 > bytes.byteLength) return resultError("PROTOTYPE_ASSET_GLB_INVALID");
    const length = view.getUint32(offset, true);
    const type = view.getUint32(offset + 4, true);
    offset += 8;
    if (length % 4 !== 0 || offset + length > bytes.byteLength) {
      return resultError("PROTOTYPE_ASSET_GLB_INVALID");
    }
    const chunk = bytes.subarray(offset, offset + length);
    offset += length;
    if (type === JSON_CHUNK && json === null && binaryLength === null) {
      json = parseJsonChunk(chunk);
      if (json === null) return resultError("PROTOTYPE_ASSET_GLB_INVALID");
    } else if (type === BIN_CHUNK && json !== null && binaryLength === null) {
      binaryLength = length;
      binary = new Uint8Array(chunk);
    } else {
      return resultError("PROTOTYPE_ASSET_GLB_INVALID");
    }
  }
  if (offset !== bytes.byteLength || json === null || binaryLength === null || binary === null) {
    return resultError("PROTOTYPE_ASSET_GLB_INVALID");
  }
  const inspected = inspectJson(json, binaryLength, new Set(externalResources.keys()));
  return inspected.ok
    ? { ...inspected, binary }
    : inspected;
}

export function inspectPrototypeGlb(bytes, externalResources = new Map()) {
  const parsed = parsePrototypeGlb(bytes, externalResources);
  return parsed.ok
    ? Object.freeze({ ok: true, triangles: parsed.triangles, surfaces: parsed.surfaces })
    : parsed;
}

function triangleCount(document) {
  let count = 0;
  for (const mesh of document.getRoot().listMeshes()) {
    for (const primitive of mesh.listPrimitives()) count += getGLPrimitiveCount(primitive);
  }
  return count;
}

async function normalizeTextures(document) {
  let maxWidth = 0;
  let maxHeight = 0;
  for (const texture of document.getRoot().listTextures()) {
    const image = texture.getImage();
    const mimeType = texture.getMimeType();
    if (!image || !["image/png", "image/jpeg"].includes(mimeType)) {
      throw new Error("PROTOTYPE_ASSET_GLB_TEXTURE_UNSUPPORTED");
    }
    const pipeline = sharp(image, {
      failOn: "error",
      limitInputPixels: MAX_TEXTURE_PIXELS,
      sequentialRead: true,
    });
    const metadata = await pipeline.metadata();
    if (
      !Number.isSafeInteger(metadata.width) ||
      !Number.isSafeInteger(metadata.height) ||
      metadata.width < 1 ||
      metadata.height < 1 ||
      (metadata.pages ?? 1) !== 1 ||
      !["png", "jpeg"].includes(metadata.format)
    ) {
      throw new Error("PROTOTYPE_ASSET_GLB_TEXTURE_UNSUPPORTED");
    }
    let output = pipeline.resize({
      width: MAX_TEXTURE_DIMENSION,
      height: MAX_TEXTURE_DIMENSION,
      fit: "inside",
      withoutEnlargement: true,
      kernel: "lanczos3",
    });
    output = mimeType === "image/png"
      ? output.png({ compressionLevel: 9, adaptiveFiltering: false, palette: false, progressive: false })
      : output.jpeg({ quality: 90, chromaSubsampling: "4:4:4", progressive: false, optimiseCoding: true });
    const encoded = await output.toBuffer({ resolveWithObject: true });
    texture.setImage(new Uint8Array(encoded.data));
    texture.setMimeType(mimeType);
    maxWidth = Math.max(maxWidth, encoded.info.width);
    maxHeight = Math.max(maxHeight, encoded.info.height);
  }
  return { maxWidth, maxHeight };
}

function scrubMetadata(document) {
  const root = document.getRoot();
  const asset = root.getAsset();
  for (const key of Object.keys(asset)) delete asset[key];
  asset.version = "2.0";
  asset.generator = "Matrix Oasis R9 Normalizer 0.1.0";
  const groups = [
    ["scene", root.listScenes()],
    ["node", root.listNodes()],
    ["mesh", root.listMeshes()],
    ["material", root.listMaterials()],
    ["texture", root.listTextures()],
    ["accessor", root.listAccessors()],
    ["buffer", root.listBuffers()],
  ];
  root.setExtras({});
  for (const [prefix, properties] of groups) {
    properties.forEach((property, index) => {
      property.setName(`${prefix}-${index}`);
      property.setExtras({});
    });
  }
}

function normalizeSceneTransform(document, kind) {
  const root = document.getRoot();
  const scene = root.listScenes()[0];
  const bounds = getBounds(scene);
  const size = bounds.max.map((value, index) => value - bounds.min[index]);
  if (size.some((value) => !Number.isFinite(value) || value < 0)) {
    throw new Error("PROTOTYPE_ASSET_GLB_BOUNDS_INVALID");
  }
  const basis = kind === "character-placeholder"
    ? size[1]
    : Math.max(size[0], size[1], size[2]);
  if (basis <= 0) {
    throw new Error("PROTOTYPE_ASSET_GLB_BOUNDS_INVALID");
  }
  const target = kind === "character-placeholder" ? 1.75 : 1;
  const scale = target / basis;
  const wrapper = document.createNode("normalization-root");
  for (const child of [...scene.listChildren()]) wrapper.addChild(child);
  wrapper.setScale([scale, scale, scale]);
  wrapper.setTranslation([
    -((bounds.min[0] + bounds.max[0]) / 2) * scale,
    -bounds.min[1] * scale,
    -((bounds.min[2] + bounds.max[2]) / 2) * scale,
  ]);
  scene.addChild(wrapper);
}

function metricBounds(document) {
  const bounds = getBounds(document.getRoot().listScenes()[0]);
  const convert = (value) => {
    const rounded = Math.round(value * 1000);
    if (!Number.isSafeInteger(rounded) || Math.abs(rounded) > 1_000_000) {
      throw new Error("PROTOTYPE_ASSET_GLB_BOUNDS_INVALID");
    }
    return Object.is(rounded, -0) ? 0 : rounded;
  };
  return {
    min: bounds.min.map(convert),
    max: bounds.max.map(convert),
  };
}

async function simplifyTo(document, maximum) {
  let current = triangleCount(document);
  if (current <= maximum) return current;
  await MeshoptSimplifier.ready;
  for (let attempt = 0; attempt < 3 && current > maximum; attempt += 1) {
    await document.transform(
      weld({ tolerance: 0.0001 }),
      simplify({
        simplifier: MeshoptSimplifier,
        ratio: Math.max(0.001, maximum / current),
        error: 1,
        lockBorder: false,
      }),
    );
    current = triangleCount(document);
  }
  if (current > maximum) throw new Error("PROTOTYPE_ASSET_GLB_SIMPLIFY_LIMIT");
  return current;
}

function stripColliderAppearance(document) {
  const root = document.getRoot();
  for (const mesh of root.listMeshes()) {
    for (const primitive of mesh.listPrimitives()) primitive.setMaterial(null);
  }
  for (const material of [...root.listMaterials()]) material.dispose();
  for (const texture of [...root.listTextures()]) texture.dispose();
}

function stripColliderVertexAttributes(document) {
  for (const mesh of document.getRoot().listMeshes()) {
    for (const primitive of mesh.listPrimitives()) {
      for (const semantic of primitive.listSemantics()) {
        if (semantic !== "POSITION") primitive.setAttribute(semantic, null);
      }
    }
  }
}

export async function normalizePrototypeGlb(
  inputBytes,
  { kind, role, externalResources = new Map() },
) {
  try {
    if (
      !["prop", "character-placeholder", "environment"].includes(kind) ||
      !["visual", "collider"].includes(role)
    ) {
      return resultError("PROTOTYPE_ASSET_NORMALIZATION_REQUEST_INVALID");
    }
    const capturedBytes = new Uint8Array(inputBytes);
    const capturedResources = new Map();
    for (const [uri, value] of externalResources) {
      capturedResources.set(uri, new Uint8Array(value));
    }
    const inspected = parsePrototypeGlb(capturedBytes, capturedResources);
    if (!inspected.ok) return inspected;
    const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).setAllowNetwork(false);
    const resources = { [GLB_BUFFER]: inspected.binary };
    for (const [uri, value] of capturedResources) resources[uri] = value;
    const jsonDocument = { json: inspected.json, resources };
    const document = await io.readJSON(jsonDocument);
    document.setLogger(new Logger(Logger.Verbosity.SILENT));
    if (document.getRoot().listScenes().length !== 1) {
      return resultError("PROTOTYPE_ASSET_GLB_SCENE_UNSUPPORTED");
    }
    const textures = role === "visual"
      ? await normalizeTextures(document)
      : { maxWidth: 0, maxHeight: 0 };
    const triangleMaximum = role === "visual" ? 100_000 : 10_000;
    if (role === "collider") stripColliderVertexAttributes(document);
    await simplifyTo(document, triangleMaximum);
    if (role === "collider") stripColliderAppearance(document);
    await document.transform(prune({ keepAttributes: false, keepLeaves: false, keepSolidTextures: false }));
    normalizeSceneTransform(document, kind);
    scrubMetadata(document);
    const bytes = new Uint8Array(await io.writeBinary(document));
    if (bytes.byteLength > MAX_OUTPUT_BYTES) {
      return resultError("PROTOTYPE_ASSET_GLB_OUTPUT_SIZE_LIMIT");
    }
    const outputInspection = inspectPrototypeGlb(bytes);
    if (!outputInspection.ok) return outputInspection;
    const root = document.getRoot();
    const metrics = Object.freeze({
      nodeCount: root.listNodes().length,
      meshCount: root.listMeshes().length,
      surfaceCount: root.listMeshes().reduce(
        (sum, mesh) => sum + mesh.listPrimitives().length,
        0,
      ),
      triangleCount: triangleCount(document),
      maxTextureWidth: textures.maxWidth,
      maxTextureHeight: textures.maxHeight,
      boundsMm: Object.freeze(metricBounds(document)),
    });
    return Object.freeze({ ok: true, bytes, metrics });
  } catch {
    return resultError("PROTOTYPE_ASSET_NORMALIZATION_FAILED");
  }
}
