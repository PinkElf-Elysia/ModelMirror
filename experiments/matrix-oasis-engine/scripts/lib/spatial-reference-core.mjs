import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const REFERENCE_ROOT = "third-party/spatial-layout-references";
const MANIFEST_PATH = `${REFERENCE_ROOT}/reference.lock.json`;
const HASH = /^[0-9a-f]{64}$/u;
const GIT_SHA = /^[0-9a-f]{40}$/u;

const EXPECTED_FILES = Object.freeze([
  "LICENSES/Apache-2.0.txt",
  "LICENSES/Godogen-MIT.md",
  "README.md",
  "gamecraft-bench.reference.txt",
  "godogen.reference.txt",
  "holodeck.reference.txt",
  "procthor.reference.txt",
  "reference.lock.json",
]);

const EXPECTED_SOURCES = Object.freeze({
  Godogen: Object.freeze({
    repository: "https://github.com/htdt/godogen",
    commit: "05cebffc8b10c5817e8a3db495b82e7b6004ab84",
    license: "MIT",
    files: Object.freeze([
      Object.freeze(["engines/godot.md", "fa06288c88de46147177f0e555e0f95cfa3fa6a1", 5479, "fc3fab633ddbc1efe42b4f8bc8e338624139c112d6bc9ceaaa81f2fc6cef9a3a"]),
      Object.freeze(["prompts/runtime.md", "dee81a39707c694de974b1a274002446389bac94", 1127, "48da31f60531a18273c564d3e0b706ec20d42991a9c37278a41c252f7ef33ad2"]),
    ]),
    upstreamLicense: Object.freeze(["LICENSE.md", "0a0509efca47ba772df917aa81890ac21f5092b2", 1077, "9994c50a3e043728bcf59106d5941c21b06e53dc64977e6bcafe9580a8a5e5d6"]),
  }),
  Holodeck: Object.freeze({
    repository: "https://github.com/allenai/Holodeck",
    commit: "362b8ed948b867b69a72f1f9491f4caa88419bfc",
    license: "Apache-2.0",
    files: Object.freeze([
      Object.freeze(["ai2holodeck/generation/floor_objects.py", "0532cc76288542e8aba135424030165603299576", 69212, "1fd62f7fdabef3987c6eae6115a7cbc6073e9d34e456a86edd3225270dc8da86"]),
    ]),
    upstreamLicense: Object.freeze(["LICENSE", "2cf1c60e32f6b69c67eb30af79005c4b947a78f7", 11372, "91803c2c3b287ae3c6305615d095b67b6626aaea833d34465b3ca125a6aad587"]),
  }),
  ProcTHOR: Object.freeze({
    repository: "https://github.com/allenai/procthor",
    commit: "53d5bd4c8c96a699e6a615dc390abb670cc9d353",
    license: "Apache-2.0",
    files: Object.freeze([
      Object.freeze(["procthor/generation/objects.py", "f61ccdfb9fece8c5a9e4a9f9afe11ad4a6dd44c0", 47276, "9327d922958f645d8b47d0a011d71852df44a36c4e68a2e8810f5767d0de21e4"]),
      Object.freeze(["procthor/generation/agent.py", "88778692fbe9ddd8ca2dd1247492aab0f0c0f548", 1776, "a3a4a7c5611a515f4dccca1ffe4fe1ea7a3aededd36964e8217f6d605da9679a"]),
    ]),
    upstreamLicense: Object.freeze(["LICENSE", "4af27c71ca0190e281a54128a91d793fd643a943", 11354, "b92f2ada1971f0048bbfc25f081649f6c13a42e04aaca2598ac415c3e0005e69"]),
  }),
  "GameCraft-Bench": Object.freeze({
    repository: "https://github.com/FreedomIntelligence/gamecraft-bench",
    commit: "a43347534374df9a0c1a6c001aa9380862783f6d",
    license: "Apache-2.0",
    files: Object.freeze([
      Object.freeze(["gamecraft_bench/verifier/replay.py", "e62ed30459d9cb9c07b2c9411996457249d1b6ff", 22316, "91658c3571d739a1a7eb87419410045eefdbfdcabceb906faabbd77603988a4a"]),
    ]),
    upstreamLicense: Object.freeze(["LICENSE", "d645695673349e3947e8e5ae42332d0ac3164cd7", 11358, "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"]),
  }),
});

const EXACT_REFERENCE_ORDER = Object.freeze(["Godogen", "Holodeck", "ProcTHOR", "GameCraft-Bench"]);

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function exactKeys(value, keys, code) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(code);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) fail(code);
}

