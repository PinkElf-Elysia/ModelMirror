import fs from "node:fs";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { planCandidateQualification, qualifySourceOnly } from "@matrix-oasis/v2-qualification-harness";

function fail(code) { const error = new Error(code); error.code = code; throw error; }

export function loadR17Candidates(moduleRoot) {
  const lockPath = path.join(moduleRoot, "third-party", "v2-qualification-references", "reference.lock.json");
  const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  return Object.freeze(lock.executableCandidates.map((candidate) => Object.freeze(candidate)));
}

export function planAllR17Candidates(moduleRoot) {
  return canonicalizeJsonValue({ planVersion: 1, profile: "matrix-oasis.v2-qualification/1", executesCandidateCode: false, candidates: loadR17Candidates(moduleRoot).map(planCandidateQualification) });
}

export function qualifyR17CandidateSourceOnly({ moduleRoot, candidateId, sourceDir, outputDir }) {
  const candidate = loadR17Candidates(moduleRoot).find((item) => item.id === candidateId);
  if (!candidate) fail("R17_CANDIDATE_UNKNOWN");
  return qualifySourceOnly({ candidate, sourceDir, outputDir });
}
