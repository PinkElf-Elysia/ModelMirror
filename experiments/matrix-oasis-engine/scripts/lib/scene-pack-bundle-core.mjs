import { createHash } from "node:crypto";
import { open, lstat, realpath } from "node:fs/promises";
import path from "node:path";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";

const GLB_MAGIC = 0x46546c67;
const JSON_CHUNK = 0x4e4f534a;
const BIN_CHUNK = 0x004e4942;
const STATIC = Object.freeze({phase: "integrity", severity: "error"});
const KENNEY_TEXTURE_URI = "Textures/colormap.png";
const KENNEY_TEXTURE_BYTE_LENGTH = 8706;
const KENNEY_TEXTURE_SHA256 = "0d4947d34ff32acf4a359c7f22ca784e057e7e72f622170a9a77b6fc88fdb70e";
const KENNEY_GLBS = new Set([
  "7dec224fbdd2297524c56fe3b4fa79fe6c5854f4b699a9e2e2c21ce6f008738c",
  "ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8",
  "873232210ff286b26bb6bfc371d3c6c96479a5b667f2927de3bcf06b1114d5af",
  "538dd97f85473999e1e9fe4758dc48daa85a7eed0be50b30c004702ab848f36c",
]);
const KENNEY_FIGURINE_SHA256 =
  "ae0ea82089e66215684b0b2f5a162be9f6c71475085c81c3b80e53abd08b6bd8";

export class SceneBundleOperationalError extends Error { constructor() { super("SCENE_BUNDLE_INTERNAL_ERROR"); this.name = "SceneBundleOperationalError"; this.code = "SCENE_BUNDLE_INTERNAL_ERROR"; } }

function isWithin(root, target) { const relative = path.relative(root, target); return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)); }
function isAbsoluteOnAnyPlatform(value) { return path.isAbsolute(value) || path.win32.isAbsolute(value) || path.posix.isAbsolute(value); }
function isSafeRelativePath(value) {
  if (typeof value !== "string" || value.length === 0 || value.includes("\0") || isAbsoluteOnAnyPlatform(value)) return false;
  const normalized = value.replaceAll("\\", "/");
  return !normalized.split("/").some((part) => part === ".." || part === "");
}
function issue(code, pointer) { return Object.freeze({...STATIC, code, path: pointer, message: code}); }
function bundleReport(diagnostics) { const values = Object.freeze(diagnostics); return Object.freeze({reportVersion: 1, valid: values.length === 0, diagnostics: values}); }
function sameIdentity(left, right) { return left.dev === right.dev && left.ino === right.ino && left.size === right.size; }

async function readStableFile(moduleRoot, requested, maxBytes, pathPointer) {
  if (!isSafeRelativePath(requested)) return {diagnostic: issue("SCENE_PACK_INPUT_PATH_INVALID", pathPointer)};
  const absolute = path.resolve(moduleRoot, requested); if (!isWithin(moduleRoot, absolute)) return {diagnostic: issue("SCENE_PACK_INPUT_PATH_INVALID", pathPointer)};
  try {
    const lexical = await lstat(absolute, {bigint: true}); if (!lexical.isFile() || lexical.isSymbolicLink() || lexical.size < 1n || lexical.size > BigInt(maxBytes)) return {diagnostic: issue("SCENE_PACK_INPUT_FILE_INVALID", pathPointer)};
    const resolved = await realpath(absolute); if (!isWithin(moduleRoot, resolved)) return {diagnostic: issue("SCENE_PACK_INPUT_PATH_INVALID", pathPointer)};
    const handle = await open(absolute, "r");
    try { const before = await handle.stat({bigint: true}); if (!sameIdentity(lexical, before)) return {diagnostic: issue("SCENE_PACK_INPUT_CHANGED", pathPointer)}; const bytes = await handle.readFile(); const after = await handle.stat({bigint: true}); if (!sameIdentity(before, after) || BigInt(bytes.length) !== after.size) return {diagnostic: issue("SCENE_PACK_INPUT_CHANGED", pathPointer)}; return {bytes, absolute, resolved}; } finally { await handle.close(); }
  } catch (error) {
    if (error && typeof error === "object" && ["ENOENT", "ENOTDIR", "EISDIR", "EACCES", "EPERM"].includes(error.code)) return {diagnostic: issue("SCENE_PACK_INPUT_FILE_INVALID", pathPointer)};
    throw error;
  }
}
function fatalUtf8(bytes, pathPointer) { try { return {text: new TextDecoder("utf-8", {fatal: true}).decode(bytes)}; } catch { return {diagnostic: issue("SCENE_PACK_INPUT_UTF8_INVALID", pathPointer)}; } }
function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }

