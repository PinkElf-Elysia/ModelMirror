import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

const scriptPath = path.resolve(import.meta.dirname, "stable-signature.mjs");

function signature(t, payload) {
  const directory = fs.mkdtempSync(
    path.join(os.tmpdir(), "openrouter-signature-"),
  );
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const inputPath = path.join(directory, "actionable.json");
  fs.writeFileSync(inputPath, JSON.stringify(payload));
  const result = spawnSync(process.execPath, [scriptPath, inputPath], {
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}

test("is stable across object-key and set-like array ordering", (t) => {
  const first = signature(t, {
    missing: ["vendor/b", "vendor/a"],
    nested: { count: 2, fields: ["output", "input"] },
  });
  const reordered = signature(t, {
    nested: { fields: ["input", "output"], count: 2 },
    missing: ["vendor/a", "vendor/b"],
  });
  assert.equal(first, reordered);
});

test("changes when actionable content changes", (t) => {
  const first = signature(t, { missing: ["vendor/a"] });
  const changed = signature(t, { missing: ["vendor/b"] });
  assert.notEqual(first, changed);
});
