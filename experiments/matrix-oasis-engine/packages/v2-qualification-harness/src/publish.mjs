import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateV2QualificationReportJson } from "@matrix-oasis/v2-qualification-contracts";
import { assertSafeTmpPath, V2QualificationOperationalError } from "./source.mjs";

function sha256(bytes) { return crypto.createHash("sha256").update(bytes).digest("hex"); }
function fail(code) { throw new V2QualificationOperationalError(code); }

function safeParent(outputDir) {
  const target = assertSafeTmpPath(outputDir, { mustExist: false });
  if (fs.existsSync(target)) fail("R17_OUTPUT_EXISTS");
  const parent = assertSafeTmpPath(path.dirname(target), { allowRoot: true });
  if (!fs.lstatSync(parent).isDirectory()) fail("R17_OUTPUT_PARENT_INVALID");
  return { target, parent };
}

function evidenceFile(name, bytes) {
  return { name, byteLength: bytes.length, sha256: sha256(bytes) };
}

function sameFile(left, right) {
  return left?.name === right.name && left?.byteLength === right.byteLength && left?.sha256 === right.sha256;
}

function sameIdentity(left, right) {
  return left.dev === right.dev
    && left.ino === right.ino
    && left.size === right.size
    && left.mtimeNs === right.mtimeNs
    && left.ctimeNs === right.ctimeNs;
}

function readStableRegularFile(filePath) {
  let handle;
  try {
    const before = fs.lstatSync(filePath, { bigint: true });
    if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n) fail("R17_EVIDENCE_FILE_INVALID");
    handle = fs.openSync(filePath, "r");
    const opened = fs.fstatSync(handle, { bigint: true });
    if (!sameIdentity(before, opened)) fail("R17_EVIDENCE_FILE_INVALID");
    const bytes = fs.readFileSync(handle);
    const after = fs.fstatSync(handle, { bigint: true });
    const current = fs.lstatSync(filePath, { bigint: true });
    if (!sameIdentity(opened, after) || !sameIdentity(opened, current) || current.nlink !== 1n) fail("R17_EVIDENCE_FILE_INVALID");
    return bytes;
  } catch (error) {
    if (error instanceof V2QualificationOperationalError) throw error;
    fail("R17_EVIDENCE_FILE_INVALID");
  } finally {
    if (handle !== undefined) fs.closeSync(handle);
  }
}

function validateArtifactSources(artifacts) {
  if (!Array.isArray(artifacts)) fail("R17_ARTIFACT_LIST_INVALID");
  const seen = new Set();
  return artifacts.map((artifact) => {
    if (!artifact || typeof artifact.name !== "string" || !/^[A-Za-z0-9._-]{1,128}$/u.test(artifact.name) || seen.has(artifact.name) || artifact.name === "qualification-report.json" || artifact.name === "source-identity.json" || artifact.name === "execution-evidence.json") fail("R17_ARTIFACT_NAME_INVALID");
    seen.add(artifact.name);
    const sourcePath = assertSafeTmpPath(artifact.sourcePath);
    let bytes;
    try { bytes = readStableRegularFile(sourcePath); }
    catch { fail("R17_ARTIFACT_SOURCE_INVALID"); }
    return { name: artifact.name, bytes };
  });
}

