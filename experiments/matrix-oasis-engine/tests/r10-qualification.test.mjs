import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  executeMarbleQualification,
  parseMarbleQualificationArgs,
} from "../scripts/qualify-marble-environment.mjs";

const TEMP_ROOT = path.resolve(path.parse(process.cwd()).root, "tmp");
const services = { lstat, mkdir, mkdtemp, openFile: open, realpath, rename, rm };
let sequence = 0;

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBytes = new TextEncoder().encode(type);
  const output = new Uint8Array(12 + data.length);
  const view = new DataView(output.buffer);
  view.setUint32(0, data.length, false);
  output.set(typeBytes, 4);
  output.set(data, 8);
  const checked = new Uint8Array(4 + data.length);
  checked.set(typeBytes);
  checked.set(data, 4);
  view.setUint32(8 + data.length, crc32(checked), false);
  return output;
}

function panoramaPng() {
  const header = new Uint8Array(13);
  const view = new DataView(header.buffer);
  view.setUint32(0, 2, false);
  view.setUint32(4, 1, false);
  header.set([8, 2, 0, 0, 0], 8);
  const chunks = [
    Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10),
    pngChunk("IHDR", header),
    pngChunk("IDAT", new Uint8Array(deflateSync(new Uint8Array(7)))),
    pngChunk("IEND", new Uint8Array()),
  ];
  const output = new Uint8Array(chunks.reduce((sum, item) => sum + item.length, 0));
  let offset = 0;
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
  return output;
}

function colliderGlb() {
  const json = { asset: { version: "2.0" }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }], accessors: [{ count: 3 }, { count: 3 }],
    buffers: [{ byteLength: 4 }] };
  const encoded = new TextEncoder().encode(JSON.stringify(json));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + 4);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true); view.setUint32(4, 2, true); view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true); view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength); output.set(encoded, 20);
  view.setUint32(20 + jsonLength, 4, true); view.setUint32(24 + jsonLength, 0x004e4942, true);
  return output;
}

function blueprint() {
  return { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: "neutral-room", contentVersion: "1", title: "Neutral Room",
      environmentPrompt: "A quiet neutral room.", visualStylePrompt: "Readable forms." },
    zones: [{ id: "zone-main", label: "Main", description: "Central zone" }],
    assetBriefs: [{ id: "asset-environment", kind: "environment", prompt: "Neutral room", entityId: null, roles: ["visual", "collider"] }],
    placements: [], nodeBindings: [{ nodeId: "node-entry", zoneId: "zone-main", visiblePlacementIds: [] }] };
}

async function fixture(label) {
  sequence += 1;
  const prototypeDir = await mkdtemp(path.join(TEMP_ROOT, `matrix-oasis-r10-qualification-${label}-input-`));
  const output = path.join(TEMP_ROOT, `matrix-oasis-r10-qualification-${label}-${process.pid}-${sequence}`);
  await writeFile(path.join(prototypeDir, "scene-blueprint.json"), canonicalizeJsonValue(blueprint()), "utf8");
  return { prototypeDir, output };
}

async function cleanup(value) {
  await rm(value.output, { recursive: true, force: true });
  await rm(value.prototypeDir, { recursive: true, force: true });
}

function args(value) {
  return ["--prototype-dir", value.prototypeDir, "--output", value.output, "--acknowledge-external-upload"];
}

function materialization(plan) {
  const panorama = panoramaPng();
  const collider = colliderGlb();
  const bundle = { format: "matrix-oasis.prototype-environment-bundle", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: plan.plan.scene, blueprint: plan.plan.blueprint,
    provider: { id: "world-labs-marble", model: "marble-1.1", environmentPromptSha256: plan.plan.environmentPromptSha256 },
    assets: { panorama: { path: "assets/environment-panorama.png", format: "png", width: 2, height: 1,
      byteLength: panorama.length, sha256: sha256(panorama) }, collider: { path: "assets/environment-collider.glb", format: "glb",
      byteLength: collider.length, sha256: sha256(collider), metrics: { nodeCount: 1, meshCount: 1, surfaceCount: 1, triangleCount: 1 } } } };
  const canonicalBundleJson = canonicalizeJsonValue(bundle);
  const canonicalReportJson = canonicalizeJsonValue({ format: "matrix-oasis.prototype-environment-materialization-report",
    formatVersion: "0.1.0", provider: { id: "world-labs-marble", model: "marble-1.1" },
    bundleSha256: sha256(canonicalBundleJson), counts: { creates: 1, downloads: 2, polls: 2, worldGets: 1 },
    files: [{ path: "assets/environment-panorama.png", byteLength: panorama.length, sha256: sha256(panorama) },
      { path: "assets/environment-collider.glb", byteLength: collider.length, sha256: sha256(collider) }] });
  return Object.freeze({ ok: true, bundle: Object.freeze(bundle), canonicalBundleJson, canonicalReportJson,
    files: Object.freeze([Object.freeze({ path: "assets/environment-panorama.png", bytes: panorama }),
      Object.freeze({ path: "assets/environment-collider.glb", bytes: collider })]) });
}

