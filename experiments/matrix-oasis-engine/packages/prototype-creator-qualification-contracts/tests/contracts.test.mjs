import test from "node:test";
import assert from "node:assert/strict";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA,
  validatePrototypeCreatorQualificationJson,
} from "../src/index.mjs";

const hash = (character) => `sha256:${character.repeat(64)}`;

function qualification() {
  return {
    format: "matrix-oasis.prototype-creator-qualification",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    profile: "matrix-oasis.creator-solved-evidence/1",
    status: "qualified",
    promptSha256: hash("8"),
    model: "openai/gpt-5.6-luna",
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
      runtimeEvidenceSha256: hash("c"),
    },
    toolchain: {
      godotVersion: "4.6.3",
      renderer: "forward_plus",
      evidenceProfile: "matrix-oasis.runtime-replay/1",
    },
    evidence: {
      runId: "c".repeat(64),
      attempt: 0,
      replayCount: 6,
      screenshotCount: 12,
      videoCount: 1,
      sampleCount: 300,
      medianFrameMicros: 16_667,
      medianFpsMilli: 60_000,
    },
  };
}

test("accepts and freezes a canonical qualified manifest report", () => {
  const text = canonicalizeJsonValue(qualification());
  const result = validatePrototypeCreatorQualificationJson(text);
  assert.deepEqual(result, { reportVersion: 1, valid: true, diagnostics: [] });
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.diagnostics), true);
  assert.equal(Object.isFrozen(PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA), true);
});

test("rejects unknown fields without echoing the unknown key", () => {
  const candidate = qualification();
  candidate.providerSecret = "must-not-appear";
  const result = validatePrototypeCreatorQualificationJson(
    canonicalizeJsonValue(candidate),
  );
  assert.equal(result.valid, false);
  assert.ok(
    result.diagnostics.some(
      (item) =>
        item.code ===
        "PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA_UNKNOWN_PROPERTY",
    ),
  );
  assert.equal(JSON.stringify(result).includes("providerSecret"), false);
  assert.equal(JSON.stringify(result).includes("must-not-appear"), false);
});

test("rejects noncanonical bytes and duplicate keys", () => {
  const pretty = `${JSON.stringify(qualification(), null, 2)}\n`;
  assert.ok(
    validatePrototypeCreatorQualificationJson(pretty).diagnostics.some(
      (item) =>
        item.code === "PROTOTYPE_CREATOR_QUALIFICATION_JSON_NON_CANONICAL",
    ),
  );

  const canonical = canonicalizeJsonValue(qualification());
  const duplicate = canonical.replace(
    '"status":"qualified"',
    '"status":"qualified","status":"qualified"',
  );
  assert.ok(
    validatePrototypeCreatorQualificationJson(duplicate).diagnostics.some(
      (item) =>
        item.code === "PROTOTYPE_CREATOR_QUALIFICATION_JSON_DUPLICATE_KEY",
    ),
  );
});

test("rejects unpaired text and URL-shaped model identifiers", () => {
  const canonical = canonicalizeJsonValue(qualification());
  const unpaired = canonical.replace(
    '"model":"openai/gpt-5.6-luna"',
    '"model":"\\ud800"',
  );
  assert.ok(
    validatePrototypeCreatorQualificationJson(unpaired).diagnostics.some(
      (item) =>
        item.code ===
        "PROTOTYPE_CREATOR_QUALIFICATION_TEXT_UNPAIRED_SURROGATE",
    ),
  );

  const urlModel = qualification();
  urlModel.model = ["ht", "tps", "://provider.invalid/model"].join("");
  assert.equal(
    validatePrototypeCreatorQualificationJson(canonicalizeJsonValue(urlModel))
      .valid,
    false,
  );
});

test("rejects inconsistent evidence identity and screenshot coverage", () => {
  const wrongIdentity = qualification();
  wrongIdentity.evidence.runId = "d".repeat(64);
  assert.ok(
    validatePrototypeCreatorQualificationJson(
      canonicalizeJsonValue(wrongIdentity),
    ).diagnostics.some(
      (item) =>
        item.code ===
        "PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_RUN_ID_MISMATCH",
    ),
  );

  const incomplete = qualification();
  incomplete.evidence.screenshotCount = 5;
  assert.ok(
    validatePrototypeCreatorQualificationJson(
      canonicalizeJsonValue(incomplete),
    ).diagnostics.some(
      (item) =>
        item.code ===
        "PROTOTYPE_CREATOR_QUALIFICATION_SCREENSHOT_COVERAGE_INCOMPLETE",
    ),
  );
});

test("enforces the fixed Godot and 300-frame performance gate", () => {
  const wrongGodot = qualification();
  wrongGodot.toolchain.godotVersion = "4.6.2";
  assert.equal(
    validatePrototypeCreatorQualificationJson(canonicalizeJsonValue(wrongGodot))
      .valid,
    false,
  );

  const tooSlow = qualification();
  tooSlow.evidence.medianFpsMilli = 29_999;
  const result = validatePrototypeCreatorQualificationJson(
    canonicalizeJsonValue(tooSlow),
  );
  assert.ok(
    result.diagnostics.some(
      (item) =>
        item.code ===
        "PROTOTYPE_CREATOR_QUALIFICATION_SCHEMA_NUMBER_CONSTRAINT",
    ),
  );
});

test("fails closed for oversized and over-depth inputs", () => {
  const oversized = `{"value":"${"x".repeat(256 * 1024)}"}`;
  assert.ok(
    validatePrototypeCreatorQualificationJson(oversized).diagnostics.some(
      (item) =>
        item.code === "PROTOTYPE_CREATOR_QUALIFICATION_JSON_SIZE_EXCEEDED",
    ),
  );

  const overDepth = `${"[".repeat(65)}${"]".repeat(65)}`;
  assert.ok(
    validatePrototypeCreatorQualificationJson(overDepth).diagnostics.some(
      (item) =>
        item.code === "PROTOTYPE_CREATOR_QUALIFICATION_JSON_DEPTH_EXCEEDED",
    ),
  );
});

test("canonical validation is byte-deterministic for twenty repetitions", () => {
  const text = canonicalizeJsonValue(qualification());
  const expected = validatePrototypeCreatorQualificationJson(text);
  for (let index = 0; index < 20; index += 1) {
    assert.deepEqual(validatePrototypeCreatorQualificationJson(text), expected);
  }
});
