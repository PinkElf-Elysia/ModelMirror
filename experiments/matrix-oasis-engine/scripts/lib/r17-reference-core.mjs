import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const ROOT = "third-party/v2-qualification-references";
const LOCK = `${ROOT}/reference.lock.json`;
const LOCK_SHA256 = "0104e57fb962705b35bbbba1ca098e272af1e178ff00492f89744385f6c0173f";
const HASH = /^[0-9a-f]{64}$/u;
const GIT_SHA = /^[0-9a-f]{40}$/u;
const ALLOWED_LICENSES = new Set(["MIT", "Apache-2.0", "CC0-1.0"]);

const EXPECTED_FILES = Object.freeze([
  "LICENSES/Beehave-MIT.txt",
  "README.md",
  "ai-town.reference.txt",
  "beehave.reference.txt",
  "concordia.reference.txt",
  "dialogue-manager.reference.txt",
  "generative-agents.reference.txt",
  "graphiti.reference.txt",
  "kenney-animated-characters-retro.reference.txt",
  "letta.reference.txt",
  "limboai.reference.txt",
  "mem0.reference.txt",
  "reference.lock.json",
  "sotopia.reference.txt",
  "tinytroupe.reference.txt",
  "voyager.reference.txt",
  "worldx.reference.txt",
]);

const EXECUTABLES = Object.freeze([
  ["beehave", "773a5f6dd9b3433cdb8735ab35e9043d4cd60674", "50bc821d8b0bd8581b0876307ec976f892f06327", "MIT"],
  ["limboai", "e45e60e976dafab7f2c15cc341ae366e4cf3352b", "c206e59fd2be90b228947d90e62e6821e7112f07", "MIT"],
  ["dialogue-manager", "ffc0011a1a3ea38fc6e65729e5f987d07dac0c88", "b1b655d1737d2ae5fb1d5a9b7f3c0b67a83e7ecf", "MIT"],
  ["mem0", "c427a453a89c5a3fee73cdb2e4c4df6a651e1692", "a5c7228ce2a59c2391b4e0e22dfe7463bff8c4f9", "Apache-2.0"],
  ["letta", "1131535716e8a31c9a437f8695e25ac98f203a24", "8d53781fa7c433a2071b578fcbae67b68063fa10", "Apache-2.0"],
]);

const ARCHITECTURE = Object.freeze([
  ["worldx", "d2fafb5522054d9d6fb94a48941761623a078986", "2e99340d815c3a08b5ac1f3fc3f5f8b6583be58e", "MIT"],
  ["concordia", "44904ecb3ff69a2874aab2b6a1b147db13f745b2", "3323f49851fb2cc3110a4729c2c3a5b9aebb3e3c", "Apache-2.0"],
  ["ai-town", "7b242334bfbfef02f7718bded120d431e8f307df", "0f80a3adf652dfe7d4eb043b153ab3f093afe219", "MIT"],
  ["generative-agents", "fe05a71d3e4ed7d10bf68aa4eda6dd995ec070f4", "549d23994ee499986f5f3b2f33622ff84a01c970", "Apache-2.0"],
  ["tinytroupe", "a6244b358a1fe1c71bf751f7ba0f8dfa368ec5a4", "ba09d5781c1d7a1bdeb6e49437df130f98855ca7", "MIT"],
  ["sotopia", "a0aaafb440e570e5e61b7c44a44e5e417c545383", "9a3830df65379362eb81e0e568d751e950d90afb", "MIT"],
  ["graphiti", "993e081a6d7948a0d8851c12a5fbdbeb49fed862", "b85b98eb8fb0d1c9f0c1f943be86c7d0c5a890ac", "Apache-2.0"],
  ["voyager", "55e45a880755d0c8c66ca7fb5fe7962ac8974f89", "66d7d2d043c5da45e1f8174cf65f953a6a7254f8", "MIT"],
]);

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function exactKeys(value, expected, code = "R17_REFERENCE_SHAPE_INVALID") {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(code);
  const actual = Object.keys(value).sort();
  const keys = [...expected].sort();
  if (actual.length !== keys.length || actual.some((key, index) => key !== keys[index])) fail(code);
}

