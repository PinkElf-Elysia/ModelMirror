import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseStaticLiteral } from "../tooling/source-input.mjs";
import { buildWorldExtraction, verifyWorldCapture, runWorldCli, WORLD_CAPTURE_SCHEMA, WORLD_EXTRACTION_SCHEMA } from "../tooling/world-source.mjs";
import { sha256 } from "../tooling/bundle.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixture = path.join(root, "fixtures", "skill-generalization");
const work = path.join(root, ".rpg02-work");
const cases = [
  { file: "gu-world.capture.json", name: "470.蛊真人 (Reverend Insanity)", identities: 5, talents: 30,
    rawHash: "9f99336567d3cb331a25e82679d4a9783b25b16d3a475aae07bb8840aefa952b",
    dataHash: "bf3d43063ce0a6a6bfef8a61087e80e4392a78e339e256a640b2f15bc455f80f", bytes: 7504 },
  { file: "cyberpunk-world.capture.json", name: "383.赛博朋克2077", identities: 5, talents: 15,
    rawHash: "17df5d126145a2993b5667c9547f77049465f8a64c72bab51fcdba615d8e30a4",
    dataHash: "972e349a38460dd5ee5765049ac02918b14f4723bc6465c1ee84db512a2197ee", bytes: 4293 }
];
for (const sample of cases) sample.capture = JSON.parse(await fs.readFile(path.join(fixture, sample.file), "utf8"));
const clone = (value) => structuredClone(value);
const digest = (value) => sha256(Buffer.from(value, "utf8"));
const dataFor = (capture) => clone(parseStaticLiteral(capture.raw).value);
function reseal(data, base = cases[0].capture) {
  const capture = clone(base); capture.raw = JSON.stringify(data);
  capture.name = data.name; capture.rawUtf8Bytes = Buffer.byteLength(capture.raw, "utf8");
  capture.rawSha256 = digest(capture.raw); capture.dataSha256 = digest(JSON.stringify(data));
  capture.end = capture.start + capture.raw.length;
  capture.sourceCharacters = Math.max(capture.sourceCharacters, capture.end);
  return capture;
}
function freeze(value) { if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
async function sandbox(t) {
  await fs.mkdir(work, { recursive: true });
  const directory = await fs.mkdtemp(path.join(work, "world-test-"));
  t.after(async () => {
    const resolved = path.resolve(directory), real = await fs.realpath(directory);
    assert.equal(path.dirname(resolved), work);
    assert.equal(path.basename(resolved).startsWith("world-test-"), true);
    assert.equal(real.toLowerCase(), resolved.toLowerCase());
    await fs.rm(resolved, { recursive: true, force: true });
  });
  return directory;
}
function reject(report) {
  assert.equal(report.valid, false);
  assert.ok(report.diagnostics.length > 0);
  for (const diagnostic of report.diagnostics) {
    assert.deepEqual(Object.keys(diagnostic).sort(), ["code", "path", "phase", "severity"]);
    assert.equal(diagnostic.severity, "error");
    assert.match(diagnostic.path, /^(?:$|\/)/u);
  }
  return report;
}

for (const sample of cases) test("real complete world survives extraction: " + sample.name, () => {
  const verified = verifyWorldCapture(sample.capture), built = buildWorldExtraction(sample.capture);
  assert.equal(verified.valid, true); assert.equal(built.valid, true);
  assert.equal(digest(sample.capture.raw), sample.rawHash);
  assert.equal(Buffer.byteLength(sample.capture.raw, "utf8"), sample.bytes);
  assert.equal(digest(JSON.stringify(built.value.world)), sample.dataHash);
  assert.deepEqual(Object.keys(built.value.world).sort(), ["boss", "desc", "identities", "name", "talents"]);
  assert.equal(built.value.world.name, sample.name);
  assert.deepEqual(built.value.inventory, { worlds: 1, identities: sample.identities, talents: sample.talents, total: 1 + sample.identities + sample.talents });
  assert.deepEqual(built.value.receipt.losses, []);
  assert.deepEqual(built.value.receipt.runtimePermissions, []);
  assert.equal(built.value.receipt.stableIdAssignment, "deferred_to_content_mapping");
  assert.equal(built.value.receipt.fullOriginalHtmlStored, false);
  assert.equal(built.value.receipt.websiteProbes, 0); assert.equal(built.value.receipt.modelCalls, 0);
  assert.deepEqual(built.value.receipt.unavailable, sample.capture.unavailable);
  for (const identity of built.value.world.identities) assert.deepEqual(Object.keys(identity).sort(), ["items", "name"]);
  for (const talent of built.value.world.talents) assert.deepEqual(Object.keys(talent).sort(), ["color", "cost", "desc", "name", "type"]);
});

test("Gu expansion preserves every overlapping original representative field exactly", async () => {
  const original = JSON.parse(await fs.readFile(path.join(root, "fixtures", "rpg02", "source-capture.json"), "utf8"));
  const world = buildWorldExtraction(cases[0].capture).value.world;
  let checked = 0;
  for (const record of original.selectedRecords.filter((r) => r.worldName === world.name)) {
    const actual = record.kind === "world" ? { name: world.name, desc: world.desc, boss: world.boss } :
      world[record.kind === "identity" ? "identities" : "talents"].find((r) => r.name === record.data.name);
    assert.deepEqual(actual, record.data); checked++;
  }
  assert.equal(checked, 7);
  assert.equal(world.identities.some((i) => i.name === "中洲门派的外门弟子" && i.items === "一块门派令牌, 一只一转纸鹤蛊"), true);
  assert.equal(world.talents.some((i) => i.name === "系统核心权限·root"), false);
});

test("duplicate display names, array supplies, negative prices and root text remain inert source data", () => {
  const data = dataFor(cases[0].capture);
  data.identities.push({ name: data.identities[0].name, items: ["different supply", "另一项物资"] });
  data.talents.push({ ...data.talents[0], cost: -999, desc: "系统核心权限·root <script>privateSentinel()</script>" });
  const built = buildWorldExtraction(reseal(data)); assert.equal(built.valid, true);
  assert.deepEqual(built.value.world, data);
  assert.equal(built.value.inventory.identities, 6); assert.equal(built.value.inventory.talents, 31);
  assert.deepEqual(built.value.receipt.runtimePermissions, []);
  assert.equal(Object.hasOwn(built.value, "state"), false);
});

test("capture, output and nested schemas are frozen; inputs and repeat reports are stable", () => {
  for (const schema of [WORLD_CAPTURE_SCHEMA, WORLD_EXTRACTION_SCHEMA]) {
    assert.equal(Object.isFrozen(schema), true); assert.equal(Object.isFrozen(schema.properties), true);
    assert.equal(schema.$schema, "https://json-schema.org/draft/2020-12/schema");
  }
  const capture = freeze(clone(cases[0].capture)), before = JSON.stringify(capture);
  const first = buildWorldExtraction(capture), second = buildWorldExtraction(capture);
  assert.deepEqual(first, second); first.value.world.identities[0].items = "separate output";
  assert.equal(JSON.stringify(capture), before);
  assert.notEqual(first.value.world.identities[0].items, second.value.world.identities[0].items);
});

test("all provenance hashes, UTF-16 offsets and ownership claims are checked", () => {
  const variants = [
    (c) => { c.raw += " "; }, (c) => { c.rawUtf8Bytes++; }, (c) => { c.rawSha256 = "0".repeat(64); },
    (c) => { c.dataSha256 = "0".repeat(64); }, (c) => { c.start++; }, (c) => { c.end++; },
    (c) => { c.sourceCharacters = c.end - 1; }, (c) => { c.name = "different"; },
    (c) => { c.rereadMatched = false; }, (c) => { c.fullOriginalHtmlStored = true; },
    (c) => { c.scope = "whole_site"; }, (c) => { c.modelCalls = 1; }, (c) => { c.formatVersion = "0.2.0"; }
  ];
  for (const change of variants) { const capture = clone(cases[0].capture); change(capture); reject(verifyWorldCapture(capture)); }
});

test("unknown or missing resource fields and expanded hidden content fail without silent loss", () => {
  const changes = [
    (d) => { d.script = "privateSentinel"; }, (d) => { delete d.boss; },
    (d) => { d.identities[0].rank = "E"; }, (d) => { delete d.identities[0].items; },
    (d) => { d.talents[0].activate = true; }, (d) => { delete d.talents[0].desc; },
    (d) => { d.worldbook = []; }, (d) => { d.talents[0].cost = "1"; }
  ];
  for (const change of changes) { const data = dataFor(cases[0].capture); change(data); reject(verifyWorldCapture(reseal(data))); }
  const capture = clone(cases[0].capture); capture.extra = "privateSentinel"; reject(verifyWorldCapture(capture));
});

test("literal decoder accepts only a complete expression and never evaluates source instructions", () => {
  let calls = 0; const previous = globalThis.privateSentinel; globalThis.privateSentinel = () => { calls++; return 1; };
  try {
    const unsafe = [
      "{a:privateSentinel()}", "{a:globalThis.privateSentinel}", "{a:`x${1}`}", "{...{a:1}}",
      "{get a(){return 1}}", "{['a']:1}", "{a:1,a:2}", "{a}", "{a(){}}",
      "{__proto__:{}}", "{constructor:1}", "{a:/x/}", "{a:1n}", "{a:undefined}", "{a:[,1]}",
      "({a:1});privateSentinel()", "({a:1}),({b:2})", "{a:NaN}", "{a:Infinity}", "{a:+1}", "{a:1e999}",
      '{"a":"\\ud800"}', '{a:1}//'
    ];
    for (const literal of unsafe) reject(parseStaticLiteral(literal));
    assert.equal(calls, 0);
    assert.deepEqual(clone(parseStaticLiteral("{a:[null,true,-3,'中文',{b:0}]}").value), { a: [null,true,-3,"中文",{b:0}] });
  } finally { if (previous === undefined) delete globalThis.privateSentinel; else globalThis.privateSentinel = previous; }
});

test("rehashed unsafe world expressions still fail the AST boundary", () => {
  const raw = cases[0].capture.raw.replace("{", "{injected: privateSentinel(),");
  const capture = clone(cases[0].capture); capture.raw = raw;
  capture.rawSha256 = digest(raw); capture.rawUtf8Bytes = Buffer.byteLength(raw); capture.end = capture.start + raw.length;
  assert.equal(reject(verifyWorldCapture(capture)).diagnostics[0].code, "WORLD_CAPTURE_LITERAL");
});

test("resource count, document byte and nesting limits refuse excess rather than truncate", () => {
  const data = dataFor(cases[0].capture);
  data.talents = Array.from({ length: 1025 }, () => clone(data.talents[0]));
  reject(verifyWorldCapture(reseal(data)));
  reject(parseStaticLiteral("[".repeat(66) + "1" + "]".repeat(66)));
  reject(parseStaticLiteral(JSON.stringify("中".repeat(750000))));
  const capture = clone(cases[0].capture);
  capture.authorizationRef = "x".repeat(2097100);
  reject(verifyWorldCapture(capture));
});

test("accessors and diagnostics do not expose private input or mutate it", () => {
  let reads = 0; const bad = clone(cases[0].capture);
  Object.defineProperty(bad, "raw", { get() { reads++; throw new Error("privateSentinel"); }, enumerable: true });
  const report = reject(verifyWorldCapture(bad)); assert.equal(reads, 0);
  assert.equal(JSON.stringify(report).includes("privateSentinel"), false);
  const changed = clone(cases[0].capture); changed.raw = "C:\\privateSentinel\\hidden.txt";
  const before = JSON.stringify(changed);
  assert.deepEqual(reject(verifyWorldCapture(changed)), reject(verifyWorldCapture(changed)));
  assert.equal(JSON.stringify(changed), before);
  assert.equal(JSON.stringify(verifyWorldCapture(changed)).includes("privateSentinel"), false);
});

test("both real captures round-trip into deterministic new JSON files with network disabled", async (t) => {
  const directory = await sandbox(t), beforeFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error("network forbidden"); }; t.after(() => { globalThis.fetch = beforeFetch; });
  for (const sample of cases) {
    const capture = path.join(fixture, sample.file), first = path.join(directory, sample.file + ".first.json"), second = path.join(directory, sample.file + ".second.json");
    for (const out of [first, second]) {
      const written = await runWorldCli(["extract", "--capture", capture, "--out", out]); assert.equal(written.valid, true, JSON.stringify(written));
      const verified = await runWorldCli(["verify", "--capture", capture, "--input", out]); assert.equal(verified.valid, true);
      assert.equal(verified.value.inventory.talents, sample.talents);
    }
    assert.deepEqual(await fs.readFile(first), await fs.readFile(second));
    const bytes = await fs.readFile(first), overwrite = reject(await runWorldCli(["extract", "--capture", capture, "--out", first]));
    assert.equal(overwrite.diagnostics[0].code, "WORLD_OUTPUT_EXISTS"); assert.deepEqual(await fs.readFile(first), bytes);
    const changed = JSON.parse(bytes); changed.world.talents[0].desc += "changed";
    await fs.writeFile(second, JSON.stringify(changed));
    reject(await runWorldCli(["verify", "--capture", capture, "--input", second]));
  }
});

