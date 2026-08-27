import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { verifyV2QualificationEvidenceDirectory } from "@matrix-oasis/v2-landscape-harness";
import { R18LandscapeError } from "./r18-landscape-core.mjs";

export const R18_QUALIFICATION_LOCK = "third-party/v2-landscape-references/qualification-evidence.lock.json";

const TMP_ROOT = path.resolve(path.win32.join("C:" + "\\", "tmp"));
const HASH = /^[0-9a-f]{64}$/u;
const CODE = /^[A-Z][A-Z0-9_]{2,95}$/u;
const ID = /^[a-z][a-z0-9-]{1,95}$/u;
const METRIC = /^[a-z][A-Za-z0-9]{0,63}$/u;
const ATTRIBUTION = Object.freeze({
  beehave: "harness",
  concordia: "harness",
  "creator-qualification-baseline": "candidate",
  "deterministic-runtime-baseline": "candidate",
  "dialogue-manager": "unresolved",
  "kenney-animated-characters-retro": "candidate",
  limboai: "candidate",
  mem0: "unresolved",
  "native-control-dialogue-baseline": "candidate",
  "runtime-evidence-baseline": "candidate",
  "static-character-asset-baseline": "candidate",
  tinytroupe: "unresolved",
  "world-event-ledger-baseline": "candidate",
});

function fail(code) {
  throw new R18LandscapeError(code);
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function readJson(file) {
  const text = fs.readFileSync(file, "utf8");
  const value = JSON.parse(text);
  if (canonicalizeJsonValue(value) !== text) fail("R18_QUALIFICATION_IMPORT_NON_CANONICAL");
  return { text, value };
}

function expectedCandidates(moduleRoot) {
  const audit = JSON.parse(fs.readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "desktop-audit.lock.json"), "utf8"));
  return [...new Set(audit.shortlists.flatMap((lane) => lane.candidateIds))].sort();
}

function exactKeys(value, keys) {
  return JSON.stringify(Object.keys(value || {}).sort()) === JSON.stringify([...keys].sort());
}

function sortedUniqueStrings(value, pattern) {
  return Array.isArray(value) && value.every((item) => typeof item === "string" && pattern.test(item)) && JSON.stringify(value) === JSON.stringify([...new Set(value)].sort());
}

function inside(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function safeEvidenceRoot(input) {
  if (typeof input !== "string" || input.length === 0) fail("R18_QUALIFICATION_IMPORT_PATH_INVALID");
  const resolved = path.resolve(input);
  try {
    const real = fs.realpathSync.native(resolved);
    const stat = fs.lstatSync(resolved);
    if (!stat.isDirectory() || stat.isSymbolicLink() || real === fs.realpathSync.native(TMP_ROOT) || !inside(fs.realpathSync.native(TMP_ROOT), real)) fail("R18_QUALIFICATION_IMPORT_PATH_INVALID");
    return real;
  } catch (error) {
    if (error instanceof R18LandscapeError) throw error;
    fail("R18_QUALIFICATION_IMPORT_PATH_INVALID");
  }
}

function expectedCandidateMap(moduleRoot) {
  const audit = JSON.parse(fs.readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "desktop-audit.lock.json"), "utf8"));
  const shortlistedLanes = new Map();
  for (const shortlist of audit.shortlists) for (const candidateId of shortlist.candidateIds) {
    if (!shortlistedLanes.has(candidateId)) shortlistedLanes.set(candidateId, new Set());
    shortlistedLanes.get(candidateId).add(shortlist.laneId);
  }
  const expected = new Set(shortlistedLanes.keys());
  const catalog = JSON.parse(fs.readFileSync(path.join(moduleRoot, "docs", "R18_CANDIDATE_CATALOG.json"), "utf8"));
  return new Map(catalog.catalog.candidates.filter((candidate) => expected.has(candidate.id)).map((candidate) => [candidate.id, { ...candidate, laneIds: candidate.laneIds.filter((laneId) => shortlistedLanes.get(candidate.id).has(laneId)) }]));
}

function validateFixtureOutcome(fixture, laneIds) {
  if (!exactKeys(fixture, ["laneId", "fixtureId", "status", "traceSha256", "metrics", "diagnosticCodes"]) || !laneIds.includes(fixture.laneId) || !ID.test(fixture.fixtureId || "") || !["passed", "failed", "evidence-gap"].includes(fixture.status) || !HASH.test(fixture.traceSha256 || "") || !sortedUniqueStrings(fixture.diagnosticCodes, CODE) || !fixture.metrics || typeof fixture.metrics !== "object" || Array.isArray(fixture.metrics)) fail("R18_QUALIFICATION_LOCK_INVALID");
  const metricKeys = Object.keys(fixture.metrics);
  if (metricKeys.length > 16 || metricKeys.some((key) => !METRIC.test(key)) || Object.values(fixture.metrics).some((value) => !Number.isSafeInteger(value) || value < 0)) fail("R18_QUALIFICATION_LOCK_INVALID");
}