function sha256(bytes) {
  return crypto.createHash("sha256").update(bytes).digest("hex");
}

function safeModuleFile(moduleRoot, relativePath) {
  if (typeof relativePath !== "string" || path.posix.isAbsolute(relativePath) || relativePath.includes("\\") || relativePath.split("/").includes("..")) fail("R17_REFERENCE_PATH_INVALID");
  const root = path.resolve(moduleRoot);
  const resolved = path.resolve(root, ...relativePath.split("/"));
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) fail("R17_REFERENCE_PATH_INVALID");
  return resolved;
}

function listFiles(root) {
  const result = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) fail("R17_REFERENCE_LINK_FORBIDDEN");
      if (stat.isDirectory()) stack.push(full);
      else if (stat.isFile()) result.push(path.relative(root, full).split(path.sep).join("/"));
      else fail("R17_REFERENCE_FILE_TYPE_FORBIDDEN");
    }
  }
  return result.sort();
}

function validateSourceTuple(tuple) {
  exactKeys(tuple, ["path", "gitBlobSha1", "byteLength", "sha256"]);
  if (typeof tuple.path !== "string" || tuple.path.includes("\\") || tuple.path.split("/").includes("..") || !GIT_SHA.test(tuple.gitBlobSha1) || !Number.isSafeInteger(tuple.byteLength) || tuple.byteLength < 0 || !HASH.test(tuple.sha256)) fail("R17_REFERENCE_SOURCE_INVALID");
}

function validateLocalHash(moduleRoot, relativePath, expectedHash, seen) {
  if (!HASH.test(expectedHash)) fail("R17_REFERENCE_HASH_INVALID");
  const file = safeModuleFile(moduleRoot, relativePath);
  const stat = fs.lstatSync(file);
  if (!stat.isFile() || stat.isSymbolicLink()) fail("R17_REFERENCE_LINK_FORBIDDEN");
  if (sha256(fs.readFileSync(file)) !== expectedHash) fail("R17_REFERENCE_BYTE_DRIFT");
  seen.add(relativePath);
}

function validatePinnedIdentity(actual, expected) {
  if (actual.id !== expected[0] || actual.commit !== expected[1] || actual.gitTreeSha1 !== expected[2] || actual.license !== expected[3]) fail("R17_REFERENCE_IDENTITY_DRIFT");
  if (!GIT_SHA.test(actual.commit) || !GIT_SHA.test(actual.gitTreeSha1) || !ALLOWED_LICENSES.has(actual.license)) fail("R17_REFERENCE_IDENTITY_DRIFT");
}