export function publishQualification({ outputDir, sourceIdentityJson, executionEvidence, report, artifacts = [] }) {
  const { target, parent } = safeParent(outputDir);
  const executionJson = canonicalizeJsonValue(executionEvidence);
  const artifactSources = validateArtifactSources(artifacts);
  const payloads = [
    { name: "source-identity.json", bytes: Buffer.from(sourceIdentityJson, "utf8") },
    { name: "execution-evidence.json", bytes: Buffer.from(executionJson, "utf8") },
    ...artifactSources,
  ];
  const expectedEvidence = payloads.map(({ name, bytes }) => evidenceFile(name, bytes)).sort((left, right) => left.name.localeCompare(right.name));
  const reportJson = canonicalizeJsonValue(report);
  const validation = validateV2QualificationReportJson(reportJson);
  if (!validation.valid) fail("R17_REPORT_INVALID");
  const declaredEvidence = [...validation.value.evidence.files].sort((left, right) => left.name.localeCompare(right.name));
  if (declaredEvidence.length !== expectedEvidence.length || declaredEvidence.some((file, index) => !sameFile(file, expectedEvidence[index]))) fail("R17_REPORT_EVIDENCE_SET_MISMATCH");
  const expectedFiles = [...payloads, { name: "qualification-report.json", bytes: Buffer.from(reportJson, "utf8") }];
  const staging = path.join(parent, `.${path.basename(target)}.staging-${crypto.randomBytes(8).toString("hex")}`);
  fs.mkdirSync(staging);
  try {
    for (const { name, bytes } of expectedFiles) fs.writeFileSync(path.join(staging, name), bytes, { flag: "wx" });
    fs.renameSync(staging, target);
  } catch (error) {
    if (fs.existsSync(staging)) fs.rmSync(staging, { recursive: true, force: true });
    if (error instanceof V2QualificationOperationalError) throw error;
    fail("R17_OUTPUT_PUBLISH_FAILED");
  }
  return Object.freeze({ outputId: sha256(Buffer.from(reportJson, "utf8")), files: Object.freeze(expectedFiles.map(({ name, bytes }) => Object.freeze(evidenceFile(name, bytes)))) });
}

export function verifyQualificationDirectory(directory) {
  const root = assertSafeTmpPath(directory);
  const stat = fs.lstatSync(root);
  if (!stat.isDirectory() || stat.isSymbolicLink()) fail("R17_EVIDENCE_ROOT_INVALID");
  const reportText = readStableRegularFile(path.join(root, "qualification-report.json")).toString("utf8");
  const validation = validateV2QualificationReportJson(reportText);
  if (!validation.valid) fail("R17_REPORT_INVALID");
  const expected = [...validation.value.evidence.files.map((file) => file.name), "qualification-report.json"].sort();
  const names = fs.readdirSync(root).sort();
  if (new Set(expected).size !== expected.length || JSON.stringify(names) !== JSON.stringify(expected)) fail("R17_EVIDENCE_FILE_SET_INVALID");
  for (const file of validation.value.evidence.files) {
    const filePath = path.join(root, file.name);
    const payload = readStableRegularFile(filePath);
    if (payload.length !== file.byteLength || sha256(payload) !== file.sha256) fail("R17_EVIDENCE_HASH_MISMATCH");
  }
  const sourceFile = validation.value.evidence.files.find((file) => file.name === "source-identity.json");
  const executionFile = validation.value.evidence.files.find((file) => file.name === "execution-evidence.json");
  if (sourceFile?.sha256 !== validation.value.evidence.sourceIdentitySha256 || executionFile?.sha256 !== validation.value.evidence.executionEvidenceSha256) fail("R17_EVIDENCE_IDENTITY_MISMATCH");
  let execution;
  try {
    const executionText = fs.readFileSync(path.join(root, "execution-evidence.json"), "utf8");
    execution = JSON.parse(executionText);
    if (canonicalizeJsonValue(execution) !== executionText) fail("R17_EXECUTION_EVIDENCE_NON_CANONICAL");
  } catch (error) {
    if (error instanceof V2QualificationOperationalError) throw error;
    fail("R17_EXECUTION_EVIDENCE_INVALID");
  }
  if (execution.candidateId !== validation.value.candidate.id || !Array.isArray(execution.artifacts)) fail("R17_EXECUTION_EVIDENCE_INVALID");
  const declaredArtifacts = [...execution.artifacts].sort((left, right) => left.name.localeCompare(right.name));
  const actualArtifacts = validation.value.evidence.files.filter((file) => !["source-identity.json", "execution-evidence.json"].includes(file.name)).sort((left, right) => left.name.localeCompare(right.name));
  if (declaredArtifacts.length !== actualArtifacts.length || declaredArtifacts.some((file, index) => !sameFile(file, actualArtifacts[index]))) fail("R17_EXECUTION_ARTIFACT_SET_MISMATCH");
  return Object.freeze({ ok: true, candidateId: validation.value.candidate.id, reportSha256: sha256(Buffer.from(reportText, "utf8")) });
}
