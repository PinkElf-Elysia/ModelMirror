import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import test from "node:test";
import { APPROVED_HOST_TEMPLATE, RPG04_APPROVED_HOST_SHA256 } from "../context/approved-host.mjs";
import { loadRpg04ApprovedHost } from "../tooling/context-host.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";

const sha = (value) => createHash("sha256").update(Buffer.from(value, "utf8")).digest("hex");

test("exports only the fixed approved host identity and exact content", () => {
  assert.deepEqual(Object.keys(APPROVED_HOST_TEMPLATE), ["id", "version", "content"]);
  assert.equal(Object.isFrozen(APPROVED_HOST_TEMPLATE), true);
  assert.match(APPROVED_HOST_TEMPLATE.content, /玩家独自决定自己的行动/);
  assert.match(APPROVED_HOST_TEMPLATE.content, /NPC保持独立人格/); // P18
  assert.doesNotMatch(APPROVED_HOST_TEMPLATE.content, /800[–~-]1200|五类|A-E|五件/); // P10 and card rules stay card-owned
});

test("prepends the fixed protocol and returns canonical identity binding plus receipts", () => {
  const report = loadRpg04ApprovedHost();
  assert.equal(report.valid, true, JSON.stringify(report.diagnostics));
  assert.equal(report.value.hostTemplate.content, report.value.content);
  assert.equal(report.value.hostTemplate.content.endsWith(APPROVED_HOST_TEMPLATE.content), true);
  assert.deepEqual(report.value.binding, { id: report.value.hostTemplate.id, version: report.value.hostTemplate.version, sha256: sha(canonicalJson(report.value.hostTemplate).value) });
  assert.equal(report.value.sources[1].sha256, RPG04_APPROVED_HOST_SHA256);
  assert.equal(report.value.contentSha256, sha(report.value.content));
  assert.equal(report.value.sources.length, 2);
  assert.deepEqual(report.value.activation, {
    ready: true,
    status: "user_approved_for_bounded_testing",
    blockers: [],
  });
  assert.equal(report.value.sources[0].sourceReference, "docs/RPG04_PROTOCOL_RUNTIME.txt");
  assert.equal(report.value.sources[1].sourceReference, "context/approved-host.mjs");
});

test("matches every included paragraph to the exact approved review block", () => {
  const review = fs.readFileSync(new URL("../docs/RPG04_INTEGRATED_REVIEW.md", import.meta.url), "utf8");
  const approvedBlocks = [["P02", 0], ["P03", 0], ["P04", 0], ["P05", 0], ["P06", 0], ["P11", 0], ["P15", 0], ["P18", 0], ["P08", 0], ["P09", 1], ["P12", 0], ["P27", 0]];
  const exact = approvedBlocks.map(([id, quoteIndex]) => {
    const section = review.match(new RegExp(`^### ${id} [^\\n]*\\n([\\s\\S]*?)(?=^### |^## )`, "mu"));
    assert.ok(section, id);
    const quote = section[1].split(/\r?\n/u).filter((line) => line.startsWith("> "))[quoteIndex];
    assert.ok(quote, id);
    return quote.slice(2);
  });
  assert.deepEqual(APPROVED_HOST_TEMPLATE.content.split("\n\n"), [...exact, "列表类型字段为空时输出 []，不得用“（空）”等字符串代替列表。"]);
});

test("does not pass reviews, rejected candidates, HTML rendering, or untrusted reasoning controls to the model", () => {
  const report = loadRpg04ApprovedHost();
  for (const forbidden of ["P29", "P24", "P25", "Unrestricted Mode", "<details>", "<span", "RPG04_INTEGRATED_REVIEW", "推理展示由用户选择", "NoMeta", "CatnipDimension", "HideReason"]) {
    assert.equal(report.value.content.includes(forbidden), false, forbidden);
  }
  assert.match(report.value.content, /不生成用于渲染的 HTML 面板/);
});

test("does not promote retained unreviewed original clauses into the universal host", () => {
  for (const retainedElsewhere of ["NoMeta", "CatnipDimension", "HideReason", "细胞即是成年", "【世界现状】"]) {
    assert.equal(APPROVED_HOST_TEMPLATE.content.includes(retainedElsewhere), false, retainedElsewhere);
  }
});

test("rejects caller input and returns detached immutable results", () => {
  assert.equal(loadRpg04ApprovedHost("review.md").diagnostics[0].code, "RPG04_HOST_ARGUMENTS_REJECTED");
  const first = loadRpg04ApprovedHost();
  assert.throws(() => { first.value.content = "changed"; }, TypeError);
  assert.deepEqual(first, loadRpg04ApprovedHost());
});
