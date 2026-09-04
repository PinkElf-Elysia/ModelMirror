import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const referenceDirectory = path.join(moduleRoot, "third-party", "npc-derived-state-references");
const lockPath = path.join(referenceDirectory, "reference.lock.json");
const expectedLockSha256 = "9fa0daa6aff1ef1beb1422523e884d0b867d2ae38b1d40beee1b20e000faed42";

const expectedReferences = Object.freeze({
  "cognee-v1.5.3": Object.freeze({ repository: "topoteretes/cognee", tag: "v1.5.3", commit: "25200a548fc6d96aa58d5663603f7c4b6b3f7621", gitTreeSha1: "9d7c2ce275c278cdb270feaf6cb40d19bec04467", licenseSpdx: "Apache-2.0", licenseBlobSha1: "fd57f68790eb9919fc622902caf31f831f9c4e8f", reuse: "architecture-reference-only", conclusionCode: "COGNEE_SERVICE_SCOPE_DELETE_RISK" }),
  "graphiti-v0.29.3": Object.freeze({ repository: "getzep/graphiti", tag: "v0.29.3", commit: "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d", gitTreeSha1: "b7f73e12b6a397465579df63c07c79dd2021cf1e", licenseSpdx: "Apache-2.0", licenseBlobSha1: "5feb0d9d299a1107adfa8331306b13cc0eff2d78", reuse: "architecture-reference-only", conclusionCode: "GRAPHITI_SERVICE_TEMPORAL_GRAPH_OVERKILL" }),
  "langmem-0.0.30-source": Object.freeze({ repository: "langchain-ai/langmem", tag: null, commit: "29cbe41e58528f92e9efa773c12e15c47be3808c", gitTreeSha1: "d85d1f815fb2b54bbc0a85c18453b7a7953ca38c", licenseSpdx: "MIT", licenseBlobSha1: "c38f6f284dc464af69e9f618bc0304d299d0bdf0", reuse: "architecture-reference-only", conclusionCode: "LANGMEM_RELEASE_AND_MODEL_DEPENDENCY_GAP" }),
  "letta-code-v0.31.8": Object.freeze({ repository: "letta-ai/letta-code", tag: "v0.31.8", commit: "385aca8f3637839d7716d557499a9056aca4198d", gitTreeSha1: "f0a43b6c8ca3840bf0bbb1611a2b437194366915", licenseSpdx: "Apache-2.0", licenseBlobSha1: "e72f5de5dd5610ee3fee7feb791ebff246cc931e", reuse: "architecture-reference-only", conclusionCode: "LETTA_AGENT_STATE_AUTHORITY_MISMATCH" }),
  "mem0-ts-v3.1.7": Object.freeze({ repository: "mem0ai/mem0", tag: "ts-v3.1.7", commit: "dc82354e143c2581d505d581a00286d6ef8c3605", gitTreeSha1: "071e89855584e397c2ce88e5cce0c510ee648166", licenseSpdx: "Apache-2.0", licenseBlobSha1: "d20d5102c3cf97ecbee54afd65893de4a11d26fe", reuse: "deferred-semantic-retrieval-only", conclusionCode: "MEM0_MODEL_VECTOR_SCOPE_BLOCKED" }),
  "minisearch-v7.2.0": Object.freeze({ repository: "lucaong/minisearch", tag: "v7.2.0", commit: "3d239d1c3ae7aef1bf5d8945dd7b5f0709f646f5", gitTreeSha1: "965c00b7bbece73c81ac23be67e01e4dc6d4f799", licenseSpdx: "MIT", licenseBlobSha1: "30922d0947fbb0be92af59f0f0a7340a222b9eb8", reuse: "trigger-gated-retrieval-only", conclusionCode: "MINISEARCH_RETRIEVAL_NOT_REQUIRED" }),
  "orama-v3.2.0": Object.freeze({ repository: "oramasearch/orama", tag: "v3.2.0", commit: "4e7cbe0de23f2ba239b85d12a03e9e57baee373e", gitTreeSha1: "0bfcaa2421c68ac7c60d6012ac8bbb72ca3dd133", licenseSpdx: "Apache-2.0", licenseBlobSha1: "78cbcbb6875982633e7c4b11ca87633e294b2867", reuse: "trigger-gated-retrieval-only", conclusionCode: "ORAMA_RETRIEVAL_NOT_REQUIRED" }),
});

const blockedDependencyPatterns = Object.freeze([
  /^@letta(?:-ai)?\//u,
  /^@mem0\//u,
  /^@orama\//u,
  /^cognee$/u,
  /^graphiti(?:-core)?$/u,
  /^langmem$/u,
  /^letta$/u,
  /^mem0(?:ai)?$/u,
  /^minisearch$/u,
]);

function assertClosedKeys(value, expected, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(label);
  const actual = Object.keys(value).sort();
  if (actual.join("\0") !== [...expected].sort().join("\0")) throw new Error(label);
}

function assertHex(value, length, label) {
  if (typeof value !== "string" || !new RegExp(`^[0-9a-f]{${length}}$`, "u").test(value)) throw new Error(label);
}