function pipeline(overrides = {}) {
  return { createMarbleWorldProvider: (config) => Object.freeze({ config }),
    planPrototypeEnvironment: (text) => {
      const value = JSON.parse(text);
      return Object.freeze({ ok: true, plan: Object.freeze({ scene: Object.freeze({ id: value.scene.id, contentVersion: value.scene.contentVersion, title: value.scene.title }),
        blueprint: Object.freeze({ format: value.format, formatVersion: value.formatVersion, canonicalSha256: sha256(text) }),
        environmentPromptSha256: sha256(value.scene.environmentPrompt) }) });
    },
    materializePrototypeEnvironment: async ({ plan }) => materialization(plan), ...overrides };
}

test("qualification arguments bind one existing prototype to one new direct C tmp output", () => {
  const input = path.join(TEMP_ROOT, "input");
  const output = path.join(TEMP_ROOT, "output");
  assert.deepEqual(parseMarbleQualificationArgs(["--prototype-dir", input, "--output", output, "--acknowledge-external-upload"], TEMP_ROOT),
    { prototypeDir: input, output });
  for (const invalid of [[], ["--prototype-dir", input, "--output", output],
    ["--prototype-dir", input, "--output", path.join(input, "nested"), "--acknowledge-external-upload"],
    ["--prototype-dir", input, "--output", output, "--wrong"]]) assert.throws(() => parseMarbleQualificationArgs(invalid, TEMP_ROOT));
});

test("fake qualification publishes only the canonical pair and two validated assets", async () => {
  const value = await fixture("success");
  try {
    const result = await executeMarbleQualification({ args: args(value), tempRoot: TEMP_ROOT,
      environment: { MATRIX_OASIS_MARBLE_API_KEY: "placeholder" }, services, pipeline: pipeline() });
    assert.deepEqual(result, { exitCode: 0, stdout: "MARBLE_ENVIRONMENT_QUALIFIED files=2 polls=2\n", stderr: "" });
    assert.deepEqual((await readdir(value.output)).sort(), ["assets", "prototype-environment-bundle.json", "prototype-environment-report.json"]);
    assert.deepEqual((await readdir(path.join(value.output, "assets"))).sort(), ["environment-collider.glb", "environment-panorama.png"]);
    const allText = `${result.stdout}${await readFile(path.join(value.output, "prototype-environment-report.json"), "utf8")}`;
    for (const forbidden of ["placeholder", "operation-secret", "world-secret", "https://", "A quiet neutral room."]) assert.equal(allText.includes(forbidden), false);
  } finally { await cleanup(value); }
});

test("provider rejection and publication failure leave no candidate output", async () => {
  const rejected = await fixture("rejected");
  const publishFailed = await fixture("publish-failed");
  const leakedReport = await fixture("leaked-report");
  try {
    const rejection = await executeMarbleQualification({ args: args(rejected), tempRoot: TEMP_ROOT,
      environment: { MATRIX_OASIS_MARBLE_API_KEY: "placeholder" }, services,
      pipeline: pipeline({ materializePrototypeEnvironment: async () => ({ ok: false,
        diagnostics: [{ code: "MARBLE_PROVIDER_CREDIT_LIMIT", path: "", message: "private supplier body" }] }) }) });
    assert.deepEqual(rejection, { exitCode: 1, stdout: "", stderr: "MARBLE_PROVIDER_CREDIT_LIMIT\n" });
    await assert.rejects(lstat(rejected.output), { code: "ENOENT" });
    const failed = await executeMarbleQualification({ args: args(publishFailed), tempRoot: TEMP_ROOT,
      environment: { MATRIX_OASIS_MARBLE_API_KEY: "placeholder" }, services: { ...services, rename: async () => { throw new Error("dynamic secret"); } },
      pipeline: pipeline() });
    assert.deepEqual(failed, { exitCode: 2, stdout: "", stderr: "MARBLE_QUALIFICATION_INTERNAL_ERROR\n" });
    await assert.rejects(lstat(publishFailed.output), { code: "ENOENT" });
    const valid = pipeline();
    const malformed = await executeMarbleQualification({ args: args(leakedReport), tempRoot: TEMP_ROOT,
      environment: { MATRIX_OASIS_MARBLE_API_KEY: "placeholder" }, services,
      pipeline: { ...valid, materializePrototypeEnvironment: async ({ plan }) => ({ ...materialization(plan),
        canonicalReportJson: '{"dynamicSecret":"must-not-persist"}' }) } });
    assert.deepEqual(malformed, { exitCode: 2, stdout: "", stderr: "MARBLE_QUALIFICATION_INTERNAL_ERROR\n" });
    await assert.rejects(lstat(leakedReport.output), { code: "ENOENT" });
  } finally { await cleanup(rejected); await cleanup(publishFailed); await cleanup(leakedReport); }
});

test("entrypoint reads one dedicated credential and ordinary verification never performs qualification", async () => {
  const source = await readFile(new URL("../scripts/qualify-marble-environment.mjs", import.meta.url), "utf8");
  const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
  assert.equal(source.includes('"cdn.marble.worldlabs.ai"'), true);
  assert.equal(source.includes('"*.worldlabs.ai"'), false);
  assert.match(source, /MATRIX_OASIS_MARBLE_API_KEY/u);
  for (const forbidden of ["WORLD_LABS_API_KEY", "MATRIX_OASIS_MODEL", "MATRIX_OASIS_MESHY", "LLM_GATEWAY", "OPENROUTER"]) assert.equal(source.includes(forbidden), false);
  assert.equal(packageJson.scripts.verify.includes("qualify:marble-environment"), false);
  assert.equal(packageJson.scripts["verify:r10"].includes("qualify:marble-environment"), false);
});
