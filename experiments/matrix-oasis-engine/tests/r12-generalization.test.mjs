import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  R12_MVP_READY_MARKER,
  R12_QUALIFICATION_MARKER,
  analyzeRuntimeReachability,
  parseR12CacheVerificationArguments,
} from "../scripts/lib/r12-qualification-core.mjs";

function neutralAuthoring() {
  return {
    format: "matrix-oasis.authoring-game-pack", formatVersion: "0.1.0", id: "neutral-loop-fixture",
    contentVersion: "1", language: "en", title: "Neutral Loop Fixture", entryNodeId: "entry",
    entities: [], variables: [], cues: [],
    nodes: [
      { id: "entry", title: "Entry", entityIds: [], entryCueIds: [], actions: [
        { id: "continue", label: "Continue", effects: [], target: { kind: "node", id: "choice" } },
      ] },
      { id: "choice", title: "Choice", entityIds: [], entryCueIds: [], actions: [
        { id: "repeat", label: "Repeat", effects: [], target: { kind: "node", id: "entry" } },
        { id: "finish-a", label: "Finish A", effects: [], target: { kind: "ending", id: "ending-a" } },
        { id: "finish-b", label: "Finish B", effects: [], target: { kind: "ending", id: "ending-b" } },
      ] },
    ],
    endings: [
      { id: "ending-a", title: "Ending A", cueIds: [] },
      { id: "ending-b", title: "Ending B", cueIds: [] },
    ],
  };
}

test("generic runtime analysis discovers all endings and a loop without case-specific paths", async () => {
  const compiled = await compileAuthoringGamePackJson(canonicalizeJsonValue(neutralAuthoring()));
  assert.equal(compiled.ok, true);
  const result = await analyzeRuntimeReachability(compiled.canonicalJson, canonicalizeJsonValue(compiled.receipt));
  assert.equal(result.ok, true);
  assert.deepEqual({ endings: result.evidence.reachableEndingCount, all: result.evidence.allEndingsReachable,
    loop: result.evidence.hasReachableLoop }, { endings: 2, all: true, loop: true });
  assert.deepEqual(result.evidence.endingPaths.map((item) => item.actionIds), [
    ["continue", "finish-a"], ["continue", "finish-b"],
  ]);
  assert.equal(Object.isFrozen(result), true);
});

test("runtime analysis is byte-stable across 20 executions and rejects invalid identity input", async () => {
  const compiled = await compileAuthoringGamePackJson(canonicalizeJsonValue(neutralAuthoring()));
  const receipt = canonicalizeJsonValue(compiled.receipt);
  const results = await Promise.all(Array.from({ length: 20 }, () => analyzeRuntimeReachability(compiled.canonicalJson, receipt)));
  assert.equal(new Set(results.map((item) => JSON.stringify(item))).size, 1);
  const invalid = await analyzeRuntimeReachability(`${compiled.canonicalJson}\n`, receipt);
  assert.deepEqual(invalid.diagnostics.map((item) => item.code), ["R12_RUNTIME_INPUT_INVALID"]);
});

test("R12 markers are stable and qualification source remains topic-neutral", async () => {
  assert.equal(R12_MVP_READY_MARKER, "MATRIX_OASIS_R12_MVP_READY");
  assert.equal(R12_QUALIFICATION_MARKER, "MATRIX_OASIS_R12_QUALIFICATION_JSON:");
  const source = await import("../scripts/lib/r12-qualification-core.mjs?surface-check");
  assert.deepEqual(Object.keys(source).sort(), [
    "R12QualificationOperationalError", "R12_LAST_TRAIN_ACCEPTANCE_PROFILE", "R12_MVP_READY_MARKER",
    "R12_QUALIFICATION_MARKER", "analyzeR12QualificationCandidate", "analyzeRuntimeReachability",
    "parseR12CacheVerificationArguments", "verifyR12NeutralSpatialCache",
    "parseR12CallArguments", "readR12CallInputs", "verifyR12CreatorPublishedQualification",
  ].sort());
  const text = await (await import("node:fs/promises")).readFile(
    new URL("../scripts/lib/r12-qualification-core.mjs", import.meta.url), "utf8");
  for (const forbidden of ["last-train", "subway", "student", "nurse", "commuter", "ticket", "clock"]) {
    assert.equal(text.toLowerCase().includes(forbidden), false);
  }
});

test("cache verification accepts only two direct C tmp roots", () => {
  const temporaryRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
  const parsed = parseR12CacheVerificationArguments([
    "--prototype-run-root", path.join(temporaryRoot, "matrix-oasis-r10-runs"),
    "--spatial-run-root", path.join(temporaryRoot, "matrix-oasis-r11-spatial-primary-density-v8-overlay"),
  ]);
  assert.equal(parsed.temporaryRoot, temporaryRoot);
  for (const args of [
    [],
    ["--prototype-run-root", path.join(temporaryRoot, "one"), "--prototype-run-root", path.join(temporaryRoot, "two")],
    ["--prototype-run-root", path.join(temporaryRoot, "one"), "--spatial-run-root", path.join(path.parse(temporaryRoot).root, "outside", "two")],
  ]) assert.throws(() => parseR12CacheVerificationArguments(args), { code: "R12_QUALIFICATION_INTERNAL_ERROR" });
});
