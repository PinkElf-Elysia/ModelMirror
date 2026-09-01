import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  prepareNpcDerivedState,
  projectNpcDerivedState,
  verifyNpcDerivedState,
} from "@matrix-oasis/npc-derived-state-runtime";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  R21_QUALIFICATION_MARKERS,
  runR21Qualification,
  runR21Verify,
} from "../scripts/lib/r21-cli-core.mjs";
import { bindNpcDerivedStateSource } from "../scripts/lib/r21-projection-core.mjs";

const rootsJson = process.env.MATRIX_OASIS_R21_REAL_CACHE_ROOTS_JSON;
const outputPrefix = process.env.MATRIX_OASIS_R21_REAL_OUTPUT_PREFIX;
const enabled = typeof rootsJson === "string" && typeof outputPrefix === "string";

const runtime = Object.freeze({
  prepareNpcDerivedState,
  projectNpcDerivedState,
  verifyNpcDerivedState,
  bindNpcDerivedStateSource,
});

function parseRoots() {
  let roots;
  try {
    roots = JSON.parse(rootsJson);
  } catch {
    throw new Error("R21_REAL_CACHE_CONFIGURATION_INVALID");
  }
  if (!Array.isArray(roots) || roots.length !== 2 || roots.some((root) => typeof root !== "string" || !path.isAbsolute(root))) {
    throw new Error("R21_REAL_CACHE_CONFIGURATION_INVALID");
  }
  return roots.map((root) => path.resolve(root));
}

function fileArguments({ npcRunRoot, runtimePack, runtimeReceipt, authorityPolicy, personaSeed, relationshipPolicy, output }) {
  return [
    "--npc-run-root", npcRunRoot,
    "--runtime-pack", runtimePack,
    "--runtime-receipt", runtimeReceipt,
    "--authority-policy", authorityPolicy,
    "--persona-seed", personaSeed,
    "--relationship-policy", relationshipPolicy,
    "--output", output,
  ];
}

async function hashTree(root) {
  const hash = createHash("sha256");
  async function visit(directory, prefix = "") {
    const entries = await readdir(directory, { withFileTypes: true });
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const target = path.join(directory, entry.name);
      if (entry.isSymbolicLink() || (!entry.isFile() && !entry.isDirectory())) throw new Error("R21_REAL_CACHE_SOURCE_IDENTITY_INVALID");
      hash.update(`${entry.isDirectory() ? "d" : "f"}:${relative}\0`);
      if (entry.isDirectory()) await visit(target, relative);
      else hash.update(await readFile(target));
    }
  }
  await visit(root);
  return `sha256:${hash.digest("hex")}`;
}

