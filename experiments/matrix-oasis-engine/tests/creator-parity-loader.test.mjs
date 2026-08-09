import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  LocalPackLoader,
  prepareCreatorSession,
} from "../apps/creator-web/src/pack-loader.ts";

const mechanicsText = await readFile(
  new URL(
    "../examples/mechanics-conformance.authoring-game-pack.json",
    import.meta.url,
  ),
  "utf8",
);

test("builtin preparation creates one opaque dual-runtime candidate", async () => {
  const result = await prepareCreatorSession(mechanicsText, {
    kind: "builtin",
    id: "neutral",
  });
  assert.equal(result.ok, true);
  assert.deepEqual(Object.keys(result.candidate).sort(), [
    "artifact",
    "emittedCues",
    "inspection",
    "prepared",
    "snapshot",
    "source",
    "transition",
  ]);
  assert.deepEqual(result.candidate.source, { kind: "builtin", id: "neutral" });
  assert.equal(Object.getPrototypeOf(result.candidate.prepared), null);
  assert.equal(result.candidate.snapshot.authoring.stepCount, 0);
  assert.equal(result.candidate.snapshot.runtime.stepCount, 0);
  assert.equal(
    result.candidate.snapshot.runtime.pack.artifactSha256.length,
    64,
  );
  assert.equal(Object.isFrozen(result.candidate.snapshot), true);
  assert.equal(Object.isFrozen(result.candidate.inspection), true);
});

test("invalid content preserves the frozen R1 validation report diagnostics", async () => {
  const source = JSON.parse(mechanicsText);
  delete source.entryNodeId;
  const result = await prepareCreatorSession(JSON.stringify(source), {
    kind: "local",
  });

  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].phase, "schema");
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.diagnostics), true);
});

test("Creator loader source depends only on the parity package public root", async () => {
  const source = await readFile(
    new URL("../apps/creator-web/src/pack-loader.ts", import.meta.url),
    "utf8",
  );

  assert.match(source, /from "@matrix-oasis\/game-pack-parity-harness"/);
  assert.doesNotMatch(source, /game-pack-simulator/);
  assert.doesNotMatch(source, /runtime-pack-simulator/);
  assert.doesNotMatch(source, /\/src\//);
  assert.equal(typeof LocalPackLoader, "function");
});