export function verifyR17References(moduleRoot) {
  const referenceRoot = safeModuleFile(moduleRoot, ROOT);
  const rootStat = fs.lstatSync(referenceRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail("R17_REFERENCE_ROOT_INVALID");
  const files = listFiles(referenceRoot);
  if (JSON.stringify(files) !== JSON.stringify(EXPECTED_FILES)) fail("R17_REFERENCE_FILE_SET_DRIFT");
  if (files.some((file) => /\.(?:exe|dll|so|dylib|wasm|py|js|mjs|cjs|ts|gd|cs|fbx|glb|png)$/iu.test(file))) fail("R17_REFERENCE_EXECUTABLE_FORBIDDEN");

  const lockBytes = fs.readFileSync(safeModuleFile(moduleRoot, LOCK));
  if (sha256(lockBytes) !== LOCK_SHA256) fail("R17_REFERENCE_LOCK_DRIFT");
  let lock;
  try {
    lock = JSON.parse(lockBytes.toString("utf8"));
  } catch {
    fail("R17_REFERENCE_LOCK_INVALID");
  }
  exactKeys(lock, ["schemaVersion", "profile", "runtimeDependency", "trackedCandidateSource", "trackedQualificationEvidence", "r13ReusedReferences", "licenseText", "executableCandidates", "architectureReferences", "animationFixtures", "deferredAlternatives"]);
  if (lock.schemaVersion !== 1 || lock.profile !== "matrix-oasis.v2-qualification-references/1" || lock.runtimeDependency !== false || lock.trackedCandidateSource !== false || lock.trackedQualificationEvidence !== false) fail("R17_REFERENCE_POLICY_INVALID");
  if (!Array.isArray(lock.executableCandidates) || lock.executableCandidates.length !== EXECUTABLES.length || !Array.isArray(lock.architectureReferences) || lock.architectureReferences.length !== ARCHITECTURE.length) fail("R17_REFERENCE_SHAPE_INVALID");

  const seen = new Set();
  validateLocalHash(moduleRoot, lock.r13ReusedReferences.lockPath, lock.r13ReusedReferences.lockSha256, seen);
  validateLocalHash(moduleRoot, lock.licenseText.mitPath, lock.licenseText.mitSha256, seen);
  validateLocalHash(moduleRoot, lock.licenseText.apachePath, lock.licenseText.apacheSha256, seen);

  lock.executableCandidates.forEach((candidate, index) => {
    validatePinnedIdentity(candidate, EXECUTABLES[index]);
    if (!HASH.test(candidate.treeListSha256) || !HASH.test(candidate.sourceArchive?.sha256) || !Number.isSafeInteger(candidate.sourceArchive?.byteLength) || candidate.sourceArchive.byteLength <= 0) fail("R17_REFERENCE_SOURCE_INVALID");
    validateSourceTuple(candidate.upstreamLicense);
    if (!Array.isArray(candidate.keyFiles) || candidate.keyFiles.length < 2) fail("R17_REFERENCE_SOURCE_INVALID");
    candidate.keyFiles.forEach(validateSourceTuple);
    if (candidate.dependencySurface?.containerRequired === true) fail("R17_REFERENCE_CONTAINER_UNAPPROVED");
    validateLocalHash(moduleRoot, candidate.notePath, candidate.noteSha256, seen);
  });

  lock.architectureReferences.forEach((reference, index) => {
    validatePinnedIdentity(reference, ARCHITECTURE[index]);
    if (!HASH.test(reference.archive?.sha256) || !Number.isSafeInteger(reference.archive?.byteLength) || reference.archive.byteLength <= 0 || !/^https:\/\/(?:api\.github\.com|codeload\.github\.com)\//u.test(reference.archive.url)) fail("R17_REFERENCE_SOURCE_INVALID");
    validateSourceTuple(reference.upstreamLicense);
    if (!Array.isArray(reference.upstreamFiles) || reference.upstreamFiles.length < 1) fail("R17_REFERENCE_SOURCE_INVALID");
    reference.upstreamFiles.forEach(validateSourceTuple);
    validateLocalHash(moduleRoot, reference.notePath, reference.noteSha256, seen);
  });

  if (!Array.isArray(lock.animationFixtures) || lock.animationFixtures.length !== 1) fail("R17_REFERENCE_ASSET_POLICY_INVALID");
  const animation = lock.animationFixtures[0];
  if (animation.id !== "kenney-animated-characters-retro" || animation.expectedVersion !== "1.0" || animation.downloadedArchiveReportedVersion !== "1.1" || animation.license !== "CC0-1.0" || animation.sourceStatus !== "deferred-version-and-clip-drift" || JSON.stringify(animation.missingRequiredClips) !== JSON.stringify(["walk", "turn"])) fail("R17_REFERENCE_ASSET_POLICY_INVALID");
  if (!HASH.test(animation.archive?.sha256) || !HASH.test(animation.upstreamLicense?.sha256)) fail("R17_REFERENCE_ASSET_POLICY_INVALID");
  validateLocalHash(moduleRoot, animation.notePath, animation.noteSha256, seen);
  if (JSON.stringify(lock.deferredAlternatives) !== JSON.stringify([{ id: "kaykit-animated-character", reasonCode: "R17_FIXED_ARCHIVE_HASH_REQUIRED" }])) fail("R17_REFERENCE_ASSET_POLICY_INVALID");

  return Object.freeze({
    ok: true,
    profile: lock.profile,
    executableCandidates: lock.executableCandidates.length,
    architectureReferences: lock.architectureReferences.length,
    animationFixtures: lock.animationFixtures.length,
    localPayloadsChecked: seen.size,
    files: files.length,
  });
}