function safeModuleFile(moduleRoot, relativePath) {
  if (typeof relativePath !== "string" || path.posix.isAbsolute(relativePath) || relativePath.includes("\\") || relativePath.split("/").includes("..")) fail("SPATIAL_REFERENCE_PATH_INVALID");
  const resolvedRoot = path.resolve(moduleRoot);
  const resolved = path.resolve(resolvedRoot, ...relativePath.split("/"));
  if (resolved !== resolvedRoot && !resolved.startsWith(`${resolvedRoot}${path.sep}`)) fail("SPATIAL_REFERENCE_PATH_INVALID");
  return resolved;
}

function sha256File(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function listFiles(root) {
  const files = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) fail("SPATIAL_REFERENCE_LINK_FORBIDDEN");
      if (stat.isDirectory()) stack.push(full);
      else if (stat.isFile()) files.push(path.relative(root, full).split(path.sep).join("/"));
      else fail("SPATIAL_REFERENCE_FILE_TYPE_FORBIDDEN");
    }
  }
  return files.sort();
}

function assertSourceTuple(actual, expected) {
  exactKeys(actual, ["path", "gitBlobSha1", "byteLength", "sha256"], "SPATIAL_REFERENCE_MANIFEST_SHAPE");
  if (actual.path !== expected[0] || actual.gitBlobSha1 !== expected[1] || actual.byteLength !== expected[2] || actual.sha256 !== expected[3]) fail("SPATIAL_REFERENCE_UPSTREAM_DRIFT");
  if (!GIT_SHA.test(actual.gitBlobSha1) || !HASH.test(actual.sha256)) fail("SPATIAL_REFERENCE_UPSTREAM_DRIFT");
}

export function verifySpatialReferences(moduleRoot) {
  const referenceRoot = safeModuleFile(moduleRoot, REFERENCE_ROOT);
  const rootStat = fs.lstatSync(referenceRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail("SPATIAL_REFERENCE_ROOT_INVALID");
  const files = listFiles(referenceRoot);
  if (JSON.stringify(files) !== JSON.stringify(EXPECTED_FILES)) fail("SPATIAL_REFERENCE_FILE_SET_DRIFT");

  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(safeModuleFile(moduleRoot, MANIFEST_PATH), "utf8"));
  } catch {
    fail("SPATIAL_REFERENCE_MANIFEST_INVALID");
  }
  exactKeys(manifest, ["schemaVersion", "profile", "runtimeDependency", "executable", "references"], "SPATIAL_REFERENCE_MANIFEST_SHAPE");
  if (manifest.schemaVersion !== 1 || manifest.profile !== "matrix-oasis.spatial-layout-references/1" || manifest.runtimeDependency !== false || manifest.executable !== false) fail("SPATIAL_REFERENCE_POLICY_INVALID");
  if (!Array.isArray(manifest.references) || manifest.references.length !== EXACT_REFERENCE_ORDER.length) fail("SPATIAL_REFERENCE_MANIFEST_SHAPE");

  const checkedPaths = new Set();
  for (let index = 0; index < EXACT_REFERENCE_ORDER.length; index += 1) {
    const name = EXACT_REFERENCE_ORDER[index];
    const reference = manifest.references[index];
    const expected = EXPECTED_SOURCES[name];
    exactKeys(reference, ["name", "repository", "commit", "license", "licensePath", "licenseSha256", "notePath", "noteSha256", "upstreamFiles", "upstreamLicense"], "SPATIAL_REFERENCE_MANIFEST_SHAPE");
    if (reference.name !== name || reference.repository !== expected.repository || reference.commit !== expected.commit || reference.license !== expected.license || !GIT_SHA.test(reference.commit)) fail("SPATIAL_REFERENCE_UPSTREAM_DRIFT");
    if (!Array.isArray(reference.upstreamFiles) || reference.upstreamFiles.length !== expected.files.length) fail("SPATIAL_REFERENCE_UPSTREAM_DRIFT");
    reference.upstreamFiles.forEach((source, sourceIndex) => assertSourceTuple(source, expected.files[sourceIndex]));
    assertSourceTuple(reference.upstreamLicense, expected.upstreamLicense);
    for (const [relativePath, expectedHash] of [[reference.notePath, reference.noteSha256], [reference.licensePath, reference.licenseSha256]]) {
      if (!HASH.test(expectedHash)) fail("SPATIAL_REFERENCE_MANIFEST_SHAPE");
      const filePath = safeModuleFile(moduleRoot, relativePath);
      const stat = fs.lstatSync(filePath);
      if (!stat.isFile() || stat.isSymbolicLink()) fail("SPATIAL_REFERENCE_LINK_FORBIDDEN");
      if (sha256File(filePath) !== expectedHash) fail("SPATIAL_REFERENCE_BYTE_DRIFT");
      checkedPaths.add(relativePath);
    }
  }
  return Object.freeze({ ok: true, profile: manifest.profile, references: manifest.references.length, files: files.length, checkedPayloads: checkedPaths.size });
}
