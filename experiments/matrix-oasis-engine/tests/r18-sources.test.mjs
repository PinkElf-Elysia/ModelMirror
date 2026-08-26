import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function sha256(relative) {
  return createHash("sha256")
    .update(readFileSync(path.join(moduleRoot, ...relative.split("/"))))
    .digest("hex");
}

test("R17 selection evidence remains byte frozen while R18 owns the active claim state", () => {
  assert.equal(
    sha256("docs/R17_QUALIFICATION_SUMMARY.json"),
    "d87346eebfbbcb22bf00a386a6511859c42aec91393d193a4c40db0b9de08c8e",
  );
  assert.equal(
    sha256("docs/R17_V2_SELECTION_MATRIX.md"),
    "9cb2dceeea7ad3ba42b52d090822b26950b544f630310cb0b7b6150e8722bc40",
  );
  assert.equal(
    sha256("third-party/v2-qualification-references/reference.lock.json"),
    "0104e57fb962705b35bbbba1ca098e272af1e178ff00492f89744385f6c0173f",
  );

  const status = JSON.parse(readFileSync(path.join(moduleRoot, "docs", "V2_STATUS.json"), "utf8"));
  assert.deepEqual(status, {
    schemaVersion: 1,
    status: "r18-landscape-in-progress",
    claimAllowed: false,
    blockingRound: "R25",
    qualificationProfile: "matrix-oasis.v2-landscape/1",
  });
});
