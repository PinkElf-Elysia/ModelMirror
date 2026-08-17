import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { PROTOTYPE_ASSEMBLY_PROFILE } from "@matrix-oasis/prototype-assembler";

test("R12 v2 capacity is six while the frozen public v1 profile remains two", async () => {
  assert.deepEqual(PROTOTYPE_ASSEMBLY_PROFILE, {
    id: "matrix-oasis.prototype-assembly/1", maxZones: 4, maxNonEnvironmentBriefs: 2,
    maxPlacements: 32, maxPlacementsPerZone: 8,
  });
  const source = await readFile(new URL("../packages/prototype-assembler/src/index.mjs", import.meta.url), "utf8");
  assert.match(source, /id: "matrix-oasis\.prototype-assembly\/2", maxZones: 4, maxNonEnvironmentBriefs: 6,/u);
  assert.match(source, /maxPlacements: 32, maxPlacementsPerZone: 8/u);
});