test("two externally supplied qualified R20 currents rebuild deterministic R21 bundles", { skip: !enabled }, async () => {
  assert.match(outputPrefix, /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,80}$/u);
  const temporaryRoot = path.resolve(path.parse(process.execPath).root, "tmp");
  const inputsRoot = path.join(temporaryRoot, `.${outputPrefix}-inputs`);
  await mkdir(inputsRoot, { recursive: false });
  const summaries = [];

  for (const [index, npcRunRoot] of parseRoots().entries()) {
    const sourceTreeSha256 = await hashTree(npcRunRoot);
    const current = JSON.parse(await readFile(path.join(npcRunRoot, "npc-current.json"), "utf8"));
    const timelineRoot = path.join(npcRunRoot, "timelines", current.manifestSha256.slice(7));
    const evidence = JSON.parse(await readFile(path.join(timelineRoot, "qualification-evidence.json"), "utf8"));
    const bindingJson = await readFile(path.join(timelineRoot, "entity-bindings.json"), "utf8");
    const binding = JSON.parse(bindingJson);
    const authorityPolicy = JSON.parse(evidence.authorityPolicyJson);
    const authority = {
      runtimePackSha256: hashCanonicalValue(JSON.parse(evidence.runtimeGamePackJson)),
      runtimeReceiptSha256: hashCanonicalValue(JSON.parse(evidence.runtimeReceiptJson)),
      authorityPolicySha256: hashCanonicalValue(authorityPolicy),
      npcEntityBindingSha256: hashCanonicalValue(binding),
    };
    const persona = {
      format: "matrix-oasis.npc-persona-seed",
      formatVersion: "0.1.0",
      canonicalization: "matrix-oasis.canonical-json/1",
      id: `real-cache-persona-${index + 1}`,
      contentVersion: "1.0.0",
      authority,
      traitIds: ["baseline"],
      actors: binding.bindings
        .map(({ actorEntityId }) => ({ actorEntityId, traits: [{ traitId: "baseline", value: 0 }] }))
        .sort((left, right) => left.actorEntityId.localeCompare(right.actorEntityId)),
    };
    const personaSeedJson = canonicalizeJsonValue(persona);
    const relationshipPolicyJson = canonicalizeJsonValue({
      format: "matrix-oasis.npc-relationship-projection-policy",
      formatVersion: "0.1.0",
      canonicalization: "matrix-oasis.canonical-json/1",
      id: `real-cache-relationship-policy-${index + 1}`,
      contentVersion: "1.0.0",
      authority,
      personaSeedSha256: hashCanonicalValue(persona),
      repeatMode: "first-accepted-per-rule-actor-target-timeline",
      rules: [],
    });
    const caseRoot = path.join(inputsRoot, `case-${index + 1}`);
    await mkdir(caseRoot, { recursive: false });
    const files = {
      runtimePack: path.join(caseRoot, "runtime-game-pack.json"),
      runtimeReceipt: path.join(caseRoot, "runtime-receipt.json"),
      authorityPolicy: path.join(caseRoot, "npc-authority-policy.json"),
      personaSeed: path.join(caseRoot, "npc-persona-seed.json"),
      relationshipPolicy: path.join(caseRoot, "npc-relationship-projection-policy.json"),
    };
    await Promise.all([
      writeFile(files.runtimePack, evidence.runtimeGamePackJson, { encoding: "utf8", flag: "wx" }),
      writeFile(files.runtimeReceipt, evidence.runtimeReceiptJson, { encoding: "utf8", flag: "wx" }),
      writeFile(files.authorityPolicy, evidence.authorityPolicyJson, { encoding: "utf8", flag: "wx" }),
      writeFile(files.personaSeed, personaSeedJson, { encoding: "utf8", flag: "wx" }),
      writeFile(files.relationshipPolicy, relationshipPolicyJson, { encoding: "utf8", flag: "wx" }),
    ]);
    const output = path.join(temporaryRoot, `${outputPrefix}-case-${index + 1}`);
    const args = fileArguments({ npcRunRoot, ...files, output });
    const qualified = await runR21Qualification(args, runtime, { temporaryRoot });
    assert.equal(qualified.ok, true);
    assert.deepEqual(qualified.markers, R21_QUALIFICATION_MARKERS);
    const verified = await runR21Verify([
      "--npc-run-root", npcRunRoot,
      "--runtime-pack", files.runtimePack,
      "--runtime-receipt", files.runtimeReceipt,
      "--authority-policy", files.authorityPolicy,
      "--projection-dir", output,
    ], runtime, { temporaryRoot });
    assert.equal(verified.ok, true);
    assert.equal(verified.bundleSha256, qualified.bundleSha256);
    const report = JSON.parse(await readFile(path.join(output, "npc-projection-qualification-report.json"), "utf8"));
    assert.deepEqual(report.markers, R21_QUALIFICATION_MARKERS);
    assert.equal(report.counts.memoryEpisodes, report.counts.acceptedEntries);
    assert.equal(report.counts.relationshipContributions, 0);
    assert.equal(await hashTree(npcRunRoot), sourceTreeSha256);
    summaries.push({ output, bundleSha256: qualified.bundleSha256, sourceTreeSha256, ledger: report.ledger, counts: report.counts });
  }

  assert.equal(new Set(summaries.map(({ bundleSha256 }) => bundleSha256)).size, 2);
  process.stdout.write(`R21_REAL_CACHE_QUALIFICATION_JSON:${JSON.stringify(summaries)}\n`);
});
