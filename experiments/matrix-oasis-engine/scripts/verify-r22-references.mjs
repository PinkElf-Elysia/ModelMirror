import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const referenceDirectory = path.join(moduleRoot, "third-party", "npc-cognition-references");
const lockPath = path.join(referenceDirectory, "reference.lock.json");
const expectedLockSha256 = "cd37e275b8f0527a6ecab8d16ee1822598cbc5121d85d226ac10ea5f3d8e0dba";

const expected = Object.freeze({
  "autogen-python-v0.7.5": Object.freeze({ repository:"microsoft/autogen", tag:"python-v0.7.5", commit:"83afbf5857aac683340d4c692194e548b1e8edda", tree:"09a39a20d6f2b2838ebd59d7e607d00722e22635", license:"MIT", licensePath:"LICENSE-CODE", licenseBlob:"9e841e7a26e4eb057b24511e7b92d42b257a80e5", licenseBytes:1141, licenseSha256:"c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383", reuse:"rejected-production-dependency", conclusion:"AUTOGEN_SCOPE_AND_MAINTENANCE_MISMATCH" }),
  "camel-v0.2.90": Object.freeze({ repository:"camel-ai/camel", tag:"v0.2.90", tagObjectSha1:"a3a21ef8647e6a05b431d25320116d5ce568bfd6", commit:"deb286f36702ab15a2cb890c6e223a79e4ce4284", tree:"a461696702c2450d4dd0ef2af0d08807721e0f37", license:"Apache-2.0", licensePath:"LICENSE", licenseBlob:"c46213dce5875ed087fc9fe3ce329b69d98713ed", licenseBytes:11344, licenseSha256:"950deb34b1341a0ac95236fae92fe247c318c3a83a62c9ebacbe1882530ab1f6", reuse:"deferred-architecture-reference", conclusion:"CAMEL_MULTI_AGENT_SURFACE_OUT_OF_SCOPE" }),
  "dialogue-manager-v3.10.4": Object.freeze({ repository:"nathanhoad/godot_dialogue_manager", tag:"v3.10.4", commit:"5487c524b9eac303b059e5859e2980b134c59d79", tree:"e232fb1bf4e584da9816c94c495c13195fe41923", license:"MIT", licensePath:"LICENSE", licenseBlob:"41671396f3920d148a81b93c6866a14982315815", licenseBytes:1111, licenseSha256:"21c98fa971b5dd23754a5974901df83c83aaebc351c4f4db60e5615f0571ed8b", reuse:"trusted-authoring-presentation-backup-only", conclusion:"DIALOGUE_MANAGER_MODEL_TEXT_EXECUTION_SURFACE" }),
  "dialogue-manager-v4.0.3": Object.freeze({ repository:"nathanhoad/godot_dialogue_manager", tag:"v4.0.3", commit:"ffc0011a1a3ea38fc6e65729e5f987d07dac0c88", tree:"b1b655d1737d2ae5fb1d5a9b7f3c0b67a83e7ecf", license:"MIT", licensePath:"LICENSE", licenseBlob:"41671396f3920d148a81b93c6866a14982315815", licenseBytes:1111, licenseSha256:"21c98fa971b5dd23754a5974901df83c83aaebc351c4f4db60e5615f0571ed8b", reuse:"incompatible-version-reference-only", conclusion:"DIALOGUE_MANAGER_GODOT_4_7_ONLY" }),
  "langgraph-v1.0.5": Object.freeze({ repository:"langchain-ai/langgraph", tag:"v1.0.5", commit:"84023451a2bd5987b1d4df530f4145d503d75ccb", tree:"57dca8a7e141a91c51c41c7ad1aece02a6c974ca", license:"MIT", licensePath:"LICENSE", licenseBlob:"fc0602feecdd6748623c852ab534e1ca612673c7", licenseBytes:1072, licenseSha256:"d9bb52f2e3540d60ff50d8f0f5b6ba649b7fd346948c4c3d086c8afb120763c7", reuse:"interrupt-semantics-reference-only", conclusion:"LANGGRAPH_INTERRUPT_REEXECUTION_RISK" }),
});

