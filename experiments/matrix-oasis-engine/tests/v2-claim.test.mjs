import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { checkV2Claim, V2ClaimError } from "../scripts/lib/v2-claim-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-v2-claim-"));
  mkdirSync(path.join(root, "docs"), { recursive: true });
  cpSync(path.join(moduleRoot, "module-boundary.json"), path.join(root, "module-boundary.json"));
  cpSync(path.join(moduleRoot, "docs", "V2_STATUS.json"), path.join(root, "docs", "V2_STATUS.json"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

test("R19 authority work keeps the V2 completion claim closed until R25", () => {
  assert.deepEqual(checkV2Claim({ moduleRoot }), {
    status: "r19-authority-qualified",
    claimAllowed: false,
    blockingRound: "R25",
  });
});

test("rejects a premature V2 claim", (t) => {
  const root = fixture(t);
  const target = path.join(root, "docs", "V2_STATUS.json");
  const status = JSON.parse(readFileSync(target, "utf8"));
  status.claimAllowed = true;
  writeFileSync(target, `${JSON.stringify(status, null, 2)}\n`, "utf8");
  assert.throws(
    () => checkV2Claim({ moduleRoot: root }),
    (error) => error instanceof V2ClaimError && error.code === "V2_CLAIM_STATUS_MISMATCH",
  );
});
