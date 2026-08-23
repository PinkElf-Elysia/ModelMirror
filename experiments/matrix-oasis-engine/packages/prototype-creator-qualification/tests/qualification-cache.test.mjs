import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  findVerifiedQualifiedCreatorRun,
  loadVerifiedQualifiedCreatorRun,
  publishQualifiedCreatorRun,
  recoverQualifiedCreatorRuns,
} from "../src/index.mjs";

const hash = (character) => `sha256:${character.repeat(64)}`;
const rawDigest = (text) => createHash("sha256").update(text).digest("hex");

function qualification(overrides = {}) {
  const evidenceHash = overrides.runtimeEvidenceSha256 ?? hash("c");
  return {
    format: "matrix-oasis.prototype-creator-qualification",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    profile: "matrix-oasis.creator-solved-evidence/1",
    status: "qualified",
    promptSha256: overrides.promptSha256 ?? hash("8"),
    model: overrides.model ?? "openai/gpt-5.6-luna",
    sourceRunId: `${"a".repeat(64)}-${"b".repeat(64)}`,
    hashes: {
      runtimePackSha256: hash("0"),
      runtimeReceiptSha256: hash("1"),
      spatialIntentSha256: hash("2"),
      environmentFactsSha256: hash("3"),
      assetBundleSha256: hash("4"),
      spatialSolutionSha256: hash("5"),
      spatialVerificationSha256: hash("6"),
      replayPlanSha256: hash("7"),
      runtimeEvidenceSha256: evidenceHash,
    },
    toolchain: {
      godotVersion: "4.6.3",
      renderer: "forward_plus",
      evidenceProfile: "matrix-oasis.runtime-replay/1",
    },
    evidence: {
      runId: evidenceHash.slice("sha256:".length),
      attempt: overrides.attempt ?? 0,
      replayCount: 6,
      screenshotCount: 12,
      videoCount: 1,
      sampleCount: 300,
      medianFrameMicros: 16_667,
      medianFpsMilli: 60_000,
    },
  };
}

async function fixture() {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "matrix-oasis-r16-cache-"));
  return {
    temporaryRoot,
    qualifiedRunRoot: path.join(temporaryRoot, "qualified"),
    async dispose() { await rm(temporaryRoot, { recursive: true, force: true }); },
  };
}

const acceptReferences = async () => true;

test("publishes canonical manifest by its SHA-256 and updates current last", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const canonicalQualificationJson = canonicalizeJsonValue(qualification());
  let verificationCalls = 0;
  const verifyReferences = async ({ qualification, qualificationJson, qualificationRunId }) => {
    verificationCalls += 1;
    assert.equal(qualification.status, "qualified");
    assert.equal(qualificationJson, canonicalQualificationJson);
    assert.equal(qualificationRunId, rawDigest(canonicalQualificationJson));
    return { valid: true };
  };
  const published = await publishQualifiedCreatorRun({ ...environment, canonicalQualificationJson, verifyReferences });
  assert.equal(published.qualificationRunId, rawDigest(canonicalQualificationJson));
  assert.equal(await readFile(path.join(published.runDirectory, "qualification.json"), "utf8"), canonicalQualificationJson);
  const currentText = await readFile(path.join(environment.qualifiedRunRoot, "qualified-current.json"), "utf8");
  assert.deepEqual(JSON.parse(currentText), {
    format: "matrix-oasis.prototype-creator-qualified-current",
    formatVersion: "0.1.0",
    qualificationRunId: published.qualificationRunId,
  });
  assert.equal(canonicalizeJsonValue(JSON.parse(currentText)), currentText);
  assert.ok(verificationCalls >= 3);

  const loaded = await loadVerifiedQualifiedCreatorRun({
    ...environment,
    qualificationRunId: published.qualificationRunId,
    verifyReferences,
  });
  assert.equal(loaded.qualificationJson, canonicalQualificationJson);
  assert.equal(Object.isFrozen(loaded), true);
  assert.equal(Object.isFrozen(loaded.qualification.hashes), true);
});

