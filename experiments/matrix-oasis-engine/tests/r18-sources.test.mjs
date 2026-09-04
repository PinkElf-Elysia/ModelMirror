import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createR18DiscoveryPlan, executeR18Discovery, validateR18SearchEvidenceValue } from "../scripts/lib/r18-discovery-core.mjs";
import { verifyR18Sources } from "../scripts/lib/r18-source-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function sha256(relative) {
  return createHash("sha256")
    .update(readFileSync(path.join(moduleRoot, ...relative.split("/"))))
    .digest("hex");
}

test("R17 and R18 selection evidence remain byte frozen through R20 qualification", () => {
  assert.equal(
    sha256("docs/R17_QUALIFICATION_SUMMARY.json"),
    "d87346eebfbbcb22bf00a386a6511859c42aec91393d193a4c40db0b9de08c8e",
  );
  assert.equal(
    sha256("docs/R17_V2_SELECTION_MATRIX.md"),
    "9cb2dceeea7ad3ba42b52d090822b26950b544f630310cb0b7b6150e8722bc40",
  );
  assert.equal(
    sha256("third-party/v2-qualification-references/reference.lock.json"),
    "0104e57fb962705b35bbbba1ca098e272af1e178ff00492f89744385f6c0173f",
  );

  const status = JSON.parse(readFileSync(path.join(moduleRoot, "docs", "V2_STATUS.json"), "utf8"));
  assert.equal(status.schemaVersion, 1);
  assert.equal(status.claimAllowed, false);
  assert.equal(status.blockingRound, "R25");
  assert.equal(typeof status.qualificationProfile, "string");
  assert.ok(status.qualificationProfile.length > 0);
});

test("R18 discovery plan is fixed, credential-free, and does not execute network", () => {
  const plan = createR18DiscoveryPlan({ moduleRoot });
  assert.equal(plan.laneQueries, 8);
  assert.equal(plan.publicDocuments, 8);
  assert.equal(plan.requestMaximum, 100);
  assert.equal(plan.credentials, "none");
  assert.equal(plan.commercialApiCalls, false);
  assert.equal(plan.supplierCalls, false);
  assert.equal(plan.querySetSha256.length, 64);
  assert.deepEqual(plan.requests.map((item) => item.host), [...plan.requests.map((item) => item.host)].sort());
});

test("R18 discovery requires the explicit public-network acknowledgement before validating an output", async () => {
  await assert.rejects(
    executeR18Discovery({ moduleRoot, output: "not-an-output", acknowledged: false }),
    (error) => error?.code === "R18_DISCOVERY_APPROVAL_REQUIRED",
  );
});

test("R18 discovery rejects an unapproved mode and a target outside the direct system temp root without network", async () => {
  await assert.rejects(
    executeR18Discovery({ moduleRoot, output: "unused", acknowledged: true, mode: "unbounded" }),
    (error) => error?.code === "R18_DISCOVERY_MODE_INVALID",
  );
  await assert.rejects(
    executeR18Discovery({ moduleRoot, output: moduleRoot, acknowledged: true }),
    (error) => error?.code === "R18_DISCOVERY_OUTPUT_INVALID",
  );
});

test("R18 identity-only recovery accepts only the published search-only evidence shape before network", () => {
  const querySet = JSON.parse(readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "discovery-query-set.json"), "utf8"));
  const querySetSha256 = createHash("sha256")
    .update(readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "discovery-query-set.json")))
    .digest("hex");
  assert.throws(
    () => validateR18SearchEvidenceValue({
      format: "matrix-oasis.r18-public-search-evidence",
      formatVersion: "0.1.0",
      mode: "documents-only",
      querySetSha256,
      lanes: querySet.lanes.map((lane) => ({ laneId: lane.id, querySha256: "0".repeat(64), responseSha256: "0".repeat(64), repositories: [] })),
    }, querySetSha256),
    (error) => error?.code === "R18_DISCOVERY_SEARCH_EVIDENCE_INVALID",
  );
});

test("R18 source lock meets the broad landscape quota without treating reported licenses as reusable", () => {
  const report = verifyR18Sources({ moduleRoot });
  assert.equal(report.candidates, 62);
  assert.equal(report.entries, 96);
  assert.equal(report.githubIdentities, 23);
  assert.equal(report.publicDocuments, 8);
  const lock = JSON.parse(readFileSync(path.join(moduleRoot, "third-party", "v2-landscape-references", "reference.lock.json"), "utf8"));
  assert.deepEqual(lock.licensePolicy.allowedSpdx, ["Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC0-1.0", "ISC", "MIT"]);
  assert.equal(lock.candidates.filter((item) => item.license.status === "reported" && item.license.reuseEligible).length, 0);
  assert.equal(lock.candidates.filter((item) => item.candidateType === "commercial-benchmark" && item.license.reuseEligible).length, 0);
  for (const id of ["langgraph", "autogen", "camel", "langmem", "cognee", "agentmembench", "dialogic", "quaternius-animated-characters"]) {
    const candidate = lock.candidates.find((item) => item.id === id);
    assert.equal(candidate.discovery.status, "identity-gap");
    assert.equal(candidate.license.reuseEligible, false);
  }
});
