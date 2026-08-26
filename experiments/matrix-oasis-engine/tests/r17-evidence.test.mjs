import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { verifyR17QualificationSummary } from "../scripts/lib/r17-evidence-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("the canonical R17 summary records only conservative externally evidenced conclusions", () => {
  assert.deepEqual(verifyR17QualificationSummary(moduleRoot), {
    ok: true,
    candidates: 5,
    lanes: 4,
    status: "r17-selection-qualified",
  });
});
