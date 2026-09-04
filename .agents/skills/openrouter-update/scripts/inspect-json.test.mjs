import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const scriptPath = path.resolve(import.meta.dirname, "inspect-json.mjs");

function completeModels(ids) {
  return {
    data: ids.map((id) => ({ id })),
    total_count: ids.length,
    links: { next: null },
  };
}

function market(ids) {
  return {
    data: {
      models: ids.map((id) => ({ endpoint: { model_variant_slug: id } })),
    },
  };
}

function compare(t, modelIds, marketIds) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "openrouter-coverage-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const modelsPath = path.join(directory, "models.json");
  const marketPath = path.join(directory, "market.json");
  fs.writeFileSync(modelsPath, JSON.stringify(completeModels(modelIds)));
  fs.writeFileSync(marketPath, JSON.stringify(market(marketIds)));
  return spawnSync(
    process.execPath,
    [scriptPath, modelsPath, "data", "compare-market", marketPath],
    { encoding: "utf8" },
  );
}

test("rejects equal-sized model and market sets with different identities", (t) => {
  const result = compare(t, ["vendor/a", "vendor/b"], ["vendor/a", "vendor/c"]);
  assert.equal(result.status, 2);
  assert.deepEqual(JSON.parse(result.stdout), {
    comparison: "exact-id-set-v1",
    source_non_batch_models: 2,
    market_unique_base_models: 2,
    missing_from_market: ["vendor/b"],
    market_only: ["vendor/c"],
    complete: false,
  });
});

test("folds only Batch market variants and preserves free and alias identities", (t) => {
  const result = compare(
    t,
    ["vendor/a", "vendor/a:batch", "vendor/a:free", "~vendor/a"],
    ["vendor/a", "vendor/a:batch", "vendor/a:free", "~vendor/a"],
  );
  assert.equal(result.status, 0, result.stderr);
  const coverage = JSON.parse(result.stdout);
  assert.equal(coverage.complete, true);
  assert.equal(coverage.source_non_batch_models, 3);
  assert.equal(coverage.market_unique_base_models, 3);
});

test("rejects duplicate or missing general model identities", (t) => {
  const duplicate = compare(t, ["vendor/a", "vendor/a"], ["vendor/a"]);
  assert.equal(duplicate.status, 2);
  assert.match(duplicate.stderr, /duplicate non-Batch identities/);

  const missing = compare(t, ["vendor/a", ""], ["vendor/a"]);
  assert.equal(missing.status, 2);
  assert.match(missing.stderr, /has no model identity/);

  const missingMarketIdentity = compare(t, ["vendor/a"], [""]);
  assert.equal(missingMarketIdentity.status, 2);
  assert.match(missingMarketIdentity.stderr, /Market record 0 has no model identity/);
});