test("requires a reference verifier on every public operation", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const canonicalQualificationJson = canonicalizeJsonValue(qualification());
  await assert.rejects(
    publishQualifiedCreatorRun({ ...environment, canonicalQualificationJson }),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID" },
  );
  await assert.rejects(
    recoverQualifiedCreatorRuns(environment),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_INPUT_INVALID" },
  );
});

test("reference or media drift makes load, recover, and find ineligible", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const referenceFile = path.join(environment.temporaryRoot, "runtime-evidence.json");
  await writeFile(referenceFile, "stable", "utf8");
  const expected = rawDigest("stable");
  const candidate = qualification({ runtimeEvidenceSha256: `sha256:${expected}` });
  const canonicalQualificationJson = canonicalizeJsonValue(candidate);
  const verifyReferences = async ({ qualification: manifest }) => {
    const bytes = await readFile(referenceFile);
    return `sha256:${createHash("sha256").update(bytes).digest("hex")}` === manifest.hashes.runtimeEvidenceSha256;
  };
  const published = await publishQualifiedCreatorRun({ ...environment, canonicalQualificationJson, verifyReferences });
  await writeFile(referenceFile, "drifted", "utf8");
  await assert.rejects(
    loadVerifiedQualifiedCreatorRun({ ...environment, qualificationRunId: published.qualificationRunId, verifyReferences }),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_REFERENCE_INVALID" },
  );
  const recovered = await recoverQualifiedCreatorRuns({ ...environment, verifyReferences });
  assert.deepEqual({ current: recovered.currentQualificationRunId, count: recovered.runs.length }, { current: null, count: 0 });
  assert.equal(await findVerifiedQualifiedCreatorRun({
    ...environment,
    promptSha256: candidate.promptSha256,
    model: candidate.model,
    verifyReferences,
  }), null);
});

test("failed post-write reference verification preserves the previous current", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const firstJson = canonicalizeJsonValue(qualification({ attempt: 0 }));
  const first = await publishQualifiedCreatorRun({ ...environment, canonicalQualificationJson: firstJson, verifyReferences: acceptReferences });
  const secondJson = canonicalizeJsonValue(qualification({ attempt: 1 }));
  let calls = 0;
  await assert.rejects(
    publishQualifiedCreatorRun({
      ...environment,
      canonicalQualificationJson: secondJson,
      verifyReferences: async () => {
        calls += 1;
        return calls < 3;
      },
    }),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_REFERENCE_INVALID" },
  );
  const recovered = await recoverQualifiedCreatorRuns({ ...environment, verifyReferences: acceptReferences });
  assert.equal(recovered.currentQualificationRunId, first.qualificationRunId);
  assert.deepEqual(recovered.runs.map((run) => run.qualificationRunId), [first.qualificationRunId]);
});

test("find prefers valid current and otherwise chooses a stable lexical run", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const first = await publishQualifiedCreatorRun({
    ...environment,
    canonicalQualificationJson: canonicalizeJsonValue(qualification({ attempt: 0 })),
    verifyReferences: acceptReferences,
  });
  const second = await publishQualifiedCreatorRun({
    ...environment,
    canonicalQualificationJson: canonicalizeJsonValue(qualification({ attempt: 1 })),
    verifyReferences: acceptReferences,
  });
  const found = await findVerifiedQualifiedCreatorRun({
    ...environment,
    promptSha256: hash("8"),
    model: "openai/gpt-5.6-luna",
    verifyReferences: acceptReferences,
  });
  assert.equal(found.qualificationRunId, second.qualificationRunId);

  await writeFile(path.join(environment.qualifiedRunRoot, "qualified-current.json"), "{}", "utf8");
  const lexical = await findVerifiedQualifiedCreatorRun({
    ...environment,
    promptSha256: hash("8"),
    model: "openai/gpt-5.6-luna",
    verifyReferences: acceptReferences,
  });
  assert.equal(lexical.qualificationRunId, [first.qualificationRunId, second.qualificationRunId].sort()[0]);
});