function validateEntry(entry, candidate) {
  if (!candidate) fail("R18_QUALIFICATION_LOCK_INVALID");
  if (!exactKeys(entry, ["candidateId", "laneIds", "status", "harnessAttribution", "sourceIdentitySha256", "sourceIdentityStatus", "planSha256", "executionEvidenceSha256", "reportSha256", "fixtureOutcomes", "diagnosticCodes"]) || entry.candidateId !== candidate.id || JSON.stringify(entry.laneIds) !== JSON.stringify(candidate.laneIds) || !["executed", "failed", "evidence-gap"].includes(entry.status) || !["candidate", "harness", "unresolved"].includes(entry.harnessAttribution) || !["pass", "not-proven"].includes(entry.sourceIdentityStatus) || ![entry.sourceIdentitySha256, entry.planSha256, entry.executionEvidenceSha256, entry.reportSha256].every((value) => HASH.test(value || "")) || !sortedUniqueStrings(entry.diagnosticCodes, CODE) || !Array.isArray(entry.fixtureOutcomes) || entry.fixtureOutcomes.length !== entry.laneIds.length) fail("R18_QUALIFICATION_LOCK_INVALID");
  for (const fixture of entry.fixtureOutcomes) validateFixtureOutcome(fixture, entry.laneIds);
  if (JSON.stringify(entry.fixtureOutcomes.map((fixture) => fixture.laneId)) !== JSON.stringify(entry.laneIds)) fail("R18_QUALIFICATION_LOCK_INVALID");
  const statuses = entry.fixtureOutcomes.map((fixture) => fixture.status);
  if (entry.status === "executed" && (entry.sourceIdentityStatus !== "pass" || statuses.some((status) => status !== "passed"))) fail("R18_QUALIFICATION_LOCK_INVALID");
  if (entry.status === "failed" && !statuses.includes("failed")) fail("R18_QUALIFICATION_LOCK_INVALID");
  if (entry.status === "evidence-gap" && entry.sourceIdentityStatus === "pass" && !statuses.includes("evidence-gap")) fail("R18_QUALIFICATION_LOCK_INVALID");
}

function entryFromDirectory(directory) {
  const verified = verifyV2QualificationEvidenceDirectory(directory);
  const plan = readJson(path.join(directory, "qualification-plan.json"));
  const execution = readJson(path.join(directory, "execution-evidence.json"));
  const report = readJson(path.join(directory, "qualification-report.json"));
  if (verified.candidateId !== report.value.candidateId || report.value.candidateId !== plan.value.candidate.id || execution.value.candidateId !== report.value.candidateId) fail("R18_QUALIFICATION_IMPORT_IDENTITY_MISMATCH");
  const fixtureOutcomes = execution.value.fixtures.map((fixture) => ({
    laneId: fixture.laneId,
    fixtureId: fixture.fixtureId,
    status: fixture.status,
    traceSha256: fixture.traceSha256,
    metrics: fixture.metrics,
    diagnosticCodes: fixture.diagnosticCodes,
  }));
  return {
    candidateId: report.value.candidateId,
    laneIds: report.value.laneIds,
    status: report.value.status,
    harnessAttribution: ATTRIBUTION[report.value.candidateId] ?? "unresolved",
    sourceIdentitySha256: report.value.sourceIdentitySha256,
    sourceIdentityStatus: report.value.hardGates.sourceIdentity,
    planSha256: report.value.planSha256,
    executionEvidenceSha256: report.value.executionEvidenceSha256,
    reportSha256: sha256(Buffer.from(report.text, "utf8")),
    fixtureOutcomes,
    diagnosticCodes: report.value.diagnosticCodes,
  };
}

export function importR18QualificationEvidence({ moduleRoot, evidenceRoot }) {
  const root = safeEvidenceRoot(evidenceRoot);
  const names = fs.readdirSync(root).sort();
  const expected = expectedCandidates(moduleRoot);
  if (JSON.stringify(names) !== JSON.stringify(expected)) fail("R18_QUALIFICATION_IMPORT_CANDIDATE_SET_INVALID");
  const entries = names.map((name) => entryFromDirectory(path.join(root, name))).sort((left, right) => left.candidateId.localeCompare(right.candidateId));
  if (JSON.stringify(entries.map((entry) => entry.candidateId)) !== JSON.stringify(expected)) fail("R18_QUALIFICATION_IMPORT_CANDIDATE_SET_INVALID");
  const value = {
    format: "matrix-oasis.r18-qualification-evidence-lock",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    evidenceSetSha256: sha256(Buffer.from(canonicalizeJsonValue(entries), "utf8")),
    entries,
  };
  return Object.freeze({ value, canonicalJson: canonicalizeJsonValue(value) });
}

export function verifyR18QualificationEvidenceLock({ moduleRoot }) {
  const { text, value } = readJson(path.join(moduleRoot, ...R18_QUALIFICATION_LOCK.split("/")));
  if (/[A-Z]:\\|https?:\/\//iu.test(text)) fail("R18_QUALIFICATION_LOCK_INVALID");
  if (!exactKeys(value, ["format", "formatVersion", "canonicalization", "evidenceSetSha256", "entries"]) || value.format !== "matrix-oasis.r18-qualification-evidence-lock" || value.formatVersion !== "0.1.0" || value.canonicalization !== "matrix-oasis.canonical-json/1") fail("R18_QUALIFICATION_LOCK_INVALID");
  const expected = expectedCandidates(moduleRoot);
  if (!HASH.test(value.evidenceSetSha256 || "") || !Array.isArray(value.entries) || JSON.stringify(value.entries.map((entry) => entry.candidateId)) !== JSON.stringify(expected) || value.evidenceSetSha256 !== sha256(Buffer.from(canonicalizeJsonValue(value.entries), "utf8"))) fail("R18_QUALIFICATION_LOCK_INVALID");
  const candidates = expectedCandidateMap(moduleRoot);
  for (const entry of value.entries) validateEntry(entry, candidates.get(entry.candidateId));
  return Object.freeze({ candidates: value.entries.length, evidenceSetSha256: value.evidenceSetSha256, lockSha256: sha256(Buffer.from(text, "utf8")), entries: value.entries });
}
