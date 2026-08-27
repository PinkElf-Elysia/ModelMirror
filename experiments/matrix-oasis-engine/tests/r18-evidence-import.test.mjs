import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { importR18QualificationEvidence, verifyR18QualificationEvidenceLock } from "../scripts/lib/r18-evidence-import-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporary = [];

test.after(() => {
  for (const target of temporary) fs.rmSync(target, { recursive: true, force: true });
});

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function tamperedModule(mutate) {
  const root = fs.mkdtempSync("C:\\tmp\\matrix-oasis-r18-lock-test-");
  temporary.push(root);
  fs.mkdirSync(path.join(root, "docs"));
  fs.mkdirSync(path.join(root, "third-party", "v2-landscape-references"), { recursive: true });
  fs.copyFileSync(path.join(moduleRoot, "docs", "R18_CANDIDATE_CATALOG.json"), path.join(root, "docs", "R18_CANDIDATE_CATALOG.json"));
  fs.copyFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "desktop-audit.lock.json"), path.join(root, "third-party", "v2-landscape-references", "desktop-audit.lock.json"));
  const lock = JSON.parse(fs.readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "qualification-evidence.lock.json"), "utf8"));
  mutate(lock);
  lock.evidenceSetSha256 = sha256(Buffer.from(canonicalizeJsonValue(lock.entries), "utf8"));
  fs.writeFileSync(path.join(root, "third-party", "v2-landscape-references", "qualification-evidence.lock.json"), canonicalizeJsonValue(lock));
  return root;
}

test("the tracked R18 qualification lock records all thirteen attempted candidates without promoting evidence gaps", () => {
  const lock = verifyR18QualificationEvidenceLock({ moduleRoot });
  assert.equal(lock.candidates, 13);
  assert.match(lock.evidenceSetSha256, /^[0-9a-f]{64}$/u);
  assert.equal(lock.entries.filter((entry) => entry.status === "executed").length, 3);
  assert.equal(lock.entries.filter((entry) => entry.status === "failed").length, 0);
  assert.equal(lock.entries.filter((entry) => entry.status === "evidence-gap").length, 10);
  assert.equal(lock.entries.find((entry) => entry.candidateId === "dialogue-manager").harnessAttribution, "unresolved");
  assert.equal(lock.entries.find((entry) => entry.candidateId === "dialogue-manager").status, "evidence-gap");
  assert.equal(lock.entries.find((entry) => entry.candidateId === "concordia").sourceIdentityStatus, "not-proven");
  assert.equal(lock.entries.find((entry) => entry.candidateId === "tinytroupe").sourceIdentityStatus, "not-proven");
});

test("qualification evidence ordering and diagnostic values contain no paths or secrets", () => {
  const lock = verifyR18QualificationEvidenceLock({ moduleRoot });
  assert.deepEqual(lock.entries.map((entry) => entry.candidateId), [...lock.entries.map((entry) => entry.candidateId)].sort());
  const text = JSON.stringify(lock.entries);
  assert.doesNotMatch(text, /[A-Z]:\\|API[_-]?KEY|TOKEN|SECRET/iu);
});

test("nested fixture hashes, lane identity and status semantics remain fail closed", () => {
  for (const mutate of [
    (lock) => { lock.entries[0].fixtureOutcomes[0].traceSha256 = "not-a-hash"; },
    (lock) => { lock.entries[0].fixtureOutcomes[0].laneId = "dialogue-presentation"; },
    (lock) => { lock.entries[0].status = "executed"; },
  ]) {
    const root = tamperedModule(mutate);
    assert.throws(() => verifyR18QualificationEvidenceLock({ moduleRoot: root }), (error) => error.code === "R18_QUALIFICATION_LOCK_INVALID");
  }
});

test("the importer rejects an evidence root outside the direct C tmp boundary", () => {
  assert.throws(
    () => importR18QualificationEvidence({ moduleRoot, evidenceRoot: path.parse(moduleRoot).root }),
    (error) => error.code === "R18_QUALIFICATION_IMPORT_PATH_INVALID",
  );
});
