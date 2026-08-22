import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { checkMvpClaim, MvpClaimError } from "../scripts/lib/mvp-claim-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const fixturePrefix = "matrix-oasis-mvp-claim-";

function withFixture(t) {
  const fixture = mkdtempSync(path.join(os.tmpdir(), fixturePrefix));
  for (const relativePath of [
    "module-boundary.json",
    "README.md",
    "docs/MVP_STATUS.json",
    "docs/PRODUCT.md",
    "docs/V1_CRITICAL_PATH.md",
    "docs/ARCHITECTURE.md",
    "docs/KNOWN_LIMITATIONS.md",
    "docs/rounds/R15_ACCEPTANCE.md",
  ]) {
    mkdirSync(path.dirname(path.join(fixture, relativePath)), { recursive: true });
    cpSync(path.join(moduleRoot, relativePath), path.join(fixture, relativePath), {
      recursive: true,
    });
  }
  t.after(() => {
    assert.equal(path.dirname(fixture), os.tmpdir());
    assert.match(path.basename(fixture), new RegExp(`^${fixturePrefix}`));
    rmSync(fixture, { recursive: true, force: true });
  });
  return fixture;
}

function expectCode(callback, code) {
  assert.throws(callback, (error) => {
    assert.ok(error instanceof MvpClaimError);
    assert.equal(error.code, code);
    assert.equal(error.message, code);
    return true;
  });
}

test("committed R15 status keeps the MVP completion claim blocked", () => {
  assert.deepEqual(checkMvpClaim({ moduleRoot }), {
    status: "pending-runtime-evidence",
    claimAllowed: false,
    checkedDocuments: 5,
  });
});

test("rejects a stale R10 completion claim in public product docs", (t) => {
  const fixture = withFixture(t);
  const target = path.join(fixture, "docs", "PRODUCT.md");
  writeFileSync(
    target,
    `${readFileSync(target, "utf8")}\nR10通过即定义初版完成。\n`,
    "utf8",
  );
  expectCode(
    () => checkMvpClaim({ moduleRoot: fixture }),
    "MVP_CLAIM_PREMATURE",
  );
  writeFileSync(target, "# 产品状态\n\n自然语言到3D初版闭环已经完成。\n", "utf8");
  expectCode(
    () => checkMvpClaim({ moduleRoot: fixture }),
    "MVP_CLAIM_PREMATURE",
  );
});

test("rejects status or policy drift before qualification", (t) => {
  const fixture = withFixture(t);
  const target = path.join(fixture, "docs", "MVP_STATUS.json");
  const status = JSON.parse(readFileSync(target, "utf8"));
  status.claimAllowed = true;
  writeFileSync(target, `${JSON.stringify(status, null, 2)}\n`, "utf8");
  expectCode(
    () => checkMvpClaim({ moduleRoot: fixture }),
    "MVP_CLAIM_STATUS_MISMATCH",
  );
});

test("rejects an acceptance record that stops declaring R15 in progress", (t) => {
  const fixture = withFixture(t);
  writeFileSync(
    path.join(fixture, "docs", "rounds", "R15_ACCEPTANCE.md"),
    "# R15验收记录\n\n状态：待记录\n",
    "utf8",
  );
  expectCode(
    () => checkMvpClaim({ moduleRoot: fixture }),
    "MVP_CLAIM_PREMATURE",
  );
});
