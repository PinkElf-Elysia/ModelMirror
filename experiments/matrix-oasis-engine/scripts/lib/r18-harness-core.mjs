import fs from "node:fs";
import path from "node:path";
import { createV2QualificationPlan, R18LandscapeHarnessError, verifyV2QualificationEvidenceDirectory } from "@matrix-oasis/v2-landscape-harness";

const CATALOG = "docs/R18_CANDIDATE_CATALOG.json";
const AUDIT = "third-party/v2-landscape-references/desktop-audit.lock.json";

function fail(code) {
  throw new R18LandscapeHarnessError(code);
}

function readJson(moduleRoot, relative) {
  try {
    return JSON.parse(fs.readFileSync(path.join(moduleRoot, ...relative.split("/")), "utf8"));
  } catch {
    fail("R18_QUALIFICATION_INPUT_INVALID");
  }
}

function selectedLanes(audit, candidateId) {
  return audit.shortlists.filter((lane) => lane.candidateIds.includes(candidateId)).map((lane) => lane.laneId);
}

export function planR18Qualification({ moduleRoot, candidateId }) {
  const catalog = readJson(moduleRoot, CATALOG);
  const audit = readJson(moduleRoot, AUDIT);
  const candidate = catalog.catalog.candidates.find((item) => item.id === candidateId);
  const laneIds = selectedLanes(audit, candidateId);
  if (!candidate || laneIds.length === 0) fail("R18_QUALIFICATION_CANDIDATE_NOT_SHORTLISTED");
  return createV2QualificationPlan({ candidate, laneIds });
}

export function planAllR18Qualifications({ moduleRoot }) {
  const audit = readJson(moduleRoot, AUDIT);
  const ids = [...new Set(audit.shortlists.flatMap((lane) => lane.candidateIds))].sort();
  return Object.freeze(ids.map((candidateId) => planR18Qualification({ moduleRoot, candidateId })));
}

export async function qualifyR18Candidate({ moduleRoot, candidateId }) {
  planR18Qualification({ moduleRoot, candidateId });
  fail("R18_QUALIFICATION_ADAPTER_UNAVAILABLE");
}

export function verifyR18EvidenceRoot(evidenceRoot) {
  let root;
  try {
    root = fs.realpathSync.native(path.resolve(evidenceRoot));
  } catch {
    fail("R18_EVIDENCE_ROOT_INVALID");
  }
  const stat = fs.lstatSync(root);
  if (!stat.isDirectory() || stat.isSymbolicLink()) fail("R18_EVIDENCE_ROOT_INVALID");
  if (fs.existsSync(path.join(root, "qualification-report.json"))) {
    const report = verifyV2QualificationEvidenceDirectory(root);
    return Object.freeze({ reports: 1, candidates: Object.freeze([report.candidateId]) });
  }
  const names = fs.readdirSync(root).sort();
  const reports = names.map((name) => {
    const child = path.join(root, name);
    const childStat = fs.lstatSync(child);
    if (!childStat.isDirectory() || childStat.isSymbolicLink()) fail("R18_EVIDENCE_ROOT_INVALID");
    return verifyV2QualificationEvidenceDirectory(child);
  });
  const candidates = reports.map((report) => report.candidateId);
  if (reports.length === 0 || new Set(candidates).size !== candidates.length) fail("R18_EVIDENCE_ROOT_INVALID");
  return Object.freeze({ reports: reports.length, candidates: Object.freeze(candidates.sort()) });
}
