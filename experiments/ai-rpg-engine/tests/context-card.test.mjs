import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createHash } from "node:crypto";
import { canonicalJson } from "../runtime/index.mjs";
import { HOST_TEMPLATE } from "../context/host-template.mjs";
import { validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { compileVerifiedContent } from "./runtime-fixtures.mjs";
import { buildRpg04ContextCard, loadRpg04ContextCardFixture, validateRpg04ContextCardSource, RPG04_AUTHORED_SOURCE_SHA256, RPG04_HOST_TEMPLATE_SHA256 } from "../tooling/context-card.mjs";

const authoredText = fs.readFileSync(new URL("../fixtures/rpg04/authored-resources.json", import.meta.url), "utf8");
const baseline = () => compileVerifiedContent();
const build = (compiled = baseline(), text = authoredText) => buildRpg04ContextCard(compiled, text);
const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");

test("builds the valid representative card and preserves all baseline resources and provenance", () => {
  const old = baseline(), report = build(old); assert.equal(report.valid, true, JSON.stringify(report.diagnostics));
  assert.equal(validateCardPackage(report.value.cardPackage).valid, true);
  for (const [key, resources] of Object.entries(old.cardPackage.resources)) assert.deepEqual(report.value.cardPackage.resources[key].slice(0, resources.length), resources);
  assert.deepEqual(report.value.cardPackage.provenance.sources.slice(0, old.cardPackage.provenance.sources.length), old.cardPackage.provenance.sources);
  assert.deepEqual(report.value.cardPackage.provenance.rights.slice(0, old.cardPackage.provenance.rights.length), old.cardPackage.provenance.rights);
});

test("binds the player to the new card while preserving persona, five talents, activations and empty permissions", () => {
  const old = baseline(), { value } = build(old); assert.equal(validatePlayerSetup(value.playerSetup, value.cardPackage).valid, true);
  assert.deepEqual(value.playerSetup.character, old.playerSetup.character); assert.deepEqual(value.playerSetup.talents, old.playerSetup.talents);
  assert.equal(value.playerSetup.talents.every((talent) => talent.owned && talent.active), true); assert.equal(value.playerSetup.talents.length, 5);
  assert.deepEqual(value.playerSetup.runtimePermissions, []); assert.deepEqual(value.playerSetup.cardPackageRef, { id: "card.rpg04-representative", version: "0.1.0" });
});

test("emits reproducible exact-source, canonical-card and canonical-host provenance hashes", () => {
  const first = build(), second = loadRpg04ContextCardFixture(); assert.equal(second.valid, true);
  assert.equal(RPG04_AUTHORED_SOURCE_SHA256, sha(authoredText)); assert.equal(first.value.provenanceReceipt.authoredSource.sha256, RPG04_AUTHORED_SOURCE_SHA256);
  assert.equal(first.value.provenanceReceipt.hostTemplateSha256, RPG04_HOST_TEMPLATE_SHA256);
  assert.equal(sha(canonicalJson(HOST_TEMPLATE).value), RPG04_HOST_TEMPLATE_SHA256);
  assert.equal(sha(canonicalJson(first.value.cardPackage).value), first.value.provenanceReceipt.cardPackageSha256);
  assert.deepEqual(first, second);
});

test("rejects byte drift including harmless JSON whitespace before parsing", () => {
  const report = build(baseline(), authoredText + " "); assert.equal(report.valid, false);
  assert.deepEqual(report.diagnostics, [{ phase: "provenance", severity: "error", code: "RPG04_AUTHORED_SOURCE_HASH_MISMATCH", path: "/authoredText" }]);
});

test("rejects invalid and open authored envelopes without exposing source text", () => {
  for (const text of ["{", authoredText.replace('"defaults": {', '"endpoint": "https://invalid.example", "defaults": {')]) {
    const report = validateRpg04ContextCardSource(baseline(), text); assert.equal(report.valid, false); assert.equal(JSON.stringify(report).includes("https://invalid.example"), false);
  }
});

test("rejects dangling and duplicate merged resources with one stable diagnostic", () => {
  const dangling = JSON.parse(authoredText), duplicate = JSON.parse(authoredText);
  dangling.resources.openings[0].worldbookRefs.push("worldbook.missing");
  duplicate.resources.styles.push(structuredClone(duplicate.resources.styles[0]));
  for (const value of [dangling, duplicate]) {
    const text = JSON.stringify(value), report = validateRpg04ContextCardSource(baseline(), text);
    assert.equal(report.valid, false); assert.equal(report.diagnostics[0].code, "RPG04_MERGED_CARD_INVALID");
  }
});

test("rejects a semantically valid but unauthenticated baseline before merge", () => {
  const changed = baseline(); changed.cardPackage.package.displayName += " drift";
  const report = buildRpg04ContextCard(changed, authoredText);
  assert.equal(report.valid, false); assert.equal(report.diagnostics[0].code, "RPG04_BASELINE_CARD_HASH_MISMATCH");
});

test("never mutates inputs and returns detached deterministic results", () => {
  const old = baseline(), before = structuredClone(old), first = build(old), second = build(old);
  assert.deepEqual(old, before); assert.deepEqual(first, second);
  first.value.cardPackage.package.displayName = "changed"; assert.notEqual(second.value.cardPackage.package.displayName, "changed");
});