function isBlockedDependency(name) {
  return blockedDependencyPatterns.some((pattern) => pattern.test(name));
}

export function assertR21ReferenceDirectory(fileNames) {
  if (!Array.isArray(fileNames) || [...fileNames].sort().join("\0") !== "reference.lock.json") throw new Error("candidate-artifact");
  return true;
}

export function assertR21ReferenceLock(lock, packageManifestTexts = []) {
  assertClosedKeys(lock, ["format", "formatVersion", "implementationDecision", "policy", "references", "schemaVersion"], "root");
  if (lock.schemaVersion !== 1 || lock.format !== "matrix-oasis.r21-memory-reference-lock" || lock.formatVersion !== "0.1.0" || lock.implementationDecision !== "internal-canonical-reducers-only") throw new Error("identity");
  assertClosedKeys(lock.policy, ["candidateArtifactsCommitted", "newProductionDependencies", "r21CandidateExecutionRequired", "transitiveLicenseClosureQualified"], "policy");
  if (lock.policy.candidateArtifactsCommitted !== false || lock.policy.newProductionDependencies !== 0 || lock.policy.r21CandidateExecutionRequired !== false || lock.policy.transitiveLicenseClosureQualified !== false) throw new Error("policy");
  if (!Array.isArray(lock.references)) throw new Error("references");
  const ids = lock.references.map((entry) => entry?.id);
  if (ids.length !== 7 || new Set(ids).size !== 7 || ids.join("\0") !== [...ids].sort().join("\0")) throw new Error("reference-order");
  for (const entry of lock.references) {
    assertClosedKeys(entry, ["commit", "conclusionCode", "gitTreeSha1", "id", "license", "reEvaluateWhen", "repository", "reuse", "sourceVersion", "tag"], `entry-${entry?.id}`);
    const expected = expectedReferences[entry.id];
    if (!expected) throw new Error("unknown-reference");
    assertClosedKeys(entry.license, ["closure", "evidenceKind", "gitBlobSha1", "path", "spdx"], `license-${entry.id}`);
    assertHex(entry.commit, 40, `commit-${entry.id}`);
    assertHex(entry.gitTreeSha1, 40, `tree-${entry.id}`);
    assertHex(entry.license.gitBlobSha1, 40, `license-blob-${entry.id}`);
    if (typeof entry.sourceVersion !== "string" || entry.sourceVersion.length === 0 || typeof entry.license.path !== "string" || entry.license.path.length === 0 || !["github-license-api", "manual-top-level-license-text"].includes(entry.license.evidenceKind)) throw new Error(`evidence-${entry.id}`);
    if (entry.repository !== expected.repository || entry.tag !== expected.tag || entry.commit !== expected.commit || entry.gitTreeSha1 !== expected.gitTreeSha1 || entry.license.spdx !== expected.licenseSpdx || entry.license.gitBlobSha1 !== expected.licenseBlobSha1 || entry.reuse !== expected.reuse || entry.conclusionCode !== expected.conclusionCode) throw new Error(`drift-${entry.id}`);
    if (entry.license.closure !== "direct-only-transitive-unverified" || entry.reuse === "production-dependency") throw new Error(`boundary-${entry.id}`);
    if (!Array.isArray(entry.reEvaluateWhen) || entry.reEvaluateWhen.length === 0 || new Set(entry.reEvaluateWhen).size !== entry.reEvaluateWhen.length || entry.reEvaluateWhen.join("\0") !== [...entry.reEvaluateWhen].sort().join("\0") || entry.reEvaluateWhen.some((value) => typeof value !== "string" || !/^[A-Z0-9_]+$/u.test(value))) throw new Error(`triggers-${entry.id}`);
  }
  for (const text of packageManifestTexts) {
    const manifest = JSON.parse(text);
    for (const section of ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]) {
      for (const name of Object.keys(manifest[section] ?? {})) if (isBlockedDependency(name)) throw new Error("candidate-dependency");
    }
  }
  return true;
}

function collectWorkspaceManifestTexts() {
  const files = [path.join(moduleRoot, "package.json")];
  for (const workspaceDir of ["apps", "packages"]) {
    const root = path.join(moduleRoot, workspaceDir);
    for (const entry of readdirSync(root, { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
      const manifest = path.join(root, entry.name, "package.json");
      if (existsSync(manifest)) files.push(manifest);
    }
  }
  return files.map((file) => readFileSync(file, "utf8"));
}

function main() {
  try {
    assertR21ReferenceDirectory(readdirSync(referenceDirectory));
    const lockBytes = readFileSync(lockPath);
    if (createHash("sha256").update(lockBytes).digest("hex") !== expectedLockSha256) throw new Error("lock-hash");
    const lock = JSON.parse(lockBytes.toString("utf8"));
    assertR21ReferenceLock(lock, collectWorkspaceManifestTexts());
    console.log(`R21_MEMORY_REFERENCES_OK references=${lock.references.length} productionDependencies=0 lockSha256=${expectedLockSha256}`);
  } catch {
    console.error("R21_MEMORY_REFERENCE_LOCK_INVALID");
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