function inspectGlbJson(value, binaryChunkLength, allowedExternalUri, allowAnimations) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.asset?.version !== "2.0") return "SCENE_PACK_GLB_INVALID";
  if ((value.animations?.length ?? 0) > 0 && !allowAnimations) return "SCENE_PACK_GLB_FEATURE_UNSUPPORTED";
  if ((value.skins?.length ?? 0) > 0 || (value.cameras?.length ?? 0) > 0) return "SCENE_PACK_GLB_FEATURE_UNSUPPORTED";
  if ((value.extensionsRequired?.length ?? 0) > 0) return "SCENE_PACK_GLB_FEATURE_UNSUPPORTED";
  const pending = [{value, pointer: ""}];
  while (pending.length) {
    const {value: current, pointer} = pending.pop();
    if (!current || typeof current !== "object") continue;
    if (Object.hasOwn(current, "uri")) {
      const imagePath = /^\/images\/(?:0|[1-9][0-9]*)$/u.test(pointer);
      if (!imagePath || current.uri !== allowedExternalUri) return "SCENE_PACK_GLB_EXTERNAL_URI";
    }
    if (Object.hasOwn(current, "camera") || Object.hasOwn(current, "skin") || Object.hasOwn(current, "KHR_lights_punctual")) return "SCENE_PACK_GLB_FEATURE_UNSUPPORTED";
    for (const [key, child] of Object.entries(current)) pending.push({value: child, pointer: `${pointer}/${key}`});
  }
  const buffers = value.buffers ?? [];
  if (!Array.isArray(buffers) || buffers.length > 1) return "SCENE_PACK_GLB_INVALID";
  if (buffers.length === 0 && binaryChunkLength !== null) return "SCENE_PACK_GLB_INVALID";
  if (buffers.length === 1) {
    const declared = buffers[0]?.byteLength;
    if (!Number.isSafeInteger(declared) || declared < 0 || binaryChunkLength === null || binaryChunkLength < declared || binaryChunkLength - declared > 3) return "SCENE_PACK_GLB_INVALID";
  }
  const nodes = value.nodes?.length ?? 0; const meshes = value.meshes?.length ?? 0; let surfaces = 0; let triangles = 0;
  for (const mesh of value.meshes ?? []) for (const primitive of mesh.primitives ?? []) { surfaces += 1; if ((primitive.mode ?? 4) !== 4) return "SCENE_PACK_GLB_FEATURE_UNSUPPORTED"; const accessorIndex = primitive.indices ?? primitive.attributes?.POSITION; const count = Number.isSafeInteger(accessorIndex) ? value.accessors?.[accessorIndex]?.count : 0; if (!Number.isSafeInteger(count) || count < 0) return "SCENE_PACK_GLB_INVALID"; triangles += Math.floor(count / 3); }
  if (nodes > 256 || meshes > 64 || surfaces > 128 || triangles > 250000) return "SCENE_PACK_GLB_COMPLEXITY_LIMIT";
  return {nodes, meshes, surfaces, triangles};
}

export function validateGlbBuffer(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 20) return {ok: false, code: "SCENE_PACK_GLB_INVALID"};
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(0, true) !== GLB_MAGIC || view.getUint32(4, true) !== 2 || view.getUint32(8, true) !== bytes.byteLength) return {ok: false, code: "SCENE_PACK_GLB_INVALID"};
  let offset = 12; let json = null; let binaryChunkLength = null;
  while (offset < bytes.byteLength) { if (offset + 8 > bytes.byteLength) return {ok: false, code: "SCENE_PACK_GLB_INVALID"}; const length = view.getUint32(offset, true); const type = view.getUint32(offset + 4, true); offset += 8; if (length % 4 !== 0 || offset + length > bytes.byteLength) return {ok: false, code: "SCENE_PACK_GLB_INVALID"}; const chunk = bytes.subarray(offset, offset + length); offset += length; if (json === null && type === JSON_CHUNK) { try { json = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(chunk).replace(/[\u0000 ]+$/u, "")); } catch { return {ok: false, code: "SCENE_PACK_GLB_INVALID"}; } } else if (type === BIN_CHUNK && json !== null && binaryChunkLength === null) binaryChunkLength = length; else return {ok: false, code: "SCENE_PACK_GLB_INVALID"}; }
  if (offset !== bytes.byteLength || json === null) return {ok: false, code: "SCENE_PACK_GLB_INVALID"}; const digest = sha256(bytes); const allowedExternalUri = KENNEY_GLBS.has(digest) ? KENNEY_TEXTURE_URI : null; const inspected = inspectGlbJson(json, binaryChunkLength, allowedExternalUri, digest === KENNEY_FIGURINE_SHA256); return typeof inspected === "string" ? {ok: false, code: inspected} : {ok: true, summary: Object.freeze(inspected)};
}

