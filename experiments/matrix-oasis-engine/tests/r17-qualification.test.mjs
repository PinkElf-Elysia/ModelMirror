import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateV2CandidateLockJson } from "@matrix-oasis/v2-qualification-contracts";
import { createCandidateLock } from "@matrix-oasis/v2-qualification-harness";
import { loadR17Candidates, planAllR17Candidates, qualifyR17CandidateSourceOnly } from "../scripts/lib/r17-qualification-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("the R17 plan is inert, pinned and container-free", () => {
  const planText = planAllR17Candidates(moduleRoot);
  const plan = JSON.parse(planText);
  assert.equal(plan.executesCandidateCode, false);
  assert.deepEqual(plan.candidates.map((candidate) => candidate.candidateId), ["beehave", "limboai", "dialogue-manager", "mem0", "letta"]);
  for (const candidate of plan.candidates) {
    assert.match(candidate.requiredSource.commit, /^[0-9a-f]{40}$/u);
    assert.match(candidate.requiredSource.tree, /^[0-9a-f]{40}$/u);
    assert.equal(candidate.behavior.container, "forbidden");
    assert.equal(candidate.behavior.lifecycleScripts, "ignore-and-audit");
  }
});

test("all executable reference entries produce valid canonical candidate locks", () => {
  for (const candidate of loadR17Candidates(moduleRoot)) {
    const text = canonicalizeJsonValue(createCandidateLock(candidate));
    assert.equal(validateV2CandidateLockJson(text).valid, true, candidate.id);
  }
});

test("an unknown candidate fails before any path or process operation", () => {
  const tempRoot = path.win32.join("C:" + "\\", "tmp");
  assert.throws(() => qualifyR17CandidateSourceOnly({ moduleRoot, candidateId: "unknown", sourceDir: path.join(tempRoot, "not-read"), outputDir: path.join(tempRoot, "not-written") }), (error) => error.code === "R17_CANDIDATE_UNKNOWN");
});

test("twenty plan serializations are byte-identical", () => {
  assert.equal(new Set(Array.from({ length: 20 }, () => planAllR17Candidates(moduleRoot))).size, 1);
});
