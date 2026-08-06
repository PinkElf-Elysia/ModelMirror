import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fingerprintSkillRuntimeIndex } from "./skill-runtime-index.mjs";

const index = JSON.parse(
  await readFile("server/skills/data/skill_runtime_index.json", "utf8"),
);
assert.equal(index.version, 1);
assert.equal(index.rankerVersion, "skill-need-local-v3");
assert.match(index.memberIndexFingerprint, /^[0-9a-f]{64}$/);
assert.match(index.fingerprint, /^[0-9a-f]{64}$/);
assert.equal(fingerprintSkillRuntimeIndex(index), index.fingerprint);
assert.ok(index.candidates.length > 4_000, "runtime index must cover the verified catalog");

const candidateIds = new Set();
const sourceKeys = new Set();
for (const candidate of index.candidates) {
  assert.match(candidate.candidateId, /^catalog:(project|member):/);
  assert.match(candidate.candidateFingerprint, /^[0-9a-f]{64}$/);
  assert.match(candidate.installSource.verifiedCommit, /^[0-9a-f]{40}$/);
  assert.ok(candidate.name && candidate.description && candidate.category);
  assert.ok(!candidateIds.has(candidate.candidateId), `duplicate candidate ${candidate.candidateId}`);
  candidateIds.add(candidate.candidateId);
  const sourceKey = [
    candidate.installSource.repoUrl.toLowerCase().replace(/\.git$/i, ""),
    candidate.installSource.subPath.replace(/^\/+|\/+$/g, ""),
  ].join("#");
  assert.ok(!sourceKeys.has(sourceKey), `duplicate install source ${sourceKey}`);
  sourceKeys.add(sourceKey);
}
assert.deepEqual(index.supersededCandidateIds, [
  "catalog:project:voltagent-coderabbitai-autofix",
  "catalog:project:voltagent-coderabbitai-code-review",
]);

console.log(
  `Skill runtime index audit passed: ${index.candidates.length} verified candidates`,
);