test("rejects existing targets, traversal, and junction roots", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const canonicalQualificationJson = canonicalizeJsonValue(qualification());
  const qualificationRunId = rawDigest(canonicalQualificationJson);
  await mkdir(path.join(environment.qualifiedRunRoot, "runs", qualificationRunId), { recursive: true });
  await assert.rejects(
    publishQualifiedCreatorRun({ ...environment, canonicalQualificationJson, verifyReferences: acceptReferences }),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID" },
  );
  await assert.rejects(
    publishQualifiedCreatorRun({
      temporaryRoot: environment.temporaryRoot,
      qualifiedRunRoot: path.join(path.dirname(environment.temporaryRoot), "escaped-qualified"),
      canonicalQualificationJson,
      verifyReferences: acceptReferences,
    }),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID" },
  );

  const actual = path.join(environment.temporaryRoot, "actual-qualified");
  const linked = path.join(environment.temporaryRoot, "linked-qualified");
  await mkdir(actual);
  await symlink(actual, linked, process.platform === "win32" ? "junction" : "dir");
  await assert.rejects(
    recoverQualifiedCreatorRuns({ ...environment, qualifiedRunRoot: linked, verifyReferences: acceptReferences }),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID" },
  );
});

test("detects a staging directory swap during the write window", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  let displacedDirectory = null;
  const services = {
    lstat,
    mkdir,
    mkdtemp,
    openFile: open,
    readFile,
    readdir,
    realpath,
    rename,
    rm,
    async writeFile(candidate, data, options) {
      if (candidate.endsWith(`${path.sep}qualification.json`)) {
        const directory = path.dirname(candidate);
        const displaced = `${directory}-displaced`;
        await rename(directory, displaced);
        displacedDirectory = displaced;
        await mkdir(directory);
      }
      return await writeFile(candidate, data, options);
    },
  };
  await assert.rejects(
    publishQualifiedCreatorRun({
      ...environment,
      canonicalQualificationJson: canonicalizeJsonValue(qualification()),
      verifyReferences: acceptReferences,
    }, services),
    { code: "PROTOTYPE_CREATOR_QUALIFICATION_CACHE_PATH_INVALID" },
  );
  assert.equal((await recoverQualifiedCreatorRuns({ ...environment, verifyReferences: acceptReferences })).runs.length, 0);
  assert.equal((await lstat(displacedDirectory)).isDirectory(), true);
});

test("concurrent identical publication has one winner and never exposes a partial run", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const canonicalQualificationJson = canonicalizeJsonValue(qualification());
  const results = await Promise.allSettled([
    publishQualifiedCreatorRun({ ...environment, canonicalQualificationJson, verifyReferences: acceptReferences }),
    publishQualifiedCreatorRun({ ...environment, canonicalQualificationJson, verifyReferences: acceptReferences }),
  ]);
  assert.equal(results.filter((result) => result.status === "fulfilled").length, 1);
  assert.equal(results.filter((result) => result.status === "rejected").length, 1);
  const recovered = await recoverQualifiedCreatorRuns({ ...environment, verifyReferences: acceptReferences });
  assert.equal(recovered.runs.length, 1);
  assert.equal(recovered.currentQualificationRunId, rawDigest(canonicalQualificationJson));
});

test("qualification run identity is deterministic for twenty independent roots", async (t) => {
  const environment = await fixture();
  t.after(() => environment.dispose());
  const canonicalQualificationJson = canonicalizeJsonValue(qualification());
  const expected = rawDigest(canonicalQualificationJson);
  for (let index = 0; index < 20; index += 1) {
    const published = await publishQualifiedCreatorRun({
      temporaryRoot: environment.temporaryRoot,
      qualifiedRunRoot: path.join(environment.temporaryRoot, `qualified-${String(index).padStart(2, "0")}`),
      canonicalQualificationJson,
      verifyReferences: acceptReferences,
    });
    assert.equal(published.qualificationRunId, expected);
  }
});