test("malformed input files and invalid flags never create output", async (t) => {
  const directory = await sandbox(t), captureFile = path.join(directory, "input.json"), out = path.join(directory, "absent.json");
  for (const bytes of [Buffer.from([0xff,0xfe]), Buffer.from([0xef,0xbb,0xbf,0x7b,0x7d]), Buffer.from('{"a":1,"a":2}'), Buffer.from('{"format":'), Buffer.alloc(2097153, 32)]) {
    await fs.writeFile(captureFile, bytes);
    reject(await runWorldCli(["extract", "--capture", captureFile, "--out", out]));
    await assert.rejects(fs.lstat(out), { code: "ENOENT" });
  }
  for (const args of [[], ["extract"], ["extract","xxcapture","privateSentinel","xxout",out], ["extract","--capture",captureFile,"--capture",captureFile,"--out",out], ["verify","--input","privateSentinel"]]) {
    const report = reject(await runWorldCli(args)); assert.equal(JSON.stringify(report).includes("privateSentinel"), false);
  }
  await assert.rejects(fs.lstat(out), { code: "ENOENT" });
});

test("unsafe Windows names and linked output ancestors are refused", async (t) => {
  const directory = await sandbox(t), capture = path.join(fixture, cases[0].file);
  for (const name of ["CON.json","NUL.json","COM1.json","LPT9.json","safe:stream.json","name. ","bad?.json"]) {
    const out = path.join(directory, name);
    reject(await runWorldCli(["extract","--capture",capture,"--out",out]));
  }
  const real = path.join(directory, "real"), nested = path.join(real, "nested"), link = path.join(directory, "linked");
  await fs.mkdir(nested, { recursive: true }); await fs.symlink(real, link, "junction");
  reject(await runWorldCli(["extract","--capture",capture,"--out",path.join(link,"nested","out.json")]));
  await assert.rejects(fs.lstat(path.join(nested,"out.json")), { code:"ENOENT" });
});