export async function validateSceneBundle({moduleRoot, scenePath, runtimePackPath, runtimeReceiptPath}) {
  try {
    const root = await realpath(moduleRoot); const inputs = await Promise.all([readStableFile(root, scenePath, 262144, "/scenePack"), readStableFile(root, runtimePackPath, 16777216, "/runtimePack"), readStableFile(root, runtimeReceiptPath, 16384, "/receipt")]);
    const inputDiagnostics = inputs.flatMap((value) => value.diagnostic ? [value.diagnostic] : []); if (inputDiagnostics.length) return bundleReport(inputDiagnostics);
    const decoded = inputs.map((value, index) => fatalUtf8(value.bytes, ["/scenePack", "/runtimePack", "/receipt"][index])); const utf8Diagnostics = decoded.flatMap((value) => value.diagnostic ? [value.diagnostic] : []); if (utf8Diagnostics.length) return bundleReport(utf8Diagnostics);
    const sceneReport = await validateScenePackJson(decoded[0].text, decoded[1].text, decoded[2].text); if (!sceneReport.valid) return sceneReport;
    const scene = JSON.parse(decoded[0].text); const sceneDirectory = path.dirname(inputs[0].absolute); let total = 0; let totalTriangles = 0; const diagnostics = []; const checkedDependencies = new Set();
    for (const [index, asset] of scene.assets.entries()) { const assetPointer = `/scenePack/assets/${index}`; const absolute = path.resolve(sceneDirectory, ...asset.path.split("/")); if (!isWithin(sceneDirectory, absolute)) { diagnostics.push(issue("SCENE_PACK_ASSET_PATH_INVALID", `${assetPointer}/path`)); continue; } let value; try { value = await readStableFile(sceneDirectory, path.relative(sceneDirectory, absolute), 33554432, `${assetPointer}/path`); } catch { diagnostics.push(issue("SCENE_PACK_ASSET_FILE_INVALID", `${assetPointer}/path`)); continue; } if (value.diagnostic) { const code = value.diagnostic.code === "SCENE_PACK_INPUT_PATH_INVALID" ? "SCENE_PACK_ASSET_PATH_INVALID" : value.diagnostic.code === "SCENE_PACK_INPUT_CHANGED" ? "SCENE_PACK_ASSET_CHANGED" : "SCENE_PACK_ASSET_FILE_INVALID"; diagnostics.push(issue(code, `${assetPointer}/path`)); continue; } total += value.bytes.length; if (total > 134217728) { diagnostics.push(issue("SCENE_PACK_ASSET_TOTAL_SIZE_LIMIT", "/scenePack/assets")); break; } const actualSha256 = sha256(value.bytes); if (value.bytes.length !== asset.byteLength) diagnostics.push(issue("SCENE_PACK_ASSET_BYTE_LENGTH_MISMATCH", `${assetPointer}/byteLength`)); if (actualSha256 !== asset.sha256) diagnostics.push(issue("SCENE_PACK_ASSET_SHA256_MISMATCH", `${assetPointer}/sha256`)); const allowedExternalUri = KENNEY_GLBS.has(actualSha256) ? KENNEY_TEXTURE_URI : null; if (allowedExternalUri && !checkedDependencies.has(allowedExternalUri)) { checkedDependencies.add(allowedExternalUri); const dependencyAbsolute = path.resolve(path.dirname(absolute), ...allowedExternalUri.split("/")); let dependency; try { dependency = isWithin(sceneDirectory, dependencyAbsolute) ? await readStableFile(sceneDirectory, path.relative(sceneDirectory, dependencyAbsolute), KENNEY_TEXTURE_BYTE_LENGTH, `${assetPointer}/path`) : {diagnostic: true}; } catch { dependency = {diagnostic: true}; } if (dependency.diagnostic || dependency.bytes.length !== KENNEY_TEXTURE_BYTE_LENGTH || sha256(dependency.bytes) !== KENNEY_TEXTURE_SHA256) diagnostics.push(issue("SCENE_PACK_ASSET_FILE_INVALID", `${assetPointer}/path`)); else total += dependency.bytes.length; } const glb = validateGlbBuffer(value.bytes); if (!glb.ok) diagnostics.push(issue(glb.code, `${assetPointer}/path`)); else { totalTriangles += glb.summary.triangles; if (totalTriangles > 1000000) { diagnostics.push(issue("SCENE_PACK_GLB_TOTAL_TRIANGLE_LIMIT", "/scenePack/assets")); break; } } }
    return bundleReport(diagnostics);
  } catch (error) { if (error instanceof SceneBundleOperationalError) throw error; throw new SceneBundleOperationalError(); }
}