const blockedDependencies = [/^@langchain\//u,/^autogen(?:-|$)/u,/^camel(?:-|$)/u,/^dialogue-manager$/u,/^langgraph$/u];
const keys = (value) => Object.keys(value).sort().join("\0");
const expectKeys = (value, names, code) => { if (!value || typeof value !== "object" || Array.isArray(value) || keys(value) !== [...names].sort().join("\0")) throw new Error(code); };
const hex = (value, length) => typeof value === "string" && new RegExp(`^[0-9a-f]{${length}}$`, "u").test(value);

export function assertR22ReferenceDirectory(fileNames) {
  if (!Array.isArray(fileNames) || [...fileNames].sort().join("\0") !== "reference.lock.json") throw new Error("candidate-artifact");
  return true;
}

export function assertR22ReferenceLock(lock, packageManifestTexts = []) {
  expectKeys(lock,["schemaVersion","format","formatVersion","implementationDecision","policy","references"],"root");
  if (lock.schemaVersion !== 1 || lock.format !== "matrix-oasis.r22-cognition-reference-lock" || lock.formatVersion !== "0.1.0" || lock.implementationDecision !== "internal-bounded-cognition-native-control") throw new Error("identity");
  expectKeys(lock.policy,["candidateArtifactsCommitted","candidateExecutionRequired","newProductionDependencies","transitiveLicenseClosureQualified"],"policy");
  if (lock.policy.candidateArtifactsCommitted !== false || lock.policy.candidateExecutionRequired !== false || lock.policy.newProductionDependencies !== 0 || lock.policy.transitiveLicenseClosureQualified !== false) throw new Error("policy");
  const ids = lock.references?.map((entry) => entry?.id) ?? [];
  if (ids.length !== 5 || new Set(ids).size !== ids.length || ids.join("\0") !== [...ids].sort().join("\0")) throw new Error("reference-order");
  for (const entry of lock.references) {
    const wanted = expected[entry.id];
    if (!wanted) throw new Error("unknown-reference");
    const entryKeys = ["id","repository","tag","commit","gitTreeSha1","license","reuse","conclusionCode"];
    if (entry.id === "camel-v0.2.90") entryKeys.push("tagObjectSha1");
    expectKeys(entry,entryKeys,`entry-${entry.id}`);
    expectKeys(entry.license,["spdx","path","gitBlobSha1","byteLength","sha256","closure"],`license-${entry.id}`);
    if (!hex(entry.commit,40) || !hex(entry.gitTreeSha1,40) || !hex(entry.license.gitBlobSha1,40) || !hex(entry.license.sha256,64) || !Number.isSafeInteger(entry.license.byteLength) || entry.license.byteLength < 1) throw new Error(`source-${entry.id}`);
    if (entry.repository !== wanted.repository || entry.tag !== wanted.tag || entry.commit !== wanted.commit || entry.gitTreeSha1 !== wanted.tree || entry.license.spdx !== wanted.license || entry.license.path !== wanted.licensePath || entry.license.gitBlobSha1 !== wanted.licenseBlob || entry.license.byteLength !== wanted.licenseBytes || entry.license.sha256 !== wanted.licenseSha256 || entry.reuse !== wanted.reuse || entry.conclusionCode !== wanted.conclusion) throw new Error(`drift-${entry.id}`);
    if (wanted.tagObjectSha1 !== undefined && entry.tagObjectSha1 !== wanted.tagObjectSha1) throw new Error(`tag-object-${entry.id}`);
    if (!entry.license.closure.endsWith("transitive-unverified") || entry.reuse === "production-dependency") throw new Error(`boundary-${entry.id}`);
  }
  for (const text of packageManifestTexts) {
    const manifest = JSON.parse(text);
    for (const section of ["dependencies","devDependencies","optionalDependencies","peerDependencies"]) {
      for (const name of Object.keys(manifest[section] ?? {})) if (blockedDependencies.some((pattern) => pattern.test(name))) throw new Error("candidate-dependency");
    }
  }
  return true;
}

function collectManifests() {
  const files=[path.join(moduleRoot,"package.json")];
  for (const rootName of ["apps","packages"]) for (const entry of readdirSync(path.join(moduleRoot,rootName),{withFileTypes:true})) {
    if (!entry.isDirectory() || entry.isSymbolicLink()) continue;
    const file=path.join(moduleRoot,rootName,entry.name,"package.json");
    if (existsSync(file)) files.push(file);
  }
  return files.map((file)=>readFileSync(file,"utf8"));
}

function main() {
  try {
    assertR22ReferenceDirectory(readdirSync(referenceDirectory));
    const bytes=readFileSync(lockPath);
    if (createHash("sha256").update(bytes).digest("hex") !== expectedLockSha256) throw new Error("lock-hash");
    const lock=JSON.parse(bytes.toString("utf8"));
    assertR22ReferenceLock(lock,collectManifests());
    console.log(`R22_COGNITION_REFERENCES_OK references=${lock.references.length} productionDependencies=0 lockSha256=${expectedLockSha256}`);
  } catch {
    console.error("R22_COGNITION_REFERENCE_LOCK_INVALID");
    process.exitCode=1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
