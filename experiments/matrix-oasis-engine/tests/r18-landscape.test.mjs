import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { validateV2CandidateCatalogJson } from "@matrix-oasis/v2-landscape-contracts";
import { buildR18DesktopLandscape, verifyR18DesktopLandscape } from "../scripts/lib/r18-landscape-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function read(relative) {
  return readFileSync(path.join(moduleRoot, ...relative.split("/")));
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

test("the tracked desktop landscape is reproducible and covers every source-lock entry", () => {
  const report = verifyR18DesktopLandscape({ moduleRoot });
  assert.equal(report.candidates, 62);
  assert.equal(report.entries, 96);
  assert.equal(report.shortlists, 7);
  assert.equal(report.catalogSha256, "e47791fd90ba0776bf90c907fc52ed57f7bf47595bb362c858152255be157222");
  assert.match(report.auditSha256, /^[0-9a-f]{64}$/);

  const catalogText = read("docs/R18_CANDIDATE_CATALOG.json").toString("utf8");
  assert.equal(validateV2CandidateCatalogJson(catalogText).valid, true);
});

test("only executable lanes receive two or three evidence-based shortlist entries", () => {
  const built = buildR18DesktopLandscape({ moduleRoot });
  const candidateById = new Map(built.catalog.catalog.candidates.map((candidate) => [candidate.id, candidate]));
  const decisionByKey = new Map(built.audit.decisions.map((decision) => [`${decision.laneId}\0${decision.candidateId}`, decision]));
  const selected = new Set();

  for (const lane of built.catalog.catalog.lanes) {
    const shortlist = built.audit.shortlists.find((item) => item.laneId === lane.id);
    assert.ok(shortlist);
    if (!lane.executable) {
      assert.deepEqual(shortlist.candidateIds, []);
      continue;
    }
    assert.ok(shortlist.candidateIds.length >= 2 && shortlist.candidateIds.length <= 3);
    for (const candidateId of shortlist.candidateIds) {
      selected.add(candidateId);
      const candidate = candidateById.get(candidateId);
      const decision = decisionByKey.get(`${lane.id}\0${candidateId}`);
      assert.equal(candidate.staticExclusion.excluded, false);
      assert.equal(candidate.license.qualificationAllowed, true);
      assert.ok(["approved", "direct-approved"].includes(candidate.license.closureStatus));
      assert.equal(decision.tier, "executable-shortlist");
      assert.equal(decision.conclusion, "backup");
      assert.ok(Number.isInteger(decision.shortlistRank));
    }
  }

  assert.ok(selected.size >= 12 && selected.size <= 16);
  assert.equal(built.audit.decisions.some((decision) => decision.tier === "integration-recommended"), false);
});

test("reported licenses and public benchmarks cannot become executable through desktop scoring", () => {
  const built = buildR18DesktopLandscape({ moduleRoot });
  const selected = new Set(built.audit.shortlists.flatMap((item) => item.candidateIds));
  for (const candidate of built.catalog.catalog.candidates) {
    if (candidate.source.kind === "github-search-result") {
      assert.equal(candidate.license.reuseAllowed, false);
      assert.equal(candidate.license.qualificationAllowed, false);
      assert.equal(selected.has(candidate.id), false);
    }
    if (candidate.candidateType === "commercial-benchmark") {
      assert.equal(selected.has(candidate.id), false);
      assert.equal(candidate.license.reuseAllowed, false);
    }
  }
});

test("desktop scores contain no named-candidate priority escape hatch", () => {
  const source = read("scripts/lib/r18-landscape-core.mjs").toString("utf8");
  assert.doesNotMatch(source, /DESKTOP_PRIORITY|worldxFit/);
});

test("twenty rebuilds are byte-identical and preserve the frozen source lock", () => {
  const sourceBefore = sha256(read("third-party/v2-landscape-references/reference.lock.json"));
  const first = buildR18DesktopLandscape({ moduleRoot });
  for (let index = 0; index < 20; index += 1) {
    const next = buildR18DesktopLandscape({ moduleRoot });
    assert.equal(next.catalogText, first.catalogText);
    assert.equal(next.auditText, first.auditText);
  }
  assert.equal(sha256(read("third-party/v2-landscape-references/reference.lock.json")), sourceBefore);
});
